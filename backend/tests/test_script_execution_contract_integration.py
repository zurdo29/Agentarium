from __future__ import annotations

import json

import pytest
from agentarium.domain.enums import RiskLevel, WorkItemStatus
from agentarium.domain.models import ScriptExecutionContract, WorkItem
from agentarium.llm import ProviderResponse
from agentarium.llm.mock import MockProvider
from agentarium.services import ApplicationService

# Direct calls to ValidationProfileExecutor.validate() (test_validation_profiles.py)
# prove the contract logic itself. This proves the wiring around it: a WorkItem
# built with an execution_contract, run through the real Orchestrator path
# (_execute_work_item -> validate(execution_contract=item.execution_contract)),
# actually has its declared args and produces respected — not just when called
# in isolation.


@pytest.mark.asyncio
async def test_execute_work_item_honors_a_manually_declared_execution_contract(
    service: ApplicationService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = service.create_project("Entregar una herramienta de linea de comandos")
    await service.orchestrator.plan_project(project.id)
    milestone_id = service.repository.list_work_items(project.id)[0].milestone_id

    # No planning path can produce an execution_contract today (ADR 0027) —
    # built by hand here, same as P1.3b's reviewer-reaching test built its
    # WorkItem by hand rather than relying on the planner to produce one.
    item = WorkItem(
        project_id=project.id,
        milestone_id=milestone_id,
        title="Entregar tool.py",
        description="Entregar una herramienta ejecutable con un contrato real",
        expected_outputs=["tool.py"],
        acceptance_criteria=["El script produce result.json a partir del argumento recibido"],
        max_attempts=1,
        risk=RiskLevel.LOW,
        status=WorkItemStatus.READY,
        execution_contract=ScriptExecutionContract(
            entrypoint="tool.py", args=["valor-real"], produces="result.json"
        ),
    )
    service.repository.add_work_item(item)

    original_generate = MockProvider.generate
    captured_validation_profiles: list[list[dict[str, object]]] = []

    async def controlled_work_and_capture(self, request, agent):  # type: ignore[no-untyped-def]
        if request.operation == "work" and request.work_item_id == item.id:
            content = {
                "artifact_type": "code",
                "title": "Herramienta",
                "summary": "Entrega tool.py",
                "quality": "verified",
                "files": [
                    {
                        "path": "tool.py",
                        "content": (
                            "import sys\n"
                            "if len(sys.argv) < 2:\n"
                            "    raise SystemExit('falta el argumento real')\n"
                            "with open('result.json', 'w', encoding='utf-8') as handle:\n"
                            "    handle.write('{\"valor\": \"' + sys.argv[1] + '\"}')\n"
                        ),
                        "purpose": "Herramienta que exige el argumento declarado",
                    }
                ],
            }
            raw = json.dumps(content)
            return ProviderResponse(
                content=content,
                raw_text=raw,
                prompt_characters=len(raw),
                response_characters=len(raw),
            )
        if request.operation == "test" and request.work_item_id == item.id:
            # The tester's payload carries the exact evidence validate()
            # produced for this attempt (engine.py) — capture it before
            # letting the default mock behaviour continue the flow.
            captured_validation_profiles.append(
                list(request.payload["validation_profiles"])
            )
        return await original_generate(self, request, agent)

    monkeypatch.setattr(MockProvider, "generate", controlled_work_and_capture)

    await service.orchestrator._execute_work_item(item, "test-correlation")

    assert captured_validation_profiles, "el tester nunca fue invocado para esta tarea"
    script_checks = [
        check
        for check in captured_validation_profiles[0]
        if check["profile"] == "script_execution"
    ]

    # Exactly one result: the declared entrypoint ran once, with its real
    # args (proving the contract's args reached the actual subprocess call),
    # and produced the declared output (proving `produces` was satisfied) —
    # no second failing result was appended for a missing artifact.
    assert len(script_checks) == 1
    assert script_checks[0]["passed"]
    assert script_checks[0]["command"][-1] == "valor-real"
