# Estado, forma de trabajo y próximos pasos

Este archivo es el punto de entrada para continuar Agentarium con otro modelo o
en otra sesión. Complementa `AGENTS.md`, la arquitectura en `docs/architecture/`
y las decisiones detalladas en `docs/decisions/`.

## Qué es Agentarium

Agentarium transforma un objetivo en un brief, un DAG de tareas y artefactos
verificables. El backend FastAPI coordina roles, SQLite, proveedores LLM,
worktrees, validaciones, testing y revisión. La interfaz permite crear y
ejecutar proyectos, observar eventos, métricas, artefactos y aprobaciones, y
abrir una vista previa segura de los resultados web.

El runtime local actual usa Ollama con `qwen3:4b` por defecto; `qwen3:8b` y
`qwen2.5-coder:7b` también están descargados localmente y disponibles para
comparar por rol. El proveedor mock sigue siendo la prueba determinista de
regresión.

Nota operativa: Ollama corre como proceso local aparte y puede caerse sin
avisar (pasó una vez en esta sesión, sin relación con el código). Si una
llamada a `/api/runtime/provider` devuelve 409 con un mensaje de
`ConnectTimeout`, no es un bug de Agentarium: correr `ollama list` para
confirmar, y si no responde, reiniciar con `ollama serve`.

## Estado actual

Ya están implementados:

- Planificación estructurada con prompts versionados y contratos Pydantic.
- Ejecución real con Ollama, además de mock y OpenAI-compatible.
- Persistencia SQLite, API, CLI, interfaz, SSE, métricas y recuperación.
- Materialización de archivos con límites, checksums y rutas protegidas.
- Un worktree y una rama efímera por intento; sólo se integra un candidato
  aprobado.
- Perfiles objetivos para sintaxis, estructura web y comportamientos exigidos,
  incluyendo ejecución real (no sólo sintaxis) de scripts que se declaran como
  herramienta ejecutable (`SCRIPT_EXECUTION`, ADR 0016).
- Tester técnico y revisor semántico con la compuerta técnica como fuente de
  verdad. El revisor ahora también recibe el contenido de los artefactos de
  dependencia y evalúa consistencia contra ellos cuando existen (ADR 0017).
- Rechazo mecánico de candidatos cuyas rutas de archivo colisionan con una
  tarea no relacionada del mismo proyecto (ADR 0019).
- Aclaración en el prompt del worker de que un script sujeto a
  `SCRIPT_EXECUTION` corre sin red y sin instalación de paquetes, sólo
  biblioteca estándar (ADR 0020). Verificado en vivo que esto por sí solo
  **no** cambia la elección de librerías de terceros de qwen2.5-coder:7b —
  ver detalle en ADR 0020 y en la sección de dominio API más abajo.
- Cuando una tarea con ≥2 criterios de aceptación agota sus intentos, se
  divide automáticamente en 2-4 subtareas más una tarea de consolidación, en
  vez de fallar directo (ADR 0021). Los dependientes existentes se
  re-apuntan a la consolidación; la tarea original se cancela. Verificado en
  vivo contra el proveedor mock (end-to-end) y contra qwen2.5-coder:7b con el
  objetivo real de biblioteca — ver detalle en ADR 0021.
- Lock real entre procesos para git worktree (`prepare`/`collect`/
  `integrate`/`discard` comparten uno solo), SQLite en WAL con
  `busy_timeout`, transiciones de `WorkItem` con CAS optimista por versión,
  evento+transición en una sola transacción, y el CLI prefiere la API
  cuando el backend está vivo en vez de abrir la DB directo (ADR 0022). La
  causa real de los crashes que motivaron esto no era locking: era longitud
  de path de Windows (`fatal: '$GIT_DIR' too big`, no relacionado con
  pytest-asyncio como se pensó) y `recover_interrupted()` disparándose como
  efecto secundario de cada comando del CLI, reseteando tareas activas de
  otro proceso. Ver detalle completo en ADR 0022.
- Ningún timeout o fallo de proveedor (tester, revisor, director, gestor
  técnico) puede tumbar una petición HTTP sin controlar: todos degradan la
  tarea o el proyecto a un estado terminal auditable (ADR 0015).
- Propiedad explícita de archivo en el contrato de cada tarea
  (`owned_paths`, `shared_component`, `output_strategy`), con un preflight
  a nivel de plan que detecta solapamientos entre tareas hermanas antes de
  ejecutar el DAG, y herencia incondicional de `shared_component`/
  `FRAGMENT` en las hijas de una división (ADR 0021) para que no dependan
  de que el modelo use el vocabulario nuevo. `_colliding_dependency_paths`
  (ADR 0019) se extendió, no se debilitó: el caso por defecto sigue
  rechazando igual (ADR 0023). Verificado en vivo con resultado mixto — ver
  detalle en ADR 0023 y en la sección de aprendizajes más abajo.
- Reintentos, recuperación de candidatos, revisiones posteriores y candidatos
  presentados por un operador sin saltarse las validaciones.
- Vista previa local aislada por proyecto.
- Code Context Engine (CCE) conectado por MCP para búsqueda de código y
  memoria entre sesiones (`context_search`, `session_recall`, etc.); ver
  `CLAUDE.md`.

La última verificación completa pasó con:

- Ruff sin errores.
- MyPy sin errores en 47 archivos.
- **134 de 134 pruebas backend en verde, sin exclusiones ni `xfail`**
  (ejecutadas con `TMP`/`TEMP` apuntando a un directorio propio porque el
  `pytest-of-<usuario>` del sistema tenía permisos corruptos en esta
  máquina — ver nota operativa más abajo). Los 4 tests que venían fallando
  de forma preexistente (`test_vertical_flow.py` × 3,
  `test_api.py::test_api_exposes_completed_vertical_flow`) y el `xfail` de
  `test_task_splitting.py` ya no necesitan ninguna exclusión — la causa real
  (longitud de path, ver ADR 0022) está corregida.
- Lint y build de la interfaz (no reverificado en esta sesión, sin cambios de
  interfaz).

Sólo quedan avisos de deprecación de dependencias; no hay fallos conocidos en
la suite.

### Nota operativa: codepage de la consola con acentos

En Git Bash sobre Windows, si la codepage activa no es UTF-8 (`chcp` muestra
850 u otra), pasar un `goal` con tildes/eñes al CLI de `agentarium` lo
corrompe silenciosamente **incluso en la base de datos**, no sólo en la
terminal — se guarda con el carácter de reemplazo Unicode. Antes de crear un
proyecto con texto en español acentuado:

```bash
chcp.com 65001
PYTHONUTF8=1 PYTHONIOENCODING=utf-8 ./.venv/Scripts/agentarium.exe project create "..."
```

`chcp 65001` sólo no alcanza: hace falta además `PYTHONUTF8=1
PYTHONIOENCODING=utf-8` para el wrapper `agentarium.exe`. Si se crea un
proyecto corrupto por este motivo, se puede borrar directo de
`runtime/agentarium.db` (tablas `projects` y `execution_events`) siempre que
siga en estado `draft` sin DAG ni workspace asociado.

## Cómo se ha venido trabajando

El ciclo usado hasta ahora es:

1. Dar a Agentarium una solicitud real sin ayudar al planificador.
2. Observar brief, DAG, intentos, eventos, candidatos, tester y revisor.
3. Comparar el producto integrado con la solicitud mediante comprobaciones
   independientes (leer los archivos reales, no confiar en el status ni en el
   resumen del artefacto).
4. Corregir la causa general, no el proyecto de ejemplo.
5. Añadir pruebas positivas y negativas, especialmente con otro dominio.
6. **Verificar cada fix en vivo contra el modelo real que reveló el
   problema**, no sólo con tests sintéticos — varias veces esa verificación
   reveló un segundo bug (a veces en el propio fix recién hecho). Repetir
   hasta que una corrida completa salga limpia de ese problema específico, sin
   perseguir cosas indefinidamente: si aparece algo nuevo y claramente
   distinto, se documenta como hallazgo separado en vez de seguir iterando en
   el mismo hilo.
7. Registrar las decisiones arquitectónicas en `docs/decisions/` y ejecutar
   `.\test.ps1` antes de dar algo por cerrado.

No se considera éxito que una demo "se vea bien". Deben coincidir objetivo,
alcance, contratos de tareas, archivos, comportamiento y evidencia.

## Aprendizajes recientes

### Sobreajuste inicial (ADR 0012, 0013, 0014)

El primer juego sirvió para crear validaciones objetivas, pero una prueba
posterior con una aplicación de inventario reveló sobreajuste y falsos éxitos.
Las correcciones generales resultantes:

- El DAG debe cubrir literalmente entregables, alcance y criterios de éxito del
  brief. Si el modelo omite algo, se agrega una tarea terminal de contrato.
- Una ambigüedad no bloqueante no puede reemplazar el objetivo principal.
- Los contratos de videojuegos sólo se activan con contexto explícito de juego.
  "Navegación por teclado" en un formulario usa reglas de accesibilidad, no
  movimiento de un jugador.
- Las aplicaciones comunes pueden activar contratos reutilizables para CRUD,
  búsqueda, filtros, confirmación de acciones destructivas, estado vacío,
  localStorage, importación/exportación JSON y guía de uso.
- Un reintento con archivos idénticos al candidato rechazado se corta antes de
  gastar worktree, tester y revisor.
- Si la compuerta técnica ya falló, la revisión semántica se acota a 3
  criterios representativos.

### Dominio CSV: primera prueba no-web/no-juego (ADR 0015, 0016)

Comparando qwen3:4b, qwen3:8b y qwen2.5-coder:7b con el mismo objetivo
(herramienta CLI que procesa un CSV de gastos) vía la API real:

- **Bug de resiliencia (ADR 0015)**: un timeout del tester o el revisor
  (`RoleExecutionError`) no estaba capturado dentro de `_evaluate_candidate`,
  a diferencia de la llamada al worker. Tumbaba la petición `/run` con un 500
  y, como `asyncio.gather` no cancela tareas hermanas cuando una levanta una
  excepción, otra tarea lista del mismo ciclo seguía mutando estado en
  segundo plano tras la respuesta fallida — producía además un 409 espurio al
  cambiar de proveedor justo después. Corregido en las 4 llamadas a rol que
  lo necesitaban (tester, revisor inicial, revisor focalizado, y más tarde
  también `brief`/`plan` en `plan_project`, que tiene el mismo problema pero
  usa `_fail_planning` en vez de `_request_changes` porque todavía no existe
  un `WorkItem`).
- **Hallazgo principal (ADR 0016)**: ninguna compuerta ejecutaba el artefacto
  que afirma ser un programa. Con qwen3:4b el proyecto terminó `completed`
  con las seis tareas aprobadas, pero no existía ningún script Python en el
  resultado integrado (el README documentaba `python expenses.py`, que no
  existe) y `summary.json` tenía la clave de mes repetida cuatro veces. Tester
  y revisor aprobaron igual. Corregido con el perfil `SCRIPT_EXECUTION`:
  rechaza si los criterios reclaman herramienta ejecutable sin ningún `.py`
  declarado, y si existe, lo ejecuta de verdad exigiendo código de salida 0.
  Alcance deliberadamente limitado a "en la misma entrega" (un solo artefacto)
  — el caso de una tarea *distinta* fabricando salida sin ejecutar nada quedó
  fuera de este ADR y se resolvió más tarde por otra vía (ver ADR 0017/0019).

### Dominio documento de arquitectura: repetido 4 veces (ADR 0017, 0018, 0019)

Segundo objetivo deliberadamente sin código (documento de arquitectura en
Markdown para un sistema de reservas de restaurante), repetido varias veces
contra qwen2.5-coder:7b en particular porque fue el que reveló más problemas:

- **Confirmación real de la prueba negativa de ADR 0016**: `SCRIPT_EXECUTION`
  no se activó ni una vez en ninguna corrida de este dominio (verificado en
  eventos reales). La disciplina de activación conservadora se sostiene con
  evidencia de extremo a extremo, no sólo con el test unitario sintético.
- **Bug de consistencia entre tareas (ADR 0017)**: el revisor semántico nunca
  recibía el contenido de los artefactos de dependencia — sólo el worker lo
  veía. Una tarea que combinaba el trabajo de otras tres produjo un glosario
  distinto al que ya existía en un archivo separado, y el revisor aprobó
  ambos porque no tenía con qué comparar. Corregido: `_review_payload` ahora
  incluye `dependency_artifacts`, y se inyecta un criterio estándar
  (`DEPENDENCY_CONSISTENCY_CRITERION`) sólo cuando hay dependencias
  aprobadas.
- **Bug descubierto verificando el fix anterior**: `ContextBuilder.
  dependency_artifacts` exigía que `acceptance_criteria_addressed` del
  artefacto de dependencia fuera superconjunto exacto de los criterios
  propios de esa tarea. Un modelo real dejó ese campo vacío en una entrega ya
  aprobada, y el filtro la excluía en silencio — el fix de ADR 0017 ni
  siquiera llegaba a activarse. Corregido: sólo se excluye cuando hay una
  señal real de desajuste (criterios declarados que no comparten nada con la
  dependencia), no por una lista vacía.
- **Fuga de contexto interno (ADR 0018)**: el worker recibe `project.
  decisions` (el registro interno de Agentarium, p. ej. "Alcance inicial
  conservador... un único hito vertical") sin ninguna aclaración de que es
  meta-información del orquestador. qwen2.5-coder:7b lo copió literalmente
  como si fuera una decisión de arquitectura real — la tercera vez que se vio
  esa familia de fuga con el mismo modelo (antes como jerga suelta "Single
  Hito Vertical"). Corregido con una aclaración explícita en el prompt de
  `work`. Es una instrucción de prompt, no una compuerta mecánica: no hay
  garantía de que un modelo la respete siempre.
- **Bug mecánico y más grave (ADR 0019)**: dos tareas *sin relación de
  dependencia entre sí* declararon el mismo path (`docs/design.md`). Al
  integrarse ambas, la segunda sobrescribió el contenido de la primera sin
  ningún aviso — pérdida de datos silenciosa, no sólo inconsistencia
  semántica. A diferencia de ADR 0017, esto es determinista y no requiere
  juicio de un revisor. Corregido con `_colliding_dependency_paths`, que
  rechaza un candidato cuya ruta ya pertenece a una tarea no emparentada.
  **La primera versión de este fix tenía el mismo tipo de bug que ADR
  0015/0017 ya habían enseñado a buscar**: sólo miraba `dependency_ids`
  directas, y rechazó a la propia tarea de cierre de ADR 0012 porque esa
  tarea sólo depende directamente del último nodo del DAG, no de los nodos
  que originalmente crearon el archivo varios pasos atrás. Corregido a
  cierre transitivo completo (`_transitive_dependency_ids`), con un test que
  reproduce esa forma exacta de DAG (abuelo → padre → cierre).

Cada corrección de esta sección se verificó ejecutando el objetivo real contra
el modelo que reveló el problema, no sólo con `test.ps1`.

### Dominio API con dependencias de terceros: tercer dominio, hallazgo refutado en vivo (ADR 0020)

Tercer objetivo deliberadamente distinto a los dos anteriores (no CLI de un
archivo, no documento sin código): API REST en Python para una biblioteca
(libros, autores, préstamos, SQLite), contra qwen2.5-coder:7b — el modelo que
más problemas había revelado hasta ahora.

- **Hallazgo (ADR 0020)**: el worker no tiene visibilidad del sandbox de
  ejecución. Reachó por Flask/Flask-SQLAlchemy (lo idiomático para "API REST
  en Python"); `SCRIPT_EXECUTION` lo ejecutó de verdad y lo rechazó
  correctamente por `ModuleNotFoundError: No module named 'flask'`
  (workspace `5875edef-b9d1-4410-a8c7-c4eaf4c2abaf`). A diferencia de ADR
  0016, esto no es específico de un dominio: cualquier objetivo que combine
  `SCRIPT_EXECUTION` con la elección natural de un paquete de terceros pisa
  lo mismo. Irónicamente FastAPI sí está instalado (dependencia del propio
  backend de Agentarium) pero el modelo no tenía forma de saberlo.
- **Fix aplicado**: aclaración explícita en la instrucción `work` de
  `llm/prompts.py` — un script sujeto a `SCRIPT_EXECUTION` corre sin red ni
  instalación de paquetes, sólo biblioteca estándar de Python.
  `WORKSPACE_PROMPT_VERSION` avanza a `workspace-v6`.
- **Verificado en vivo y refutado**: se repitió el mismo objetivo contra el
  mismo modelo (workspace `f3dd4e9f-2962-4761-a83d-5fac57576ff7`). El worker
  volvió a elegir Flask en los dos intentos generados; la aclaración de
  prompt no cambió la decisión ni una vez. El proyecto falló por otra vía
  (revisor rechazó dos veces por manejo de errores, tercer intento repitió
  candidato idéntico) antes de que `SCRIPT_EXECUTION` llegara a activarse de
  nuevo en esa corrida en particular, así que el `ModuleNotFoundError`
  puntual no se reobservó — pero la pregunta real ("¿deja de usar librerías
  de terceros al avisarle del sandbox?") quedó respondida: no. Detalle
  completo y opciones que quedan abiertas (instalar dependencias reales,
  aceptar el límite, o probar otro modelo) en ADR 0020.
- **Confirmación de paso, sin acción**: los mismos 4 tests de
  `test_vertical_flow.py`/`test_api.py` fallan igual en HEAD limpio (sin el
  fix) — una race condition preexistente en el isolation de git worktrees
  (`collect()` en `git_worktree.py` no toma el lock que sí toman
  `prepare()`/`discard()`), reproducible bajo pytest-asyncio en Windows pero
  no en una llamada aislada directa. No se investigó a fondo ni se corrigió
  — es un hallazgo aparte, no relacionado con ADR 0020.

### División de tareas amplias en subtareas (ADR 0021)

Backlog ya identificado (ver secciones anteriores) implementado esta sesión:
cuando una tarea con ≥2 criterios agota intentos, se divide en 2-4 subtareas
más una tarea de consolidación en vez de fallar directo. Diseñado con
`EnterPlanMode`, validado con un agente `Plan` antes de escribir código
(encontró 3 correcciones a mi diseño inicial: `WorkItemRow` no tiene columna
`dependency_ids` — sólo vive en `DependencyRow`, simplificando el nuevo
método de repositorio; las subtareas deben nacer `READY` directo, no
`BLOCKED`+promovidas, porque sus dependencias ya están `COMPLETED` por
construcción; y nombrar la tarea de consolidación "integración" colisionaría
con un título ya usado por el plan por defecto del mock).

- **Bug real encontrado en la primera verificación end-to-end** (no en los 4
  tests unitarios directos, que no tocan git worktrees): `_colliding_
  dependency_paths` (ADR 0019) rechazaba a las subtareas por escribir en un
  path que la tarea original — ya `CANCELLED` — había reclamado antes de
  cancelarse. Corregido excluyendo artefactos de tareas `CANCELLED` del
  chequeo de colisión (su candidato nunca se integró, el reclamo es
  irrelevante). Encontrado y corregido igual que ADR 0017/0019: el primer
  intento del fix no funcionaba hasta correrlo de verdad contra el flujo
  completo, no sólo contra los tests unitarios aislados.
- **Hallazgo colateral, marcado `xfail` en su momento, resuelto después**: el
  test end-to-end de esta funcionalidad reproducía el mismo fallo de
  `git worktree add` que los 4 tests preexistentes. Se marcó `xfail` en vez
  de dejarlo rojo sin explicación. La causa real (longitud de path, no
  pytest-asyncio) se encontró y corrigió en ADR 0022 de esta misma sesión;
  el marcador `xfail` ya se quitó, el test es verde de forma confiable.
- **Verificación en vivo contra qwen2.5-coder:7b** (workspace
  `bfab46f3-6ab8-4729-9561-f29963777538`, mismo objetivo de biblioteca): el
  mecanismo funcionó exactamente como se diseñó — cuatro tareas distintas
  ("listar", "actualizar", "eliminar libros" y "validación del préstamo")
  agotaron intentos y se dividieron (2, 4, 4 y 2 subtareas), cada
  consolidación quedó bloqueada correctamente esperando sus hijas, la tarea
  final se re-apuntó a las cuatro consolidaciones (ninguna tarea cancelada
  sigue apareciendo en ningún `dependency_ids` del resto del DAG), y el
  freno de recursión se sostuvo en las cuatro divisiones. El proyecto
  terminó `failed` igual, por dos causas reales y separadas del mecanismo:
  (1) el modelo repite candidatos idénticos también a nivel de subtarea —
  dividir no cambia ese comportamiento, sólo le da un alcance más chico; (2)
  varias subtareas de divisiones *distintas* colisionaron entre sí por
  escribir al mismo archivo convencional (`library_api.py`) — no es un bug
  nuevo de ADR 0019, las cuatro tareas de endpoints originales ya eran
  hermanas sin dependencia entre sí en el DAG de `technical_manager`, así
  que ya compartían ese riesgo antes de dividir nada; dividir sólo
  multiplicó cuántas tareas independientes compiten por el mismo archivo.
  Detalle completo en ADR 0021.
- **Se encontró de paso, no perseguido**: el hallazgo de colisión entre
  hermanas (punto 2 arriba) es un problema real y separado de calidad de
  descomposición del DAG inicial — tareas que en la práctica necesitan
  compartir un archivo deberían declararse dependientes entre sí, no
  hermanas. No se investigó ni se corrigió esta sesión.

**Un detalle operativo de esta verificación que resultó ser mucho más
importante de lo que parecía**: la primera corrida de esta verificación
crasheó con `InvalidTransition: ready -> awaiting_review` al correr
`agentarium project status` en paralelo mientras `project run` seguía
escribiendo. En el momento se anotó como "no correr `project status` en
paralelo" — un diagnóstico apresurado e incorrecto. La causa real (`_service()`
del CLI disparando una recuperación global en cada comando, sin relación con
WAL/locking) se investigó a fondo y se corrigió en ADR 0022 de esta misma
sesión. Con el fix, correr `project status` en paralelo mientras un
`project run` está en curso es seguro — verificado con 24+ lecturas
concurrentes a lo largo de una corrida real completa, cero crashes.

### Concurrencia y consistencia de estado (ADR 0022)

Pedido explícito del usuario, priorizado incluso antes que decidir el
destino de ADR 0020: unificar el lock de git worktree y endurecer
SQLite/CLI. El diagnóstico inicial ("falta lock en `collect()`") resultó
incompleto en dos formas distintas, ambas encontradas verificando en vivo
el fix anterior — no por análisis de código:

1. Con el lock ya unificado, los 4 tests preexistentes seguían fallando
   igual. La causa real: `fatal: '$GIT_DIR' too big`, un límite de git para
   Windows en su propia contabilidad interna de worktrees
   (`.git/worktrees/<nombre>/gitdir`), no cubierto por `core.longpaths`.
   Confirmado de forma determinista comparando un path base corto (funciona)
   contra el anidado profundo de `tmp_path` de pytest (falla) con el mismo
   esquema de nombres. "Sólo bajo pytest-asyncio en Windows" era una
   atribución equivocada — el factor real era la profundidad del path, que
   pytest genera y producción normalmente no. Corregido acortando el
   esquema de nombres de worktree/rama (`GitWorktreeIsolation.prepare`).
2. Con el path corregido, la verificación en vivo (correr `project status`
   repetidas veces durante un `project run` real) volvió a crashear —
   `InvalidTransition: ready -> passed`, en un punto distinto. Causa real:
   `_service()` del CLI (usado por *todo* comando, incluido `status` en su
   camino sin servidor) llamaba `ApplicationService.initialize()`, que
   dispara `recover_interrupted()` de forma **global e incondicional** —
   resetea a `READY` cualquier tarea `ASSIGNED`/`RUNNING`/
   `AWAITING_REVIEW`, sin poder distinguir "esto quedó de un crash anterior"
   de "otro proceso lo está procesando ahora mismo". No era sensible al
   timing: pasaba siempre que un segundo comando del CLI tocara la base
   mientras había una tarea en vuelo. Corregido separando
   `ApplicationService.ensure_ready()` (sólo esquema, sin recuperación,
   usado por el CLI en cada comando) de `initialize()` (recuperación
   global, sólo en `agentarium init` y en el arranque de la API); la
   recuperación acotada al propio proyecto ahora vive al principio de
   `Orchestrator.run_project`.

**Verificación final**: proyecto real contra qwen2.5-coder:7b, 24+ lecturas
de `project status` concurrentes repartidas en ~3 minutos de ejecución —
cero crashes. El proyecto terminó `failed`, pero por las mismas razones de
calidad de modelo ya documentadas en ADR 0021 (candidato repetido, colisión
entre subtareas hermanas), no por infraestructura. Confirmado también que
con la API activa, `project status` pasa por `GET /api/projects/{id}` (log
de uvicorn) en vez de abrir la base directo. Suite completa: 128/128 en
verde, sin exclusiones. Detalle completo en ADR 0022.

### Propiedad explícita de archivos en el DAG (ADR 0023)

Pedido explícito del usuario, con diseño propio incluido, a partir del
hallazgo colateral de ADR 0021 (subtareas de divisiones distintas
colisionando por escribir al mismo `library_api.py`). Diseñado con
`EnterPlanMode` y validado con un agente `Plan` antes de escribir código
(corrigió 3 puntos: `_colliding_dependency_paths` sigue siendo el único
punto real de aplicación reactiva; las columnas nuevas necesitan el mismo
patrón de migración manual de ADR 0022, no hay Alembic; y
`ContextBuilder.operational` necesita dejar de vaciar `dependency_artifacts`
en reintentos para las estrategias `patch`/`consolidation`, si no la
estrategia queda inservible en cuanto el primer intento no pasa revisión).

- **Contrato**: `OutputStrategy` (`exclusive`/`fragment`/`patch`/
  `consolidation`) más `owned_paths`/`shared_component`/`output_strategy`
  en `TaskProposal`, `SubtaskProposal` y `WorkItem`. Preflight de nivel de
  plan (`_resolve_owned_path_conflicts`) que detecta hermanas con
  `owned_paths` solapados y pide revisión a `technical_manager` (operación
  nueva `plan_revision`) antes de persistir ningún `WorkItem`.
  `_colliding_dependency_paths` extendida (no debilitada): exime una
  colisión sólo con `shared_component` compartido no nulo y estrategia
  no-exclusiva del candidato; el caso por defecto sigue rechazando igual
  que ADR 0019. `_attempt_split` (ADR 0021) ahora fuerza
  `shared_component`/`FRAGMENT` en sus hijas **de forma incondicional**,
  sin depender de lo que el modelo declare en su propio `SubtaskProposal`.
- **Verificación en vivo, resultado mixto** (workspace
  `487c5194-296c-4e2c-9b33-7f5dd2e1835b`, mismo objetivo de biblioteca que
  ADR 0021, contra qwen2.5-coder:7b): el plan inicial esta vez no reprodujo
  el patrón de hermanas colisionando — `technical_manager` generó un único
  task grueso en vez de cuatro por endpoint, así que el preflight de plan
  nunca se disparó (varianza de granularidad entre corridas, no evidencia
  a favor ni en contra). Pero el mismo patrón reapareció un nivel más
  abajo: al agotar intentos esa tarea única y disparar `decompose`,
  `technical_manager` devolvió 4 subtareas todas con
  `expected_outputs: ["api.py"]` y `output_strategy: "exclusive"`,
  `owned_paths` vacío — el modelo siguió señalando "este archivo" con el
  campo viejo (`expected_outputs`), no con el nuevo (`owned_paths`), pese a
  que el prompt de `decompose` se lo pide explícitamente. Mismo tipo de
  hallazgo negativo que ADR 0020: una instrucción de prompt no garantiza
  que un modelo chico la siga. El proyecto no llegó a chocar por esto
  (`_attempt_split` habría forzado `shared_component`/`FRAGMENT` en las 4
  hijas de todas formas, sin depender del modelo) — falló antes, por una
  causa preexistente y no relacionada de ADR 0021 (el criterio combinado
  original no es cubierto textualmente por los criterios individuales de
  las subtareas). Terminó `failed` al 71% (5/7 tareas completadas) sin
  llegar a ejercitar la herencia de propiedad contra artefactos reales.
  Detalle completo en ADR 0023.
- **Lectura honesta**: la mitad del mecanismo que no depende de compliance
  del modelo (herencia incondicional en `_attempt_split`) es sólida por
  diseño. La mitad que sí depende de que el modelo declare `owned_paths`
  (el preflight de plan, y un `decompose` que etiquete bien un solapamiento
  desde el origen) no se pudo confirmar ni refutar — nunca hubo un caso con
  `owned_paths` realmente poblado para ejercitarla. No se repitió la
  corrida buscando una reproducción más "limpia": variar el objetivo o
  reintentar hasta que el modelo colisione de la forma exacta que se
  quiere observar no es verificación, y el hallazgo ya obtenido (el modelo
  ignora el campo nuevo, igual que en ADR 0020) es suficiente para cerrar
  esta iteración.
- **No implementado, candidato natural para la próxima iteración**: hacer
  que el preflight y el validador de `DecomposeProposal` también miren
  `expected_outputs` solapados como señal de conflicto (no sólo
  `owned_paths`) — es el campo que el modelo efectivamente sigue usando
  para indicar "este archivo es mío". Convertiría la detección en mecánica
  de verdad sin depender de que el modelo adopte vocabulario nuevo.

### Comparación de modelos (evidencia insuficiente todavía)

Con ambos dominios, en varias corridas: qwen3:8b tiende a ser el más completo
en contenido pero el más lento (hasta ~968s en el caso CSV, ~768s en el de
arquitectura); qwen3:4b suele ser el más rápido; qwen2.5-coder:7b fue el que
más problemas reveló (fuga de contexto interno recurrente, a veces responde en
inglés pese a un objetivo en español, repite candidato idéntico con más
frecuencia al recibir `changes_requested`). Con el número de runs hecho hasta
ahora **no alcanza para convertir esto en una recomendación de modelo por
rol** — hace falta repetir con más objetivos y contar ocurrencias, no
impresiones de una sola corrida.

## Proyectos de auditoría que quedan visibles

No volverlos verdes desactivando contratos ni aprobando manualmente. Sirven
para comprobar una futura estrategia de reparación y como evidencia de en qué
estado estaba el sistema antes de cada corrección.

- `Inventario local - prueba genérica final`: seis tareas completadas y una
  revisión fallida. El modelo afirmó haber agregado edición, borrado,
  confirmación, estado vacío y guía, pero repitió los mismos archivos.
- `CSV gastos - qwen3-4b` (workspace `9d87210d-bdae-4c46-9563-f02fa6fa7da1`):
  terminó `completed` sin que exista un script ejecutable real. Verificado:
  correr `SCRIPT_EXECUTION` contra este mismo workspace ahora lo rechaza.
- `Doc arquitectura - qwen2.5-coder-7b` (workspace
  `d4679178-dd67-4fb2-ae26-3d9ab5db6ed9`): terminó `completed` con dos
  glosarios inconsistentes entre sí. Evidencia del estado anterior a
  ADR 0017/0018/0019.
- `Biblioteca API - qwen2.5-coder-7b` (workspace
  `5875edef-b9d1-4410-a8c7-c4eaf4c2abaf`): `failed`, evidencia del
  `ModuleNotFoundError` que motivó ADR 0020 (antes del fix de prompt).
- `Biblioteca API v2 - qwen2.5-coder-7b (post prompt fix)` (workspace
  `f3dd4e9f-2962-4761-a83d-5fac57576ff7`): `failed`, evidencia de que el fix
  de ADR 0020 no cambió la elección de Flask del modelo.
- `Biblioteca API v3 - qwen2.5-coder-7b (post task-splitting)` (workspace
  `bfab46f3-6ab8-4729-9561-f29963777538`): `failed`, pero con cuatro
  divisiones reales exitosas (mecanismo de ADR 0021 confirmado); falló por
  repetición de candidatos a nivel de subtarea y colisión entre hermanas —
  ver detalle en la sección de ADR 0021 y en ADR 0021 mismo.
- `Verificacion concurrencia final - modelo real` (workspace
  `febe2a7d-d98c-41a3-b0e5-307d1a9b55e3`): `failed` por las mismas razones
  de calidad de modelo de siempre (candidato repetido, colisión entre
  hermanas), pero es la evidencia de la verificación final de ADR 0022 —
  24+ lecturas de `project status` concurrentes durante la corrida, cero
  crashes.
- `Biblioteca API v4 - qwen2.5-coder-7b (post ownership)` (workspace
  `487c5194-296c-4e2c-9b33-7f5dd2e1835b`): `failed` al 71% (5/7 tareas). Sin
  colisión de plan (el plan inicial no fue de 4 hermanas esta vez), pero el
  `decompose` disparado al agotar intentos reprodujo el mismo patrón de
  ADR 0021 (4 subtareas apuntando a `api.py`, todas `exclusive`, sin
  `owned_paths`) — evidencia de que el modelo sigue sin adoptar el
  vocabulario nuevo. Ver detalle en ADR 0023.

## Próximos pasos recomendados

Tres dominios de prueba (CSV, documento de arquitectura, API con
dependencias) ya se ejecutaron contra proveedores reales. ADR 0015-0019 están
implementados, con tests, y confirmados en vivo. ADR 0020 está implementado
y verificado en vivo, pero la verificación fue negativa: el fix de prompt no
cambió el comportamiento del modelo. ADR 0021 (división de tareas amplias en
subtareas) está implementado, con tests, y verificado en vivo contra el
proveedor mock (end-to-end, `COMPLETED`) y contra qwen2.5-coder:7b. ADR 0022
(concurrencia y consistencia de estado) está implementado, con tests, y
verificado en vivo de forma definitiva — suite completa en verde sin
exclusiones. ADR 0023 (propiedad explícita de archivos) está implementado,
con tests, y verificado en vivo con resultado mixto: la mitad del mecanismo
que no depende del modelo (herencia incondicional en `_attempt_split`) es
sólida; la mitad que depende de que el modelo declare `owned_paths` no se
pudo ejercitar porque el modelo sigue sin usar ese campo. En este orden:

1. Hacer que el preflight de ADR 0023 y el validador de `DecomposeProposal`
   también miren `expected_outputs` solapados (no sólo `owned_paths`) como
   señal de conflicto — es el campo que el modelo efectivamente sigue
   usando para indicar "este archivo es mío", según la verificación en vivo
   de ADR 0023. Convertiría la detección en mecánica de verdad sin depender
   de que el modelo adopte el vocabulario nuevo. Candidato más directo para
   continuar.
2. Decidir qué hacer con ADR 0020 antes de seguir agregando dominios nuevos:
   ¿instalar dependencias declaradas en un sandbox real (toca autoridad de
   red, requiere aprobación explícita), aceptar el límite y documentarlo
   como alcance conocido, o probar si qwen3:8b/qwen3:4b sí respetan la
   aclaración de prompt donde qwen2.5-coder:7b no lo hizo? Necesita decisión
   del usuario, no es una corrección de código de rutina.
3. Recopilar más runs (≥3 por modelo, mismos objetivos) antes de convertir
   cualquier observación de calidad/velocidad por modelo en una preferencia
   de modelo por rol.
4. Después avanzar hacia departamentos, plugins y políticas de autoridad
   administrables.

## Reglas para el próximo agente

- Preservar el worktree sucio y no modificar cambios ajenos.
- No modificar `.openai/hosting.json`, credenciales, CI/CD ni autoridad de red.
- No añadir reglas específicas de un dominio sin una prueba negativa en otro.
- No integrar un candidato porque su resumen diga que funciona.
- Mantener al tester técnico y al revisor semántico separados.
- Registrar decisiones arquitectónicas nuevas en `docs/decisions/`.
- **No dar un fix por terminado sólo porque los tests pasan, ni porque el
  diagnóstico suene razonable**: si es razonable, correrlo en vivo contra el
  escenario real que lo motivó. Varias veces en esta sesión esa
  verificación reveló un segundo bug, incluso en el propio fix recién
  escrito (ADR 0017 y ADR 0019 ambos tuvieron una primera versión
  incompleta que sólo se detectó así). ADR 0022 es el caso más extremo: el
  diagnóstico inicial ("falta un lock") sonaba razonable y no lo era —
  fueron necesarias tres verificaciones en vivo seguidas para llegar a las
  dos causas reales (longitud de path de Windows, y una recuperación
  global disparándose en cada comando del CLI), ninguna de las cuales tenía
  que ver con locking.
- Ejecutar antes de entregar:

  `powershell -NoProfile -ExecutionPolicy Bypass -File .\test.ps1`
