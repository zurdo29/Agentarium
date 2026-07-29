from agentarium.api.app import create_app
from agentarium.services import ApplicationService
from fastapi.testclient import TestClient


def test_api_exposes_completed_vertical_flow(
    service: ApplicationService,
) -> None:
    app = create_app(service)
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["runtime"] == {
            "mode": "workspace",
            "providers": ["mock"],
            "active_model": None,
            "capabilities": {
                "model_inference": False,
                "project_files": True,
                "command_execution": True,
                "change_isolation": True,
            },
        }

        created = client.post(
            "/api/projects",
            json={
                "goal": "Crear un prototipo local verificable",
                "auto_plan": True,
            },
        )
        assert created.status_code == 201
        project_id = created.json()["project"]["id"]
        assert len(created.json()["work_items"]) == 3
        blocked_task = created.json()["work_items"][1]

        priority = client.patch(
            f"/api/work-items/{blocked_task['id']}/priority",
            json={"priority": 95},
        )
        assert priority.status_code == 200
        assert priority.json()["priority"] == 95

        escalation = client.post(
            f"/api/work-items/{blocked_task['id']}/escalate",
            json={"reason": "El CEO debe confirmar el alcance de esta tarea."},
        )
        assert escalation.status_code == 200
        assert escalation.json()["status"] == "pending"
        resolved = client.post(
            f"/api/approvals/{escalation.json()['id']}/resolve",
            json={
                "status": "approved",
                "comments": "Continuar con alcance pequeño.",
            },
        )
        assert resolved.status_code == 200

        run = client.post(f"/api/projects/{project_id}/run")
        assert run.status_code == 200
        assert run.json()["project"]["status"] == "completed"

        events = client.get(f"/api/projects/{project_id}/events")
        assert events.status_code == 200
        assert len(events.json()) > 5

        dashboard = client.get("/api/dashboard")
        assert dashboard.status_code == 200
        assert dashboard.json()["runtime"]["mode"] == "workspace"
        assert dashboard.json()["projects"][0]["progress_percent"] == 100

        metrics = client.get(f"/api/projects/{project_id}/metrics")
        assert metrics.json()["tasks_completed"] == 3


def test_api_can_activate_a_ready_provider(
    service: ApplicationService,
    monkeypatch: object,
) -> None:
    from agentarium.llm import ProviderDiagnostic

    async def diagnostics(*, force: bool = False) -> list[ProviderDiagnostic]:
        del force
        return [
            ProviderDiagnostic(
                name="mock",
                label="Motor determinista",
                reachable=True,
                ready=True,
                message="Respaldo disponible.",
            ),
            ProviderDiagnostic(
                name="ollama",
                label="Ollama local",
                endpoint="http://127.0.0.1:11434",
                reachable=True,
                ready=True,
                models=["qwen3:4b"],
                message="1 modelo disponible.",
            ),
            ProviderDiagnostic(
                name="openai_compatible",
                label="Servidor compatible",
                reachable=False,
                ready=False,
                message="No responde.",
            ),
        ]

    monkeypatch.setattr(service.providers, "diagnostics", diagnostics)  # type: ignore[attr-defined]
    app = create_app(service)
    with TestClient(app) as client:
        before = client.get("/api/providers")
        assert before.status_code == 200
        assert before.json()["active_provider"] == "mock"

        selected = client.put(
            "/api/runtime/provider",
            json={"provider": "ollama", "model": "qwen3:4b"},
        )
        assert selected.status_code == 200
        assert selected.json()["active_provider"] == "ollama"
        assert selected.json()["active_model"] == "qwen3:4b"

        health = client.get("/api/health")
        assert health.json()["runtime"]["providers"] == ["ollama"]
        assert health.json()["runtime"]["active_model"] == "qwen3:4b"
        assert health.json()["runtime"]["capabilities"]["model_inference"] is True

        agents = client.get("/api/agents").json()["definitions"]
        assert {agent["provider"] for agent in agents} == {"ollama"}
        assert {agent["model"] for agent in agents} == {"qwen3:4b"}


def test_api_keeps_mock_when_provider_is_not_ready(
    service: ApplicationService,
    monkeypatch: object,
) -> None:
    from agentarium.llm import ProviderDiagnostic

    async def diagnostics(*, force: bool = False) -> list[ProviderDiagnostic]:
        del force
        return [
            ProviderDiagnostic(
                name="mock",
                label="Motor determinista",
                reachable=True,
                ready=True,
                message="Respaldo disponible.",
            ),
            ProviderDiagnostic(
                name="ollama",
                label="Ollama local",
                reachable=False,
                ready=False,
                message="Ollama no responde.",
            ),
            ProviderDiagnostic(
                name="openai_compatible",
                label="Servidor compatible",
                reachable=False,
                ready=False,
                message="No responde.",
            ),
        ]

    monkeypatch.setattr(service.providers, "diagnostics", diagnostics)  # type: ignore[attr-defined]
    app = create_app(service)
    with TestClient(app) as client:
        selected = client.put(
            "/api/runtime/provider",
            json={"provider": "ollama", "model": "qwen3:4b"},
        )
        assert selected.status_code == 409
        assert selected.json()["detail"] == "Ollama no responde."
        assert client.get("/api/health").json()["provider"] == "mock"
