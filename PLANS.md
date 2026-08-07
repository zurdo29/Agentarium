# Agentarium — estado y hoja de ruta

Este documento es la guía operativa del proyecto: qué garantías ya existen, qué sigue abierto y en qué orden conviene trabajar. No es una bitácora detallada. La evidencia histórica y las decisiones de diseño viven en `docs/decisions/` y en `benchmarks/results/`.

## Estado al 7 de agosto de 2026

- `P0` — **CERRADO**. Rutas efectivas, ownership, colisiones, partición de criterios y profundidad de división quedaron mecanizadas.
- `P1` — **CERRADO como fase de medición y remediación**. La matriz baseline se completó y sus causas principales fueron instrumentadas/corregidas. Esto **no** significa que el objetivo de calidad de cero falsos `completed` se haya demostrado todavía.
- `P2` — **CERRADO, incluida la confirmación dirigida.** El runtime declara capacidades y el preflight de imports corta dependencias estáticas no soportadas antes de tester/revisor; P3.0 lo confirmó con una corrida real (ver siguiente punto).
- `main` después de P2.2: referencia de cierre `6174639` o posterior.
- Verificación de P2.2: **372 passed + 1 skip preexistente**, Ruff/MyPy limpios y web en verde.
- `P3.0` — **CERRADO (5 de agosto de 2026).** Corrida única `library_api_sqlite × ollama:qwen2.5-coder:7b × 1` (suite `p3.0-confirmation-2026-08`). El modelo propuso Flask, `IMPORT_PREFLIGHT` lo rechazó antes de tester/revisor, y el intento siguiente del mismo work item cambió a `http.server` (stdlib) — ocurrió el camino 2 previsto, con evidencia de autocorrección entre intentos. El proyecto terminó igual en `failed`/`path_conflict`, causa ajena a P2 (colisión de ownership de plan sobre `api.py`, mecanismo de P0), clasificada y mandada a backlog. Detalle en `benchmarks/results/p3.0-confirmation-2026-08/`.
- `P3.1a` — **CERRADO (6 de agosto de 2026).** Gate de drift backend/Pydantic ↔ TypeScript: `scripts/check-api-contract.mjs` (AST puro) + `backend/tests/contract_types.py`/`contract_registry.py` (selección, cero formas hardcodeadas -- se derivan de `model_fields` o de una llamada real a la API viá `TestClient`). Corrida contra el código real encontró y corrigió dos cosas antes de mergear: un bug real del extractor TS (`null` como tipo se representaba mal) y una regla demasiado estricta (un campo TS ausente en Python sólo es fallo si no es `optional`). Sin `response_model=`, sin tests de interacción UI, sin cambios funcionales -- eso es P3.1b. ADR 0030.
- `P3.1b` — **CERRADO (6 de agosto de 2026).** 16 tests de interacción real (`@testing-library/react` + `user-event` + `jsdom`) contra `Home()`, API mockeada con match exacto y falla ruidosa ante ruta no registrada (`tests/support/fetch-mock.mjs`). Cubre crear/abrir/controlar un proyecto, retry/rework/escalate, aprobaciones, conectividad, y los campos de sólo lectura que P4.3/P4.4 van a necesitar (last_error, veredicto, evidencia de test report, decisiones, artifacts, métricas). Stack y decisiones durables (por qué `node:test`+`jsdom`+RTL y no otro runner, transpile a archivo temporal en vez de un loader, match exacto del mock) en ADR 0031; los tres problemas puntuales del arnés encontrados corriendo el mecanismo quedaron documentados como comentario junto al código que los resuelve en `tests/support/dom-setup.mjs`, no en el ADR. Sin refactor de `page.tsx`, sin cambios visuales/funcionales, sin response_model=.
- `P3.2` — **CERRADO (6 de agosto de 2026).** Migraciones versionadas (`PRAGMA user_version` + lista de pasos, sin Alembic) y backup validado antes de migrar, derivados del path real de cada base SQLite -- nunca de una carpeta global, porque esta app ya corre varias bases reales en paralelo (default, por suite de benchmark, por test). `agentarium db restore <backup>` reemplaza "copiar el archivo encima": adquiere el mismo lock que el arranque, valida el backup antes de tocar nada, preserva la base reemplazada como `.failed-<timestamp>`, limpia `-wal`/`-shm` viejos y revalida al final. Corrección encontrada antes de implementar nada (no en producción): `user_version == 0` es legacy/ambiguo, nunca un prefijo confiable de pasos ya aplicados -- confirmado necesario por un test ya existente en `main` que agrega una columna fuera de orden a propósito; el mecanismo revisa cada paso contra el schema real en vez de confiar en el número. `test_schema_migration.py` sigue verde sin cambiar ninguna aserción (se le sumó una prueba con las 11 tablas en su forma original de `fd83772`); `test_migration_backup.py` nuevo cubre disparo condicional del backup, backup/restore inválido, aislamiento entre bases, regresión concurrente y sabotaje controlado a mitad de migración. ADR 0032; runbook en `docs/guides/database-migrations-windows.md`.
- `P3.3` — **CERRADO (7 de agosto de 2026).** Backend (`orchestration/engine.py`) **sin extracción** — sólo 4 métodos del `Orchestrator` se llaman desde fuera del archivo, y P4.1/P4.2/P4.4 no lo tocan; P4.3 ("recuperar artefacto"/"enviar candidato") ya llega vía `ApplicationService`, una frontera limpia y suficiente sin reorganizar el orchestrator interno -- resultado documentado, no un refactor pospuesto. Frontend: se extrajo `TaskDrawer` (`app/task-drawer.tsx`) con atadura escrita en el propio comentario de cabecera de `tests/home-work-items.test.mjs` (P3.1b), en diseño acíclico (`app/shared.tsx` como hoja pura para `ROLE_LABELS`/`dateLabel`/`Status`, evitando un ciclo runtime `page.tsx` ↔ `task-drawer.tsx`). El mecanismo de test (`tests/support/dom-setup.mjs`) pasó de transpilar un único archivo hardcodeado a un servidor Vite real (`createServer` + `ssrLoadModule`, ya devDependencies) que resuelve cualquier import relativo nuevo sin tocar el harness de nuevo. `contract_registry.py`/`test_type_contract.py` sin tocar (salida de `check-api-contract.mjs` idéntica antes/después); los 16 tests de interacción UI + SSR sin cambiar ninguna aserción salvo la que debía apuntar al archivo nuevo. `.\test.ps1` completo en verde (396 backend + 18 web). ADR 0033.
- `P3.4` — **CERRADO (7 de agosto de 2026).** `Project.imported: bool` (default `False`, persistido vía una nueva `MigrationStep` sobre `projects`) declara si un proyecto viene de un repositorio importado. La frontera es por perfil de validación, no por proyecto entero: `ValidationProfileExecutor` declara explícitamente qué perfiles no ejecutan el contenido entregado (`NON_EXECUTING_PROFILES`) y bloquea fail-closed cualquier otro cuando `allow_project_code_execution` es falso -- un `Artifact` se materializa y conserva siempre, pero el código nunca corre; una tarea que sólo necesita validación estática no se rechaza por estar en un proyecto importado. `Orchestrator._evaluate_candidate` pasa esa autoridad a `validate()` desde el único punto donde convergen los 3 caminos reales de ejecución (worker autónomo, recuperar artefacto, candidato de operador) y, ante un bloqueo, termina la tarea en `FAILED` directo -- sin tester/revisor, sin retry ni split. `P4.1` (importar un repo) todavía no existe, así que nada hoy puede producir `imported=True` fuera de un test que lo construye directamente. `.\test.ps1` completo en verde. ADR 0034.
- Próximo paso: **P4 — convertir Agentarium en una herramienta útil sobre repositorios reales**, empezando por P4.1 (P3 completo).

> Regla de interpretación: una fase puede estar cerrada aunque su medición haya mostrado problemas. “Cerrar P1” significa que el baseline y las remediaciones previstas terminaron; no que el sistema haya alcanzado mágicamente cero errores.

---

## Norte del producto

Agentarium debe convertirse en una herramienta local-first capaz de trabajar de forma confiable sobre proyectos pequeños y, después, repositorios existentes.

Las prioridades son, en este orden:

1. **No declarar éxito falso.** `COMPLETED` tiene que estar respaldado por evidencia mecánica suficiente.
2. **No perder trabajo.** Ninguna colisión de archivos puede terminar en sobreescritura silenciosa.
3. **Fallar pronto y de forma explicable.** Dependencias, capacidades, validaciones y políticas deben producir causas estructuradas.
4. **Recuperarse sin improvisar.** Reintentos, división y reparación manual tienen que conservar contexto y presupuesto correctamente.
5. **No exceder autoridad.** Una política declarada no debe presentarse como aislamiento real; cualquier ejecución de código generado debe tener una frontera honesta y explícita.
6. **Ser útil en proyectos reales.** Después de estabilizar los contratos, la prioridad pasa de seguir perfeccionando el orquestador a importar, reparar y exportar trabajo sobre repositorios existentes.

---

## Qué ya está resuelto mecánicamente

### P0 — contratos de trabajo y división

- Claims efectivos derivados de `owned_paths` y de outputs que representan rutas.
- Preflight de colisiones y resolución sin depender de que el modelo adopte vocabulario nuevo.
- Agrupamiento transitivo de rutas solapadas: sólo las tareas que chocan se serializan; las independientes siguen en paralelo.
- Protección contra sobreescritura silenciosa entre fragmentos.
- Clasificación por tipo de fallos de workspace/seguridad/infraestructura.
- División por `acceptance_criteria_ids`, con asignación determinista de respaldo.
- `split_depth` persistido; una consolidación no puede abrir recursión infinita de subtareas.
- `expected_outputs` se convierten en criterios exigibles.

Detalle histórico: ADR 0021, 0024, 0025 y 0026.

### P1 — medición reproducible y cierre de puntos ciegos

- Casos de benchmark versionados y ledger reanudable.
- Taxonomía estable de fallos.
- Identidad de suite/runtime/modelo congelada para evitar comparaciones inválidas.
- Validación funcional con fixture oculto para el caso CSV.
- Separación de `queue_wait_ms` y `generation_ms`.
- Contrato declarado de `SCRIPT_EXECUTION` (`entrypoint`, `args`, `produces`).
- Trazabilidad explícita `goal → validator` en los tres casos.

Baseline histórico `p1-baseline-2026-08`:

| Modelo | Completed reales | Falsos completed automáticos | Nota |
|---|---:|---:|---|
| qwen2.5-coder:7b | 1/9 | 0 | Baseline más estable de los tres |
| qwen3:4b | 2/9 | 3 | Los 3 falsos fueron confirmados manualmente |
| qwen3:8b | 0/9 | 1 | El falso señalado por el informe fue un falso negativo del validador; además hubo fuerte confusión por timeout/cola |

La adjudicación manual dejó **3 falsos `completed` confirmados de 27**. `duplicate_candidate` fue la causa terminal más frecuente (9/27) y `technical_validation` la siguiente (7/27).

El baseline es histórico: **no se reescribe** después de arreglar el sistema. Las mejoras se medirán en una suite futura distinta.

### P2 — capacidades del runtime y fallo temprano

P2.1:

- `RuntimeCapabilityManifest` distingue ejecutables permitidos de ejecutables realmente disponibles.
- `third_party_packages_allowed` explicita paquetes externos permitidos.
- `network_policy: deny` expresa política, no una garantía de aislamiento.
- El mismo manifiesto fuente se serializa para `plan`, `plan_revision` y `work`.
- Los prompts correspondientes conocen el contrato y sus versiones fueron incrementadas.

P2.2:

- `IMPORT_PREFLIGHT` recorre imports estáticos mediante AST antes de tester/revisor.
- Reconoce stdlib, paquetes declarados e imports locales resolubles dentro del proyecto.
- Un import estático no permitido produce una señal estructurada de capacidad no soportada.
- El reintento recibe `cumulative_rejected_imports`; no queda ciego por no existir todavía `TestReport`.
- Errores de sintaxis del preflight no derriban el orquestador.

Detalle histórico: ADR 0028 y 0029.

---

## Límites conocidos que siguen siendo reales

Estos puntos no deben maquillarse como resueltos:

1. **P1.3c está preparado pero no adoptado por el flujo autónomo.** `execution_contract` existe y está probado punta a punta, pero hoy las propuestas del planner/subtask no construyen ese contrato de forma autónoma.
2. **P2.2 cubre imports estáticos, no ejecución arbitraria.** Imports dinámicos, acceso a rutas absolutas, red o creación de subprocesos no quedan convertidos mágicamente en seguros por analizar AST.
3. **`network_policy: deny` es política declarada, no sandbox de SO.** No afirmar lo contrario en UI, ADRs ni documentación.
4. **qwen3:8b sigue sin calibración real después de la instrumentación.** Ya podemos separar cola de generación, pero todavía no hay evidencia suficiente para cambiar su timeout.
5. **`engine.py` y `app/page.tsx` son grandes.** Es deuda de mantenibilidad, no una emergencia que justifique una reescritura antes de tener tests de contrato suficientes.
6. **El producto sigue orientado principalmente a greenfield.** El salto de valor real será trabajar con un repositorio existente sin arriesgar el original.
7. **El gate de P3.4 niega ejecución, no es un sandbox de SO.** Un proyecto importado no puede ejecutar código a través de Agentarium, pero los perfiles de validación que sí corren (inventario, sintaxis) siguen sin aislamiento de sistema operativo, y lo mismo vale para `SCRIPT_EXECUTION` en un proyecto no importado -- esta fase niega la ejecución para proyectos importados, no resuelve el aislamiento real que P3.4 dejó fuera de alcance a propósito.

---

## Reglas para no volver a iterar de más

1. **Una hipótesis principal por PR.** Si aparece un hallazgo lateral, se documenta y se agenda salvo que invalide directamente el cambio actual.
2. **Primero regresión determinista.** Un bug reproducible debe tener una prueba que falle antes del arreglo siempre que sea razonable.
3. **Una confirmación real por comportamiento importante es suficiente.** No repetir corridas buscando el resultado deseado.
4. **No hacer fishing con modelos.** Una corrida inesperada genera evidencia, no una excusa para relanzar hasta que salga verde.
5. **No cambiar prompt y mecanismo en el mismo experimento salvo necesidad demostrada.** Así se puede atribuir el efecto.
6. **No mezclar refactor estructural con comportamiento nuevo.** Primero congelar contratos; luego mover código.
7. **ADRs sólo para decisiones arquitectónicas duraderas.** Los arreglos locales y la evidencia de una corrida pertenecen al plan, tests o findings.
8. **Los validadores sólo pueden exigir lo que el caso comunica al modelo.** `expected_artifacts` u otro metadato oculto no justifican por sí solos una condición de éxito.
9. **Una matriz 3×3×3 es un gate de hito, no una prueba cotidiana.** La siguiente matriz completa se hace cuando exista candidato a MVP, no al terminar cada P.
10. **P5 no entra por curiosidad técnica.** Plugins, departamentos, LangGraph, Postgres, multiusuario y ejecución distribuida necesitan una demanda real del producto.

---

## Gates de calidad permanentes

Todo PR de comportamiento debe cumplir lo que corresponda:

| Gate | Exigencia |
|---|---|
| Formato/estática | Ruff + MyPy limpios |
| Backend | Suite completa verde; skips explicados |
| Web | ESLint + tests + build verdes |
| Persistencia | Round-trip real cuando cambie schema/serialización |
| Contratos | Prueba negativa y positiva para cada frontera nueva |
| Seguridad/autoridad | No ampliar autoridad silenciosamente |
| Benchmark | No contaminar suite histórica ni comparar identidades incompatibles |
| Git | PR pequeño, branch desde `main`, squash merge y branch borrada |

Antes de empezar un incremento:

```powershell
git switch main
git pull --ff-only
git fetch --prune
git status
```

El árbol debe estar limpio antes de crear la rama nueva.

---

# Roadmap recomendado

## P0 — contratos, ownership y división — CERRADO

No reabrir salvo regresión concreta. Los detalles históricos viven en los ADR correspondientes.

## P1 — benchmark y remediación de puntos ciegos — CERRADO

P1 está cerrado **como paquete de ingeniería**, aunque el baseline no cumplió el objetivo de cero falsos `completed`.

No repetir ahora las 27 corridas. Conservar:

- `report.md`
- `report.json`
- `environment.md`
- `findings.md`

como evidencia inmutable de `p1-baseline-2026-08`.

## P2 — capacidades y preflight — CERRADO EN CÓDIGO

P2.1 y P2.2 están entregados. Sólo queda el checkpoint P3.0 para observar el mecanismo con un modelo real.

### Fuera de P2 a propósito

- No auto-instalar paquetes.
- No convertir análisis AST en un supuesto sandbox.
- No bloquear comandos por heurísticas de texto como sustituto de aislamiento real.
- No calibrar timeouts sin datos de `queue_wait_ms`/`generation_ms`.

---

## P3 — cerrar guardrails antes de convertirlo en herramienta diaria

### P3.0 — confirmación real dirigida de P2

**Objetivo:** comprobar una sola vez que el mecanismo recién entregado aparece en una ejecución real. No es un PR de features.

Caso recomendado:

- caso: `library_api_sqlite`;
- modelo: `ollama:qwen2.5-coder:7b`;
- una sola repetición;
- `main` limpio y sin modificar durante la corrida;
- suite/nombre de corrida nuevo: nunca escribir sobre el baseline P1.

Por qué ese caso: históricamente el modelo tendió a elegir Flask, así que ofrece una oportunidad natural de observar P2 sin fabricar un fallo artificial.

Resultados válidos:

1. El modelo respeta `runtime_capabilities` y usa una solución compatible con las capacidades declaradas; o
2. propone un import externo no permitido y `IMPORT_PREFLIGHT` lo rechaza antes de tester/revisor, dejando la señal estructurada y feedback para el siguiente intento.

Registrar también `queue_wait_ms` y `generation_ms` si aparecen. **No cambiar timeouts como consecuencia automática de esta corrida.**

Criterio de cierre:

- la corrida queda identificada y documentada;
- se comprueba cuál de los dos caminos ocurrió;
- si aparece un fallo nuevo, se clasifica y se manda a backlog a menos que demuestre que P2 está mecánicamente roto;
- no se repite la corrida para buscar otro resultado.

P3.0 no necesita ADR ni PR si sólo produce evidencia.

**Resultado (5 de agosto de 2026):** ejecutado. Ocurrió el camino 2 —dos veces, con evidencia de autocorrección entre intentos (el mismo work item pasó de `flask` a `http.server` después del primer rechazo)—. El proyecto igual terminó en `failed`/`path_conflict`, causa ajena a P2 (colisión de ownership de plan sobre `api.py`, mecanismo de P0), clasificada y mandada a backlog: no demuestra que P2 esté mecánicamente roto. Detalle completo en `benchmarks/results/p3.0-confirmation-2026-08/` (`environment.md`, `findings.md`, `report.md`, `report.json`). No se repitió la corrida ni se tocó ningún `timeout_seconds`.

### P3.1 — tests de contrato API/UI antes del refactor

**Objetivo:** congelar el comportamiento que P4 va a necesitar antes de partir archivos grandes.

Entregables:

1. Tests de interacción de UI contra API mock para los flujos críticos: crear proyecto, ejecutar, pausar/reanudar, revisar/reintentar y errores principales.
2. Contrato verificable entre OpenAPI/Pydantic y los tipos TypeScript consumidos por la web. Preferir generación o validación automática a duplicación manual.
3. Test explícito para estados/campos que P4 vaya a consumir antes de extraer componentes.
4. CI/test local falla si backend y frontend divergen en el contrato.


**P3.1a — CERRADO (6 de agosto de 2026).** Entregables 2 y 4 (contrato verificable backend/Pydantic ↔ TypeScript, gate que falla si divergen). Investigar antes de implementar mostró que 'generar TS desde OpenAPI' no alcanzaba: ninguna ruta declara `response_model=`, así que el `openapi.json` autogenerado no describe formas de respuesta reales hoy. El mecanismo final deriva todo de fuentes reales (`model_fields` de las clases de dominio, o una llamada real a través de `TestClient` para los endpoints compuestos como `/api/dashboard`) -- el registro (`contract_registry.py`) sólo selecciona qué comparar, nunca declara una forma a mano. Detalle completo, incluidas dos correcciones encontradas en revisión y en la corrida real, en `docs/decisions/0030-*`.
**P3.1b — CERRADO (6 de agosto de 2026).** Entregables 1 y 3 (tests de interacción de UI contra API mock, estados/campos que P4 va a consumir). 16 tests nuevos bajo `tests/`, `node --test "tests/**/*.test.mjs"` los descubre solo (una línea en `package.json`). Decisiones durables del stack de testing en `docs/decisions/0031-*`; el detalle de los problemas puntuales del arnés (por qué cada workaround) vive como comentario junto al código en `tests/support/dom-setup.mjs`, no en el ADR -- no era una decisión arquitectónica, era log de implementación.
No hacer aquí una reescritura visual de `page.tsx` ni dividir `engine.py` por estética.

Criterio de cierre de P3.1 completo (a+b) — cumplido: los flujos críticos quedan protegidos de una regresión de contrato y de interacción, y se puede refactorizar sin depender de inspección manual.

### P3.2 — migraciones versionadas y backup de SQLite

**Objetivo:** dejar de depender de backfills ad hoc antes de importar proyectos reales valiosos.

Entregables:

1. Mecanismo versionado de migración adoptado explícitamente.
2. Backup automático de la DB antes de una migración que modifique esquema/datos.
3. Prueba de upgrade desde al menos un esquema histórico representativo hasta el actual.
4. Fallo recuperable: una migración fallida no puede dejar una DB medio actualizada sin una ruta clara de recuperación.
5. Runbook Windows para backup, upgrade y restore.

Criterio de cierre: una DB antigua puede actualizarse de forma reproducible y recuperar su estado previo si el upgrade falla.

**Resultado (6 de agosto de 2026): CERRADO.** `PRAGMA user_version` + una lista ordenada de pasos (`backend/agentarium/repositories/migrations.py`) reemplaza la tupla implícita anterior; backup validado (`VACUUM INTO` + `integrity_check` + tablas de referencia consultables, sin comparar contra el estado posterior de la fuente -- ver más abajo) y `FileLock`, ambos derivados del path real de cada base -- nunca de una carpeta global (`backend/agentarium/repositories/backup.py`). `agentarium db restore <backup>` es una operación real: exige el lock, valida el backup antes de tocar nada, confirma que ninguna otra conexión sigue usando la base activa, preserva la base reemplazada (`.failed-<timestamp>`) y limpia sidecars `-wal`/`-shm`, en vez de copiar el archivo encima de una base WAL viva. Revisión previa a implementar corrigió dos supuestos del primer diseño: `user_version == 0` no puede tratarse como versión histórica exacta (el mecanismo revisa cada paso contra el schema real, sin asumir que sólo falta un *sufijo* ordenado -- un test ya existente en `main` agrega una columna fuera de orden a propósito y así lo confirmó antes de escribir ningún test nuevo); y backup/lock deben aislarse por base, no compartir una carpeta global, porque esta app ya corre varias bases SQLite reales en paralelo hoy. Una segunda ronda de revisión directa, ya sobre el PR, encontró y corrigió cuatro gaps de rigor antes de mergear (detalle completo en ADR 0032): mutación posible (incluido `create_all()`) antes del rechazo por versión demasiado nueva; `satisfied()` mirando sólo presencia de columna en vez de la postcondición de datos en pasos con backfill; `restore()` tratando el lock de migración como prueba de ausencia de conexiones activas (no lo es -- comprobación nueva vía `PRAGMA wal_checkpoint(TRUNCATE)`); y una comparación de conteo de filas con TOCTOU contra el estado posterior de la fuente. `test_schema_migration.py` sigue verde sin cambiar ninguna aserción preexistente (se le sumaron tres pruebas: 11 tablas en su forma original de `fd83772`, backfill pendiente con columna ya presente, y pasos fuera de orden); `test_migration_backup.py` cubre disparo condicional del backup, backup/restore inválido, aislamiento entre bases, regresión concurrente, sabotaje controlado, rechazo sin mutación de una base demasiado nueva con tabla faltante, conexión activa bloqueando `restore()`, y write concurrente durante la validación del backup. `.\test.ps1` completo en verde (396 backend + web). Diseño completo en ADR 0032; runbook en `docs/guides/database-migrations-windows.md`.

### P3.3 — modularización selectiva

**Objetivo:** reducir el coste de cambio justo donde P4 lo necesite, sin convertir el refactor en otro proyecto.

Orden:

1. Con P3.1 verde, identificar qué responsabilidades de `engine.py` tocará P4.
2. Extraer sólo fronteras claras — por ejemplo scheduling/attempt policy, split/consolidación, validation/review o workspace integration — manteniendo la API observable.
3. Dividir `app/page.tsx` conforme aparezcan componentes reales de P4 (importación, reparación, exportación), no mediante una reescritura total anticipada.
4. Cada extracción debe ser behavior-preserving y tener tests existentes verdes antes y después.

Criterio de cierre: P4 puede evolucionar sin seguir acumulando responsabilidades en los dos archivos gigantes. No existe una meta arbitraria de número de líneas.

**Resultado (7 de agosto de 2026): CERRADO.** Backend investigado, **sin
extracción**: de los 40+ métodos de `Orchestrator`, sólo 4 se llaman desde
fuera de `engine.py` (todos desde `ApplicationService`), y cruzando cada
sub-fase de P4 contra el código real, ninguna necesita reorganizar el
orchestrator -- P4.3 ("recuperar artefacto"/"enviar candidato") ya accede
a esa lógica a través de `ApplicationService.recover_artifact`/
`submit_candidate`, frontera suficiente para lo que un centro de
reparación necesita construir encima. Frontend: única extracción,
`TaskDrawer` (`app/task-drawer.tsx`), con atadura escrita -- no inferida
-- en el comentario de cabecera de `tests/home-work-items.test.mjs`.
Diseño acíclico (`app/shared.tsx` como hoja pura para los 3 valores que
`TaskDrawer` comparte con `page.tsx`, tipos vía `import type` sin mover
nada de `contract_registry.py`). El mecanismo de carga de tests
(`tests/support/dom-setup.mjs`) se generalizó de transpilar un único
archivo a un servidor Vite real (`createServer`+`ssrLoadModule`, cero
dependencias nuevas), que resuelve cualquier import relativo nuevo que P4
agregue sin tocar el harness otra vez -- más lento que el transpile de un
solo archivo que reemplaza, aceptado a propósito porque el punto era
generalidad, no velocidad.
`contract_registry.py`/`test_type_contract.py` intactos (verificado
comparando la salida de `check-api-contract.mjs` antes/después, idéntica);
los 16 tests de interacción UI + los 2 de `rendered-html.test.mjs` sin
cambiar ninguna aserción salvo la que debía apuntar al archivo nuevo.
`.\test.ps1` completo en verde (396 backend + 18 web). Detalle completo en
ADR 0033.

### P3.4 — gate de autoridad/seguridad antes de repositorios reales

**Objetivo:** decidir honestamente qué código generado puede ejecutarse cuando Agentarium trabaje sobre un repo que al usuario le importa.

Problema actual: `SCRIPT_EXECUTION` controla el contrato de ejecución, pero no constituye un sandbox de sistema operativo. `network_policy: deny` tampoco lo hace.

Este paso debe escoger y probar una frontera real. Opciones aceptables incluyen, según coste/plataforma:

- aislamiento real del proceso;
- aprobación explícita del usuario antes de ejecutar código no confiable;
- o deshabilitar ejecución de scripts para repositorios importados hasta disponer del aislamiento adecuado.

No aceptar como solución de seguridad principal una lista creciente de patrones AST/strings para detectar `subprocess`, red o rutas absolutas: sirve como señal adicional, no como frontera de autoridad.

Criterio de cierre: importar un repositorio no da al código generado autoridad implícita para modificar/ejecutar fuera de la frontera aprobada, y la UI/documentación describe exactamente la garantía real.

Los cambios que amplíen autoridad requieren decisión explícita del usuario.

**Resultado (7 de agosto de 2026): CERRADO.** De las 3 opciones que este
punto acepta, se eligió deshabilitar ejecución de scripts para proyectos
importados hasta que exista aislamiento real -- no aprobación explícita
ni aislamiento de proceso como frontera principal para esta fase.
`Project.imported: bool` (persistido) es el hecho de dominio; la frontera
vive dentro de `ValidationProfileExecutor`, fail-closed por identidad de
perfil (`NON_EXECUTING_PROFILES` declara qué perfiles no ejecutan
contenido entregado, cualquier otro queda bloqueado cuando la ejecución
está deshabilitada) en vez de por heurística de texto/AST. El `Artifact`
de cada intento se conserva siempre; una tarea que sólo necesita
validación estática no se ve afectada. `Orchestrator._evaluate_candidate`
aplica la misma frontera a los 3 caminos reales de ejecución (worker
autónomo, recuperar artefacto, candidato de operador) y termina en
`FAILED` directo, sin tester/revisor ni reintento. Detalle completo en
ADR 0034.

---

## P4 — convertir Agentarium en una herramienta útil sobre repositorios reales

P4 es el siguiente gran salto de producto. Una vez terminados los guardrails mínimos de P3, priorizar valor de usuario sobre nuevas abstracciones internas.

### P4.1 — importar un proyecto existente sin poner en riesgo el original

- Seleccionar carpeta/repo existente.
- Inspección inicial de sólo lectura.
- Trabajar sobre copia/worktree/área controlada; no mutar el origen por defecto.
- Mostrar qué commit/estado de origen se importó.

### P4.2 — exportar cambios de forma auditable

- Patch y/o branch exportable.
- Nunca sobrescribir el repo origen silenciosamente.
- Resumen de archivos, tests y validaciones asociados a la entrega.

### P4.3 — centro de reparación

- Ver tareas fallidas y causa estructurada.
- Reintentar, recuperar artefacto o enviar candidato sin perder linaje ni presupuesto.
- Exponer la evidencia relevante sin obligar al usuario a leer eventos crudos.

### P4.4 — entrega y auditoría

- Informe final: qué se pidió, qué cambió, qué validaciones corrieron, qué quedó sin verificar.
- Historial de intentos/modelos cuando aporte a diagnóstico, no como panel decorativo.

### P4.5 — onboarding Windows

- Setup reproducible.
- Comprobación de Ollama/modelos/configuración.
- Mensajes accionables para fallos frecuentes de permisos/temp/worktree.

Criterio de cierre de P4: un usuario puede tomar un repo pequeño existente, pedir un cambio, revisar/reparar el resultado y exportarlo sin que Agentarium modifique el original de forma implícita.

---

## Gate pre-MVP — segunda matriz completa

Sólo cuando P4 tenga un flujo candidato a MVP se ejecuta otra matriz 3 casos × 3 modelos × 3 repeticiones.

Reglas:

1. Nombre de suite nuevo. El baseline P1 permanece inmutable.
2. Congelar commit limpio, digests de modelo, prompts, casos, plataforma y concurrencia como ya exige el benchmark.
3. Si se usan los mismos modelos, comparar contra P1 aclarando cualquier cambio de digest/runtime. Un modelo actualizado no es una comparación idéntica.
4. Adjudicar manualmente cualquier `false_completed` automático antes de sacar conclusiones.
5. Comparar al menos: completed reales, falsos completed confirmados, taxonomía terminal, duración, queue/generation y provider failures.
6. Si quedan falsos `completed`, corregir causas mecánicas demostradas; no abrir otra campaña genérica de prompt tuning.

Objetivo de calidad para candidato MVP: **cero falsos `completed` confirmados en la suite**, además de una tasa de éxito útil y fallos explicables. Si no se cumple, P4 puede estar funcionalmente terminado, pero el candidato MVP no pasa el gate.

---

## P5 — extensibilidad, sólo después del MVP

Fuera del camino crítico actual:

- departamentos administrables;
- plugins;
- políticas de autoridad configurables más amplias;
- LangGraph u otro motor de orquestación;
- Postgres;
- multiusuario;
- ejecución distribuida/remota.

Sólo promover uno de estos puntos cuando una limitación observada del MVP lo justifique.

---

## Backlog consciente — no empezar ahora

- Calibración específica de timeout de qwen3:8b sin nueva evidencia real de queue/generation.
- Preflight de comandos convertido en falsa barrera de seguridad mediante regex/AST.
- Auto-instalación dinámica de paquetes.
- Otra matriz 27/27 inmediatamente después de P2/P3.
- Más prompt tuning sin causa medible.
- Refactor completo de `engine.py` o `page.tsx` antes de P3.1.
- Departamentos/plugins/LangGraph/Postgres/multiusuario.
- Reescritura del historial de `main` por los commits duplicados antiguos: es ruido cosmético y no justifica reescribir historia compartida.
- Resolución de conflictos de ownership de plan (`plan_owned_path_conflict_unresolved`) más allá de la compuerta de ejecución actual — encontrado en P3.0, no bloquea nada hoy porque la compuerta final ya evita la sobreescritura silenciosa.
- `agentarium benchmark run` con stdout redirigido en Windows sin forzar UTF-8 (`PYTHONUTF8=1`) — encontrado en P3.0 (`_echo` en `cli.py` usa `·`/`→`); no corrompe el ledger pero corta una matriz de más de una corrida a mitad de camino.
- Cerrar la brecha TypeScript↔fixture-de-test en `tests/support/fixtures.mjs` (duplicado a mano de los tipos de `page.tsx`, puede desalinearse en silencio) — encontrado en P3.1b, aceptado a propósito por ahora (ver ADR 0031).

---

## Próximas tres entregas

### 1. P3.0 — CERRADO (5 de agosto de 2026)

Corrida dirigida `library_api_sqlite × qwen2.5-coder:7b × 1` ejecutada y documentada en `benchmarks/results/p3.0-confirmation-2026-08/`. Sin feature PR, como estaba previsto.

### 2. P3.1 — CERRADO (a+b, 6 de agosto de 2026)

Contrato backend↔TypeScript (ADR 0030) + tests de interacción UI/API mock (ADR 0031) entregados. Cinturón de seguridad para el refactor y P4 completo.

### 3. P3.2 — CERRADO (6 de agosto de 2026)

Migraciones versionadas (`PRAGMA user_version` + lista de pasos) + backup validado/restore real de SQLite, aislados por base. ADR 0032; runbook en `docs/guides/database-migrations-windows.md`.

Después: P3.3 modularización selectiva → P3.4 gate de autoridad → P4 repositorios reales → matriz completa pre-MVP.

---

## Instrucción para la próxima sesión/agente

Usar este texto literalmente como punto de partida:

> Lee `CLAUDE.md`/las instrucciones del repo y `PLANS.md` completos. Verifica que estás sobre `main` actualizado y limpio. No reabras P0, P1, P2, P3.1 (a+b), P3.2, P3.3 ni P3.4 salvo una regresión demostrable o un requisito concreto de P4 que lo justifique (ver el disparador de revisión en ADR 0033 para el caso de backend, y la condición futura en ADR 0034 para habilitar ejecución aislada). Empieza por **P4.1** — importar un proyecto existente sin poner en riesgo el original -- ver la sección P4.1 del roadmap para el alcance ya definido.

---

## Referencias que conservan el detalle histórico

- `docs/decisions/0021-*` — división al agotar intentos.
- `docs/decisions/0024-*` — claims de rutas y colisiones.
- `docs/decisions/0025-*` — routing de agotamiento.
- `docs/decisions/0026-*` — identidad/asignación de criterios.
- `docs/decisions/0027-*` — contrato declarado de `SCRIPT_EXECUTION` y su límite de adopción.
- `docs/decisions/0028-*` — manifiesto de capacidades del runtime.
- `docs/decisions/0029-*` — preflight de imports.
- `docs/decisions/0030-*` — gate de drift backend/Pydantic ↔ TypeScript (P3.1a).
- `docs/decisions/0031-*` — arnés de tests de interacción UI↔API mock (P3.1b).
- `docs/decisions/0032-*` — migraciones versionadas + backup/restore de SQLite (P3.2).
- `docs/guides/database-migrations-windows.md` — runbook Windows de backup, upgrade y restore (P3.2).
- `docs/decisions/0033-*` — modularización selectiva: sin extracción de backend (resultado documentado), extracción de `TaskDrawer` y mecanismo de test Vite-based (P3.3).
- `docs/decisions/0034-*` — frontera de autoridad para proyectos importados: `Project.imported`, bloqueo fail-closed por perfil de validación dentro de `ValidationProfileExecutor` (P3.4).
- `benchmarks/results/p1-baseline-2026-08/` — baseline, ambiente y adjudicación manual.
- `benchmarks/results/p3.0-confirmation-2026-08/` — confirmación dirigida de P2 con modelo real, ambiente y hallazgos.

Cuando una afirmación histórica de este plan choque con un ADR o con datos versionados del benchmark, la evidencia versionada manda; actualizar este resumen en vez de reinterpretar el pasado.