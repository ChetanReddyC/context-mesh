"""Heuristic memory-kind classifier for distilled session text.

The classifier maps a body (and optional headline) to one of the three memory
kinds defined in ``docs/MEMORY_TYPES.md``:

- ``episodic``   -- a one-time event tied to a specific session, time, or actor.
- ``semantic``   -- a generalized rule, principle, or constraint that persists.
- ``procedural`` -- executable knowledge: commands, scripts, step-by-step workflows.

What we deliberately are NOT doing:

- No LLM call. This is a stdlib regex pipeline; classification is offline and free.
- No language detection. English-only signals; non-English text falls through.
- No POS tagging or syntactic parsing.
- No statistical model, no learned weights, no training data.
- No body mutation. The text is read; the returned ``ClassificationResult`` is
  a parallel artefact and never rewrites the input.

Default policy: when no signal fires (zero scores across all kinds) we return
``semantic`` with ``used_default=True``. We diverge here from
``docs/MEMORY_TYPES.md`` "Decision Tree", which suggests defaulting to
``episodic`` on the rationale that episodics decay fast and self-clean. We
choose ``semantic`` instead because the downstream cost of a
mis-labelled episodic is a confidently-wrong factual claim attached to a
fabricated date or actor, while a mis-labelled semantic is an over-broad rule
the user can easily contradict or supersede. The conservative failure mode is
to surface a soft rule, not to invent history. The same default applies to
all-tied scores. Future contributors revisiting this trade-off should update
both this module and ``docs/MEMORY_TYPES.md`` together.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping

    from context_mesh.api.types import NodeKind

DEFAULT_KIND: Final[NodeKind] = "semantic"


@dataclass(frozen=True, slots=True)
class ClassificationResult:
    kind: NodeKind
    scores: Mapping[NodeKind, float]
    matched_signals: tuple[str, ...]
    used_default: bool


@dataclass(frozen=True, slots=True)
class _Signal:
    kind: NodeKind
    name: str
    pattern: re.Pattern[str]
    weight: float


_FLAGS: Final[int] = re.IGNORECASE | re.MULTILINE
_FLAGS_CASE_SENSITIVE: Final[int] = re.MULTILINE


_PROCEDURAL_SIGNALS: Final[tuple[_Signal, ...]] = (
    _Signal(
        "procedural",
        "code_fence_block",
        re.compile(r"```[\s\S]*?```", _FLAGS),
        3.0,
    ),
    _Signal(
        "procedural",
        "inline_code_command_shape",
        re.compile(
            r"`[^`\n]*\b(?:make|kubectl|npm|yarn|pnpm|pip|uv|docker|git|cargo|go|python"
            r"|bash|sh|terraform|ansible|helm|psql|redis-cli|curl|wget|ssh|scp|rsync"
            r"|systemctl|journalctl|aws|gcloud|az)\b[^`\n]*`",
            _FLAGS,
        ),
        1.5,
    ),
    _Signal(
        "procedural",
        "imperative_sentence_start",
        re.compile(
            r"^\s*(?:Run|Execute|Deploy|Install|Build|Start|Stop|Launch|Restart|Rollback"
            r"|Roll back|Apply|Compile|Configure|Set up|Setup|Bootstrap|Migrate|Seed"
            r"|Provision|Push|Pull|Clone|Checkout|Verify|Check|Open|Edit|Add|Update|Remove)\b",
            _FLAGS,
        ),
        1.5,
    ),
    _Signal(
        "procedural",
        "numbered_steps",
        re.compile(r"^\s*\d+[.)]\s+\S", _FLAGS),
        2.5,
    ),
    _Signal(
        "procedural",
        "bullet_list_with_command",
        re.compile(
            r"^\s*[-*•]\s+.*\b(?:make|kubectl|npm|yarn|pnpm|pip|uv|docker|git|cargo|go"
            r"|python|bash|sh|terraform|ansible|helm|psql|redis-cli|curl|wget|ssh|scp|rsync"
            r"|systemctl|journalctl|aws|gcloud|az)\b",
            _FLAGS,
        ),
        1.0,
    ),
    _Signal(
        "procedural",
        "to_do_X_template",
        re.compile(r"\b(?:to|how to|in order to)\s+\w+[^\n.]*[:\n]", _FLAGS),
        1.5,
    ),
    _Signal(
        "procedural",
        "path_or_flag_token",
        re.compile(r"\B--[a-z][\w-]+\b|\B\./[A-Za-z]", _FLAGS),
        0.5,
    ),
    _Signal(
        "procedural",
        "keyword_steps_header",
        re.compile(
            r"^\s*(?:Steps?|Procedure|Workflow|Setup|Deploy|Run|Build|Usage):",
            _FLAGS,
        ),
        2.0,
    ),
)


_EPISODIC_SIGNALS: Final[tuple[_Signal, ...]] = (
    _Signal(
        "episodic",
        "iso_date",
        re.compile(r"\b\d{4}-\d{2}-\d{2}\b", _FLAGS),
        2.5,
    ),
    _Signal(
        "episodic",
        "relative_time_phrase",
        re.compile(
            r"\b(?:yesterday|today|last\s+(?:week|month|year|monday|tuesday|wednesday"
            r"|thursday|friday|saturday|sunday|night|sprint|release|deploy)"
            r"|this\s+morning|this\s+afternoon|tonight|earlier|ago)\b",
            _FLAGS,
        ),
        2.0,
    ),
    _Signal(
        "episodic",
        "incident_or_pr_id",
        re.compile(
            r"\b(?:PR|MR|INC[-_]?|TICKET[-_]?|JIRA[-_]?|RUN[-_]?|DEPLOY[-_]?)"
            r"\s*[#-]?\s*\d{2,}\b|#\d{2,}",
            _FLAGS,
        ),
        1.5,
    ),
    _Signal(
        "episodic",
        "past_tense_verb",
        re.compile(
            r"\b(?:was|were|had|did|got|caused|crashed|broke|failed|happened|discovered"
            r"|tried|reverted|deployed|merged|rolled\s+back|hit|saw|noticed|learned|found"
            r"|root[\- ]caused|debugged|fixed)\b",
            _FLAGS,
        ),
        1.0,
    ),
    _Signal(
        "episodic",
        "we_actor_past",
        re.compile(
            r"\b(?:we|I|the team|the agent|@\w+)\s+"
            r"(?:tried|deployed|rolled\s+back|reverted|merged|debugged|fixed|hit|found"
            r"|saw|broke|reproduced)\b",
            _FLAGS,
        ),
        2.0,
    ),
    _Signal(
        "episodic",
        "error_with_code",
        re.compile(
            r"\b(?:HTTP\s+)?(?:4\d{2}|5\d{2})\b|\b(?:Error|Exception|Traceback|stacktrace|panic):",
            _FLAGS,
        ),
        1.5,
    ),
    # Case-sensitive: a leading capital letter on the actor token is the discriminating
    # signal (proper noun vs sentence-initial common verb).
    _Signal(
        "episodic",
        "named_individual_past",
        re.compile(
            r"\b[A-Z][a-z]{2,}\s+"
            r"(?:said|deployed|broke|reverted|merged|fixed|reported|noticed|reproduced)\b",
            _FLAGS_CASE_SENSITIVE,
        ),
        1.5,
    ),
    _Signal(
        "episodic",
        "version_or_commit_anchor",
        re.compile(
            r"\bv\d+\.\d+\.\d+\b|\bcommit\s+[0-9a-f]{7,40}\b|\bSHA\s*[:=]\s*[0-9a-f]{7,40}\b",
            _FLAGS,
        ),
        1.0,
    ),
)


_SEMANTIC_SIGNALS: Final[tuple[_Signal, ...]] = (
    _Signal(
        "semantic",
        "modal_imperative",
        re.compile(
            r"\b(?:always|never|must|should|shall|don['`]?t|do not|avoid"
            r"|require[sd]?|prefer|forbidden|prohibited|mandatory)\b",
            _FLAGS,
        ),
        2.0,
    ),
    _Signal(
        "semantic",
        "principle_template",
        re.compile(
            r"^\s*(?:Rule|Principle|Convention|Policy|Standard|Guideline|Note|Invariant"
            r"|Constraint):",
            _FLAGS,
        ),
        3.0,
    ),
    _Signal(
        "semantic",
        "generalized_subject",
        re.compile(
            r"\b(?:all|every|any|no)\s+"
            r"(?:auth|deploy|migration|PR|pull request|service|endpoint|request|response"
            r"|user|database|change[s]?|commit[s]?|branch[es]*|test[s]?)\b",
            _FLAGS,
        ),
        1.5,
    ),
    _Signal(
        "semantic",
        "present_tense_universal",
        re.compile(
            r"\b(?:is|are|requires?|needs?|applies\s+to|means|implies|forbids|allows|blocks)\b",
            _FLAGS,
        ),
        0.5,
    ),
    _Signal(
        "semantic",
        "colon_definition",
        re.compile(r"^[A-Z][\w\s]+:\s+\S+", _FLAGS_CASE_SENSITIVE),
        1.0,
    ),
    _Signal(
        "semantic",
        "applies_to_template",
        re.compile(
            r"\bapplies\s+(?:to|when|if)\b"
            r"|\bin\s+(?:the\s+)?(?:auth|payments|api|frontend|backend|infra|build|test|ci|deploy)"
            r"\s+layer\b",
            _FLAGS,
        ),
        1.5,
    ),
)


_PER_SIGNAL_CAP: Final[dict[str, int]] = {
    "code_fence_block": 2,
    "inline_code_command_shape": 3,
    "imperative_sentence_start": 5,
    "numbered_steps": 1,
    "bullet_list_with_command": 4,
    "to_do_X_template": 3,
    "path_or_flag_token": 4,
    "keyword_steps_header": 2,
    "iso_date": 3,
    "relative_time_phrase": 3,
    "incident_or_pr_id": 3,
    "past_tense_verb": 4,
    "we_actor_past": 3,
    "error_with_code": 3,
    "named_individual_past": 2,
    "version_or_commit_anchor": 3,
    "modal_imperative": 3,
    "principle_template": 1,
    "generalized_subject": 3,
    "present_tense_universal": 4,
    "colon_definition": 2,
    "applies_to_template": 3,
}


def _signal_contribution(signal: _Signal, text: str) -> float:
    if not text:
        return 0.0
    # numbered_steps fires once iff >=2 numbered-step lines exist; a single "1. foo"
    # line is far too noisy on its own (every ordered list item would otherwise count).
    if signal.name == "numbered_steps":
        hits = len(signal.pattern.findall(text))
        return signal.weight if hits >= 2 else 0.0
    cap = _PER_SIGNAL_CAP.get(signal.name, 5)
    hits = sum(1 for _ in signal.pattern.finditer(text))
    return min(hits, cap) * signal.weight


def _pick_winner(scores: Mapping[NodeKind, float]) -> tuple[NodeKind, bool]:
    procedural = scores["procedural"]
    episodic = scores["episodic"]
    semantic = scores["semantic"]

    if procedural == 0.0 and episodic == 0.0 and semantic == 0.0:
        return DEFAULT_KIND, True

    max_score = max(procedural, episodic, semantic)
    ordered: tuple[NodeKind, ...] = ("procedural", "episodic", "semantic")
    leaders = [k for k in ordered if scores[k] == max_score]

    if len(leaders) == 1:
        return leaders[0], False

    return leaders[0], False


def classify(body: str, headline: str | None = None) -> ClassificationResult:
    """Classify body+headline into a memory kind.

    Never raises; falls back to ``DEFAULT_KIND`` when no signal fires.
    """
    body_clean = body or ""
    headline_clean = (headline or "").strip()

    if not body_clean.strip() and not headline_clean:
        return ClassificationResult(
            kind=DEFAULT_KIND,
            scores={"episodic": 0.0, "semantic": 0.0, "procedural": 0.0},
            matched_signals=(),
            used_default=True,
        )

    body_text = body_clean
    headline_text = headline_clean
    matched_in_order: list[str] = []
    scores: dict[NodeKind, float] = {"episodic": 0.0, "semantic": 0.0, "procedural": 0.0}

    for table in (_PROCEDURAL_SIGNALS, _EPISODIC_SIGNALS, _SEMANTIC_SIGNALS):
        for signal in table:
            body_contribution = _signal_contribution(signal, body_text)
            # Headline matches are weighted 1.5x: a signal in the title carries
            # disproportionate intent (it's what the author chose to lead with).
            headline_contribution = (
                _signal_contribution(signal, headline_text) * 1.5 if headline_text else 0.0
            )
            total = body_contribution + headline_contribution
            if total > 0.0:
                scores[signal.kind] += total
                matched_in_order.append(signal.name)

    winner, used_default = _pick_winner(scores)

    return ClassificationResult(
        kind=winner,
        scores=scores,
        matched_signals=tuple(matched_in_order),
        used_default=used_default,
    )
