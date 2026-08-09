import type { ExportSummary } from "./page";

// Same shape as import-project.tsx (P4.1): imports types from ./page,
// zero useState of its own, page.tsx owns every piece of state and this
// file is presentation plus the callbacks it's given. Two independent
// actions (preview, export) share one destination field, so both are
// plain button clicks rather than <form onSubmit> -- nesting two <form>s
// around one shared input isn't valid HTML.
export function ExportProject({
  destination,
  onDestinationChange,
  formats,
  onFormatsChange,
  preview,
  previewing,
  onPreview,
  result,
  exporting,
  onExport,
  connected,
}: {
  destination: string;
  onDestinationChange: (value: string) => void;
  formats: { patch: boolean; bundle: boolean };
  onFormatsChange: (formats: { patch: boolean; bundle: boolean }) => void;
  preview: ExportSummary | null;
  previewing: boolean;
  onPreview: () => Promise<void>;
  result: ExportSummary | null;
  exporting: boolean;
  onExport: () => Promise<void>;
  connected: boolean;
}) {
  const summary = result ?? preview;
  const canAct = destination.trim().length > 0 && (formats.patch || formats.bundle);

  return (
    <section className="project-export">
      <div className="composer-copy">
        <span className="eyebrow">Exportar cambios</span>
        <h2>Llevate el trabajo a tu propio repositorio</h2>

        <div className="import-path-row">
          <input
            value={destination}
            onChange={(event) => onDestinationChange(event.target.value)}
            placeholder="Ruta absoluta donde escribir el patch/bundle"
            aria-label="Destino de la exportación"
          />
        </div>

        <div className="export-format-options">
          <label className="export-format-option">
            <input
              type="checkbox"
              checked={formats.patch}
              onChange={(event) =>
                onFormatsChange({ ...formats, patch: event.target.checked })
              }
            />
            Patch (git am)
          </label>
          <label className="export-format-option">
            <input
              type="checkbox"
              checked={formats.bundle}
              onChange={(event) =>
                onFormatsChange({ ...formats, bundle: event.target.checked })
              }
            />
            Bundle (git fetch)
          </label>
        </div>

        {summary && (
          <div className="import-preview" role="status">
            <span className="micro-label">
              {result ? "Exportación completa" : "Vista previa"}
            </span>
            <p>
              {summary.range.commit_count} commit(s) · {summary.range.files_changed.length}{" "}
              archivo(s) modificado(s) ·{" "}
              {summary.consistency.matches_git_history
                ? "coincide con el historial de integraciones"
                : "el historial de integraciones todavía no coincide -- reintentá"}
            </p>
            {result?.destination && (
              <p>
                Escrito en {result.destination}
                {result.patch_path ? " · changes.patch" : ""}
                {result.bundle_path ? " · changes.bundle" : ""}
              </p>
            )}
          </div>
        )}

        <div className="composer-footer">
          <span>
            {connected
              ? "El repositorio original nunca se modifica -- sólo se escribe en el destino elegido."
              : "Inicia el backend para exportar un proyecto real."}
          </span>
          <div className="export-actions">
            <button
              type="button"
              className="control-button"
              onClick={() => void onPreview()}
              disabled={previewing || !destination.trim()}
            >
              {previewing ? "Generando…" : "Vista previa"}
            </button>
            <button
              type="button"
              className="primary-button"
              onClick={() => void onExport()}
              disabled={exporting || !canAct}
            >
              {exporting ? "Exportando…" : "Exportar"}
              <span aria-hidden="true">→</span>
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}
