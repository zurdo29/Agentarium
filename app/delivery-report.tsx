import type { DeliveryReport, DeliveryReportOutcome, DeliveryReportWorkItem } from "./page";
import { PanelHeading, Status, statusLabel } from "./shared";

// Presentational only, same acyclic pattern as export-project.tsx/
// repair-center.tsx: imports types from ./page, helpers from ./shared,
// zero own state -- page.tsx owns the fetched report, the loading flag
// and which row is expanded, so a stale report/expansion never survives
// a project switch (see openProject()'s reset in page.tsx).
const OUTCOME_HINT: Record<DeliveryReportOutcome, string> = {
  completed: "Completada con evidencia verificada.",
  changes_requested: "El revisor crítico pidió cambios.",
  failed: "El último intento falló.",
  exhausted: "Se agotó el presupuesto de intentos.",
  blocked: "Bloqueada por una dependencia real.",
  cancelled: "Cancelada.",
  superseded_by_split: "Reemplazada por una división en subtareas.",
  awaiting_approval: "Espera una decisión humana.",
  in_progress: "Todavía en curso.",
};

// Gate-MVP.2 (ADR 0041): "evidencia verificada" is exactly the overclaim
// this gate exists to prevent when nothing was ever executed -- the normal
// case for an imported project. The outcome alone cannot carry that claim.
// Three states, not two: `null` (no TestReport at all) is a third,
// distinct fact -- absence of evidence, which must never borrow the
// wording of either verified execution or a real static check.
function outcomeHint(item: DeliveryReportWorkItem): string {
  if (item.outcome !== "completed") {
    return OUTCOME_HINT[item.outcome];
  }
  if (item.test_verification_mode === "static_only") {
    return "Completada con verificación estática; el código no se ejecutó.";
  }
  if (item.test_verification_mode === null) {
    return "Completada sin informe técnico disponible.";
  }
  return OUTCOME_HINT.completed;
}

// Why an item counted as completed still has no usable verification --
// two structurally different facts that must not read the same.
const UNVERIFIED_REASON_LABEL: Record<string, string> = {
  static_only_verification: "código no ejecutado",
  missing_review_or_test_report: "sin review o informe técnico",
};

export function DeliveryReportView({
  report,
  loading,
  onLoad,
  connected,
  expandedId,
  onToggleExpand,
}: {
  report: DeliveryReport | null;
  loading: boolean;
  onLoad: () => Promise<void>;
  connected: boolean;
  expandedId: string | null;
  onToggleExpand: (workItemId: string) => void;
}) {
  const totals = report ? Object.entries(report.totals) : [];

  return (
    <section className="panel project-delivery-report">
      <PanelHeading
        index="07"
        eyebrow="Entrega y auditoría"
        title="Qué se pidió, qué cambió y qué quedó sin verificar"
        trailing={report ? report.work_items.length.toString() : undefined}
      />

      {report?.project.brief && (
        <p className="delivery-report-goal">{report.project.brief.summary}</p>
      )}

      {report && report.unverified_completed_items.length > 0 && (
        <div className="delivery-report-warning" role="alert">
          <span className="alert-icon">!</span>
          <div>
            <strong>
              {report.unverified_completed_items.length} ítem(s) completados sin
              evidencia verificable
            </strong>
            {/* The reason is the whole point: "código no ejecutado" is the
                expected, honest state of an imported project, while "sin
                review o informe técnico" is a data-integrity problem. A
                joined list of titles hid that difference entirely. */}
            <ul className="delivery-report-unverified-list">
              {report.unverified_completed_items.map((item) => (
                <li key={item.work_item_id}>
                  <strong>{item.title}</strong>
                  <span>
                    {UNVERIFIED_REASON_LABEL[item.reason] ?? item.reason}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}

      {totals.length > 0 && (
        <div className="delivery-report-totals">
          {totals.map(([outcome, count]) => (
            <div key={outcome}>
              <strong>{count}</strong>
              <span>{statusLabel(outcome)}</span>
            </div>
          ))}
        </div>
      )}

      {report && report.work_items.length > 0 && (
        <div className="delivery-report-list">
          {report.work_items.map((item) => (
            <DeliveryReportRow
              key={item.work_item_id}
              item={item}
              expanded={expandedId === item.work_item_id}
              onToggle={() => onToggleExpand(item.work_item_id)}
            />
          ))}
        </div>
      )}

      {report && report.work_items.length === 0 && (
        <p className="empty-copy">Este proyecto todavía no tiene tareas.</p>
      )}

      {!report && !loading && (
        <p className="empty-copy">
          Generá el informe para ver el estado acumulado del proyecto.
        </p>
      )}

      <div className="delivery-report-footer">
        <span>
          {connected
            ? "El informe refleja el estado acumulado del proyecto -- generalo de nuevo después de reparar o reintentar algo."
            : "Inicia el backend para generar el informe de un proyecto real."}
        </span>
        <button
          type="button"
          className="control-button"
          onClick={() => void onLoad()}
          disabled={loading}
        >
          {loading ? "Generando…" : report ? "Actualizar informe" : "Generar informe"}
        </button>
      </div>
    </section>
  );
}

function DeliveryReportRow({
  item,
  expanded,
  onToggle,
}: {
  item: DeliveryReportWorkItem;
  expanded: boolean;
  onToggle: () => void;
}) {
  const acceptanceEntries = Object.entries(item.review_acceptance_results);
  // Same computed summary TaskDrawer already derives from the identical
  // command_evidence shape (app/task-drawer.tsx's "Controles independientes"
  // section) -- kept as a local computation here rather than a shared
  // helper, matching this file's own zero-shared-logic precedent.
  const validationProfileCount = item.test_command_evidence.filter(
    (evidence) => evidence.check === "validation_profile",
  ).length;
  const worktreeVerified = item.test_command_evidence.some(
    (evidence) => evidence.check === "isolated_change_set" && evidence.verified,
  );
  // Gate-MVP.2 (ADR 0041): only the non-empty lists. The Orchestrator
  // persists one entry per delivered .py file, empty ones included (so the
  // evidence trail can tell "checked, nothing removed" from "never
  // checked") -- but the normal case is nothing removed, and a block that
  // is always present and almost always empty trains the reader to skip
  // it. Never a rejection: a refactor may remove names legitimately.
  const removedTopLevelNames = item.test_command_evidence.filter(
    (evidence) =>
      evidence.check === "removed_top_level_names" &&
      (evidence.removed?.length ?? 0) > 0,
  );

  return (
    <article className="delivery-report-card">
      <button
        type="button"
        className="delivery-report-card-summary"
        onClick={onToggle}
      >
        <Status status={item.outcome} />
        <div>
          <strong>{item.title}</strong>
          <small>
            {item.attempt_count}/{item.max_attempts} intentos
          </small>
        </div>
        {/* Evidence stays visible in the summary row for every outcome,
            "completed" included -- a completed item's test result is
            exactly what makes it trustworthy, not just the exceptions. */}
        {item.test_passed !== null && (
          <span className={item.test_passed ? "result-pass" : "result-fail"}>
            {item.test_passed ? "PASS" : "FAIL"}
          </span>
        )}
      </button>

      {expanded && (
        <div className="delivery-report-card-detail">
          <p>{outcomeHint(item)}</p>

          {item.review_verdict && (
            <div className="review-result">
              <span
                className={
                  item.review_verdict === "approved" ? "result-pass" : "result-fail"
                }
              >
                {item.review_verdict === "approved" ? "PASS" : "REVIEW"}
              </span>
              <div>
                <strong>Revisor crítico</strong>
                {item.review_reasons.length > 0 && <p>{item.review_reasons.join(" ")}</p>}
                {acceptanceEntries.length > 0 && (
                  <small>
                    {acceptanceEntries.filter(([, passed]) => passed).length}/
                    {acceptanceEntries.length} criterios de aceptación cumplidos
                  </small>
                )}
              </div>
            </div>
          )}

          {item.test_summary && (
            <div className="review-result">
              <span className={item.test_passed ? "result-pass" : "result-fail"}>
                {item.test_passed ? "PASS" : "FAIL"}
              </span>
              <div>
                <strong>Tester</strong>
                <p>{item.test_summary}</p>
                {item.test_checks.length > 0 && (
                  <ul className="delivery-report-check-list">
                    {item.test_checks.map((check, index) => (
                      <li key={`${check.name}-${index}`}>
                        <span className={check.passed ? "result-pass" : "result-fail"}>
                          {check.passed ? "PASS" : "FAIL"}
                        </span>
                        <strong>{check.name}</strong>
                        <span>{check.evidence}</span>
                      </li>
                    ))}
                  </ul>
                )}
                {item.test_command_evidence.length > 0 && (
                  <small>
                    {validationProfileCount} perfiles registrados ·{" "}
                    {worktreeVerified ? "worktree verificado" : "sin aislamiento registrado"} ·{" "}
                    {item.test_verification_mode === "static_only"
                      ? "código no ejecutado"
                      : item.test_verification_mode === "executed"
                        ? "código ejecutado"
                        : "modo de verificación desconocido"}
                  </small>
                )}
                {removedTopLevelNames.length > 0 && (
                  <div className="delivery-report-removed-names">
                    <span className="micro-label">
                      Definiciones eliminadas respecto de la base
                    </span>
                    <ul>
                      {removedTopLevelNames.map((evidence) => (
                        <li key={evidence.path}>
                          <code>{evidence.path}</code>
                          <span>{(evidence.removed ?? []).join(", ")}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            </div>
          )}

          {item.integration_commit && (
            <div className="delivery-report-integration">
              <span className="micro-label">Integrado</span>
              <p>
                {item.integration_branch ?? "sin rama"} ·{" "}
                {item.integration_commit.slice(0, 8)}
              </p>
              {item.integration_files.map((path) => (
                <p key={path}>{path}</p>
              ))}
            </div>
          )}

          {item.blocking_dependency_title && (
            <p className="repair-blocked-note">
              Bloqueada por <strong>{item.blocking_dependency_title}</strong>.
            </p>
          )}

          {!item.review_verdict && !item.test_summary && (
            <p className="empty-copy">Sin evidencia registrada todavía.</p>
          )}
        </div>
      )}
    </article>
  );
}
