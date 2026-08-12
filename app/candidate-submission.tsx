import type { CandidateFileDraft } from "./page";

// Presentational only, same shape as export-project.tsx/import-project.tsx:
// zero own state, page.tsx owns the draft (title/summary/files) so it can
// be cleared centrally by resetRepairDrafts() -- a stale draft here must
// never survive a switch to a different work item's confirm panel.
export function CandidateSubmission({
  title,
  onTitleChange,
  summary,
  onSummaryChange,
  files,
  onFilesChange,
}: {
  title: string;
  onTitleChange: (value: string) => void;
  summary: string;
  onSummaryChange: (value: string) => void;
  files: CandidateFileDraft[];
  onFilesChange: (files: CandidateFileDraft[]) => void;
}) {
  function updateFile(index: number, patch: Partial<CandidateFileDraft>) {
    onFilesChange(
      files.map((file, position) => (position === index ? { ...file, ...patch } : file)),
    );
  }

  function addFile() {
    onFilesChange([...files, { path: "", content: "", purpose: "" }]);
  }

  function removeFile(index: number) {
    onFilesChange(files.filter((_, position) => position !== index));
  }

  return (
    <div className="candidate-form">
      <label>
        Título
        <input
          value={title}
          onChange={(event) => onTitleChange(event.target.value)}
          placeholder="Título del candidato"
        />
      </label>
      <label>
        Resumen
        <textarea
          value={summary}
          onChange={(event) => onSummaryChange(event.target.value)}
          placeholder="Qué resuelve este candidato"
          rows={2}
        />
      </label>
      <div className="candidate-files">
        <span className="micro-label">Archivos</span>
        {files.map((file, index) => (
          <div className="candidate-file-row" key={index}>
            <input
              value={file.path}
              onChange={(event) => updateFile(index, { path: event.target.value })}
              placeholder="ruta/al/archivo.ext"
              aria-label={`Ruta del archivo ${index + 1}`}
            />
            <input
              value={file.purpose}
              onChange={(event) => updateFile(index, { purpose: event.target.value })}
              placeholder="propósito"
              aria-label={`Propósito del archivo ${index + 1}`}
            />
            <textarea
              value={file.content}
              onChange={(event) => updateFile(index, { content: event.target.value })}
              placeholder="contenido"
              aria-label={`Contenido del archivo ${index + 1}`}
              rows={3}
            />
            <button
              type="button"
              className="control-button danger"
              onClick={() => removeFile(index)}
            >
              Quitar archivo
            </button>
          </div>
        ))}
        <button type="button" className="control-button" onClick={addFile}>
          + Agregar archivo
        </button>
      </div>
    </div>
  );
}
