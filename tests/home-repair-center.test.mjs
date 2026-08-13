// P4.3b -- UI coverage for the Repair Center (app/repair-center.tsx +
// app/candidate-submission.tsx + page.tsx's wiring), same style as
// home-export-project.test.mjs: real interaction
// (@testing-library/user-event) against a real Home, API mocked by
// tests/support/fetch-mock.mjs (exact match, fails loudly on an
// unregistered route -- see that file).

import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { cleanup, screen, userEvent, within } from "./support/dom-setup.mjs";
import { createFetchMock } from "./support/fetch-mock.mjs";
import {
  buildArtifact,
  buildProject,
  buildProjectDetail,
  buildRepairItem,
  buildWorkItem,
} from "./support/fixtures.mjs";
import { mockBackgroundRefresh, renderHome } from "./support/render-home.mjs";

afterEach(() => {
  cleanup();
});

async function openRepairCenterWith(mock, items) {
  mockBackgroundRefresh(mock);
  await renderHome();
  mock.on("GET", "/api/repair-center", items);
  await userEvent.click(screen.getByRole("button", { name: /Centro de reparación/i }));
  await screen.findByRole("heading", { name: /Centro de reparación/i });
  return items;
}

test("repair center: rows render evidence and expanding shows the full error", async () => {
  const mock = createFetchMock();
  globalThis.fetch = mock.fetch;
  const item = buildRepairItem({
    work_item_id: "item-1",
    title: "Tarea con error real",
    last_error: "El checksum del artefacto no coincide.",
  });
  await openRepairCenterWith(mock, [item]);

  await screen.findByText("Tarea con error real");
  await userEvent.click(screen.getByRole("button", { name: /Tarea con error real/i }));

  await screen.findByText("El checksum del artefacto no coincide.");
  mock.assertAllMatched();
});

test("repair center: filtering by project and cause narrows rows without a new network call", async () => {
  const mock = createFetchMock();
  globalThis.fetch = mock.fetch;
  const itemA = buildRepairItem({
    work_item_id: "item-a",
    title: "Tarea del proyecto A",
    project_id: "project-a",
    project_title: "Proyecto A",
    cause: "failed",
  });
  const itemB = buildRepairItem({
    work_item_id: "item-b",
    title: "Tarea del proyecto B",
    project_id: "project-b",
    project_title: "Proyecto B",
    cause: "changes_requested",
  });
  await openRepairCenterWith(mock, [itemA, itemB]);

  await screen.findByText("Tarea del proyecto A");
  await screen.findByText("Tarea del proyecto B");

  await userEvent.selectOptions(screen.getByLabelText(/^Proyecto$/i), "project-a");
  assert.ok(screen.queryByText("Tarea del proyecto A"));
  assert.equal(screen.queryByText("Tarea del proyecto B"), null);

  await userEvent.selectOptions(screen.getByLabelText(/^Proyecto$/i), "");
  await userEvent.selectOptions(screen.getByLabelText(/^Causa$/i), "changes_requested");
  assert.equal(screen.queryByText("Tarea del proyecto A"), null);
  assert.ok(screen.queryByText("Tarea del proyecto B"));

  mock.assertAllMatched();
});

test("repair center: a blocked row offers no actions and its link opens the blocking dependency", async () => {
  const mock = createFetchMock();
  globalThis.fetch = mock.fetch;
  const item = buildRepairItem({
    work_item_id: "item-blocked",
    title: "Tarea bloqueada",
    cause: "blocked",
    status: "blocked",
    project_id: "project-1",
    blocking_dependency_id: "item-blocking",
    blocking_dependency_title: "Dependencia rota",
    blocking_dependency_status: "failed",
  });
  await openRepairCenterWith(mock, [item]);

  await userEvent.click(screen.getByRole("button", { name: /Tarea bloqueada/i }));
  await screen.findByText(/Bloqueada por/i);

  assert.equal(screen.queryByRole("button", { name: /^↻ Reintentar$/i }), null);
  assert.equal(screen.queryByRole("button", { name: /Recuperar artefacto/i }), null);
  assert.equal(screen.queryByRole("button", { name: /Enviar candidato/i }), null);
  assert.equal(screen.queryByRole("button", { name: /^↑ Escalar$/i }), null);

  const project = buildProject({ id: "project-1", title: "Proyecto con dependencia rota" });
  const blockingItem = buildWorkItem({
    id: "item-blocking",
    title: "Dependencia rota",
    status: "failed",
  });
  mock.on(
    "GET",
    "/api/projects/project-1",
    buildProjectDetail({ project, work_items: [blockingItem] }),
  );
  mock.on("GET", "/api/projects/project-1/events", []);

  await userEvent.click(screen.getByRole("button", { name: /Abrir la dependencia bloqueante/i }));

  await screen.findByRole("heading", { name: "Dependencia rota" });
  mock.assertAllMatched();
});

test("repair center: retry only fires the real route after the confirm panel is confirmed", async () => {
  const mock = createFetchMock();
  globalThis.fetch = mock.fetch;
  const item = buildRepairItem({ work_item_id: "item-retry", title: "Tarea para reintentar" });
  await openRepairCenterWith(mock, [item]);

  await userEvent.click(screen.getByRole("button", { name: /Tarea para reintentar/i }));
  await userEvent.click(screen.getByRole("button", { name: /^↻ Reintentar$/i }));
  await screen.findByText(/¿Reintentar esta tarea\?/i);

  mock.on("POST", "/api/work-items/item-retry/retry", {});
  mock.on("GET", "/api/repair-center", []);

  await userEvent.click(screen.getByRole("button", { name: /^Confirmar$/i }));

  await screen.findByText(/Nada que reparar/i);
  mock.assertAllMatched();
});

test("repair center: cancelling a confirm panel never calls the action route", async () => {
  const mock = createFetchMock();
  globalThis.fetch = mock.fetch;
  const item = buildRepairItem({ work_item_id: "item-cancel", title: "Tarea para cancelar" });
  await openRepairCenterWith(mock, [item]);

  await userEvent.click(screen.getByRole("button", { name: /Tarea para cancelar/i }));
  await userEvent.click(screen.getByRole("button", { name: /^↻ Reintentar$/i }));
  await screen.findByText(/¿Reintentar esta tarea\?/i);

  // /api/work-items/item-cancel/retry is deliberately never registered:
  // if Cancelar somehow still confirmed, fetch-mock would record it as
  // unmatched and this would fail loudly.
  await userEvent.click(screen.getByRole("button", { name: /^Cancelar$/i }));

  assert.equal(screen.queryByText(/¿Reintentar esta tarea\?/i), null);
  await screen.findByRole("button", { name: /^↻ Reintentar$/i });
  mock.assertAllMatched();
});

test("repair center: recover loads artifacts once, then confirms with the selected one", async () => {
  const mock = createFetchMock();
  globalThis.fetch = mock.fetch;
  const item = buildRepairItem({
    work_item_id: "item-recover",
    title: "Tarea para recuperar",
    project_id: "project-recover",
  });
  await openRepairCenterWith(mock, [item]);

  const artifact = buildArtifact({
    id: "artifact-old",
    work_item_id: "item-recover",
    title: "Intento anterior",
  });
  const project = buildProject({ id: "project-recover", title: "Proyecto de recuperación" });
  mock.on(
    "GET",
    "/api/projects/project-recover",
    buildProjectDetail({ project, artifacts: [artifact] }),
  );

  await userEvent.click(screen.getByRole("button", { name: /Tarea para recuperar/i }));
  await userEvent.click(screen.getByRole("button", { name: /Recuperar artefacto/i }));

  const artifactSelect = await screen.findByLabelText(/Artefacto a recuperar/i);
  await userEvent.selectOptions(artifactSelect, "artifact-old");

  mock.on("POST", "/api/work-items/item-recover/recover/artifact-old", {});
  mock.on("GET", "/api/repair-center", []);

  await userEvent.click(screen.getByRole("button", { name: /Confirmar recuperación/i }));

  await screen.findByText(/Nada que reparar/i);
  mock.assertAllMatched();
});

test("repair center: escalate requires a real reason and refreshes on success", async () => {
  const mock = createFetchMock();
  globalThis.fetch = mock.fetch;
  const item = buildRepairItem({ work_item_id: "item-escalate", title: "Tarea para escalar" });
  await openRepairCenterWith(mock, [item]);

  await userEvent.click(screen.getByRole("button", { name: /Tarea para escalar/i }));
  await userEvent.click(screen.getByRole("button", { name: /^↑ Escalar$/i }));

  const confirmButton = screen.getByRole("button", { name: /Confirmar escalación/i });
  assert.equal(confirmButton.disabled, true);

  await userEvent.type(
    screen.getByLabelText(/Motivo de la escalación/i),
    "Necesita una decisión humana real.",
  );
  assert.equal(confirmButton.disabled, false);

  mock.on("POST", "/api/work-items/item-escalate/escalate", (call) => {
    assert.equal(call.body.reason, "Necesita una decisión humana real.");
    return {};
  });
  mock.on("GET", "/api/repair-center", []);

  await userEvent.click(confirmButton);

  await screen.findByText(/Nada que reparar/i);
  mock.assertAllMatched();
});

test("repair center: candidate submit is disabled until title, summary, and a complete file exist", async () => {
  const mock = createFetchMock();
  globalThis.fetch = mock.fetch;
  const item = buildRepairItem({ work_item_id: "item-candidate", title: "Tarea para candidato" });
  await openRepairCenterWith(mock, [item]);

  await userEvent.click(screen.getByRole("button", { name: /Tarea para candidato/i }));
  await userEvent.click(screen.getByRole("button", { name: /^Enviar candidato$/i }));

  const submitButton = screen.getByRole("button", { name: /^Enviar candidato$/i });
  assert.equal(submitButton.disabled, true);

  await userEvent.type(screen.getByLabelText(/^Título$/i), "Arreglo manual");
  assert.equal(submitButton.disabled, true);

  await userEvent.type(screen.getByLabelText(/^Resumen$/i), "Arreglo enviado por un operador");
  assert.equal(submitButton.disabled, true);

  await userEvent.click(screen.getByRole("button", { name: /Agregar archivo/i }));
  assert.equal(submitButton.disabled, true);

  await userEvent.type(screen.getByLabelText(/Ruta del archivo 1/i), "fix.py");
  await userEvent.type(screen.getByLabelText(/Propósito del archivo 1/i), "Arreglo");
  await userEvent.type(screen.getByLabelText(/Contenido del archivo 1/i), "print('ok')");
  assert.equal(submitButton.disabled, false);

  mock.on("POST", "/api/work-items/item-candidate/candidate", (call) => {
    assert.equal(call.body.title, "Arreglo manual");
    assert.equal(call.body.files.length, 1);
    assert.equal(call.body.files[0].path, "fix.py");
    return {};
  });
  mock.on("GET", "/api/repair-center", []);

  await userEvent.click(submitButton);

  await screen.findByText(/Nada que reparar/i);
  mock.assertAllMatched();
});

test("repair center: switching from item A to item B clears the candidate draft (A -> B regression)", async () => {
  const mock = createFetchMock();
  globalThis.fetch = mock.fetch;
  const itemA = buildRepairItem({ work_item_id: "item-a", title: "Primera tarea reparable" });
  const itemB = buildRepairItem({ work_item_id: "item-b", title: "Segunda tarea reparable" });
  await openRepairCenterWith(mock, [itemA, itemB]);

  await userEvent.click(screen.getByRole("button", { name: /Primera tarea reparable/i }));
  await userEvent.click(screen.getByRole("button", { name: /^Enviar candidato$/i }));
  await userEvent.type(screen.getByLabelText(/^Título$/i), "Borrador de A, nunca debe llegar a B");
  await userEvent.type(screen.getByLabelText(/^Resumen$/i), "Resumen de A");

  // Switching to a different row's expansion must drop the draft --
  // otherwise a candidate meant for A could be submitted against B.
  await userEvent.click(screen.getByRole("button", { name: /Segunda tarea reparable/i }));
  await userEvent.click(screen.getByRole("button", { name: /^Enviar candidato$/i }));

  const titleField = screen.getByLabelText(/^Título$/i);
  const summaryField = screen.getByLabelText(/^Resumen$/i);
  assert.equal(titleField.value, "");
  assert.equal(summaryField.value, "");

  await userEvent.type(titleField, "Candidato real de B");
  await userEvent.type(summaryField, "Resumen real de B");
  await userEvent.click(screen.getByRole("button", { name: /Agregar archivo/i }));
  await userEvent.type(screen.getByLabelText(/Ruta del archivo 1/i), "b.py");
  await userEvent.type(screen.getByLabelText(/Propósito del archivo 1/i), "Arreglo de B");
  await userEvent.type(screen.getByLabelText(/Contenido del archivo 1/i), "print('b')");

  mock.on("POST", "/api/work-items/item-b/candidate", (call) => {
    assert.equal(call.body.title, "Candidato real de B");
    assert.equal(call.body.summary, "Resumen real de B");
    return {};
  });
  mock.on("GET", "/api/repair-center", []);

  await userEvent.click(screen.getByRole("button", { name: /^Enviar candidato$/i }));

  await screen.findByText(/Nada que reparar/i);
  mock.assertAllMatched();
});

test("repair center: navigating away to the dashboard and back clears the candidate draft", async () => {
  const mock = createFetchMock();
  globalThis.fetch = mock.fetch;
  const item = buildRepairItem({ work_item_id: "item-nav", title: "Tarea con candidato pendiente" });
  await openRepairCenterWith(mock, [item]);

  await userEvent.click(screen.getByRole("button", { name: /Tarea con candidato pendiente/i }));
  await userEvent.click(screen.getByRole("button", { name: /^Enviar candidato$/i }));
  await userEvent.type(screen.getByLabelText(/^Título$/i), "Borrador que nunca debe sobrevivir a la navegación");
  await userEvent.type(screen.getByLabelText(/^Resumen$/i), "Resumen que tampoco debe sobrevivir");
  await userEvent.click(screen.getByRole("button", { name: /Agregar archivo/i }));
  await userEvent.type(screen.getByLabelText(/Ruta del archivo 1/i), "draft.py");

  // Leaving via the nav (not a Repair Center control) is the path the
  // fix targets -- resetRepairDrafts() previously only ran on in-page
  // transitions (row switch, cancel, action success), never on nav-away.
  // Scoped to <nav>: the brand button's aria-label ("Ir al dashboard")
  // also matches an unanchored /Dashboard/i outside that scope.
  const mainNav = screen.getByRole("navigation", { name: /Navegación principal/i });
  await userEvent.click(within(mainNav).getByRole("button", { name: /Dashboard/i }));
  await screen.findByRole("heading", { name: "La empresa, en una mirada." });

  mock.on("GET", "/api/repair-center", [item]);
  await userEvent.click(screen.getByRole("button", { name: /Centro de reparación/i }));
  await screen.findByRole("heading", { name: /Centro de reparación/i });

  // The confirm panel itself must be closed -- confirming reset to null,
  // so the plain trigger button renders instead of the candidate form.
  assert.equal(screen.queryByLabelText(/^Título$/i), null);

  await userEvent.click(screen.getByRole("button", { name: /^Enviar candidato$/i }));
  assert.equal(screen.getByLabelText(/^Título$/i).value, "");
  assert.equal(screen.getByLabelText(/^Resumen$/i).value, "");
  assert.equal(screen.queryByLabelText(/Ruta del archivo 1/i), null);

  mock.assertAllMatched();
});

test("repair center: attempt_repair_available=false hides retry/recover/candidate but keeps escalate", async () => {
  const mock = createFetchMock();
  globalThis.fetch = mock.fetch;
  const item = buildRepairItem({
    work_item_id: "item-exhausted",
    title: "Tarea con presupuesto agotado de verdad",
    cause: "exhausted",
    status: "ready",
    attempt_count: 25,
    max_attempts: 25,
    attempt_repair_available: false,
  });
  await openRepairCenterWith(mock, [item]);

  await userEvent.click(
    screen.getByRole("button", { name: /Tarea con presupuesto agotado de verdad/i }),
  );

  await screen.findByText(/sólo se puede escalar/i);
  assert.equal(screen.queryByRole("button", { name: /^↻ Reintentar$/i }), null);
  assert.equal(screen.queryByRole("button", { name: /Recuperar artefacto/i }), null);
  assert.equal(screen.queryByRole("button", { name: /Enviar candidato/i }), null);
  screen.getByRole("button", { name: /^↑ Escalar$/i });

  mock.assertAllMatched();
});

test("repair center: a backend error retrying surfaces its detail message, row stays visible", async () => {
  const mock = createFetchMock();
  globalThis.fetch = mock.fetch;
  const item = buildRepairItem({ work_item_id: "item-error", title: "Tarea con error de API" });
  await openRepairCenterWith(mock, [item]);

  await userEvent.click(screen.getByRole("button", { name: /Tarea con error de API/i }));
  await userEvent.click(screen.getByRole("button", { name: /^↻ Reintentar$/i }));

  mock.on("POST", "/api/work-items/item-error/retry", {
    status: 409,
    body: { detail: "El presupuesto de intentos ya está en el máximo." },
  });

  await userEvent.click(screen.getByRole("button", { name: /^Confirmar$/i }));

  const alert = await screen.findByRole("alert");
  assert.match(
    within(alert).getByText(/presupuesto de intentos/i).textContent,
    /presupuesto de intentos/i,
  );
  // The row is still there -- a failed action never refreshed the list.
  screen.getByText("Tarea con error de API");
  mock.assertAllMatched();
});

test("repair center: a raw network failure surfaces a generic error, not a crash", async () => {
  const mock = createFetchMock();
  globalThis.fetch = mock.fetch;
  const item = buildRepairItem({ work_item_id: "item-network", title: "Tarea con fallo de red" });
  await openRepairCenterWith(mock, [item]);

  await userEvent.click(screen.getByRole("button", { name: /Tarea con fallo de red/i }));
  await userEvent.click(screen.getByRole("button", { name: /^↻ Reintentar$/i }));

  mock.on("POST", "/api/work-items/item-network/retry", () => {
    throw new TypeError("Failed to fetch");
  });

  await userEvent.click(screen.getByRole("button", { name: /^Confirmar$/i }));

  const alert = await screen.findByRole("alert");
  assert.match(alert.textContent, /Failed to fetch/i);
  mock.assertAllMatched();
});
