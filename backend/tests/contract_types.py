"""Shape extraction and diffing for the P3.1a backend<->TypeScript contract
gate (`test_type_contract.py`) and its own regression suite
(`test_contract_types.py`). Holds no data of its own -- what to check
lives in `contract_registry.py`; this module only knows how to compare
two shape trees.

A "shape" is a plain dict: `{kind, nullable, element?, properties?,
opaque?}`. `nullable` belongs to a value's own type -- it can appear
wherever a type can, not only on an object property. `optional` belongs
to a *property* (a key can be absent from its containing object) and is
only ever attached where a shape is stored inside another shape's
`properties` mapping. These are independent axes on purpose: a
TypeScript `field?: string` (optional, non-nullable) does not admit
`null`, and is therefore NOT equivalent to `field: string | null` for a
Python `str | None` field -- collapsing the two was a real defect caught
in plan review, not a style choice. See `test_contract_types.py` for the
regression that pins this down. The exact same flat shape (`kind` next to
`nullable`/`optional`, not nested under them) is produced independently
by `scripts/check-api-contract.mjs` on the TypeScript side, so the two
trees compare directly with no translation step.
"""

from __future__ import annotations

import types
import typing
from datetime import datetime
from enum import Enum
from typing import Any, TypedDict

from pydantic import BaseModel

Kind = str  # "string" | "number" | "boolean" | "array" | "object" | "unknown"

_UNION_ORIGINS = (typing.Union, types.UnionType)


class ShapeTree(TypedDict, total=False):
    kind: Kind
    nullable: bool
    optional: bool
    element: ShapeTree
    properties: dict[str, ShapeTree]
    opaque: bool


def shape_from_model(model: type[BaseModel]) -> ShapeTree:
    """The real, always-derived source of truth for a domain model's wire
    shape: `model_fields` is literally what `model_dump(mode="json")` uses
    to serialize, so this is never a hand-typed duplicate of it.

    Deliberately reads `FieldInfo.annotation`, never `FieldInfo.is_required()`.
    `is_required()` answers "does the constructor need this argument",
    which is a different axis from "can this appear as `null` on the
    wire". `ProjectBrief.assumptions: list[str] = Field(default_factory=list)`
    is not required but is never nullable either -- nothing in this
    codebase calls `model_dump(exclude_none=...)`, so a field is always
    present at its default (`[]`, `0`, ...), never nulled out or omitted.
    """
    properties: dict[str, ShapeTree] = {}
    for name, field_info in model.model_fields.items():
        inner = _shape_from_annotation(field_info.annotation)
        properties[name] = {**inner, "optional": False}
    return {"kind": "object", "nullable": False, "properties": properties}


def shape_from_value(value: Any, *, opaque_dict: bool = False) -> ShapeTree:
    """Shape of one concrete, already-serialized Python/JSON value.

    This is what backs the composite endpoints (`ProjectDetail`,
    `DashboardData`, `ProviderOverview`) that have no single Pydantic
    class behind them -- `test_type_contract.py` calls the real API via
    `TestClient` and walks the actual response with this function instead
    of a hand-typed description.

    IMPORTANT LIMITATION, by construction, not oversight: this reflects a
    runtime shape *observed in one concrete run* of the test fixture (a
    project driven through the `mock` provider), not an exhaustive
    schema. An empty list has no element to recurse into, and a key that
    only appears under a condition the fixture never hits simply isn't
    seen. `test_type_contract.py` falls back to `shape_from_model` for the
    handful of nested pieces (`decisions`, `approvals`) the fixture does
    not reliably populate, and says so where it does it.
    """
    if value is None:
        return {"kind": "unknown", "nullable": True}
    if isinstance(value, bool):
        return {"kind": "boolean", "nullable": False}
    if isinstance(value, (int, float)):
        return {"kind": "number", "nullable": False}
    if isinstance(value, str):
        return {"kind": "string", "nullable": False}
    if isinstance(value, list):
        non_null = next((item for item in value if item is not None), None)
        nullable_element = any(item is None for item in value)
        element = (
            _mark_nullable(shape_from_value(non_null), nullable=nullable_element)
            if non_null is not None
            else {"kind": "unknown", "nullable": nullable_element or not value}
        )
        return {"kind": "array", "nullable": False, "element": element}
    if isinstance(value, dict):
        if opaque_dict:
            return {"kind": "object", "nullable": False, "opaque": True}
        properties = {
            key: {**shape_from_value(item), "optional": False} for key, item in value.items()
        }
        return {"kind": "object", "nullable": False, "properties": properties}
    return {"kind": "unknown", "nullable": False}


def _mark_nullable(shape: ShapeTree, *, nullable: bool) -> ShapeTree:
    if not nullable:
        return shape
    return {**shape, "nullable": True}


def _shape_from_annotation(annotation: Any) -> ShapeTree:
    origin = typing.get_origin(annotation)

    if origin in _UNION_ORIGINS:
        args = typing.get_args(annotation)
        non_none = [arg for arg in args if arg is not type(None)]
        nullable = len(non_none) != len(args)
        if not non_none:
            return {"kind": "unknown", "nullable": True}
        if len(non_none) == 1:
            inner = _shape_from_annotation(non_none[0])
            return {**inner, "nullable": inner.get("nullable", False) or nullable}
        # Multiple non-None members with no single Literal/Enum covering
        # all of them doesn't occur in this codebase today; fall back
        # conservatively rather than guess at a kind.
        return {"kind": "unknown", "nullable": nullable}

    if typing.get_origin(annotation) is typing.Literal:
        return {"kind": "string", "nullable": False}

    if annotation is str or annotation is datetime:
        return {"kind": "string", "nullable": False}
    if annotation is int or annotation is float:
        return {"kind": "number", "nullable": False}
    if annotation is bool:
        return {"kind": "boolean", "nullable": False}

    if origin is list:
        (element_type,) = typing.get_args(annotation) or (Any,)
        return {"kind": "array", "nullable": False, "element": _shape_from_annotation(element_type)}
    if origin is dict:
        # Opaque, matching TS `Record<K, V>` -- see module docstring: no
        # per-key comparison, both sides deliberately don't model this.
        return {"kind": "object", "nullable": False, "opaque": True}

    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return {"kind": "string", "nullable": False}
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return shape_from_model(annotation)

    return {"kind": "unknown", "nullable": False}


def diff_shapes(ts: ShapeTree, py: ShapeTree, path: str) -> list[str]:
    """Asymmetric on purpose. A field Python has and TS doesn't is not a
    failure -- the frontend simply doesn't consume it (the common case:
    e.g. `WorkItem` only reads 13 of its 26 real fields). A field TS
    references that Python doesn't have *is* a failure **unless that TS
    field is itself `optional` (`field?: T`)** -- `?:` already tells the
    frontend the key may be absent, so a specific real response lacking it
    is exactly what the type promises, not a hole. Confirmed against two
    real cases found by actually running this against the app, not
    designed in the abstract: TS's `Project` type is shared between
    `/api/dashboard` (splices in `tasks_total?`/`tasks_completed?`) and
    `project_detail()`'s plain `project` key (which never has them) --
    both fields are declared `?:` precisely because they're not always
    there. Likewise every field but `check` in `TestReport.command_evidence`
    (backend `dict[str, Any]`, no fixed schema) is `?:` because different
    check kinds populate different subsets. A **non-optional** TS field
    (`field: T`) with no Python source is still a hard failure -- the
    frontend would read `undefined` where the type promises `T` always
    exists, the actual risk this gate exists to catch.

    Nullability is checked independently from optionality, in the
    TS-is-at-least-as-permissive-as-Python direction only: Python
    `nullable=True` requires TS `nullable=True` on the same field (its
    type must include `| null`) -- a bare `field?: T` without `| null`
    does NOT satisfy it, because nothing in this codebase's
    `model_dump()` calls ever omits a key (no `exclude_none`), so a
    `None` value always reaches the wire as a real `"field": null`, not
    an absent key. This is a separate axis from the presence check above
    and is never relaxed by `optional`.
    """
    if ts.get("kind") == "unknown" or py.get("kind") == "unknown":
        return []

    problems: list[str] = []

    if py.get("nullable") and not ts.get("nullable"):
        problems.append(
            f"{path}: Python side is nullable (`X | None`) but the TS type at this "
            "path does not include `| null` -- a real `null` value would not "
            "type-check (an optional `?:` alone does not cover this)."
        )

    if ts.get("opaque") or py.get("opaque"):
        return problems

    ts_kind = ts.get("kind")
    py_kind = py.get("kind")
    if ts_kind != py_kind:
        problems.append(f"{path}: kind mismatch, TS={ts_kind!r} vs Python={py_kind!r}.")
        return problems

    if ts_kind == "array":
        ts_element = ts.get("element") or {"kind": "unknown"}
        py_element = py.get("element") or {"kind": "unknown"}
        problems.extend(diff_shapes(ts_element, py_element, f"{path}[]"))

    if ts_kind == "object":
        ts_properties = ts.get("properties")
        py_properties = py.get("properties")
        if ts_properties is None or py_properties is None:
            return problems
        for field_name, ts_field_shape in ts_properties.items():
            if field_name not in py_properties:
                if ts_field_shape.get("optional"):
                    # `field?: T` already promises the key may be absent;
                    # a specific real response not having it is exactly
                    # that, not drift. See the module/function docstring
                    # for the two real cases (`Project`, `command_evidence`)
                    # that made this exemption necessary.
                    continue
                problems.append(
                    f"{path}.{field_name}: present in the TS type as a required field "
                    "(no `?`) but not found on the Python side -- would read as "
                    "`undefined` at runtime despite the type promising it always exists."
                )
                continue
            problems.extend(
                diff_shapes(ts_field_shape, py_properties[field_name], f"{path}.{field_name}")
            )

    return problems
