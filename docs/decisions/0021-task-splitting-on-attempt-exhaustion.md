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

**Freno de recursión sin migración de esquema**: una subtarea se marca con
el prefijo de título `"[subtarea] "`; si ella misma agota intentos, no
vuelve a dividirse, falla normal. No se agregó una columna `is_subtask`
porque no existe Alembic ni `ALTER TABLE` en el backend
(`Database.create_all()` sólo crea tablas faltantes) y `runtime/
agentarium.db` ya tiene datos reales acumulados.

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
agotando sus propios 3 intentos, intentó dividirse de nuevo.

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
