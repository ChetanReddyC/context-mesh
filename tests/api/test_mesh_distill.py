"""Tests for `Mesh.distill` — Phase 3 distillation pipeline integration."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest

from context_mesh.api import Mesh
from context_mesh.api.types import MemoryNode
from context_mesh.distillation import DistillerCandidate, HeuristicDistiller
from context_mesh.distillation.redaction import Finding

if TYPE_CHECKING:
    from collections.abc import Iterator

    from context_mesh.api.types import NodeKind


# ----- stub distiller --------------------------------------------------------


@dataclass(frozen=True)
class _StubDistiller:
    candidates: tuple[DistillerCandidate, ...] = ()
    name_value: str = "stub/v1"

    @property
    def name(self) -> str:
        return self.name_value

    def distill(self, session_text: str) -> list[DistillerCandidate]:
        return [] if not session_text.strip() else list(self.candidates)


def _candidate(
    *,
    body: str = "b",
    headline: str = "h",
    kind: NodeKind = "episodic",
    **kw: Any,
) -> DistillerCandidate:
    return DistillerCandidate(body=body, headline=headline, kind=kind, **kw)


# ----- fixtures --------------------------------------------------------------


@pytest.fixture()
def mesh() -> Iterator[Mesh]:
    m = Mesh.local(":memory:")
    m.connection.execute(
        "INSERT INTO scopes (id, name, level, created_at) VALUES (?, ?, ?, ?)",
        ("s", "scope-s", "private", 100),
    )
    m.connection.execute(
        "INSERT INTO sessions (id, repo, agent, started_at) VALUES (?, ?, ?, ?)",
        ("x", "r", "claude-code", 100),
    )
    m.connection.commit()
    try:
        yield m
    finally:
        m.close()


# ----- helpers ---------------------------------------------------------------


def _audit_count(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM audit").fetchone()[0])


def _audit_rows(conn: sqlite3.Connection) -> list[tuple[Any, ...]]:
    return list(
        conn.execute(
            "SELECT event_type, actor, node_ids, metadata FROM audit ORDER BY rowid"
        ).fetchall()
    )


def _distill_kwargs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "scope_id": "s",
        "source_session_id": "x",
        "source_repo": "r",
    }
    base.update(overrides)
    return base


# =============================================================================
# A. Happy-path persistence + field mapping
# =============================================================================


def test_distill_persists_one_candidate_as_node(mesh: Mesh) -> None:
    cand = _candidate(body="rule body", headline="rule headline", kind="semantic")
    distiller = _StubDistiller(candidates=(cand,))

    out = mesh.distill("session text", distiller=distiller, **_distill_kwargs())

    assert len(out) == 1
    nodes = mesh.list_nodes()
    assert len(nodes) == 1
    node = nodes[0]
    assert node.body == "rule body"
    assert node.headline == "rule headline"
    assert node.kind == "semantic"
    assert node.scope_id == "s"
    assert node.source_session_id == "x"
    assert node.source_repo == "r"


def test_distill_returns_persisted_nodes_in_order(mesh: Mesh) -> None:
    cands = (
        _candidate(body="b1", headline="h1", kind="semantic"),
        _candidate(body="b2", headline="h2", kind="episodic"),
        _candidate(body="b3", headline="h3", kind="procedural"),
    )
    distiller = _StubDistiller(candidates=cands)

    out = mesh.distill("text", distiller=distiller, **_distill_kwargs())

    assert [n.headline for n in out] == ["h1", "h2", "h3"]
    assert [n.body for n in out] == ["b1", "b2", "b3"]


def test_distill_maps_candidate_tags_to_node_tags(mesh: Mesh) -> None:
    cand = _candidate(tags=("auth", "api"))
    distiller = _StubDistiller(candidates=(cand,))

    out = mesh.distill("text", distiller=distiller, **_distill_kwargs())

    assert out[0].tags == ["auth", "api"]
    fetched = mesh.get(out[0].id)
    assert fetched is not None
    assert fetched.tags == ["auth", "api"]


def test_distill_maps_file_paths_to_file_dependencies(mesh: Mesh) -> None:
    cand = _candidate(file_paths=("a.py", "b.py"))
    distiller = _StubDistiller(candidates=(cand,))

    out = mesh.distill("text", distiller=distiller, **_distill_kwargs())

    assert out[0].file_dependencies == ["a.py", "b.py"]
    fetched = mesh.get(out[0].id)
    assert fetched is not None
    assert fetched.file_dependencies == ["a.py", "b.py"]


def test_distill_maps_decisions_failed_approaches_error_signatures(mesh: Mesh) -> None:
    cand = _candidate(
        decisions=("d1", "d2"),
        failed_approaches=("f1",),
        error_signatures=("E001", "E002"),
    )
    distiller = _StubDistiller(candidates=(cand,))

    out = mesh.distill("text", distiller=distiller, **_distill_kwargs())

    fetched = mesh.get(out[0].id)
    assert fetched is not None
    assert fetched.decisions == ["d1", "d2"]
    assert fetched.failed_approaches == ["f1"]
    assert fetched.error_signatures == ["E001", "E002"]


def test_distill_empty_tuple_fields_become_null(mesh: Mesh) -> None:
    cand = _candidate(tags=())
    distiller = _StubDistiller(candidates=(cand,))

    out = mesh.distill("text", distiller=distiller, **_distill_kwargs())

    raw = mesh.connection.execute("SELECT tags FROM nodes WHERE id = ?", (out[0].id,)).fetchone()
    assert raw[0] is None


def test_distill_uses_make_node_content_hash(mesh: Mesh) -> None:
    cand = _candidate(body="hash-body", decisions=("d1", "d2"))
    distiller = _StubDistiller(candidates=(cand,))

    out = mesh.distill("text", distiller=distiller, **_distill_kwargs())

    expected = MemoryNode.compute_content_hash("hash-body", None, ["d1", "d2"])
    assert out[0].content_hash == expected
    fetched = mesh.get(out[0].id)
    assert fetched is not None
    assert fetched.content_hash == expected


def test_distill_uses_make_node_content_hash_no_decisions(mesh: Mesh) -> None:
    cand = _candidate(body="plain")
    distiller = _StubDistiller(candidates=(cand,))

    out = mesh.distill("text", distiller=distiller, **_distill_kwargs())

    expected = MemoryNode.compute_content_hash("plain", None, None)
    assert out[0].content_hash == expected


def test_distill_carries_caller_scope_session_repo(mesh: Mesh) -> None:
    cand = _candidate()
    distiller = _StubDistiller(candidates=(cand,))

    out = mesh.distill("text", distiller=distiller, **_distill_kwargs())

    raw = mesh.connection.execute(
        "SELECT scope_id, source_session_id, source_repo FROM nodes WHERE id = ?",
        (out[0].id,),
    ).fetchone()
    assert raw == ("s", "x", "r")


def test_distill_does_not_attach_vectors(mesh: Mesh) -> None:
    cands = (
        _candidate(body="b1", kind="semantic"),
        _candidate(body="b2", kind="episodic"),
    )
    distiller = _StubDistiller(candidates=cands)

    out = mesh.distill("text", distiller=distiller, **_distill_kwargs())

    for node in out:
        assert mesh.has_vector(node.id) is False


def test_distill_uses_candidate_importance(mesh: Mesh) -> None:
    cand = _candidate(importance=0.85)
    distiller = _StubDistiller(candidates=(cand,))

    out = mesh.distill("text", distiller=distiller, **_distill_kwargs())

    assert out[0].importance == 0.85
    fetched = mesh.get(out[0].id)
    assert fetched is not None
    assert fetched.importance == 0.85


# =============================================================================
# B. Empty / no-op paths
# =============================================================================


def test_distill_empty_string_returns_empty_list(mesh: Mesh) -> None:
    distiller = _StubDistiller(candidates=(_candidate(),))

    before = len(mesh.list_nodes())
    out = mesh.distill("", distiller=distiller, **_distill_kwargs())
    after = len(mesh.list_nodes())

    assert out == []
    assert before == after


def test_distill_empty_string_does_not_audit(mesh: Mesh) -> None:
    distiller = _StubDistiller(candidates=(_candidate(),))

    before = _audit_count(mesh.connection)
    mesh.distill("", distiller=distiller, **_distill_kwargs())
    after = _audit_count(mesh.connection)

    assert before == after


def test_distill_whitespace_only_returns_empty_no_audit(mesh: Mesh) -> None:
    distiller = _StubDistiller(candidates=(_candidate(),))

    before_nodes = len(mesh.list_nodes())
    before_audit = _audit_count(mesh.connection)

    out = mesh.distill("   \n\t", distiller=distiller, **_distill_kwargs())

    assert out == []
    assert len(mesh.list_nodes()) == before_nodes
    assert _audit_count(mesh.connection) == before_audit


def test_distill_zero_candidates_returns_empty_no_audit(mesh: Mesh) -> None:
    distiller = _StubDistiller(candidates=())

    before_nodes = len(mesh.list_nodes())
    before_audit = _audit_count(mesh.connection)

    out = mesh.distill("real text", distiller=distiller, **_distill_kwargs())

    assert out == []
    assert len(mesh.list_nodes()) == before_nodes
    assert _audit_count(mesh.connection) == before_audit


# =============================================================================
# C. Argument validation
# =============================================================================


@pytest.mark.parametrize(
    "bad",
    ["", "   ", "\n\t"],
)
def test_distill_empty_scope_id_raises_value_error(mesh: Mesh, bad: str) -> None:
    distiller = _StubDistiller(candidates=(_candidate(),))
    with pytest.raises(ValueError, match="scope_id"):
        mesh.distill(
            "text",
            distiller=distiller,
            scope_id=bad,
            source_session_id="x",
            source_repo="r",
        )


@pytest.mark.parametrize(
    "bad",
    ["", "   ", "\n\t"],
)
def test_distill_empty_session_id_raises_value_error(mesh: Mesh, bad: str) -> None:
    distiller = _StubDistiller(candidates=(_candidate(),))
    with pytest.raises(ValueError, match="source_session_id"):
        mesh.distill(
            "text",
            distiller=distiller,
            scope_id="s",
            source_session_id=bad,
            source_repo="r",
        )


@pytest.mark.parametrize(
    "bad",
    ["", "   ", "\n\t"],
)
def test_distill_empty_repo_raises_value_error(mesh: Mesh, bad: str) -> None:
    distiller = _StubDistiller(candidates=(_candidate(),))
    with pytest.raises(ValueError, match="source_repo"):
        mesh.distill(
            "text",
            distiller=distiller,
            scope_id="s",
            source_session_id="x",
            source_repo=bad,
        )


@pytest.mark.parametrize(
    "bad",
    ["", "   ", "\n\t"],
)
def test_distill_empty_actor_raises_value_error(mesh: Mesh, bad: str) -> None:
    distiller = _StubDistiller(candidates=(_candidate(),))
    with pytest.raises(ValueError, match="actor"):
        mesh.distill(
            "text",
            distiller=distiller,
            scope_id="s",
            source_session_id="x",
            source_repo="r",
            actor=bad,
        )


# =============================================================================
# D. Edge inference
# =============================================================================


def test_distill_infers_generalizes_edge_semantic_to_episodic(mesh: Mesh) -> None:
    cands = (
        _candidate(body="rule", headline="rule", kind="semantic"),
        _candidate(body="event", headline="event", kind="episodic"),
    )
    distiller = _StubDistiller(candidates=cands)

    out = mesh.distill("text", distiller=distiller, **_distill_kwargs())

    edges = mesh.list_edges()
    assert len(edges) == 1
    edge = edges[0]
    assert edge.relation == "generalizes"
    assert edge.from_node_id == out[0].id
    assert edge.to_node_id == out[1].id
    assert edge.created_by == "auto"
    assert edge.confidence == 0.5


def test_distill_infers_applies_to_edge_procedural_to_episodic(mesh: Mesh) -> None:
    cands = (
        _candidate(body="proc", headline="proc", kind="procedural"),
        _candidate(body="event", headline="event", kind="episodic"),
    )
    distiller = _StubDistiller(candidates=cands)

    out = mesh.distill("text", distiller=distiller, **_distill_kwargs())

    edges = mesh.list_edges()
    assert len(edges) == 1
    edge = edges[0]
    assert edge.relation == "applies_to"
    assert edge.from_node_id == out[0].id
    assert edge.to_node_id == out[1].id
    assert edge.created_by == "auto"
    assert edge.confidence == 0.5


def test_distill_infers_no_edges_for_single_kind(mesh: Mesh) -> None:
    cands = tuple(_candidate(body=f"sem-{i}", headline=f"h{i}", kind="semantic") for i in range(3))
    distiller = _StubDistiller(candidates=cands)

    mesh.distill("text", distiller=distiller, **_distill_kwargs())

    assert mesh.list_edges() == []


def test_distill_infers_no_edges_when_no_episodic(mesh: Mesh) -> None:
    cands = (
        _candidate(body="sem", headline="sem", kind="semantic"),
        _candidate(body="proc", headline="proc", kind="procedural"),
    )
    distiller = _StubDistiller(candidates=cands)

    mesh.distill("text", distiller=distiller, **_distill_kwargs())

    assert mesh.list_edges() == []


def test_distill_infers_cross_product_for_multiple_each_kind(mesh: Mesh) -> None:
    cands = (
        _candidate(body="sem-0", headline="s0", kind="semantic"),
        _candidate(body="sem-1", headline="s1", kind="semantic"),
        _candidate(body="ep-0", headline="e0", kind="episodic"),
        _candidate(body="ep-1", headline="e1", kind="episodic"),
        _candidate(body="ep-2", headline="e2", kind="episodic"),
    )
    distiller = _StubDistiller(candidates=cands)

    out = mesh.distill("text", distiller=distiller, **_distill_kwargs())

    edges = mesh.list_edges()
    assert len(edges) == 6
    assert all(e.relation == "generalizes" for e in edges)

    semantic_ids = {out[0].id, out[1].id}
    episodic_ids = {out[2].id, out[3].id, out[4].id}
    pairs = {(e.from_node_id, e.to_node_id) for e in edges}
    expected_pairs = {(s, ep) for s in semantic_ids for ep in episodic_ids}
    assert pairs == expected_pairs


def test_distill_inferred_edges_use_created_by_auto(mesh: Mesh) -> None:
    cands = (
        _candidate(body="sem", headline="s", kind="semantic"),
        _candidate(body="ep", headline="e", kind="episodic"),
        _candidate(body="proc", headline="p", kind="procedural"),
    )
    distiller = _StubDistiller(candidates=cands)

    mesh.distill("text", distiller=distiller, **_distill_kwargs())

    for edge in mesh.list_edges():
        assert edge.created_by == "auto"


def test_distill_inferred_edges_have_confidence_0_5(mesh: Mesh) -> None:
    cands = (
        _candidate(body="sem", headline="s", kind="semantic"),
        _candidate(body="ep", headline="e", kind="episodic"),
        _candidate(body="proc", headline="p", kind="procedural"),
    )
    distiller = _StubDistiller(candidates=cands)

    mesh.distill("text", distiller=distiller, **_distill_kwargs())

    edges = mesh.list_edges()
    assert len(edges) >= 1
    for edge in edges:
        assert edge.confidence == 0.5


def test_distill_inferred_edge_metadata_contains_distiller_name_and_session_id(mesh: Mesh) -> None:
    cands = (
        _candidate(body="sem", headline="s", kind="semantic"),
        _candidate(body="ep", headline="e", kind="episodic"),
    )
    distiller = _StubDistiller(candidates=cands, name_value="stub/v1")

    mesh.distill("text", distiller=distiller, **_distill_kwargs())

    edges = mesh.list_edges()
    assert len(edges) == 1
    metadata = edges[0].metadata
    assert metadata is not None
    assert metadata["inferred_by"] == "distill"
    assert metadata["distiller"] == "stub/v1"
    assert metadata["session_id"] == "x"


def test_distill_does_not_infer_contradicts_edge(mesh: Mesh) -> None:
    cands = (
        _candidate(body="sem-0", headline="s0", kind="semantic"),
        _candidate(body="sem-1", headline="s1", kind="semantic"),
        _candidate(body="ep-0", headline="e0", kind="episodic"),
        _candidate(body="proc-0", headline="p0", kind="procedural"),
    )
    distiller = _StubDistiller(candidates=cands)

    mesh.distill("text", distiller=distiller, **_distill_kwargs())

    for edge in mesh.list_edges():
        assert edge.relation != "contradicts"


def test_distill_edge_count_capped_at_16(mesh: Mesh) -> None:
    cands = tuple(
        _candidate(body=f"sem-{i}", headline=f"s{i}", kind="semantic") for i in range(5)
    ) + tuple(_candidate(body=f"ep-{i}", headline=f"e{i}", kind="episodic") for i in range(5))
    distiller = _StubDistiller(candidates=cands)

    mesh.distill("text", distiller=distiller, **_distill_kwargs())

    edges = mesh.list_edges()
    assert len(edges) == 16
    assert all(e.relation == "generalizes" for e in edges)


# =============================================================================
# E. Audit
# =============================================================================


def test_distill_audits_each_node_as_add(mesh: Mesh) -> None:
    cands = (
        _candidate(body="b1", headline="h1", kind="semantic"),
        _candidate(body="b2", headline="h2", kind="episodic"),
        _candidate(body="b3", headline="h3", kind="procedural"),
    )
    distiller = _StubDistiller(candidates=cands)

    out = mesh.distill("text", distiller=distiller, **_distill_kwargs())

    rows = mesh.connection.execute(
        "SELECT actor, node_ids, metadata FROM audit "
        "WHERE event_type = 'add' "
        "AND json_extract(metadata, '$.action') IS NULL "
        "ORDER BY rowid"
    ).fetchall()
    assert len(rows) == 3
    persisted_ids = [n.id for n in out]
    audited_ids: list[str] = []
    for actor, node_ids_json, _metadata_json in rows:
        assert actor == "agent:distiller"
        node_ids = json.loads(node_ids_json)
        assert len(node_ids) == 1
        audited_ids.append(node_ids[0])
    assert audited_ids == persisted_ids


def test_distill_node_audit_metadata_includes_distiller_kind_headline(mesh: Mesh) -> None:
    cand = _candidate(body="b", headline="hello", kind="semantic", confidence=0.9)
    distiller = _StubDistiller(candidates=(cand,), name_value="stub/v1")

    mesh.distill("text", distiller=distiller, **_distill_kwargs())

    row = mesh.connection.execute(
        "SELECT metadata FROM audit "
        "WHERE event_type = 'add' "
        "AND json_extract(metadata, '$.action') IS NULL"
    ).fetchone()
    metadata = json.loads(row[0])
    expected_keys = {
        "kind",
        "scope_id",
        "distiller",
        "headline",
        "candidate_confidence",
        "redaction_finding_count",
        "redaction_kinds",
        "source_session_id",
    }
    assert expected_keys <= set(metadata.keys())
    assert metadata["kind"] == "semantic"
    assert metadata["scope_id"] == "s"
    assert metadata["distiller"] == "stub/v1"
    assert metadata["headline"] == "hello"
    assert metadata["candidate_confidence"] == 0.9
    assert metadata["redaction_finding_count"] == 0
    assert metadata["redaction_kinds"] == []
    assert metadata["source_session_id"] == "x"


def test_distill_node_audit_metadata_truncates_headline(mesh: Mesh) -> None:
    long_headline = "A" * 200
    cand = _candidate(body="b", headline=long_headline, kind="semantic")
    distiller = _StubDistiller(candidates=(cand,))

    mesh.distill("text", distiller=distiller, **_distill_kwargs())

    row = mesh.connection.execute(
        "SELECT metadata FROM audit "
        "WHERE event_type = 'add' "
        "AND json_extract(metadata, '$.action') IS NULL"
    ).fetchone()
    metadata = json.loads(row[0])
    assert len(metadata["headline"]) <= 60


def test_distill_node_audit_redaction_kinds_sorted_unique(mesh: Mesh) -> None:
    findings = (
        Finding("GITHUB_TOKEN", 0, 5, "[REDACTED:GITHUB_TOKEN]"),
        Finding("GITHUB_TOKEN", 6, 11, "[REDACTED:GITHUB_TOKEN]"),
        Finding("JWT", 12, 20, "[REDACTED:JWT]"),
    )
    cand = _candidate(body="b", headline="h", kind="semantic", redaction_findings=findings)
    distiller = _StubDistiller(candidates=(cand,))

    mesh.distill("text", distiller=distiller, **_distill_kwargs())

    row = mesh.connection.execute(
        "SELECT metadata FROM audit "
        "WHERE event_type = 'add' "
        "AND json_extract(metadata, '$.action') IS NULL"
    ).fetchone()
    metadata = json.loads(row[0])
    assert metadata["redaction_finding_count"] == 3
    assert metadata["redaction_kinds"] == ["GITHUB_TOKEN", "JWT"]


def test_distill_audits_each_inferred_edge(mesh: Mesh) -> None:
    cands = (
        _candidate(body="sem", headline="s", kind="semantic"),
        _candidate(body="ep", headline="e", kind="episodic"),
    )
    distiller = _StubDistiller(candidates=cands)

    out = mesh.distill("text", distiller=distiller, **_distill_kwargs())

    rows = mesh.connection.execute(
        "SELECT actor, node_ids, metadata FROM audit "
        "WHERE event_type = 'add' "
        "AND json_extract(metadata, '$.action') = 'infer_edge'"
    ).fetchall()
    assert len(rows) == 1
    actor, node_ids_json, metadata_json = rows[0]
    assert actor == "agent:distiller"
    metadata = json.loads(metadata_json)
    assert metadata["action"] == "infer_edge"
    assert metadata["relation"] == "generalizes"
    node_ids = json.loads(node_ids_json)
    assert node_ids == [out[0].id, out[1].id]


def test_distill_custom_actor_propagates_to_audit(mesh: Mesh) -> None:
    cands = (
        _candidate(body="sem", headline="s", kind="semantic"),
        _candidate(body="ep", headline="e", kind="episodic"),
    )
    distiller = _StubDistiller(candidates=cands)

    mesh.distill(
        "text",
        distiller=distiller,
        scope_id="s",
        source_session_id="x",
        source_repo="r",
        actor="agent:claude-code",
    )

    rows = mesh.connection.execute("SELECT actor FROM audit WHERE event_type = 'add'").fetchall()
    assert len(rows) == 3
    for (actor,) in rows:
        assert actor == "agent:claude-code"


# =============================================================================
# F. Transactional / failure semantics
# =============================================================================


def test_distill_partial_persistence_on_duplicate_content_hash(mesh: Mesh) -> None:
    pre_existing = mesh.make_node(
        kind="semantic",
        body="x",
        headline="pre",
        scope_id="s",
        source_session_id="x",
        source_repo="r",
    )
    mesh.add(pre_existing)

    expected_collision_hash = MemoryNode.compute_content_hash("x", None, None)
    assert pre_existing.content_hash == expected_collision_hash

    cands = (
        _candidate(body="a", headline="ha", kind="semantic"),
        _candidate(body="x", headline="hx", kind="semantic"),
        _candidate(body="c", headline="hc", kind="semantic"),
    )
    distiller = _StubDistiller(candidates=cands)

    with pytest.raises(sqlite3.IntegrityError):
        mesh.distill("text", distiller=distiller, **_distill_kwargs())

    bodies = {n.body for n in mesh.list_nodes()}
    assert "a" in bodies
    assert "c" not in bodies
    assert "x" in bodies


def test_distill_partial_persistence_on_invalid_scope_id(mesh: Mesh) -> None:
    cands = (
        _candidate(body="a", headline="ha", kind="semantic"),
        _candidate(body="b", headline="hb", kind="semantic"),
    )
    distiller = _StubDistiller(candidates=cands)

    before_count = len(mesh.list_nodes())

    with pytest.raises(sqlite3.IntegrityError):
        mesh.distill(
            "text",
            distiller=distiller,
            scope_id="nonexistent",
            source_session_id="x",
            source_repo="r",
        )

    assert len(mesh.list_nodes()) == before_count


def test_distill_no_audit_for_unpersisted_candidate(mesh: Mesh) -> None:
    pre_existing = mesh.make_node(
        kind="semantic",
        body="x",
        headline="pre",
        scope_id="s",
        source_session_id="x",
        source_repo="r",
    )
    mesh.add(pre_existing)

    audit_before = _audit_count(mesh.connection)

    cands = (
        _candidate(body="a", headline="ha", kind="semantic"),
        _candidate(body="x", headline="hx", kind="semantic"),
        _candidate(body="c", headline="hc", kind="semantic"),
    )
    distiller = _StubDistiller(candidates=cands)

    with pytest.raises(sqlite3.IntegrityError):
        mesh.distill("text", distiller=distiller, **_distill_kwargs())

    audit_after = _audit_count(mesh.connection)
    new_audits = audit_after - audit_before
    assert new_audits == 1

    rows = mesh.connection.execute(
        "SELECT metadata FROM audit "
        "WHERE event_type = 'add' "
        "AND json_extract(metadata, '$.action') IS NULL "
        "AND json_extract(metadata, '$.distiller') IS NOT NULL"
    ).fetchall()
    assert len(rows) == 1
    metadata = json.loads(rows[0][0])
    assert metadata["headline"] == "ha"


# =============================================================================
# G. Integration
# =============================================================================


def test_distill_with_heuristic_distiller_smoke(mesh: Mesh) -> None:
    text = (
        "Decision: we will use Postgres going forward for the payments deploy.\n"
        "\n"
        "On 2026-04-15, hit a KeyError: 'session_id' while debugging the payments webhook."
    )
    distiller = HeuristicDistiller()

    audit_before = _audit_count(mesh.connection)
    out = mesh.distill("hsmoke-text\n" + text, distiller=distiller, **_distill_kwargs())

    assert len(out) >= 1
    assert _audit_count(mesh.connection) > audit_before
    for node in out:
        fetched = mesh.get(node.id)
        assert fetched is not None
        assert fetched.scope_id == "s"
