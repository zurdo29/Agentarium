"""Which TS type checks against which real Python source -- selection
only. No field kind is ever hand-typed here; `test_type_contract.py`
resolves each entry into a shape via `contract_types.shape_from_model`
(introspects a real Pydantic class) or `contract_types.shape_from_value`
(walks a real response from the running FastAPI app), never a literal
string like `"tasks_total": "number"` written by hand in this file.

Two kinds of pair:

`DIRECT_PAIRS` -- the TS type is a 1:1 mirror of one real domain/schema
class, dumped as-is (`model.model_dump(mode="json")`, no extra keys
spliced in by the route). Shape comes straight from `model_fields`.

`ENDPOINT_PAIRS` -- the TS type corresponds to a real HTTP response (or a
piece of one) that has no single backing class -- either a composite
dict assembled by hand in `app.py`/`services/application.py`, or a real
class with a couple of extra keys spliced onto it before it goes out.
`path` is a route on the running app (`{project_id}` is substituted by
the test with a real fixture project's id); `at` is the sequence of
dict keys / list indices to drill into that JSON response to reach the
piece this particular TS type describes -- `()` means the whole response
*is* the shape.
"""

from __future__ import annotations

from agentarium.api.schemas import RuntimeStatus
from agentarium.domain.models import ApprovalRequest, ProjectBrief, WorkItem
from pydantic import BaseModel

DIRECT_PAIRS: dict[str, type[BaseModel]] = {
    "WorkItem": WorkItem,
    "Brief": ProjectBrief,
    "Approval": ApprovalRequest,
    "RuntimeStatus": RuntimeStatus,
}

# `EventRecord` is registered here even though `ExecutionEvent` is a real
# class: `Repository._event_from_row` never actually constructs one (see
# repositories/repository.py) -- it hand-builds a dict with an extra
# `sequence` column ExecutionEvent has no field for, so this can't be a
# DIRECT pair without false-positiving on `sequence` from day one.
ENDPOINT_PAIRS: dict[str, tuple[str, tuple[object, ...]]] = {
    "Project": ("/api/dashboard", ("projects", 0)),
    "EventRecord": ("/api/projects/{project_id}/events", (0,)),
    "ProviderDiagnostic": ("/api/providers", ("providers", 0)),
    "ProjectDetail": ("/api/projects/{project_id}", ()),
    "DashboardData": ("/api/dashboard", ()),
    "ProviderOverview": ("/api/providers", ()),
    "ExportSummary": ("/api/projects/{project_id}/export/preview", ()),
    "RepairItem": ("/api/repair-center", (0,)),
}

ALL_TS_TYPE_NAMES: tuple[str, ...] = tuple(DIRECT_PAIRS) + tuple(ENDPOINT_PAIRS)
