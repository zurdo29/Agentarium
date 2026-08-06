// Test-only DOM environment for driving app/page.tsx's `Home` component
// under node:test, without any of the app's real Next/vinext/Cloudflare
// Workers build pipeline. Nothing here is imported by, or changes,
// production code -- see docs/decisions/ for why (P3.1b plan).

import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { JSDOM } from "jsdom";
import ts from "typescript";

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

// Transpiles app/page.tsx with the same `typescript` package already
// used by scripts/check-api-contract.mjs (P3.1a) -- no new dependency
// for this step. Writes the plain-JS output to a unique file and imports
// it from there, rather than a --experimental-loader hook: a loader
// intercepts module resolution for the whole process (react, jsdom,
// typescript, node:test itself), while a one-off transpile only touches
// this one file. Memoized: page.tsx has no per-import side effects worth
// re-running, and every test file wants the same compiled component.
//
// The temp file lives under node_modules/.cache/, not os.tmpdir():
// found by actually running this that Node's ESM resolver walks *up*
// from the importing file looking for node_modules, and a real
// os.tmpdir() path (e.g. C:\Users\...\AppData\Local\Temp\...) has no
// node_modules anywhere above it, so the transpiled file's own
// `import "react"` failed with ERR_MODULE_NOT_FOUND. node_modules/ is
// already fully gitignored (`/node_modules` in .gitignore), so
// node_modules/.cache/ needs no new ignore rule -- same convention other
// tools (babel, eslint, ...) already use for exactly this kind of
// scratch output.
export async function loadHomeComponent() {
  homeComponentPromise ??= (async () => {
    const projectRoot = new URL("../../", import.meta.url);
    const sourcePath = new URL("app/page.tsx", projectRoot);
    const sourceText = await readFile(sourcePath, "utf8");

    const { outputText, diagnostics } = ts.transpileModule(sourceText, {
      fileName: "page.tsx",
      compilerOptions: {
        jsx: ts.JsxEmit.ReactJSX,
        module: ts.ModuleKind.ESNext,
        target: ts.ScriptTarget.ES2022,
        esModuleInterop: true,
      },
      reportDiagnostics: true,
    });
    if (diagnostics && diagnostics.length > 0) {
      const formatted = ts.formatDiagnosticsWithColorAndContext(diagnostics, {
        getCanonicalFileName: (fileName) => fileName,
        getCurrentDirectory: () => process.cwd(),
        getNewLine: () => "\n",
      });
      throw new Error(`Failed to transpile app/page.tsx for tests:\n${formatted}`);
    }

    const cacheRoot = join(fileURLToPath(projectRoot), "node_modules", ".cache", "agentarium-tests");
    await mkdir(cacheRoot, { recursive: true });
    const tempDir = await mkdtemp(join(cacheRoot, "page-"));
    const tempPath = join(tempDir, `home-${process.pid}-${Date.now()}.mjs`);
    await writeFile(tempPath, outputText, "utf8");
    try {
      const moduleExports = await import(pathToFileURL(tempPath).href);
      return moduleExports.default;
    } finally {
      await rm(tempDir, { recursive: true, force: true });
    }
  })();
  return homeComponentPromise;
}

export const API_BASE = API_SENTINEL;
