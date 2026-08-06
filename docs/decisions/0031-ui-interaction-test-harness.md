# ADR 0031: Tests de interacción UI↔API mock (P3.1b)

- Estado: aceptada
- Fecha: 2026-08-06

## Contexto

`app/page.tsx` (2207 líneas, componente cliente único `Home()`) no tenía
ninguna prueba de interacción: el único test previo
(`tests/rendered-html.test.mjs`) importa el worker ya compilado y
compara el HTML de SSR — no puede ver nada de lo que pasa después de la
hidratación, que es donde vive toda la interacción real (`Home()` hace
todo su fetching de datos client-side, vía `useEffect`).

Cero herramientas de testing de DOM/componentes existían en el proyecto
(sin `jsdom`, `@testing-library/*`, `vitest` ni `jest`; `@playwright/test`
sólo aparecía como peerDependency opcional de `next`, nunca instalado).
`Home()` importa únicamente React (`"use client"` + hooks) — cero
acoplamiento a Next/vinext/RSC — así que es renderizable de forma
aislada sin correr la pipeline real de build (vinext + Cloudflare
Workers + Wrangler/Miniflare).

Restricciones explícitas del usuario: sin refactor de `page.tsx`, sin
cambios visuales ni funcionales, sin ampliar P3.1a. Si un test descubre
un bug real, se demuestra en un test, no se arregla en este PR.

## Decisión

Se agregan exactamente 3 devDependencies: `jsdom@29.1.1` (fijado, no
`30.x`: esa versión angosta el rango de `engines.node` fuera del piso
que este proyecto ya declara soportar; sin `canvas`, peer opcional que
`page.tsx` no necesita), `@testing-library/react@16.3.2` (primera major
con soporte oficial de React 19), `@testing-library/user-event@14.6.3`.
Se mantiene `node:test`/`node:assert` como runner.

`app/page.tsx` se transpila en cada corrida con `ts.transpileModule`
(mismo paquete `typescript` que ya usa `scripts/check-api-contract.mjs`
de P3.1a, cero dependencia nueva para este paso), se escribe a un
archivo único y se importa con `import(pathToFileURL(...))`. Se
descartó un loader/hook de ESM (`--experimental-loader`): sigue siendo
experimental e intercepta toda resolución de módulos del proceso, no
sólo de este archivo.

`tests/support/dom-setup.mjs`, `fetch-mock.mjs`, `fixtures.mjs`,
`render-home.mjs` (helper compartido, factorizado tras aparecer
duplicado con un bug en una de las dos copias) y tres archivos de test
(`home-project-lifecycle`, `home-work-items`,
`home-approvals-and-connectivity`) — 16 tests. `package.json` cambia una
línea (`node --test "tests/**/*.test.mjs"` en vez de nombrar sólo el
archivo viejo); ningún archivo de producción se toca.

### Correcciones del usuario incorporadas antes de implementar

- `fetch-mock` matchea exacto (origin+pathname+method, y `search` cuando
  el test lo declara), nunca por sufijo. Una ruta sin registrar lanza un
  error nombrando el método+path exacto -- no hay default silencioso.
  Verificado a mano (`mock.fetch('.../unregistered-route')` revienta con
  mensaje explícito) y orgánicamente: cada vez que un test tenía una ruta
  mal escrita durante el desarrollo, el mock lo señaló de inmediato en
  vez de dejar pasar un estado inconsistente.
- `node --test "tests/**/*.test.mjs"` explícito, no el descubrimiento
  por convención de directorio de Node.
- `dom-setup.mjs` instala una lista mínima y explícita de globals
  (`window`, `document`, `navigator`, `requestAnimationFrame`/
  `cancelAnimationFrame`, el stub de `EventSource`), nunca
  `Object.defineProperties(global, Object.getOwnPropertyDescriptors(dom.window))`.

## Tres problemas reales encontrados corriendo el mecanismo, no diseñados de antemano

1. **`navigator` es una propiedad de sólo getter en Node ≥21.**
   `globalThis.navigator = value` lanza `TypeError: Cannot set property
   navigator which has only a getter`. Arreglado redefiniendo la
   propiedad vía `Object.defineProperty` en vez de asignación directa.
2. **El singleton `screen` de `@testing-library/dom` se resuelve una
   sola vez, al importarse el módulo** (`screen.js:48`:
   `typeof document !== 'undefined' && document.body ? ... :
   <fallback roto>`), no de forma perezosa en cada query. Como los
   imports estáticos de ES modules siempre se resuelven antes de que
   corra cualquier código propio del archivo, no hay forma de "instalar
   los globals y después importar `@testing-library/react`" dentro de un
   mismo archivo con imports estáticos. Resuelto instalando los globals
   de forma síncrona en el nivel superior de `dom-setup.mjs`, seguido de
   un `await import("@testing-library/react")` dinámico -- todo archivo
   de test importa `render`/`screen`/`cleanup` re-exportados desde ahí,
   nunca directo de `@testing-library/react`, para que ese único
   `import` dinámico gane siempre la carrera.
3. **El archivo transpilado no puede vivir en `os.tmpdir()`.** El
   resolutor de ESM de Node camina *hacia arriba* buscando
   `node_modules` desde el archivo que se importa; una ruta bajo
   `%TEMP%` no tiene ningún `node_modules` en su ascendencia, así que el
   `import "react"` del propio `page.tsx` transpilado fallaba con
   `ERR_MODULE_NOT_FOUND`. Resuelto escribiendo el archivo bajo
   `node_modules/.cache/agentarium-tests/` -- ya completamente
   ignorado por git (`/node_modules` en `.gitignore`), sin necesitar una
   regla nueva, mismo patrón que otras herramientas (babel, eslint) ya
   usan para este tipo de scratch output.

Ninguno de los tres es un bug de `page.tsx` -- son propiedades del
arnés de test mismo, arregladas ahí.

## Alcance explícito

Cubierto: crear proyecto (éxito, error HTTP con `detail`, falla de red
cruda), abrir un proyecto, controles run/pause/resume/cancel (incluido
el atajo de `DEMO_ID`, que nunca llama `fetch`), retry/rework/escalate
de una tarea, aprobaciones (aprobar/rechazar con y sin comentario,
badge de pendientes), conectividad (estado offline cuando el refresh de
fondo falla), y los campos de sólo lectura que P4.3 (centro de
reparación)/P4.4 (entrega y auditoría) van a necesitar: `last_error`,
veredicto de revisión, evidencia de test report derivada de
`command_evidence`, decisiones, artifacts, panel de métricas.

Fuera de este PR, marcado explícitamente en el plan, no descartado en
silencio: selección de proveedor (`ProviderConsole`) y prioridad de
tareas -- código real que funciona pero no atado a nada que P4 necesite
todavía.

## Un hallazgo, no un bug

El `useEffect` del stream SSE tiene `events` en su arreglo de
dependencias, así que cada mensaje entrante reconstruye el
`EventSource` con un `after_sequence` recalculado en vez de reusar la
conexión. No pierde ni duplica mensajes (el guard de dedup + el
`after_sequence` recalculado lo cubren), así que no calificó como bug
real bajo la regla nueva de este PR -- no se tocó `page.tsx`.

## Consecuencias

- Las fixtures de `tests/support/fixtures.mjs` son duplicados a mano de
  los tipos TS de `page.tsx` (no exportados, y erased en runtime de
  todos modos). Pueden desalinearse en silencio con el tiempo -- el gate
  de P3.1a (ADR 0030) cubre backend↔TypeScript, no
  TypeScript↔fixture-de-test; cerrar esa brecha queda fuera de este PR.
- El mecanismo de transpile-a-archivo-temporal asume que `page.tsx` no
  tiene imports relativos propios (hoy sólo importa `"react"`). Si una
  edición futura le agrega uno, este mecanismo necesita revisarse --
  comentario dejado en `dom-setup.mjs` marcando ese supuesto.
