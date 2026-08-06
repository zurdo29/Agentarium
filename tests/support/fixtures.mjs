// Hand-authored fixture factories shaped like the TS types app/page.tsx
// declares (Project, ProjectDetail, DashboardData, ...). Necessarily a
// duplicate: those types aren't exported, and are erased at runtime
// either way. This can drift from the real shapes silently -- the
// P3.1a gate (docs/decisions/0030-*) covers backend<->TypeScript, not
// TypeScript<->test-fixture, and closing that second gap is not part of
// this PR. Modeled closely on page.tsx's own SAMPLE_* constants, which
// are the closest thing to a source of truth for what "realistic" means
// here.

let idCounter = 0;
function nextId(prefix) {
  idCounter += 1;
  return `${prefix}-${idCounter}`;
}

export function buildProject(overrides = {}) {
  return {
    id: nextId("project"),
    title: "Proyecto de prueba",
    goal: "Meta de prueba para P3.1b.",
    status: "running",
    progress_percent: 50,
    current_milestone_id: "milestone-1",
    tasks_total: 2,
    tasks_completed: 1,
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:10:00Z",
    ...overrides,
  };
}

export function buildBrief(overrides = {}) {
  return {
    summary: "Resumen de prueba.",
    scope: ["Alcance A"],
    deliverables: ["Entregable A"],
    assumptions: [],
    ambiguities: [],
    constraints: [],
    success_criteria: ["El flujo se completa"],
    ...overrides,
  };
}

export function buildWorkItem(overrides = {}) {
  return {
    id: nextId("task"),
    title: "Tarea de prueba",
    description: "Descripción de la tarea de prueba.",
    status: "running",
    assignee_role: "implementation_worker",
    dependency_ids: [],
    acceptance_criteria: ["Cumple el criterio"],
    expected_outputs: ["result"],
    attempt_count: 1,
    max_attempts: 3,
    risk: "low",
    priority: 50,
    ...overrides,
  };
}

export function buildMilestone(overrides = {}) {
  return {
    id: "milestone-1",
    title: "Hito de prueba",
    description: "Descripción del hito.",
    completed: false,
    ...overrides,
  };
}

export function buildDependency(overrides = {}) {
  return {
    id: nextId("dep"),
    work_item_id: "task-1",
    depends_on_id: "task-0",
    ...overrides,
  };
}

export function buildArtifact(overrides = {}) {
  return {
    id: nextId("artifact"),
    work_item_id: "task-1",
    title: "Artefacto de prueba",
    artifact_type: "implementation_artifact",
    file_paths: ["workspaces/test/artifacts/output.json"],
    created_at: "2026-08-01T00:05:00Z",
    ...overrides,
  };
}

export function buildReview(overrides = {}) {
  return {
    id: nextId("review"),
    work_item_id: "task-1",
    verdict: "approved",
    reasons: ["La evidencia es explícita."],
    ...overrides,
  };
}

// `command_evidence` entries mirror what backend/agentarium/execution
// actually produces (verified while building the P3.1a gate): each
// check kind populates a different subset of these optional fields.
export function buildCommandEvidence(overrides = {}) {
  return {
    check: "validation_profile",
    profile: "workspace_inventory",
    passed: true,
    return_code: 0,
    ...overrides,
  };
}

export function buildTestReport(overrides = {}) {
  return {
    id: nextId("test-report"),
    work_item_id: "task-1",
    passed: true,
    summary: "Comprobaciones automáticas superadas.",
    checks: [{ name: "file_exists", passed: true, evidence: "Checksum verificado." }],
    command_evidence: [],
    ...overrides,
  };
}

export function buildDecision(overrides = {}) {
  return {
    id: nextId("decision"),
    title: "Decisión de prueba",
    decision: "Se eligió el camino simple.",
    rationale: "Es reversible.",
    reversible: true,
    ...overrides,
  };
}

export function buildApproval(overrides = {}) {
  return {
    id: nextId("approval"),
    project_id: "project-1",
    work_item_id: null,
    action: "Escalar tarea bloqueada",
    reason: "El CEO debe revisar el bloqueo.",
    risk: "medium",
    alternatives: [],
    affected_resources: [],
    status: "pending",
    comments: null,
    created_at: "2026-08-01T00:00:00Z",
    resolved_at: null,
    ...overrides,
  };
}

export function buildEventRecord(overrides = {}) {
  return {
    sequence: 1,
    id: nextId("event"),
    action: "task_state_changed",
    message: "Evento de prueba.",
    timestamp: "2026-08-01T00:05:00Z",
    ...overrides,
  };
}

export function buildProjectDetail(overrides = {}) {
  const { project, ...rest } = overrides;
  return {
    project: buildProject(project),
    milestones: [buildMilestone()],
    work_items: [buildWorkItem({ id: "task-1" })],
    dependencies: [],
    artifacts: [],
    reviews: [],
    test_reports: [],
    decisions: [],
    approvals: [],
    metrics: {
      tasks_completed: 1,
      tasks_rejected: 0,
      retries: 0,
      tester_approval_rate: 1,
      reviewer_approval_rate: 1,
      average_agent_duration_ms: 1000,
    },
    ...rest,
  };
}

export function buildDashboard(overrides = {}) {
  return {
    runtime: {
      mode: "workspace",
      providers: ["mock"],
      active_model: null,
      capabilities: {
        model_inference: false,
        project_files: true,
        command_execution: true,
        change_isolation: true,
      },
    },
    projects: [],
    active_agents: 0,
    blocked_tasks: 0,
    pending_approvals: 0,
    latest_errors: [],
    ...overrides,
  };
}

export function buildProviderOverview(overrides = {}) {
  return {
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
        message: "Respaldo local estable.",
        selected: true,
        selected_model: null,
      },
    ],
    ...overrides,
  };
}
