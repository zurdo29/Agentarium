# ADR 0021: Dividir una tarea amplia en subtareas al agotar intentos

- Estado: aceptada
- Fecha: 2026-07-31

## Contexto

Tres corridas en vivo independientes contra qwen2.5-coder:7b (CSV, documento
de arquitectura, y la API de biblioteca — ver `PLANS.md`) muestran el mismo
patrón: una tarea que bunde varios criterios de aceptación independientes
(ej. "listar/agregar/actualizar/eliminar libros" + "validar préstamo" en una
sola tarea) recibe `changes_requested`, el worker reintenta regenerando
*todo* el entregable, y a menudo repite el candidato byte-for-byte idéntico
al rechazado (el corto-circuito ya existente de "Retry candidate files are
byte-for-byte identical..."). Se agotan los 3 intentos sin progreso real y el
proyecto entero falla. Ya estaba identificado como próximo paso en
`PLANS.md` pero sin diseñar ni implementar.

## Decisión

Cuando una tarea agota sus intentos y tiene ≥2 `acceptance_criteria`, en vez
de fallarla directamente se llama una vez a `technical_manager` (el rol que
ya sabe descomponer trabajo — hasta ahora sólo se usaba una vez, en el
planning inicial) con una operación nueva `"decompose"`, pidiendo 2-4
subtareas cuya unión de criterios cubra literalmente los de la tarea
original. Si la respuesta es válida y cubre todo: se crean las subtareas
(`status=READY`, heredan `dependency_ids`/`allowed_tools`/`authorized_files`/
`risk`/`priority` de la original) más una tarea de consolidación
(`status=BLOCKED`, depende de las subtareas, hereda `expected_outputs`/
`acceptance_criteria` de la original literalmente), se re-apuntan todos los
dependientes existentes de la tarea original hacia la consolidación
(`Repository.retarget_dependency`, nuevo — un solo `UPDATE` sobre
`dependencies`, la tabla es la única fuente de verdad de `dependency_ids`),
y la tarea original se cancela (`CANCELLED`, transición legal desde
`CHANGES_REQUESTED`/`RUNNING` según `state_machine.py`).

Si la llamada a `technical_manager` falla (`RoleExecutionError`), la
respuesta no valida contra el contrato, o no cubre todos los criterios
originales: cae al comportamiento actual (`FAILED`), sin integrar una
división a medias.

**Alcance deliberado: sólo 2 de los 4 puntos de "agotar intentos" en
`engine.py`.** Entran `_request_changes` (rechazo semántico/técnico) y el
catch de `InvalidPlan | WorkspaceRejected | IsolationError` en
`_execute_work_item` (incluye el corto-circuito de candidato idéntico). NO
entran: el catch de `RoleExecutionError` en `_execute_work_item` (el worker
ni produjo candidato — dividir no ayuda a un fallo de proveedor), ni
`re_evaluate_artifact`/`evaluate_operator_candidate` (flujos de recuperación
ya iniciados por un humano, que puede decidir por su cuenta). Esta exclusión
es intencional, no un olvido.

**Freno de recursión** — ver la revisión de 2026-08-02 más abajo, que
reemplazó el mecanismo original. La versión inicial marcaba una subtarea con
el prefijo de título `"[subtarea] "` y evitaba una columna nueva porque no
existe Alembic en el backend; resultó insuficiente y se corrigió con una
columna `split_depth` persistida.

**El chequeo de finalización de `run_project`** se amplió de `all(status is
COMPLETED)` a `all(status in {COMPLETED, CANCELLED})` — si no, una tarea
cancelada-y-reemplazada bloquearía para siempre que el proyecto termine,
aunque sus subtareas se completen bien.

**Bug encontrado y corregido de paso**: `_colliding_dependency_paths` (ADR
0019) rechazaba a las subtareas por escribir en el mismo path que la tarea
original (ahora `CANCELLED`) ya había reclamado — el candidato cancelado
nunca se integró, así que su reclamo sobre un path es irrelevante. Se agregó
una excepción explícita: artefactos de tareas `CANCELLED` no cuentan como
"dueños" de un path para efectos de colisión. Verificado en vivo con un
script standalone (ver más abajo) que sin este fix la división completa
fallaba con "Workspace file paths collide...".

## Consecuencias

Es una compuerta mecánica para decidir *cuándo* dividir (≥2 criterios, no
recursivo), pero la *calidad* de la división depende del modelo — no hay
garantía de que `technical_manager` proponga una partición útil, sólo de que
si no cubre los criterios originales no se usa.

**Verificado en vivo contra el proveedor mock**: recreado el flujo completo
con un revisor forzado a rechazar siempre una tarea de dos criterios — la
tarea se dividió, ambas subtareas se completaron, la consolidación se
completó, el dependiente aguas abajo se re-apuntó correctamente, y el
proyecto terminó `COMPLETED`.

**Verificado en vivo contra qwen2.5-coder:7b** (workspace
`bfab46f3-6ab8-4729-9561-f29963777538`, mismo objetivo de biblioteca que
motivó este ADR): el mecanismo funcionó exactamente como se diseñó — cuatro
tareas distintas ("listar", "actualizar", "eliminar libros" y "validación
del préstamo") agotaron intentos y se dividieron (2, 4, 4 y 2 subtareas
respectivamente), cada consolidación quedó correctamente bloqueada esperando
sus hijas, y la tarea final del proyecto quedó re-apuntada a las cuatro
consolidaciones en vez de a las tareas originales (ninguna de las cuatro
tareas canceladas sigue apareciendo en ningún `dependency_ids` del resto del
DAG). El freno de recursión también se sostuvo: ninguna subtarea, aun
agotando sus propios 3 intentos, intentó dividirse de nuevo. (Cierto para
subtareas, que es lo único que se pudo observar entonces; el hueco estaba en
las tareas de **consolidación** — ver la revisión de 2026-08-02 al final.)

El proyecto terminó `failed` de todos modos, por dos causas reales y
separadas del mecanismo de división en sí:

1. **El modelo repite candidatos idénticos también a nivel de subtarea.** La
   mayoría de las subtareas fallaron con el mismo error "Retry candidate
   files are byte-for-byte identical..." que motivó este ADR — dividir el
   trabajo no cambia el comportamiento subyacente de qwen2.5-coder:7b, sólo
   le da un alcance más chico para repetirlo.
2. **Colisión de archivo entre subtareas de divisiones distintas.** Varias
   subtareas fallaron con "Workspace file paths collide..." contra
   `library_api.py`/`library.py` — no contra la tarea original cancelada (ya
   exceptuada), sino contra otra tarea HERMANA, no relacionada por
   dependencias, que ya había reclamado ese path (p. ej. el endpoint
   "agregar libros", que sí completó). No es un bug nuevo de la compuerta de
   colisión (ADR 0019): las cuatro tareas originales de endpoints
   ("listar"/"agregar"/"actualizar"/"eliminar libros") ya eran hermanas sin
   relación de dependencia entre sí en el DAG que generó `technical_manager`,
   así que ya compartían el mismo riesgo de colisión *antes* de dividir nada
   — dividir simplemente multiplicó el número de tareas independientes que
   compiten por el mismo archivo convencional. Es un hallazgo real y
   separado sobre la calidad de la descomposición inicial del DAG (tareas
   que en la práctica necesitan compartir un archivo deberían declararse
   dependientes entre sí, no hermanas), no algo para corregir dentro de este
   ADR.

**Test end-to-end marcado `xfail` bajo pytest, no relacionado con esta
lógica**: el test `test_task_splits_into_subtasks_after_exhausting_retries_
and_project_completes` reproduce de forma fiable una race condition
preexistente y ya documentada de `isolation/git_worktree.py` bajo
pytest-asyncio en Windows (`collect()` no toma el lock de proyecto que sí
toman `prepare()`/`discard()`) — confirmado corriendo la misma secuencia
fuera de pytest, donde completa sin problema. Los 4 tests unitarios directos
de `test_task_splitting.py` (que no pasan por git worktree) sí verifican la
lógica de división de forma determinista y confiable.

## Revisión 2026-08-02: el freno de recursión pasa a `split_depth` persistido

El freno original miraba el prefijo de título `"[subtarea] "`. Es insuficiente
por dos motivos, uno de los cuales se observó en vivo:

1. **Una tarea de consolidación no lleva ese prefijo.** Su título es
   `"Consolidar subtareas: …"`, así que el freno no la alcanzaba. En la corrida
   de verificación de ADR 0026 (workspace
   `75ae6456-b10b-4dc9-82d7-82e7e2e759cf`) la consolidación de una división
   agotó sus propios intentos y se dividió a su vez, generando
   `"Consolidar subtareas: Consolidar subtareas: Validation Implementation"` y
   una segunda generación de hijas. Una nieta falló y arrastró al proyecto.
   Ese camino era inalcanzable hasta ADR 0026: sin divisiones que llegaran a
   crear hijas, nunca hubo una consolidación capaz de agotar intentos.
2. **El título es texto de un modelo, no un tipo.** Derivar de él una decisión
   de control de flujo es la misma clase de dependencia de compliance que ADR
   0018, 0020, 0023 y 0024 documentaron como poco fiable.

**Lo que se implementó**: una columna `split_depth` en `work_items`
(`INTEGER NOT NULL DEFAULT 0`, con el mismo patrón de migración manual e
idempotente de ADR 0022/0023 — el argumento original de "no hay Alembic" ya no
aplica, ese patrón existe desde ADR 0022) y el campo correspondiente en
`WorkItem`.

- Una tarea planificada nace con `split_depth = 0`.
- Todo lo que produce una división —hijas **y** consolidación por igual—
  hereda `split_depth = item.split_depth + 1`.
- `_attempt_split` se niega a dividir cuando `split_depth >= MAX_SPLIT_DEPTH`
  (1), y emite `task_split_depth_exhausted` para dejarlo auditable.
- El chequeo por prefijo de título desapareció por completo del código de
  producción; el prefijo sigue existiendo sólo como texto visible del título.

**Máximo una división automática por linaje.** Una hija o una consolidación
que agota intentos falla, y ahí se detiene el automatismo.

**Fallar no es el final**: `ApplicationService.retry_work_item`,
`recover_artifact` y `submit_candidate` ya aceptan una tarea `FAILED` y
extienden su presupuesto de intentos, así que la reparación manual sigue
disponible sin código nuevo. Reabrir una tarea **no** reinicia el linaje: su
`split_depth` se conserva, así que un segundo agotamiento tampoco divide.

**Pruebas**: herencia de `split_depth` en hijas y consolidación (incluida la
lectura desde la base, no sólo el objeto en memoria); tres casos
parametrizados que confirman que lo que frena es la profundidad y no la forma
del título (prefijo `"[subtarea] "`, título de consolidación, y un título
arbitrario sin prefijo), cada uno terminando en `FAILED` con el evento nuevo y
sin crear ninguna tarea; y una prueba de que una hija fallida se reabre por el
camino manual y sigue sin dividirse al agotarse otra vez. Suite completa:
170/170 en verde.

**Consecuencia aceptada**: un linaje agotado ahora termina en una falla que
requiere intervención humana en vez de seguir descomponiéndose solo. Es
deliberado — la segunda generación de la corrida de ADR 0026 no aportó
progreso, sólo tareas más chicas repitiendo el mismo candidato.
