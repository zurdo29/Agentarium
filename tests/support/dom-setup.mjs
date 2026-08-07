// Test-only DOM environment for driving app/page.tsx's `Home` component
// under node:test, without any of the app's real Next/vinext/Cloudflare
// Workers build pipeline. Nothing here is imported by, or changes,
// production code -- see docs/decisions/ for why (P3.1b plan).

import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { JSDOM } from "jsdom";
import { createServer } from "vite";

// Fixed on purpose, before anything imports page.tsx: `API` in page.tsx
// reads `process.env.NEXT_PUBLIC_AGENTARIUM_API_URL` at module-eval time.
// If this were left to whatever a contributor's shell happens to have
// set, fetch-mock's route matching would silently stop matching.
const API_SENTINEL = "http://agentarium.test/api";
process.env.NEXT_PUBLIC_AGENTARIUM_API_URL = API_SENTINEL;

// A stub, not a real EventSource -- neither Node nor jsdom provide one
// (verified against jsdom's own source tree and this Node's own globals
// while planning this).
//
// Note for whoever writes the first test that drives this: app/page.tsx's
// SSE effect has `events` in its dependency array, so every incoming
// message tears down and reconstructs the EventSource with a recomputed
// `after_sequence`, rather than reusing one connection. Not a bug --
// the dedup guard plus the recomputed `after_sequence` mean no message
// is lost or double-applied -- but it does mean a test simulating two
// sequential messages must call `StubEventSource.latest()` again between
// them rather than holding onto the first instance.
class StubEventSource {
  static instances = [];

  constructor(url) {
    this.url = url;
    this.onmessage = null;
    this.onerror = null;
    this.closed = false;
    StubEventSource.instances.push(this);
  }

  close() {
    this.closed = true;
  }

  static latest() {
    return StubEventSource.instances.at(-1) ?? null;
  }

  static reset() {
    StubEventSource.instances = [];
  }
}

// Deliberately NOT `Object.defineProperties(global,
// Object.getOwnPropertyDescriptors(dom.window))` -- copying every jsdom
// window property onto the Node process's global would shadow globals
// Node already defines natively (fetch, URL, Event, ...) for the entire
// process, not just for the component under test. Instead: an explicit,
// minimal list, each entry justified by a concrete error seen while
// building these tests, not guessed upfront.
//
// - window/document: Home() itself uses `window.setTimeout`/
//   `window.clearTimeout`; @testing-library/react's render() attaches to
//   `document.body`.
// - navigator: @testing-library/user-event reads `navigator.userAgent`
//   to decide platform-specific event sequencing.
// - requestAnimationFrame/cancelAnimationFrame: React's scheduler uses
//   these when they exist; jsdom only exposes them with
//   `pretendToBeVisual: true`.
// - EventSource: the stub above.
//
// Installed ONCE, at module load, unconditionally (no restore-to-undefined
// between tests): found by actually running this that
// @testing-library/dom's `screen` singleton (screen.js:48) computes
// `typeof document !== 'undefined' && document.body ? ... : <broken
// fallback>` exactly once, when @testing-library/dom is first imported --
// not lazily on each query. Since ESM static imports are always resolved
// before any module's own top-level code runs, there is no way, within a
// single file, to "install globals, then import @testing-library/react"
// with a static import; @testing-library/react must be dynamically
// imported *after* this runs. Every test file gets `render`/`screen`/
// `cleanup` re-exported from here rather than importing
// @testing-library/react directly, so this module's one-time,
// first-evaluated setup always wins the race.
const dom = new JSDOM("<!doctype html><html><body></body></html>", {
  url: "http://localhost/",
  pretendToBeVisual: true,
});

for (const [key, value] of Object.entries({
  window: dom.window,
  document: dom.window.document,
  navigator: dom.window.navigator,
  requestAnimationFrame: dom.window.requestAnimationFrame?.bind(dom.window),
  cancelAnimationFrame: dom.window.cancelAnimationFrame?.bind(dom.window),
  EventSource: StubEventSource,
})) {
  // Node defines some of these (e.g. `navigator`, since Node 21) as
  // getter-only accessor properties on globalThis -- a plain
  // `globalThis[key] = value` throws on those ("Cannot set property ...
  // which has only a getter"), found by actually running this.
  Object.defineProperty(globalThis, key, {
    value,
    configurable: true,
    writable: true,
    enumerable: true,
  });
}

const { render, screen, cleanup, fireEvent, waitFor, within } = await import(
  "@testing-library/react"
);
const userEventModule = await import("@testing-library/user-event");
const userEvent = userEventModule.default;

export { render, screen, cleanup, fireEvent, waitFor, within, userEvent, StubEventSource };

export function resetEventSource() {
  StubEventSource.reset();
}

let homeComponentPromise = null;

// A real Vite dev server in middleware mode, used only for its module
// loader -- nothing here ever listens on a port (`ws: false` on top of
// `hmr: false`: found by actually running this that `hmr: false` alone
// still tried to open a WebSocket server, which broke down to a port
// conflict warning when multiple `node --test` child processes -- one per
// test file -- each started their own server; `ws: false` is the option
// that actually suppresses it). `ssrLoadModule` resolves page.tsx's whole
// relative-import graph itself (P3.3's task-drawer.tsx, and whatever P4
// adds later) the same way Vite would for a real request, so this file
// never needs to know the shape of that graph. Deliberately
// `configFile: false`: the project's real vite.config.ts pulls in
// vinext/@cloudflare/vite-plugin and Workers-deployment bindings (D1/R2)
// that a test loading one React component for jsdom has no use for and
// no reason to risk breaking on -- see docs/decisions/ (P3.3 plan) for
// why that config was ruled out rather than reused.
//
// Memoized like the loaded component below: page.tsx has no per-import
// side effects worth re-running, and every test in a file wants the same
// compiled component. The server is closed as soon as it has produced
// that one module -- found by actually running this that leaving it open
// (even with `ws: false`) keeps a handle alive that stops `node --test`
// from exiting once all tests finish; nothing about the already-evaluated
// component depends on the server staying up afterwards.
export async function loadHomeComponent() {
  homeComponentPromise ??= (async () => {
    const server = await createServer({
      configFile: false,
      root: fileURLToPath(new URL("../../", import.meta.url)),
      plugins: [react()],
      server: { middlewareMode: true, hmr: false, ws: false },
      appType: "custom",
      logLevel: "warn",
      // Dependency pre-bundling exists to serve a browser efficiently; an
      // SSR module load in Node mostly doesn't need it. `noDiscovery`
      // skips scanning the whole source tree for importable deps (the
      // expensive part on a project this size), telling Vite exactly what
      // page.tsx's graph actually imports from node_modules instead.
      optimizeDeps: { noDiscovery: true, include: ["react", "react-dom"] },
    });
    try {
      const moduleExports = await server.ssrLoadModule("/app/page.tsx");
      return moduleExports.default;
    } finally {
      await server.close();
    }
  })();
  return homeComponentPromise;
}

export const API_BASE = API_SENTINEL;
