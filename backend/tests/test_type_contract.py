"""Integration entry point for the P3.1a backend<->TypeScript contract
gate. Builds one real, fully-executed project (mock provider -- same
scenario `test_vertical_flow.py` already relies on for a fully populated
project) and checks every TS type registered in `contract_registry.py`
against a real, live response from the same FastAPI app the frontend
actually talks to. See `contract_types.py` for how a shape is derived and
compared, and `contract_registry.py` for what gets checked.

Nothing here (or in anything it imports) adds `response_model=` to a
route or otherwise changes what the API returns -- this only reads.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from typing import Any

import pytest
from agentarium.api.app import create_app
from agentarium.config.settings import project_root
from agentarium.domain.enums import WorkItemStatus
from agentarium.domain.models import ApprovalRequest, Decision, Milestone, Project, WorkItem
from agentarium.services import ApplicationService
from contract_registry import ALL_TS_TYPE_NAMES, DIRECT_PAIRS, ENDPOINT_PAIRS
from contract_types import ShapeTree, diff_shapes, shape_from_model, shape_from_value
from fastapi.testclient import TestClient

_FIXTURE_GOAL = "Crear un pequeño ARPG con progresión de objetos y alcance de prototipo"


def _extract_ts_shapes() -> dict[str, Any]:
    result = subprocess.run(
        ["node", "scripts/check-api-contract.mjs", "app/page.tsx", *ALL_TS_TYPE_NAMES],
        cwd=project_root(),
        capture_output=True,
        text=True,
        # Windows subprocess pipes default to the system codepage, not
        # UTF-8, without this -- the extractor's JSON output is ASCII
        # today, but pinning it keeps that from becoming a latent bug the
        # moment a non-ASCII string literal appears in a checked type.
        encoding="utf-8",
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(
            f"scripts/check-api-contract.mjs failed (exit {result.returncode}):\n{result.stderr}"
        )
    return json.loads(result.stdout)


def _drill(value: Any, at: tuple[object, ...]) -> Any:
    for step in at:
        value = value[step]
    return value


@pytest.mark.asyncio
async def test_backend_ts_contract_has_no_drift(service: ApplicationService) -> None:
    # RepairItem needs a real, non-empty /api/repair-center response to
    # check against -- the main fixture below never fails, so it alone
    # would leave that endpoint empty. Built with an explicit, deliberately
    # earlier created_at (not relying on real-clock ordering between two
    # back-to-back constructor calls): Repository.list_projects() orders
    # by created_at descending, so this stays out of index 0, which
    # "Project"/"DashboardData" already depend on being the completed
    # fixture project below.
    repair_project = service.repository.create_project(
        Project(
            title="Proyecto con una tarea que necesita reparación",
            goal="Proyecto con una tarea que necesita reparación",
            created_at=datetime(2020, 1, 1, tzinfo=UTC),
        )
    )
    repair_milestone = Milestone(
        project_id=repair_project.id,
        title="Entrega",
        description="Entrega local",
        order=0,
    )
    service.repository.add_milestone(repair_milestone)
    service.repository.add_work_item(
        WorkItem(
            project_id=repair_project.id,
            milestone_id=repair_milestone.id,
            title="Tarea fallida",
            description="Descripción de la tarea fallida",
            expected_outputs=["resultado"],
            acceptance_criteria=["Existe"],
            status=WorkItemStatus.FAILED,
            last_error="Fallo real para el contrato de RepairItem",
        )
    )

    project = service.create_project(_FIXTURE_GOAL)
    await service.run_project(project.id)

    ts_shapes = _extract_ts_shapes()

    py_shapes: dict[str, ShapeTree] = {
        name: shape_from_model(model) for name, model in DIRECT_PAIRS.items()
    }

    with TestClient(create_app(service)) as client:
        for name, (path_template, at) in ENDPOINT_PAIRS.items():
            path = path_template.format(project_id=project.id)
            response = client.get(path)
            assert response.status_code == 200, f"GET {path} returned {response.status_code}"
            py_shapes[name] = shape_from_value(_drill(response.json(), at))

    # `decisions`/`approvals`: this one fixture run is not guaranteed to
    # produce either (see shape_from_value's docstring on observed-vs-
    # -exhaustive shapes) -- overlay the real class shape for these two
    # nested element positions so an empty list on this particular run
    # doesn't silently stop checking them.
    project_detail_properties = py_shapes["ProjectDetail"].get("properties")
    assert project_detail_properties is not None, "ProjectDetail response was not an object"
    project_detail_properties["decisions"] = {
        **project_detail_properties["decisions"],
        "element": shape_from_model(Decision),
    }
    project_detail_properties["approvals"] = {
        **project_detail_properties["approvals"],
        "element": shape_from_model(ApprovalRequest),
    }

    problems: list[str] = []
    for name in ALL_TS_TYPE_NAMES:
        ts_shape = ts_shapes[name]
        if "error" in ts_shape:
            problems.append(f"{name}: not found as a top-level `type` in app/page.tsx.")
            continue
        problems.extend(diff_shapes(ts_shape, py_shapes[name], name))

    assert not problems, "Backend<->TypeScript contract drift:\n" + "\n".join(
        f"- {problem}" for problem in problems
    )
