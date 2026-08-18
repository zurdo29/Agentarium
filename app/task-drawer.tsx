import type { AgentRun, EventRecord, ProjectDetail, WorkItem } from "./page";
import { ROLE_LABELS, Status, dateLabel } from "./shared";

// P4.4b: RunOutcome vocabulary (backend/agentarium/domain/enums.py) --
// distinct from WorkItemStatus/DeliveryReportWorkItem's outcome, so this
// stays a small local map rather than growing shared.tsx's STATUS_LABELS
// with terms that don't belong to that vocabulary.
const AGENT_RUN_OUTCOME_LABELS: Record<string, string> = {
  artifact_delivered: "Artefacto entregado",
  needs_information: "Necesita información",
  blocked: "Bloqueado",
  approval_requested: "Pidió aprobación",
  escalated: "Escalado",
  rejected: "Rechazado",
};

// queue_wait_ms/generation_ms are a real ResourceUsage | null pair (P1.3a
// split queue time from generation time; not every provider path fills
// both) -- "sin dato" is a real, distinct value from "0ms", never
// collapsed into it.
function formatMs(ms: number): string {
  return `${ms}ms`;
}

function formatMsOrNull(ms: number | null): string {
  return ms === null ? "sin dato" : formatMs(ms);
}

export function TaskDrawer({
  item,
  detail,
  events,
  onClose,
  onRetry,
  onRework,
  onEscalate,
  onPriority,
  loading,
  confirmingAction,
  onStartConfirm,
  onCancelConfirm,
  reworkReason,
  onReworkReasonChange,
  escalateReason,
  onEscalateReasonChange,
  agentRuns,
  agentRunsLoading,
  onLoadAgentRuns,
}: {
  item: WorkItem;
  detail: ProjectDetail;
  events: EventRecord[];
  onClose: () => void;
  onRetry: (id: string) => Promise<void>;
  onRework: (id: string) => Promise<void>;
  onEscalate: (id: string) => Promise<void>;
  onPriority: (id: string, priority: number) => Promise<void>;
  loading: boolean;
  confirmingAction: "retry" | "rework" | "escalate" | null;
  onStartConfirm: (action: "retry" | "rework" | "escalate") => void;
  onCancelConfirm: () => void;
  reworkReason: string;
  onReworkReasonChange: (value: string) => void;
  escalateReason: string;
  onEscalateReasonChange: (value: string) => void;
  agentRuns: AgentRun[] | null;
  agentRunsLoading: boolean;
  onLoadAgentRuns: () => void;
}) {
  const dependencies = item.dependency_ids
    .map((id) => detail.work_items.find((candidate) => candidate.id === id))
    .filter(Boolean) as WorkItem[];
  const artifacts = detail.artifacts.filter(
    (artifact) => artifact.work_item_id === item.id,
  );
  const reviews = detail.reviews.filter(
    (review) => review.work_item_id === item.id,
  );
  const reports = detail.test_reports.filter(
    (report) => report.work_item_id === item.id,
  );
  const canRetry = ["failed", "changes_requested"].includes(item.status);
  const taskEvents = events.filter((event) => event.work_item_id === item.id);
  const itemRuns = agentRuns?.filter((run) => run.work_item_id === item.id) ?? [];
  const priorities = Array.from(new Set([item.priority, 25, 50, 75, 100])).sort(
    (a, b) => a - b,
  );

  return (
    <div className="drawer-backdrop" onMouseDown={onClose}>
      <aside
        className="task-drawer"
        onMouseDown={(event) => event.stopPropagation()}
        aria-label={`Detalle de ${item.title}`}
      >
        <button className="drawer-close" onClick={onClose} aria-label="Cerrar">
          ×
        </button>
        <span className="kicker">TAREA / {item.id.slice(0, 8)}</span>
        <h2>{item.title}</h2>
        <div className="drawer-status-line">
          <Status status={item.status} />
          <span>{ROLE_LABELS[item.assignee_role]}</span>
          <span>prioridad {item.priority}</span>
        </div>
        <p className="drawer-description">{item.description}</p>

        <section className="drawer-section">
          <span className="micro-label">Criterios de aceptación</span>
          <ul className="check-list">
            {item.acceptance_criteria.map((criterion) => (
              <li key={criterion}>
                <span>
                  {item.status === "completed" || item.status === "passed"
                    ? "✓"
                    : "·"}
                </span>
                {criterion}
              </li>
            ))}
          </ul>
        </section>

        <section className="drawer-section drawer-grid">
          <div>
            <span className="micro-label">Dependencias</span>
            <strong>{dependencies.length}</strong>
            <small>
              {dependencies.map((dependency) => dependency.title).join(", ") ||
                "Ninguna"}
            </small>
          </div>
          <div>
            <span className="micro-label">Intentos</span>
            <strong>
              {item.attempt_count}/{item.max_attempts}
            </strong>
            <small>presupuesto acotado</small>
          </div>
          <div>
            <span className="micro-label">Riesgo</span>
            <strong>{item.risk}</strong>
            <small>política local</small>
          </div>
        </section>

        <section className="drawer-section">
          <span className="micro-label">Artefactos y evidencia</span>
          {artifacts.map((artifact) => (
            <div className="drawer-evidence" key={artifact.id}>
              <span className="file-mark">
                {artifact.file_paths.some((path) =>
                  path.replaceAll("\\", "/").includes("/project/"),
                )
                  ? "FILE"
                  : "JSON"}
              </span>
              <div>
                <strong>{artifact.title}</strong>
                <small>
                  {artifact.file_paths.find((path) =>
                    path.replaceAll("\\", "/").includes("/project/"),
                  ) ?? artifact.file_paths[0]}
                </small>
              </div>
            </div>
          ))}
          {artifacts.length === 0 && (
            <p className="empty-copy">Aún no existe un artefacto.</p>
          )}
        </section>

        <section className="drawer-section">
          <span className="micro-label">Controles independientes</span>
          {[...reports, ...reviews].map((result) => (
            <div className="review-result" key={result.id}>
              {"passed" in result ? (
                <>
                  <span className={result.passed ? "result-pass" : "result-fail"}>
                    {result.passed ? "PASS" : "FAIL"}
                  </span>
                  <div>
                    <strong>Tester</strong>
                    <p>{result.summary}</p>
                    <small>
                      {result.command_evidence?.filter(
                        (evidence) => evidence.check === "validation_profile",
                      ).length ?? 0}{" "}
                      perfiles registrados ·{" "}
                      {result.command_evidence?.some(
                        (evidence) =>
                          evidence.check === "isolated_change_set" &&
                          evidence.verified,
                      )
                        ? "worktree verificado"
                        : "sin aislamiento registrado"} ·{" "}
                      {result.verification_mode === "static_only"
                        ? "código no ejecutado"
                        : "código ejecutado"}
                    </small>
                    {/* Gate-MVP.2 (ADR 0041): only the non-empty lists --
                        one entry per delivered .py file is persisted,
                        empty ones included, but a block that is almost
                        always empty trains the reader to skip it. Never a
                        rejection: a refactor may remove names legitimately. */}
                    {(() => {
                      const removed = (result.command_evidence ?? []).filter(
                        (evidence) =>
                          evidence.check === "removed_top_level_names" &&
                          (evidence.removed?.length ?? 0) > 0,
                      );
                      if (removed.length === 0) return null;
                      return (
                        <div className="delivery-report-removed-names">
                          <span className="micro-label">
                            Definiciones eliminadas respecto de la base
                          </span>
                          <ul>
                            {removed.map((evidence) => (
                              <li key={evidence.path}>
                                <code>{evidence.path}</code>
                                <span>{(evidence.removed ?? []).join(", ")}</span>
                              </li>
                            ))}
                          </ul>
                        </div>
                      );
                    })()}
                  </div>
                </>
              ) : (
                <>
                  <span
                    className={
                      result.verdict === "approved"
                        ? "result-pass"
                        : "result-fail"
                    }
                  >
                    {result.verdict === "approved" ? "PASS" : "REVIEW"}
                  </span>
                  <div>
                    <strong>Revisor crítico</strong>
                    <p>{result.reasons.join(" ")}</p>
                  </div>
                </>
              )}
            </div>
          ))}
        </section>

        <section className="drawer-section">
          <span className="micro-label">Historial de intentos</span>
          {agentRuns === null ? (
            <button
              type="button"
              className="control-button"
              onClick={onLoadAgentRuns}
              disabled={agentRunsLoading}
            >
              {agentRunsLoading ? "Cargando…" : "Ver historial de intentos"}
            </button>
          ) : (
            <div className="agent-run-list">
              {itemRuns.map((run) => (
                <div className="agent-run-item" key={run.id}>
                  <span className="agent-run-outcome">
                    {AGENT_RUN_OUTCOME_LABELS[run.outcome] ?? run.outcome}
                  </span>
                  <div>
                    <strong>
                      {ROLE_LABELS[run.agent_role] ?? run.agent_role} · intento{" "}
                      {run.attempt}
                    </strong>
                    <small>
                      {run.model} ({run.provider}) · {dateLabel(run.started_at)}
                    </small>
                    <small className="agent-run-usage">
                      {formatMs(run.resource_usage.duration_ms)} totales · cola{" "}
                      {formatMsOrNull(run.resource_usage.queue_wait_ms)} · generación{" "}
                      {formatMsOrNull(run.resource_usage.generation_ms)} ·{" "}
                      {run.resource_usage.errors} error(es)
                    </small>
                    {run.error && <small className="agent-run-error">{run.error}</small>}
                  </div>
                </div>
              ))}
              {itemRuns.length === 0 && (
                <p className="empty-copy">Sin intentos registrados para esta tarea.</p>
              )}
            </div>
          )}
        </section>

        <section className="drawer-section">
          <span className="micro-label">Log de ejecución</span>
          <div className="drawer-log">
            {[...taskEvents].reverse().slice(0, 6).map((event) => (
              <div key={event.id}>
                <time>{dateLabel(event.timestamp)}</time>
                <span>{event.message}</span>
              </div>
            ))}
            {taskEvents.length === 0 && (
              <p className="empty-copy">Aún no hay eventos para esta tarea.</p>
            )}
          </div>
        </section>

        {item.last_error && (
          <div className="drawer-error">
            <span className="micro-label">Último error</span>
            <p>{item.last_error}</p>
          </div>
        )}

        <div className="drawer-controls">
          <label>
            Prioridad
            <select
              value={item.priority}
              onChange={(event) =>
                void onPriority(item.id, Number(event.target.value))
              }
              disabled={loading}
            >
              {priorities.map((priority) => (
                <option key={priority} value={priority}>
                  {priority}
                </option>
              ))}
            </select>
          </label>
          {confirmingAction === "escalate" ? (
            <div className="drawer-confirm">
              <label>
                Motivo de la escalación
                <textarea
                  value={escalateReason}
                  onChange={(event) => onEscalateReasonChange(event.target.value)}
                  placeholder="¿Por qué necesita esto una decisión humana?"
                  rows={2}
                />
              </label>
              <div className="drawer-confirm-actions">
                <button
                  className="control-button"
                  onClick={onCancelConfirm}
                  disabled={loading}
                >
                  Cancelar
                </button>
                <button
                  className="primary-button"
                  onClick={() => void onEscalate(item.id)}
                  disabled={loading || !escalateReason.trim()}
                >
                  Confirmar escalación
                </button>
              </div>
            </div>
          ) : (
            <button
              className="control-button"
              onClick={() => onStartConfirm("escalate")}
              disabled={
                loading || ["completed", "cancelled"].includes(item.status)
              }
            >
              ↑ Escalar
            </button>
          )}

          {canRetry &&
            (confirmingAction === "retry" ? (
              <div className="drawer-confirm">
                <span>¿Reintentar esta tarea?</span>
                <div className="drawer-confirm-actions">
                  <button
                    className="control-button"
                    onClick={onCancelConfirm}
                    disabled={loading}
                  >
                    Cancelar
                  </button>
                  <button
                    className="primary-button"
                    onClick={() => void onRetry(item.id)}
                    disabled={loading}
                  >
                    Confirmar
                  </button>
                </div>
              </div>
            ) : (
              <button
                className="primary-button"
                onClick={() => onStartConfirm("retry")}
                disabled={loading}
              >
                ↻ Reintentar
              </button>
            ))}

          {item.status === "completed" &&
            (confirmingAction === "rework" ? (
              <div className="drawer-confirm">
                <label>
                  Motivo de la revisión
                  <textarea
                    value={reworkReason}
                    onChange={(event) => onReworkReasonChange(event.target.value)}
                    placeholder="¿Qué hay que corregir?"
                    rows={2}
                  />
                </label>
                <div className="drawer-confirm-actions">
                  <button
                    className="control-button"
                    onClick={onCancelConfirm}
                    disabled={loading}
                  >
                    Cancelar
                  </button>
                  <button
                    className="primary-button"
                    onClick={() => void onRework(item.id)}
                    disabled={loading || !reworkReason.trim()}
                  >
                    Confirmar revisión
                  </button>
                </div>
              </div>
            ) : (
              <button
                className="primary-button"
                onClick={() => onStartConfirm("rework")}
                disabled={loading}
              >
                ↻ Revisar de nuevo
              </button>
            ))}
        </div>
      </aside>
    </div>
  );
}
