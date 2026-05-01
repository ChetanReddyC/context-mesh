"""Claude Code CLI-backed distiller. Falls back to HeuristicDistiller on any failure."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any, Final

from context_mesh._logging import get_logger
from context_mesh.distillation.heuristic import HeuristicDistiller
from context_mesh.distillation.protocol import Distiller, DistillerCandidate
from context_mesh.distillation.redaction import Finding, redact

if TYPE_CHECKING:
    from context_mesh.api.types import NodeKind

_RAW_PROMPT = """
You are a memory-distillation extractor for a software engineering team's shared memory store.
Your input is a redacted session transcript. Your output is STRICT JSON describing zero or more
"memory candidates" worth persisting.

A memory candidate is a self-contained, durable lesson: a decision made, a failed approach
worth remembering, an error pattern, a procedural recipe, or a generalized rule. Pure chatter,
greetings, repeated boilerplate, and successful-but-unremarkable steps are NOT candidates.

OUTPUT FORMAT (mandatory):
- Output ONE JSON object and nothing else. No prose. No markdown fences. No leading or trailing
  text. The first character of your output MUST be '{' and the last MUST be '}'.
- Top-level shape: {"candidates": [<candidate>, ...]}
- For a noise session with no durable lessons, output exactly: {"candidates": []}

CANDIDATE SCHEMA (each object in "candidates"):
{
  "body":              string  (required, non-empty; the full distilled text of the memory),
  "headline":          string  (required, non-empty, <= 120 characters; one-line title),
  "kind":              string  (required, one of: "episodic", "semantic", "procedural"),
  "tags":              array of strings (optional; lowercase, single-token domain tags),
  "decisions":         array of strings (optional; one decision per item, full sentence),
  "failed_approaches": array of strings (optional; one failed approach per item, full sentence),
  "error_signatures":  array of strings (optional; e.g. "HTTP 502", "KeyError", "Traceback"),
  "file_paths":        array of strings (optional; repo-relative or absolute paths mentioned),
  "importance":        number  (optional, in [0.0, 1.0]; default 0.5),
  "confidence":        number  (optional, in [0.0, 1.0]; default 0.7)
}

KIND DEFINITIONS:
- "episodic":   a one-time event tied to a specific session, date, or actor.
- "semantic":   a generalized rule, principle, or constraint that persists across sessions.
- "procedural": executable knowledge -- commands, scripts, ordered steps -- to accomplish a task.

EXTRACTION RULES:
- A single session may contain multiple distinct lessons. Emit one candidate per lesson.
- Do NOT merge unrelated lessons. Do NOT split one lesson into multiple candidates.
- The "body" should be a clean, self-contained restatement of the lesson -- not verbatim copy.
- Keep "body" tight: one short paragraph, ideally 1-4 sentences.
- "headline" must be human-readable at a glance.
- The transcript has already been scrubbed of secrets; tokens like "[REDACTED:STRIPE_KEY]"
  may appear. Treat them as opaque placeholders.
- Set "importance" higher (0.7-1.0) for: production incidents, security decisions, irreversible
  architectural choices. Lower (0.3-0.5) for: minor preferences, low-stakes notes.
- Set "confidence" to your honest reliability estimate.

Now extract candidates from the following session transcript. Output STRICT JSON only.

--- BEGIN SESSION ---
{session_text}
--- END SESSION ---
"""
DEFAULT_PROMPT: Final[str] = _RAW_PROMPT[1:]
DEFAULT_TIMEOUT_SECONDS: Final[float] = 60.0
DEFAULT_BINARY: Final[str] = "claude"
HEADLINE_MAX_LEN: Final[int] = 120

_VALID_KINDS: Final[frozenset[str]] = frozenset({"episodic", "semantic", "procedural"})

SubprocessRunner = Callable[..., "subprocess.CompletedProcess[str]"]
WhichFn = Callable[[str], str | None]


class ClaudeCliDistiller:
    def __init__(
        self,
        *,
        binary: str = DEFAULT_BINARY,
        prompt_template: str = DEFAULT_PROMPT,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        fallback: Distiller | None = None,
        subprocess_runner: SubprocessRunner | None = None,
        which: WhichFn = shutil.which,
        extra_args: Sequence[str] = (),
    ) -> None:
        self._binary = binary
        self._prompt_template = prompt_template
        self._timeout_seconds = timeout_seconds
        self._fallback: Distiller = fallback if fallback is not None else HeuristicDistiller()
        self._runner: SubprocessRunner = (
            subprocess_runner if subprocess_runner is not None else _default_runner
        )
        self._which = which
        self._extra_args = tuple(extra_args)
        self._log = get_logger(__name__)

    @property
    def name(self) -> str:
        return "claude-cli/v1"

    def distill(self, session_text: str) -> list[DistillerCandidate]:
        if not session_text or not session_text.strip():
            return []

        resolved = self._which(self._binary)
        if resolved is None:
            self._log.warning("claude_cli.fallback", reason="cli_not_on_path", binary=self._binary)
            return self._fallback.distill(session_text)

        redacted_text, findings = redact(session_text)
        findings_tuple = tuple(findings)
        full_prompt = self._prompt_template.replace("{session_text}", redacted_text)

        cmd = [resolved, "-p", "--output-format", "json", *self._extra_args]

        try:
            result = self._runner(
                cmd,
                input=full_prompt,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self._timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            self._log.warning(
                "claude_cli.fallback",
                reason="cli_timeout",
                timeout_seconds=self._timeout_seconds,
            )
            return self._fallback.distill(session_text)
        except FileNotFoundError as exc:
            self._log.warning("claude_cli.fallback", reason="cli_spawn_failed", error=str(exc))
            return self._fallback.distill(session_text)

        if result.returncode != 0:
            self._log.warning(
                "claude_cli.fallback",
                reason="cli_nonzero_exit",
                returncode=result.returncode,
                stderr_head=(result.stderr or "")[:500],
            )
            return self._fallback.distill(session_text)

        parsed = _parse_response(result.stdout or "")
        if parsed is None:
            self._log.warning(
                "claude_cli.fallback",
                reason="invalid_json",
                stdout_head=(result.stdout or "")[:500],
            )
            return self._fallback.distill(session_text)

        candidates: list[DistillerCandidate] = []
        for raw in parsed:
            built = _build_candidate(raw, findings_tuple, self._log)
            if built is not None:
                candidates.append(built)
        return candidates


def _default_runner(cmd: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    result: subprocess.CompletedProcess[str] = subprocess.run(cmd, **kwargs)
    return result


def _parse_response(stdout: str) -> list[dict[str, Any]] | None:
    text = stdout.strip()
    if not text:
        return None
    first = text.find("{")
    last = text.rfind("}")
    if first == -1 or last == -1 or first >= last:
        return None
    sliced = text[first : last + 1]
    try:
        payload: Any = json.loads(sliced)
    except json.JSONDecodeError:
        return None

    if isinstance(payload, dict) and isinstance(payload.get("result"), str):
        try:
            inner = json.loads(payload["result"])
            if isinstance(inner, dict):
                payload = inner
        except json.JSONDecodeError:
            pass

    if not isinstance(payload, dict):
        return None
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        return None
    return [item for item in candidates if isinstance(item, dict)]


def _build_candidate(
    raw: dict[str, Any],
    findings: tuple[Finding, ...],
    log: Any,
) -> DistillerCandidate | None:
    body = str(raw.get("body", "")).strip()
    if not body:
        log.info("claude_cli.candidate_dropped", reason="empty_body")
        return None

    headline = str(raw.get("headline", "")).strip()
    if not headline:
        log.info("claude_cli.candidate_dropped", reason="empty_headline")
        return None
    headline = _truncate_headline(headline)

    kind_raw = raw.get("kind")
    if not isinstance(kind_raw, str) or kind_raw not in _VALID_KINDS:
        log.info("claude_cli.candidate_dropped", reason="invalid_kind", kind=kind_raw)
        return None
    kind: NodeKind = kind_raw  # type: ignore[assignment]

    return DistillerCandidate(
        body=body,
        headline=headline,
        kind=kind,
        tags=_coerce_string_tuple(raw.get("tags")),
        decisions=_coerce_string_tuple(raw.get("decisions")),
        failed_approaches=_coerce_string_tuple(raw.get("failed_approaches")),
        error_signatures=_coerce_string_tuple(raw.get("error_signatures")),
        file_paths=_coerce_string_tuple(raw.get("file_paths")),
        importance=_clamp_float(raw.get("importance"), 0.5),
        confidence=_clamp_float(raw.get("confidence"), 0.7),
        redaction_findings=findings,
    )


def _coerce_string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    out: list[str] = []
    for item in value:
        if isinstance(item, (str, int, float)) and not isinstance(item, bool):
            s = str(item).strip()
            if s:
                out.append(s)
    return tuple(out)


def _clamp_float(value: Any, default: float) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        f = float(value)
    else:
        return default
    return max(0.0, min(1.0, f))


def _truncate_headline(text: str) -> str:
    if len(text) <= HEADLINE_MAX_LEN:
        return text
    cut = text[: HEADLINE_MAX_LEN - 3]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut + "..."


__all__ = ["DEFAULT_PROMPT", "ClaudeCliDistiller"]
