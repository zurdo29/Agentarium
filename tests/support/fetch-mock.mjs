// Strict, exact-match fetch mock. Deliberately NOT a suffix/substring
// matcher: `request<T>` in app/page.tsx always calls
// `fetch(`${API}${path}`, ...)` against one fixed base
// (tests/support/dom-setup.mjs's API_SENTINEL), so every real call is
// fully specified -- a mock that matches loosely would let a route table
// gap through silently, or worse, let an unrelated test route match by
// accident.

function parseKey(method, path) {
  // `path` may itself carry a query string (e.g.
  // "/projects/abc/events/stream?after_sequence=5"); resolved against a
  // throwaway base purely to get `pathname`/`search` parsed apart, the
  // base itself is never compared.
  const url = new URL(path, "http://route-table.invalid/");
  return `${method.toUpperCase()} ${url.pathname}${url.search}`;
}

export function createFetchMock() {
  const routes = new Map();
  const calls = [];
  const unmatched = [];

  function on(method, path, respond) {
    routes.set(parseKey(method, path), respond);
  }

  async function fetchMock(input, init) {
    const url = new URL(String(input));
    const method = (init?.method ?? "GET").toUpperCase();
    const record = {
      url: url.toString(),
      pathname: url.pathname,
      search: url.search,
      method,
      body: init?.body ? JSON.parse(String(init.body)) : undefined,
    };
    calls.push(record);

    const key = `${method} ${url.pathname}${url.search}`;
    if (!routes.has(key)) {
      unmatched.push(record);
      throw new Error(
        `fetch-mock: no route registered for ${key} ` +
          `(full URL: ${url.toString()}). Register it with mock.on("${method}", ` +
          `"${url.pathname}${url.search}", ...) before this call can happen -- ` +
          "a route-table gap must fail the test, not fall back to a default.",
      );
    }

    const respond = routes.get(key);
    // A handler that throws (or returns a rejected promise) simulates
    // `fetch` itself failing (DNS/connection-level), as distinct from a
    // resolved-but-non-ok HTTP response below.
    const result = typeof respond === "function" ? await respond(record) : respond;
    const status = result && typeof result === "object" && "status" in result ? result.status : 200;
    const body = result && typeof result === "object" && "status" in result ? result.body : result;
    return {
      ok: status >= 200 && status < 300,
      status,
      json: async () => body,
    };
  }

  function assertAllMatched() {
    if (unmatched.length > 0) {
      const summary = unmatched
        .map((record) => `${record.method} ${record.pathname}${record.search}`)
        .join(", ");
      throw new Error(`fetch-mock: unregistered request(s) were made and not asserted on: ${summary}`);
    }
  }

  return { fetch: fetchMock, on, calls, unmatched, assertAllMatched };
}
