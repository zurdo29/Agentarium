import pytest
from agentarium.domain.models import ProjectBrief, ScriptExecutionContract, WorkItem
from pydantic import ValidationError


def test_brief_requires_success_criteria() -> None:
    with pytest.raises(ValidationError):
        ProjectBrief(
            project_id="project",
            summary="summary",
            scope=["scope"],
            deliverables=["deliverable"],
        )


def test_work_item_rejects_zero_attempt_budget() -> None:
    with pytest.raises(ValidationError):
        WorkItem(
            project_id="project",
            milestone_id="milestone",
            title="task",
            description="description",
            expected_outputs=["artifact"],
            acceptance_criteria=["passes"],
            max_attempts=0,
        )


def test_work_item_allows_audited_recovery_beyond_ten_attempts() -> None:
    work_item = WorkItem(
        project_id="project",
        milestone_id="milestone",
        title="task",
        description="description",
        expected_outputs=["artifact"],
        acceptance_criteria=["passes"],
        max_attempts=11,
    )

    assert work_item.max_attempts == 11


def test_work_item_retains_a_finite_attempt_ceiling() -> None:
    with pytest.raises(ValidationError):
        WorkItem(
            project_id="project",
            milestone_id="milestone",
            title="task",
            description="description",
            expected_outputs=["artifact"],
            acceptance_criteria=["passes"],
            max_attempts=26,
        )


def test_script_execution_contract_rejects_blank_entrypoint() -> None:
    with pytest.raises(ValidationError):
        ScriptExecutionContract(entrypoint="   ")


@pytest.mark.parametrize(
    "entrypoint",
    ["../tool.py", "/abs/tool.py", "C:/tool.py", "sub/../tool.py"],
)
def test_script_execution_contract_rejects_escaping_entrypoint(
    entrypoint: str,
) -> None:
    with pytest.raises(ValidationError):
        ScriptExecutionContract(entrypoint=entrypoint)


def test_script_execution_contract_requires_a_python_entrypoint() -> None:
    with pytest.raises(ValidationError):
        ScriptExecutionContract(entrypoint="tool.sh")


def test_script_execution_contract_rejects_blank_args_entry() -> None:
    with pytest.raises(ValidationError):
        ScriptExecutionContract(entrypoint="tool.py", args=["input.csv", "   "])


@pytest.mark.parametrize("produces", ["../result.json", "/abs/result.json"])
def test_script_execution_contract_rejects_escaping_produces(produces: str) -> None:
    with pytest.raises(ValidationError):
        ScriptExecutionContract(entrypoint="tool.py", produces=produces)


def test_script_execution_contract_defaults_to_no_args_or_produces() -> None:
    contract = ScriptExecutionContract(entrypoint="tool.py")

    assert contract.args == []
    assert contract.produces is None
