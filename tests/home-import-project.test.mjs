// P4.1 -- UI coverage for the import flow (app/import-project.tsx +
// page.tsx's wiring), same style as home-project-lifecycle.test.mjs:
// real interaction (@testing-library/user-event) against a real `Home`,
// API mocked by tests/support/fetch-mock.mjs (exact match, fails loudly
// on an unregistered route -- see that file).

import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { cleanup, screen, userEvent } from "./support/dom-setup.mjs";
import { createFetchMock } from "./support/fetch-mock.mjs";
import {
  buildProject,
  buildProjectDetail,
  buildSourceInspection,
} from "./support/fixtures.mjs";
import { mockBackgroundRefresh, renderHome } from "./support/render-home.mjs";

afterEach(() => {
  cleanup();
});

test("importing a project: inspect -> preview -> confirm creates the project and shows its import metadata", async () => {
  const mock = createFetchMock();
  globalThis.fetch = mock.fetch;
  mockBackgroundRefresh(mock);
  await renderHome();

  const sourcePath = "C:\\repos\\mi-app";
  const headCommit = "b".repeat(40);
  mock.on("POST", "/api/projects/import/inspect", (call) => {
    assert.equal(call.body.source_path, sourcePath);
    return buildSourceInspection({ head_commit: headCommit, branch: "main" });
  });

  const pathInput = screen.getByLabelText(/Ruta del proyecto a importar/i);
  await userEvent.type(pathInput, sourcePath);
  await userEvent.click(screen.getByRole("button", { name: /^Inspeccionar$/i }));

  const preview = await screen.findByText(/Listo para importar/i);
  assert.ok(preview.textContent.includes("rama main"));
  assert.ok(preview.textContent.includes(headCommit.slice(0, 8)));

  const imported = buildProjectDetail({
    project: buildProject({
      id: "imported-project",
      title: "Proyecto importado",
      imported_source_path: sourcePath,
      imported_commit: headCommit,
    }),
  });
  mock.on("POST", "/api/projects/import", (call) => {
    assert.equal(call.body.source_path, sourcePath);
    assert.equal(call.body.goal, "Continuar el desarrollo existente");
    return { status: 201, body: imported };
  });

  const goalField = screen.getByLabelText(/Objetivo para el proyecto importado/i);
  await userEvent.type(goalField, "Continuar el desarrollo existente");
  await userEvent.click(screen.getByRole("button", { name: /Importar proyecto/i }));

  await screen.findByRole("heading", { name: "Proyecto importado" });
  const importNote = await screen.findByText(
    (content) => content.startsWith("Importado desde") && content.includes(sourcePath),
  );
  assert.ok(importNote.textContent.includes(headCommit.slice(0, 8)));
  mock.assertAllMatched();
});

test("importing a project: an ineligible source shows the reason and keeps confirm disabled without ever calling /import", async () => {
  const mock = createFetchMock();
  globalThis.fetch = mock.fetch;
  mockBackgroundRefresh(mock);
  await renderHome();

  const sourcePath = "C:\\repos\\sucio";
  mock.on("POST", "/api/projects/import/inspect", (call) => {
    assert.equal(call.body.source_path, sourcePath);
    return buildSourceInspection({
      eligible: false,
      reason: "El repositorio tiene cambios sin commitear. Commiteá o guardá con stash antes de importar.",
      is_dirty: true,
      head_commit: null,
      branch: null,
    });
  });

  const pathInput = screen.getByLabelText(/Ruta del proyecto a importar/i);
  await userEvent.type(pathInput, sourcePath);
  await userEvent.click(screen.getByRole("button", { name: /^Inspeccionar$/i }));

  await screen.findByText(/No se puede importar/i);
  screen.getByText(/cambios sin commitear/i);

  // The goal field only appears once eligible -- an ineligible source
  // must never reach that step.
  assert.equal(
    screen.queryByLabelText(/Objetivo para el proyecto importado/i),
    null,
  );

  const confirmButton = screen.getByRole("button", { name: /Importar proyecto/i });
  assert.equal(confirmButton.disabled, true);

  // /api/projects/import is deliberately never registered above: if the
  // disabled button were somehow still submittable, fetch-mock would
  // record it as unmatched and the assertion below would fail loudly.
  mock.assertAllMatched();
});

test("importing a project: editing the path after a successful inspect clears the stale preview", async () => {
  const mock = createFetchMock();
  globalThis.fetch = mock.fetch;
  mockBackgroundRefresh(mock);
  await renderHome();

  mock.on("POST", "/api/projects/import/inspect", buildSourceInspection());

  const pathInput = screen.getByLabelText(/Ruta del proyecto a importar/i);
  await userEvent.type(pathInput, "C:\\repos\\primero");
  await userEvent.click(screen.getByRole("button", { name: /^Inspeccionar$/i }));
  await screen.findByText(/Listo para importar/i);

  // Editing the path must drop the now-stale preview -- otherwise a
  // second, unrelated path could be confirmed against the first path's
  // eligibility.
  await userEvent.type(pathInput, " editado");

  assert.equal(screen.queryByText(/Listo para importar/i), null);
  assert.equal(screen.getByRole("button", { name: /Importar proyecto/i }).disabled, true);
  mock.assertAllMatched();
});
