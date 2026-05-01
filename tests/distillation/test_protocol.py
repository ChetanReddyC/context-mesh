"""Tests for the `Distiller` Protocol and the `DistillerCandidate` dataclass contract."""

from __future__ import annotations

import dataclasses

import pytest

from context_mesh.distillation import Distiller, DistillerCandidate, HeuristicDistiller

# --- A. Runtime Protocol checks -----------------------------------------------------------


def test_distiller_protocol_runtime_check_accepts_heuristic() -> None:
    assert isinstance(HeuristicDistiller(), Distiller) is True


def test_distiller_protocol_runtime_check_rejects_empty_object() -> None:
    assert isinstance(object(), Distiller) is False


def test_distiller_protocol_runtime_check_rejects_partial_class() -> None:
    class _PartialDistiller:
        @property
        def name(self) -> str:
            return "partial/v0"

    assert isinstance(_PartialDistiller(), Distiller) is False


# --- B. Dataclass shape: frozen + slots ---------------------------------------------------


def _sample_candidate() -> DistillerCandidate:
    return DistillerCandidate(body="b", headline="h", kind="semantic")


def test_distiller_candidate_is_frozen() -> None:
    c = _sample_candidate()
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.body = "other"  # type: ignore[misc]


def test_distiller_candidate_uses_slots() -> None:
    c = _sample_candidate()
    assert not hasattr(c, "__dict__")


# --- C. Defaults --------------------------------------------------------------------------


def test_distiller_candidate_defaults() -> None:
    c = DistillerCandidate(body="b", headline="h", kind="semantic")
    assert c.tags == ()
    assert c.decisions == ()
    assert c.failed_approaches == ()
    assert c.error_signatures == ()
    assert c.file_paths == ()
    assert c.importance == 0.5
    assert c.confidence == 0.7
    assert c.redaction_findings == ()


# --- D. Locked field set ------------------------------------------------------------------

_EXPECTED_FIELDS: tuple[str, ...] = (
    "body",
    "headline",
    "kind",
    "tags",
    "decisions",
    "failed_approaches",
    "error_signatures",
    "file_paths",
    "importance",
    "confidence",
    "redaction_findings",
)


def test_distiller_candidate_field_shape() -> None:
    actual = tuple(f.name for f in dataclasses.fields(DistillerCandidate))
    assert actual == _EXPECTED_FIELDS


# --- E. Plural fields are tuples ----------------------------------------------------------


@pytest.mark.parametrize(
    "field_name",
    [
        "tags",
        "decisions",
        "failed_approaches",
        "error_signatures",
        "file_paths",
        "redaction_findings",
    ],
)
def test_distiller_candidate_plural_fields_are_tuples(field_name: str) -> None:
    c = _sample_candidate()
    assert isinstance(getattr(c, field_name), tuple)
