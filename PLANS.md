# Agentarium — estado y hoja de ruta

Este documento es la guía operativa del proyecto: qué garantías ya existen, qué sigue abierto y en qué orden conviene trabajar. No es una bitácora detallada. La evidencia histórica y las decisiones de diseño viven en `docs/decisions/` y en `benchmarks/results/`.

## Estado al 18 de agosto de 2026

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
- `P3.4` — **CERRADO (7 de agosto de 2026).** `Project.imported: bool` (default `False`, persistido vía una nueva `MigrationStep` sobre `projects`) declara si un proyecto viene de un repositorio importado. La frontera es por perfil de validación, no por proyecto entero: `ValidationProfileExecutor` declara explícitamente qué perfiles no ejecutan el contenido entregado (`NON_EXECUTING_PROFILES`) y bloquea fail-closed cualquier otro cuando `allow_project_code_execution` es falso -- un `Artifact` se materializa y conserva siempre, pero el código nunca corre; una tarea que sólo necesita validación estática no se rechaza por estar en un proyecto importado. `Orchestrator._evaluate_candidate` pasa esa autoridad a `validate()` desde el único punto donde convergen los 3 caminos reales de ejecución (worker autónomo, recuperar artefacto, candidato de operador) y, ante un bloqueo, termina la tarea en `FAILED` directo -- sin tester/revisor, sin retry ni split. `P4.1` (importar un repo) todavía no existe, así que nada hoy puede producir `imported=True` fuera de un test que lo construye directamente. ADR 0034.
- `P4.1` — **CERRADO (8 de agosto de 2026).** `ApplicationService.import_project` clona/copia un repositorio o carpeta externa a `workspace_root/<project_id>/project` -- el mismo lugar que ya usa un proyecto greenfield, así que `GitWorktreeIsolation`/P3.4 siguen sin cambios de código. El origen se resuelve y se compara contra `workspace_root` para rechazar solapamiento en ambas direcciones; un origen sucio, con submódulos, o con un symlink/reparse point en el caso de carpeta plana se rechaza directo. `git clone --no-local` (nunca hardlinks) captura HEAD+limpio antes de clonar y vuelve a leer el origen justo después para detectar y rechazar un cambio a mitad de importación -- no pretende volver atómica la operación. `Project` gana `imported_source_path`/`imported_commit` (persistidos, sin backfill). Verificación manual con un repositorio real de tres commits en este equipo encontró y corrigió dos bugs reales: ninguna llamada git revisaba su código de salida (un fallo real dejaba el destino a medio construir y el siguiente comando reventaba varios cuadros después con un error de bajo nivel sin relación aparente -- ahora `_run_git_checked` lo convierte en un `ImportSourceError` limpio), y `git clone` por sí solo podía superar el límite de ruta de Windows antes de que la config de rutas largas llegara a aplicarse (ahora se pasa también como `-c` directo en la propia invocación del clone). Confirmado a mano: el origen queda byte a byte idéntico antes/después, y una tarea real contra el proyecto importado termina bloqueada por la compuerta de P3.4. ADR 0035.
- `P4.2` — **CERRADO (8 de agosto de 2026).** `ProjectExporter` exporta el rango `base..main` (`base` = `imported_commit` si el proyecto fue importado, raíz del repo si es greenfield) como `changes.patch`/`changes.bundle`, con `summary.json`/`summary.md` cruzados contra los eventos `change_set_integrated` reales (`consistency.matches_git_history`). Publicación fail-safe vía carpeta de staging + `publish()`/`discard()` explícitos, y una validación de alineación git/DB bajo el mismo `project_lock()` que ya usan `prepare/collect/integrate` -- ambas correcciones de una revisión de diseño antes de implementar. `imported_source_path` gana un uso nuevo puramente defensivo: comparar (nunca leer/escribir) el destino de exportación contra él. Verificado con 19 tests contra repos git reales y a mano en este equipo: `git am` con árbol idéntico byte a byte, `git fetch` del bundle con SHA idéntico, origen intacto en todo momento, y los dos rechazos limpios (rango vacío, proyecto sin repo todavía) confirmados a mano, no sólo por test. ADR 0036. **No cierra P4** -- P4.3 y P4.4 siguen abiertos.
- `P4.3` — **CERRADO (12 de agosto de 2026), a+b.** `repair_center()` agrega work items de todos los proyectos en 4 causas (`failed`/`changes_requested`/`exhausted`/`blocked`, esta última sólo con una dependencia real en falla) con la evidencia suficiente por fila; `resolve_approval()` cierra el callejón escalar→aprobar con un vínculo estructural (`task_escalated` guarda `approval_id` en su propia metadata; sólo ese vínculo autoriza preparar un retry, nunca `approval.work_item_id` solo) y deja el proyecto en `READY` vía `resume_project()` cuando no quedan aprobaciones pendientes. `attempt_repair_available` por fila respeta `MAX_WORK_ITEM_ATTEMPTS` (=25) con una regla exacta por causa (siempre falso para `blocked`). Frontend: `app/repair-center.tsx` nuevo (filtros, fila expandible, sin acciones para `blocked`) con un único patrón de confirmación explícita por panel inline (sin `window.confirm`) para las 4 acciones, `resetRepairDrafts()` con regresión A→B dedicada, y la misma confirmación real aplicada a retry/rework/escalate en `TaskDrawer`, que P4.3a había dejado sin tocar. Verificado con 499 passed + 1 skipped (backend) + 38/38 (web) y, a mano, las 4 acciones ejecutadas con llamadas HTTP reales contra un proyecto real forzado a fallar -- incluido el vínculo estructural `task_escalated.metadata.approval_id` confirmado en producción, no sólo en test. ADR 0037. **No cierra P4** -- P4.4 y P4.5 siguen abiertos.
- `P4.4` — **CERRADO (14 de agosto de 2026), a+b.** `ApplicationService.delivery_report()` deriva, por work item, un `outcome` de 9 valores (`completed`/`changes_requested`/`failed`/`exhausted`/`blocked`/`cancelled`/`superseded_by_split`/`awaiting_approval`/`in_progress`) con evidencia estructurada de review/test/integración; `awaiting_approval` usa el mismo vínculo estructural que `resolve_approval()` (ADR 0037), corregido dos veces sobre el mismo PR. `Repository.list_agent_runs(project_id)` cerró el único hueco real de `AgentRun` (existía `add_agent_run`, ningún `list_`). Frontend: `app/delivery-report.tsx` nuevo y una sección "Historial de intentos" en `TaskDrawer`, ambos on-demand (un clic, nunca auto-fetched por `openProject()`) porque PLANS.md pide explícitamente que el historial no sea "un panel decorativo" y porque auto-fetchearlos ahí habría obligado a registrar las dos rutas nuevas en cada test existente que abre un proyecto. Dos correcciones explícitas de esta fase: una guarda de id de solicitud monótono contra respuestas tardías A→B en ambos fetches (un proyecto abandonado que resuelve tarde no puede repoblar el estado de uno distinto ya abierto), y evidencia de review/test visible también en ítems `completed`, no sólo en las causas problemáticas. De paso, dos paneles ya tipados pero nunca renderizados: `decision.rationale` y `metrics.tasks_completed`/`tasks_rejected`. Verificado con 521 passed + 1 skipped (backend -- sin cambio numérico sobre P4.4a: el registro de contrato de `AgentRun`/`DeliveryReport` se ejercita dentro del test de drift ya existente, no agrega funciones nuevas) y 46/46 (web, incluye el nuevo `home-delivery-report.test.mjs`, dos regresiones A→B de historial de intentos, y la ronda de corrección de diagnóstico de `AgentRun`/detalle del informe) y a mano contra un proyecto corrido con el proveedor mock. ADR 0038. **Cierra P4** -- sólo P4.5 sigue abierto.
- `P4.5` — **CERRADO (14 de agosto de 2026).** `agentarium doctor` deja de ser un volcado de JSON que nunca falla: reusa `ProviderRegistry` (la misma fuente que ya usa la web) en vez de su propia comprobación divergente de Ollama, revisa si el modelo configurado está realmente descargado, advierte proactivamente rutas de `workspace_root` demasiado largas para `git worktree` en Windows (ADR 0022), comprueba escritura real (no sólo permisos) sobre workspace y carpeta temporal, y sale con código 12 si algo queda en `FAIL` -- construye `Settings()` directo, nunca `get_settings()`, para poder diagnosticar un entorno donde `ensure_directories()` fallaría. `git worktree add` fallando con `fatal: '$GIT_DIR' too big` ahora agrega un mensaje que nombra `AGENTARIUM_WORKSPACE_ROOT` como fix, sin cambiar el tipo de `IsolationError` (verificado que `_candidate_failure_policy` despacha por `isinstance`, no por mensaje). `setup.ps1` corre `doctor` al final sin abortar la instalación -- la primera versión de esto tenía un bug real, encontrado en verificación manual (no por `test.ps1`, que no ejecuta ningún `.ps1`): no reseteaba `$LASTEXITCODE` después de leer el código de `doctor`, así que `setup.ps1` terminaba heredando el código 12 de `doctor` como propio; corregido con reset explícito + `exit 0` final, reverificado en vivo forzando un `doctor` en `FAIL` real. `docs/guides/windows-setup.md` nuevo documenta, por primera vez en el repo, el fix de TMP/TEMP para el permiso roto de pytest y el procedimiento seguro (no matar todos los `node.exe`) para el cuelgue de `npm test`; `docs/guides/ollama.md` corregido -- su paso 3 no tenía ningún efecto real. Verificado con 545 passed + 1 skipped (backend, +24 sobre P4.4b) + 46/46 (web, sin cambio) y a mano contra el `provider-selection.json` real de esta máquina (no un fixture): la selección guardada real ganó sobre el default de variables de entorno, `ollama` marcó `FAIL` por no responder de verdad, `openai_compatible` marcó `WARN` por no ser el proveedor activo. ADR 0039. **Cierra P4.**
- **Candidato MVP medido (15 de agosto de 2026): `measurement_valid=true` / `candidate_passed=false`.** Flujo candidato `textkit-slugify` (import → run → export → verificar en clon limpio) corrido una sola vez contra `ollama:qwen2.5-coder:7b`, sin repetir la corrida ni realizar reintentos manuales ni ajustar el goal (los reintentos automáticos normales del orquestador sí ocurrieron). 5/9 del checklist de aprobación; el proyecto terminó `failed`. Evidencia completa, incluida la adjudicación read-only de la causa real (releyendo `Orchestrator._colliding_dependency_paths` contra la tabla `artifacts`, no inferida del mensaje de evento), en `benchmarks/results/mvp-candidate-textkit-slugify-2026-08/`. Detalle en "Gate pre-MVP — medición realizada, candidato no aprobado" más abajo. No se corrigió nada del código como parte de esta medición.
- `Gate-MVP.1` — **CERRADO (17 de agosto de 2026, PR #28 / `afbc538`).** La frontera efectiva de escritura rechaza un candidato que excede los claims de su propio work item antes de tocar disco, en los tres caminos reales.
- `Gate-MVP.2` — **CERRADO (18 de agosto de 2026).** `TestReport.verification_mode` (`static_only`/`executed`, requerido, calculado desde `CommandResult.started` -- nunca de `passed`) distingue evidencia ejecutada de sólo estática en `delivery_report`/export/UI; perfil no-ejecutante nuevo `PYTHON_UNDEFINED_NAMES` (`ruff --isolated --select F821`) cierra la clase de bug exacta del incidente sin ejecutar nada; `_removed_top_level_definitions` persiste y envía al reviewer qué funciones/clases/métodos de nivel superior desaparecieron entre base y candidato. P3.4/ADR 0034 sin cambios. Un bug real de la propia política de seguridad (`--output-format=concise` chocaba con el deny-token `format`) se encontró y corrigió durante la verificación, no por inspección de código. `test_honest_verification.py` reconstruye el incidente `textkit-slugify` byte a byte contra un proyecto importado real. ADR 0041.
- Próximo paso: **Gate-MVP.3 — repetición única de `textkit-slugify`**, mismo modelo y condiciones, sin pesca. La matriz 3×3×3 deja de ser un requisito automático del MVP; P5 sigue bloqueado hasta tener un candidato aprobado y señal de usuarios reales.

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
6. **El flujo de repos importados existe, pero todavía no está aprobado como MVP.** Importar, reparar, auditar y exportar funciona; falta demostrar honestidad de verificación y utilidad repetible con el perfil inicial soportado.
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
9. **Una matriz 3×3×3 es evaluación comparativa, no un gate automático.** Sólo se ejecuta después de Gate-MVP.3/alpha si comparar modelos responde una decisión real; nunca para sustituir tres flujos de producto verificables.
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
- **Todo proyecto creado por esta vía debe persistirse con `Project.imported = True`** (P3.4/ADR 0034) -- la frontera de ejecución fail-closed en `ValidationProfileExecutor` depende de que este campo esté correctamente poblado; un proyecto importado que quede con `imported=False` por omisión heredaría autoridad de ejecución que esta fase existe para negar.

**Resultado (8 de agosto de 2026): CERRADO.** Selector de carpeta =
campo de texto (`app/import-project.tsx`), porque el navegador no puede
entregar una ruta absoluta real; flujo inspeccionar → vista previa →
confirmar con un *goal* → importar. `ApplicationService.import_project`
genera el `project_id`, corre la importación completa contra él, y sólo
si termina sin error persiste la fila -- un fallo a mitad de camino
nunca deja un `Project` con `imported=True` sin contenido real en disco.
CLI (`agentarium project import`) async-envuelta como `run`, no
síncrona como `create`. Detalle completo, incluidas las garantías reales
y las dos correcciones encontradas mediante verificación manual con un
repositorio real (no un fixture), en ADR 0035.

### P4.2 — exportar cambios de forma auditable

- Patch y/o branch exportable.
- Nunca sobrescribir el repo origen silenciosamente.
- Resumen de archivos, tests y validaciones asociados a la entrega.

**Resultado (8 de agosto de 2026): CERRADO.** `ProjectExporter`
(`isolation/export.py`) exporta el rango `base..main` del repo propio del
proyecto (`base` = `imported_commit` si fue importado, raíz del repo si es
greenfield) como `changes.patch` (`git format-patch --binary --stdout`,
bytes crudos sin decodificar) y/o `changes.bundle` (`git bundle create`),
junto a `summary.json`/`summary.md` cruzados contra los eventos
`change_set_integrated` reales. Publicación fail-safe (carpeta de staging,
`publish()`/`discard()` explícitos, `project_exported` sólo tras éxito) y
una validación de alineación git/DB bajo el mismo lock que ya usan
`prepare/collect/integrate` -- ambas correcciones de una revisión de
diseño antes de implementar, no encontradas después. `imported_source_path`
gana un único uso nuevo puramente defensivo: comparar (nunca leer/escribir)
el destino de exportación contra él. Verificado con 19 tests contra repos
git reales y a mano en este equipo: `git am` de un clon fresco con árbol
idéntico byte a byte (no SHA de commit -- `git am` linealiza, nunca
reproduce un merge `--no-ff`), `git fetch` del bundle en un clon separado
con SHA idéntico, origen intacto en todo momento, y los dos rechazos
limpios (rango vacío, proyecto sin repo todavía) confirmados a mano.
Detalle completo en ADR 0036. **No cierra P4**: P4.3 (centro de
reparación) y P4.4 (entrega y auditoría) siguen abiertos.

### P4.3 — centro de reparación

- Ver tareas fallidas y causa estructurada.
- Reintentar, recuperar artefacto o enviar candidato sin perder linaje ni presupuesto.
- Exponer la evidencia relevante sin obligar al usuario a leer eventos crudos.

**Resultado (12 de agosto de 2026): CERRADO, a+b.** P4.3a (backend/API/
CLI, PR #22) entregó `repair_center()`, el vínculo estructural de
`resolve_approval()` y `agentarium repair {list,retry,recover,rework,
escalate,candidate}`. P4.3b (frontend, este PR) agregó
`attempt_repair_available` por fila (autorizado explícitamente sobre
P4.3a ya mergeado) y toda la interfaz: `app/repair-center.tsx`,
confirmación explícita por panel inline para las 4 acciones (sin
`window.confirm`), `resetRepairDrafts()` con regresión A→B, y la misma
confirmación real para retry/rework/escalate en `TaskDrawer` -- deuda
que P4.3a había dejado pendiente al no tocar frontend. Verificado con
499 passed + 1 skipped (backend) + 38/38 (web) y a mano, con las 4
acciones ejecutadas contra un proyecto real vía HTTP real, incluido el
vínculo `task_escalated.metadata.approval_id` confirmado en producción.
Detalle completo en ADR 0037. **No cierra P4**: P4.4 (entrega y
auditoría) y P4.5 (onboarding Windows) siguen abiertos.

### P4.4 — entrega y auditoría

- Informe final: qué se pidió, qué cambió, qué validaciones corrieron, qué quedó sin verificar.
- Historial de intentos/modelos cuando aporte a diagnóstico, no como panel decorativo.

**Resultado (14 de agosto de 2026): CERRADO, a+b.** P4.4a (backend/API/CLI,
PR #24, squash `4b47c0b`) entregó `delivery_report()`,
`list_agent_runs(project_id)` y `agentarium project report <id>`. P4.4b
(frontend, este PR) agregó los tipos TS y el registro de contrato
diferidos explícitamente desde P4.4a, `app/delivery-report.tsx` y el
historial de intentos en `TaskDrawer` -- ambos on-demand (un clic) con una
guarda de id de solicitud monótono contra respuestas tardías A→B, y
evidencia de review/test visible también en ítems `completed`, no sólo en
las causas problemáticas (dos correcciones explícitas de esta fase). Detalle
completo en ADR 0038. **Cierra P4** -- sólo P4.5 sigue abierto.

### P4.5 — onboarding Windows

- Setup reproducible.
- Comprobación de Ollama/modelos/configuración.
- Mensajes accionables para fallos frecuentes de permisos/temp/worktree.

Criterio de cierre de P4: un usuario puede tomar un repo pequeño existente, pedir un cambio, revisar/reparar el resultado y exportarlo sin que Agentarium modifique el original de forma implícita.

**Resultado (14 de agosto de 2026): CERRADO.** `agentarium doctor`
rediseñado con comprobaciones `pass`/`warn`/`fail` reales (antes: JSON
plano, siempre exit 0) -- reusa `ProviderRegistry` para Ollama/modelos en
vez de una comprobación propia divergente, detecta proactivamente rutas
de workspace riesgosas para `git worktree` en Windows y problemas reales
de escritura en workspace/temp, y sale con código 12 si algo falla de
verdad. Mensaje accionable (nombra `AGENTARIUM_WORKSPACE_ROOT`) agregado
al error real de `fatal: '$GIT_DIR' too big` sin cambiar el tipo de
`IsolationError`. `setup.ps1` corre `doctor` al final sin abortar la
instalación -- un bug real de esto (el exit code de `doctor` se filtraba
como propio) sólo apareció al forzarlo a mano, `test.ps1` no lo hubiera
atrapado porque no ejecuta ningún `.ps1`. Documentación nueva
(`docs/guides/windows-setup.md`) para
tres fallos frecuentes que antes no tenían rastro permanente en el repo
(ruta de workspace, permiso roto de pytest en TMP/TEMP, cuelgue de `npm
test` por caché de Vite), más una corrección real en `docs/guides/
ollama.md` (su paso 3 no tenía efecto). Detalle completo, incluidas las
dos limitaciones dejadas fuera a propósito, en ADR 0039. **Cierra P4** --
los cinco sub-hitos (P4.1-P4.5) están completos.

---

## Gate pre-MVP — medición realizada, candidato no aprobado

**Resultado (15 de agosto de 2026): `measurement_valid = true` / `candidate_passed = false`.**
Candidato elegido: repo sintético `textkit-slugify` (bug acotado de guiones
al borde en una función `slugify` + pedido de test de regresión) importado
y corrido una sola vez contra `ollama:qwen2.5-coder:7b`, `model_concurrency=1`,
sin repetir la corrida ni realizar reintentos manuales ni ajustar el goal
(los reintentos automáticos normales del orquestador sí ocurrieron), sin
repair-center. El proyecto terminó
`failed`; 5/9 del checklist de aprobación. Evidencia completa en
`benchmarks/results/mvp-candidate-textkit-slugify-2026-08/` (`findings.md`,
`environment.md`, `verification.md`, `relevant-trace.json`, `changes.patch`,
`export-summary.json`/`summary.md`, `project-report.json`, `source-hashes.txt`).

Dos causas reales; la primera ya quedó corregida por Gate-MVP.1 y la segunda
define el único incremento de núcleo todavía abierto:

1. Un work item entregó un candidato fuera de su `expected_outputs`
   declarado (tocó un archivo que era el trabajo de una tarea hermana); en
   esa corrida el candidato se integró igual y el `Artifact` resultante
   bloqueó permanentemente a la hermana. Gate-MVP.1 cerró después esa causa:
   ahora el candidato se rechaza antes de tocar disco.
   Adjudicación read-only completa (releyendo `_colliding_dependency_paths`
   contra la tabla `artifacts` real, no inferida del mensaje de evento) en
   `findings.md` -- una lectura previa había atribuido esto a un
   interbloqueo contra la tarea de cierre auto-generada por P0, y esa
   atribución era incorrecta.
2. El único work item que sí llegó a `completed` integró código que ni
   siquiera ejecuta (`NameError` por un `import` faltante) y borró tests
   preexistentes en vez de extenderlos -- el revisor (LLM) lo aprobó
   afirmando que "procesa correctamente". Ningún perfil de validación
   estático disponible para proyectos importados puede detectar un error
   de runtime.

La medición no corrigió código. Gate-MVP.1 se implementó después, en un PR
independiente y dirigido por esta evidencia. Gate-MVP.2 conserva la misma
disciplina: una garantía observable, un PR, sin abrir P5 ni otra campaña de
prompts.

### Gate-MVP.1 — Frontera efectiva de escritura

**Objetivo:** un candidato no puede escribir paths fuera de los reclamos
efectivos de su propio work item (su `expected_outputs`/scope real), no
sólo fuera de lo que ya reclamó otro work item. Diseñar a partir de la
adjudicación de `findings.md` -- la causa real fue una tarea entregando
fuera de su propio scope, no un interbloqueo contra la tarea de cierre; no
atribuir el problema a esa tarea de cierre sin evidencia equivalente la
próxima vez que se retome esto.

**Resultado (17 de agosto de 2026): CERRADO.**
`Orchestrator._out_of_scope_paths` (`engine.py`, función pura, sin
`Repository` ni grafo de dependencias) compara el candidato contra
`merge_path_claims(item.owned_paths, item.expected_outputs)` normalizando
separador y mayúsculas en ambos lados -- deliberadamente **sin** las
excepciones de ancestro/`shared_component` que sí tiene
`_colliding_dependency_paths`, porque esas responden una pregunta cruzada
("¿pueden dos tareas relacionadas compartir esto?"), no si esta tarea se
salió de su propio scope. `_reject_out_of_scope_write` lo envuelve, emite
`workspace_own_scope_rejected` (evento distinto de
`workspace_action_rejected`) y lanza `InvalidPlan`. Tres puntos de
integración, los tres antes de `isolation.prepare()`/`workspace.stage()` --
nunca dentro de `_evaluate_candidate`, que en el camino autónomo vive fuera
del único `except` que captura `InvalidPlan` (se habría propagado sin
capturar). Dos límites explícitos, documentados en ADR 0040: **sin ningún
claim parseable la frontera es permisiva** (no una garantía universal --
`expected_outputs` en prosa sigue sin exigir nada, a propósito, mismo
criterio que ya fijó ADR 0023 del lado cruzado); y **es una frontera de
coordinación entre tareas, no una sandbox de seguridad** -- no impide
escritura adversarial dentro de lo ya autorizado a nivel de proyecto, sólo
que una tarea honesta-pero-descuidada dañe en silencio el trabajo de una
hermana. Verificado con 10 tests nuevos en `test_evaluation_contracts.py` +
4 end-to-end en `test_effective_write_boundary.py` (uno por camino real más
la regresión nombrada `textkit-slugify`, que confirma que la tarea hermana
deja de bloquearse), y tres ajustes sobre tests preexistentes cuyo
`expected_outputs`/mock no coincidía con lo que su propio fixture
entregaba -- `test_workspace_io_failures.py` (dos `expected_outputs`
alineados con `library/api.py`), `test_expected_output_criteria.py`
(respuesta `work` explícita que entrega `INFORME.md`) y
`test_task_splitting.py` (la consolidación mockeada entrega los
`owned_paths` reales que la maquinaria de split le asignó, en vez del path
fijo que traducía antes) -- mismo desajuste que este gate existe para
detectar, documentado en el PR. Detalle completo en ADR 0040.

### Gate-MVP.2 — Verificación honesta de proyectos importados

**Objetivo:** mantener deshabilitada la ejecución por defecto (P3.4/ADR
0034 sigue vigente, no se afloja), pero distinguir estructuralmente
validación estática de validación ejecutada -- un `Review` y un
`TestReport` producidos sin ejecutar el candidato no deben poder
presentarse (a la UI, al `delivery_report`, a un revisor humano) como
prueba de que el código funciona. El diseño posterior debe incluir análisis
estático seguro capaz de atrapar errores como `F821` (nombre no definido --
exactamente la clase de bug que apareció en esta corrida) y contexto
base-vs-candidato explícito para que el reviewer pueda detectar
eliminaciones de código/tests existentes, no sólo adiciones. No resolver
esto sólo cambiando prompts -- hace falta una señal estructural nueva.

**Alcance aprobado para implementación: un único PR con dos incrementos
lógicos, no una nueva familia Gate-MVP.2a/2b salvo que el diff demuestre que
no puede revisarse con seguridad.**

1. **Verdad estructural de la evidencia.** `TestReport` debe persistir un
   modo explícito de verificación (`static_only` o `executed`), calculado por
   Agentarium desde una señal propia del ejecutor que diga si
   `SCRIPT_EXECUTION` realmente arrancó. No se deriva de `passed`: una
   ejecución que termina con exit code distinto de cero sigue siendo
   evidencia ejecutada, mientras un bloqueo de autoridad, un contrato sin
   entrypoint o un comando que nunca arrancó siguen siendo `static_only`.
   No se agrega `externally_verified` sin un mecanismo real que ingiera y
   conserve esa evidencia.
2. **Honestidad visible y auditable.** `delivery_report`, export summary,
   API/TypeScript y UI deben mostrar el modo sin llamar "tests pasados" a
   una validación sólo estática. Todo item `completed` con
   `verification_mode=static_only` entra en `unverified_completed_items`
   con una razón estructurada; `COMPLETED` puede seguir describiendo el fin
   del workflow, nunca prueba funcionalidad por sí solo.
3. **Red mecánica mínima contra el incidente real.** Agregar un perfil
   no-ejecutante para Python que corra Ruff `F821` con configuración aislada
   y sin caché. Ruff pasa a dependencia de runtime (no sólo `dev`) porque
   Agentarium lo invoca en producción. Activar el perfil exige bump de
   `VALIDATION_CONTRACT_VERSION` y pruebas positiva, negativa y contra un
   `ruff.toml` que intente ignorar `F821`.
4. **Contexto base-vs-candidato persistido.** Las definiciones/métodos de
   nivel superior eliminados se calculan mecánicamente, se conservan como
   evidencia del `TestReport`/informe y se envían en ambos payloads del
   reviewer. El prompt correspondiente cambia de versión. La lista no es
   un auto-reject: una eliminación puede ser legítima, pero ya no puede
   quedar invisible ni depender de reconstruir el diff a mano.

Límite explícito: Gate-MVP.2 eleva la validación estática y evita presentar
la evidencia como algo que no es; no demuestra lógica correcta ni habilita
ejecución de repos importados. P3.4/ADR 0034 permanece intacto.

**Resultado (18 de agosto de 2026): CERRADO.**
`TestReport.verification_mode: static_only | executed` (requerido, sin
default) se calcula en `Orchestrator._evaluate_candidate` desde
`CommandResult.started` (nuevo campo del ejecutor, `True` únicamente
cuando un subproceso real llegó a arrancar, sin importar el resultado) --
nunca de `passed`, confirmado con un caso real no importado que ejecuta y
falla (`verification_mode=executed`, `passed=false`). Propagado a
`delivery_report` (`unverified_completed_items` gana `reason`:
`missing_review_or_test_report` o `static_only_verification`), a
`export_summary` (markdown nunca dice "ok" para evidencia estática) y a
`TaskDrawer`/`DeliveryReportView` (tercera cláusula visible: "código
ejecutado"/"código no ejecutado"). Perfil no-ejecutante nuevo
`PYTHON_UNDEFINED_NAMES` (`python -m ruff check --isolated --no-cache
--select F821`) en `NON_EXECUTING_PROFILES`, activo también para proyectos
importados; `--isolated` verificado con un test que coloca un `ruff.toml`
que intenta ignorar `F821` dentro del propio árbol validado. Ruff pasa a
`[project].dependencies`. `VALIDATION_CONTRACT_VERSION` de `profiles-v7` a
`profiles-v8`. `GitWorktreeIsolation.read_base_file` +
`Orchestrator._removed_top_level_definitions` comparan AST base-vs-candidato
(funciones/clases de nivel de módulo y métodos directos de esas clases,
como `"Clase.metodo"`) y persisten una entrada determinista por archivo en
`TestReport.command_evidence`, enviada en las dos rutas de
`_review_payload`; nunca un auto-reject, y expuesto de verdad (no sólo
persistido) en `summary.json`/`summary.md` y en el detalle del tester de
`DeliveryReportView`/`TaskDrawer`, mostrando únicamente las listas no
vacías. `ARTIFACT_PROMPT_VERSION` nuevo (no `WORKSPACE_PROMPT_VERSION`, que
sólo aplica a `operation=="work"`) bumpeado por el cambio de payload del
reviewer, y **congelado por el benchmark**: `prompt_versions()` no lo
declaraba, así que un cambio del contrato del reviewer no producía
`SuiteDrift` -- consecuencia declarada, todo ledger anterior ahora produce
drift, que es la semántica buscada y no afecta la evidencia versionada.
La advertencia de `unverified_completed_items` muestra la razón de cada
item ("código no ejecutado" vs. "sin review o informe técnico") y el hint
del outcome dejó de decir "evidencia verificada" para un item
`static_only`. Un bug real de la propia política de seguridad se encontró
durante la verificación, no por inspección: `--output-format=concise`
matcheaba el deny-token `format` (`-`/`=` cuentan como frontera de palabra)
y `PYTHON_UNDEFINED_NAMES` habría fallado siempre, para cualquier `.py`,
por `CommandRejected` -- corregido quitando esa flag, sin tocar la política
de seguridad. Verificado con `test_validation_profiles.py`,
`test_safe_commands.py`, `test_schema_migration.py`,
`test_git_worktree_isolation.py`, `test_evaluation_contracts.py`,
`test_benchmarks.py`/`test_benchmark_identity.py` (drift de `artifact` con
versión distinta y con clave ausente), `test_export_summary.py` (nuevo),
`tests/home-delivery-report.test.mjs`/`home-work-items.test.mjs`, y
`test_honest_verification.py` (nuevo): reconstrucción byte a byte del
incidente real vía `evaluate_operator_candidate` contra un proyecto
importado -- F821 rechaza sin ejecutar nada, `verification_mode=static_only`,
los 3 nombres reales del incidente aparecen en `removed_top_level_names` y
en cada payload del reviewer que se disparó, más control negativo y caso
no-importado ejecutado-y-fallido. `test_type_contract.py` confirma cero
drift backend↔TypeScript. `ruff`, `mypy`, `.\test.ps1` completos en verde.
Detalle completo en ADR 0041.

### Gate-MVP.3 — repetición única (recién después de cerrar 1 y 2)

Una sola repetición del mismo caso (`textkit-slugify`), mismo modelo,
mismas condiciones -- para confirmar que las dos correcciones de arriba
resolvieron lo que esta corrida encontró. No es una excusa para relanzar
buscando otro veredicto si algo más sale mal.

El criterio anterior `unverified_completed_items=[]` queda reemplazado: en
un proyecto importado cuya ejecución está bloqueada honestamente, un item
`completed` **debe** aparecer como `static_only`/no verificado. Gate-MVP.3
pasa sólo si esa limitación es visible y la verificación externa en clones
limpios aporta la evidencia funcional: patch aplicable, árbol esperado,
suite completa verde post-fix y contraprueba roja pre-fix. Además exige que
Gate-MVP.1 impida la escritura fuera de scope, que F821 no llegue a integrar
y que cualquier eliminación de tests quede registrada.

---

## Decisión posterior a Gate-MVP.3 — no otra matriz automática

La matriz 3 casos × 3 modelos × 3 repeticiones deja de ser una precondición
automática del MVP. Responde principalmente "¿qué modelo rinde mejor?" y no
"¿el flujo inicial soportado es útil y honesto?"; repetir 27 corridas antes
de probar el producto con usuarios volvería a priorizar maquinaria sobre
señal real.

Después de Gate-MVP.3 se toma una decisión explícita:

1. Si falla por una causa mecánica nueva y demostrada: corregir sólo esa
   causa o detener el candidato; no relanzar buscando suerte.
2. Si pasa: declarar un perfil inicial soportado y estrecho -- Windows,
   Ollama, `qwen2.5-coder:7b`, `model_concurrency=1`, repos importados sin
   ejecución automática -- y completar una suite candidata de **tres flujos
   importados en total**, una corrida por flujo y sin pesca: Gate-MVP.3 más
   un cambio documental/no ejecutante y un segundo cambio de código pequeño
   que preserve una suite preexistente.
3. Si los tres flujos terminan con resultados correctos o fallos explicables,
   cero falsos `completed` y evidencia versionada, habilitar una alpha para
   1--3 usuarios reales. La comprobación funcional de código importado sigue
   ocurriendo externamente mientras P3.4 esté vigente.
4. Sólo después de señal de alpha decidir si una nueva matriz 3×3×3 aporta
   valor para comparar modelos. Si se ejecuta: suite nueva, identidad
   congelada, baseline P1 inmutable y adjudicación manual de cada falso
   `completed`. No requiere extender el harness de benchmark antes de esa
   decisión.

---

## P5 — extensibilidad, sólo después del MVP

**Bloqueado explícitamente (15 de agosto de 2026):** el candidato MVP
corrido no fue aprobado (`candidate_passed=false`, ver "Gate pre-MVP —
medición realizada, candidato no aprobado" arriba). Las dos limitaciones
reales que esa medición observó (colisión de ownership por escritura fuera
de scope; verificación que no distingue estático de ejecutado) pertenecían
al núcleo/Gate-MVP y **ya están cerradas** -- Gate-MVP.1 (ADR 0040) y
Gate-MVP.2 (ADR 0041) respectivamente. Ninguna de las dos justificó
promover un elemento de extensibilidad de la lista de abajo, y eso no
cambia: antes de P5 falta Gate-MVP.3 y la decisión de alpha posterior.

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
- Otra matriz 27/27 usada como gate automático antes de una alpha.
- Más prompt tuning sin causa medible.
- Refactor completo de `engine.py` o `page.tsx` antes de P3.1.
- Departamentos/plugins/LangGraph/Postgres/multiusuario.
- Reescritura del historial de `main` por los commits duplicados antiguos: es ruido cosmético y no justifica reescribir historia compartida.
- Resolución de conflictos de ownership de plan (`plan_owned_path_conflict_unresolved`) más allá de la compuerta de ejecución actual — encontrado en P3.0, no bloquea nada hoy porque la compuerta final ya evita la sobreescritura silenciosa.
- `agentarium benchmark run` con stdout redirigido en Windows sin forzar UTF-8 (`PYTHONUTF8=1`) — encontrado en P3.0 (`_echo` en `cli.py` usa `·`/`→`); no corrompe el ledger pero corta una matriz de más de una corrida a mitad de camino.
- Cerrar la brecha TypeScript↔fixture-de-test en `tests/support/fixtures.mjs` (duplicado a mano de los tipos de `page.tsx`, puede desalinearse en silencio) — encontrado en P3.1b, aceptado a propósito por ahora (ver ADR 0031).

---

## Qué sigue

P0, P1, P2, P3 (P3.0-P3.4), P4 (P4.1-P4.5), Gate-MVP.1 y Gate-MVP.2 están
cerrados. El flujo candidato a MVP ya se eligió y se midió una vez (15 de
agosto de 2026, ver "Gate pre-MVP — medición realizada, candidato no
aprobado" arriba): no fue aprobado. Lo que sigue es una decisión explícita,
en este orden -- ninguno de estos pasos se dispara solo:

1. **Gate-MVP.3**: una sola repetición del mismo caso (`textkit-slugify`),
   sin cambiar goal, modelo ni condiciones y sin exigir que la evidencia
   estática finja ser ejecución -- `unverified_completed_items` no vacío ya
   no es por sí solo un fallo, mientras esté honestamente marcado
   `static_only` (ver "Gate-MVP.3 — repetición única" arriba para el
   criterio completo).
2. Si Gate-MVP.3 pasa, completar dos flujos importados adicionales con el
   perfil inicial soportado y decidir si Agentarium entra en alpha.
3. Recoger señal de 1--3 usuarios antes de promover P5. La matriz 3×3×3 es
   opcional y posterior, no el siguiente paso automático.

**P5 (extensibilidad) sigue bloqueado.** Sólo entra por una limitación real
observada del MVP, nunca por curiosidad técnica (regla 10 de "Reglas para
no volver a iterar de más"). Los hallazgos observados pertenecen al Gate
pre-MVP y no justifican promover ningún elemento de extensibilidad de P5.

No ejecutar Gate-MVP.3 (implica correr Ollama), ampliar a otros modelos ni
abrir P5 sin autorización explícita de la próxima sesión -- ninguno de
estos pasos se dispara solo por haber cerrado Gate-MVP.2.

---

## Instrucción para la próxima sesión/agente

Usar este texto literalmente como punto de partida:

> Lee `CLAUDE.md` y `PLANS.md` completos. Verifica `main` actualizado y
> limpio. P0--P4, Gate-MVP.1 y Gate-MVP.2 están cerrados; no los reabras
> sin una regresión demostrable. El candidato `textkit-slugify` fue una
> medición válida pero no aprobada; evidencia en
> `benchmarks/results/mvp-candidate-textkit-slugify-2026-08/`. El siguiente
> paso es Gate-MVP.3: una sola repetición del mismo caso, mismo modelo,
> mismas condiciones, sin pesca -- pero no lo ejecutes todavía sin
> confirmación explícita, porque implica correr Ollama de verdad. No
> amplíes a otros modelos ni abras P5 sin esa misma confirmación.

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
- `docs/decisions/0035-*` — importar un proyecto existente sin arriesgar el original (P4.1).
- `docs/decisions/0036-*` — exportar cambios de forma auditable sin tocar el origen (P4.2).
- `docs/decisions/0037-*` — centro de reparación: agregación por causa, vínculo estructural escalar→aprobar, interfaz (P4.3).
- `docs/decisions/0038-*` — informe de entrega y auditoría, historial de intentos on-demand, guarda A→B (P4.4).
- `docs/decisions/0039-*` — onboarding Windows: `doctor` con diagnóstico real, mensaje accionable de `$GIT_DIR`, documentación de fallos frecuentes (P4.5).
- `docs/decisions/0040-*` — frontera efectiva de escritura del propio work item, distinta de la colisión cruzada de `_colliding_dependency_paths` (Gate-MVP.1).
- `docs/guides/windows-setup.md` — guía de instalación y diagnóstico en Windows (P4.5).
- `benchmarks/results/p1-baseline-2026-08/` — baseline, ambiente y adjudicación manual.
- `benchmarks/results/p3.0-confirmation-2026-08/` — confirmación dirigida de P2 con modelo real, ambiente y hallazgos.
- `benchmarks/results/mvp-candidate-textkit-slugify-2026-08/` — candidato MVP medido, no aprobado; adjudicación read-only de la colisión de ownership, evidencia completa.

Cuando una afirmación histórica de este plan choque con un ADR o con datos versionados del benchmark, la evidencia versionada manda; actualizar este resumen en vez de reinterpretar el pasado.
