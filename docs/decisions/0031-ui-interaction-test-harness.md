# ADR 0031: Stack de tests de interacción UI↔API mock (P3.1b)

- Estado: aceptada
- Fecha: 2026-08-06

## Contexto

`app/page.tsx` (componente cliente único `Home()`) no tenía ninguna
prueba de interacción: el único test previo (`tests/rendered-html.test.mjs`)
compara HTML de SSR del worker ya compilado — no puede ver nada de lo
que pasa después de la hidratación, que es donde vive toda la
interacción real (`Home()` hace todo su fetching de datos client-side,
vía `useEffect`). Cero herramientas de testing de DOM/componentes
existían en el proyecto. `Home()` importa únicamente React — cero
acoplamiento a Next/vinext/RSC — así que es renderizable de forma
aislada sin correr la pipeline real de build.

## Decisión

**Stack adoptado: `node:test` + `jsdom` + `@testing-library/react` +
`@testing-library/user-event`.** No se suma un test runner nuevo
(vitest/jest): `node:test`/`node:assert` ya es el patrón establecido por
`tests/rendered-html.test.mjs`. No se usa Playwright/un navegador real:
`Home()` no tiene acoplamiento a Next/vinext/RSC que lo exija, así que
jsdom alcanza y evita el costo de instalar/orquestar un navegador para
esto. `@testing-library/react` es la librería estándar para ejercer un
componente React como lo haría un usuario (roles, texto, eventos) sin
depender de su estructura interna.

**`app/page.tsx` se transpila en cada corrida con `ts.transpileModule`**
(mismo paquete `typescript` que ya usa `scripts/check-api-contract.mjs`
de P3.1a) **a un archivo temporal que se importa dinámicamente**, en vez
de un loader/hook de ESM (`--experimental-loader`). Un loader sigue
siendo experimental e intercepta toda resolución de módulos del
proceso completo (react, jsdom, typescript, node:test mismo); un
transpile puntual sólo toca este archivo y no cambia cómo se invoca
`node --test`. Detalle de implementación (dónde vive el archivo
temporal y por qué) documentado como comentario junto al código en
`tests/support/dom-setup.mjs`, no acá.

**El mock de `fetch` (`tests/support/fetch-mock.mjs`) matchea exacto
—origin, pathname, método y `search` cuando el test lo declara—, nunca
por sufijo, y una ruta sin registrar hace fallar el test en vez de
devolver un default silencioso.** Ésta es una regla que futuros tests
deben seguir, no un detalle de implementación: un mock permisivo dejaría
pasar en silencio un gap real de cobertura o un match accidental con la
ruta de otro test.

**Los globals de jsdom que `tests/support/dom-setup.mjs` instala sobre
`globalThis` son una lista explícita y mínima** (`window`, `document`,
`navigator`, `requestAnimationFrame`/`cancelAnimationFrame`, un stub de
`EventSource`), nunca una copia indiscriminada de todo `window`
(`Object.defineProperties(global, Object.getOwnPropertyDescriptors(dom.window))`).
Copiar todo pisaría, para el proceso entero, globals que Node ya define
nativamente (`fetch`, `URL`, `Event`, ...). Extender esta lista en el
futuro debe seguir el mismo criterio: un global nuevo entra sólo cuando
algo que se está probando de verdad lo necesita.

Sin `response_model=`, sin tests de interacción UI *ampliando* P3.1a,
sin refactor ni cambios visuales/funcionales en `app/page.tsx` —
restricciones del usuario para este PR, no de este ADR.

## Consecuencias

- Las fixtures de `tests/support/fixtures.mjs` son duplicados a mano de
  los tipos TS de `page.tsx` (no exportados, y erased en runtime de
  todos modos). Pueden desalinearse en silencio con el tiempo — el gate
  de P3.1a (ADR 0030) cubre backend↔TypeScript, no
  TypeScript↔fixture-de-test; cerrar esa brecha queda fuera de este PR.
- El mecanismo de transpile-a-archivo-temporal asume que `page.tsx` no
  tiene imports relativos propios (hoy sólo importa `"react"`). Si una
  edición futura le agrega uno, el mecanismo necesita revisarse —
  comentario dejado en `dom-setup.mjs` marcando ese supuesto.
