"""Git-branch-backed adapter for Entire.io agent-session checkpoints.

Each commit on the configured branch (default `entire/checkpoints/v1`) carries a
JSON payload as its commit message. The adapter is read-only over the git tree;
durable cursor + seen-SHA state lives in `adapter_sync_state`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from context_mesh._logging import get_logger
from context_mesh.adapters.protocol import (
    SessionReference,
    SessionTranscript,
    SyncState,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from context_mesh.api import Mesh


logger = get_logger(__name__)


_REQUIRED_KEYS: Final[frozenset[str]] = frozenset({"session_id", "agent", "started_at"})


class _MalformedCheckpointError(Exception):
    """Internal signalling type for checkpoint payload parse failures."""


def _coerce_epoch(value: str, *, field: str) -> int:
    raw = value.strip()
    if not raw:
        raise _MalformedCheckpointError(f"{field} is empty")
    try:
        return int(raw)
    except ValueError:
        pass
    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise _MalformedCheckpointError(
            f"{field} is neither integer epoch nor ISO-8601: {value!r}"
        ) from exc
    return int(parsed.timestamp())


class EntireAdapter:
    """SourceAdapter reading Entire.io checkpoints from a git branch.

    Checkpoints are stored as commits whose message body is a JSON object. The
    adapter walks the branch in `--reverse` (oldest first) order, tracking a
    cursor SHA + a set of already-ingested commit SHAs in adapter sync state.
    """

    def __init__(
        self,
        mesh: Mesh,
        repo_path: str | Path,
        *,
        branch: str = "entire/checkpoints/v1",
        name: str = "entire",
        git_binary: str = "git",
        timeout_seconds: float = 30.0,
    ) -> None:
        if not name.strip():
            raise ValueError("name must be a non-empty string")
        if not branch.strip():
            raise ValueError("branch must be a non-empty string")
        if not timeout_seconds > 0:
            raise ValueError("timeout_seconds must be > 0")
        resolved = Path(repo_path).resolve(strict=False)
        if not resolved.exists():
            raise ValueError(f"repo_path does not exist: {resolved}")
        if not resolved.is_dir():
            raise ValueError(f"repo_path is not a directory: {resolved}")
        which = shutil.which(git_binary)
        if which is None:
            raise RuntimeError(f"git binary not found on PATH: {git_binary}")
        self._git_binary: str = which

        toplevel = subprocess.run(
            [self._git_binary, "-C", str(resolved), "rev-parse", "--show-toplevel"],
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
        if toplevel.returncode != 0:
            raise ValueError(f"repo_path is not inside a git repository: {resolved}")
        canonical = Path(toplevel.stdout.decode("utf-8", errors="strict").strip()).resolve(
            strict=False
        )

        self._mesh: Mesh = mesh
        self._repo_path: Path = canonical
        self._name: str = name
        self._branch: str = branch
        self._timeout: float = timeout_seconds
        self._seen_shas: set[str] | None = None
        self._cursor_sha: str = ""
        self._missing_branch_warned: bool = False

    @property
    def name(self) -> str:
        return self._name

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(
            [self._git_binary, "-C", str(self._repo_path), *args],
            capture_output=True,
            timeout=self._timeout,
            check=check,
        )

    def discover(self) -> list[SessionReference]:
        if self._seen_shas is None:
            state = self.sync_state()
            if state is None:
                self._seen_shas = set()
                self._cursor_sha = ""
            else:
                raw = state.state.get("seen_shas", [])
                if not isinstance(raw, list):
                    raise RuntimeError(
                        f"adapter sync state for {self._name!r}: 'seen_shas' must be a list, "
                        f"got {type(raw).__name__}"
                    )
                self._seen_shas = set(raw)
                self._cursor_sha = state.cursor or ""

        verify = self._git("rev-parse", "--verify", f"refs/heads/{self._branch}", check=False)
        if verify.returncode != 0:
            if not self._missing_branch_warned:
                logger.warning(
                    "entire_branch_missing",
                    adapter=self._name,
                    branch=self._branch,
                    repo=str(self._repo_path),
                )
                self._missing_branch_warned = True
            return []
        self._missing_branch_warned = False

        cursor_reachable = False
        if self._cursor_sha:
            ancestor = self._git(
                "merge-base",
                "--is-ancestor",
                self._cursor_sha,
                self._branch,
                check=False,
            )
            cursor_reachable = ancestor.returncode == 0
            if not cursor_reachable:
                logger.warning(
                    "entire_cursor_unreachable",
                    adapter=self._name,
                    cursor=self._cursor_sha,
                    branch=self._branch,
                )

        if self._cursor_sha and cursor_reachable:
            log_args = (
                "log",
                "--reverse",
                "--format=%H",
                f"{self._cursor_sha}..{self._branch}",
            )
        else:
            log_args = ("log", "--reverse", "--format=%H", self._branch)
        log_result = self._git(*log_args, check=False)
        if log_result.returncode != 0:
            return []
        try:
            stdout_text = log_result.stdout.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            logger.warning(
                "entire_log_decode_failed",
                adapter=self._name,
                branch=self._branch,
                reason=str(exc),
            )
            return []

        shas = [line for line in stdout_text.split("\n") if line]

        results: list[SessionReference] = []
        for sha in shas:
            assert self._seen_shas is not None
            if sha in self._seen_shas:
                continue
            try:
                show = self._git("show", "-s", "--format=%B", sha, check=True)
            except subprocess.CalledProcessError as exc:
                logger.warning(
                    "entire_show_failed",
                    adapter=self._name,
                    sha=sha,
                    returncode=exc.returncode,
                )
                continue
            try:
                body = show.stdout.decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                logger.warning(
                    "non_utf8_commit_message",
                    adapter=self._name,
                    sha=sha,
                    reason=str(exc),
                )
                continue
            body = body.rstrip("\n")
            if not body or not body.strip():
                logger.warning(
                    "malformed_checkpoint",
                    adapter=self._name,
                    sha=sha,
                    reason="empty body",
                )
                continue
            try:
                payload = json.loads(body)
            except json.JSONDecodeError as exc:
                logger.warning(
                    "malformed_checkpoint",
                    adapter=self._name,
                    sha=sha,
                    reason=f"json: {exc.msg}",
                )
                continue
            if not isinstance(payload, dict):
                logger.warning(
                    "malformed_checkpoint",
                    adapter=self._name,
                    sha=sha,
                    reason="top-level is not an object",
                )
                continue
            try:
                ref = self._build_reference(sha, payload)
            except _MalformedCheckpointError as exc:
                logger.warning(
                    "malformed_checkpoint",
                    adapter=self._name,
                    sha=sha,
                    reason=str(exc),
                )
                continue
            results.append(ref)
        return results

    def fetch(self, reference: SessionReference) -> SessionTranscript:
        if reference.adapter_name != self._name:
            raise ValueError(
                f"reference.adapter_name {reference.adapter_name!r} does not match "
                f"adapter name {self._name!r}"
            )
        metadata = dict(reference.metadata)
        checkpoint_sha = metadata.get("checkpoint_sha")
        if not checkpoint_sha:
            raise ValueError("reference.metadata is missing 'checkpoint_sha'")

        show = self._git("show", "-s", "--format=%B", checkpoint_sha, check=True)
        body = show.stdout.decode("utf-8", errors="strict").rstrip("\n")
        payload = json.loads(body)

        transcript_text = payload.get("transcript")
        if not isinstance(transcript_text, str):
            raise ValueError(f"checkpoint {checkpoint_sha} has no string 'transcript' field")

        ended_at: int | None = None
        if "ended_at" in payload:
            ended_at = _coerce_epoch(str(payload["ended_at"]), field="ended_at")
        turn_count: int | None = None
        if "turn_count" in payload:
            turn_count = int(payload["turn_count"])
        token_usage: int | None = None
        if "token_usage" in payload:
            token_usage = int(payload["token_usage"])

        return SessionTranscript(
            reference=reference,
            text=transcript_text,
            ended_at=ended_at,
            turn_count=turn_count,
            token_usage=token_usage,
        )

    def sync_state(self) -> SyncState | None:
        return self._mesh.get_sync_state(self._name)

    def set_sync_state(self, state: SyncState) -> None:
        if state.adapter_name != self._name:
            raise ValueError(
                f"state.adapter_name {state.adapter_name!r} does not match "
                f"adapter name {self._name!r}"
            )
        raw = state.state.get("seen_shas", [])
        if not isinstance(raw, list):
            raise ValueError("state['seen_shas'] must be a list when present")
        self._mesh.set_sync_state(
            self._name,
            cursor=state.cursor,
            state={"seen_shas": sorted(raw)},
        )
        self._seen_shas = set(raw)
        self._cursor_sha = state.cursor or ""

    def seen_key(self, reference: SessionReference) -> str:
        metadata = dict(reference.metadata)
        sha = metadata.get("checkpoint_sha")
        if not sha:
            raise ValueError(
                f"reference.metadata is missing 'checkpoint_sha': {reference.source_id!r}"
            )
        return sha

    def merge_state(
        self,
        existing: SyncState | None,
        processed_refs: Sequence[SessionReference],
    ) -> tuple[str, dict[str, Any]]:
        existing_shas: list[str] = []
        if existing is not None:
            raw = existing.state.get("seen_shas", [])
            if not isinstance(raw, list):
                raise RuntimeError(
                    f"adapter sync state for {self._name!r}: 'seen_shas' must be a list"
                )
            existing_shas = list(raw)
        processed_shas = [self.seen_key(r) for r in processed_refs]
        if processed_shas:
            cursor = processed_shas[-1]
        else:
            cursor = existing.cursor if existing is not None else ""
        merged = sorted(set(existing_shas) | set(processed_shas))
        return cursor, {"seen_shas": merged}

    def _build_reference(
        self,
        checkpoint_sha: str,
        payload: dict[str, Any],
    ) -> SessionReference:
        missing = sorted(_REQUIRED_KEYS - payload.keys())
        if missing:
            raise _MalformedCheckpointError(f"missing required key(s): {missing}")

        session_id = payload["session_id"]
        if not isinstance(session_id, str):
            raise _MalformedCheckpointError("session_id must be a string")
        agent = payload["agent"]
        if not isinstance(agent, str):
            raise _MalformedCheckpointError("agent must be a string")
        started_raw = payload["started_at"]
        if not isinstance(started_raw, (str, int)):
            raise _MalformedCheckpointError("started_at must be a string or integer")

        started_at = _coerce_epoch(str(started_raw), field="started_at")

        repo_field = payload.get("repo")
        if "repo" in payload and not isinstance(repo_field, str):
            raise _MalformedCheckpointError("repo must be a string when present")
        repo = repo_field if isinstance(repo_field, str) else self._repo_path.name

        branch_field = payload.get("branch")
        if branch_field is not None and not isinstance(branch_field, str):
            raise _MalformedCheckpointError("branch must be a string or null")
        commit_sha_field = payload.get("commit_sha")
        if commit_sha_field is not None and not isinstance(commit_sha_field, str):
            raise _MalformedCheckpointError("commit_sha must be a string or null")

        pairs: list[tuple[str, str]] = [("checkpoint_sha", checkpoint_sha)]
        if "ended_at" in payload:
            pairs.append(
                ("ended_at", str(_coerce_epoch(str(payload["ended_at"]), field="ended_at")))
            )
        if "turn_count" in payload:
            try:
                pairs.append(("turn_count", str(int(payload["turn_count"]))))
            except (ValueError, TypeError) as exc:
                raise _MalformedCheckpointError(
                    f"turn_count is not a valid integer: {payload['turn_count']!r}"
                ) from exc
        if "token_usage" in payload:
            try:
                pairs.append(("token_usage", str(int(payload["token_usage"]))))
            except (ValueError, TypeError) as exc:
                raise _MalformedCheckpointError(
                    f"token_usage is not a valid integer: {payload['token_usage']!r}"
                ) from exc
        file_changes = payload.get("file_changes")
        if isinstance(file_changes, list):
            pairs.append(("file_change_count", str(len(file_changes))))

        metadata = tuple(sorted(pairs))

        return SessionReference(
            adapter_name=self._name,
            source_id=session_id,
            repo=repo,
            agent=agent,
            started_at=started_at,
            branch=branch_field,
            commit_sha=commit_sha_field,
            transcript_uri=None,
            metadata=metadata,
        )


__all__ = ["EntireAdapter"]
