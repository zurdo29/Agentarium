import type { FormEvent } from "react";
import type { SourceInspection } from "./page";

// Extracted like task-drawer.tsx (P3.3): imports types from ./page,
// helpers from ./shared, never the other way around -- keeps
// page.tsx -> import-project.tsx a DAG, not a cycle. page.tsx owns every
// piece of state here too (see task-drawer.tsx's own zero-`useState`
// precedent); this file is presentation plus the callbacks it's given.
export function ImportProject({
  path,
  onPathChange,
  inspection,
  inspecting,
  onInspect,
  goal,
  onGoalChange,
  title,
  onTitleChange,
  onImport,
  loading,
  connected,
}: {
  path: string;
  onPathChange: (value: string) => void;
  inspection: SourceInspection | null;
  inspecting: boolean;
  onInspect: (event: FormEvent) => Promise<void>;
  goal: string;
  onGoalChange: (value: string) => void;
  title: string;
  onTitleChange: (value: string) => void;
  onImport: (event: FormEvent) => Promise<void>;
  loading: boolean;
  connected: boolean;
}) {
  const canConfirm = inspection?.eligible === true && goal.trim().length > 0;

  return (
    <div className="goal-composer import-composer">
      <div className="composer-number">02</div>
      <div className="composer-copy">
        <span className="eyebrow">Proyecto existente</span>
        <h2>¿Ya tenés un repositorio para seguir?</h2>

        <form className="import-path-row" onSubmit={onInspect}>
          <input
            value={path}
            onChange={(event) => onPathChange(event.target.value)}
            placeholder="Ruta absoluta a la carpeta o repositorio"
            aria-label="Ruta del proyecto a importar"
          />
          <button
            type="submit"
            className="control-button"
            disabled={inspecting || !path.trim()}
          >
            {inspecting ? "Inspeccionando…" : "Inspeccionar"}
          </button>
        </form>

        {inspection && (
          <div
            className={
              inspection.eligible ? "import-preview" : "import-preview ineligible"
            }
            role="status"
          >
            {inspection.eligible ? (
              <>
                <span className="micro-label">
                  {inspection.is_git_repo ? "Repositorio git" : "Carpeta"}
                </span>
                <p>
                  {inspection.is_git_repo
                    ? `Listo para importar · rama ${inspection.branch ?? "—"} · commit ${
                        inspection.head_commit?.slice(0, 8) ?? "—"
                      }`
                    : "Listo para importar · sin historial git; se inicializará uno nuevo."}
                </p>
              </>
            ) : (
              <>
                <span className="micro-label">No se puede importar</span>
                <p>{inspection.reason ?? "La ruta no es elegible."}</p>
              </>
            )}
          </div>
        )}

        <form onSubmit={onImport}>
          {inspection?.eligible && (
            <>
              <textarea
                value={goal}
                onChange={(event) => onGoalChange(event.target.value)}
                placeholder="¿Qué debe hacer tu empresa con este proyecto?"
                aria-label="Objetivo para el proyecto importado"
                rows={3}
              />
              <input
                className="import-title-input"
                value={title}
                onChange={(event) => onTitleChange(event.target.value)}
                placeholder="Título opcional"
                aria-label="Título opcional del proyecto"
              />
            </>
          )}
          <div className="composer-footer">
            <span>
              {connected
                ? "El original nunca se modifica: sólo se lee para crear una copia local."
                : "Inicia el backend para importar un proyecto real."}
            </span>
            <button
              className="primary-button"
              type="submit"
              disabled={loading || !canConfirm}
            >
              {loading ? "Importando…" : "Importar proyecto"}
              <span aria-hidden="true">→</span>
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
