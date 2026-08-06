# Hallazgos — suite `p3.0-confirmation-2026-08`

Adjudicación manual de la única corrida de P3.0
(`library_api_sqlite` × `ollama:qwen2.5-coder:7b` × 1). Inspección read-only
de `runtime/agentarium.db` (`execution_events`, `agent_runs`, `artifacts`)
para el proyecto `c8a5e46f-9d51-4e23-b7fb-f0079be0b7d3`. Nada de esto vino
de re-correr la medición.

## Resultado automático

`project_status: failed`, categoría `path_conflict`, 25 intentos, 2
divisiones, 479.82s. `validation_passed: true` — los 4 validadores del caso
(código Python, SQLite real, validación de préstamo, documentación Markdown)
pasaron contra los archivos finalmente materializados — pero como
`project_status` nunca llegó a `completed`, `false_completed` es `false`.
El sistema no declaró éxito falso incluso en una corrida larga y accidentada.

## La pregunta de P3.0: ¿cuál de los dos caminos ocurrió?

**Ocurrió el camino 2 — dos veces, mecánicamente confirmado — y además se
observó una corrección real entre intentos.** Este proyecto contuvo en
paralelo dos linajes de tareas independientes resolviendo la misma API, y
tuvieron desenlaces distintos frente a `runtime_capabilities`:

### Linaje A (`validate_borrowing_logic`, work item `8652241d` y su hijo `5af38cab`) — camino 1

`api.py` entregado usa `from sqlite3 import connect`: sólo biblioteca
estándar desde el primer intento. Se integró sin fricción
(`change_set_integrated` #4051, #4119 para `api/models.py`). Es el archivo
que terminó satisfaciendo los validadores del caso.

### Linaje B (`implement_api_endpoints`/`903b222d`, "Completar y verificar la entrega") — camino 2, con autocorrección

- **Intento con `app.py`:** `import sqlite3` + `from flask import Flask,
  request, jsonify`. `IMPORT_PREFLIGHT` lo rechazó **antes de gastar
  tester/revisor** (evento `unsupported_capability_detected` #3971):

  > "Se rechazaron 1 import(s) no soportados antes de gastar
  > tester/revisor." — `metadata.rejected_imports: ["flask"]`,
  > `profile: import_preflight`.

- **Intento siguiente, mismo work item:** `app.py` reescrito con
  `import sqlite3` + `from http.server import BaseHTTPRequestHandler,
  HTTPServer` — stdlib pura. El modelo cambió de librería entre intentos,
  consistente con que el mecanismo de P2.2 (evidencia estructurada vía
  `cumulative_rejected_imports`) tuvo efecto observable, no sólo teórico.
  Esto es justo lo que ADR 0029 dejó como pregunta abierta ("medirlo con
  una corrida real queda para una sesión futura") y aquí se responde: sí,
  al menos en esta corrida, el worker no repitió el import rechazado.

- **Subtarea hija tras el split (`25ea2018`, "Pruebas unitarias"):**
  entregó sólo `tests/test_books.py` con `from app import db, Book` —
  shape de Flask-SQLAlchemy que ya no correspondía al `app.py` vigente
  (movido a `http.server`). `IMPORT_PREFLIGHT` lo rechazó de nuevo (evento
  #4110, `rejected_imports: ["app"]`) porque `app.py` nunca fue parte del
  propio work item ni de ningún `change_set_integrated` de este proyecto
  (sólo se integraron `api_documentation.md`, `requirements.txt`, `api.py`,
  `schema.sql`, `api/models.py` — nunca `app.py`). No es un falso positivo
  de la resolución local de ADR 0029: `app` genuinamente no era resoluble
  como sibling en el árbol que esa subtarea tenía delante. Es, más bien,
  una subtarea heredando un supuesto (Flask + SQLAlchemy) que el propio
  padre ya había abandonado — desalineación entre split y contexto, no un
  bug del preflight.

Notablemente, el propio artefacto de `25ea2018` autodeclara en
`limitations`: *"No se permite el uso de paquetes de terceros"* — el
modelo puede articular la restricción en prosa y aun así entregar código
que la viola. Confirma otra vez la premisa de ADR 0020/0028: la
prosa/dato declarado no garantiza cumplimiento; lo que efectivamente cortó
antes de tester/revisor fue el mecanismo de P2.2, no el conocimiento del
modelo sobre la política.

## Por qué terminó `failed`: causa ajena a P2

La causa terminal (`path_conflict`) es un mecanismo de P0, no de P2.
`implement_api_endpoints` y `validate_borrowing_logic` declararon ambos
`api.py` como output esperado. El chequeo de plan lo marcó
(`plan_owned_path_conflict_detected` ×2) y pidió revisión; quedó **sin
resolver** tras esa revisión (`plan_owned_path_conflict_unresolved` #3945:
*"la compuerta de colisión en ejecución sigue siendo la protección
final"*). Esa compuerta de ejecución rechazó la escritura 13 veces
(`workspace_action_rejected`, siempre el mismo mensaje: colisión sobre
`api.py`) hasta forzar un split (#4071) que tampoco alcanzó a consolidar
dentro del presupuesto de intentos. Ningún archivo se sobreescribió en
silencio — exactamente la garantía que P0 promete — pero el proyecto, en
conjunto, no llegó a `completed`.

**Esto no demuestra que P2 esté mecánicamente roto.** P2.2 se activó dos
veces y en ambas cortó antes de tester/revisor con la señal estructurada
esperada. La causa de fondo del `failed` es un conflicto de ownership de
plan que el propio sistema ya sabía dejar "sin resolver" antes de
ejecutar — punto de mejora real, pero de P0/planificación, no de P2. Se
manda a backlog, no se corrige en esta sesión (regla de PLANS.md: "si
aparece un fallo nuevo... a menos que demuestre que P2 está mecánicamente
roto").

## Instrumentación (P1.3a) — corroboración de paso

Los 52 `agent_runs` de este proyecto tienen `queue_wait_ms`/`generation_ms`
completos (0 faltantes). Ningún `agent_run` terminó en error de proveedor
(52/52 `outcome: artifact_delivered`) — el resultado refleja comportamiento
real del modelo/orquestador, no ruido de infraestructura.

| Rol | n | cola media (ms) | cola máx (ms) | generación media (ms) | generación máx (ms) |
|---|---:|---:|---:|---:|---:|
| director | 1 | 0 | 0 | 11 507 | 11 507 |
| technical_manager | 5 | 0 | 0 | 14 861 | 17 795 |
| implementation_worker | 25 | 25 114 | 52 486 | 11 920 | 31 847 |
| tester | 10 | 21 844 | 30 655 | 6 819 | 10 780 |
| critical_reviewer | 11 | 7 079 | 14 261 | 2 410 | 4 039 |

Nada de esto motiva un cambio de `timeout_seconds` (ninguna llamada se
acercó a su límite de rol) ni se tocó ninguno como consecuencia de esta
corrida.

## Hallazgo lateral: `agentarium benchmark run` puede morir en Windows con stdout redirigido

El progreso de `_echo` (`backend/agentarium/cli.py:229-234`) imprime `·` y
`→`. Con stdout redirigido a archivo en Windows (`> log.txt`, sin consola
adjunta), Python eligió `cp1252` en vez de UTF-8 y el proceso terminó con
`UnicodeEncodeError` y exit code 1 **después** de que `ledger.append(record)`
ya había escrito el registro — el dato de esta corrida no se perdió, pero
una matriz de varias corridas invocada así en Windows se cortaría después
de la primera, sin ejecutar el resto. Workaround usado para generar
`report.md`/`report.json`: `PYTHONUTF8=1`. No se tocó `cli.py` en esta
sesión — es hallazgo lateral, no alcance de P3.0.

## Cierre de P3.0

- Corrida identificada y documentada: sí (este directorio).
- Camino observado: **camino 2**, dos veces, con evidencia de
  autocorrección entre intentos.
- Fallo nuevo (`path_conflict`) clasificado y mandado a backlog; no
  demuestra que P2 esté roto.
- No se repitió la corrida.
