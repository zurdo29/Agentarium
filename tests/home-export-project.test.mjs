// P4.2 -- UI coverage for the export flow (app/export-project.tsx +
// page.tsx's wiring), same style as home-import-project.test.mjs: real
// interaction (@testing-library/user-event) against a real `Home`, API
// mocked by tests/support/fetch-mock.mjs (exact match, fails loudly on an
// unregistered route -- see that file).

import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { cleanup, screen, userEvent } from "./support/dom-setup.mjs";
import { createFetchMock } from "./support/fetch-mock.mjs";
import {
  buildExportSummary,
  buildProject,
  buildProjectDetail,
} from "./support/fixtures.mjs";
import { mockBackgroundRefresh, renderHome } from "./support/render-home.mjs";

afterEach(() => {
  cleanup();
});

const PROJECT_ID = "export-project";

async function openProjectWith(mock, detailOverrides) {
  mockBackgroundRefresh(mock);
  await renderHome();

  const project = buildProject({ id: PROJECT_ID, title: "Proyecto para exportar" });
  const detail = buildProjectDetail({ project, ...detailOverrides });
  mock.on("POST", "/api/projects", detail);

  const textarea = screen.getByLabelText(/Objetivo del nuevo proyecto/i);
  await userEvent.type(textarea, "Meta de prueba");
  await userEvent.click(screen.getByRole("button", { name: /Crear proyecto/i }));
  await screen.findByRole("heading", { name: "Proyecto para exportar" });

  return detail;
}

test("exporting a project: preview then export shows the real summary and where it was written", async () => {
  const mock = createFetchMock();
  globalThis.fetch = mock.fetch;
  await openProjectWith(mock);

  const destination = "C:\\deliveries\\export-1";
  const preview = buildExportSummary({
    project: { id: PROJECT_ID, title: "Proyecto para exportar" },
  });
  mock.on("GET", `/api/projects/${PROJECT_ID}/export/preview`, preview);

  const destinationInput = screen.getByLabelText(/Destino de la exportación/i);
  await userEvent.type(destinationInput, destination);
  await userEvent.click(screen.getByRole("button", { name: /^Vista previa$/i }));

  const previewPanel = await screen.findByText(/2 commit\(s\)/i);
  assert.ok(previewPanel.textContent.includes("1 archivo(s) modificado(s)"));
  assert.ok(previewPanel.textContent.includes("coincide con el historial de integraciones"));

  const result = buildExportSummary({
    project: { id: PROJECT_ID, title: "Proyecto para exportar" },
    destination,
    patch_path: `${destination}\\changes.patch`,
    bundle_path: `${destination}\\changes.bundle`,
  });
  mock.on("POST", `/api/projects/${PROJECT_ID}/export`, (call) => {
    assert.equal(call.body.destination, destination);
    assert.deepEqual(call.body.formats.slice().sort(), ["bundle", "patch"]);
    return { status: 201, body: result };
  });

  await userEvent.click(screen.getByRole("button", { name: /^Exportar$/i }));

  await screen.findByText(/Exportación completa/i);
  const writtenNote = await screen.findByText(
    (content) => content.startsWith("Escrito en") && content.includes(destination),
  );
  assert.ok(writtenNote.textContent.includes("changes.patch"));
  assert.ok(writtenNote.textContent.includes("changes.bundle"));
  mock.assertAllMatched();
});

test("exporting a project: unchecking both formats disables the export button without ever calling /export", async () => {
  const mock = createFetchMock();
  globalThis.fetch = mock.fetch;
  await openProjectWith(mock);

  const destinationInput = screen.getByLabelText(/Destino de la exportación/i);
  await userEvent.type(destinationInput, "C:\\deliveries\\export-2");

  await userEvent.click(screen.getByLabelText(/Patch \(git am\)/i));
  await userEvent.click(screen.getByLabelText(/Bundle \(git fetch\)/i));

  const exportButton = screen.getByRole("button", { name: /^Exportar$/i });
  assert.equal(exportButton.disabled, true);

  // /api/projects/export-project/export is deliberately never registered
  // above: if the disabled button were somehow still submittable,
  // fetch-mock would record it as unmatched and this would fail loudly.
  mock.assertAllMatched();
});

test("exporting a project: editing the destination after a preview clears the stale summary", async () => {
  const mock = createFetchMock();
  globalThis.fetch = mock.fetch;
  await openProjectWith(mock);

  mock.on("GET", `/api/projects/${PROJECT_ID}/export/preview`, buildExportSummary());

  const destinationInput = screen.getByLabelText(/Destino de la exportación/i);
  await userEvent.type(destinationInput, "C:\\deliveries\\primero");
  await userEvent.click(screen.getByRole("button", { name: /^Vista previa$/i }));
  // Scoped to the summary panel's own label, not the button of the same
  // name -- both contain the exact text "Vista previa".
  await screen.findByText(/Vista previa/i, { selector: ".micro-label" });

  // Editing the destination must drop the now-stale preview -- otherwise
  // a second, unrelated destination could show a summary that was never
  // actually generated for it.
  await userEvent.type(destinationInput, " editado");

  assert.equal(screen.queryByText(/\d+ commit\(s\)/i), null);
  mock.assertAllMatched();
});

test("exporting a project: the preview button stays disabled until a destination is entered", async () => {
  const mock = createFetchMock();
  globalThis.fetch = mock.fetch;
  await openProjectWith(mock);

  const previewButton = screen.getByRole("button", { name: /^Vista previa$/i });
  assert.equal(previewButton.disabled, true);

  const destinationInput = screen.getByLabelText(/Destino de la exportación/i);
  await userEvent.type(destinationInput, "C:\\deliveries\\export-3");
  assert.equal(previewButton.disabled, false);

  // No preview/export call was ever made in this test.
  mock.assertAllMatched();
});
