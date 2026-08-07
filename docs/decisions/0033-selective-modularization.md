# ADR 0033: Modularización selectiva de `engine.py` y `app/page.tsx` (P3.3)

- Estado: aceptada
- Fecha: 2026-08-07

## Contexto

PLANS.md P3.3 pedía identificar las mínimas fronteras que
`backend/agentarium/orchestration/engine.py` (2514 líneas) y
`app/page.tsx` (2207 líneas) necesitan extraer para que P4 (importar
repos, exportar cambios, centro de reparación, entrega/auditoría) no siga
acumulando responsabilidades ahí, sin que fuera un refactor general ni una
meta de líneas. Investigado con 3 agentes de exploración en paralelo
(responsabilidades del orchestrator vs. P4; responsabilidades de
`page.tsx` vs. P4; cobertura real de los tests de P3.1) más un agente de
diseño, y una ronda de revisión directa del usuario que corrigió el
resultado antes de implementar — ver "Correcciones de revisión" abajo.

## Decisión

### Backend: ninguna extracción

Sólo 4 métodos del `Orchestrator` se llaman desde fuera de `engine.py`,
todos desde `ApplicationService`: `plan_project`, `run_project`,
`re_evaluate_artifact`, `evaluate_operator_candidate`. Cruzando cada
sub-fase de P4 contra el código real: P4.1/P4.2/P4.4 no tocan el
orchestrator (necesitan campos nuevos en `Project`, una capacidad nueva en
`isolation/git_worktree.py`, y agregación nueva sobre
`Repository.metrics()`/`ApplicationService.project_detail()`,
respectivamente — todo fuera de `engine.py`). P4.3 ("centro de
reparación") sí involucra al orchestrator indirectamente
(`ApplicationService.recover_artifact`/`submit_candidate` llaman a
`re_evaluate_artifact`/`evaluate_operator_candidate`), pero **ya a través
de una frontera limpia y suficiente**: `ApplicationService`. Que esos dos
métodos sean la implementación real detrás de "recuperar artefacto"/
"enviar candidato" no implica que haga falta reorganizarlos — construir
el centro de reparación es trabajo de agregación de lectura + UI sobre la
frontera que ya existe.

**Resultado documentado, no un refactor pospuesto:** si en el futuro P4.3
necesita tocar el orchestrator de verdad (por ejemplo, para relajar la
regla de procedencia en `_recovery_work_proposal`, que hoy exige que el
artefacto recuperado pertenezca a la misma tarea o a una dependencia
aprobada), esa es la señal concreta para reabrir esto — no antes.

### Frontend: extraer `TaskDrawer`

Único componente con atadura escrita, no inferida: el comentario de
cabecera de `tests/home-work-items.test.mjs` (P3.1b) ya decía que esos
tests existen porque P4.3/P4.4 van a leer exactamente los campos que
`TaskDrawer` expone. Se extrae como prueba de que la UI puede crecer de
forma modular, no como el único componente que algún día se mueva.

**Diseño acíclico:** `TaskDrawer` usaba 3 valores (`ROLE_LABELS`,
`dateLabel`, `Status`) también usados en otras partes de `page.tsx`, lo
que habría forzado un ciclo runtime real (`page.tsx` ↔ `task-drawer.tsx`)
si esos valores se hubieran quedado en `page.tsx`. Se creó
`app/shared.tsx` como hoja pura (sin dependencias hacia ninguno de los
otros dos) para esos 3 valores. Grafo resultante:
`page.tsx → task-drawer.tsx`, `page.tsx → shared.tsx`,
`task-drawer.tsx → shared.tsx` — un DAG limpio. Los 3 tipos que
`TaskDrawer` necesita (`WorkItem`, `ProjectDetail`, `EventRecord`) se
quedaron declarados en `page.tsx` con `export`, importados en
`task-drawer.tsx` vía `import type` — un borde que se borra en
compilación, no una dependencia runtime, y evita tocar el path
harcodeado de `contract_registry.py`/`test_type_contract.py` sin
necesidad (el ciclo lo causaban los valores, no los tipos).

### Mecanismo de test: Vite en vez de un transpiler de un solo archivo

`tests/support/dom-setup.mjs` transpilaba antes exactamente
`app/page.tsx` con `ts.transpileModule` (sin resolución de módulos) — una
extracción con imports relativos reales lo habría roto, y arreglarlo caso
por caso habría exigido tocar ese archivo de nuevo por cada módulo nuevo
que P4 agregue. Reemplazado por un servidor Vite real en modo
`middlewareMode`, usado sólo por su `ssrLoadModule(url)`: resuelve todo
el grafo de imports relativos igual que lo haría para un request real, así
que el harness no necesita conocer la forma de ese grafo — un import
relativo nuevo en `page.tsx` funciona sin tocar este archivo otra vez.
`vite`/`@vitejs/plugin-react` ya eran devDependencies reales, ninguna
dependencia nueva.

Deliberadamente `configFile: false`: el `vite.config.ts` real del
proyecto carga `vinext()` + `@cloudflare/vite-plugin` con bindings D1/R2
y un plugin `sites()` — maquinaria de despliegue a Cloudflare Workers sin
relación con renderizar un componente React para jsdom. Reusarlo tal cual
habría arriesgado arrastrar esa maquinaria a cada corrida de test; se usa
en cambio un config mínimo standalone, sólo `@vitejs/plugin-react`.

**Tres problemas reales encontrados corriendo el mecanismo, no
asumidos** (mismo patrón que ADR 0031: workarounds de implementación
puntuales documentados como comentario junto al código, no como bitácora
del ADR):

1. `hmr: false` solo no alcanza para evitar que Vite intente levantar un
   servidor WebSocket — con varios procesos hijos de `node --test` (uno
   por archivo de test) arrancando su propio servidor a la vez, esto
   colisionaba en el mismo puerto. `ws: false` es la opción que
   realmente lo suprime.
2. Sin cerrar el servidor explícitamente, algo queda vivo (aun con
   `ws: false`) y `node --test` no termina de salir tras acabar los
   tests. El servidor se cierra apenas produce el módulo — nada de lo ya
   evaluado depende de que siga vivo después.
3. El primer arranque (caché de `node_modules/.vite` vacía, como ocurre
   siempre en un clon nuevo o en CI) paga un costo real de
   pre-bundling de dependencias vía escaneo — de ~57s a ~13s corrigiendo
   con `optimizeDeps: { noDiscovery: true, include: ["react", "react-dom"] }`
   (evita el escaneo del árbol de fuentes, declara directamente lo único
   que hace falta pre-empaquetar). En corridas con caché tibia baja a
   ~8-10s. Sigue siendo más lento que el mecanismo de un solo archivo que
   reemplaza (sub-segundo) — aceptado a propósito: el punto de esta
   corrección era generalidad, no velocidad, y no había una meta de
   rendimiento en el plan.

## Consecuencias

- `tests/rendered-html.test.mjs` ahora también lee `app/task-drawer.tsx`
  para las 2 aserciones de texto que viven ahí (`perfiles registrados`,
  `worktree verificado`) — el resto de sus aserciones contra `page.tsx`
  no cambiaron.
- `contract_registry.py`/`test_type_contract.py` no se tocaron —
  confirmado corriendo `scripts/check-api-contract.mjs` contra el
  `page.tsx` de antes y de después de la extracción y comparando que la
  salida es idéntica.
- Los 16 tests de interacción UI de P3.1b y el test SSR de
  `rendered-html.test.mjs` no cambiaron ninguna aserción (sólo la que
  necesitaba apuntar a `task-drawer.tsx`).
- `ProjectView`, `Dashboard`, `ApprovalCenter` y el router-shell de `Home`
  quedan intactos — no hay una segunda extracción de frontend en este PR;
  ver PLANS.md para por qué (los paneles de `ProjectView` relevantes para
  P4.4 tienen una atadura más débil y una forma de vista todavía no
  definida).
- Disparador de revisión: si un futuro P4.3 necesita tocar
  `_recovery_work_proposal` u otro método del orchestrator directamente
  (no sólo vía `ApplicationService`), reabrir la pregunta de extracción
  de backend en ese momento, con el requisito concreto en mano.
