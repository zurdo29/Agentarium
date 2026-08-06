// Shared setup used by every home-*.test.mjs file: register the
// background-refresh routes every render triggers on mount, then wait
// for evidence that refresh actually landed before interacting further.
// Factored out after the first test file needed this pattern more than
// once and a bug turned up in one copy -- one place to get it right.

import React from "react";
import { loadHomeComponent, render, screen } from "./dom-setup.mjs";
import { buildDashboard } from "./fixtures.mjs";

export const EMPTY_APPROVALS = [];
export const EMPTY_PROVIDERS = { active_provider: "mock", active_model: null, providers: [] };

export function mockBackgroundRefresh(mock, dashboard = buildDashboard()) {
  mock.on("GET", "/api/dashboard", dashboard);
  mock.on("GET", "/api/approvals", EMPTY_APPROVALS);
  mock.on("GET", "/api/providers", EMPTY_PROVIDERS);
}

export async function renderHome() {
  const Home = await loadHomeComponent();
  render(React.createElement(Home));
  // "Ir al dashboard" exists on the very first render regardless of any
  // fetch (found by using it as the wait condition initially: it doesn't
  // wait for anything). `connected` only flips to true, flipping the
  // sidebar's status line from "Modo demostración" to this exact text,
  // once the mocked /api/dashboard + /api/approvals + /api/providers have
  // actually resolved and been applied -- the real signal this needs. An
  // exact-string matcher, not a substring regex: a longer RuntimeBanner
  // headline elsewhere on the page also *contains* "Workspace activo" as
  // a substring (found by running this).
  await screen.findByText(
    (content) => content === "Workspace activo" || content === "Simulación activa",
  );
}
