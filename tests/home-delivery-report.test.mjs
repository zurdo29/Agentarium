// P4.4b -- frontend half of "entrega y auditoría" (PLANS.md P4.4).
// DeliveryReportView (app/delivery-report.tsx) is on-demand, not a
// decorative panel auto-fetched by openProject() -- see page.tsx's own
// comment on loadDeliveryReport(). Covers: the report renders what was
// asked/delivered, evidence stays visible for a *completed* item (not
// just the problem outcomes -- an explicit correction on this phase, see
// PR description), and a slow response for a project the user has since
// left never clobbers whatever is now on screen for a different one.

import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { cleanup, screen, userEvent, within } from "./support/dom-setup.mjs";
import { createFetchMock } from "./support/fetch-mock.mjs";
import {
  buildCommandEvidence,
  buildDashboard,
  buildDeliveryReport,
  buildDeliveryReportWorkItem,
  buildProject,
  buildProjectDetail,
} from "./support/fixtures.mjs";
import { mockBackgroundRefresh, renderHome } from "./support/render-home.mjs";

afterEach(() => {
  cleanup();
});

test("generating the delivery report renders the goal, totals and a work item's outcome", async () => {
  const mock = createFetchMock();
  globalThis.fetch = mock.fetch;
  const project = buildProject({ id: "report-project", title: "Proyecto con informe" });
  mockBackgroundRefresh(mock, buildDashboard({ projects: [project] }));
  await renderHome();

  mock.on("GET", "/api/projects/report-project", buildProjectDetail({ project }));
  mock.on("GET", "/api/projects/report-project/events", []);
  await userEvent.click(screen.getByRole("button", { name: /Proyecto con informe/i }));
  await screen.findByRole("heading", { name: "Proyecto con informe" });

  const report = buildDeliveryReport({
    project: { id: "report-project", title: "Proyecto con informe", goal: project.goal },
    work_items: [buildDeliveryReportWorkItem({ title: "Tarea entregada" })],
    totals: { completed: 1 },
  });
  mock.on("GET", "/api/projects/report-project/report", report);

  await userEvent.click(screen.getByRole("button", { name: /Generar informe/i }));

  await screen.findByText("Tarea entregada");
  // totals bar: one "completed" entry -> "1" next to its Spanish label.
  await screen.findByText("1", { selector: ".delivery-report-totals strong" });
  await screen.findByRole("button", { name: /Actualizar informe/i });
  mock.assertAllMatched();
});

test("a completed item's review/test evidence stays visible, not just the problem outcomes", async () => {
  const mock = createFetchMock();
  globalThis.fetch = mock.fetch;
  const project = buildProject({ id: "evidence-project", title: "Proyecto con evidencia" });
  mockBackgroundRefresh(mock, buildDashboard({ projects: [project] }));
  await renderHome();

  mock.on("GET", "/api/projects/evidence-project", buildProjectDetail({ project }));
  mock.on("GET", "/api/projects/evidence-project/events", []);
  await userEvent.click(screen.getByRole("button", { name: /Proyecto con evidencia/i }));
  await screen.findByRole("heading", { name: "Proyecto con evidencia" });

  const completedItem = buildDeliveryReportWorkItem({
    work_item_id: "item-completed",
    title: "Tarea completada con evidencia",
    outcome: "completed",
    review_verdict: "approved",
    review_reasons: ["La evidencia es explícita."],
    test_passed: true,
    test_summary: "Todas las validaciones automáticas pasaron.",
  });
  mock.on(
    "GET",
    "/api/projects/evidence-project/report",
    buildDeliveryReport({
      project: { id: "evidence-project", title: "Proyecto con evidencia", goal: project.goal },
      work_items: [completedItem],
      totals: { completed: 1 },
    }),
  );

  await userEvent.click(screen.getByRole("button", { name: /Generar informe/i }));
  await screen.findByText("Tarea completada con evidencia");

  // The row's own PASS badge is visible collapsed, for every outcome --
  // read app/delivery-report.tsx before writing this, not guessed.
  await screen.findByText("PASS");

  // Expanding a *completed* row still surfaces the real review/test
  // evidence -- this is the exact behavior the P4.4b correction asked
  // for: evidence must not be suppressed once an item is "done".
  await userEvent.click(screen.getByRole("button", { name: /Tarea completada con evidencia/i }));
  await screen.findByText("La evidencia es explícita.");
  await screen.findByText("Todas las validaciones automáticas pasaron.");
  mock.assertAllMatched();
});

test("a completed item's expanded detail lists each test check, a command-evidence summary, and the real integration file paths", async () => {
  const mock = createFetchMock();
  globalThis.fetch = mock.fetch;
  const project = buildProject({ id: "files-project", title: "Proyecto con archivos" });
  mockBackgroundRefresh(mock, buildDashboard({ projects: [project] }));
  await renderHome();

  mock.on("GET", "/api/projects/files-project", buildProjectDetail({ project }));
  mock.on("GET", "/api/projects/files-project/events", []);
  await userEvent.click(screen.getByRole("button", { name: /Proyecto con archivos/i }));
  await screen.findByRole("heading", { name: "Proyecto con archivos" });

  const completedItem = buildDeliveryReportWorkItem({
    work_item_id: "item-files",
    title: "Tarea con archivos integrados",
    outcome: "completed",
    test_passed: true,
    test_summary: "Todas las validaciones automáticas pasaron.",
    test_checks: [
      { name: "file_exists", passed: true, evidence: "Checksum verificado." },
      { name: "lint_clean", passed: false, evidence: "2 advertencias de ruff." },
    ],
    test_command_evidence: [
      buildCommandEvidence({ check: "validation_profile", profile: "workspace_inventory" }),
      buildCommandEvidence({ check: "validation_profile", profile: "python_syntax" }),
      buildCommandEvidence({ check: "isolated_change_set", backend: "git_worktree", verified: true }),
    ],
    integration_files: ["backend/agentarium/api/app.py", "backend/tests/test_delivery_report.py"],
  });
  mock.on(
    "GET",
    "/api/projects/files-project/report",
    buildDeliveryReport({
      project: { id: "files-project", title: "Proyecto con archivos", goal: project.goal },
      work_items: [completedItem],
      totals: { completed: 1 },
    }),
  );

  await userEvent.click(screen.getByRole("button", { name: /Generar informe/i }));
  await screen.findByText("Tarea con archivos integrados");
  await userEvent.click(screen.getByRole("button", { name: /Tarea con archivos integrados/i }));

  // Each test_check individually (name + PASS/FAIL + evidence), not just
  // a count -- both the passing and the failing check must render.
  await screen.findByText("file_exists");
  await screen.findByText("Checksum verificado.");
  await screen.findByText("lint_clean");
  await screen.findByText("2 advertencias de ruff.");

  // Compact command-evidence summary -- same computed text TaskDrawer
  // already derives from the identical command_evidence shape.
  await screen.findByText(/2 perfiles registrados/);
  await screen.findByText(/worktree verificado/);
  // Gate-MVP.2 (ADR 0041): the fixture's default test_verification_mode
  // is "executed" -- confirms the third clause renders, not just exists.
  await screen.findByText(/código ejecutado/);

  // Real integration file paths, not just a count.
  await screen.findByText("backend/agentarium/api/app.py");
  await screen.findByText("backend/tests/test_delivery_report.py");
  mock.assertAllMatched();
});

test("a static-only verified item never reads as if its code had run", async () => {
  const mock = createFetchMock();
  globalThis.fetch = mock.fetch;
  const project = buildProject({ id: "static-project", title: "Proyecto importado" });
  mockBackgroundRefresh(mock, buildDashboard({ projects: [project] }));
  await renderHome();

  mock.on("GET", "/api/projects/static-project", buildProjectDetail({ project }));
  mock.on("GET", "/api/projects/static-project/events", []);
  await userEvent.click(screen.getByRole("button", { name: /Proyecto importado/i }));
  await screen.findByRole("heading", { name: "Proyecto importado" });

  const completedItem = buildDeliveryReportWorkItem({
    work_item_id: "item-static",
    title: "Tarea de proyecto importado",
    outcome: "completed",
    test_passed: true,
    test_verification_mode: "static_only",
    test_command_evidence: [
      buildCommandEvidence({ check: "validation_profile", profile: "workspace_inventory" }),
      buildCommandEvidence({ check: "isolated_change_set", backend: "git_worktree", verified: true }),
    ],
  });
  mock.on(
    "GET",
    "/api/projects/static-project/report",
    buildDeliveryReport({
      project: { id: "static-project", title: "Proyecto importado", goal: project.goal },
      work_items: [completedItem],
      totals: { completed: 1 },
    }),
  );

  await userEvent.click(screen.getByRole("button", { name: /Generar informe/i }));
  await screen.findByText("Tarea de proyecto importado");
  await userEvent.click(screen.getByRole("button", { name: /Tarea de proyecto importado/i }));

  await screen.findByText(/código no ejecutado/);
  assert.equal(screen.queryByText(/código ejecutado/), null);
  // The outcome hint itself must not claim verification either -- "evidencia
  // verificada" for code that never ran is exactly the overclaim this gate
  // exists to prevent.
  await screen.findByText(/el código no se ejecutó/i);
  assert.equal(screen.queryByText(/Completada con evidencia verificada/i), null);
  mock.assertAllMatched();
});

test("a completed item with no test report at all never claims verified evidence", async () => {
  const mock = createFetchMock();
  globalThis.fetch = mock.fetch;
  const project = buildProject({ id: "noreport-project", title: "Proyecto sin informe" });
  mockBackgroundRefresh(mock, buildDashboard({ projects: [project] }));
  await renderHome();

  mock.on("GET", "/api/projects/noreport-project", buildProjectDetail({ project }));
  mock.on("GET", "/api/projects/noreport-project/events", []);
  await userEvent.click(screen.getByRole("button", { name: /Proyecto sin informe/i }));
  await screen.findByRole("heading", { name: "Proyecto sin informe" });

  // Third state: no TestReport at all. Distinct from both "executed" and
  // "static_only" -- absence of evidence must not borrow either wording.
  const completedItem = buildDeliveryReportWorkItem({
    work_item_id: "item-noreport",
    title: "Tarea completada sin informe",
    outcome: "completed",
    test_passed: null,
    test_verification_mode: null,
    test_summary: null,
    test_checks: [],
    test_command_evidence: [],
  });
  mock.on(
    "GET",
    "/api/projects/noreport-project/report",
    buildDeliveryReport({
      project: { id: "noreport-project", title: "Proyecto sin informe", goal: project.goal },
      work_items: [completedItem],
      totals: { completed: 1 },
    }),
  );

  await userEvent.click(screen.getByRole("button", { name: /Generar informe/i }));
  await screen.findByText("Tarea completada sin informe");
  await userEvent.click(screen.getByRole("button", { name: /Tarea completada sin informe/i }));

  await screen.findByText(/Completada sin informe técnico disponible/i);
  assert.equal(screen.queryByText(/Completada con evidencia verificada/i), null);
  assert.equal(screen.queryByText(/verificación estática/i), null);
  mock.assertAllMatched();
});

test("removed base definitions are shown with file and names, not just persisted", async () => {
  const mock = createFetchMock();
  globalThis.fetch = mock.fetch;
  const project = buildProject({ id: "removed-project", title: "Proyecto con borrados" });
  mockBackgroundRefresh(mock, buildDashboard({ projects: [project] }));
  await renderHome();

  mock.on("GET", "/api/projects/removed-project", buildProjectDetail({ project }));
  mock.on("GET", "/api/projects/removed-project/events", []);
  await userEvent.click(screen.getByRole("button", { name: /Proyecto con borrados/i }));
  await screen.findByRole("heading", { name: "Proyecto con borrados" });

  const completedItem = buildDeliveryReportWorkItem({
    work_item_id: "item-removed",
    title: "Tarea que borró tests",
    outcome: "completed",
    test_passed: true,
    test_summary: "Validaciones automáticas.",
    test_command_evidence: [
      buildCommandEvidence({ check: "validation_profile", profile: "python_syntax" }),
      // Empty list for one file, real removals for another -- only the
      // non-empty one may render.
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
  mock.on(
    "GET",
    "/api/projects/removed-project/report",
    buildDeliveryReport({
      project: { id: "removed-project", title: "Proyecto con borrados", goal: project.goal },
      work_items: [completedItem],
      totals: { completed: 1 },
    }),
  );

  await userEvent.click(screen.getByRole("button", { name: /Generar informe/i }));
  await screen.findByText("Tarea que borró tests");
  await userEvent.click(screen.getByRole("button", { name: /Tarea que borró tests/i }));

  await screen.findByText("tests/test_slug.py");
  await screen.findByText(/SlugifyTests, SlugifyTests\.test_strips_accents/);
  // The file whose list was empty must not appear at all.
  assert.equal(screen.queryByText("textkit/slug.py"), null);
  mock.assertAllMatched();
});

test("a completed item with nothing removed renders no removals block at all", async () => {
  const mock = createFetchMock();
  globalThis.fetch = mock.fetch;
  const project = buildProject({ id: "clean-project", title: "Proyecto sin borrados" });
  mockBackgroundRefresh(mock, buildDashboard({ projects: [project] }));
  await renderHome();

  mock.on("GET", "/api/projects/clean-project", buildProjectDetail({ project }));
  mock.on("GET", "/api/projects/clean-project/events", []);
  await userEvent.click(screen.getByRole("button", { name: /Proyecto sin borrados/i }));
  await screen.findByRole("heading", { name: "Proyecto sin borrados" });

  const completedItem = buildDeliveryReportWorkItem({
    work_item_id: "item-clean",
    title: "Tarea que no borró nada",
    outcome: "completed",
    test_passed: true,
    test_summary: "Validaciones automáticas.",
    test_command_evidence: [
      buildCommandEvidence({ check: "validation_profile", profile: "python_syntax" }),
      buildCommandEvidence({
        check: "removed_top_level_names",
        path: "textkit/slug.py",
        removed: [],
      }),
    ],
  });
  mock.on(
    "GET",
    "/api/projects/clean-project/report",
    buildDeliveryReport({
      project: { id: "clean-project", title: "Proyecto sin borrados", goal: project.goal },
      work_items: [completedItem],
      totals: { completed: 1 },
    }),
  );

  await userEvent.click(screen.getByRole("button", { name: /Generar informe/i }));
  await screen.findByText("Tarea que no borró nada");
  await userEvent.click(screen.getByRole("button", { name: /Tarea que no borró nada/i }));

  await screen.findByText("Validaciones automáticas.");
  assert.equal(screen.queryByText(/Definiciones eliminadas/i), null);
  mock.assertAllMatched();
});

test("unverified_completed_items renders as a visible warning, not silently dropped", async () => {
  const mock = createFetchMock();
  globalThis.fetch = mock.fetch;
  const project = buildProject({ id: "warning-project", title: "Proyecto con alerta" });
  mockBackgroundRefresh(mock, buildDashboard({ projects: [project] }));
  await renderHome();

  mock.on("GET", "/api/projects/warning-project", buildProjectDetail({ project }));
  mock.on("GET", "/api/projects/warning-project/events", []);
  await userEvent.click(screen.getByRole("button", { name: /Proyecto con alerta/i }));
  await screen.findByRole("heading", { name: "Proyecto con alerta" });

  mock.on(
    "GET",
    "/api/projects/warning-project/report",
    buildDeliveryReport({
      project: { id: "warning-project", title: "Proyecto con alerta", goal: project.goal },
      unverified_completed_items: [
        {
          work_item_id: "item-x",
          title: "Tarea sospechosa",
          reason: "static_only_verification",
        },
      ],
    }),
  );

  await userEvent.click(screen.getByRole("button", { name: /Generar informe/i }));

  const alert = await screen.findByRole("alert");
  assert.match(within(alert).getByText(/Tarea sospechosa/i).textContent, /Tarea sospechosa/i);
  mock.assertAllMatched();
});

test("each unverified item shows its own structured reason, not just its title", async () => {
  const mock = createFetchMock();
  globalThis.fetch = mock.fetch;
  const project = buildProject({ id: "reason-project", title: "Proyecto con dos causas" });
  mockBackgroundRefresh(mock, buildDashboard({ projects: [project] }));
  await renderHome();

  mock.on("GET", "/api/projects/reason-project", buildProjectDetail({ project }));
  mock.on("GET", "/api/projects/reason-project/events", []);
  await userEvent.click(screen.getByRole("button", { name: /Proyecto con dos causas/i }));
  await screen.findByRole("heading", { name: "Proyecto con dos causas" });

  mock.on(
    "GET",
    "/api/projects/reason-project/report",
    buildDeliveryReport({
      project: { id: "reason-project", title: "Proyecto con dos causas", goal: project.goal },
      unverified_completed_items: [
        {
          work_item_id: "item-static",
          title: "Tarea de proyecto importado",
          reason: "static_only_verification",
        },
        {
          work_item_id: "item-missing",
          title: "Tarea con base editada a mano",
          reason: "missing_review_or_test_report",
        },
      ],
    }),
  );

  await userEvent.click(screen.getByRole("button", { name: /Generar informe/i }));

  // Two structurally different facts: one is the expected honest state of
  // an imported project, the other is a data-integrity problem. They must
  // not read the same.
  const alert = await screen.findByRole("alert");
  within(alert).getByText(/código no ejecutado/i);
  within(alert).getByText(/sin review o informe técnico/i);
  mock.assertAllMatched();
});

test("a slow report response for a project the user has left never overwrites the one now on screen", async () => {
  const mock = createFetchMock();
  globalThis.fetch = mock.fetch;
  const projectA = buildProject({ id: "project-a", title: "Proyecto Alfa" });
  const projectB = buildProject({ id: "project-b", title: "Proyecto Beta" });
  mockBackgroundRefresh(mock, buildDashboard({ projects: [projectA, projectB] }));
  await renderHome();

  mock.on("GET", "/api/projects/project-a", buildProjectDetail({ project: projectA }));
  mock.on("GET", "/api/projects/project-a/events", []);
  mock.on("GET", "/api/projects/project-b", buildProjectDetail({ project: projectB }));
  mock.on("GET", "/api/projects/project-b/events", []);

  await userEvent.click(screen.getByRole("button", { name: /Proyecto Alfa/i }));
  await screen.findByRole("heading", { name: "Proyecto Alfa" });

  let resolveReportA;
  const pendingReportA = new Promise((resolve) => {
    resolveReportA = resolve;
  });
  mock.on("GET", "/api/projects/project-a/report", () => pendingReportA);

  await userEvent.click(screen.getByRole("button", { name: /Generar informe/i }));
  await screen.findByText(/Generando…/i);

  // Leave for a different project before Alfa's report resolves.
  const mainNav = screen.getByRole("navigation", { name: /Navegación principal/i });
  await userEvent.click(within(mainNav).getByRole("button", { name: /Dashboard/i }));
  await userEvent.click(screen.getByRole("button", { name: /Proyecto Beta/i }));
  await screen.findByRole("heading", { name: "Proyecto Beta" });

  // Now let Alfa's slow report land -- it must not surface on Beta's screen.
  resolveReportA(
    buildDeliveryReport({
      project: { id: "project-a", title: "Proyecto Alfa", goal: projectA.goal },
      work_items: [buildDeliveryReportWorkItem({ title: "Tarea exclusiva de Alfa" })],
    }),
  );
  await new Promise((resolve) => setTimeout(resolve, 0));

  assert.equal(screen.queryByText("Tarea exclusiva de Alfa"), null);
  // Beta's own report was never requested, so its button is still in the
  // initial "Generar informe" state, not "Actualizar informe" -- proof
  // deliveryReport is still null for Beta, not silently filled by Alfa.
  await screen.findByRole("button", { name: /^Generar informe$/i });
  mock.assertAllMatched();
});
