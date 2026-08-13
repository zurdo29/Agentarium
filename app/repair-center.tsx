import type { CandidateFileDraft, ProjectDetail, RepairItem } from "./page";
import { CandidateSubmission } from "./candidate-submission";
import { Status, dateLabel } from "./shared";

const CAUSE_LABELS: Record<RepairItem["cause"], string> = {
  failed: "Fallida",
  changes_requested: "Cambios solicitados",
  exhausted: "Presupuesto agotado",
  blocked: "Bloqueada",
};

type RepairAction = "retry" | "recover" | "escalate" | "candidate";

// Presentational only, same acyclic pattern as export-project.tsx/
// import-project.tsx: imports types from ./page, helpers from ./shared,
// zero own state -- page.tsx owns filters/expansion/confirm-panel/draft
// state so resetRepairDrafts() can clear it centrally from one place.
export function RepairCenter({
  items,
  loading,
  connected,
  filterProject,
  onFilterProjectChange,
  filterCause,
  onFilterCauseChange,
  expandedId,
  onToggleExpand,
  onOpenItem,
  confirming,
  onStartConfirm,
  onCancelConfirm,
  onRetry,
  escalateReason,
  onEscalateReasonChange,
  onEscalate,
  recoverArtifacts,
  recoverArtifactsLoading,
  selectedArtifactId,
  onSelectArtifact,
  onRecover,
  candidateTitle,
  onCandidateTitleChange,
  candidateSummary,
  onCandidateSummaryChange,
  candidateFiles,
  onCandidateFilesChange,
  onSubmitCandidate,
}: {
  items: RepairItem[];
  loading: boolean;
  connected: boolean;
  filterProject: string;
  onFilterProjectChange: (value: string) => void;
  filterCause: string;
  onFilterCauseChange: (value: string) => void;
  expandedId: string | null;
  onToggleExpand: (workItemId: string) => void;
  onOpenItem: (projectId: string, workItemId: string) => void;
  confirming: { workItemId: string; action: RepairAction } | null;
  onStartConfirm: (item: RepairItem, action: RepairAction) => void;
  onCancelConfirm: () => void;
  onRetry: (workItemId: string) => Promise<void>;
  escalateReason: string;
  onEscalateReasonChange: (value: string) => void;
  onEscalate: (workItemId: string) => Promise<void>;
  recoverArtifacts: ProjectDetail["artifacts"] | null;
  recoverArtifactsLoading: boolean;
  selectedArtifactId: string;
  onSelectArtifact: (id: string) => void;
  onRecover: (workItemId: string) => Promise<void>;
  candidateTitle: string;
  onCandidateTitleChange: (value: string) => void;
  candidateSummary: string;
  onCandidateSummaryChange: (value: string) => void;
  candidateFiles: CandidateFileDraft[];
  onCandidateFilesChange: (files: CandidateFileDraft[]) => void;
  onSubmitCandidate: (workItemId: string) => Promise<void>;
}) {
  const projects = Array.from(
    new Map(items.map((item) => [item.project_id, item.project_title])).entries(),
  );
  const filtered = items.filter(
    (item) =>
      (!filterProject || item.project_id === filterProject) &&
      (!filterCause || item.cause === filterCause),
  );
  const candidateFilesComplete = candidateFiles.filter(
    (file) => file.path.trim() && file.content.trim() && file.purpose.trim(),
  );
  const canSubmitCandidate =
    candidateTitle.trim().length > 0 &&
    candidateSummary.trim().length > 0 &&
    candidateFilesComplete.length > 0;

  return (
    <div className="page repair-page">
      <section className="page-heading">
        <div>
          <span className="kicker">CAUSA ESTRUCTURADA</span>
          <h1>Centro de reparación</h1>
          <p>
            Tareas que necesitan una decisión o una acción humana, con la
            evidencia justa para entenderlas.
          </p>
        </div>
      </section>

      <div className="repair-filters">
        <label>
          Proyecto
          <select
            value={filterProject}
            onChange={(event) => onFilterProjectChange(event.target.value)}
          >
            <option value="">Todos</option>
            {projects.map(([id, title]) => (
              <option key={id} value={id}>
                {title}
              </option>
            ))}
          </select>
        </label>
        <label>
          Causa
          <select
            value={filterCause}
            onChange={(event) => onFilterCauseChange(event.target.value)}
          >
            <option value="">Todas</option>
            <option value="failed">Fallida</option>
            <option value="changes_requested">Cambios solicitados</option>
            <option value="exhausted">Presupuesto agotado</option>
            <option value="blocked">Bloqueada</option>
          </select>
        </label>
      </div>

      {loading && <p className="empty-copy">Cargando…</p>}

      {!loading && connected && filtered.length === 0 && (
        <div className="empty-state">
          <span>✓</span>
          <h2>Nada que reparar</h2>
          <p>Ningún work item necesita intervención en este momento.</p>
        </div>
      )}

      {!loading && !connected && items.length === 0 && (
        <div className="offline-explainer">
          <span className="alert-icon">i</span>
          <div>
            <strong>No hay API conectada</strong>
            <p>Cuando haya tareas fallidas o bloqueadas, aparecerán acá.</p>
          </div>
        </div>
      )}

      <div className="repair-list">
        {filtered.map((item) => {
          const expanded = expandedId === item.work_item_id;
          const isConfirming =
            confirming?.workItemId === item.work_item_id ? confirming.action : null;
          return (
            <article className="repair-card" key={item.work_item_id}>
              <button
                type="button"
                className="repair-card-summary"
                onClick={() => onToggleExpand(item.work_item_id)}
              >
                <Status status={item.status} />
                <div>
                  <strong>{item.title}</strong>
                  <small>{item.project_title}</small>
                </div>
                <span className="repair-cause-pill">{CAUSE_LABELS[item.cause]}</span>
                <span className="repair-attempts">
                  {item.attempt_count}/{item.max_attempts}
                </span>
              </button>

              {expanded && (
                <div className="repair-card-detail">
                  <p>
                    {item.last_error
                      ? item.last_error
                      : item.latest_review_reasons.length > 0
                        ? item.latest_review_reasons.join(" ")
                        : (item.latest_test_summary ??
                          "Sin evidencia adicional registrada.")}
                  </p>

                  {item.cause === "blocked" ? (
                    <div className="repair-blocked-note">
                      <p>
                        Bloqueada por{" "}
                        <strong>{item.blocking_dependency_title}</strong> (
                        {item.blocking_dependency_status}).
                      </p>
                      <button
                        type="button"
                        className="control-button"
                        onClick={() =>
                          item.blocking_dependency_id &&
                          onOpenItem(item.project_id, item.blocking_dependency_id)
                        }
                      >
                        Abrir la dependencia bloqueante
                      </button>
                    </div>
                  ) : (
                    <>
                      <button
                        type="button"
                        className="control-button"
                        onClick={() => onOpenItem(item.project_id, item.work_item_id)}
                      >
                        Abrir en el proyecto
                      </button>

                      {!item.attempt_repair_available && (
                        <p className="repair-budget-note">
                          Presupuesto de intentos agotado ({item.attempt_count}/
                          {item.max_attempts}) -- sólo se puede escalar.
                        </p>
                      )}

                      <div className="repair-actions">
                        {item.attempt_repair_available &&
                          (isConfirming === "retry" ? (
                            <div className="drawer-confirm">
                              <span>¿Reintentar esta tarea?</span>
                              <div className="drawer-confirm-actions">
                                <button
                                  type="button"
                                  className="control-button"
                                  onClick={onCancelConfirm}
                                  disabled={loading}
                                >
                                  Cancelar
                                </button>
                                <button
                                  type="button"
                                  className="primary-button"
                                  onClick={() => void onRetry(item.work_item_id)}
                                  disabled={loading}
                                >
                                  Confirmar
                                </button>
                              </div>
                            </div>
                          ) : (
                            <button
                              type="button"
                              className="primary-button"
                              onClick={() => onStartConfirm(item, "retry")}
                              disabled={loading}
                            >
                              ↻ Reintentar
                            </button>
                          ))}

                        {item.attempt_repair_available &&
                          (isConfirming === "recover" ? (
                            <div className="drawer-confirm">
                              <label>
                                Artefacto a recuperar
                                {recoverArtifactsLoading ? (
                                  <span>Cargando artefactos…</span>
                                ) : (
                                  <select
                                    value={selectedArtifactId}
                                    onChange={(event) =>
                                      onSelectArtifact(event.target.value)
                                    }
                                  >
                                    <option value="">Elegí un artefacto</option>
                                    {(recoverArtifacts ?? [])
                                      .filter(
                                        (artifact) =>
                                          artifact.work_item_id === item.work_item_id,
                                      )
                                      .map((artifact) => (
                                        <option key={artifact.id} value={artifact.id}>
                                          {artifact.title} (
                                          {dateLabel(artifact.created_at)})
                                        </option>
                                      ))}
                                  </select>
                                )}
                              </label>
                              <div className="drawer-confirm-actions">
                                <button
                                  type="button"
                                  className="control-button"
                                  onClick={onCancelConfirm}
                                  disabled={loading}
                                >
                                  Cancelar
                                </button>
                                <button
                                  type="button"
                                  className="primary-button"
                                  onClick={() => void onRecover(item.work_item_id)}
                                  disabled={loading || !selectedArtifactId}
                                >
                                  Confirmar recuperación
                                </button>
                              </div>
                            </div>
                          ) : (
                            <button
                              type="button"
                              className="control-button"
                              onClick={() => onStartConfirm(item, "recover")}
                              disabled={loading}
                            >
                              Recuperar artefacto
                            </button>
                          ))}

                        {isConfirming === "escalate" ? (
                          <div className="drawer-confirm">
                            <label>
                              Motivo de la escalación
                              <textarea
                                value={escalateReason}
                                onChange={(event) =>
                                  onEscalateReasonChange(event.target.value)
                                }
                                rows={2}
                                placeholder="¿Por qué necesita esto una decisión humana?"
                              />
                            </label>
                            <div className="drawer-confirm-actions">
                              <button
                                type="button"
                                className="control-button"
                                onClick={onCancelConfirm}
                                disabled={loading}
                              >
                                Cancelar
                              </button>
                              <button
                                type="button"
                                className="primary-button"
                                onClick={() => void onEscalate(item.work_item_id)}
                                disabled={loading || !escalateReason.trim()}
                              >
                                Confirmar escalación
                              </button>
                            </div>
                          </div>
                        ) : (
                          <button
                            type="button"
                            className="control-button"
                            onClick={() => onStartConfirm(item, "escalate")}
                            disabled={loading}
                          >
                            ↑ Escalar
                          </button>
                        )}

                        {item.attempt_repair_available &&
                          (isConfirming === "candidate" ? (
                            <div className="drawer-confirm">
                              <CandidateSubmission
                                title={candidateTitle}
                                onTitleChange={onCandidateTitleChange}
                                summary={candidateSummary}
                                onSummaryChange={onCandidateSummaryChange}
                                files={candidateFiles}
                                onFilesChange={onCandidateFilesChange}
                              />
                              <div className="drawer-confirm-actions">
                                <button
                                  type="button"
                                  className="control-button"
                                  onClick={onCancelConfirm}
                                  disabled={loading}
                                >
                                  Cancelar
                                </button>
                                <button
                                  type="button"
                                  className="primary-button"
                                  onClick={() => void onSubmitCandidate(item.work_item_id)}
                                  disabled={loading || !canSubmitCandidate}
                                >
                                  Enviar candidato
                                </button>
                              </div>
                            </div>
                          ) : (
                            <button
                              type="button"
                              className="control-button"
                              onClick={() => onStartConfirm(item, "candidate")}
                              disabled={loading}
                            >
                              Enviar candidato
                            </button>
                          ))}
                      </div>
                    </>
                  )}
                </div>
              )}
            </article>
          );
        })}
      </div>
    </div>
  );
}
