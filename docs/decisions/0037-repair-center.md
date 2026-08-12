# ADR 0037: Centro de reparación -- backend, API/CLI y frontend (P4.3)

- Estado: aceptada
- Fecha: 2026-08-12

## Contexto

PLANS.md P4.3: ver tareas fallidas y su causa estructurada, reintentar/
recuperar artefacto/enviar candidato sin perder linaje ni presupuesto de
intentos, exponer la evidencia relevante sin obligar a leer eventos
crudos. P4.1 y P4.2 ya cerrados dejaron un proyecto real con work items
que pueden fallar de verdad (importado o no) y un historial git
auditable -- P4.3 es la primera fase que le da al usuario una forma de
actuar sobre esas fallas en vez de sólo observarlas. Dividida en dos
PRs por decisión explícita: P4.3a (backend/API/CLI, PR #22, squash
`1a43ce0`) y P4.3b (frontend/UI, este PR) -- P4.3a no tocó ningún
archivo de `app/`.

## Decisión

### P4.3a -- agregación de lectura y el callejón escalar→aprobar

`ApplicationService.repair_center()` recorre todos los proyectos y
agrega work items en 4 causas: `failed`/`changes_requested` (estado
directo), `exhausted` (`READY` con `attempt_count >= max_attempts`) y
`blocked` (`BLOCKED` con una dependencia real en `FAILED`/`CANCELLED`,
resuelta vía el propio `dependency_ids` del item -- sin ella, la fila no
se genera, porque un bloqueo sin causa real todavía no es reparable).
Cada fila lleva el `last_error` o, si no hay, las razones de la última
revisión/resumen de test -- la evidencia suficiente para decidir sin
abrir el log de eventos crudo.

`resolve_approval()` cierra un callejón real: antes de esta fase,
aprobar una escalación dejaba el work item en el mismo estado, sin
ninguna acción automática. La autorización para preparar el retry es
estructural, no inferida: `escalate_work_item` graba `approval_id` en
la `metadata` de su propio evento `task_escalated`, y `resolve_approval`
sólo dispara `retry_work_item` cuando encuentra ese evento con
`metadata.approval_id` igual al id de la aprobación resuelta -- nunca
a partir de `approval.work_item_id` solo, porque una aprobación
genérica (no nacida de una escalación) podría cargar ese campo por
coincidencia sin que eso autorice nada. Aprobar no ejecuta
automáticamente: sólo prepara un retry si el item sigue en un estado
elegible, y si no quedan aprobaciones pendientes intenta
`resume_project()` (atrapando el `ValueError` si el proyecto no está
en un estado que lo permita) para no dejar el proyecto colgado
esperando una aprobación ya resuelta. Rechazar nunca reintenta.
`GET /api/repair-center` y `agentarium repair {list,retry,recover,
rework,escalate,candidate}` (CLI, `PAYLOAD_FILE` en vez de
`PAYLOAD_JSON`) exponen esto sin agregar rutas ni schemas nuevos más
allá de lo estrictamente necesario.

### P4.3b -- `attempt_repair_available` (backend, autorizado explícitamente sobre P4.3a ya mergeado)

`repair_center()` gana `attempt_repair_available: bool` por fila.
`retry_work_item`/`extend_attempt_budget` levantan `ValueError` sin
capturar si `max_attempts` ya está en `MAX_WORK_ITEM_ATTEMPTS` (=25) y
el item está agotado; `recover_artifact`/`submit_candidate` comparten
exactamente ese camino porque ambos delegan en `retry_work_item`
cuando el item no está ya `READY`. Regla por causa: para `blocked` el
valor es **siempre** `false` -- retry/recover/candidate son inválidos
ahí sin importar presupuesto, `retry_work_item` los rechaza para
cualquier item `BLOCKED` de por sí. Para las tres causas accionables,
`false` únicamente cuando el item está agotado **y** `max_attempts` ya
alcanzó el tope; `true` en cualquier otro caso. Un bug real de la
primera versión de este cálculo (ignoraba `cause` y evaluaba sólo
agotamiento) se encontró antes de mergear, con un test dedicado que lo
cubre. La UI oculta retry/recover/candidate cuando es `false`, pero
siempre ofrece escalar, que nunca toca presupuesto de intentos.

### P4.3b -- interfaz

`app/repair-center.tsx` (nuevo, presentacional, sin estado propio --
mismo patrón que `export-project.tsx`/`import-project.tsx`, no el más
viejo de `ApprovalCenter`): filtros de proyecto/causa sobre el array ya
cargado (sin refetch), fila expandible in-place con la evidencia
suficiente, y para causa `blocked` ningún botón de acción -- sólo un
enlace a la dependencia bloqueante, porque la causa real está en otro
lado. `RepairItem.cause` es la unión literal `"failed" |
"changes_requested" | "exhausted" | "blocked"` (el extractor de
contrato ya resolvía uniones de literales string a `{kind: "string"}`,
verificado en `scripts/check-api-contract.mjs` antes de asumirlo).

**Patrón único de confirmación explícita, sin `window.confirm` en
ningún lado del código base**: cada botón de acción abre un panel
inline en vez de disparar la llamada -- retry es un panel simple,
escalate/rework llevan el textarea de razón real dentro del panel
(escribir + confirmar **es** la confirmación, sin un tercer clic
redundante), recover dispara la carga perezosa de artefactos del
propio item (`GET /api/projects/{id}`, filtrado en cliente -- no ofrece
artefactos de una dependencia aprobada, simplificación explícita) y
candidate es un formulario título/resumen/archivos que sólo habilita
"Enviar candidato" con los tres campos completos. `resetRepairDrafts()`
limpia todo ese estado de borrador al cambiar de tarea, de acción,
cancelar, cerrar o cambiar la fila desplegada, y al completar una
acción -- con una regresión dedicada (abrir el formulario de candidato
en la tarea A, escribir, cambiar a la tarea B sin enviar, confirmar que
B nace vacío) que prueba que un borrador nunca sobrevive a un cambio de
fila. Toda acción exitosa vuelve a pedir `GET /repair-center`.

**P4.3a no había tocado el frontend.** `TaskDrawer` disparaba retry en
un solo clic y `reworkTask`/`escalateTask` mandaban una razón
hardcodeada, nunca lo que el usuario escribía -- deuda real, no un
ajuste cosmético. Mismo patrón de panel inline aplicado ahí: retry con
confirmación simple, rework/escalate con el textarea real reemplazando
el string fijo. Estado propio y separado del Repair Center
(`taskConfirmingAction`/`taskReworkReason`/`taskEscalateReason`,
limpiados por `resetTaskActionDrafts()`) porque vive en un flujo
distinto con su propio ciclo de vida, aunque comparta el mismo riesgo
estructural (una razón tipeada para una tarea nunca debe sobrevivir a
un cambio de tarea).

## Garantía real

`.\test.ps1` completo en verde: **499 passed + 1 skipped** (backend,
incluye `test_repair_center.py` -- causas, el límite exacto de 25
intentos, el caso `blocked` con presupuesto intacto -- y las
extensiones de `test_approvals.py` para el vínculo estructural) y
**37/37** web (incluye `tests/home-repair-center.test.mjs`, 12 pruebas
nuevas, y los 3 tests de `home-work-items.test.mjs` migrados al flujo
de confirmación de dos pasos). Ruff/MyPy/`check-api-contract.mjs`/build
limpios.

Verificación manual en navegador real, contra un proyecto greenfield
real corrido hasta completar y luego forzado a fallar (edición directa
de fila SQLite, no un fixture): las 4 acciones del Repair Center se
ejercieron con datos reales y llamadas HTTP reales, no mockeadas --
retry devolvió el item a `READY` y lo sacó de la lista; recover
re-evaluó el artefacto propio ya existente del item y lo completó;
escalar generó una `ApprovalRequest` real cuyo evento `task_escalated`
llevaba `metadata.approval_id` apuntando exactamente a esa aprobación
(el vínculo estructural de P4.3a, confirmado en producción, no sólo en
test); enviar candidato con título/resumen/un archivo completo generó
el `POST` real y completó el item. El selector de proyecto/causa
filtró sobre datos reales sin llamadas de red adicionales, y el estado
vacío ("Nada que reparar") renderizó correctamente una vez las 3 filas
quedaron resueltas.

## Limitaciones

Recover sólo ofrece los artefactos propios del item, nunca los de una
dependencia aprobada (que el backend sí acepta) -- calcular esa
elegibilidad en cliente exige recorrer `reviews`/`dependency_ids`, fuera
de alcance de esta pasada. Sin badge de conteo en vivo en el nav
(decisión ya tomada en P4.3a, mantenida). Rework nunca se ofrece desde
una fila del Repair Center -- estructuralmente imposible, porque
`rework_work_item` sólo acepta un item `COMPLETED` y `repair_center()`
nunca devuelve uno (no tiene causa que reparar); su confirmación real
vive enteramente en `TaskDrawer`. Hereda las limitaciones ya declaradas
en ADR 0034 (autoridad de ejecución por perfil de validación) y ADR
0036 (binding local/loopback) sin cambios.

## Qué queda para P4.4

P4.3 cierra el par "importar/exportar sin arriesgar" (P4.1/P4.2) con
"revisar/reparar" (P4.3). Queda P4.4 -- entrega y auditoría: informe
final de qué se pidió/cambió/validó/quedó sin verificar, historial de
intentos y modelos cuando aporte a diagnóstico. P4.5 (onboarding
Windows) sigue abierto detrás.
