# ADR 0030: Gate de drift backend/Pydantic ↔ TypeScript (P3.1a)

- Estado: aceptada
- Fecha: 2026-08-06

## Contexto

`app/page.tsx` escribe a mano 10 `type X = {...}` que reflejan lo que la
API devuelve, sin ningún mecanismo que detecte cuándo se desalinean.
Investigar el problema real (no asumido) mostró que es más específico de
lo que "generar TS desde OpenAPI" resolvería:

- Ninguna ruta de `backend/agentarium/api/app.py` declara `response_model=`;
  todas devuelven `dict[str, Any]`/`list[dict[str, Any]]`. El
  `openapi.json` autogenerado no dice nada útil sobre formas de respuesta
  hoy.
- Las formas reales vienen de dos patrones: volcado directo
  (`modelo.model_dump(mode="json")` de una clase de
  `backend/agentarium/domain/models.py`) o diccionarios compuestos
  armados a mano (`project_detail()`, `provider_status()`, `/api/dashboard`)
  que mezclan varios volcados con claves extra calculadas.
- `EventRecord` (TS) no corresponde a `ExecutionEvent.model_dump()`:
  `Repository._event_from_row` nunca construye un `ExecutionEvent`, arma
  el dict a mano con una columna `sequence` que el modelo no tiene.

Agregar `response_model=` a cada ruta habría sido el arreglo "correcto"
en abstracto, pero cambia validación/filtrado real de las respuestas —
exactamente el tipo de cambio funcional que P3.1a decidió no tocar.

## Decisión

Un gate de sólo lectura, sin dependencias nuevas (`typescript` ya es
devDependency; Pydantic v2 expone `model_fields` sin instalar nada):

- `scripts/check-api-contract.mjs` — parsea `app/page.tsx` con
  `ts.createSourceFile` puro (sin `ts.Program`/tsconfig: los tipos
  objetivo son literales autocontenidos) y extrae `{kind, nullable,
  optional, element?, properties?, opaque?}` por tipo.
- `backend/tests/contract_types.py` — el mismo shape del lado Python,
  vía `model_fields` (nunca `is_required()` — ver más abajo) o vía
  `shape_from_value` sobre una respuesta HTTP real.
- `backend/tests/contract_registry.py` — **sólo selección**: qué tipo TS
  corresponde a qué clase Pydantic real o a qué llamada real de la API.
  Ningún campo se declara a mano como string (`"tasks_total": "number"`
  no existe en este código) — sería una segunda fuente de verdad manual
  paralela a Pydantic, descartado explícitamente en revisión.
- `backend/tests/test_type_contract.py` — arma un proyecto real (mismo
  escenario que `test_vertical_flow.py`, proveedor `mock`, determinista),
  levanta `TestClient(create_app(service))`, pega contra los endpoints
  reales, y compara.
- `backend/tests/test_contract_types.py` — regresiones unitarias del
  motor mismo con modelos/shapes sintéticos, independientes de
  `app/page.tsx`.

Se descubre solo vía `pytest`/`testpaths`; **cero líneas nuevas en
`test.ps1`**.

### `optional` y `nullable` son ejes independientes

Un borrador inicial trató `field?: string` (TS) como suficiente para un
Python `str | None`. Es incorrecto: nada en este código usa
`exclude_none`, así que un valor `None` siempre llega como
`"field": null` real, nunca como clave ausente — `field?: string` sin
`| null` no admite ese valor bajo TypeScript estricto. Corregido en
revisión antes de implementar: `nullable=True` en Python exige
`nullable=True` en TS (su tipo debe incluir `| null`); `optional` no lo
sustituye. Fijado con regresiones explícitas en `test_contract_types.py`
(positivo/negativo), incluyendo confirmación manual de que el caso
negativo efectivamente falla si se comenta el chequeo.

### Un campo TS ausente en Python sólo es fallo si no es `optional`

Corrección posterior, encontrada corriendo el mecanismo contra la app
real (no diseñada en abstracto): `ProjectDetail.project` reusa el mismo
tipo TS `Project` que `/api/dashboard` — que sí agrega `tasks_total`/
`tasks_completed` — pero `project_detail()` nunca los agrega. Ambos
campos ya eran `optional` en TS precisamente porque no siempre están.
Mismo patrón en `TestReport.command_evidence` (`dict[str, Any]` en
Python, sin schema fijo): cada tipo de check puebla un subconjunto
distinto de sus campos opcionales. Un campo TS `field?: T` ya promete
que la clave puede faltar — que una respuesta real puntual no la tenga
es justo lo que el tipo declara, no drift. Un campo TS **no** opcional
(`field: T`) sin fuente en Python sigue siendo fallo duro: es la
promesa rota que el gate existe para atajar.

### `is_required()` no es nulabilidad

`ProjectBrief.assumptions: list[str] = Field(default_factory=list)` no es
requerido para construir el objeto, pero tampoco es nuleable — siempre
serializa como `[]` como mínimo, nunca `null` ni ausente. La nulabilidad
se deriva de la anotación (`X | None`), nunca de `FieldInfo.is_required()`.
Usar `is_required()` habría marcado ~15 campos de `domain/models.py` como
falso positivo desde el primer día.

### Bug real encontrado al probar el extractor a mano

`null` como *tipo* en TypeScript es un `LiteralTypeNode` que envuelve un
token `NullKeyword` — no un `NullKeyword` suelto (a diferencia de
`undefined`, que sí es su propio nodo de tipo). Confundir ambos hacía que
**todo** campo `X | null` se clasificara como `nullable: false` — se
detectó corriendo el script a mano contra `Project.brief`/
`RuntimeStatus.active_model` antes de integrarlo (paso explícito del
plan), no en producción.

### `shape_from_value` es observación, no schema

Documentado como docstring de la propia función: deriva la forma de una
corrida concreta del fixture (proyecto real vía `mock`), no un schema
exhaustivo. Una lista vacía no tiene elemento del que derivar forma
interna. Para `decisions`/`approvals` (que un proyecto fresco puede
dejar vacíos) se sobrescribe con `shape_from_model` directo — sigue
siendo derivado de la fuente real (`model_fields` es lo que
`model_dump()` usa), no una descripción paralela.

## Consecuencias

- `response_model=` sigue sin agregarse a ninguna ruta — fuera de
  alcance a propósito, ver Contexto.
- No valida el conjunto exacto de valores de un `Enum`/`Literal` (match
  grueso contra `string`), ni la forma interna de blobs `dict[str, Any]`
  que tampoco tienen tipo propio en Python hoy (`Artifact.content`,
  `metrics`, `command_evidence`). Ambos son no-objetivos explícitos, no
  huecos descubiertos después.
- P3.1b (tests de interacción UI↔API mock) queda fuera, PR aparte.
