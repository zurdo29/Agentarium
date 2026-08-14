// Small, dependency-free UI helpers shared between page.tsx and
// task-drawer.tsx. Deliberately a leaf: nothing here imports from either
// of those files, so page.tsx -> task-drawer.tsx -> shared.tsx stays a
// DAG instead of a runtime cycle (see docs/decisions/, P3.3 plan).

export const ROLE_LABELS: Record<string, string> = {
  director: "Director de proyecto",
  technical_manager: "Manager técnico",
  implementation_worker: "Worker",
  tester: "Tester",
  critical_reviewer: "Revisor crítico",
};

const STATUS_LABELS: Record<string, string> = {
  draft: "Borrador",
  planning: "Planificando",
  ready: "Lista",
  running: "En ejecución",
  paused: "Pausado",
  blocked: "Bloqueada",
  assigned: "Asignada",
  awaiting_review: "En revisión",
  changes_requested: "Cambios pedidos",
  awaiting_approval: "Espera aprobación",
  passed: "Aprobada",
  failed: "Fallida",
  completed: "Completada",
  cancelled: "Cancelada",
  pending: "Pendiente",
  approved: "Aprobada",
  rejected: "Rechazada",
  // P4.4b: DeliveryReportWorkItem.outcome extends the status vocabulary
  // with three terms no WorkItemStatus ever carries.
  exhausted: "Presupuesto agotado",
  superseded_by_split: "Reemplazada por división",
  in_progress: "En curso",
};

export function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status.replaceAll("_", " ");
}

const MONTH_LABELS = [
  "ene",
  "feb",
  "mar",
  "abr",
  "may",
  "jun",
  "jul",
  "ago",
  "sep",
  "oct",
  "nov",
  "dic",
] as const;

export function dateLabel(value: string): string {
  const instant = new Date(value);
  if (Number.isNaN(instant.getTime())) return value;

  // Uruguay uses UTC-03:00. Formatting the shifted instant from UTC fields
  // avoids ICU punctuation and whitespace differences during hydration.
  const montevideo = new Date(instant.getTime() - 3 * 60 * 60 * 1000);
  const day = montevideo.getUTCDate().toString().padStart(2, "0");
  const month = MONTH_LABELS[montevideo.getUTCMonth()];
  const hour = montevideo.getUTCHours().toString().padStart(2, "0");
  const minute = montevideo.getUTCMinutes().toString().padStart(2, "0");
  return `${day} ${month} · ${hour}:${minute}`;
}

export function Status({ status }: { status: string }) {
  return (
    <span className={`status-pill ${status}`}>
      <span />
      {statusLabel(status)}
    </span>
  );
}

// Moved here from page.tsx (P4.4b) once delivery-report.tsx became a
// second consumer -- same acyclic reasoning as the rest of this file.
export function PanelHeading({
  index,
  eyebrow,
  title,
  trailing,
}: {
  index: string;
  eyebrow: string;
  title: string;
  trailing?: string;
}) {
  return (
    <div className="panel-heading">
      <span className="panel-index">{index}</span>
      <div>
        <span className="eyebrow">{eyebrow}</span>
        <h2>{title}</h2>
      </div>
      {trailing && <span className="panel-trailing">{trailing}</span>}
    </div>
  );
}
