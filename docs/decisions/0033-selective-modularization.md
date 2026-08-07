# ADR 0033: Modularización selectiva de `engine.py` y `app/page.tsx` (P3.3)

- Estado: aceptada
- Fecha: 2026-08-07

## Contexto

PLANS.md P3.3 pedía identificar las mínimas fronteras que
`backend/agentarium/orchestration/engine.py` y `app/page.tsx` necesitan
extraer para que P4 (importar repos, exportar cambios, centro de
reparación, entrega/auditoría) no siga acumulando responsabilidades ahí —
explícitamente no un refactor general ni una meta de líneas.

## Decisión

### Backend: ninguna extracción hasta que un requisito concreto lo justifique

Sólo 4 métodos del `Orchestrator` se llaman desde fuera de `engine.py`,
todos desde `ApplicationService`: `plan_project`, `run_project`,
`re_evaluate_artifact`, `evaluate_operator_candidate`. Cruzando cada
sub-fase de P4 contra el código real: P4.1/P4.2/P4.4 no tocan el
orchestrator. P4.3 ("centro de reparación") involucra al orchestrator
indirectamente (`ApplicationService.recover_artifact`/`submit_candidate`
llaman a `re_evaluate_artifact`/`evaluate_operator_candidate`), pero **ya
a través de una frontera limpia y suficiente**: `ApplicationService`. Que
esos dos métodos sean la implementación real detrás de "recuperar
artefacto"/"enviar candidato" no implica que haga falta reorganizarlos —
construir el centro de reparación es trabajo de agregación de lectura +
UI sobre la frontera que ya existe.

**Resultado documentado, no un refactor pospuesto:** si en el futuro P4.3
necesita tocar el orchestrator de verdad (por ejemplo, para relajar la
regla de procedencia en `_recovery_work_proposal`, que hoy exige que el
artefacto recuperado pertenezca a la misma tarea o a una dependencia
aprobada), esa es la señal concreta para reabrir esto — no antes.

### Frontend: `TaskDrawer` como frontera, grafo acíclico vía `shared.tsx`

Único componente con atadura escrita, no inferida: el comentario de
cabecera de `tests/home-work-items.test.mjs` (P3.1b) ya decía que esos
tests existen porque P4.3/P4.4 van a leer exactamente los campos que
`TaskDrawer` expone.

`TaskDrawer` comparte 3 valores (`ROLE_LABELS`, `dateLabel`, `Status`) con
el resto de `page.tsx`, que también los usa. En vez de un ciclo runtime
real (`page.tsx` ↔ `task-drawer.tsx`), esos 3 valores se movieron a
`app/shared.tsx`, una hoja pura sin dependencias hacia ninguno de los
otros dos. Grafo resultante: `page.tsx → task-drawer.tsx`,
`page.tsx → shared.tsx`, `task-drawer.tsx → shared.tsx` — un DAG limpio.
Los tipos que `TaskDrawer` necesita se quedaron declarados en `page.tsx`,
importados vía `import type` (un borde que se borra en compilación, no
una dependencia runtime).

### Vite/`ssrLoadModule` como loader de test module-aware

`tests/support/dom-setup.mjs` transpilaba antes exactamente `app/page.tsx`
con `ts.transpileModule`, sin resolución de módulos — cualquier extracción
con imports relativos reales lo rompía, y arreglarlo caso por caso habría
exigido tocar ese archivo de nuevo por cada módulo nuevo que P4 agregue.
Reemplazado por un servidor Vite real en modo `middlewareMode`, usado sólo
por su `ssrLoadModule(url)`: resuelve todo el grafo de imports relativos
igual que lo haría para un request real, así que el harness no necesita
conocer la forma de ese grafo. `vite`/`@vitejs/plugin-react` ya eran
devDependencies reales, ninguna dependencia nueva.

Deliberadamente `configFile: false`: el `vite.config.ts` real del proyecto
carga `vinext()` + `@cloudflare/vite-plugin` con bindings D1/R2 de
despliegue a Cloudflare Workers, sin relación con renderizar un componente
React para jsdom. Se usa en cambio un config mínimo standalone.

**Tradeoff aceptado a propósito:** este mecanismo es más lento que el
transpile de un solo archivo que reemplaza. El punto de la corrección era
generalidad (que un import relativo nuevo funcione sin tocar el harness),
no velocidad, y el plan no fijó una meta de rendimiento.

## Consecuencias

- `tests/rendered-html.test.mjs` ahora también lee `app/task-drawer.tsx`
  para las aserciones de texto que viven ahí.
- `contract_registry.py`/`test_type_contract.py` no se tocaron —
  confirmado comparando la salida de `scripts/check-api-contract.mjs`
  antes y después de la extracción, idéntica.
- `ProjectView`, `Dashboard`, `ApprovalCenter` y el router-shell de `Home`
  quedan intactos — no hay una segunda extracción de frontend en este PR.
- Disparador de revisión: si un futuro P4.3 necesita tocar
  `_recovery_work_proposal` u otro método del orchestrator directamente
  (no sólo vía `ApplicationService`), reabrir la pregunta de extracción
  de backend en ese momento, con el requisito concreto en mano.
