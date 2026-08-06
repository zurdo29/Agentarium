"""Regressions for the comparison engine itself (`contract_types.py`), not
for the real backend<->TS contract -- that's `test_type_contract.py`. Uses
synthetic Pydantic models and hand-written shape dicts so these stay valid
regardless of what `app/page.tsx` or the domain models look like on any
given day.

The three `optional`-vs-`nullable` cases are the load-bearing ones: an
earlier draft of `diff_shapes` treated a bare TS `field?: T` as sufficient
for a Python `T | None` field, which is wrong (see `contract_types.py`'s
module docstring) and would have silently accepted a real type-safety gap.
"""

from __future__ import annotations

from contract_types import ShapeTree, diff_shapes, shape_from_model, shape_from_value
from pydantic import BaseModel


class _NullableField(BaseModel):
    field: str | None = None


def _object_shape(field_shape: ShapeTree) -> ShapeTree:
    return {"kind": "object", "nullable": False, "properties": {"field": field_shape}}


def test_ts_optional_without_null_does_not_satisfy_python_nullable() -> None:
    py_shape = shape_from_model(_NullableField)
    ts_shape = _object_shape({"optional": True, "kind": "string", "nullable": False})

    problems = diff_shapes(ts_shape, py_shape, "Root")

    assert problems, "a bare `field?: string` must not satisfy Python's `str | None`"
    assert "nullable" in problems[0]


def test_ts_nullable_without_optional_satisfies_python_nullable() -> None:
    py_shape = shape_from_model(_NullableField)
    ts_shape = _object_shape({"optional": False, "kind": "string", "nullable": True})

    assert diff_shapes(ts_shape, py_shape, "Root") == []


def test_ts_optional_and_nullable_satisfies_python_nullable() -> None:
    py_shape = shape_from_model(_NullableField)
    ts_shape = _object_shape({"optional": True, "kind": "string", "nullable": True})

    assert diff_shapes(ts_shape, py_shape, "Root") == []


class _PlainModel(BaseModel):
    id: str
    count: int


def test_field_only_python_has_is_not_drift() -> None:
    """The asymmetric direction: TS not consuming a real field is fine."""
    py_shape = shape_from_model(_PlainModel)
    ts_shape: ShapeTree = {
        "kind": "object",
        "nullable": False,
        "properties": {"id": {"optional": False, "kind": "string", "nullable": False}},
    }

    assert diff_shapes(ts_shape, py_shape, "Root") == []


def test_field_only_ts_has_is_drift() -> None:
    """The dangerous direction: TS reading a field with no backend source."""
    py_shape = shape_from_model(_PlainModel)
    ts_shape: ShapeTree = {
        "kind": "object",
        "nullable": False,
        "properties": {
            "id": {"optional": False, "kind": "string", "nullable": False},
            "count": {"optional": False, "kind": "number", "nullable": False},
            "ghost_field": {"optional": False, "kind": "string", "nullable": False},
        },
    }

    problems = diff_shapes(ts_shape, py_shape, "Root")

    assert len(problems) == 1
    assert "ghost_field" in problems[0]


def test_opaque_dict_is_never_compared_key_by_key() -> None:
    ts_shape: ShapeTree = {"kind": "object", "nullable": False, "opaque": True}
    py_shape = shape_from_value({"anything": 1, "goes": 2}, opaque_dict=True)

    assert diff_shapes(ts_shape, py_shape, "Root") == []


def test_shape_from_value_reflects_one_observed_run_not_a_schema() -> None:
    """Documents the epistemic limit called out in `shape_from_value`'s
    docstring: an empty list has no element to recurse into."""
    shape = shape_from_value([])

    assert shape == {
        "kind": "array",
        "nullable": False,
        "element": {"kind": "unknown", "nullable": True},
    }
