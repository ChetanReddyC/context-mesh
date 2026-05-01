"""Filesystem-backed adapter for `.agent-memory/` markdown session transcripts."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
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


_FRONTMATTER_FENCE: Final[str] = "---"
_KV_RE: Final[re.Pattern[str]] = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):[ \t]*(.*)$")
_REQUIRED_KEYS: Final[frozenset[str]] = frozenset({"session_id", "agent", "started_at"})
_INT_KEYS: Final[frozenset[str]] = frozenset({"turn_count", "token_usage"})


class _MalformedFrontmatterError(Exception):
    """Internal signalling type for frontmatter parse failures."""


def _coerce_epoch(value: str, *, field: str) -> int:
    raw = value.strip()
    if not raw:
        raise _MalformedFrontmatterError(f"{field} is empty")
    try:
        return int(raw)
    except ValueError:
        pass
    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise _MalformedFrontmatterError(
            f"{field} is neither integer epoch nor ISO-8601: {value!r}"
        ) from exc
    return int(parsed.timestamp())


def _parse_tags(value: str) -> list[str]:
    return [tag for tag in (item.strip() for item in value.split(",")) if tag]


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split a UTF-8 text into (frontmatter dict, body string).

    Raises `_MalformedFrontmatterError` for any structural problem (missing or
    unterminated fence, malformed line). Required-key validation is performed
    here so callers receive a single, descriptive failure reason.
    """
    lines = text.split("\n")
    if not lines or lines[0].rstrip("\r") != _FRONTMATTER_FENCE:
        raise _MalformedFrontmatterError("file does not start with '---' fence")

    fields: dict[str, str] = {}
    end_index: int | None = None
    for i in range(1, len(lines)):
        stripped = lines[i].rstrip("\r")
        if stripped == _FRONTMATTER_FENCE:
            end_index = i
            break
        if not stripped.strip() or stripped.lstrip().startswith("#"):
            continue
        match = _KV_RE.match(stripped)
        if match is None:
            raise _MalformedFrontmatterError(f"invalid frontmatter line: {stripped!r}")
        key, raw_value = match.group(1), match.group(2)
        fields[key] = raw_value.strip()

    if end_index is None:
        raise _MalformedFrontmatterError("frontmatter is not terminated by '---'")

    missing = sorted(_REQUIRED_KEYS - fields.keys())
    if missing:
        raise _MalformedFrontmatterError(f"missing required keys: {missing}")

    body_lines = lines[end_index + 1 :]
    if body_lines and body_lines[0] == "":
        body_lines = body_lines[1:]
    body = "\n".join(body_lines)
    return fields, body


class AgentMemoryAdapter:
    """SourceAdapter for `.agent-memory/` directories committed alongside repos.

    Each `.md` file is treated as a discrete session transcript. The adapter is
    purely read-only over the filesystem; durable state (which content hashes
    have already been ingested) lives in `adapter_sync_state`.
    """

    def __init__(
        self,
        mesh: Mesh,
        repo_path: str | Path,
        *,
        name: str = "agent-memory",
    ) -> None:
        if not name.strip():
            raise ValueError("name must be a non-empty string")
        resolved = Path(repo_path).resolve(strict=False)
        if not resolved.exists():
            raise ValueError(f"repo_path does not exist: {resolved}")
        if not resolved.is_dir():
            raise ValueError(f"repo_path is not a directory: {resolved}")
        self._mesh = mesh
        self._repo_path = resolved
        self._name = name
        self._memory_dir = self._repo_path / ".agent-memory"
        self._seen_hashes: set[str] | None = None
        self._missing_dir_warned: bool = False

    @property
    def name(self) -> str:
        return self._name

    def discover(self) -> list[SessionReference]:
        if self._seen_hashes is None:
            state = self.sync_state()
            if state is None:
                self._seen_hashes = set()
            else:
                raw = state.state.get("seen_hashes", [])
                if not isinstance(raw, list):
                    raise RuntimeError(
                        f"adapter sync state for {self._name!r}: 'seen_hashes' must be a list, "
                        f"got {type(raw).__name__}"
                    )
                self._seen_hashes = set(raw)

        if not self._memory_dir.exists():
            if not self._missing_dir_warned:
                logger.warning(
                    "agent_memory_dir_missing",
                    adapter=self._name,
                    path=str(self._memory_dir),
                )
                self._missing_dir_warned = True
            return []
        if not self._memory_dir.is_dir():
            raise RuntimeError(f"{self._memory_dir} exists but is not a directory")

        seen_in_walk: set[str] = set()
        results: list[SessionReference] = []
        for path in sorted(self._memory_dir.rglob("*")):
            if path.suffix.lower() != ".md":
                continue
            if not path.is_file():
                continue
            if path.is_symlink():
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size == 0:
                logger.warning("empty_file", adapter=self._name, path=str(path))
                continue

            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            if digest in self._seen_hashes:
                continue
            if digest in seen_in_walk:
                logger.warning(
                    "duplicate_content",
                    adapter=self._name,
                    path=str(path),
                    content_sha256=digest,
                )
                continue

            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                logger.warning("non_utf8", adapter=self._name, path=str(path))
                continue

            try:
                ref = self._build_reference(path, text, digest)
            except _MalformedFrontmatterError as exc:
                logger.warning(
                    "malformed_frontmatter",
                    adapter=self._name,
                    path=str(path),
                    reason=str(exc),
                )
                continue

            seen_in_walk.add(digest)
            results.append(ref)

        results.sort(key=lambda r: (r.started_at, r.source_id))
        return results

    def fetch(self, reference: SessionReference) -> SessionTranscript:
        if reference.adapter_name != self._name:
            raise ValueError(
                f"reference.adapter_name {reference.adapter_name!r} does not match "
                f"adapter name {self._name!r}"
            )
        if reference.transcript_uri is None:
            raise ValueError("reference.transcript_uri must be set for AgentMemoryAdapter")
        path = Path(reference.transcript_uri)
        text = path.read_text(encoding="utf-8")
        fields, body = _split_frontmatter(text)
        ended_at = (
            _coerce_epoch(fields["ended_at"], field="ended_at") if "ended_at" in fields else None
        )
        turn_count = int(fields["turn_count"].strip()) if "turn_count" in fields else None
        token_usage = int(fields["token_usage"].strip()) if "token_usage" in fields else None
        return SessionTranscript(
            reference=reference,
            text=body,
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
        self._mesh.set_sync_state(self._name, cursor=state.cursor, state=dict(state.state))
        raw = state.state.get("seen_hashes", [])
        if not isinstance(raw, list):
            raise ValueError("state['seen_hashes'] must be a list when present")
        self._seen_hashes = set(raw)

    def seen_key(self, reference: SessionReference) -> str:
        metadata = dict(reference.metadata)
        h = metadata.get("content_sha256")
        if not h:
            raise ValueError(
                f"reference.metadata is missing 'content_sha256': {reference.source_id!r}"
            )
        return h

    def merge_state(
        self,
        existing: SyncState | None,
        processed_refs: Sequence[SessionReference],
    ) -> tuple[str, dict[str, Any]]:
        existing_hashes: list[str] = []
        if existing is not None:
            raw = existing.state.get("seen_hashes", [])
            if not isinstance(raw, list):
                raise RuntimeError(
                    f"adapter sync state for {self._name!r}: 'seen_hashes' must be a list"
                )
            existing_hashes = list(raw)
        merged = sorted(set(existing_hashes) | {self.seen_key(r) for r in processed_refs})
        cursor = datetime.now(UTC).isoformat()
        return cursor, {"seen_hashes": merged}

    def _build_reference(self, file_path: Path, text: str, digest: str) -> SessionReference:
        fields, _ = _split_frontmatter(text)

        for key in _INT_KEYS:
            if key in fields:
                try:
                    int(fields[key].strip())
                except ValueError as exc:
                    raise _MalformedFrontmatterError(
                        f"{key} is not a valid integer: {fields[key]!r}"
                    ) from exc

        started_at = _coerce_epoch(fields["started_at"], field="started_at")
        if "ended_at" in fields:
            _coerce_epoch(fields["ended_at"], field="ended_at")

        repo = fields.get("repo") or self._repo_path.name
        branch = fields.get("branch") or None
        commit_sha = fields.get("commit_sha") or None

        pairs: list[tuple[str, str]] = [("content_sha256", digest)]
        if "ended_at" in fields:
            pairs.append(("ended_at", str(_coerce_epoch(fields["ended_at"], field="ended_at"))))
        if "turn_count" in fields:
            pairs.append(("turn_count", str(int(fields["turn_count"].strip()))))
        if "token_usage" in fields:
            pairs.append(("token_usage", str(int(fields["token_usage"].strip()))))
        if "tags" in fields:
            for tag in sorted(_parse_tags(fields["tags"])):
                pairs.append(("tag", tag))
        metadata = tuple(sorted(pairs))

        return SessionReference(
            adapter_name=self._name,
            source_id=fields["session_id"],
            repo=repo,
            agent=fields["agent"],
            started_at=started_at,
            branch=branch,
            commit_sha=commit_sha,
            transcript_uri=str(file_path.resolve()),
            metadata=metadata,
        )


__all__ = ["AgentMemoryAdapter"]
