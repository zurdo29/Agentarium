// P3.1b -- entregable 1 de PLANS.md P3.1: flujos críticos restantes
// (aprobaciones) y "errores principales" en su forma más básica: qué
// pasa cuando el refresh de fondo del dashboard no puede conectar.

import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import React from "react";
import { cleanup, loadHomeComponent, render, screen, userEvent } from "./support/dom-setup.mjs";
import { createFetchMock } from "./support/fetch-mock.mjs";
import { buildApproval, buildDashboard } from "./support/fixtures.mjs";
import { EMPTY_APPROVALS, EMPTY_PROVIDERS, mockBackgroundRefresh, renderHome } from "./support/render-home.mjs";

afterEach(() => {
  cleanup();
});

async function openApprovals(mock, approvals) {
  mock.on("GET", "/api/dashboard", buildDashboard());
  mock.on("GET", "/api/approvals", approvals);
  mock.on("GET", "/api/providers", EMPTY_PROVIDERS);
  await renderHome();

  await userEvent.click(screen.getByRole("button", { name: /Aprobaciones/i }));
  await screen.findByRole("heading", { name: /Decisiones que no deben automatizarse/i });
}

test("approving a pending approval sends the typed comment", async () => {
  const mock = createFetchMock();
  globalThis.fetch = mock.fetch;
  const approval = buildApproval({ id: "approval-1", action: "Publicar el resultado" });
  await openApprovals(mock, [approval]);

  await screen.findByRole("heading", { name: "Publicar el resultado" });
  const commentBox = screen.getByLabelText(/Comentario para los agentes/i);
  await userEvent.type(commentBox, "Adelante, sin cambios.");

  mock.on("POST", "/api/approvals/approval-1/resolve", (call) => {
    assert.equal(call.body.status, "approved");
    assert.equal(call.body.comments, "Adelante, sin cambios.");
    return {};
  });
  mockBackgroundRefresh(mock);

  await userEvent.click(screen.getByRole("button", { name: /Aprobar acción/i }));

  await screen.findByText(/Sin decisiones pendientes/i);
  mock.assertAllMatched();
});

test("rejecting a pending approval without a comment sends the default rejection text", async () => {
  const mock = createFetchMock();
  globalThis.fetch = mock.fetch;
  const approval = buildApproval({ id: "approval-2", action: "Borrar el workspace anterior" });
  await openApprovals(mock, [approval]);

  await screen.findByRole("heading", { name: "Borrar el workspace anterior" });

  mock.on("POST", "/api/approvals/approval-2/resolve", (call) => {
    assert.equal(call.body.status, "rejected");
    // resolveApproval() in app/page.tsx falls back to this exact copy
    // when the comment field is left blank -- read before writing this
    // assertion, not guessed.
    assert.equal(call.body.comments, "Rechazado desde el centro de control.");
    return {};
  });
  mockBackgroundRefresh(mock);

  await userEvent.click(screen.getByRole("button", { name: /Rechazar/i }));

  await screen.findByText(/Sin decisiones pendientes/i);
  mock.assertAllMatched();
});

test("pending approvals show a count badge in the sidebar nav", async () => {
  const mock = createFetchMock();
  globalThis.fetch = mock.fetch;
  mock.on("GET", "/api/dashboard", buildDashboard());
  mock.on(
    "GET",
    "/api/approvals",
    [buildApproval({ id: "a1" }), buildApproval({ id: "a2" }), buildApproval({ id: "a3", status: "approved" })],
  );
  mock.on("GET", "/api/providers", EMPTY_PROVIDERS);
  await renderHome();

  // Only 2 of the 3 approvals are "pending" -- the badge counts pending
  // ones specifically (`approvals.filter(a => a.status === "pending")`
  // in app/page.tsx), not the raw list length.
  const approvalsNav = screen.getByRole("button", { name: /Aprobaciones/i });
  assert.match(approvalsNav.textContent, /2/);
});

test("when the background refresh fails, the dashboard shows the offline/demo state", async () => {
  const mock = createFetchMock();
  globalThis.fetch = mock.fetch;
  mock.on("GET", "/api/dashboard", () => {
    throw new TypeError("Failed to fetch");
  });
  mock.on("GET", "/api/approvals", EMPTY_APPROVALS);
  mock.on("GET", "/api/providers", EMPTY_PROVIDERS);

  const Home = await loadHomeComponent();
  render(React.createElement(Home));

  // refreshDashboard() catches any failure and sets connected=false
  // without surfacing an `error` banner (read in app/page.tsx: this
  // specific background call has no catch->setError, unlike the
  // interactive flows) -- the only observable signal is the sidebar
  // staying on its demo-state copy.
  await screen.findByText("Modo demostración");
  assert.equal(screen.queryByText("Workspace activo"), null);
  assert.equal(screen.queryByText("Simulación activa"), null);
});
