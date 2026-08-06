// P3.1b -- entregable 1 de PLANS.md P3.1 ("revisar/reintentar") y
// entregable 3 ("estados/campos que P4 vaya a consumir"). P4.3 (centro de
// reparación) es literalmente "ver tareas fallidas y causa estructurada
// ... exponer la evidencia relevante"; P4.4 (entrega y auditoría) es
// "historial de intentos/modelos". Los campos de sólo lectura cubiertos
// acá (last_error, veredicto de revisión, evidencia de test report,
// decisiones, artifacts, métricas) son exactamente lo que esas piezas
// futuras van a leer -- y sólo son alcanzables con datos armados a mano,
// no clickeando el camino feliz.

import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { cleanup, screen, userEvent, within } from "./support/dom-setup.mjs";
import { createFetchMock } from "./support/fetch-mock.mjs";
import {
  buildArtifact,
  buildCommandEvidence,
  buildDecision,
  buildProject,
  buildProjectDetail,
  buildReview,
  buildTestReport,
  buildWorkItem,
} from "./support/fixtures.mjs";
import { mockBackgroundRefresh, renderHome } from "./support/render-home.mjs";

afterEach(() => {
  cleanup();
});

const PROJECT_ID = "work-items-project";

async function openProjectWith(mock, detailOverrides) {
  mockBackgroundRefresh(mock);
  await renderHome();

  const project = buildProject({ id: PROJECT_ID, title: "Proyecto con tareas", status: "running" });
  const detail = buildProjectDetail({ project, ...detailOverrides });
  mock.on("POST", "/api/projects", detail);

  const textarea = screen.getByLabelText(/Objetivo del nuevo proyecto/i);
  await userEvent.type(textarea, "Meta de prueba");
  await userEvent.click(screen.getByRole("button", { name: /Crear proyecto/i }));
  await screen.findByRole("heading", { name: "Proyecto con tareas" });

  return detail;
}

test("retrying a failed work item calls /retry and refreshes the project", async () => {
  const mock = createFetchMock();
  globalThis.fetch = mock.fetch;
  const failedItem = buildWorkItem({
    id: "item-failed",
    title: "Tarea fallida",
    status: "failed",
    last_error: "El validador de sintaxis rechazó el archivo.",
  });
  const detail = await openProjectWith(mock, { work_items: [failedItem] });

  await userEvent.click(screen.getByRole("button", { name: /Tarea fallida/i }));
  await screen.findByRole("heading", { name: "Tarea fallida" });

  // The read-only evidence field P4.3 will read: last_error, shown only
  // when present.
  await screen.findByText("El validador de sintaxis rechazó el archivo.");

  const retriedItem = { ...failedItem, status: "running", last_error: null };
  mock.on("POST", "/api/work-items/item-failed/retry", {});
  mock.on(
    "GET",
    `/api/projects/${PROJECT_ID}`,
    buildProjectDetail({ project: detail.project, work_items: [retriedItem] }),
  );
  mock.on("GET", `/api/projects/${PROJECT_ID}/events`, []);

  await userEvent.click(screen.getByRole("button", { name: /Reintentar/i }));

  // The drawer re-renders against the refreshed item once openProject()
  // resolves; the retry button (only shown for failed/changes_requested)
  // disappears once status is "running".
  await screen.findByRole("heading", { name: "Tarea fallida" });
  assert.equal(screen.queryByRole("button", { name: /Reintentar/i }), null);
  mock.assertAllMatched();
});

test("reworking a completed work item calls /rework with a reason and refreshes", async () => {
  const mock = createFetchMock();
  globalThis.fetch = mock.fetch;
  const completedItem = buildWorkItem({ id: "item-done", title: "Tarea completada", status: "completed" });
  const detail = await openProjectWith(mock, { work_items: [completedItem] });

  await userEvent.click(screen.getByRole("button", { name: /Tarea completada/i }));
  await screen.findByRole("heading", { name: "Tarea completada" });

  mock.on("POST", "/api/work-items/item-done/rework", (call) => {
    assert.equal(typeof call.body.reason, "string");
    assert.ok(call.body.reason.length > 0);
    return {};
  });
  mock.on(
    "GET",
    `/api/projects/${PROJECT_ID}`,
    buildProjectDetail({ project: detail.project, work_items: [{ ...completedItem, status: "changes_requested" }] }),
  );
  mock.on(`GET`, `/api/projects/${PROJECT_ID}/events`, []);
  mockBackgroundRefresh(mock);

  await userEvent.click(screen.getByRole("button", { name: /Revisar de nuevo/i }));

  await screen.findByRole("button", { name: /Reintentar/i });
  mock.assertAllMatched();
});

test("escalating a work item switches to the approvals view", async () => {
  const mock = createFetchMock();
  globalThis.fetch = mock.fetch;
  const blockedItem = buildWorkItem({ id: "item-blocked", title: "Tarea bloqueada", status: "blocked" });
  await openProjectWith(mock, { work_items: [blockedItem] });

  await userEvent.click(screen.getByRole("button", { name: /Tarea bloqueada/i }));
  await screen.findByRole("heading", { name: "Tarea bloqueada" });

  mock.on("POST", "/api/work-items/item-blocked/escalate", (call) => {
    assert.ok(call.body.reason.length > 0);
    return {};
  });
  mockBackgroundRefresh(mock);

  await userEvent.click(screen.getByRole("button", { name: /Escalar/i }));

  await screen.findByRole("heading", { name: /Decisiones que no deben automatizarse/i });
  mock.assertAllMatched();
});

test("work item drawer surfaces tester and reviewer evidence P4.3/P4.4 will read", async () => {
  const mock = createFetchMock();
  globalThis.fetch = mock.fetch;
  const item = buildWorkItem({ id: "item-evidence", title: "Tarea con evidencia", status: "completed" });
  const testReport = buildTestReport({
    work_item_id: "item-evidence",
    passed: true,
    summary: "Todas las validaciones pasaron.",
    command_evidence: [
      buildCommandEvidence({ check: "validation_profile", profile: "workspace_inventory" }),
      buildCommandEvidence({ check: "validation_profile", profile: "python_syntax" }),
      buildCommandEvidence({ check: "isolated_change_set", backend: "git_worktree", verified: true }),
    ],
  });
  const review = buildReview({ work_item_id: "item-evidence", verdict: "changes_requested", reasons: ["Falta manejo de errores."] });
  const artifact = buildArtifact({ work_item_id: "item-evidence", title: "Artefacto principal" });
  await openProjectWith(mock, {
    work_items: [item],
    test_reports: [testReport],
    reviews: [review],
    artifacts: [artifact],
  });

  await userEvent.click(screen.getByRole("button", { name: /Tarea con evidencia/i }));
  await screen.findByRole("heading", { name: "Tarea con evidencia" });
  // The drawer is an overlay on top of ProjectView, which independently
  // shows its own (unrelated) artifact panel -- scope queries to the
  // drawer so this test only asserts on the drawer's own evidence, found
  // necessary when "Artefacto principal" matched twice on the page.
  const drawer = within(screen.getByRole("complementary", { name: /Detalle de Tarea con evidencia/i }));

  // Tester result: PASS + the command_evidence-derived summary text --
  // exact rendering logic lives in TaskDrawer (app/page.tsx), read before
  // writing this assertion, not guessed.
  await drawer.findByText("PASS");
  await drawer.findByText(/2 perfiles registrados/);
  await drawer.findByText(/worktree verificado/);

  // Reviewer result: not approved -> "REVIEW", plus the reasons text.
  await drawer.findByText("REVIEW");
  await drawer.findByText("Falta manejo de errores.");

  // Artifact evidence.
  await drawer.findByText("Artefacto principal");
});

test("work item drawer's tester evidence flags a missing worktree distinctly", async () => {
  const mock = createFetchMock();
  globalThis.fetch = mock.fetch;
  const item = buildWorkItem({ id: "item-no-worktree", title: "Tarea sin worktree", status: "completed" });
  const testReport = buildTestReport({
    work_item_id: "item-no-worktree",
    command_evidence: [buildCommandEvidence({ check: "validation_profile", profile: "workspace_inventory" })],
  });
  await openProjectWith(mock, { work_items: [item], test_reports: [testReport] });

  await userEvent.click(screen.getByRole("button", { name: /Tarea sin worktree/i }));
  await screen.findByRole("heading", { name: "Tarea sin worktree" });

  await screen.findByText(/1 perfiles registrados/);
  await screen.findByText(/sin aislamiento registrado/);
});

test("project view surfaces decisions and metrics P4.4's audit trail will read", async () => {
  const mock = createFetchMock();
  globalThis.fetch = mock.fetch;
  const decision = buildDecision({
    title: "Usar SQLite en vez de Postgres",
    decision: "Persistencia local con SQLite.",
    rationale: "No hay necesidad de un servidor de base de datos separado.",
  });
  await openProjectWith(mock, {
    decisions: [decision],
    metrics: {
      tasks_completed: 4,
      tasks_rejected: 2,
      retries: 3,
      tester_approval_rate: 0.8,
      reviewer_approval_rate: 0.6,
      average_agent_duration_ms: 4500,
    },
  });

  // Decisions and metrics render on the project view itself (ProjectView
  // in app/page.tsx), not inside a work item's drawer -- confirmed by
  // reading the component before writing this test.
  await screen.findByText("Usar SQLite en vez de Postgres");

  // percent() rounds to the nearest integer: 0.8 -> "80%", 0.6 -> "60%".
  await screen.findByText("80%");
  await screen.findByText("60%");
  await screen.findByText("3", { selector: ".mini-metrics strong" });
  // average_agent_duration_ms 4500 -> Math.round(4500/1000) -> "5s".
  await screen.findByText("5s");
});
