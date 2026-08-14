# ADR 0038: Entrega y auditoría -- backend, API/CLI y frontend (P4.4)

- Estado: aceptada
- Fecha: 2026-08-14

## Contexto

PLANS.md P4.4: informe final de qué se pidió, qué cambió, qué validaciones
corrieron y qué quedó sin verificar; historial de intentos/modelos cuando
aporte a diagnóstico, no como panel decorativo. P4.1/P4.2/P4.3 ya cerrados
dejan un proyecto real con un historial de intentos, artefactos, revisiones
e integraciones git auditables -- P4.4 es la fase que junta esa evidencia
dispersa en una vista/lectura útil sin obligar a leer eventos crudos.
Dividida en dos PRs por la misma razón que P4.3 (superficie combinada
comparable a P4.3a+b juntos): P4.4a (backend/API/CLI, PR #24, squash
`4b47c0b`) y P4.4b (frontend, este PR) -- P4.4a no tocó ningún archivo de
`app/`.

## Decisión

### P4.4a -- `ApplicationService.delivery_report()` y el hueco real de `AgentRun`

`build_delivery_report()` (`services/delivery_report.py`, mismo
`build_payload`-only, sin I/O que `export_summary.py`) deriva, por work
item, un `outcome` en una prioridad estricta -- hechos terminales primero
(`COMPLETED`/`CANCELLED`, este último distinguiendo `superseded_by_split`
de `cancelled` según un evento real `task_split_created`), luego una
aprobación pendiente real (`awaiting_approval`), luego la misma
clasificación de atascado que ya usa `repair_center()`
(`_classify_stuck_work_items()`, extraída sin cambiar su lógica -- los
tests de P4.3a pasan sin tocar ninguna aserción), y por último `in_progress`.
Tres hallazgos de código muerto/inalcanzable, cada uno verificado por
lectura directa antes de diseñar la clasificación, no asumidos:
`ReviewVerdict.REJECTED` nunca se construye (el vocabulario real es el
binario `APPROVED`/`CHANGES_REQUESTED` que `repair_center()` ya usa); un
ítem `COMPLETED` no puede carecer de evidencia bajo ningún camino normal
(`unverified_completed_items` queda documentado como red de seguridad para
una DB editada a mano, no como señal principal); y
`WorkItemStatus.AWAITING_APPROVAL` nunca se asigna a un work item en
ningún lugar del backend -- `awaiting_approval` se deriva del mismo vínculo
estructural que `resolve_approval()` (ADR 0037): sólo cuenta un evento
`task_escalated` cuyo `metadata.approval_id` referencia una aprobación
`PENDING` real, con el `work_item_id` de esa aprobación confirmado igual al
del propio evento -- nunca `approval.work_item_id` en aislamiento, que una
aprobación genérica podría llevar por coincidencia. Corregido dos veces
sobre el mismo PR hasta llegar a esa disciplina exacta.

`Repository.list_agent_runs(project_id)` cerró el único hueco real de
`AgentRun`: existía `add_agent_run` pero ningún método `list_`, ninguna
ruta API, ningún tipo TypeScript. Alcance de proyecto (siete de ocho
métodos `list_` ya lo son) y orden estable (`started_at`+`id` como
desempate, porque el proveedor mock puede producir intentos con timestamp
idéntico). Rutas nuevas: `GET /api/projects/{id}/report`,
`GET /api/projects/{id}/agent-runs`; CLI `agentarium project report <id>`.

### P4.4b -- tipos TypeScript y registro de contrato

`AgentRun` (dominio real, sin claves extra en la ruta) se agregó a
`DIRECT_PAIRS` -- `shape_from_model` lo recorre de forma exhaustiva e
incluye a `ResourceUsage` de manera automática por ser un `BaseModel`
anidado no nulable, así que `ResourceUsage` no necesita su propia entrada
en el registro. `DeliveryReport` (dict compuesto sin una única clase
Pydantic detrás, igual que `ProjectDetail`/`ExportSummary`) se agregó a
`ENDPOINT_PAIRS` contra el mismo proyecto fixture que ya prueba esos dos.
Diferido explícitamente desde P4.4a (su propio PR lo documentó así) porque
registrar sin el tipo TS correspondiente habría roto
`test_type_contract.py` en un PR backend-only -- mismo patrón que P4.3a.
El extractor (`scripts/check-api-contract.mjs`) sólo lee `app/page.tsx`,
así que los cuatro tipos nuevos (`ResourceUsage`, `AgentRun`,
`DeliveryReportWorkItem`, `DeliveryReport`) viven ahí, no en
`delivery-report.tsx` -- confirmado leyendo el extractor antes de
escribirlos, no asumido del patrón de `RepairItem`/`ExportSummary`.

### P4.4b -- `app/delivery-report.tsx` y el historial de intentos, ambos on-demand

Ninguno de los dos fetches nuevos se agregó a `openProject()`. La razón es
doble: PLANS.md pide explícitamente que el historial de intentos aporte a
diagnóstico "no como panel decorativo", y `openProject()` se reejecuta
después de cada acción (retry/rework/escalate/priority/control) -- si el
informe y el historial se auto-refetchearan ahí, cada clic en el drawer
dispararía dos requests más, y **cada test existente que abre un proyecto
tendría que registrar las dos rutas nuevas** en el mock estricto de
`tests/support/fetch-mock.mjs` (falla ruidosa ante una ruta no registrada)
para no romperse -- un costo de acoplamiento real, no sólo de estilo.
En cambio, ambos son un clic explícito: `DeliveryReportView` (nueva
sección dentro de `ProjectView`, antes de la barra de progreso) con un
botón "Generar informe"/"Actualizar informe", y una sección nueva
"Historial de intentos" en `TaskDrawer` con su propio botón "Ver historial
de intentos" -- mismo patrón ya establecido por
`loadRepairArtifacts()`/"Recuperar artefacto" en el Centro de reparación
(P4.3b), que carga bajo demanda al abrir ese panel específico.
`GET .../agent-runs` es de alcance de proyecto (P4.4a), así que se pide
una sola vez por proyecto y se filtra en cliente por `work_item_id` dentro
de `TaskDrawer`, igual que ya hacen `reviews`/`test_reports`.

**Guarda contra respuestas tardías A→B, corrección explícita del usuario
para esta fase.** Ambos fetches usan un id de solicitud monótono
(`deliveryReportRequestRef`/`agentRunsRequestRef`, un `useRef` incrementado
en cada llamada): una respuesta sólo se aplica si su id sigue siendo el
último emitido en el momento en que resuelve. `openProject()` incrementa
ambos y limpia `deliveryReport`/`agentRuns` a `null` en cada cambio de
proyecto -- necesario pero no suficiente por sí solo: sin la guarda, una
respuesta lenta del proyecto A que resuelve después de que el usuario ya
abrió el proyecto B repoblaría el estado con los datos de A encima del
`null` que el cambio de proyecto acababa de dejar. Ningún otro fetch de
este archivo tenía esta guarda antes (`loadRepairArtifacts()` tampoco la
tiene hoy) -- deliberadamente no retrofiteada ahí, fuera de alcance de esta
fase.

Las dos regresiones A→B dedicadas (una por fetch, en
`tests/home-delivery-report.test.mjs` y `tests/home-work-items.test.mjs`)
encontraron un bug real antes de mergear, no sólo lo previnieron: la
primera versión reseteaba `deliveryReport`/`agentRuns` a `null` en
`openProject()` pero no sus flags `...Loading`, y el `finally` de cada
`load...()` deliberadamente se salta el reset de loading cuando la
solicitud ya es vieja (para no pisar una solicitud más nueva del mismo
proyecto todavía en vuelo) -- el resultado neto era que abandonar un
fetch a mitad de camino dejaba el botón del proyecto nuevo trabado en
"Generando…"/"Cargando…" para siempre. Corregido reseteando también
ambos `...Loading` a `false` dentro de `openProject()`.

**Evidencia visible también en ítems `completed`, segunda corrección
explícita.** `DeliveryReportRow` no oculta el veredicto de revisión ni el
resultado de test según el `outcome` -- el badge PASS/FAIL de la fila
colapsada y el panel de evidencia expandido usan exactamente la misma
lógica sin importar si el ítem es `completed`, `failed`, `blocked`, etc.
Un ítem completado es justamente el que más necesita mostrar por qué se
confió en él (qué revisor aprobó, qué test pasó, qué commit se integró),
no el que menos.

Dos paneles ya tipados pero jamás renderizados, arreglados en el mismo PR
por ser triviales y ya cubiertos por un test de P3.1b
(`home-work-items.test.mjs`, comentario propio: "P4.4 ... exponer la
evidencia relevante"): `decision.rationale` (el "por qué" de una decisión,
sólo se mostraba el "qué") y `metrics.tasks_completed`/`tasks_rejected` en
el panel de métricas del proyecto (ya viajaban en la respuesta real desde
antes de P4.4, sin usarse).

`PanelHeading` se movió de `page.tsx` a `shared.tsx` -- mismo criterio de
extracción que el resto de este archivo (un segundo consumidor real,
`delivery-report.tsx`, aparece; se mueve entonces, no antes). Comportamiento
idéntico, sin cambiar su firma.

## Garantía real

`.\test.ps1` completo en verde: **521 passed + 1 skipped** (backend --
sin cambio numérico sobre P4.4a: `AgentRun`/`DeliveryReport` se verifican
dentro de `test_backend_ts_contract_has_no_drift`, el test de drift ya
existente que recorre `ALL_TS_TYPE_NAMES`, no agregan funciones de test
nuevas) y **44/44** web (incluye `tests/home-delivery-report.test.mjs`
nuevo -- 4 pruebas -- y las dos pruebas de historial de intentos agregadas
a `home-work-items.test.mjs`, incluidas las dos regresiones A→B descritas
arriba). Ruff/MyPy/`check-api-contract.mjs`/build limpios.

Verificación manual en navegador real (backend real en :8000, no un mock)
contra un proyecto histórico de benchmark ya completado
(`architecture_document · mock · #1`, 3/3 tareas): informe generado con
los 3 outcomes reales en `completed`, evidencia de revisor (criterios de
aceptación cumplidos) y tester (comprobaciones registradas) visible al
expandir cada fila, y commit/rama/archivos de integración reales. Historial
de intentos verificado en dos work items distintos del mismo proyecto sin
recargar: el primero (1/3 intentos) mostró 3 entradas; el segundo (2/3
intentos, con un veredicto `REVIEW` real de un intento previo antes del
`PASS` final) mostró el intento 2 del worker sin un segundo clic en "Ver
historial de intentos" -- confirma tanto el cacheo por proyecto como el
filtrado por `work_item_id` contra datos reales, no sólo el mock de test.

## Limitaciones

El informe y el historial no se auto-refrescan tras una acción (retry/
rework/escalate) -- decisión deliberada de esta fase (ver más arriba), no
un descuido: hay que volver a pedirlos con el mismo botón. La guarda A→B
no se retrofiteó a `loadRepairArtifacts()` (P4.3b), que comparte la misma
clase de riesgo sin corregir. Hereda las limitaciones ya declaradas en ADR
0034 (autoridad de ejecución por perfil de validación), 0036 (binding
local/loopback) y 0037 (recover sólo ofrece artefactos propios del ítem,
sin badge de conteo en vivo en el nav) sin cambios.

## Qué queda para P4.5

P4.4 cierra el último tramo de contenido de P4: importar/exportar sin
arriesgar (P4.1/P4.2), reparar (P4.3) y ahora entregar/auditar (P4.4).
Sólo queda P4.5 -- onboarding Windows: setup reproducible, comprobación de
Ollama/modelos/configuración, mensajes accionables para fallos frecuentes
de permisos/temp/worktree.
