# ADR 0022: Concurrencia y consistencia de estado entre procesos

- Estado: aceptada
- Fecha: 2026-07-31

## Contexto

Dos señales de la sesión anterior, tratadas inicialmente como una sola causa
("una race condition de locking"):

1. `collect()` en `isolation/git_worktree.py` no tomaba el mismo lock por
   proyecto que `prepare()`/`integrate()`/`discard()`.
2. Correr `agentarium project status` en un proceso separado mientras
   `agentarium project run` seguía escribiendo produjo
   `InvalidTransition: ready -> awaiting_review`.

Ninguna de las dos resultó ser lo que parecía. El diagnóstico real requirió
tres iteraciones separadas, cada una descubierta verificando en vivo el fix
anterior — el mismo patrón que ya había pasado con ADR 0017 y ADR 0019.

## Lo que se implementó (independientemente de causar los síntomas o no)

Todo esto es correcto y se queda, aunque el diagnóstico original de la señal
1 resultó incompleto:

- **Lock único por proyecto, entre procesos**
  (`GitWorktreeIsolation._project_lock`): dos capas — un `asyncio.Lock`
  interno (rápido, mismo proceso) más un `filelock.FileLock` real
  (`workspace_root/<project_id>/.isolation.lock`). `prepare()`, `collect()`,
  `integrate()` y `discard()` usan las cuatro el mismo lock ahora;
  `collect()` no lo tenía porque opera sobre el worktree aislado del
  intento, pero comparte objetos/refs de git con el repo principal.
  **Trampa real encontrada implementando esto**: `filelock` detecta
  "deadlock" cuando adquisición y liberación corren en threads del SO
  distintos — su bookkeeping es por-thread, no por-lock-lógico. Usar
  `asyncio.to_thread` para `acquire()`/`release()` (la opción obvia) colgaba
  o lanzaba `RuntimeError: Deadlock` de forma intermitente porque
  `to_thread` no garantiza el mismo thread del pool en cada llamada.
  Solución: `acquire(blocking=False)` corrido directo en el thread del event
  loop dentro de un poll con `asyncio.sleep`, nunca `to_thread` — verificado
  con exclusión mutua real bajo 8 corrutinas concurrentes × 20 ciclos antes
  de integrarlo.
- **SQLite en WAL + `busy_timeout=5000`** (`repositories/database.py`): antes
  sólo `PRAGMA foreign_keys=ON`. WAL permite lectores y el escritor
  coexistir sin bloquearse mutuamente; `busy_timeout` hace que un escritor
  que sí contiende reintente en vez de fallar.
- **Transición optimista con versión** (`WorkItemRow.version`, nueva
  columna vía `ALTER TABLE` idempotente en `Database.create_all()`, no hay
  Alembic en el backend): `Repository.transition_work_item` ahora lee
  versión, valida la transición, y hace `UPDATE ... WHERE id=:id AND
  version=:expected_version`; si otra escritura ganó la carrera
  (`rowcount==0`), reintenta desde una lectura fresca hasta 5 veces antes de
  levantar `ConcurrentModificationError`.
- **Evento + transición en una sola transacción**: `transition_work_item`
  acepta un `event: ExecutionEvent | None` opcional e inserta el `EventRow`
  en el mismo `with self.database.session()` que el `UPDATE` CAS.
  `Orchestrator._transition` arma el evento y lo pasa en vez de llamar a
  `self._event(...)` por separado después.
- **CLI prefiere la API cuando el backend está vivo**: `project status`
  intenta `GET /api/projects/{id}` (timeout 1s) antes de abrir la DB
  directo; cualquier `httpx.HTTPError` (servidor no está arriba) cae al
  camino de siempre. Verificado en vivo: con el servidor corriendo, el log
  de uvicorn confirma que la petición pasó por ahí.

## Hallazgo real #1 (no el sospechado): longitud de path de Windows

La señal 1 (los 4 tests de `test_vertical_flow.py`/`test_api.py`, más el
`xfail` de `test_task_splitting.py`) seguía fallando **después** de unificar
el lock, con el mismo `git worktree add failed`. Reproducido de forma
aislada y determinista: el mensaje real (oculto detrás del recorte de
`_run()`) es `fatal: '$GIT_DIR' too big`. Confirmado con un script standalone
que compara un path base corto (`C:\agtest`, funciona) contra el path
profundamente anidado que usa `tmp_path` de pytest (`...\pytest-of-
<usuario>\pytest-<N>\<test>0\...`, falla) usando el mismo esquema de
nombres — el worktree por sí solo llegaba a 230 caracteres una vez se usaban
`project_id`/`task_id` como UUID completos (36 caracteres cada uno,
anidados como directorios propios). `core.longpaths=true` (ya configurado)
no cubre esta operación interna de git (el archivo `.git/worktrees/<nombre>/
gitdir`), sólo el checkout de archivos de trabajo.

En producción esto casi nunca se ve (el path base bajo
`Agentarium\workspaces\` es mucho más corto que el de pytest), lo cual
explica por qué "sólo bajo pytest-asyncio en Windows" — la atribución a
pytest-asyncio específicamente era una atribución equivocada; el verdadero
factor común era la profundidad del path, no el event loop.

**Fix**: `GitWorktreeIsolation.prepare()` ya no anida `task_id` completo como
directorio propio ni lo repite en el nombre de rama junto con un prefijo
`attempt-`. Ahora usa `{task_id[:8]}-{attempt}-{token}` como único
componente bajo `worktrees/` (un nivel menos de anidamiento) y como sufijo
de rama — la unicidad real la da `token` (`uuid4().hex[:10]`, 40 bits de
aleatoriedad por intento), el prefijo de 8 caracteres de `task_id` es sólo
una pista legible, no la garantía de unicidad. Verificado: el mismo path
profundo de pytest que antes daba 230 caracteres y fallaba, ahora da 194 y
funciona.

## Hallazgo real #2 (la causa de la señal 2): recuperación global como
efecto secundario de cada comando del CLI

Con el fix de longitud de path aplicado, los 4 tests + el `xfail` quedaron
verdes — pero la verificación en vivo (correr `project status` repetidas
veces mientras `project run` seguía activo contra qwen2.5-coder:7b) volvió a
crashear, esta vez con `InvalidTransition: ready -> passed`, en un punto
distinto del flujo. La causa no tenía nada que ver con locking, WAL ni CAS:

`cli.py`'s `_service()` (llamado por **todo** comando del CLI, incluido
`project status` en su camino de respaldo sin servidor) construía un
`ApplicationService` fresco y llamaba `.initialize()`, que a su vez llama
incondicionalmente a `Repository.recover_interrupted()` — un barrido
**global** que resetea a `READY` cualquier `WorkItem` en `ASSIGNED`,
`RUNNING` o `AWAITING_REVIEW`, sin ninguna forma de distinguir "esto quedó
así por un crash anterior" de "esto lo está procesando activamente otro
proceso ahora mismo". Cada vez que `project status` caía al camino directo
(sin servidor activo), reseteaba silenciosamente el `WorkItem` que el
`project run` en curso estaba procesando — exactamente la transición
inválida observada, en cualquier punto donde el estado real fuera distinto
de `READY` en el momento del reseteo.

Esto no era una condición de carrera sensible al timing: era determinista
cada vez que un segundo comando del CLI tocaba la base mientras había una
tarea en vuelo.

**Fix**: separar `ApplicationService.initialize()` (esquema + recuperación
global) de un nuevo `ensure_ready()` (sólo esquema/directorios, idempotente,
sin recuperación). `cli.py`'s `_service()` ahora usa `ensure_ready()` — la
recuperación global queda sólo en `agentarium init` (acción explícita del
usuario) y en el `lifespan` de la API (arranque real del servidor, que ya lo
hacía así, correctamente). `Repository.recover_interrupted` ganó un
parámetro opcional `project_id` para acotar el barrido; `Orchestrator.
run_project` llama a la versión acotada al propio proyecto justo al
empezar, preservando el caso legítimo (reanudar un proyecto cuyo run
anterior sí se cortó) sin poder tocar tareas de otros proyectos ni de una
ejecución activa en otro proceso del mismo proyecto.

## Consecuencias

El diagnóstico original ("falta un lock") era razonable pero incompleto en
dos sentidos distintos, y ambos requirieron reproducir el fallo real (no una
simulación) para encontrarlos — otra confirmación de la regla ya establecida
de no dar un fix por bueno sólo porque el razonamiento suena bien.

**Verificado en vivo, definitivamente**: proyecto real contra
qwen2.5-coder:7b, 24+ lecturas de `project status` concurrentes (camino
directo a la DB, sin servidor) repartidas a lo largo de ~3 minutos de
ejecución real — cero crashes, cero `InvalidTransition`. El proyecto terminó
`failed`, pero por la razón de calidad de modelo ya documentada en ADR 0021
(candidatos repetidos, colisión entre subtareas hermanas), no por ningún
problema de infraestructura. Verificado también que con el servidor API
activo, `project status` pasa por `GET /api/projects/{id}` (confirmado en el
log de uvicorn) en vez de abrir la base directo.

Suite completa: ruff y mypy limpios, toda la suite en verde sin exclusiones
— los 4 tests preexistentes y el `xfail` de `test_task_splitting.py` ya no
necesitan estar en ninguna lista de "fallos conocidos". Nuevo
`test_isolation_concurrency.py` cubre ciclos concurrentes reales de
`prepare→collect→discard` y lecturas concurrentes durante transiciones.
