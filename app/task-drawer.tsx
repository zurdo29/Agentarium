import type { EventRecord, ProjectDetail, WorkItem } from "./page";
import { ROLE_LABELS, Status, dateLabel } from "./shared";

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
                        : "sin aislamiento registrado"}
                    </small>
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
