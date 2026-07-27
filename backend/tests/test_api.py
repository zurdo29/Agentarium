from agentarium.api.app import create_app
from agentarium.services import ApplicationService
from fastapi.testclient import TestClient


def test_api_exposes_completed_vertical_flow(
    service: ApplicationService,
) -> None:
    app = create_app(service)
    with TestClient(app) as client:
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

        run = client.post(f"/api/projects/{project_id}/run")
        assert run.status_code == 200
        assert run.json()["project"]["status"] == "completed"

        events = client.get(f"/api/projects/{project_id}/events")
        assert events.status_code == 200
        assert len(events.json()) > 5

        dashboard = client.get("/api/dashboard")
        assert dashboard.status_code == 200
        assert dashboard.json()["projects"][0]["progress_percent"] == 100

        metrics = client.get(f"/api/projects/{project_id}/metrics")
        assert metrics.json()["tasks_completed"] == 3
