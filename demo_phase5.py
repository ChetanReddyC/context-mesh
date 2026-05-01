"""Phase 5 demo — run with: .venv\\Scripts\\python.exe demo_phase5.py"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from context_mesh.adapters import AgentMemoryAdapter, EntireAdapter
from context_mesh.api import Mesh
from context_mesh.distillation import HeuristicDistiller
from context_mesh.embeddings import DeterministicEmbeddingProvider

_BRANCH = "entire/checkpoints/v1"


def _run_git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        env=env,
    )
    return result.stdout.decode("utf-8").strip()


def _empty_tree_sha(repo: Path) -> str:
    """Return the SHA of an empty tree by running `git write-tree` against a
    fresh, empty index. Avoids touching the repo's real index."""
    import os

    env = os.environ.copy()
    env["GIT_INDEX_FILE"] = str(repo / ".git" / "empty-index")
    return _run_git(repo, "write-tree", env=env)


def _add_checkpoint_commit(repo: Path, branch: str, payload: dict[str, object]) -> str:
    tree_sha = _empty_tree_sha(repo)
    parent_lookup = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", f"refs/heads/{branch}"],
        check=False,
        capture_output=True,
    )
    parents: list[str] = []
    if parent_lookup.returncode == 0:
        parents.append(parent_lookup.stdout.decode("utf-8").strip())

    cmd = ["git", "-C", str(repo), "commit-tree", tree_sha]
    for parent in parents:
        cmd.extend(["-p", parent])
    cmd.extend(["-m", json.dumps(payload)])
    sha = subprocess.run(cmd, check=True, capture_output=True).stdout.decode("utf-8").strip()
    _run_git(repo, "update-ref", f"refs/heads/{branch}", sha)
    return sha


def _write_agent_memory_file(path: Path, frontmatter: dict[str, str], body: str) -> None:
    lines = ["---"]
    for key, value in frontmatter.items():
        lines.append(f"{key}: {value}")
    lines.append("---")
    lines.append("")
    lines.append(body)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _print_result(label: str, result: object) -> None:
    fields = (
        "discovered",
        "fetched",
        "memory_nodes_added",
        "cursor",
        "state_keys_count",
        "elapsed_ms",
    )
    print(f"  {label}:")
    for f in fields:
        value = getattr(result, f)
        if f == "cursor" and isinstance(value, str) and len(value) > 16:
            value = value[:16] + "..."
        print(f"    {f:<20} = {value}")


def main() -> None:
    print("=" * 60)
    print("Phase 5 demo — source adapters, sync orchestrator, sync CLI")
    print("=" * 60)

    # ------------------------------------------------------------------
    # STEP 0 — Preflight
    # ------------------------------------------------------------------
    if shutil.which("git") is None:
        print("\n[STEP 0] git not found on PATH; the Entire adapter requires git.")
        print("         Install git and re-run. Exiting cleanly.")
        return
    print("\n[STEP 0] git available; proceeding.")

    # ------------------------------------------------------------------
    # STEP 1 — Build a temp workspace + git repo
    # ------------------------------------------------------------------
    tmp_root = Path(tempfile.mkdtemp(prefix="cm-demo5-"))
    repo = tmp_root / "repo"
    db_path = tmp_root / "memory.db"
    repo.mkdir()

    _run_git(repo, "init", "-q", "-b", "main")
    _run_git(repo, "config", "user.email", "demo@context-mesh.local")
    _run_git(repo, "config", "user.name", "demo")
    _run_git(repo, "config", "commit.gpgsign", "false")
    (repo / "README").write_text("placeholder\n", encoding="utf-8")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-q", "-m", "init")
    print(f"\n[STEP 1] workspace: {tmp_root}")
    print(f"         repo:      {repo}")
    print(f"         db:        {db_path}")

    # ------------------------------------------------------------------
    # STEP 2 — Seed an agent-memory fixture
    # ------------------------------------------------------------------
    am_dir = repo / ".agent-memory"
    sample_md = am_dir / "sample.md"
    _write_agent_memory_file(
        sample_md,
        frontmatter={
            "session_id": "sess-am-1",
            "agent": "claude-code",
            "started_at": "1700000000",
            "ended_at": "1700000600",
            "turn_count": "9",
            "repo": "demo-payments",
            "tags": "stripe, webhooks, security",
        },
        body=(
            "## Session log\n\n"
            "Investigated a Stripe webhook timing bug. The handler trusted the\n"
            "first webhook timestamp as authoritative.\n\n"
            "Decision: Always re-verify Stripe webhook timestamps with current_time. "
            "We will not trust the first one.\n"
        ),
    )
    print(f"\n[STEP 2] seeded agent-memory file: {sample_md.name}")

    # ------------------------------------------------------------------
    # STEP 3 — Seed an entire-checkpoints fixture
    # ------------------------------------------------------------------
    payload = {
        "session_id": "sess-entire-1",
        "agent": "claude-code",
        "started_at": 1_700_001_000,
        "ended_at": 1_700_001_900,
        "repo": "demo-payments",
        "branch": "feat/locks",
        "commit_sha": "deadbee",
        "turn_count": 5,
        "token_usage": 4321,
        "file_changes": ["migrations/001.sql", "db.py"],
        "transcript": (
            "## Decision\n\n"
            "Always use advisory locks for online migrations. We will not skip "
            "lock acquisition under load."
        ),
    }
    sha = _add_checkpoint_commit(repo, _BRANCH, payload)
    print(f"\n[STEP 3] seeded entire checkpoint commit on {_BRANCH}: {sha[:12]}")

    # ------------------------------------------------------------------
    # STEP 4 — Open a Mesh + build a distiller and an embedder
    # ------------------------------------------------------------------
    mesh = Mesh.local(db_path)
    distiller = HeuristicDistiller()
    embedder = DeterministicEmbeddingProvider()
    print("\n[STEP 4] opened mesh; HeuristicDistiller + DeterministicEmbeddingProvider ready.")

    # ------------------------------------------------------------------
    # STEP 5 — First sync: agent-memory
    # ------------------------------------------------------------------
    am_adapter = AgentMemoryAdapter(mesh, repo)
    am_result_1 = mesh.sync(adapter=am_adapter, distiller=distiller, embedder=embedder)
    print("\n[STEP 5] first sync — agent-memory")
    _print_result("agent-memory", am_result_1)

    # ------------------------------------------------------------------
    # STEP 6 — First sync: entire
    # ------------------------------------------------------------------
    entire_adapter = EntireAdapter(mesh, repo)
    entire_result_1 = mesh.sync(adapter=entire_adapter, distiller=distiller, embedder=embedder)
    print("\n[STEP 6] first sync — entire")
    _print_result("entire", entire_result_1)

    # ------------------------------------------------------------------
    # STEP 7 — Idempotency proof: rerun both
    # ------------------------------------------------------------------
    am_result_2 = mesh.sync(
        adapter=AgentMemoryAdapter(mesh, repo),
        distiller=distiller,
        embedder=embedder,
    )
    entire_result_2 = mesh.sync(
        adapter=EntireAdapter(mesh, repo),
        distiller=distiller,
        embedder=embedder,
    )
    assert am_result_2.discovered == 0 and am_result_2.memory_nodes_added == 0, am_result_2
    assert entire_result_2.discovered == 0 and entire_result_2.memory_nodes_added == 0, (
        entire_result_2
    )
    print("\n[STEP 7] idempotency: ok")
    print(
        f"         agent-memory rerun: discovered={am_result_2.discovered}, "
        f"added={am_result_2.memory_nodes_added}"
    )
    print(
        f"         entire       rerun: discovered={entire_result_2.discovered}, "
        f"added={entire_result_2.memory_nodes_added}"
    )

    # ------------------------------------------------------------------
    # STEP 8 — Add a second agent-memory file so the CLI demo has something
    #          to discover.
    # ------------------------------------------------------------------
    mesh.close()
    sample2_md = am_dir / "sample2.md"
    _write_agent_memory_file(
        sample2_md,
        frontmatter={
            "session_id": "sess-am-2",
            "agent": "claude-code",
            "started_at": "1700100000",
            "ended_at": "1700100400",
            "turn_count": "4",
            "repo": "demo-payments",
            "tags": "deploy, runbook",
        },
        body=(
            "## Session log\n\n"
            "Drafted a release runbook for payments.\n\n"
            "Decision: Always run `make smoke-test` before promoting payments to prod. "
            "We will not skip the smoke step."
        ),
    )
    print(f"\n[STEP 8] seeded a second agent-memory file: {sample2_md.name}")

    # ------------------------------------------------------------------
    # STEP 9 — CLI surface: dry-run then real run
    # ------------------------------------------------------------------
    cmd_dry = [
        sys.executable,
        "-m",
        "context_mesh.cli",
        "sync",
        "agent-memory",
        "--repo",
        str(repo),
        "--db",
        str(db_path),
        "--dry-run",
        "--json",
    ]
    dry = subprocess.run(cmd_dry, check=True, capture_output=True)
    dry_payload = json.loads(dry.stdout.decode("utf-8"))
    print("\n[STEP 9a] CLI dry-run:")
    print(f"          discovered      = {dry_payload['discovered']}")
    print(f"          would_ingest[n] = {len(dry_payload.get('would_ingest', []))}")
    print(f"          dry_run flag    = {dry_payload['dry_run']}")

    cmd_real = [
        sys.executable,
        "-m",
        "context_mesh.cli",
        "sync",
        "agent-memory",
        "--repo",
        str(repo),
        "--db",
        str(db_path),
        "--json",
    ]
    real = subprocess.run(cmd_real, check=True, capture_output=True)
    real_payload = json.loads(real.stdout.decode("utf-8"))
    print("\n[STEP 9b] CLI real run:")
    print(f"          discovered         = {real_payload['discovered']}")
    print(f"          fetched            = {real_payload['fetched']}")
    print(f"          memory_nodes_added = {real_payload['memory_nodes_added']}")
    print(f"          state_keys_count   = {real_payload['state_keys_count']}")

    # ------------------------------------------------------------------
    # STEP 10 — Final stats
    # ------------------------------------------------------------------
    mesh = Mesh.local(db_path)
    try:
        conn = mesh.connection
        node_total = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        kind_rows = conn.execute(
            "SELECT kind, COUNT(*) FROM nodes GROUP BY kind ORDER BY kind"
        ).fetchall()
        sync_rows = conn.execute(
            "SELECT adapter_name, cursor FROM adapter_sync_state ORDER BY adapter_name"
        ).fetchall()
        sync_pull_count = conn.execute(
            "SELECT COUNT(*) FROM audit WHERE event_type = 'sync_pull'"
        ).fetchone()[0]
    finally:
        mesh.close()

    print("\n[STEP 10] final stats")
    print(f"          total nodes:  {node_total}")
    for kind, count in kind_rows:
        print(f"            {kind:<11} = {count}")
    print("          sync state cursors:")
    for adapter_name, cursor in sync_rows:
        shown = cursor[:16] + "..." if len(cursor) > 16 else cursor
        print(f"            {adapter_name:<14} = {shown}")
    print(f"          audit rows where event_type='sync_pull': {sync_pull_count}")

    print("\n" + "=" * 60)
    print("Phase 5 demo complete.")
    print("=" * 60)


if __name__ == "__main__":
    main()
