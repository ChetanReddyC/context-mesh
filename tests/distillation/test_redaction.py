"""Tests for `context_mesh.distillation.redaction.redact` and the `Finding` dataclass."""

from __future__ import annotations

import dataclasses
import math
import random
import time
from collections import Counter

import pytest

from context_mesh.distillation import Finding, redact
from context_mesh.distillation import redaction as redaction_module

# Fixtures chosen so each rule's positive case is realistic-shaped (matches the documented
# vendor format) and each negative case is the smallest possible string that should NOT match.
_PRIVATE_KEY_RSA: str = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIEpAIBAAKCAQEA1234567890abcdef\n"
    "abcdefghijklmnopqrstuvwxyzABCDEF\n"
    "-----END RSA PRIVATE KEY-----"
)
_PRIVATE_KEY_OPENSSH: str = (
    "-----BEGIN OPENSSH PRIVATE KEY-----\n"
    "b3BlbnNzaC1rZXktdjEAAAAA\n"
    "-----END OPENSSH PRIVATE KEY-----"
)
_PRIVATE_KEY_PGP: str = (
    "-----BEGIN PGP PRIVATE KEY BLOCK-----\n"
    "lQHYBF1234567890abcdef\n"
    "-----END PGP PRIVATE KEY BLOCK-----"
)
_PUBLIC_KEY_BLOCK: str = (
    "-----BEGIN PUBLIC KEY-----\nMIIBIjANBgkqhkiG9w0BAQEFAAOC\n-----END PUBLIC KEY-----"
)

_JWT_REAL: str = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4ifQ"
    ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
)

# 40-char base64-shaped string with no digits/hyphens/underscores so the high-entropy
# guard skips it and we can assert the AWS_SECRET_KEY rule alone does not fire without a label.
_AWS_SECRET_BARE_NO_LABEL: str = "wJalrXUtnFEMIKMDENGbPxRfiCYEXAMPLEKEYabc"

# 64-char alnum mixing case + digits, no hyphens/underscores. Verified entropy ~5.5 (>= 4.0)
# and no rule prefix shape, so the only finding should be HIGH_ENTROPY_TOKEN.
_HIGH_ENTROPY_64: str = "K9zL3pQwR8mN4tBvC7yX1jA5sH6gE0fD2nU2hY3iJ7kP4qV5wN8mE6tA1xZ9bRcF"

# 64-char pure-lowercase letters: entropy ~4.67 (>= 4.0) but lacks digit/hyphen/underscore,
# so the high-entropy alphabet guard skips it.
_LOWERCASE_64_NO_DIGITS: str = "abcdefghijklmnopqrstuvwxyzabcdefghijklmnopqrstuvwxyzabcdefghijkl"


def _kinds(findings: list[Finding]) -> list[str]:
    return [f.kind for f in findings]


# --- A. PRIVATE_KEY_BLOCK ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "fixture_name"),
    [
        (_PRIVATE_KEY_RSA, "rsa"),
        (_PRIVATE_KEY_OPENSSH, "openssh"),
    ],
)
def test_private_key_block_positive(text: str, fixture_name: str) -> None:
    out, findings = redact(text)
    assert len(findings) == 1
    assert findings[0].kind == "PRIVATE_KEY_BLOCK"
    assert "[REDACTED:PRIVATE_KEY_BLOCK]" in out
    assert "BEGIN" not in out
    assert "END" not in out


def test_private_key_block_pgp_positive() -> None:
    out, findings = redact(_PRIVATE_KEY_PGP)
    assert len(findings) == 1
    assert findings[0].kind == "PRIVATE_KEY_BLOCK"
    assert "[REDACTED:PRIVATE_KEY_BLOCK]" in out


def test_private_key_block_public_key_negative() -> None:
    _, findings = redact(_PUBLIC_KEY_BLOCK)
    assert findings == []


def test_private_key_block_prose_negative() -> None:
    _, findings = redact("the private key in the safe is on a usb drive")
    assert findings == []


# --- B. URL_WITH_CREDENTIALS ---------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://alice:hunter2@db.acme.com:5432/prod",
        "postgres://u:p@host/db",
    ],
)
def test_url_with_credentials_positive(url: str) -> None:
    out, findings = redact(url)
    assert len(findings) == 1
    assert findings[0].kind == "URL_WITH_CREDENTIALS"
    assert out == "[REDACTED:URL_WITH_CREDENTIALS]"


def test_url_with_credentials_empty_user_positive() -> None:
    _, findings = redact("redis://:secretpass@host:6379")
    assert any(f.kind == "URL_WITH_CREDENTIALS" for f in findings)


@pytest.mark.parametrize(
    "url",
    [
        "https://docs.python.org/3/",
        "mailto:alice@acme.com",
        "git@github.com:user/repo.git",
    ],
)
def test_url_with_credentials_negative(url: str) -> None:
    _, findings = redact(url)
    assert findings == []


# --- C. JWT --------------------------------------------------------------------------------


def test_jwt_standalone_positive() -> None:
    out, findings = redact(_JWT_REAL)
    assert _kinds(findings) == ["JWT"]
    assert out == "[REDACTED:JWT]"


def test_jwt_inline_positive() -> None:
    text = f"the token is {_JWT_REAL} and it expired"
    out, findings = redact(text)
    assert _kinds(findings) == ["JWT"]
    assert out == "the token is [REDACTED:JWT] and it expired"


@pytest.mark.parametrize(
    "text",
    [
        "eyJabc.def",
        "eyJxxx.yyyy.zz",
    ],
)
def test_jwt_negative(text: str) -> None:
    _, findings = redact(text)
    assert findings == []


# --- D. AWS_ACCESS_KEY ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "AKIAIOSFODNN7EXAMPLE",
        "ASIAQ3EGTXYZ12345678",
    ],
)
def test_aws_access_key_positive(key: str) -> None:
    out, findings = redact(key)
    assert _kinds(findings) == ["AWS_ACCESS_KEY"]
    assert out == "[REDACTED:AWS_ACCESS_KEY]"


@pytest.mark.parametrize(
    "key",
    [
        "AKIA1234",
        "AKIAabcdefghijklmnop",
    ],
)
def test_aws_access_key_negative(key: str) -> None:
    _, findings = redact(key)
    assert "AWS_ACCESS_KEY" not in _kinds(findings)


# --- E. AWS_SECRET_KEY ---------------------------------------------------------------------


def test_aws_secret_key_label_preserved() -> None:
    secret = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
    text = f"aws_secret_access_key={secret}"
    out, findings = redact(text)
    assert _kinds(findings) == ["AWS_SECRET_KEY"]
    assert out == "aws_secret_access_key=[REDACTED:AWS_SECRET_KEY]"
    f = findings[0]
    assert text[f.start : f.end] == secret


def test_aws_secret_key_case_variant() -> None:
    secret = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
    text = f'AWS_Secret_Key: "{secret}"'
    out, findings = redact(text)
    assert _kinds(findings) == ["AWS_SECRET_KEY"]
    assert out == 'AWS_Secret_Key: "[REDACTED:AWS_SECRET_KEY]"'


def test_aws_secret_key_no_label_does_not_match() -> None:
    text = f"a paragraph {_AWS_SECRET_BARE_NO_LABEL} end"
    _, findings = redact(text)
    assert "AWS_SECRET_KEY" not in _kinds(findings)


# --- F. Per-vendor token rules -------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "positive", "negative"),
    [
        (
            "GITHUB_TOKEN",
            "ghp_abcdefghijklmnopqrstuvwxyz1234567890ab",
            "ghp_short",
        ),
        (
            "STRIPE_KEY",
            "sk_live_abcdefghijklmnopqrstuvwx",
            "sk_live_short",
        ),
        (
            "STRIPE_KEY",
            "pk_test_abcdefghijklmnopqrstuvwxyz12345",
            "pk_test_short",
        ),
        (
            "STRIPE_KEY",
            "rk_live_abcdefghijklmnopqrstuvwxyz1234567",
            "rk_live_short",
        ),
        (
            "HUGGINGFACE_TOKEN",
            "hf_abcdefghijklmnopqrstuvwxyz1234567890",
            "hf_short",
        ),
        (
            "SLACK_TOKEN",
            "xoxb-1234567890-1234567890-abcdefghij",
            "xoxb-",
        ),
        (
            "GOOGLE_API_KEY",
            "AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ1234567",
            "AIzaSy123",
        ),
    ],
)
def test_vendor_token_positive_negative(kind: str, positive: str, negative: str) -> None:
    out, findings = redact(positive)
    assert _kinds(findings) == [kind]
    assert out == f"[REDACTED:{kind}]"

    _, neg_findings = redact(negative)
    assert kind not in _kinds(neg_findings)


def test_anthropic_key_does_not_get_classified_as_openai() -> None:
    key = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz1234567890ABCDEFGH"
    out, findings = redact(key)
    assert _kinds(findings) == ["ANTHROPIC_KEY"]
    assert out == "[REDACTED:ANTHROPIC_KEY]"


def test_anthropic_short_negative() -> None:
    _, findings = redact("sk-ant-")
    assert findings == []


@pytest.mark.parametrize(
    "key",
    [
        "sk-proj-abcdefghijklmnopqrstuvwxyz12345678",
        "sk-abcdefghijklmnopqrstuv",
    ],
)
def test_openai_key_positive(key: str) -> None:
    out, findings = redact(key)
    assert _kinds(findings) == ["OPENAI_KEY"]
    assert out == "[REDACTED:OPENAI_KEY]"


def test_openai_key_short_negative() -> None:
    _, findings = redact("sk-")
    assert findings == []


# --- G. BEARER_TOKEN -----------------------------------------------------------------------


def test_bearer_with_jwt_classifies_as_jwt() -> None:
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.A1234B5678abcdefghij"
    text = f"Authorization: Bearer {jwt}"
    out, findings = redact(text)
    assert _kinds(findings) == ["JWT"]
    assert out == "Authorization: Bearer [REDACTED:JWT]"
    f = findings[0]
    assert text[f.start : f.end] == jwt


def test_bearer_token_without_jwt_shape_positive() -> None:
    token = "abc123def456ghi789jklmnopqrs"
    text = f"Bearer {token}"
    out, findings = redact(text)
    assert _kinds(findings) == ["BEARER_TOKEN"]
    assert out == "Bearer [REDACTED:BEARER_TOKEN]"
    f = findings[0]
    assert text[f.start : f.end] == token


@pytest.mark.parametrize(
    "text",
    [
        "the bearer of bad news",
        "Bearer abc",
    ],
)
def test_bearer_token_negative(text: str) -> None:
    _, findings = redact(text)
    assert "BEARER_TOKEN" not in _kinds(findings)


# --- H. GENERIC_SECRET_ASSIGNMENT ----------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected_out"),
    [
        ("password=hunter2andmore", "password=[REDACTED:GENERIC_SECRET_ASSIGNMENT]"),
        (
            'API_KEY: "abc123def456ghi789"',
            'API_KEY: "[REDACTED:GENERIC_SECRET_ASSIGNMENT]"',
        ),
        (
            "client_secret = 'xyzlongenough12345'",
            "client_secret = '[REDACTED:GENERIC_SECRET_ASSIGNMENT]'",
        ),
    ],
)
def test_generic_secret_assignment_positive(text: str, expected_out: str) -> None:
    out, findings = redact(text)
    assert _kinds(findings) == ["GENERIC_SECRET_ASSIGNMENT"]
    assert out == expected_out


@pytest.mark.parametrize(
    "text",
    [
        'password=""',
        "password=TODO",
    ],
)
def test_generic_secret_assignment_negative(text: str) -> None:
    _, findings = redact(text)
    assert "GENERIC_SECRET_ASSIGNMENT" not in _kinds(findings)


# --- I. HIGH_ENTROPY_TOKEN -----------------------------------------------------------------


def test_high_entropy_fixture_passes_threshold() -> None:
    counts = Counter(_HIGH_ENTROPY_64)
    n = len(_HIGH_ENTROPY_64)
    entropy = -sum((c / n) * math.log2(c / n) for c in counts.values())
    assert entropy >= 4.0


def test_high_entropy_token_positive() -> None:
    text = f"token {_HIGH_ENTROPY_64} here"
    out, findings = redact(text)
    assert _kinds(findings) == ["HIGH_ENTROPY_TOKEN"]
    assert out == "token [REDACTED:HIGH_ENTROPY_TOKEN] here"


@pytest.mark.parametrize(
    ("label", "text"),
    [
        ("sha-256", "a3f5c8b1d4e7a2c6f9b8d1e4a7c2f5b8d1e4a7c2f5b8d1e4a7c2f5b8d1e4a7c2"),
        ("sha-1", "a3f5c8b1d4e7a2c6f9b8d1e4a7c2f5b8d1e4a7c2"),
        ("md5", "d41d8cd98f00b204e9800998ecf8427e"),
        ("sha-512", "a3f5c8b1d4e7a2c6f9b8d1e4a7c2f5b8d1e4a7c2" * 3 + "a3f5c8b1"),
        ("uuid-v4", "550e8400-e29b-41d4-a716-446655440000"),
        ("lowercase-no-digits", _LOWERCASE_64_NO_DIGITS),
        ("camelcase", "thisIsAVeryLongCamelCaseIdentifierThatLooksLikeCode"),
    ],
)
def test_high_entropy_negative(label: str, text: str) -> None:
    _, findings = redact(text)
    assert "HIGH_ENTROPY_TOKEN" not in _kinds(findings)


# --- J. Cross-cutting / structural ---------------------------------------------------------


def test_empty_string() -> None:
    assert redact("") == ("", [])


def test_plain_prose_unchanged() -> None:
    assert redact("hello world") == ("hello world", [])


_IDEMPOTENCE_FIXTURES: list[str] = [
    _PRIVATE_KEY_RSA,
    _PRIVATE_KEY_OPENSSH,
    "https://alice:hunter2@db.acme.com:5432/prod",
    f"the token is {_JWT_REAL} end",
    "AKIAIOSFODNN7EXAMPLE",
    "aws_secret_access_key=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    "ghp_abcdefghijklmnopqrstuvwxyz1234567890ab",
    "sk_live_abcdefghijklmnopqrstuvwx",
    "sk-ant-api03-abcdefghijklmnopqrstuvwxyz1234567890ABCDEFGH",
    "sk-proj-abcdefghijklmnopqrstuvwxyz12345678",
    "hf_abcdefghijklmnopqrstuvwxyz1234567890",
    "xoxb-1234567890-1234567890-abcdefghij",
    "AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ1234567",
    f"Authorization: Bearer {_JWT_REAL}",
    "Bearer abc123def456ghi789jklmnopqrs",
    f"token {_HIGH_ENTROPY_64} end",
]


@pytest.mark.parametrize("text", _IDEMPOTENCE_FIXTURES)
def test_redact_is_idempotent(text: str) -> None:
    first, first_findings = redact(text)
    second, second_findings = redact(first)
    assert first == second
    assert first_findings != []
    assert second_findings == []


def test_redact_text_stable_for_generic_secret_assignment() -> None:
    text = "password=hunter2andmore"
    first, _ = redact(text)
    second, _ = redact(first)
    assert first == second


def test_redact_is_deterministic() -> None:
    text = (
        f"password=secret12345 and AKIAIOSFODNN7EXAMPLE plus token {_HIGH_ENTROPY_64} "
        f"and {_JWT_REAL}"
    )
    baseline_out, baseline_findings = redact(text)
    for _ in range(50):
        out, findings = redact(text)
        assert out == baseline_out
        assert findings == baseline_findings


def test_multi_secret_line_finds_each() -> None:
    text = "password=abc12345andlong api_key=xyz67890def123"
    out, findings = redact(text)
    assert len(findings) >= 2
    assert all(f.kind == "GENERIC_SECRET_ASSIGNMENT" for f in findings)
    assert out == (
        "password=[REDACTED:GENERIC_SECRET_ASSIGNMENT] api_key=[REDACTED:GENERIC_SECRET_ASSIGNMENT]"
    )


def test_unicode_passthrough() -> None:
    secret = "sk-proj-abcdef0123456789ABCDEFGHIJ"
    text = f"こんにちは {secret} さようなら"
    out, findings = redact(text)
    assert _kinds(findings) == ["OPENAI_KEY"]
    assert out == "こんにちは [REDACTED:OPENAI_KEY] さようなら"
    f = findings[0]
    assert text[f.start : f.end] == secret


# --- Offsets correctness -------------------------------------------------------------------


_FULL_MATCH_OFFSET_CASES: list[tuple[str, str, str]] = [
    ("PRIVATE_KEY_BLOCK", _PRIVATE_KEY_RSA, _PRIVATE_KEY_RSA),
    (
        "URL_WITH_CREDENTIALS",
        "https://alice:hunter2@db.acme.com:5432/prod",
        "https://alice:hunter2@db.acme.com:5432/prod",
    ),
    ("JWT", _JWT_REAL, _JWT_REAL),
    ("AWS_ACCESS_KEY", "AKIAIOSFODNN7EXAMPLE", "AKIAIOSFODNN7EXAMPLE"),
    (
        "GITHUB_TOKEN",
        "ghp_abcdefghijklmnopqrstuvwxyz1234567890ab",
        "ghp_abcdefghijklmnopqrstuvwxyz1234567890ab",
    ),
    (
        "STRIPE_KEY",
        "sk_live_abcdefghijklmnopqrstuvwx",
        "sk_live_abcdefghijklmnopqrstuvwx",
    ),
    (
        "ANTHROPIC_KEY",
        "sk-ant-api03-abcdefghijklmnopqrstuvwxyz1234567890ABCDEFGH",
        "sk-ant-api03-abcdefghijklmnopqrstuvwxyz1234567890ABCDEFGH",
    ),
    (
        "OPENAI_KEY",
        "sk-proj-abcdefghijklmnopqrstuvwxyz12345678",
        "sk-proj-abcdefghijklmnopqrstuvwxyz12345678",
    ),
    (
        "HUGGINGFACE_TOKEN",
        "hf_abcdefghijklmnopqrstuvwxyz1234567890",
        "hf_abcdefghijklmnopqrstuvwxyz1234567890",
    ),
    (
        "SLACK_TOKEN",
        "xoxb-1234567890-1234567890-abcdefghij",
        "xoxb-1234567890-1234567890-abcdefghij",
    ),
    (
        "GOOGLE_API_KEY",
        "AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ1234567",
        "AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ1234567",
    ),
]


@pytest.mark.parametrize(("kind", "text", "expected_secret"), _FULL_MATCH_OFFSET_CASES)
def test_offsets_full_match_rules(kind: str, text: str, expected_secret: str) -> None:
    _, findings = redact(text)
    matching = [f for f in findings if f.kind == kind]
    assert len(matching) == 1
    f = matching[0]
    assert text[f.start : f.end] == expected_secret


def test_offsets_aws_secret_key_capture_only() -> None:
    secret = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
    text = f"aws_secret_access_key={secret}"
    _, findings = redact(text)
    f = next(x for x in findings if x.kind == "AWS_SECRET_KEY")
    assert text[f.start : f.end] == secret


def test_offsets_bearer_token_capture_only() -> None:
    token = "abc123def456ghi789jklmnopqrs"
    text = f"Bearer {token}"
    _, findings = redact(text)
    f = next(x for x in findings if x.kind == "BEARER_TOKEN")
    assert text[f.start : f.end] == token


def test_offsets_generic_secret_assignment_capture_only() -> None:
    secret = "hunter2andmore"
    text = f"password={secret}"
    _, findings = redact(text)
    f = next(x for x in findings if x.kind == "GENERIC_SECRET_ASSIGNMENT")
    assert text[f.start : f.end] == secret


# --- Module docstring ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "needle",
    [
        "Email addresses",
        "UUIDs",
        "Raw hex hashes",
        "File paths containing usernames",
    ],
)
def test_module_docstring_documents_what_we_do_not_redact(needle: str) -> None:
    doc = redaction_module.__doc__
    assert doc is not None
    assert needle in doc


# --- Finding placeholder format ------------------------------------------------------------


@pytest.mark.parametrize("text", _IDEMPOTENCE_FIXTURES)
def test_finding_placeholder_format(text: str) -> None:
    _, findings = redact(text)
    for f in findings:
        assert f.placeholder == f"[REDACTED:{f.kind}]"


# --- Finding is frozen + slotted -----------------------------------------------------------


def test_finding_is_frozen() -> None:
    f = Finding(kind="X", start=0, end=0, placeholder="[REDACTED:X]")
    with pytest.raises(dataclasses.FrozenInstanceError):
        f.kind = "Y"  # type: ignore[misc]


def test_finding_uses_slots() -> None:
    f = Finding(kind="X", start=0, end=0, placeholder="[REDACTED:X]")
    assert not hasattr(f, "__dict__")


# --- Performance smoke ---------------------------------------------------------------------


@pytest.mark.slow
def test_redact_performance_smoke_one_megabyte() -> None:
    rng = random.Random(0xC0FFEE)
    alphabet = "abcdefghijklmnopqrstuvwxyz "
    payload = "".join(rng.choice(alphabet) for _ in range(1_000_000))
    start = time.perf_counter()
    out, findings = redact(payload)
    elapsed = time.perf_counter() - start
    assert elapsed < 5.0, f"redact took {elapsed:.2f}s on a 1MB payload"
    assert isinstance(out, str)
    assert isinstance(findings, list)
