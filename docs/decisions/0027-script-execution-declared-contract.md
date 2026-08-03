# ADR 0027: `SCRIPT_EXECUTION` acepta un contrato de ejecución declarado

- Estado: aceptada
- Fecha: 2026-08-03

## Contexto

`SCRIPT_EXECUTION` (ADR 0016) ejecuta cada archivo `.py` entregado sin
argumentos y sólo mira el código de salida. Dos huecos conocidos, ya
descritos como el criterio de salida de P1.3c en `PLANS.md`:

1. No puede validar un contrato CLI real (`python expenses.py input.csv
   --output result.json`) porque nunca pasa argumentos.
2. Un script que traga excepciones (`except Exception as e: print(e)` sin
   `raise`/`sys.exit`) siempre devuelve código 0, pase lo que pase. La
   compuerta no puede distinguir "corrió y funcionó" de "corrió y no hizo
   nada".

El segundo punto no es hipotético: las tres repeticiones de
`csv_expenses_cli`/`qwen3:4b` en P1.2 tenían exactamente esta forma —
nombres de archivo hardcodeados en vez de leer `sys.argv`, y la lógica
envuelta en un `except Exception as e: print(...)` sin salida de error. Ver
`benchmarks/results/p1-baseline-2026-08/findings.md`.

El análogo más cercano ya existe: `FunctionalCheck`
(`backend/agentarium/benchmarks/functional.py`, P1.1b) — `entrypoint`,
`args`, `fixtures`, `produces`, `expect`. Pero esa clase resuelve un
problema distinto: el arnés de benchmark tiene un fixture oculto y una
salida JSON conocida de antemano contra la cual comparar. El orquestador en
vivo, validando un proyecto real de usuario, no tiene eso — no hay oráculo
de valores correctos para un proyecto arbitrario. Por eso este ADR no
reutiliza `FunctionalCheck` tal cual ni copia `fixtures`/`expect`.

**Por qué el work item no puede declarar esto hoy.** `WorkItem`
(`domain/models.py`) sólo tiene campos de prosa libre
(`acceptance_criteria`/`expected_outputs: list[str]`). El único camino que
construye un `WorkItem` es la salida del LLM de planificación
(`planning/contracts.py::TaskProposal`/`SubtaskProposal`, con
`extra="forbid"`) vía `orchestration/engine.py` — no existe una vía manual
o de API para crear uno a mano. Dos ADR previos son evidencia directa y
repetida de que agregar un campo opcional nuevo al contrato del LLM no
resuelve esto por sí solo:

- **ADR 0020**: una frase de prompt explicando el sandbox de ejecución no
  cambió ni una vez la elección de librería del modelo, verificado en vivo.
- **ADR 0024**: `owned_paths`/`shared_component` existen como campos
  opcionales del contrato desde ADR 0023, con soporte de prompt explicando
  su uso. Confirmado **cinco veces en vivo**, across ADR 0023/0024/0025,
  que el modelo sigue sin adoptarlos — seguía dejando `owned_paths` vacío y
  repitiendo el vocabulario viejo en `expected_outputs`.

A diferencia de `owned_paths` (que ADR 0024 pudo derivar mecánicamente del
`expected_outputs` que el modelo ya llena, vía `implicit_path_claims`), un
contrato de ejecución (qué entrypoint, con qué argumentos exactos) no tiene
hoy ningún campo de prosa existente del que derivarse mecánicamente — el
modelo nunca declara "ejecutame con estos argumentos" en ningún campo que
ya use.

## Decisión

Se agrega `ScriptExecutionContract` (`domain/models.py`, junto a
`WorkItem`): `entrypoint: str` (relativo, debe terminar en `.py`),
`args: list[str]` (por defecto vacío, máximo 20), `produces: str | None`
(relativo, opcional). Validación local de paths seguros (sin `/` inicial,
sin `:`, sin segmento `..`) — cuarta copia local de este mismo chequeo en el
codebase (`planning/contracts.py`, `execution/contracts.py`,
`benchmarks/functional.py`), a propósito: evitar importar a través de capas
sigue siendo la práctica establecida acá.

`WorkItem` gana `execution_contract: ScriptExecutionContract | None = None`.
Se persiste como columna `JSON` nueva en `work_items`
(`execution_contract_json`, nullable), agregada al mecanismo de migración
idempotente ya existente en `Database._ensure_work_item_columns` — no hace
falta backfill, `NULL` ya es el valor correcto de cualquier fila anterior a
este cambio.

`ValidationProfileExecutor.validate(...)` gana el kwarg
`execution_contract`. Un contrato declarado dispara el perfil por sí solo
(sin necesitar además la heurística de palabras clave existente — una
señal explícita es más fuerte que cualquier heurística de prosa, exigir
ambas sería una trampa). Con contrato presente:

- Se busca, entre los `.py` entregados, el que coincide por path relativo
  completo con `entrypoint` declarado. Sin coincidencia (incluye el caso de
  cero archivos `.py` entregados) → falla de inmediato nombrando el
  entrypoint declarado, nunca cae en silencio al modo ciego.
- Con coincidencia, se invoca con los `args` declarados, en el mismo
  directorio del script (`cwd = project_root / entrypoint.parent`) — el
  mismo criterio de `cwd` que ya usa el modo ciego. Diverge a propósito de
  `FunctionalCheck`, que usa `cwd=run_root` (la raíz de la entrega): acá se
  prioriza consistencia con los demás `.py` de la misma entrega, que siguen
  corriendo en modo ciego con ese mismo criterio.
- Si la ejecución pasa (código 0 y sin timeout) y hay `produces` declarado,
  se verifica sólo la **existencia** del archivo relativo a ese mismo
  `cwd` — no se compara contenido ni se exige JSON. Es una versión
  deliberadamente más débil que `FunctionalCheck.expect`: no hay fixture
  oculto para un proyecto real de usuario, mismo motivo por el que ADR 0016
  ya dejó fuera de alcance comparar salida real contra artefactos de otra
  tarea del mismo DAG.
- Cualquier otro `.py` entregado que no coincide con el entrypoint sigue
  corriendo en modo ciego exactamente como hoy — el cambio es aditivo, no
  reemplaza el perfil completo.
- Sin contrato, cero cambios de comportamiento.

**Deliberadamente no se toca** `planning/contracts.py`
(`TaskProposal`/`SubtaskProposal`): agregar `execution_contract` ahí como
campo opcional que el LLM podría llenar repetiría exactamente el fallo que
ADR 0020 y ADR 0024 ya confirmaron dos veces en vivo. Consecuencia honesta:
**hoy nada construye un `WorkItem` con `execution_contract` seteado.** El
mecanismo consumidor queda real, persistido y probado de punta a punta
(`engine.py` pasa `item.execution_contract` a `validate(...)` en la
llamada real, no sólo en pruebas aisladas), pero inerte en cualquier
corrida real hasta que exista, en el futuro, una decisión aparte sobre
cómo poblarlo — no se improvisa ninguna heurística de AST para adivinarlo,
como pide explícitamente el criterio de salida de P1.3c.

**No se sube `VALIDATION_CONTRACT_VERSION`** (queda en `profiles-v6`). ADR
0016 sí la subió porque cambiaba el resultado de work items ya
construibles con esa versión — hacía falta invalidar contexto de reintento
que ya no aplicaba. Este cambio no altera el resultado de ningún work item
construible hoy (nada tiene `execution_contract`), así que no hay ningún
requisito obsoleto del que proteger a un reintento. Subir la versión de
todas formas sólo tendría el efecto colateral de descartar, sin motivo,
contexto de reintento válido para fallos ordinarios del modo ciego.

## Consecuencias

- El mecanismo que cierra los dos huecos nombrados en el contexto existe,
  está probado con casos deterministas (incluyendo la forma exacta del bug
  real de `csv_expenses_cli`) y no depende de que ningún modelo adopte
  vocabulario nuevo — porque, a propósito, ningún modelo puede tocarlo
  todavía.
- Límite conocido, documentado en vez de resuelto: no hay hoy ninguna vía
  (LLM ni manual) para que un `WorkItem` real declare un
  `execution_contract`. Cerrar eso es una decisión aparte y futura, no
  parte de este ADR.
- Las hijas de `_attempt_split` y la tarea de consolidación no heredan el
  `execution_contract` del padre (documentado inline en `engine.py`): el
  contrato nombra un entrypoint específico que una hija post-split puede no
  poseer.
- `produces` sólo verifica existencia, nunca contenido — un script que
  produce el archivo declarado con datos incorrectos sigue sin ser
  detectado por esta compuerta. Ese es el mismo límite que ADR 0016 ya
  documentó para la comparación de salida entre tareas del mismo DAG, no
  uno nuevo.
