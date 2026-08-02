# ADR 0026: Mapeo de criterios por identificador en `decompose`

- Estado: aceptada
- Fecha: 2026-08-02

## Contexto

Último PR de la serie P0, con diseño dado por el usuario y alcance limitado a
`decompose`. Sale de las dos verificaciones en vivo anteriores: `_attempt_split`
llega hasta la llamada al modelo y se abandona después, siempre por lo mismo —
que el modelo no reproduce literalmente lo que el contrato le exige.

Las dos causas observadas, una por corrida:

1. Workspace `8be5cde9`: una subtarea con `expected_outputs: []` contra el
   `min_length=1` de `SubtaskProposal` → `ValidationError`.
2. Workspace `13ee7f71`: el chequeo
   `original_criteria.issubset(covered_criteria)` con matching textual exacto.
   El padre pedía "Listar libros", "Agregar libros", …; el modelo propuso
   subtareas sobre `requirements.txt`, `README.md` y entorno de desarrollo. Ni
   un criterio coincidía palabra por palabra.

La segunda es la más profunda: **le pedíamos al modelo que copiara texto
literal como forma de garantizar cobertura**. Es la misma dependencia de
compliance que ADR 0018, 0020, 0023 y 0024 ya mostraron que no se sostiene con
un modelo chico, sólo que acá el costo no es una etiqueta perdida sino la
división entera.

## Decisión

El texto de los criterios deja de viajar de vuelta desde el modelo. El
orquestador numera, el modelo reparte identificadores, y el orquestador copia
el texto original.

- **Índice** (`planning/contracts.py`): `acceptance_criteria_index(criteria)`
  produce `[{"id": "ac-1", "text": …}, …]`, y `_attempt_split` lo manda en el
  payload de `decompose` como `acceptance_criteria_index`.
- **Asignación** (`SubtaskProposal.acceptance_criteria_ids`): lista de ids.
  El contrato valida **sólo la forma** (`^ac-[1-9][0-9]{0,2}$`, normalizando
  mayúsculas); si un id existe de verdad, si está repetido y si queda alguno
  sin asignar sólo se puede decidir contra la tarea padre, que el contrato no
  ve, así que eso vive en el orquestador.
- **Resolución** (`Orchestrator._resolve_criteria_assignment`): devuelve las
  posiciones del padre por subtarea, o `None` si el mapeo es inservible. Es
  inservible cuando un id no existe, cuando el mismo id va a dos subtareas (o
  dos veces a la misma), cuando una subtarea queda sin ningún criterio, o
  cuando algún criterio del padre no lo reclama nadie.
- **Partición determinista**
  (`Orchestrator._deterministic_criteria_assignment`): si el mapeo es
  inservible —incluido el caso de que el modelo no mande ningún id, que es lo
  que hacía hasta ahora— los criterios del padre se reparten en bloques
  contiguos y balanceados. Nunca deja una hija vacía: si el modelo propuso más
  subtareas que criterios, la cantidad de hijas baja a la cantidad de
  criterios. Se emite `task_split_criteria_partitioned` para que la corrida
  quede auditable.
- **El texto siempre sale del padre**: la hija recibe
  `[item.acceptance_criteria[i] for i in asignación]`. La reescritura del
  modelo (`acceptance_criteria`, que sigue existiendo y sigue siendo
  obligatoria por contrato) nunca se usa para construir la hija.
- **Cobertura por construcción**: tanto la resolución como la partición cubren
  cada criterio del padre exactamente una vez, así que el viejo chequeo
  textual `issubset` desaparece — no se debilitó, se volvió imposible de
  violar.
- **Herencia de outputs**: si una subtarea **no declara ningún**
  `expected_outputs`, hereda los del padre y, si de ahí no sale ningún claim,
  también los claims efectivos del padre
  (`merge_path_claims(owned_paths, expected_outputs)`). `min_length=1` se
  mantiene intacto: se satisface antes de validar, igual que ADR 0024 hace con
  `owned_paths`.
- **Prompt** (`decompose-v3`): explica el índice y pide el reparto de ids,
  aclarando que el texto se copia del padre y no hace falta reproducirlo fiel.
  Es la única parte que depende del modelo, y **es la única que tiene una capa
  mecánica debajo si falla**.

Un output declarado pero que no es un archivo ("un informe de lectura") **no**
se reemplaza por el del padre: es una respuesta real, y heredar el `api.py` del
padre encadenaría subtareas que nunca comparten archivo.

## Consecuencia de diseño: qué hacen ahora las hijas que comparten archivo

Cuando el modelo omite `expected_outputs` en todas las subtareas, todas heredan
el mismo archivo del padre, y el agrupamiento de ADR 0024 las encadena en
secuencia con `PATCH` en vez de dejarlas pisarse. No hizo falta código nuevo:
es el punto 7 del pedido y sale solo de la combinación.

## Cambio de comportamiento observable

`test_split_falls_back_to_failed_when_the_proposal_drops_a_criterion` afirmaba
que una propuesta que repite un criterio y omite otro abandona la división y
falla la tarea. Ahora esa propuesta se **repara** con la partición
determinista. El test se reescribió para afirmar el contrato nuevo
(`test_a_proposal_that_drops_a_criterion_falls_back_to_a_partition`), no se
borró: sigue cubriendo exactamente la misma entrada.

## Pruebas

`backend/tests/test_decompose_criteria_mapping.py`, 13 tests:

- índice numerado desde 1; el contrato rechaza `ac-0`, `ac_1`, `1`, `AC-1x`,
  `ac-` y normaliza `AC-2` → `ac-2`;
- resolución correcta a posiciones del padre; y rechazo de id desconocido, id
  repetido entre subtareas, id repetido dentro de una, criterio faltante,
  subtarea sin ids, y ausencia total de ids (la forma que producía el modelo
  antes de este contrato);
- la partición cubre cada criterio exactamente una vez y nunca deja una hija
  vacía, verificado exhaustivamente para 2..11 criterios × 2..4 subtareas;
- extremo a extremo por `_attempt_split`: las hijas reciben el texto del padre
  y no la reescritura del modelo; un mapeo roto cae en la partición y emite el
  evento; sin `expected_outputs` las hijas heredan `api.py` y quedan
  encadenadas (`FRAGMENT`/`READY` → `PATCH`/`BLOCKED`); un entregable en prosa
  declarado no hereda el archivo del padre ni encadena.

El proveedor mock ahora emite `acceptance_criteria_ids` correctos, así que la
prueba end-to-end de ADR 0021 ejercita el camino feliz del mapeo y la partición
queda cubierta por los tests dirigidos.

Suite completa: 167/167 en verde. Ruff y mypy limpios.

## Verificación en vivo

Corrida única, mismo objetivo de biblioteca y mismo proveedor
(qwen2.5-coder:7b), workspace `75ae6456-b10b-4dc9-82d7-82e7e2e759cf`.
Terminó `failed` al 58,3% (7/12 tareas completadas).

**La división produjo hijas reales por primera vez en toda esta serie.**
`Validation Implementation` (2 criterios) agotó intentos por candidato
repetido, `_attempt_split` corrió, y las dos hijas se crearon **y ambas
terminaron `COMPLETED`**.

**El modelo adoptó el contrato nuevo al primer intento**, cosa que no había
pasado con ningún campo nuevo desde ADR 0023. Respuesta literal:

```json
{"title": "Prevent Re-issuance Logic", "expected_outputs": ["prevent_reissuance.py"],
 "acceptance_criteria_ids": ["ac-1"], "output_strategy": "exclusive"}
{"title": "Error Message Handling",   "expected_outputs": ["error_messages.py"],
 "acceptance_criteria_ids": ["ac-2"], "output_strategy": "exclusive"}
```

`_resolve_criteria_assignment` lo aceptó (no se emitió
`task_split_criteria_partitioned` en toda la corrida) y cada hija recibió el
texto exacto del padre:

| tarea | criterios |
| --- | --- |
| `Validation Implementation` (padre) | "The system should prevent the re-issuance of a loaned book." + "Appropriate error messages should be returned…" |
| `[subtarea] Prevent Re-issuance Logic` | el primero, literal |
| `[subtarea] Error Message Handling` | el segundo, literal |

De paso, tercera confirmación de la normalización de ADR 0024 contra salida
real: el modelo **no** mandó `owned_paths` en ninguna subtarea, y las hijas
quedaron con `owned_paths` derivado de `expected_outputs`
(`["prevent_reissuance.py"]`, `["error_messages.py"]`). Archivos distintos, así
que quedaron en paralelo con `FRAGMENT` — correcto, no había nada que
encadenar. La partición determinista no se ejercitó en vivo: sigue cubierta
sólo por tests, porque el modelo no rompió el mapeo ni una vez.

### Hallazgo nuevo, no perseguido: el freno de recursión no cubre la consolidación

`_attempt_split` se niega a dividir una tarea cuyo título empieza con
`"[subtarea] "`, pero **una tarea de consolidación no lleva ese prefijo**. En
esta corrida la consolidación de la primera división agotó sus propios
intentos (otra vez por candidato repetido) y se dividió a su vez, generando
`Consolidar subtareas: Consolidar subtareas: Validation Implementation` y una
segunda generación de hijas. Una de esas nietas falló y arrastró al proyecto.

Es un camino que hasta ahora era inalcanzable en la práctica: con un modelo
real la división nunca había llegado a crear hijas, así que nunca hubo una
consolidación que pudiera agotar intentos. Lo destapó este ADR al hacer que la
división funcione, pero el hueco es de ADR 0021.

No se corrigió acá por instrucción explícita del usuario ("se termina P0 aunque
aparezca otro motivo de fallo"). El arreglo aparente es extender el freno a los
títulos de consolidación, pero conviene decidir a conciencia si una
consolidación agotada debe fallar, reintentarse con otro alcance, o
efectivamente dividirse una vez.

## Consecuencias

- La cobertura de criterios de una división pasa de "el modelo copió bien el
  texto" a una garantía estructural. Es la última de las tres dependencias de
  compliance que bloqueaban ADR 0021 en la práctica; las otras dos
  (`owned_paths`, `output_strategy`) ya las había cubierto ADR 0024.
- `min_length=1` sigue en pie en `expected_outputs` y `acceptance_criteria`, y
  la reescritura del modelo sigue sin usarse para nada. Ninguna de las dos
  cosas que el pedido prohibía tocar se tocó.
- Riesgo aceptado: la partición determinista reparte por posición, sin
  entender qué criterio va mejor con qué subtarea. Cuando el modelo manda un
  mapeo usable se respeta el suyo, que sí tiene criterio semántico; la
  partición es la red, no el camino principal. El evento
  `task_split_criteria_partitioned` deja ver cuál de los dos ocurrió.
- El título y la descripción de cada hija siguen siendo los del modelo. Si el
  mapeo cayó en la partición, un título puede no describir bien los criterios
  que le tocaron. Es preferible a no dividir, pero es una inconsistencia real
  y visible en los artefactos.
