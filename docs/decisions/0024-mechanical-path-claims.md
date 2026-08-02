# ADR 0024: Reclamo de archivo mecánico y encadenamiento de subtareas que colisionan

- Estado: aceptada
- Fecha: 2026-08-02

## Contexto

Trabajo futuro declarado explícitamente al cierre de ADR 0023, y primer punto
de los próximos pasos de `PLANS.md`: hacer que el preflight de plan y el
validador de `DecomposeProposal` miren `expected_outputs` solapados como señal
de conflicto, no sólo `owned_paths`.

El motivo está documentado con evidencia en vivo en ADR 0023 (workspace
`487c5194-296c-4e2c-9b33-7f5dd2e1835b`): qwen2.5-coder:7b devolvió 4 subtareas
todas con `expected_outputs: ["api.py"]`, `owned_paths` vacío y
`output_strategy: "exclusive"`, pese a que el prompt de `decompose` pide
explícitamente lo contrario. Los dos puntos de detección de ADR 0023 leen sólo
`owned_paths`, así que en la práctica nunca se activan: el modelo sigue usando
el campo viejo para decir "este archivo es mío".

**Segundo hallazgo, encontrado leyendo el código antes de escribir nada, y más
grave que el primero**: ADR 0023 convirtió esa colisión en una pérdida de
datos *silenciosa* dentro de una misma división.

1. `_attempt_split` (ADR 0021 + 0023) fuerza el mismo `shared_component` y
   `output_strategy = FRAGMENT` en las 2-4 hijas, de forma incondicional.
2. `_colliding_dependency_paths` (ADR 0019 + 0023) exime exactamente esa
   combinación: candidato con `shared_component` igual al del dueño actual del
   path y estrategia no-exclusiva.
3. `WorkspaceMaterializer.stage()` escribe con `os.replace`. **No hay ningún
   mecanismo de fusión de contenido en ninguna parte del sistema**;
   `output_strategy` sólo existía como vocabulario de contrato y bifurcación de
   prompt.

Encadenando los tres: hija A integra `api.py`, hija B integra `api.py`, el
gate la exime por grupo compartido, y el contenido de A se pierde sin ningún
aviso. Es la misma pérdida de datos que motivó ADR 0019, reintroducida por la
exención de ADR 0023. En ADR 0021 el mismo choque ocurría entre divisiones
*distintas* (grupos distintos), donde el gate lo rechazaba ruidosamente y el
proyecto fallaba; ADR 0023 lo volvió silencioso dentro de una división.

Hacer la detección mecánica sin resolver esto habría producido un sistema que
ve el conflicto y pierde los datos igual.

## Decisión

Reclamo de archivo mecánico en los dos puntos de detección, más resolución
mecánica —no rechazo— dentro de `_attempt_split`.

### 1. Reclamo derivado de `expected_outputs`

`planning/contracts.py` expone `implicit_path_claims(expected_outputs)` y
`merge_path_claims(owned_paths, expected_outputs)`, y ambos `TaskProposal` y
`SubtaskProposal` ganan `claimed_paths()`.

La activación es deliberadamente conservadora, en la misma disciplina de
`SCRIPT_EXECUTION` (ADR 0016): sólo cuenta un token sin espacios, con path
relativo seguro (sin `/` inicial, sin `:`, sin `..`) y con una extensión real
—al menos una letra, para que `v1.2` no pase por archivo—. `api.py`,
`docs/design.md` y `src/routes/books.ts` son reclamos; `resultado`,
`Documento de arquitectura en Markdown`, `informe final.md` y `/etc/passwd`
no. Es un helper de detección, no una compuerta: descarta en silencio en vez
de rechazar.

### 2. Los dos puntos de detección leen el reclamo completo

- `Orchestrator._detect_owned_path_conflicts` (preflight de plan) compara
  `claimed_paths()` en vez de `owned_paths`. Cada conflicto reportado incluye
  `declared_via` (`owned_paths` / `expected_outputs`) para que el evento y el
  payload de `plan_revision` digan por qué campo se detectó. La exención por
  agrupamiento no cambia.
- `DecomposeProposal.validate_no_unresolved_path_overlap` compara
  `claimed_paths()`. El mensaje pasa de `owned_paths overlap` a
  `path claim overlap`, porque ahora el reclamo puede venir de cualquiera de
  los dos campos.

### 3. `_attempt_split` resuelve el solapamiento, no lo rechaza

Rechazar duro en el validador habría sido una **regresión**: el caso real de
ADR 0023 (4 subtareas `exclusive` sobre `api.py`) pasaría de dividirse a
`ValidationError` → `_attempt_split` devuelve `False` → tarea `FAILED`. Peor
que el estado previo. En su lugar:

- **Normalización previa a la validación**
  (`Orchestrator._normalized_decompose_content`): antes de
  `DecomposeProposal.model_validate`, cada subtarea recibe el
  `shared_component` de la división, `output_strategy = fragment`, y
  `owned_paths` completado desde sus reclamos implícitos (`merge_path_claims`,
  recortado a los 20 que admite el contrato). `_attempt_split` ya reagrupaba
  todo eso incondicionalmente después de validar; hacerlo antes evita que el
  default `exclusive` del modelo tumbe una división utilizable, y convierte el
  path que el modelo nombró en `expected_outputs` en propiedad real y
  persistida.
- **Agrupamiento por reclamo compartido**
  (`Orchestrator._group_overlapping_claims`): componentes conexas sobre
  "comparten al menos un path". Transitivo a propósito — si A y B comparten
  `api.py` y B y C comparten `models.py`, las tres van a la misma cadena;
  poner a B en dos cadenas distintas reintroduciría la sobreescritura.
- **Encadenamiento sólo de lo que colisiona**: dentro de un grupo, la primera
  hija queda `READY` con `FRAGMENT` y las dependencias del padre; cada
  siguiente queda `BLOCKED`, con `output_strategy = PATCH` y
  `dependency_ids = [*deps del padre, hija anterior]`. Una subtarea que
  escribe su propio archivo sigue siendo un grupo de una sola hija: `READY`,
  `FRAGMENT`, en paralelo, exactamente como antes de este ADR.
- **La consolidación depende de la cola de cada cadena** más las subtareas
  independientes. La cola cubre transitivamente a los eslabones anteriores, así
  que no hacen falta aristas duplicadas.
- **Evento nuevo `task_split_chained_overlapping_paths`** cuando al menos un
  grupo tiene más de una hija, con las cadenas y los paths reclamados. El
  evento existente `task_split_created` lleva ahora `chains` en su metadata.

`PATCH` funciona de verdad acá porque la maquinaria ya existía: el prompt de
`work` tiene su rama `patch` ("extendés un archivo que ya posee otra tarea
aprobada"), `ContextBuilder.operational` conserva `dependency_artifacts` en
reintentos para `patch`/`consolidation` (ADR 0023), y
`ContextBuilder.dependency_artifacts` compara los criterios de cada dependencia
contra su propio artefacto, no contra los del consumidor, así que una hija
encadenada sí ve el contenido de la anterior.

## Lo que NO cambió

- `_colliding_dependency_paths` no se debilitó ni se tocó. El caso por defecto
  sigue rechazando igual que ADR 0019, y la exención de ADR 0023 sigue
  existiendo — sólo que ahora las hijas que la usarían ya no compiten por el
  mismo archivo al mismo tiempo.
- Ningún prompt cambió. `WORKSPACE_PROMPT_VERSION` queda igual. Es deliberado:
  la lección repetida de ADR 0018, 0020 y 0023 es que una instrucción de
  prompt a un modelo chico debe tratarse como probablemente ignorada. Este ADR
  no le pide nada nuevo al modelo; lee el campo que ya usa.
- No hay columnas nuevas ni migración. `owned_paths`, `shared_component` y
  `output_strategy` ya existen desde ADR 0023.

## Pruebas

Nueve tests nuevos, todos sobre la forma exacta del hallazgo en vivo
(`owned_paths` vacío, `output_strategy` default, archivo nombrado sólo en
`expected_outputs`):

- Contrato: `implicit_path_claims` acepta paths y rechaza prosa, versiones y
  paths inseguros; `merge_path_claims` no duplica; `DecomposeProposal` rechaza
  el solapamiento declarado sólo vía `expected_outputs` y acepta archivos
  distintos.
- Preflight de plan: tres tareas hermanas sin `owned_paths`, dos apuntando a
  `api.py` — el conflicto se detecta con `declared_via: ["expected_outputs"]`,
  la revisión lo resuelve, y la tercera tarea queda intacta.
- `_attempt_split`, los tres casos pedidos: solapamiento total (una cadena,
  cabeza `FRAGMENT`/`READY`, seguidora `PATCH`/`BLOCKED`, consolidación
  dependiendo sólo de la cola), solapamiento parcial (se encadena únicamente el
  par que colisiona; la tercera sigue en paralelo), y sin solapamiento
  (comportamiento de ADR 0021 sin cambios, sin evento de encadenamiento). Más
  una prueba negativa: dos subtareas con `expected_outputs: ["resultado"]` no
  se encadenan, porque eso no es un reclamo de archivo.

Suite completa: 144/144 en verde, sin exclusiones (incluye
`test_ollama_smoke`, con Ollama corriendo). Ruff y mypy limpios. Lint y
pruebas de interfaz en verde.

## Verificación en vivo — parcial, documentada sin maquillar

Mismo objetivo de biblioteca que ADR 0021/0023, mismo proveedor
(qwen2.5-coder:7b), workspace `8be5cde9-f1aa-4824-a41f-20a98596dd23`
("Biblioteca API v5"). Terminó `failed` al 60% (6/10 tareas completadas).

**Lo que la corrida sí confirmó:**

- El plan inicial fue de **9 tareas hermanas, cada una con un archivo
  distinto** (`project_scope.txt`, `api_endpoints.txt`,
  `database_schema.sql`, `api_code.py`, `loan_validation.py`,
  `unit_tests.py`, `api_documentation.md`, `code_review_report.txt`,
  `deployment_report.txt`) y `owned_paths` vacío en las nueve. El preflight
  leyó los nueve reclamos implícitos y **no disparó ningún conflicto**, que es
  la respuesta correcta: no había solapamiento. Control negativo real sobre
  salida de modelo, del mismo tipo que la no-activación de `SCRIPT_EXECUTION`
  en ADR 0017.
- Los `expected_outputs` en prosa de la tarea de cierre ("Código fuente de la
  API REST en Python", "Base de datos SQLite con estructura adecuada") no
  produjeron ningún reclamo implícito. La heurística conservadora se sostuvo
  contra texto real, no sólo contra el test unitario.
- Cuarta confirmación consecutiva de que el modelo **no adopta el vocabulario
  nuevo**: `owned_paths` vacío en las 9 tareas del plan y ausente por completo
  en la respuesta de `decompose` (ver abajo).

**Lo que la corrida NO pudo ejercitar, y por qué** (corregido tras
investigarlo a fondo en ADR 0025 — la primera lectura de esta corrida fue
equivocada y se deja anotada como tal): `_attempt_split` **sí se llamó** en
las tres tareas fallidas, y el rol `decompose` respondió en las tres. El
encadenamiento no se ejercitó porque la división se abandonó un paso después,
en `DecomposeProposal.model_validate`: el modelo devolvió una subtarea con
`expected_outputs: []`, que viola el `min_length=1` del contrato de
`SubtaskProposal` (ADR 0021). Sin hijas, no hay nada que encadenar.

La lectura inicial ("`_attempt_split` nunca llegó a llamarse, las rutas de
`InvalidPlan` fallan directo") era **incorrecta**: se apoyó en un filtro de
eventos demasiado estrecho, que no mostraba las corridas de
`technical_manager`, y en confundir `engine.py:556`/`633` —que están en
`re_evaluate_artifact` y `evaluate_operator_candidate`, entradas manuales del
operador— con la ruta autónoma de `engine.py:476`, que sí llama a
`_on_attempts_exhausted` desde siempre. Ver ADR 0025.

**Sonda dirigida sobre salida real del modelo** (no una segunda corrida de
proyecto): se llamó la operación `decompose` real contra la tarea real ya
fallida `Develop API Implementation`, y se pasó la respuesta por el código
nuevo. El modelo devolvió:

```json
{"subtasks": [
  {"title": "Implement API Endpoints", "expected_outputs": ["api_code.py"],
   "output_strategy": "exclusive"},
  {"title": "Set Up SQLite Database", "expected_outputs": ["database_setup.py"],
   "output_strategy": "exclusive"}
]}
```

Sin la clave `owned_paths` siquiera, y con `exclusive` declarado
explícitamente. Tras la normalización quedaron
`owned=['api_code.py']` / `owned=['database_setup.py']`, ambos `fragment`; el
agrupamiento las dejó **en paralelo**, correctamente, porque reclaman archivos
distintos. Antes de este ADR esas dos subtareas habrían nacido con
`owned_paths` vacío. Confirma la mitad de normalización contra salida real; la
mitad de encadenamiento sigue confirmada sólo por tests, porque el modelo no
produjo un solapamiento en esta corrida.

**Lectura honesta**: igual que en ADR 0023, la parte del mecanismo que no
depende del modelo funciona y está probada, y la parte que necesitaba que el
modelo produjera una colisión concreta no se pudo observar en vivo. No se
repitió la corrida ni se varió el objetivo buscando una colisión: eso sería
*fishing*, mismo criterio que ADR 0023.

### Confirmación posterior: el preflight sí dispara con un modelo real

La corrida de ADR 0025 (workspace `13ee7f71-5b5c-4c4c-951a-c238a19befda`,
mismo objetivo y modelo, sin cambiar nada de este ADR) produjo el caso que
acá no se había podido observar. `technical_manager` planificó dos tareas
hermanas que reclamaban el mismo archivo, y el preflight lo detectó:

```json
{"task_a": "task3", "task_b": "task5", "paths": ["api_endpoints.py"],
 "declared_via": ["expected_outputs"]}
```

`owned_paths` vacío en las nueve tareas del plan, como siempre: la detección
salió **enteramente** del campo viejo. Antes de este ADR ese conflicto era
invisible para el preflight.

Lo que pasó después también es informativo. Se pidieron las dos revisiones de
plan; el modelo puso `output_strategy: "patch"` en una de las dos tareas pero
dejó `shared_component: null`, así que la condición de agrupamiento (mismo
`shared_component` no nulo en ambos lados **y** estrategia no-exclusiva en
ambos) no se cumplió y el conflicto se reportó correctamente como no resuelto
(`plan_owned_path_conflict_unresolved`). En ejecución,
`_colliding_dependency_paths` rechazó a la segunda tarea las tres veces, y esa
tarea terminó `failed` sin pisar el archivo de la primera — que es exactamente
el comportamiento que ADR 0019 debe dar y que este ADR no debilitó.

Quinta confirmación consecutiva de que el modelo adopta el vocabulario nuevo
sólo a medias: esta vez usó `output_strategy` pero no `shared_component` ni
`owned_paths`. La detección mecánica es lo único que no dependió de eso.

De paso, otra confirmación de la heurística conservadora: las subtareas que
devolvió el `decompose` de esa corrida declararon `expected_outputs` en prosa
("Archivo requirements.txt con todas las dependencias"), y no produjeron
ningún reclamo implícito.

## Consecuencias

- La detección de propiedad de archivo deja de depender de que el modelo
  adopte vocabulario nuevo: lee `expected_outputs`, que es el campo que
  efectivamente usa. Es la parte que ADR 0023 dejó explícitamente pendiente.
- El agujero de sobreescritura silenciosa que ADR 0023 abrió dentro de una
  división queda cerrado por construcción, sin depender de compliance del
  modelo, igual que la herencia incondicional de `_attempt_split`.
- Costo aceptado: una división cuyas hijas comparten archivo ahora se ejecuta
  en serie en vez de en paralelo, y si un eslabón falla los siguientes quedan
  `BLOCKED`. Es el precio de no perder contenido; el alternativo (paralelo con
  sobreescritura) no es aceptable, y rechazar la división directamente es peor
  que el estado previo.
- Límite conocido, no resuelto acá: la heurística de "esto parece un archivo"
  es textual. Un `expected_output` en prosa que igual termina produciendo un
  archivo concreto (`"un módulo Python con los endpoints"`) no se detecta.
  `_colliding_dependency_paths` sigue siendo la red final para eso.
- Límite preexistente que sigue abierto: el corte de `_attempt_split` por
  `original_criteria.issubset(covered_criteria)` (matching textual exacto)
  sigue siendo la causa por la que la división de ADR 0023 nunca llegó a crear
  hijas. Es de ADR 0021, no de este ADR, y queda fuera de alcance.
- **Hallazgo real de esta verificación** (ver ADR 0025, que lo investiga y
  corrige lo que se puede corregir): la división se abandona cuando el modelo
  devuelve una subtarea con `expected_outputs: []`, porque el contrato de
  `SubtaskProposal` exige `min_length=1`. Pasó en las tres tareas fallidas de
  esta corrida. No se corrigió acá ni en ADR 0025: relajar ese contrato es una
  decisión aparte.
