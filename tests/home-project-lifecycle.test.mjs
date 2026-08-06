// P3.1b -- entregable 1 de PLANS.md P3.1: "crear proyecto, ejecutar,
// pausar/reanudar" y sus errores principales. Interacción real
// (@testing-library/user-event) contra un `Home` real, con la API
// mockeada por tests/support/fetch-mock.mjs (match exacto, falla
// ruidosa ante una ruta no registrada -- ver ese archivo).

import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { cleanup, screen, userEvent, within } from "./support/dom-setup.mjs";
import { createFetchMock } from "./support/fetch-mock.mjs";
import { buildDashboard, buildProject, buildProjectDetail } from "./support/fixtures.mjs";
import { mockBackgroundRefresh, renderHome } from "./support/render-home.mjs";

afterEach(() => {
  cleanup();
});

test("creating a project: happy path switches to the project view", async () => {
  const mock = createFetchMock();
  globalThis.fetch = mock.fetch;
  mockBackgroundRefresh(mock);
  await renderHome();

  const created = buildProjectDetail({
    project: buildProject({ id: "new-project", title: "ARPG de prueba", status: "ready" }),
  });
  mock.on("POST", "/api/projects", (call) => {
    assert.equal(call.body.auto_plan, true);
    assert.equal(call.body.goal, "Construir un prototipo de ARPG");
    return created;
  });

  const textarea = screen.getByLabelText(/Objetivo del nuevo proyecto/i);
  await userEvent.type(textarea, "Construir un prototipo de ARPG");
  await userEvent.click(screen.getByRole("button", { name: /Crear proyecto/i }));

  await screen.findByRole("heading", { name: "ARPG de prueba" });
  mock.assertAllMatched();
});

test("creating a project: backend HTTP error surfaces its `detail` message", async () => {
  const mock = createFetchMock();
  globalThis.fetch = mock.fetch;
  mockBackgroundRefresh(mock);
  await renderHome();

  mock.on("POST", "/api/projects", { status: 409, body: { detail: "El objetivo es demasiado ambiguo." } });

  const textarea = screen.getByLabelText(/Objetivo del nuevo proyecto/i);
  await userEvent.type(textarea, "algo");
  await userEvent.click(screen.getByRole("button", { name: /Crear proyecto/i }));

  const alert = await screen.findByRole("alert");
  assert.match(within(alert).getByText(/demasiado ambiguo/i).textContent, /demasiado ambiguo/i);
  mock.assertAllMatched();
});

test("creating a project: a raw network failure surfaces a generic error, not a crash", async () => {
  const mock = createFetchMock();
  globalThis.fetch = mock.fetch;
  mockBackgroundRefresh(mock);
  await renderHome();

  mock.on("POST", "/api/projects", () => {
    throw new TypeError("Failed to fetch");
  });

  const textarea = screen.getByLabelText(/Objetivo del nuevo proyecto/i);
  await userEvent.type(textarea, "algo");
  await userEvent.click(screen.getByRole("button", { name: /Crear proyecto/i }));

  const alert = await screen.findByRole("alert");
  assert.match(alert.textContent, /Failed to fetch/i);
  mock.assertAllMatched();
});

test("opening a project from the dashboard list renders its detail", async () => {
  const mock = createFetchMock();
  globalThis.fetch = mock.fetch;
  const project = buildProject({ id: "existing-project", title: "Motor de facturación" });
  mockBackgroundRefresh(mock, buildDashboard({ projects: [project] }));
  await renderHome();

  const detail = buildProjectDetail({ project });
  mock.on("GET", "/api/projects/existing-project", detail);
  mock.on("GET", "/api/projects/existing-project/events", []);

  await userEvent.click(screen.getByRole("button", { name: /Motor de facturación/i }));

  await screen.findByRole("heading", { name: "Motor de facturación" });
  mock.assertAllMatched();
});

test("project controls: run/pause/resume/cancel call the matching endpoint and refresh", async () => {
  const mock = createFetchMock();
  globalThis.fetch = mock.fetch;
  mockBackgroundRefresh(mock);
  await renderHome();

  const readyProject = buildProject({ id: "controlled-project", title: "Proyecto controlado", status: "ready" });
  mock.on("POST", "/api/projects", buildProjectDetail({ project: readyProject }));
  const textarea = screen.getByLabelText(/Objetivo del nuevo proyecto/i);
  await userEvent.type(textarea, "Meta de prueba");
  await userEvent.click(screen.getByRole("button", { name: /Crear proyecto/i }));
  await screen.findByRole("heading", { name: "Proyecto controlado" });

  // "ready" -> shows "Ejecutar". After clicking it, controlProject("run")
  // POSTs /run then re-fetches the project (openProject) and the
  // dashboard (refreshDashboard) -- every one of those calls must be
  // registered, or fetch-mock's strict matcher fails the test.
  const runningProject = buildProject({ id: "controlled-project", title: "Proyecto controlado", status: "running" });
  mock.on("POST", "/api/projects/controlled-project/run", {});
  mock.on("GET", "/api/projects/controlled-project", buildProjectDetail({ project: runningProject }));
  mock.on("GET", "/api/projects/controlled-project/events", []);

  await userEvent.click(screen.getByRole("button", { name: /▶ Ejecutar/i }));
  await screen.findByRole("button", { name: /Ⅱ Pausar/i });

  mock.on("POST", "/api/projects/controlled-project/pause", {});
  const pausedProject = buildProject({ id: "controlled-project", title: "Proyecto controlado", status: "paused" });
  // Re-registering the same route replaces the earlier handler outright
  // (fetch-mock routes are a plain Map keyed by method+path) -- this only
  // needs to answer the *next* call, made by openProject() after the
  // pause POST resolves, so a plain value is enough; no counter needed.
  mock.on("GET", "/api/projects/controlled-project", buildProjectDetail({ project: pausedProject }));

  await userEvent.click(screen.getByRole("button", { name: /Ⅱ Pausar/i }));
  await screen.findByRole("button", { name: /▶ Reanudar/i });

  mock.assertAllMatched();
});

test("project controls on the demo project short-circuit with an error and never call fetch", async () => {
  const mock = createFetchMock();
  globalThis.fetch = mock.fetch;
  mockBackgroundRefresh(mock);
  await renderHome();

  await userEvent.click(screen.getByRole("button", { name: /Proyecto activo/i }));
  // SAMPLE_PROJECT.status is "running", so the demo view shows "Pausar".
  await userEvent.click(screen.getByRole("button", { name: /Ⅱ Pausar/i }));

  const alert = await screen.findByRole("alert");
  assert.match(alert.textContent, /Inicia la API para ejecutar controles reales/i);
  mock.assertAllMatched();
});
