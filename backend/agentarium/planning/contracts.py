from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from agentarium.domain.enums import OutputStrategy, RiskLevel


class PlanningModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# A single whitespace-free token ending in a real extension (at least one
# letter, so "1.2" or "v0.1" are not mistaken for files). Deliberately narrow:
# prose entries like "resultado", "Documento de arquitectura" or "API REST
# funcionando" must not be read as a file claim.
_PATH_CLAIM_PATTERN = re.compile(r"^[^.\s][^\s]*\.[A-Za-z][A-Za-z0-9]{0,7}$")

# Gate-MVP.3 follow-up (ADR 0042): a Markdown-style backtick span inside an
# otherwise prose entry -- "Código modificado en `textkit/slug.py`". The
# planner writes the file it is going to touch this way at least as often as
# it writes it bare, and which spelling it picks is not stable across runs.
_BACKTICK_SEGMENT_PATTERN = re.compile(r"`([^`\n]+)`")

# Suffixes that make a separator-less token recognizable as a file rather than
# a dotted identifier. Only consulted for tokens found *inside* backticks
# (see `implicit_path_claims`), where "re.sub", "str.strip" and "os.path" are
# common and would otherwise pass `_PATH_CLAIM_PATTERN` -- verified against
# the real pattern, not assumed. Conservative on purpose: an unlisted suffix
# means "no claim detected", which is the pre-existing permissive behavior,
# never a false claim.
_FILE_SUFFIXES = frozenset(
    {
        "cfg", "cjs", "css", "csv", "htm", "html", "ini", "js", "json", "jsx",
        "md", "mjs", "ps1", "py", "rst", "sh", "sql", "toml", "ts", "tsx",
        "txt", "xml", "yaml", "yml",
    }
)


def _safe_relative_path(value: str) -> str | None:
    """`value` normalized as a safe relative path, or None.

    Same rules the whole-entry check has always applied, factored out so the
    backtick scan below cannot drift from them.
    """
    candidate = value.strip().replace("\\", "/")
    if not _PATH_CLAIM_PATTERN.match(candidate):
        return None
    if candidate.startswith("/") or ":" in candidate:
        return None
    parts = candidate.split("/")
    if ".." in parts or any(not part for part in parts):
        return None
    return candidate


def _looks_like_a_file(candidate: str) -> bool:
    """Extra signal demanded of a token embedded in prose, never of a whole
    entry: a directory separator, or a suffix that is actually a file type."""
    if "/" in candidate:
        return True
    return candidate.rpartition(".")[2].casefold() in _FILE_SUFFIXES


def implicit_path_claims(expected_outputs: list[str]) -> list[str]:
    """Paths a task effectively claims through `expected_outputs`.

    Models keep naming the file they are going to write in `expected_outputs`
    and leave `owned_paths` empty (ADR 0023 live evidence), so any mechanical
    ownership check has to read the field they actually use. Entries that are
    not safe relative paths are ignored rather than rejected: this is a
    detection helper, not a contract gate.

    Two ways an entry can name a path, with deliberately different bars
    (ADR 0042). The whole entry being a path is an unambiguous declaration of
    intent and keeps the original, looser rule -- unchanged. A token merely
    *embedded* in prose is weaker evidence, so it additionally has to look
    like a file (`_looks_like_a_file`): without that, "Usar `re.sub` para
    limpiar" would claim `re.sub`, arming an ownership boundary with a bogus
    path and rejecting the task's own legitimate delivery -- strictly worse
    than detecting nothing.
    """
    claims: list[str] = []
    seen: set[str] = set()

    def add(candidate: str) -> None:
        key = candidate.casefold()
        if key in seen:
            return
        seen.add(key)
        claims.append(candidate)

    for value in expected_outputs:
        whole = _safe_relative_path(value)
        if whole is not None:
            add(whole)
            continue
        for segment in _BACKTICK_SEGMENT_PATTERN.findall(value):
            inner = _safe_relative_path(segment)
            if inner is not None and _looks_like_a_file(inner):
                add(inner)
    return claims


def merge_path_claims(owned_paths: list[str], expected_outputs: list[str]) -> list[str]:
    """Every path a task claims: declared `owned_paths` plus implicit ones."""
    claims = list(owned_paths)
    seen = {claim.strip().replace("\\", "/").casefold() for claim in claims}
    for claim in implicit_path_claims(expected_outputs):
        if claim.casefold() in seen:
            continue
        seen.add(claim.casefold())
        claims.append(claim)
    return claims


def _reject_unsafe_owned_paths(values: list[str]) -> list[str]:
    cleaned = [value.strip() for value in values]
    if any(not value for value in cleaned):
        raise ValueError("owned_paths cannot contain blank entries")
    for value in cleaned:
        normalized = value.replace("\\", "/")
        if normalized.startswith("/") or ":" in normalized:
            raise ValueError(f"owned_paths must be relative: {value!r}")
        parts = normalized.split("/")
        if ".." in parts or any(not part for part in parts):
            raise ValueError(f"owned_paths cannot escape the workspace: {value!r}")
    return cleaned


class BriefProposal(PlanningModel):
    summary: str = Field(min_length=1)
    scope: list[str] = Field(min_length=1)
    deliverables: list[str] = Field(min_length=1)
    assumptions: list[str] = Field(default_factory=list)
    ambiguities: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(min_length=1)
    approvals_required: list[str] = Field(default_factory=list)

    @field_validator(
        "scope",
        "deliverables",
        "assumptions",
        "ambiguities",
        "constraints",
        "success_criteria",
        "approvals_required",
    )
    @classmethod
    def reject_blank_entries(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value for value in cleaned):
            raise ValueError("Planning lists cannot contain blank entries")
        return cleaned


class MilestoneProposal(PlanningModel):
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)


class TaskProposal(PlanningModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    dependencies: list[str] = Field(default_factory=list)
    expected_outputs: list[str] = Field(min_length=1)
    acceptance_criteria: list[str] = Field(min_length=1)
    risk: RiskLevel
    priority: int = Field(ge=0, le=100)
    owned_paths: list[str] = Field(default_factory=list, max_length=20)
    shared_component: str | None = Field(default=None, max_length=120)
    output_strategy: OutputStrategy = OutputStrategy.EXCLUSIVE

    @field_validator("dependencies", "expected_outputs", "acceptance_criteria")
    @classmethod
    def reject_blank_task_entries(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value for value in cleaned):
            raise ValueError("Task lists cannot contain blank entries")
        return cleaned

    @field_validator("owned_paths")
    @classmethod
    def validate_owned_paths(cls, values: list[str]) -> list[str]:
        return _reject_unsafe_owned_paths(values)

    def claimed_paths(self) -> list[str]:
        return merge_path_claims(self.owned_paths, self.expected_outputs)


ACCEPTANCE_CRITERION_ID_PATTERN = r"^ac-[1-9][0-9]{0,2}$"


def acceptance_criteria_index(criteria: list[str]) -> list[dict[str, str]]:
    """`ac-1`, `ac-2`, … for the criteria a task must hand to a decompose call.

    The ids exist so a subtask can *point at* a parent criterion instead of
    restating it: a small model rewrites the text almost every time, and the
    rewritten text is what used to make the coverage check fail.
    """
    return [
        {"id": f"ac-{position}", "text": criterion}
        for position, criterion in enumerate(criteria, start=1)
    ]


class SubtaskProposal(PlanningModel):
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    expected_outputs: list[str] = Field(min_length=1)
    acceptance_criteria: list[str] = Field(min_length=1)
    acceptance_criteria_ids: list[str] = Field(default_factory=list, max_length=50)
    owned_paths: list[str] = Field(default_factory=list, max_length=20)
    shared_component: str | None = Field(default=None, max_length=120)
    output_strategy: OutputStrategy = OutputStrategy.EXCLUSIVE

    @field_validator("acceptance_criteria_ids")
    @classmethod
    def validate_criteria_id_shape(cls, values: list[str]) -> list[str]:
        # Shape only. Whether an id actually exists, is unique across the whole
        # decomposition and leaves nothing uncovered can only be decided
        # against the parent task, so it lives in the orchestrator.
        cleaned = [value.strip().casefold() for value in values]
        for value in cleaned:
            if not re.match(ACCEPTANCE_CRITERION_ID_PATTERN, value):
                raise ValueError(
                    f"acceptance_criteria_ids must look like 'ac-1': {value!r}"
                )
        return cleaned

    @field_validator("expected_outputs", "acceptance_criteria")
    @classmethod
    def reject_blank_subtask_entries(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values]
        if any(not value for value in cleaned):
            raise ValueError("Subtask lists cannot contain blank entries")
        return cleaned

    @field_validator("owned_paths")
    @classmethod
    def validate_owned_paths(cls, values: list[str]) -> list[str]:
        return _reject_unsafe_owned_paths(values)

    def claimed_paths(self) -> list[str]:
        return merge_path_claims(self.owned_paths, self.expected_outputs)


class DecomposeProposal(PlanningModel):
    subtasks: list[SubtaskProposal] = Field(min_length=2, max_length=4)

    @model_validator(mode="after")
    def validate_unique_titles(self) -> DecomposeProposal:
        titles = [subtask.title.strip().casefold() for subtask in self.subtasks]
        if len(set(titles)) != len(titles):
            raise ValueError("Subtask titles must be unique")
        return self

    @model_validator(mode="after")
    def validate_no_unresolved_path_overlap(self) -> DecomposeProposal:
        # All subtasks of one decompose call end up grouped under the same
        # shared_component by _attempt_split regardless of what's declared
        # here (they're siblings from the same split by construction), so
        # matching shared_component isn't required — only an explicit
        # "exclusive" claim on a path another subtask also wants is a real,
        # unresolved conflict. The claim is read from owned_paths *and*
        # expected_outputs: the second is the field models actually use to say
        # "this file is mine" (ADR 0023 live evidence), so looking only at the
        # first made this gate unreachable in practice.
        claimed: dict[str, SubtaskProposal] = {}
        for subtask in self.subtasks:
            for path in subtask.claimed_paths():
                key = path.casefold()
                owner = claimed.get(key)
                if owner is None:
                    claimed[key] = subtask
                    continue
                grouped = (
                    owner.output_strategy != OutputStrategy.EXCLUSIVE
                    and subtask.output_strategy != OutputStrategy.EXCLUSIVE
                )
                if not grouped:
                    raise ValueError(
                        f"path claim overlap between subtasks without a "
                        f"non-exclusive output_strategy on both sides: {path!r}"
                    )
        return self


class PlanProposal(PlanningModel):
    milestone: MilestoneProposal
    tasks: list[TaskProposal] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_dag(self) -> PlanProposal:
        keys = [task.key for task in self.tasks]
        if len(set(keys)) != len(keys):
            raise ValueError("Task keys must be unique")

        titles = [task.title.strip().casefold() for task in self.tasks]
        if len(set(titles)) != len(titles):
            raise ValueError("Task titles must be unique")

        known = set(keys)
        graph = {task.key: task.dependencies for task in self.tasks}
        for task in self.tasks:
            if len(set(task.dependencies)) != len(task.dependencies):
                raise ValueError(f"Task {task.key!r} repeats a dependency")
            if not set(task.dependencies).issubset(known):
                raise ValueError(f"Task {task.key!r} references an unknown dependency")

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> None:
            if node in visiting:
                raise ValueError("Task dependency graph contains a cycle")
            if node in visited:
                return
            visiting.add(node)
            for dependency in graph[node]:
                visit(dependency)
            visiting.remove(node)
            visited.add(node)

        for key in keys:
            visit(key)
        return self
