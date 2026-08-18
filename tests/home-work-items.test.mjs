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
  buildAgentRun,
  buildArtifact,
  buildCommandEvidence,
  buildDashboard,
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

  // P4.3b: retry is no longer a single click -- it opens an inline
  // confirmation panel first, the real POST only fires once confirmed.
  await userEvent.click(screen.getByRole("button", { name: /Reintentar/i }));
  await screen.findByText(/¿Reintentar esta tarea\?/i);
  await userEvent.click(screen.getByRole("button", { name: /^Confirmar$/i }));

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

  // P4.3b: rework opens a confirm panel with a real reason textarea --
  // the hardcoded string is gone, a real typed reason is required.
  await userEvent.click(screen.getByRole("button", { name: /Revisar de nuevo/i }));
  const reworkReasonField = await screen.findByLabelText(/Motivo de la revisión/i);
  await userEvent.type(reworkReasonField, "Falta manejar el caso límite reportado.");
  await userEvent.click(screen.getByRole("button", { name: /Confirmar revisión/i }));

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

  // P4.3b: escalate opens a confirm panel with a real reason textarea --
  // the hardcoded string is gone, a real typed reason is required.
  await userEvent.click(screen.getByRole("button", { name: /Escalar/i }));
  const escalateReasonField = await screen.findByLabelText(/Motivo de la escalación/i);
  await userEvent.type(escalateReasonField, "El riesgo de esta tarea excede la autoridad automática.");
  await userEvent.click(screen.getByRole("button", { name: /Confirmar escalación/i }));

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
  // Gate-MVP.2 (ADR 0041): the fixture's default verification_mode is
  // "executed" -- confirms the third clause renders.
  await drawer.findByText(/código ejecutado/);

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

test("work item drawer's tester evidence never claims execution for a static-only report", async () => {
  const mock = createFetchMock();
  globalThis.fetch = mock.fetch;
  const item = buildWorkItem({
    id: "item-static-only",
    title: "Tarea de proyecto importado",
    status: "completed",
  });
  const testReport = buildTestReport({
    work_item_id: "item-static-only",
    verification_mode: "static_only",
    command_evidence: [buildCommandEvidence({ check: "validation_profile", profile: "workspace_inventory" })],
  });
  await openProjectWith(mock, { work_items: [item], test_reports: [testReport] });

  await userEvent.click(screen.getByRole("button", { name: /Tarea de proyecto importado/i }));
  await screen.findByRole("heading", { name: "Tarea de proyecto importado" });

  await screen.findByText(/código no ejecutado/);
  assert.equal(screen.queryByText(/código ejecutado/), null);
});

test("the drawer's tester evidence shows removed base definitions, only the non-empty ones", async () => {
  const mock = createFetchMock();
  globalThis.fetch = mock.fetch;
  const item = buildWorkItem({
    id: "item-removed",
    title: "Tarea que borró tests",
    status: "completed",
  });
  const testReport = buildTestReport({
    work_item_id: "item-removed",
    command_evidence: [
      buildCommandEvidence({ check: "validation_profile", profile: "python_syntax" }),
      buildCommandEvidence({
        check: "removed_top_level_names",
        path: "textkit/slug.py",
        removed: [],
      }),
      buildCommandEvidence({
        check: "removed_top_level_names",
        path: "tests/test_slug.py",
        removed: ["SlugifyTests", "SlugifyTests.test_strips_accents"],
      }),
    ],
  });
  await openProjectWith(mock, { work_items: [item], test_reports: [testReport] });

  await userEvent.click(screen.getByRole("button", { name: /Tarea que borró tests/i }));
  await screen.findByRole("heading", { name: "Tarea que borró tests" });

  await screen.findByText("tests/test_slug.py");
  await screen.findByText(/SlugifyTests, SlugifyTests\.test_strips_accents/);
  assert.equal(screen.queryByText("textkit/slug.py"), null);
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

// P4.4b -- attempt history in TaskDrawer, on demand (GET .../agent-runs
// is project-scoped, so it's fetched once per project and filtered
// client-side by work_item_id, same idiom as reviews/test_reports).

test("the attempt history button loads and filters agent runs to the open task", async () => {
  const mock = createFetchMock();
  globalThis.fetch = mock.fetch;
  const item = buildWorkItem({ id: "item-with-runs", title: "Tarea con intentos" });
  const otherItem = buildWorkItem({ id: "item-other", title: "Otra tarea" });
  await openProjectWith(mock, { work_items: [item, otherItem] });

  await userEvent.click(screen.getByRole("button", { name: /Tarea con intentos/i }));
  await screen.findByRole("heading", { name: "Tarea con intentos" });

  const ownRun = buildAgentRun({
    work_item_id: "item-with-runs",
    agent_role: "implementation_worker",
    model: "qwen2.5-coder:7b",
    attempt: 1,
  });
  const foreignRun = buildAgentRun({ work_item_id: "item-other", attempt: 1 });
  mock.on("GET", `/api/projects/${PROJECT_ID}/agent-runs`, [ownRun, foreignRun]);

  await userEvent.click(screen.getByRole("button", { name: /Ver historial de intentos/i }));

  await screen.findByText(/qwen2\.5-coder:7b/);
  // Only one run belongs to this item -- the other work item's run must
  // not leak in just because both share the same project-scoped fetch.
  const drawer = within(
    screen.getByRole("complementary", { name: /Detalle de Tarea con intentos/i }),
  );
  assert.equal(drawer.getAllByText(/qwen2\.5-coder:7b/i).length, 1);
  mock.assertAllMatched();
});

test("the attempt history shows full resource usage diagnostics, including null queue/generation", async () => {
  const mock = createFetchMock();
  globalThis.fetch = mock.fetch;
  const item = buildWorkItem({ id: "item-diagnostics", title: "Tarea con diagnóstico" });
  await openProjectWith(mock, { work_items: [item] });

  await userEvent.click(screen.getByRole("button", { name: /Tarea con diagnóstico/i }));
  await screen.findByRole("heading", { name: "Tarea con diagnóstico" });

  // queue_wait_ms/generation_ms are a real ResourceUsage | null pair (not
  // every provider path fills both) -- null here on purpose, must render
  // as a real "sin dato", never "nullms" or a silent blank.
  const run = buildAgentRun({
    work_item_id: "item-diagnostics",
    resource_usage: {
      duration_ms: 3400,
      queue_wait_ms: null,
      generation_ms: null,
      prompt_characters: 500,
      response_characters: 800,
      prompt_tokens_approx: 120,
      response_tokens_approx: 200,
      model: "qwen2.5-coder:7b",
      provider: "mock",
      errors: 2,
    },
  });
  mock.on("GET", `/api/projects/${PROJECT_ID}/agent-runs`, [run]);

  await userEvent.click(screen.getByRole("button", { name: /Ver historial de intentos/i }));

  await screen.findByText(
    /3400ms totales · cola sin dato · generación sin dato · 2 error\(es\)/,
  );
  mock.assertAllMatched();
});

test("a slow attempt-history response for a project the user has left never overwrites the one now on screen", async () => {
  const mock = createFetchMock();
  globalThis.fetch = mock.fetch;
  const projectA = buildProject({ id: "runs-project-a", title: "Runs Alfa" });
  const projectB = buildProject({ id: "runs-project-b", title: "Runs Beta" });
  mockBackgroundRefresh(mock, buildDashboard({ projects: [projectA, projectB] }));
  await renderHome();

  const itemA = buildWorkItem({ id: "task-a", title: "Tarea de Alfa" });
  const itemB = buildWorkItem({ id: "task-b", title: "Tarea de Beta" });
  mock.on(
    "GET",
    "/api/projects/runs-project-a",
    buildProjectDetail({ project: projectA, work_items: [itemA] }),
  );
  mock.on("GET", "/api/projects/runs-project-a/events", []);
  mock.on(
    "GET",
    "/api/projects/runs-project-b",
    buildProjectDetail({ project: projectB, work_items: [itemB] }),
  );
  mock.on("GET", "/api/projects/runs-project-b/events", []);

  await userEvent.click(screen.getByRole("button", { name: /Runs Alfa/i }));
  await screen.findByRole("heading", { name: "Runs Alfa" });
  await userEvent.click(screen.getByRole("button", { name: /Tarea de Alfa/i }));
  await screen.findByRole("heading", { name: "Tarea de Alfa" });

  let resolveRunsA;
  const pendingRunsA = new Promise((resolve) => {
    resolveRunsA = resolve;
  });
  mock.on("GET", "/api/projects/runs-project-a/agent-runs", () => pendingRunsA);
  await userEvent.click(screen.getByRole("button", { name: /Ver historial de intentos/i }));
  await screen.findByText(/Cargando…/i);

  // Leave for a different project before Alfa's history resolves.
  const mainNav = screen.getByRole("navigation", { name: /Navegación principal/i });
  await userEvent.click(within(mainNav).getByRole("button", { name: /Dashboard/i }));
  await userEvent.click(screen.getByRole("button", { name: /Runs Beta/i }));
  await screen.findByRole("heading", { name: "Runs Beta" });
  await userEvent.click(screen.getByRole("button", { name: /Tarea de Beta/i }));
  await screen.findByRole("heading", { name: "Tarea de Beta" });

  resolveRunsA([buildAgentRun({ work_item_id: "task-a", model: "modelo-exclusivo-de-alfa" })]);
  await new Promise((resolve) => setTimeout(resolve, 0));

  assert.equal(screen.queryByText(/modelo-exclusivo-de-alfa/i), null);
  // Beta's drawer never had its own history requested, so it still shows
  // the initial button -- proof agentRuns is still null for Beta, not
  // silently filled by Alfa's late response.
  await screen.findByRole("button", { name: /Ver historial de intentos/i });
  mock.assertAllMatched();
});
