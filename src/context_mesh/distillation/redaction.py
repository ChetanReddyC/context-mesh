"""Strip credentials and high-entropy secrets from raw session text before persistence.

Detected categories (in source order, with later rules deferring to earlier matches):

1.  ``PRIVATE_KEY_BLOCK``         -- PEM-style ``-----BEGIN ... PRIVATE KEY-----`` blocks.
2.  ``URL_WITH_CREDENTIALS``      -- ``scheme://user:password@host`` URLs.
3.  ``JWT``                       -- three-part ``eyJ...``-prefixed JSON Web Tokens.
4.  ``AWS_ACCESS_KEY``            -- ``AKIA``/``ASIA``/etc. 20-char access key IDs.
5.  ``AWS_SECRET_KEY``            -- 40-char secrets following an ``aws_secret*`` label.
6.  ``GITHUB_TOKEN``              -- ``ghp_``/``gho_``/``github_pat_`` tokens.
7.  ``STRIPE_KEY``                -- ``sk_test_``/``pk_live_``/``rk_*`` keys.
8.  ``ANTHROPIC_KEY``             -- ``sk-ant-api##-``/``sk-ant-admin##-`` keys.
9.  ``OPENAI_KEY``                -- ``sk-`` and ``sk-proj-`` OpenAI keys.
10. ``HUGGINGFACE_TOKEN``         -- ``hf_`` Hugging Face tokens.
11. ``SLACK_TOKEN``               -- ``xoxb-``/``xoxp-``/etc. Slack tokens.
12. ``GOOGLE_API_KEY``            -- ``AIza`` Google API keys.
13. ``BEARER_TOKEN``              -- the secret following ``Authorization: Bearer ...``.
14. ``GENERIC_SECRET_ASSIGNMENT`` -- the value following ``password=``/``api_key:`` style labels.
15. ``HIGH_ENTROPY_TOKEN``        -- residual tokens with Shannon entropy >= 4.0.

What we do NOT redact and why:

- Email addresses -- load-bearing for source traceability and decision attribution.
- Phone numbers -- rare in engineering logs; high false-positive risk on version strings,
  port lists, and timestamps.
- IP addresses -- internal IPs in deploy notes are useful and routinely benign.
- AWS account IDs (12-digit numbers) -- too false-positive prone without surrounding context.
- File paths containing usernames -- load-bearing for utility (``/Users/alice/repo/...``).
- Raw hex hashes (MD5/SHA-1/SHA-256/SHA-512) -- content-addressable identifiers used across
  git, CI, and our own ``content_hash`` column.
- UUIDs -- used as ``MemoryNode``/session/scope IDs throughout the system.
- The literal word ``Bearer`` without a token attached -- false-positives on prose.

This redactor is **best-effort, not perfect**, mirroring ``docs/SECURITY_PRIVACY.md``
(``Redaction Limitations``). Adversarially-crafted strings can evade pattern matching;
high-sensitivity content should be kept ``private`` rather than promoted to ``team`` or ``org``.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class Finding:
    kind: str
    start: int
    end: int
    placeholder: str


@dataclass(frozen=True, slots=True)
class _Rule:
    kind: str
    pattern: re.Pattern[str]
    group: int


# Anthropic (rule 9) is listed before OpenAI (rule 8) here because ``sk-ant-...`` is a
# strict prefix of the OpenAI ``sk-...`` shape; matching OpenAI first would consume the
# Anthropic prefix and emit the wrong kind.
RULES_IN_ORDER: Final[tuple[_Rule, ...]] = (
    _Rule(
        "PRIVATE_KEY_BLOCK",
        re.compile(
            r"-----BEGIN [A-Z0-9 ]*?PRIVATE KEY(?: BLOCK)?-----[\s\S]*?"
            r"-----END [A-Z0-9 ]*?PRIVATE KEY(?: BLOCK)?-----"
        ),
        0,
    ),
    _Rule(
        "URL_WITH_CREDENTIALS",
        re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s/?#:@]*:[^\s/?#@]+@[^\s]+"),
        0,
    ),
    _Rule(
        "JWT",
        re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b"),
        0,
    ),
    _Rule(
        "AWS_ACCESS_KEY",
        re.compile(r"\b(?:AKIA|ASIA|AGPA|AROA|AIPA|ANPA|ANVA|ABIA|ACCA)[0-9A-Z]{16}\b"),
        0,
    ),
    # Capture-group preservation: keep the ``aws_secret_access_key=`` label intact so the
    # surrounding decision/log line stays human-readable; only the secret value is masked.
    _Rule(
        "AWS_SECRET_KEY",
        re.compile(
            r"(?i)(?:aws[_\-]?secret(?:[_\-]?access)?[_\-]?key)[\"'\s:=]{1,5}"
            r"([A-Za-z0-9/+=]{40})\b"
        ),
        1,
    ),
    _Rule(
        "GITHUB_TOKEN",
        re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{20,255}\b"),
        0,
    ),
    _Rule(
        "STRIPE_KEY",
        re.compile(r"\b(?:sk|pk|rk)_(?:test|live)_[A-Za-z0-9]{24,}\b"),
        0,
    ),
    _Rule(
        "ANTHROPIC_KEY",
        re.compile(r"\bsk-ant-(?:api|admin)\d{2}-[A-Za-z0-9_\-]{40,}\b"),
        0,
    ),
    _Rule(
        "OPENAI_KEY",
        re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_\-]{20,}\b"),
        0,
    ),
    _Rule(
        "HUGGINGFACE_TOKEN",
        re.compile(r"\bhf_[A-Za-z0-9]{30,}\b"),
        0,
    ),
    _Rule(
        "SLACK_TOKEN",
        re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
        0,
    ),
    _Rule(
        "GOOGLE_API_KEY",
        re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"),
        0,
    ),
    # Capture-group preservation: keep ``Authorization: Bearer`` visible; redact only the token.
    _Rule(
        "BEARER_TOKEN",
        re.compile(r"(?i)\b(?:Authorization\s*[:=]\s*)?Bearer\s+([A-Za-z0-9._\-]{20,})"),
        1,
    ),
    _Rule(
        "GENERIC_SECRET_ASSIGNMENT",
        re.compile(
            r"(?i)\b(?:password|passwd|pwd|secret|api[_\-]?key|access[_\-]?token"
            r"|auth[_\-]?token|client[_\-]?secret)\b\s*[:=]\s*"
            r"[\"']?([^\s\"',;]{8,256})[\"']?"
        ),
        1,
    ),
)


_TOKEN_SPLIT_RE: Final[re.Pattern[str]] = re.compile(r"[^\s<>\"',;()\[\]{}]+")
_HEX_HASH_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{40}"
    r"|[0-9a-fA-F]{64}|[0-9a-fA-F]{128})$"
)
_UUID_RE: Final[re.Pattern[str]] = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-7][0-9a-fA-F]{3}"
    r"-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)
_HIGH_ENTROPY_MIN_LEN: Final[int] = 32
_HIGH_ENTROPY_MIN_BITS: Final[float] = 4.0


def _overlaps_any(span: tuple[int, int], consumed: list[tuple[int, int]]) -> bool:
    s, e = span
    for cs, ce in consumed:
        if s < ce and cs < e:
            return True
        if cs >= e:
            break
    return False


def _insert_sorted(consumed: list[tuple[int, int]], span: tuple[int, int]) -> None:
    lo, hi = 0, len(consumed)
    while lo < hi:
        mid = (lo + hi) // 2
        if consumed[mid][0] < span[0]:
            lo = mid + 1
        else:
            hi = mid
    consumed.insert(lo, span)


def _shannon_entropy(token: str) -> float:
    length = len(token)
    if length == 0:
        return 0.0
    counts = Counter(token)
    return -sum((c / length) * math.log2(c / length) for c in counts.values())


def _scan_high_entropy(text: str, consumed: list[tuple[int, int]]) -> list[Finding]:
    findings: list[Finding] = []
    for match in _TOKEN_SPLIT_RE.finditer(text):
        token = match.group(0)
        if len(token) < _HIGH_ENTROPY_MIN_LEN:
            continue
        span = (match.start(), match.end())
        if _overlaps_any(span, consumed):
            continue
        if _HEX_HASH_RE.match(token):
            continue
        if _UUID_RE.match(token):
            continue
        if "-" not in token and "_" not in token and not any(ch.isdigit() for ch in token):
            continue
        if _shannon_entropy(token) < _HIGH_ENTROPY_MIN_BITS:
            continue
        findings.append(
            Finding("HIGH_ENTROPY_TOKEN", span[0], span[1], "[REDACTED:HIGH_ENTROPY_TOKEN]")
        )
        _insert_sorted(consumed, span)
    return findings


def redact(text: str) -> tuple[str, list[Finding]]:
    """Strip credentials and high-entropy secrets from raw session text before persistence."""
    consumed: list[tuple[int, int]] = []
    findings: list[Finding] = []

    for rule in RULES_IN_ORDER:
        for match in rule.pattern.finditer(text):
            span = (match.start(rule.group), match.end(rule.group))
            if span[0] == -1 or span[0] == span[1]:
                continue
            if _overlaps_any(span, consumed):
                continue
            findings.append(Finding(rule.kind, span[0], span[1], f"[REDACTED:{rule.kind}]"))
            _insert_sorted(consumed, span)

    findings.extend(_scan_high_entropy(text, consumed))

    findings.sort(key=lambda f: (f.start, -f.end))

    out: list[str] = []
    cursor = 0
    for f in findings:
        if f.start < cursor:
            continue
        out.append(text[cursor : f.start])
        out.append(f.placeholder)
        cursor = f.end
    out.append(text[cursor:])
    return "".join(out), findings
