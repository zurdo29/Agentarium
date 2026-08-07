"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { ROLE_LABELS, Status, dateLabel, statusLabel } from "./shared";
import { TaskDrawer } from "./task-drawer";

type View = "dashboard" | "project" | "approvals";
type ProjectAction = "run" | "pause" | "resume" | "cancel";

type Project = {
  id: string;
  title: string;
  goal: string;
  status: string;
  progress_percent: number;
  current_milestone_id?: string | null;
  created_at: string;
  updated_at: string;
  tasks_total?: number;
  tasks_completed?: number;
  brief?: Brief | null;
};

type Brief = {
  summary: string;
  scope: string[];
  deliverables: string[];
  assumptions: string[];
  ambiguities: string[];
  constraints: string[];
  success_criteria: string[];
};

export type WorkItem = {
  id: string;
  title: string;
  description: string;
  status: string;
  assignee_role: string;
  dependency_ids: string[];
  acceptance_criteria: string[];
  expected_outputs: string[];
  attempt_count: number;
  max_attempts: number;
  risk: string;
  priority: number;
  last_error?: string | null;
};

type Approval = {
  id: string;
  project_id: string;
  work_item_id?: string | null;
  action: string;
  reason: string;
  risk: string;
  alternatives: string[];
  affected_resources: string[];
  status: string;
  comments?: string | null;
  created_at: string;
};

export type EventRecord = {
  sequence: number;
  id: string;
  action: string;
  message: string;
  timestamp: string;
  work_item_id?: string | null;
  agent_role?: string | null;
  error?: string | null;
  previous_state?: string | null;
  new_state?: string | null;
};

export type ProjectDetail = {
  project: Project;
  milestones: Array<{
    id: string;
    title: string;
    description: string;
    completed: boolean;
  }>;
  work_items: WorkItem[];
  dependencies: Array<{
    id: string;
    work_item_id: string;
    depends_on_id: string;
  }>;
  artifacts: Array<{
    id: string;
    work_item_id: string;
    title: string;
    artifact_type: string;
    file_paths: string[];
    created_at: string;
  }>;
  reviews: Array<{
    id: string;
    work_item_id: string;
    verdict: string;
    reasons: string[];
  }>;
  test_reports: Array<{
    id: string;
    work_item_id: string;
    passed: boolean;
    summary: string;
    checks: Array<{ name: string; passed: boolean; evidence: string }>;
    command_evidence?: Array<{
      check: string;
      profile?: string;
      backend?: string;
      branch?: string;
      commit?: string;
      return_code?: number;
      timed_out?: boolean;
      passed?: boolean;
      verified?: boolean;
    }>;
  }>;
  decisions: Array<{
    id: string;
    title: string;
    decision: string;
    rationale: string;
    reversible: boolean;
  }>;
  approvals: Approval[];
  metrics: Record<string, number>;
};

type DashboardData = {
  runtime: RuntimeStatus;
  projects: Project[];
  active_agents: number;
  blocked_tasks: number;
  pending_approvals: number;
  latest_errors: EventRecord[];
};

type RuntimeStatus = {
  mode: "simulation" | "artifact_only" | "workspace";
  providers: string[];
  active_model: string | null;
  capabilities: {
    model_inference: boolean;
    project_files: boolean;
    command_execution: boolean;
    change_isolation: boolean;
  };
};

type ProviderName = "mock" | "ollama" | "openai_compatible";

type ProviderDiagnostic = {
  name: ProviderName;
  label: string;
  endpoint: string | null;
  reachable: boolean;
  ready: boolean;
  models: string[];
  message: string;
  selected: boolean;
  selected_model: string | null;
};

type ProviderOverview = {
  active_provider: string;
  active_model: string | null;
  providers: ProviderDiagnostic[];
};

const API =
  process.env.NEXT_PUBLIC_AGENTARIUM_API_URL ?? "http://127.0.0.1:8000/api";
const DEMO_ID = "agentarium-demo";

const SAMPLE_PROJECT: Project = {
  id: DEMO_ID,
  title: "Prototipo ARPG · sistema de objetos",
  goal: "Crear un pequeño ARPG con progresión de objetos y alcance realista.",
  status: "running",
  progress_percent: 67,
  current_milestone_id: "milestone-demo",
  tasks_total: 3,
  tasks_completed: 2,
  created_at: "2026-07-27T00:00:00Z",
  updated_at: "2026-07-27T00:18:00Z",
};

const SAMPLE_DETAIL: ProjectDetail = {
  project: {
    ...SAMPLE_PROJECT,
    brief: {
      summary:
        "Un slice jugable de diez minutos centrado en combate, botín legible y una progresión de objetos acotada.",
      scope: [
        "Una arena compacta",
        "Tres familias de objetos",
        "Un encuentro final",
      ],
      deliverables: [
        "Especificación verificable",
        "Artefacto principal",
        "Resultado integrado",
      ],
      assumptions: ["Ejecución local", "Sin servicios cloud"],
      ambiguities: ["La dirección artística queda fuera del MVP técnico"],
      constraints: ["8 GB de VRAM", "Una inferencia simultánea"],
      success_criteria: [
        "El flujo se completa",
        "Tester y reviewer aprueban",
      ],
    },
  },
  milestones: [
    {
      id: "milestone-demo",
      title: "MVP verificable",
      description: "Del objetivo al resultado integrado.",
      completed: false,
    },
  ],
  work_items: [
    {
      id: "task-scope",
      title: "Especificar el alcance verificable",
      description: "Convertir el brief en una especificación acotada.",
      status: "completed",
      assignee_role: "implementation_worker",
      dependency_ids: [],
      acceptance_criteria: [
        "Incluye alcance y exclusiones",
        "Define evidencia verificable",
      ],
      expected_outputs: ["specification"],
      attempt_count: 1,
      max_attempts: 3,
      risk: "low",
      priority: 90,
    },
    {
      id: "task-build",
      title: "Producir el artefacto principal",
      description: "Construir el entregable según la especificación.",
      status: "completed",
      assignee_role: "implementation_worker",
      dependency_ids: ["task-scope"],
      acceptance_criteria: [
        "Satisface la especificación",
        "Incluye evidencia observable",
      ],
      expected_outputs: ["implementation_artifact"],
      attempt_count: 2,
      max_attempts: 3,
      risk: "medium",
      priority: 80,
    },
    {
      id: "task-integrate",
      title: "Integrar y documentar el resultado",
      description: "Integrar artefactos y documentar los límites.",
      status: "running",
      assignee_role: "implementation_worker",
      dependency_ids: ["task-build"],
      acceptance_criteria: [
        "Referencia dependencias",
        "Resume validaciones",
      ],
      expected_outputs: ["integrated_result"],
      attempt_count: 1,
      max_attempts: 3,
      risk: "low",
      priority: 70,
    },
  ],
  dependencies: [
    {
      id: "dep-1",
      work_item_id: "task-build",
      depends_on_id: "task-scope",
    },
    {
      id: "dep-2",
      work_item_id: "task-integrate",
      depends_on_id: "task-build",
    },
  ],
  artifacts: [
    {
      id: "artifact-1",
      work_item_id: "task-scope",
      title: "Especificación de alcance",
      artifact_type: "specification",
      file_paths: ["workspaces/demo/artifacts/specification.json"],
      created_at: "2026-07-27T00:08:00Z",
    },
    {
      id: "artifact-2",
      work_item_id: "task-build",
      title: "Artefacto principal corregido",
      artifact_type: "implementation_artifact",
      file_paths: ["workspaces/demo/artifacts/implementation.json"],
      created_at: "2026-07-27T00:15:00Z",
    },
  ],
  reviews: [
    {
      id: "review-1",
      work_item_id: "task-build",
      verdict: "approved",
      reasons: ["La evidencia y las limitaciones son explícitas."],
    },
  ],
  test_reports: [
    {
      id: "test-1",
      work_item_id: "task-build",
      passed: true,
      summary: "Comprobaciones automáticas superadas.",
      checks: [
        {
          name: "file_exists",
          passed: true,
          evidence: "Checksum verificado.",
        },
      ],
    },
  ],
  decisions: [
    {
      id: "decision-1",
      title: "Alcance inicial conservador",
      decision: "Un único hito vertical con tres entregables dependientes.",
      rationale: "Es reversible y adecuado para el presupuesto local.",
      reversible: true,
    },
  ],
  approvals: [],
  metrics: {
    tasks_completed: 2,
    tasks_rejected: 1,
    retries: 1,
    tester_approval_rate: 1,
    reviewer_approval_rate: 0.67,
    average_agent_duration_ms: 1230,
  },
};

const SAMPLE_EVENTS: EventRecord[] = [
  {
    sequence: 1,
    id: "event-1",
    action: "planning_completed",
    message: "Brief y DAG creados con 3 tareas.",
    timestamp: "2026-07-27T00:04:00Z",
    agent_role: "technical_manager",
  },
  {
    sequence: 2,
    id: "event-2",
    action: "review_rejected",
    message: "Faltaba evidencia verificable; se solicitó una corrección.",
    timestamp: "2026-07-27T00:12:00Z",
    agent_role: "critical_reviewer",
  },
  {
    sequence: 3,
    id: "event-3",
    action: "task_state_changed",
    message: "Artefacto principal: passed → completed",
    timestamp: "2026-07-27T00:15:00Z",
    agent_role: "tester",
  },
  {
    sequence: 4,
    id: "event-4",
    action: "agent_run_completed",
    message: "implementation_worker está integrando el resultado final.",
    timestamp: "2026-07-27T00:18:00Z",
    agent_role: "implementation_worker",
  },
];

const SAMPLE_DASHBOARD: DashboardData = {
  runtime: {
    mode: "simulation",
    providers: ["mock"],
    active_model: null,
    capabilities: {
      model_inference: false,
      project_files: false,
      command_execution: false,
      change_isolation: false,
    },
  },
  projects: [SAMPLE_PROJECT],
  active_agents: 1,
  blocked_tasks: 0,
  pending_approvals: 0,
  latest_errors: [],
};

const SAMPLE_PROVIDERS: ProviderOverview = {
  active_provider: "mock",
  active_model: null,
  providers: [
    {
      name: "mock",
      label: "Motor determinista",
      endpoint: null,
      reachable: true,
      ready: true,
      models: [],
      message: "Respaldo local estable; no realiza inferencia con un modelo.",
      selected: true,
      selected_model: null,
    },
    {
      name: "ollama",
      label: "Ollama local",
      endpoint: "http://127.0.0.1:11434",
      reachable: false,
      ready: false,
      models: [],
      message: "Esperando diagnóstico del backend.",
      selected: false,
      selected_model: null,
    },
    {
      name: "openai_compatible",
      label: "Servidor compatible",
      endpoint: "http://127.0.0.1:1234/v1",
      reachable: false,
      ready: false,
      models: [],
      message: "Esperando diagnóstico del backend.",
      selected: false,
      selected_model: null,
    },
  ],
};

function percent(value: number | undefined): string {
  return `${Math.round((value ?? 0) * 100)}%`;
}

export default function Home() {
  const [view, setView] = useState<View>("dashboard");
  const [dashboard, setDashboard] =
    useState<DashboardData>(SAMPLE_DASHBOARD);
  const [providerOverview, setProviderOverview] =
    useState<ProviderOverview>(SAMPLE_PROVIDERS);
  const [detail, setDetail] = useState<ProjectDetail>(SAMPLE_DETAIL);
  const [events, setEvents] = useState<EventRecord[]>(SAMPLE_EVENTS);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [connected, setConnected] = useState(false);
  const [loading, setLoading] = useState(false);
  const [providerLoading, setProviderLoading] = useState(false);
  const [activeAction, setActiveAction] = useState<ProjectAction | null>(null);
  const [goal, setGoal] = useState("");
  const [error, setError] = useState<string | null>(null);

  const selectedTask = useMemo(
    () =>
      detail.work_items.find((item) => item.id === selectedTaskId) ?? null,
    [detail.work_items, selectedTaskId],
  );

  const request = useCallback(
    async <T,>(path: string, options?: RequestInit): Promise<T> => {
      const response = await fetch(`${API}${path}`, {
        ...options,
        headers: {
          "Content-Type": "application/json",
          ...options?.headers,
        },
      });
      if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as {
          detail?: string;
        } | null;
        throw new Error(payload?.detail ?? `Error HTTP ${response.status}`);
      }
      return (await response.json()) as T;
    },
    [],
  );

  const refreshDashboard = useCallback(async () => {
    try {
      const [dashboardResult, approvalResult, providerResult] = await Promise.all([
        request<DashboardData>("/dashboard"),
        request<Approval[]>("/approvals"),
        request<ProviderOverview>("/providers").catch(() => null),
      ]);
      setDashboard(dashboardResult);
      setApprovals(approvalResult);
      if (providerResult) setProviderOverview(providerResult);
      setConnected(true);
    } catch {
      setConnected(false);
    }
  }, [request]);

  const openProject = useCallback(
    async (projectId: string) => {
      setView("project");
      setError(null);
      if (projectId === DEMO_ID) {
        setDetail(SAMPLE_DETAIL);
        setEvents(SAMPLE_EVENTS);
        return;
      }
      setLoading(true);
      try {
        const [projectDetail, projectEvents] = await Promise.all([
          request<ProjectDetail>(`/projects/${projectId}`),
          request<EventRecord[]>(`/projects/${projectId}/events`),
        ]);
        setDetail(projectDetail);
        setEvents(projectEvents);
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "No se pudo abrir.");
      } finally {
        setLoading(false);
      }
    },
    [request],
  );

  useEffect(() => {
    const timer = window.setTimeout(() => void refreshDashboard(), 0);
    return () => window.clearTimeout(timer);
  }, [refreshDashboard]);

  useEffect(() => {
    const projectId = detail.project.id;
    if (!connected || projectId === DEMO_ID || view !== "project") return;
    const lastSequence = events.at(-1)?.sequence ?? 0;
    const source = new EventSource(
      `${API}/projects/${projectId}/events/stream?after_sequence=${lastSequence}`,
    );
    source.onmessage = (message) => {
      const incoming = JSON.parse(message.data) as EventRecord;
      setEvents((current) =>
        current.some((event) => event.id === incoming.id)
          ? current
          : [...current, incoming],
      );
    };
    return () => source.close();
  }, [connected, detail.project.id, events, view]);

  async function createProject(event: FormEvent) {
    event.preventDefault();
    if (!goal.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const created = await request<ProjectDetail>("/projects", {
        method: "POST",
        body: JSON.stringify({ goal: goal.trim(), auto_plan: true }),
      });
      setGoal("");
      setDetail(created);
      setEvents([]);
      setConnected(true);
      setView("project");
      await refreshDashboard();
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "No fue posible crear el proyecto.",
      );
    } finally {
      setLoading(false);
    }
  }

  async function controlProject(action: ProjectAction) {
    if (detail.project.id === DEMO_ID) {
      setError("Inicia la API para ejecutar controles reales.");
      return;
    }
    setLoading(true);
    setActiveAction(action);
    setError(null);
    try {
      await request(`/projects/${detail.project.id}/${action}`, {
        method: "POST",
      });
      await openProject(detail.project.id);
      await refreshDashboard();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "La acción falló.");
    } finally {
      setActiveAction(null);
      setLoading(false);
    }
  }

  async function resolveApproval(
    id: string,
    status: "approved" | "rejected",
    comments: string,
  ) {
    setLoading(true);
    setError(null);
    try {
      await request(`/approvals/${id}/resolve`, {
        method: "POST",
        body: JSON.stringify({
          status,
          comments:
            comments.trim() ||
            (status === "approved"
              ? "Aprobado desde el centro de control."
              : "Rechazado desde el centro de control."),
        }),
      });
      await refreshDashboard();
      if (detail.project.id !== DEMO_ID) {
        await openProject(detail.project.id);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "La acción falló.");
    } finally {
      setLoading(false);
    }
  }

  async function retryTask(taskId: string) {
    setLoading(true);
    setError(null);
    try {
      await request(`/work-items/${taskId}/retry`, { method: "POST" });
      await openProject(detail.project.id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "No se pudo reintentar.");
    } finally {
      setLoading(false);
    }
  }

  async function reworkTask(taskId: string) {
    setLoading(true);
    setError(null);
    try {
      await request(`/work-items/${taskId}/rework`, {
        method: "POST",
        body: JSON.stringify({
          reason:
            "La verificación independiente encontró comportamiento incompleto; corregirlo contra todos los criterios.",
        }),
      });
      await openProject(detail.project.id);
      await refreshDashboard();
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "No se pudo abrir la revisión.",
      );
    } finally {
      setLoading(false);
    }
  }

  async function escalateTask(taskId: string) {
    setLoading(true);
    setError(null);
    try {
      await request(`/work-items/${taskId}/escalate`, {
        method: "POST",
        body: JSON.stringify({
          reason:
            "El CEO debe revisar el bloqueo, el riesgo o la discrepancia de esta tarea.",
        }),
      });
      setSelectedTaskId(null);
      await refreshDashboard();
      setView("approvals");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "No se pudo escalar.");
    } finally {
      setLoading(false);
    }
  }

  async function updateTaskPriority(taskId: string, priority: number) {
    setLoading(true);
    setError(null);
    try {
      await request(`/work-items/${taskId}/priority`, {
        method: "PATCH",
        body: JSON.stringify({ priority }),
      });
      await openProject(detail.project.id);
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "No se pudo cambiar la prioridad.",
      );
    } finally {
      setLoading(false);
    }
  }

  async function selectProvider(provider: ProviderName, model: string | null) {
    setProviderLoading(true);
    setError(null);
    try {
      const selected = await request<ProviderOverview>("/runtime/provider", {
        method: "PUT",
        body: JSON.stringify({ provider, model }),
      });
      setProviderOverview(selected);
      await refreshDashboard();
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "No se pudo activar el proveedor.",
      );
    } finally {
      setProviderLoading(false);
    }
  }

  const totalTasks = dashboard.projects.reduce(
    (sum, project) => sum + (project.tasks_total ?? 0),
    0,
  );
  const completedTasks = dashboard.projects.reduce(
    (sum, project) => sum + (project.tasks_completed ?? 0),
    0,
  );
  const pendingApprovals = approvals.filter(
    (approval) => approval.status === "pending",
  );

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <button
          className="brand"
          onClick={() => setView("dashboard")}
          aria-label="Ir al dashboard"
        >
          <span className="brand-mark">A</span>
          <span>
            <strong>Agentarium</strong>
            <small>AI COMPANY OS</small>
          </span>
        </button>

        <nav aria-label="Navegación principal">
          <button
            className={view === "dashboard" ? "nav-item active" : "nav-item"}
            onClick={() => setView("dashboard")}
          >
            <span className="nav-glyph">⌂</span>
            Dashboard
          </button>
          <button
            className={view === "project" ? "nav-item active" : "nav-item"}
            onClick={() => setView("project")}
          >
            <span className="nav-glyph">◇</span>
            Proyecto activo
          </button>
          <button
            className={view === "approvals" ? "nav-item active" : "nav-item"}
            onClick={() => setView("approvals")}
          >
            <span className="nav-glyph">✓</span>
            Aprobaciones
            {pendingApprovals.length > 0 && (
              <span className="nav-count">{pendingApprovals.length}</span>
            )}
          </button>
        </nav>

        <div className="sidebar-bottom">
          <div className="system-card">
            <span className="eyebrow">Inferencia local</span>
            <div className="system-row">
              <span className={connected ? "live-dot" : "live-dot idle"} />
              <strong>
                {connected
                  ? dashboard.runtime.mode === "workspace"
                    ? "Workspace activo"
                    : "Simulación activa"
                  : "Modo demostración"}
              </strong>
            </div>
            <small>
              {dashboard.runtime.providers.join(" + ")} ·{" "}
              {dashboard.runtime.capabilities.project_files
                ? dashboard.runtime.capabilities.change_isolation
                  ? "worktrees + archivos"
                  : "genera archivos"
                : "sin archivos de producto"}
            </small>
          </div>
          <div className="owner">
            <span className="owner-avatar">R</span>
            <span>
              <strong>Propietario</strong>
              <small>Autoridad final</small>
            </span>
          </div>
        </div>
      </aside>

      <section className="workspace">
        <header className="topbar">
          <div>
            <span className="breadcrumb">EMPRESA / OPERACIONES</span>
            <span className="topbar-title">
              {view === "dashboard"
                ? "Centro de mando"
                : view === "approvals"
                  ? "Decisiones humanas"
                  : detail.project.title}
            </span>
          </div>
          <div className="topbar-actions">
            <span className="provider-chip">
              <span className={connected ? "live-dot" : "live-dot idle"} />
              {connected
                ? dashboard.runtime.mode === "workspace"
                  ? `${dashboard.runtime.providers.join(" + ")}${
                      dashboard.runtime.active_model
                        ? ` / ${dashboard.runtime.active_model}`
                        : ""
                    } · workspace`
                  : `${dashboard.runtime.providers.join(" + ")} · simulación`
                : "Vista offline"}
            </span>
            <button
              className="compact-button"
              onClick={() => void refreshDashboard()}
              aria-label="Actualizar datos"
              disabled={loading}
            >
              ↻
            </button>
          </div>
        </header>

        <RuntimeBanner runtime={dashboard.runtime} connected={connected} />

        {error && (
          <div className="error-banner" role="alert">
            <span>!</span>
            {error}
            <button onClick={() => setError(null)} aria-label="Cerrar error">
              ×
            </button>
          </div>
        )}

        {view === "dashboard" && (
          <Dashboard
            dashboard={dashboard}
            totalTasks={totalTasks}
            completedTasks={completedTasks}
            goal={goal}
            setGoal={setGoal}
            createProject={createProject}
            openProject={openProject}
            loading={loading}
            connected={connected}
            providerOverview={providerOverview}
            providerLoading={providerLoading}
            selectProvider={selectProvider}
          />
        )}

        {view === "project" && (
          <ProjectView
            detail={detail}
            events={events}
            runtime={dashboard.runtime}
            activeAction={activeAction}
            loading={loading}
            onControl={controlProject}
            onSelectTask={setSelectedTaskId}
            onOpenApprovals={() => setView("approvals")}
          />
        )}

        {view === "approvals" && (
          <ApprovalCenter
            approvals={approvals}
            onResolve={resolveApproval}
            loading={loading}
            connected={connected}
          />
        )}
      </section>

      {selectedTask && (
        <TaskDrawer
          item={selectedTask}
          detail={detail}
          events={events}
          onClose={() => setSelectedTaskId(null)}
          onRetry={retryTask}
          onRework={reworkTask}
          onEscalate={escalateTask}
          onPriority={updateTaskPriority}
          loading={loading}
        />
      )}
    </main>
  );
}

function RuntimeBanner({
  runtime,
  connected,
}: {
  runtime: RuntimeStatus;
  connected: boolean;
}) {
  return (
    <div className={`runtime-banner ${runtime.mode}`} role="status">
      <span className="runtime-mark">
        {runtime.mode === "workspace"
          ? "FILE"
          : runtime.mode === "simulation"
            ? "SIM"
            : "JSON"}
      </span>
      <span className="runtime-copy">
        <strong>
          {connected
            ? runtime.mode === "workspace"
              ? "Workspace activo: las tareas ya materializan archivos reales."
              : runtime.mode === "simulation"
                ? "Modo simulación: el flujo funciona, pero aún no construye el producto."
                : "Modo artefactos: el modelo responde, pero aún no escribe el producto."
            : "Vista de demostración: conecta la API para trabajar con proyectos reales."}
        </strong>
        <small>
          {runtime.mode === "workspace" && !runtime.capabilities.model_inference
            ? runtime.capabilities.change_isolation
              ? "El proveedor mock escribe contenido determinista; cada intento sí usa un worktree y validaciones locales reales."
              : runtime.capabilities.command_execution
                ? "El proveedor mock escribe contenido determinista; los perfiles fijos sí ejecutan validaciones locales reales."
              : "El proveedor mock escribe contenido determinista; todavía no genera una aplicación específica mediante un modelo."
            : runtime.capabilities.project_files
              ? "Los archivos del proyecto quedan materializados en un workspace."
            : "Los resultados actuales son registros JSON verificables, no archivos ejecutables."}
        </small>
      </span>
      <span className="runtime-next">
        {runtime.capabilities.change_isolation
          ? "Worktrees activos"
          : runtime.capabilities.command_execution
            ? "Perfiles controlados"
            : "Comandos desactivados"}
      </span>
    </div>
  );
}

function Dashboard({
  dashboard,
  totalTasks,
  completedTasks,
  goal,
  setGoal,
  createProject,
  openProject,
  loading,
  connected,
  providerOverview,
  providerLoading,
  selectProvider,
}: {
  dashboard: DashboardData;
  totalTasks: number;
  completedTasks: number;
  goal: string;
  setGoal: (value: string) => void;
  createProject: (event: FormEvent) => Promise<void>;
  openProject: (id: string) => Promise<void>;
  loading: boolean;
  connected: boolean;
  providerOverview: ProviderOverview;
  providerLoading: boolean;
  selectProvider: (provider: ProviderName, model: string | null) => Promise<void>;
}) {
  return (
    <div className="page dashboard-page">
      <section className="page-heading dashboard-heading">
        <div>
          <span className="kicker">BUENAS NOCHES, CEO</span>
          <h1>La empresa, en una mirada.</h1>
          <p>
            Da una dirección. Agentarium convierte la ambigüedad en trabajo
            pequeño, revisado y observable.
          </p>
        </div>
        <span className="date-stamp">27 · JUL · 2026</span>
      </section>

      <section className="metric-grid" aria-label="Resumen">
        <Metric
          label="Proyectos"
          value={dashboard.projects.length.toString().padStart(2, "0")}
          meta="en el portfolio local"
          tone="ink"
        />
        <Metric
          label="Trabajo completado"
          value={`${completedTasks}/${totalTasks || 3}`}
          meta="tareas verificadas"
          tone="green"
        />
        <Metric
          label="Agentes activos"
          value={dashboard.active_agents.toString().padStart(2, "0")}
          meta="límite físico: 1"
          tone="blue"
        />
        <Metric
          label="Requiere atención"
          value={(
            dashboard.blocked_tasks + dashboard.pending_approvals
          )
            .toString()
            .padStart(2, "0")}
          meta={`${dashboard.pending_approvals} aprobaciones`}
          tone="orange"
        />
      </section>

      <ProviderConsole
        overview={providerOverview}
        connected={connected}
        loading={providerLoading}
        onSelect={selectProvider}
      />

      <section className="dashboard-columns">
        <div className="main-column">
          <form className="goal-composer" onSubmit={createProject}>
            <div className="composer-number">01</div>
            <div className="composer-copy">
              <span className="eyebrow">Nueva directiva</span>
              <h2>¿Qué debe construir tu empresa?</h2>
              <textarea
                value={goal}
                onChange={(event) => setGoal(event.target.value)}
                placeholder="Ej.: Crea un pequeño ARPG con progresión de objetos, con alcance realista para un prototipo…"
                aria-label="Objetivo del nuevo proyecto"
                rows={3}
              />
              <div className="composer-footer">
                <span>
                  {connected
                    ? "El director estructurará el brief inmediatamente."
                    : "Inicia el backend para crear un proyecto real."}
                </span>
                <button
                  className="primary-button"
                  type="submit"
                  disabled={loading || !goal.trim()}
                >
                  {loading ? "Estructurando…" : "Crear proyecto"}
                  <span aria-hidden="true">→</span>
                </button>
              </div>
            </div>
          </form>

          <div className="section-title">
            <div>
              <span className="eyebrow">Portfolio local</span>
              <h2>Proyectos recientes</h2>
            </div>
            <span className="section-count">{dashboard.projects.length}</span>
          </div>

          <div className="project-list">
            {dashboard.projects.map((project, index) => (
              <button
                key={project.id}
                className="project-row"
                onClick={() => void openProject(project.id)}
              >
                <span className="project-index">
                  {(index + 1).toString().padStart(2, "0")}
                </span>
                <span className="project-main">
                  <span className="project-title-line">
                    <strong>{project.title}</strong>
                    <Status status={project.status} />
                  </span>
                  <span className="project-goal">{project.goal}</span>
                  <span className="progress-track">
                    <span
                      style={{ width: `${project.progress_percent}%` }}
                    />
                  </span>
                </span>
                <span className="project-progress">
                  <strong>{Math.round(project.progress_percent)}%</strong>
                  <small>
                    {project.tasks_completed ?? 0}/{project.tasks_total ?? 3} tareas
                  </small>
                </span>
                <span className="row-arrow">↗</span>
              </button>
            ))}
          </div>
        </div>

        <aside className="activity-panel">
          <div className="section-title compact">
            <div>
              <span className="eyebrow">Pulso operativo</span>
              <h2>Actividad reciente</h2>
            </div>
            <span className="pulse-ring" />
          </div>
          <div className="activity-list">
            {(dashboard.latest_errors.length
              ? dashboard.latest_errors
              : SAMPLE_EVENTS
            ).map((event) => (
              <div className="activity-item" key={event.id}>
                <span
                  className={
                    event.error ? "activity-mark error" : "activity-mark"
                  }
                />
                <div>
                  <strong>{event.message}</strong>
                  <span>
                    {ROLE_LABELS[event.agent_role ?? ""] ?? "Sistema"} ·{" "}
                    {dateLabel(event.timestamp)}
                  </span>
                </div>
              </div>
            ))}
          </div>
          <div className="quiet-note">
            <span>MEMORIA DURADERA</span>
            <p>
              Estado, artefactos y decisiones se guardan en SQLite y archivos
              versionables.
            </p>
          </div>
        </aside>
      </section>
    </div>
  );
}

function ProviderConsole({
  overview,
  connected,
  loading,
  onSelect,
}: {
  overview: ProviderOverview;
  connected: boolean;
  loading: boolean;
  onSelect: (provider: ProviderName, model: string | null) => Promise<void>;
}) {
  const [models, setModels] = useState<Record<string, string>>({});

  return (
    <section className="provider-console" aria-label="Motor de inferencia">
      <div className="provider-console-heading">
        <div>
          <span className="eyebrow">Motor de inferencia</span>
          <h2>Proveedor y modelo para nuevas ejecuciones</h2>
        </div>
        <span className="provider-session-note">guardado en este equipo</span>
      </div>
      <div className="provider-options">
        {overview.providers.map((provider) => {
          const chosenModel =
            models[provider.name] ??
            provider.selected_model ??
            provider.models[0] ??
            "";
          const state = provider.selected
            ? "selected"
            : provider.ready
              ? "ready"
              : "offline";
          return (
            <article className={`provider-option ${state}`} key={provider.name}>
              <div className="provider-option-top">
                <span
                  className={`provider-state-dot ${state}`}
                  aria-hidden="true"
                />
                <div>
                  <strong>{provider.label}</strong>
                  <small>
                    {provider.selected
                      ? "ACTIVO"
                      : provider.ready
                        ? "LISTO"
                        : provider.reachable
                          ? "SIN MODELOS"
                          : "SIN CONEXIÓN"}
                  </small>
                </div>
              </div>
              <p>{provider.message}</p>
              {provider.endpoint && <code>{provider.endpoint}</code>}
              <div className="provider-option-actions">
                {provider.models.length > 0 && (
                  <select
                    aria-label={`Modelo para ${provider.label}`}
                    value={chosenModel}
                    onChange={(event) =>
                      setModels((current) => ({
                        ...current,
                        [provider.name]: event.target.value,
                      }))
                    }
                  >
                    {provider.models.map((model) => (
                      <option value={model} key={model}>
                        {model}
                      </option>
                    ))}
                  </select>
                )}
                <button
                  className="provider-activate"
                  disabled={
                    loading ||
                    !connected ||
                    !provider.ready ||
                    provider.selected ||
                    (provider.name !== "mock" && !chosenModel)
                  }
                  onClick={() =>
                    void onSelect(
                      provider.name,
                      provider.name === "mock" ? null : chosenModel,
                    )
                  }
                >
                  {provider.selected
                    ? provider.selected_model ?? "En uso"
                    : loading
                      ? "Comprobando…"
                      : "Activar"}
                </button>
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}

function Metric({
  label,
  value,
  meta,
  tone,
}: {
  label: string;
  value: string;
  meta: string;
  tone: string;
}) {
  return (
    <article className={`metric-card ${tone}`}>
      <span className="metric-label">{label}</span>
      <strong>{value}</strong>
      <span className="metric-meta">
        <span />
        {meta}
      </span>
    </article>
  );
}

function ProjectView({
  detail,
  events,
  runtime,
  activeAction,
  loading,
  onControl,
  onSelectTask,
  onOpenApprovals,
}: {
  detail: ProjectDetail;
  events: EventRecord[];
  runtime: RuntimeStatus;
  activeAction: ProjectAction | null;
  loading: boolean;
  onControl: (action: ProjectAction) => Promise<void>;
  onSelectTask: (id: string) => void;
  onOpenApprovals: () => void;
}) {
  const project = detail.project;
  const pending = detail.approvals.filter(
    (approval) => approval.status === "pending",
  );
  const latestEvent = events.at(-1);
  const productFiles = Array.from(
    new Set(
      detail.artifacts.flatMap((artifact) =>
        artifact.file_paths.filter((path) =>
          path.replaceAll("\\", "/").includes("/project/"),
        ),
      ),
    ),
  );
  const htmlFiles = productFiles
    .map((path) => path.replaceAll("\\", "/"))
    .filter((path) => path.toLowerCase().endsWith(".html"));
  const previewFile =
    htmlFiles.find((path) => path.toLowerCase().endsWith("/index.html")) ??
    htmlFiles[0];
  const previewRelativePath = previewFile?.split("/project/")[1];
  const previewUrl = previewRelativePath
    ? `${API}/projects/${project.id}/preview/${previewRelativePath
        .split("/")
        .map(encodeURIComponent)
        .join("/")}`
    : null;
  return (
    <div className="page project-page">
      <section className="project-header">
        <div className="project-heading">
          <span className="kicker">PROYECTO / {project.id.slice(0, 8)}</span>
          <div className="title-with-status">
            <h1>{project.title}</h1>
            <Status status={project.status} />
          </div>
          <p>{project.goal}</p>
        </div>
        <div className="project-controls" aria-label="Controles de ejecución">
          {["paused", "awaiting_approval", "failed"].includes(project.status) ? (
            <button
              className="control-button"
              onClick={() => void onControl("resume")}
              disabled={loading}
            >
              ▶ Reanudar
            </button>
          ) : project.status === "running" ? (
            <button
              className="control-button"
              onClick={() => void onControl("pause")}
              disabled={loading}
            >
              Ⅱ Pausar
            </button>
          ) : (
            <button
              className="primary-button"
              onClick={() => void onControl("run")}
              disabled={loading || project.status === "completed"}
            >
              {activeAction === "run" ? "Ejecutando agentes…" : "▶ Ejecutar"}
            </button>
          )}
          <button
            className="control-button danger"
            onClick={() => void onControl("cancel")}
            disabled={
              loading ||
              ["completed", "cancelled"].includes(project.status)
            }
          >
            × Cancelar
          </button>
        </div>
      </section>

      <section className="project-progress-bar">
        <div>
          <span>Progreso del hito</span>
          <strong>{Math.round(project.progress_percent)}%</strong>
        </div>
        <span className="progress-track large">
          <span style={{ width: `${project.progress_percent}%` }} />
        </span>
      </section>

      {activeAction !== "run" && project.status === "ready" && (
        <section className="execution-notice ready-to-run">
          <span className="execution-result-mark">GO</span>
          <span>
            <strong>El plan está listo para convertirse en archivos reales.</strong>
            <small>
              Hay {detail.work_items.filter((item) => item.status === "ready").length}{" "}
              tarea inicial disponible; pulsa Ejecutar y deja que los agentes recorran
              el grafo completo.
            </small>
          </span>
          <span className="execution-stage">{detail.work_items.length} tareas</span>
        </section>
      )}

      {activeAction === "run" && (
        <section className="execution-notice running" aria-live="polite">
          <span className="execution-spinner" aria-hidden="true" />
          <span>
            <strong>La ejecución está en curso.</strong>
            <small>
              {latestEvent?.message ??
                "Preparando las tareas y recopilando evidencia…"}
            </small>
          </span>
          <span className="execution-stage">{events.length} eventos</span>
        </section>
      )}

      {activeAction !== "run" &&
        project.status === "completed" &&
        runtime.mode !== "workspace" && (
          <section className="execution-notice simulated">
            <span className="execution-result-mark">SIM</span>
            <span>
              <strong>Simulación completada; el producto aún no fue construido.</strong>
              <small>
                Se verificaron {detail.artifacts.length} artefactos de control en
                formato JSON. El próximo incremento materializará archivos y
                comandos dentro del workspace.
              </small>
            </span>
            <span className="execution-stage">
              {runtime.providers.join(" + ")}
            </span>
          </section>
        )}

      {activeAction !== "run" &&
        project.status === "completed" &&
        runtime.mode === "workspace" && (
          <section className="execution-notice workspace-ready">
            <span className="execution-result-mark">FILE</span>
            <span>
              <strong>
                {productFiles.length > 0
                  ? "Workspace materializado correctamente."
                  : "Este proyecto terminó antes de activar el workspace."}
              </strong>
              <small>
                {productFiles.length > 0
                  ? `Agentarium integró ${productFiles.length} archivo${
                      productFiles.length === 1 ? "" : "s"
                    } de proyecto con checksum, perfiles y worktrees verificables${
                      runtime.capabilities.model_inference
                        ? "."
                        : "; el contenido actual sigue siendo determinista por usar mock."
                    }`
                  : "Crea y ejecuta un proyecto nuevo para generar sus archivos confinados."}
              </small>
            </span>
            <span className="execution-stage">{productFiles.length} archivos</span>
          </section>
        )}

      {project.status === "completed" && previewUrl && (
        <section className="product-preview">
          <div className="product-preview-heading">
            <div>
              <span className="eyebrow">Producto ejecutable</span>
              <h2>Vista previa aislada</h2>
              <p>
                El contenido se ejecuta con red bloqueada y separado del centro de
                control.
              </p>
            </div>
            <a
              className="control-button"
              href={previewUrl}
              target="_blank"
              rel="noreferrer"
            >
              Abrir en grande ↗
            </a>
          </div>
          <div className="product-preview-frame">
            <iframe
              src={previewUrl}
              title={`Vista previa de ${project.title}`}
              sandbox="allow-scripts allow-same-origin"
              referrerPolicy="no-referrer"
            />
          </div>
          <div className="product-preview-footer">
            <span className="live-dot" />
            <span>{previewRelativePath}</span>
            <strong>{productFiles.length} archivos verificados</strong>
          </div>
        </section>
      )}

      {pending.length > 0 && (
        <button className="approval-alert" onClick={onOpenApprovals}>
          <span className="alert-icon">!</span>
          <span>
            <strong>{pending.length} decisión humana pendiente</strong>
            <small>La ejecución no continuará hasta resolverla.</small>
          </span>
          <span>Revisar →</span>
        </button>
      )}

      <section className="project-grid">
        <div className="project-primary">
          <article className="panel brief-panel">
            <PanelHeading index="01" eyebrow="Mandato" title="Brief ejecutivo" />
            <p className="brief-summary">
              {project.brief?.summary ?? "El director aún no creó el brief."}
            </p>
            <div className="brief-columns">
              <div>
                <span className="micro-label">Entregables</span>
                <ul className="check-list">
                  {(project.brief?.deliverables ?? []).map((item) => (
                    <li key={item}>
                      <span>✓</span>
                      {item}
                    </li>
                  ))}
                </ul>
              </div>
              <div>
                <span className="micro-label">Ambigüedades controladas</span>
                <ul className="plain-list">
                  {(project.brief?.ambiguities ?? []).map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </div>
            </div>
          </article>

          <article className="panel task-panel">
            <PanelHeading
              index="02"
              eyebrow="Plan de ejecución"
              title="Grafo de tareas"
              trailing={`${detail.work_items.filter((item) => item.status === "completed").length}/${detail.work_items.length} completas`}
            />
            <div className="task-flow">
              {detail.work_items.map((item, index) => (
                <div className="task-flow-item" key={item.id}>
                  {index > 0 && <span className="task-connector" />}
                  <button
                    className="task-card"
                    onClick={() => onSelectTask(item.id)}
                  >
                    <span
                      className={`task-order ${item.status}`}
                      aria-hidden="true"
                    >
                      {item.status === "completed"
                        ? "✓"
                        : (index + 1).toString().padStart(2, "0")}
                    </span>
                    <span className="task-copy">
                      <span className="task-topline">
                        <strong>{item.title}</strong>
                        <Status status={item.status} />
                      </span>
                      <span>{item.description}</span>
                      <span className="task-meta">
                        {ROLE_LABELS[item.assignee_role] ?? item.assignee_role}
                        <i />
                        intento {item.attempt_count}/{item.max_attempts}
                        <i />
                        riesgo {item.risk}
                      </span>
                    </span>
                    <span className="row-arrow">→</span>
                  </button>
                </div>
              ))}
            </div>
          </article>

          <article className="panel timeline-panel">
            <PanelHeading
              index="03"
              eyebrow="Auditoría"
              title="Timeline de eventos"
              trailing={`${events.length} eventos`}
            />
            <div className="timeline">
              {[...events].reverse().slice(0, 8).map((event) => (
                <div className="timeline-event" key={event.id}>
                  <time>{dateLabel(event.timestamp)}</time>
                  <span
                    className={event.error ? "timeline-dot error" : "timeline-dot"}
                  />
                  <div>
                    <strong>{event.message}</strong>
                    <span>
                      {ROLE_LABELS[event.agent_role ?? ""] ?? "Sistema"}
                      {event.new_state
                        ? ` · ${statusLabel(event.new_state)}`
                        : ""}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </article>
        </div>

        <aside className="project-secondary">
          <article className="panel agent-panel">
            <span className="eyebrow">Equipo asignado</span>
            <h3>Cinco roles, autoridad acotada</h3>
            <div className="agent-stack">
              {Object.entries(ROLE_LABELS).map(([role, label], index) => (
                <div className="agent-row" key={role}>
                  <span className="agent-avatar">
                    {["D", "M", "W", "T", "R"][index]}
                  </span>
                  <span>
                    <strong>{label}</strong>
                    <small>
                      {role === "implementation_worker"
                        ? "workspace_write"
                        : role.replaceAll("_", " ")}
                    </small>
                  </span>
                  <span
                    className={
                      index === 2 && project.status === "running"
                        ? "live-dot"
                        : "live-dot idle"
                    }
                  />
                </div>
              ))}
            </div>
          </article>

          <article className="panel">
            <PanelHeading index="04" eyebrow="Registro" title="Decisiones" />
            <div className="decision-list">
              {detail.decisions.map((decision) => (
                <div className="decision-item" key={decision.id}>
                  <span>{decision.reversible ? "REVERSIBLE" : "PROTEGIDA"}</span>
                  <strong>{decision.title}</strong>
                  <p>{decision.decision}</p>
                </div>
              ))}
            </div>
          </article>

          <article className="panel">
            <PanelHeading
              index="05"
              eyebrow="Evidencia"
              title="Artefactos"
              trailing={detail.artifacts.length.toString()}
            />
            <div className="artifact-list">
              {detail.artifacts.slice(-5).map((artifact) => (
                <div className="artifact-item" key={artifact.id}>
                  <span className="file-mark">
                    {artifact.file_paths.some((path) =>
                      path.replaceAll("\\", "/").includes("/project/"),
                    )
                      ? "FILE"
                      : "JSON"}
                  </span>
                  <span>
                    <strong>{artifact.title}</strong>
                    <small>
                      {artifact.file_paths.find((path) =>
                        path.replaceAll("\\", "/").includes("/project/"),
                      ) ?? artifact.artifact_type}
                    </small>
                  </span>
                  <span>✓</span>
                </div>
              ))}
              {detail.artifacts.length === 0 && (
                <p className="empty-copy">Aún no hay artefactos.</p>
              )}
            </div>
          </article>

          <article className="panel metric-panel">
            <PanelHeading index="06" eyebrow="Calidad" title="Métricas" />
            <div className="mini-metrics">
              <div>
                <strong>
                  {percent(detail.metrics.tester_approval_rate)}
                </strong>
                <span>tester</span>
              </div>
              <div>
                <strong>
                  {percent(detail.metrics.reviewer_approval_rate)}
                </strong>
                <span>reviewer</span>
              </div>
              <div>
                <strong>{detail.metrics.retries ?? 0}</strong>
                <span>reintentos</span>
              </div>
              <div>
                <strong>
                  {Math.round(
                    (detail.metrics.average_agent_duration_ms ?? 0) / 1000,
                  )}
                  s
                </strong>
                <span>por agente</span>
              </div>
            </div>
          </article>
        </aside>
      </section>
    </div>
  );
}

function PanelHeading({
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

function ApprovalCenter({
  approvals,
  onResolve,
  loading,
  connected,
}: {
  approvals: Approval[];
  onResolve: (
    id: string,
    status: "approved" | "rejected",
    comments: string,
  ) => Promise<void>;
  loading: boolean;
  connected: boolean;
}) {
  const [comments, setComments] = useState<Record<string, string>>({});

  return (
    <div className="page approvals-page">
      <section className="page-heading">
        <div>
          <span className="kicker">AUTORIDAD HUMANA</span>
          <h1>Decisiones que no deben automatizarse.</h1>
          <p>
            Revisa el motivo, riesgo, alternativas y alcance antes de permitir
            una acción protegida.
          </p>
        </div>
      </section>

      {!connected && approvals.length === 0 && (
        <div className="offline-explainer">
          <span className="alert-icon">i</span>
          <div>
            <strong>No hay API conectada</strong>
            <p>
              Cuando un objetivo requiera publicar, usar credenciales o ejecutar
              una acción destructiva, aparecerá aquí.
            </p>
          </div>
        </div>
      )}

      <div className="approval-list">
        {approvals.map((approval, index) => (
          <article className="approval-card" key={approval.id}>
            <div className="approval-card-top">
              <span className="approval-number">
                {(index + 1).toString().padStart(2, "0")}
              </span>
              <div>
                <span className="eyebrow">Riesgo {approval.risk}</span>
                <h2>{approval.action}</h2>
              </div>
              <Status status={approval.status} />
            </div>
            <p className="approval-reason">{approval.reason}</p>
            <div className="approval-details">
              <div>
                <span className="micro-label">Recursos afectados</span>
                <ul>
                  {approval.affected_resources.map((resource) => (
                    <li key={resource}>{resource}</li>
                  ))}
                </ul>
              </div>
              <div>
                <span className="micro-label">Alternativas</span>
                <ul>
                  {approval.alternatives.map((alternative) => (
                    <li key={alternative}>{alternative}</li>
                  ))}
                </ul>
              </div>
            </div>
            {approval.status === "pending" && (
              <div className="approval-resolution">
                <label>
                  Comentario para los agentes
                  <input
                    value={comments[approval.id] ?? ""}
                    onChange={(event) =>
                      setComments((current) => ({
                        ...current,
                        [approval.id]: event.target.value,
                      }))
                    }
                    placeholder="Añade límites, condiciones o el motivo del rechazo…"
                  />
                </label>
                <div className="approval-actions">
                  <button
                    className="control-button danger"
                    onClick={() =>
                      void onResolve(
                        approval.id,
                        "rejected",
                        comments[approval.id] ?? "",
                      )
                    }
                    disabled={loading}
                  >
                    Rechazar
                  </button>
                  <button
                    className="primary-button"
                    onClick={() =>
                      void onResolve(
                        approval.id,
                        "approved",
                        comments[approval.id] ?? "",
                      )
                    }
                    disabled={loading}
                  >
                    Aprobar acción
                  </button>
                </div>
              </div>
            )}
          </article>
        ))}
        {connected && approvals.length === 0 && (
          <div className="empty-state">
            <span>✓</span>
            <h2>Sin decisiones pendientes</h2>
            <p>La empresa puede avanzar dentro de su autoridad actual.</p>
          </div>
        )}
      </div>
    </div>
  );
}
