"""Pure-stdlib rule-based distiller that turns redacted session text into structured candidates.

The pipeline is deterministic by construction: it never reads the wall clock, never
generates UUIDs, never samples randomness, and never inspects ``os.environ``. Identical
input always produces identical output, which keeps tests stable and audit logs
reproducible. The tag vocabulary is intentionally finite and curated -- we would rather
miss a long-tail tag than emit noisy stems, so new tags require an explicit edit to
``_TAG_VOCAB`` rather than open-vocabulary inference.
"""

from __future__ import annotations

import re
from typing import Final

from context_mesh.distillation.classifier import classify
from context_mesh.distillation.protocol import DistillerCandidate
from context_mesh.distillation.redaction import redact

MAX_BLOCKS: Final[int] = 64
KEEP_THRESHOLD: Final[float] = 1.5
HEADLINE_MAX_LEN: Final[int] = 120
MIN_BLOCK_CHARS: Final[int] = 8

_TAG_VOCAB: Final[frozenset[str]] = frozenset(
    {
        "auth",
        "deploy",
        "payments",
        "webhook",
        "api",
        "frontend",
        "backend",
        "infra",
        "build",
        "test",
        "ci",
        "cd",
        "database",
        "migration",
        "cache",
        "redis",
        "stripe",
        "docker",
        "kubernetes",
        "k8s",
        "aws",
        "gcp",
        "azure",
        "terraform",
        "security",
        "performance",
        "bug",
        "refactor",
    }
)


_BLANK_LINE_SPLIT_RE: Final[re.Pattern[str]] = re.compile(r"\n\s*\n+")
_MD_HEADER_RE: Final[re.Pattern[str]] = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)
_MD_HEADER_LINE_RE: Final[re.Pattern[str]] = re.compile(r"^#{1,6}\s", re.MULTILINE)
_LABEL_HEADER_RE: Final[re.Pattern[str]] = re.compile(
    r"^([A-Z][A-Za-z ]{1,40}):\s*(.*)$", re.MULTILINE
)
_FIRST_SENTENCE_RE: Final[re.Pattern[str]] = re.compile(r"(?<=[.!?])\s+")
_DECISION_VERB_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:decided|chose|picked|will use|going forward|switched to|adopted|standardized on)\b",
    re.IGNORECASE,
)
_FAILED_APPROACH_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:tried.*?but|didn'?t work|failed because|reverted|rolled back"
    r"|attempted .* but|first attempt)\b",
    re.IGNORECASE,
)
_HTTP_ERROR_CODE_RE: Final[re.Pattern[str]] = re.compile(
    r"\bHTTP\s+(\d{3})\b"
    r"|\b(?:status|code|response)[\s:]+(\d{3})\b"
    r"|\b(\d{3})\b(?=\s+(?:status|code|response|error))",
    re.IGNORECASE,
)
_EXCEPTION_NAME_RE: Final[re.Pattern[str]] = re.compile(r"\b([A-Z][a-zA-Z]*(?:Error|Exception))\b")
_TRACEBACK_RE: Final[re.Pattern[str]] = re.compile(
    r"^Traceback \(most recent call last\)", re.MULTILINE
)
_FILE_PATH_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:^|[\s\(])"
    r"((?:/[\w./\-]+|[A-Za-z]:[\\/][\w\\/.\-]+|[\w][\w./\-]*\.[a-zA-Z]{1,5}))\b"
)
_CODE_FENCE_RE: Final[re.Pattern[str]] = re.compile(r"^```", re.MULTILINE)
_MODAL_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:always|never|must|should|shall|don'?t|do not|avoid"
    r"|require[sd]?|prefer|forbidden|prohibited|mandatory)\b",
    re.IGNORECASE,
)
_COLON_DEFINITION_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Z][\w\s]+:\s+\S+", re.MULTILINE)
_REDACTED_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"\[REDACTED:[A-Z_]+\]")
_REDACTED_ONLY_BLOCK_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:\s*\[REDACTED:[A-Z_]+\]\s*)+$", re.MULTILINE
)
_WORD_RE: Final[re.Pattern[str]] = re.compile(r"\b\w+\b")
_SENTENCE_SPLIT_RE: Final[re.Pattern[str]] = re.compile(r"(?<=[.!?])\s+|\n+")
_DECISION_PREFIX_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:Decision|Resolution):", re.IGNORECASE
)
_TRAILING_PARTIAL_WORD_RE: Final[re.Pattern[str]] = re.compile(r"\s+\S*$")


def _split_into_blocks(text: str) -> list[str]:
    forced = _LABEL_HEADER_RE.sub(lambda m: f"\n{m.group(0)}", text)
    forced = _MD_HEADER_LINE_RE.sub(lambda m: f"\n{m.group(0)}", forced)
    parts = _BLANK_LINE_SPLIT_RE.split(forced)
    return [p for p in parts if p]


def _is_redacted_only(text: str) -> bool:
    stripped = _REDACTED_TOKEN_RE.sub("", text).strip()
    return stripped == ""


def _score_block(text: str) -> float:
    score = 0.0

    decision_hits = len(_DECISION_VERB_RE.findall(text))
    score += min(decision_hits * 2.0, 4.0)

    failed_hits = len(_FAILED_APPROACH_RE.findall(text))
    score += min(failed_hits * 2.0, 4.0)

    has_error = bool(
        _HTTP_ERROR_CODE_RE.search(text)
        or _EXCEPTION_NAME_RE.search(text)
        or _TRACEBACK_RE.search(text)
    )
    if has_error:
        score += 2.5

    file_hits = len(_FILE_PATH_RE.findall(text))
    score += min(file_hits * 1.0, 4.0)

    if _CODE_FENCE_RE.search(text):
        score += 1.5

    modal_hits = len(_MODAL_RE.findall(text))
    score += min(modal_hits * 1.5, 3.0)

    length = len(text)
    if length >= 80:
        score += 0.5
    if length >= 240:
        score += 0.5

    if _COLON_DEFINITION_RE.search(text):
        score += 0.5

    return score


def _dedupe_preserving_order(items: list[str], cap: int) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
            if len(out) >= cap:
                break
    return tuple(out)


def _extract_decisions(text: str) -> tuple[str, ...]:
    sentences = _SENTENCE_SPLIT_RE.split(text)
    matches: list[str] = []
    for sentence in sentences:
        s = sentence.strip()
        if not s:
            continue
        if _DECISION_VERB_RE.search(s) or _DECISION_PREFIX_RE.match(s):
            matches.append(s.rstrip(".!? \t"))
    return _dedupe_preserving_order(matches, 8)


def _extract_failed_approaches(text: str) -> tuple[str, ...]:
    sentences = _SENTENCE_SPLIT_RE.split(text)
    matches: list[str] = []
    for sentence in sentences:
        s = sentence.strip()
        if not s:
            continue
        if _FAILED_APPROACH_RE.search(s):
            matches.append(s.rstrip(".!? \t"))
    return _dedupe_preserving_order(matches, 8)


def _extract_error_signatures(text: str) -> tuple[str, ...]:
    matches: list[str] = []
    for m in _HTTP_ERROR_CODE_RE.finditer(text):
        for group in m.groups():
            if group is not None:
                matches.append(f"HTTP {group}")
                break
    for m in _EXCEPTION_NAME_RE.finditer(text):
        matches.append(m.group(1))
    if _TRACEBACK_RE.search(text):
        matches.append("Traceback")
    return _dedupe_preserving_order(matches, 8)


def _extract_file_paths(text: str) -> tuple[str, ...]:
    matches: list[str] = []
    for m in _FILE_PATH_RE.finditer(text):
        path = m.group(1)
        if "[REDACTED:" in path:
            continue
        if path in (".", ".."):
            continue
        if "/" not in path and "\\" not in path and "." not in path:
            continue
        matches.append(path)
    return _dedupe_preserving_order(matches, 16)


def _extract_tags(text: str) -> tuple[str, ...]:
    lowered = text.lower()
    matches: list[str] = []
    for m in _WORD_RE.finditer(lowered):
        word = m.group(0)
        if word in _TAG_VOCAB:
            matches.append(word)
    return _dedupe_preserving_order(matches, 8)


def _truncate_headline(headline: str) -> str:
    if len(headline) <= HEADLINE_MAX_LEN:
        return headline
    cut = headline[: HEADLINE_MAX_LEN - 3]
    trimmed = _TRAILING_PARTIAL_WORD_RE.sub("", cut)
    if not trimmed:
        trimmed = cut
    return f"{trimmed}..."


def _generate_headline(text: str) -> str:
    md_match = _MD_HEADER_RE.search(text)
    if md_match:
        candidate = md_match.group(1).strip()
        if candidate:
            return _truncate_headline(candidate)

    label_match = _LABEL_HEADER_RE.search(text)
    if label_match:
        label = label_match.group(1).strip()
        tail = label_match.group(2).strip()
        candidate = f"{label}: {tail}".strip() if tail else label
        if candidate:
            return _truncate_headline(candidate)

    first_split = _FIRST_SENTENCE_RE.split(text, maxsplit=1)
    if first_split:
        candidate = first_split[0].strip().rstrip(".!?")
        if candidate:
            return _truncate_headline(candidate)

    first_line = text.split("\n", 1)[0].strip()
    if first_line:
        return _truncate_headline(first_line)

    return "Untitled memory"


def _clamp(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def _compute_importance(
    text: str,
    error_signatures: tuple[str, ...],
    decisions: tuple[str, ...],
    failed_approaches: tuple[str, ...],
    tags: tuple[str, ...],
) -> float:
    score = 0.5
    if decisions:
        score += 0.15
    if error_signatures:
        score += 0.1
    if failed_approaches:
        score += 0.1
    if _MODAL_RE.search(text):
        score += 0.05
    if "security" in tags:
        score += 0.1
    if "deploy" in tags or "payments" in tags:
        score += 0.05
    if len(text) >= 240:
        score += 0.05
    if len(text) < 40:
        score -= 0.1
    return _clamp(score)


def _compute_confidence(text: str, used_default: bool, has_structured_field: bool) -> float:
    score = 0.7
    if used_default:
        score -= 0.2
    if has_structured_field:
        score += 0.1
    if _CODE_FENCE_RE.search(text):
        score += 0.05
    if len(text) < 40:
        score -= 0.1
    if len(text) >= 240:
        score += 0.05
    return _clamp(score)


class HeuristicDistiller:
    """Pure-stdlib rule-based distiller. Deterministic, network-free, LLM-free."""

    def __init__(self, *, min_block_score: float = KEEP_THRESHOLD) -> None:
        self._min_block_score = min_block_score

    @property
    def name(self) -> str:
        return "heuristic/v1"

    def distill(self, session_text: str) -> list[DistillerCandidate]:
        if not session_text or not session_text.strip():
            return []

        normalized = session_text.replace("\r\n", "\n")
        normalized = "\n".join(line.rstrip() for line in normalized.split("\n"))

        redacted_text, findings = redact(normalized)
        findings_tuple = tuple(findings)

        blocks = _split_into_blocks(redacted_text)
        kept: list[DistillerCandidate] = []

        for block in blocks[:MAX_BLOCKS]:
            block_stripped = block.strip()
            if len(block_stripped) < MIN_BLOCK_CHARS:
                continue
            if _REDACTED_ONLY_BLOCK_RE.match(block_stripped):
                continue
            if not block_stripped or _is_redacted_only(block_stripped):
                continue

            score = _score_block(block_stripped)
            if score < self._min_block_score:
                continue

            decisions = _extract_decisions(block_stripped)
            failed_approaches = _extract_failed_approaches(block_stripped)
            error_signatures = _extract_error_signatures(block_stripped)
            file_paths = _extract_file_paths(block_stripped)
            tags = _extract_tags(block_stripped)
            headline = _generate_headline(block_stripped)

            classification = classify(body=block_stripped, headline=headline)

            importance = _compute_importance(
                block_stripped, error_signatures, decisions, failed_approaches, tags
            )
            confidence = _compute_confidence(
                block_stripped,
                classification.used_default,
                bool(decisions or error_signatures or failed_approaches),
            )

            kept.append(
                DistillerCandidate(
                    body=block_stripped,
                    headline=headline,
                    kind=classification.kind,
                    tags=tags,
                    decisions=decisions,
                    failed_approaches=failed_approaches,
                    error_signatures=error_signatures,
                    file_paths=file_paths,
                    importance=importance,
                    confidence=confidence,
                    redaction_findings=findings_tuple,
                )
            )

        return kept


__all__ = ["HeuristicDistiller"]
