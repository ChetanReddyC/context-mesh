"""Tests for the `HeuristicDistiller` — block split, scoring, extraction, redaction, headline."""

from __future__ import annotations

import dataclasses
import time

import pytest

from context_mesh.distillation import Distiller, DistillerCandidate, HeuristicDistiller

# --- A. Empty / threshold -----------------------------------------------------------------


@pytest.mark.parametrize("text", ["", "   ", "\n\n", "\t"])
def test_distill_empty_returns_empty_list(text: str) -> None:
    assert HeuristicDistiller().distill(text) == []


def test_distill_below_threshold_returns_empty() -> None:
    assert HeuristicDistiller().distill("a single sentence") == []


def test_distill_short_block_dropped() -> None:
    assert HeuristicDistiller().distill("x") == []


def test_distill_redacted_only_block_dropped() -> None:
    private_key = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEpAIBAAKCAQEA1234567890abcdef\n"
        "abcdefghijklmnopqrstuvwxyzABCDEF\n"
        "-----END RSA PRIVATE KEY-----"
    )
    assert HeuristicDistiller().distill(private_key) == []


# --- B. Per-kind extraction ---------------------------------------------------------------


def test_distill_decision_block_extracts_decisions() -> None:
    out = HeuristicDistiller().distill("Decision: we will use Postgres going forward.")
    assert len(out) == 1
    candidate = out[0]
    assert candidate.decisions != ()
    assert candidate.kind == "semantic"


def test_distill_episodic_block_extracts_exception() -> None:
    out = HeuristicDistiller().distill(
        "On 2026-04-15, hit a KeyError: 'session_id' while debugging the payments webhook."
    )
    assert len(out) == 1
    candidate = out[0]
    assert candidate.kind == "episodic"
    assert "KeyError" in candidate.error_signatures


def test_distill_procedural_block_extracts_file_paths() -> None:
    out = HeuristicDistiller().distill(
        "To deploy: run `make deploy` and verify infra/k8s/payments.yaml."
    )
    assert len(out) == 1
    candidate = out[0]
    assert candidate.kind == "procedural"
    assert any("payments.yaml" in p for p in candidate.file_paths)


def test_distill_failed_approach_block_extracts_failed_approaches() -> None:
    out = HeuristicDistiller().distill(
        "We tried using Redis but it didn't work for the auth layer; reverted to Postgres."
    )
    assert len(out) == 1
    assert out[0].failed_approaches != ()


# --- C. Multi-block -----------------------------------------------------------------------


def test_distill_hybrid_session_emits_multiple_candidates() -> None:
    text = (
        "On 2026-04-15 we deployed v1.2.3 and reverted within 10 minutes after PR #482 "
        "broke session persistence.\n"
        "\n"
        "Decision: we will use Postgres going forward for the payments deploy.\n"
        "\n"
        "Rule: never trust the first webhook timestamp from Stripe; always re-verify "
        "with current_time."
    )
    out = HeuristicDistiller().distill(text)
    assert len(out) >= 3


def test_distill_label_header_forces_split() -> None:
    # No blank line between intro and the label header, but the label header rule injects
    # a paragraph break that splits the two blocks. Both blocks score above threshold.
    text = (
        "We tried using Redis but it did not work; reverted to Postgres for the deploy "
        "of payments.\n"
        "Decision: we will use Postgres going forward for the payments deploy."
    )
    out = HeuristicDistiller().distill(text)
    assert len(out) == 2


def test_distill_markdown_header_forces_split() -> None:
    text = (
        "We tried using Redis but it did not work; reverted to Postgres for the payments "
        "deploy.\n"
        "## Stripe webhook fix\n"
        "Decision: we adopted retry-with-jitter going forward for all payments webhooks."
    )
    out = HeuristicDistiller().distill(text)
    assert len(out) == 2


# --- D. Redaction integration -------------------------------------------------------------


def test_distill_body_does_not_contain_raw_secret() -> None:
    text = (
        "We adopted retry-with-jitter going forward for the payments webhook deploy. "
        "The key is sk_live_abcdefghijklmnopqrstuvwx for testing in payments."
    )
    out = HeuristicDistiller().distill(text)
    assert len(out) >= 1
    for candidate in out:
        assert "sk_live" not in candidate.body
    assert any(c.redaction_findings != () for c in out)


def test_distill_redaction_findings_shared_across_candidates() -> None:
    text = (
        "Decision: we will use Postgres going forward for payments deploy and the key is "
        "sk-proj-abcdefghijklmnopqrstuvwxyz12345678 here for testing.\n"
        "\n"
        "Decision: we standardized on retry-with-jitter for all webhooks going forward.\n"
        "\n"
        "Decision: we picked Redis for the cache layer going forward in payments deploy."
    )
    out = HeuristicDistiller().distill(text)
    assert len(out) >= 2
    baseline = out[0].redaction_findings
    for candidate in out[1:]:
        assert candidate.redaction_findings == baseline


# --- E. Headline rules --------------------------------------------------------------------


def test_distill_headline_under_120_chars() -> None:
    long_block = (
        "Decision: we will use Postgres going forward for the payments deploy "
        + ("and " * 50)
        + "standardized on Redis cache."
    )
    out = HeuristicDistiller().distill(long_block)
    assert len(out) == 1
    assert len(out[0].headline) <= 120


def test_distill_headline_uses_markdown_header_when_present() -> None:
    text = (
        "# Stripe webhook fix\n"
        "Details follow here that explain how we adopted Postgres going forward for "
        "payments deploy."
    )
    out = HeuristicDistiller().distill(text)
    assert len(out) == 1
    assert out[0].headline == "Stripe webhook fix"


def test_distill_headline_uses_label_header() -> None:
    out = HeuristicDistiller().distill("Rule: never trust the first webhook timestamp.")
    assert len(out) == 1
    assert out[0].headline.startswith("Rule:")


def test_distill_headline_truncates_with_ellipsis() -> None:
    text = "Decision: " + ("A" * 200) + " going forward for payments deploy."
    out = HeuristicDistiller().distill(text)
    assert len(out) == 1
    headline = out[0].headline
    assert headline.endswith("...")
    assert len(headline) <= 120


# --- F. Determinism -----------------------------------------------------------------------


_DETERMINISM_TEXT: str = (
    "On 2026-04-15 we deployed v1.2.3 and reverted within 10 minutes after PR #482 "
    "broke session persistence.\n"
    "\n"
    "Decision: we will use Postgres going forward for the payments deploy.\n"
    "\n"
    "Rule: never trust the first webhook timestamp from Stripe; always re-verify.\n"
    "\n"
    "We tried using Redis but it did not work for the auth layer; reverted to Postgres.\n"
    "\n"
    "To deploy: run `make deploy` and verify infra/k8s/payments.yaml."
)


def test_distill_is_deterministic() -> None:
    distiller = HeuristicDistiller()
    baseline = [dataclasses.asdict(c) for c in distiller.distill(_DETERMINISM_TEXT)]
    for _ in range(10):
        again = [dataclasses.asdict(c) for c in distiller.distill(_DETERMINISM_TEXT)]
        assert again == baseline


# --- G. Bounds ----------------------------------------------------------------------------


def test_distill_caps_blocks_at_max() -> None:
    block = "Decision: we will use Postgres going forward for the deploy of payments."
    text = "\n\n".join([block] * 200)
    out = HeuristicDistiller().distill(text)
    assert len(out) <= 64


@pytest.mark.slow
def test_distill_handles_large_input_under_budget() -> None:
    paragraph = (
        "Decision: we will use Postgres going forward for the payments deploy.\n"
        "\n"
        "On 2026-04-15 we hit a KeyError during the rollout and reverted v1.2.3.\n"
        "\n"
        "Rule: always re-verify webhook timestamps before processing.\n"
        "\n"
        "To deploy: run `make deploy` and check infra/k8s/payments.yaml.\n"
        "\n"
    )
    body = paragraph * 400
    assert len(body) >= 100_000
    start = time.perf_counter()
    out = HeuristicDistiller().distill(body)
    elapsed = time.perf_counter() - start
    assert elapsed < 5.0, f"distill took {elapsed:.2f}s on a {len(body)}-byte body"
    assert isinstance(out, list)


# --- H. Importance / confidence -----------------------------------------------------------


_IMPORTANCE_TEXT: str = (
    "On 2026-04-15 we deployed v1.2.3 and reverted within 10 minutes after PR #482 "
    "broke session persistence.\n"
    "\n"
    "Decision: we will use Postgres going forward for the payments deploy.\n"
    "\n"
    "Rule: never trust the first webhook timestamp from Stripe; always re-verify."
)


def test_distill_importance_in_range() -> None:
    out = HeuristicDistiller().distill(_IMPORTANCE_TEXT)
    assert len(out) >= 1
    for candidate in out:
        assert 0.0 <= candidate.importance <= 1.0


def test_distill_confidence_in_range() -> None:
    out = HeuristicDistiller().distill(_IMPORTANCE_TEXT)
    assert len(out) >= 1
    for candidate in out:
        assert 0.0 <= candidate.confidence <= 1.0


def test_distill_confidence_higher_for_structurally_rich_block() -> None:
    distiller = HeuristicDistiller(min_block_score=0.0)
    rich = (
        "Decision: we will adopt retry-with-jitter for all payments webhooks going "
        "forward; this is the standard pattern for the deploy."
    )
    plain = "Some plain prose with no special structure that goes on for a moderate length."
    rich_out = distiller.distill(rich)
    plain_out = distiller.distill(plain)
    assert len(rich_out) == 1
    assert len(plain_out) == 1
    assert rich_out[0].confidence >= plain_out[0].confidence


def test_distill_importance_higher_for_error_block() -> None:
    err_text = (
        "We hit a KeyError and a ValueError during deploy. Got HTTP 502 from upstream "
        "and a Traceback (most recent call last): RuntimeError occurred again."
    )
    thin_text = "Decision: x going forward."
    err_out = HeuristicDistiller().distill(err_text)
    thin_out = HeuristicDistiller().distill(thin_text)
    assert len(err_out) == 1
    assert len(thin_out) == 1
    assert err_out[0].importance > 0.6
    assert thin_out[0].importance < err_out[0].importance


# --- I. Tags ------------------------------------------------------------------------------


def test_distill_tags_from_curated_vocabulary() -> None:
    out = HeuristicDistiller().distill(
        "Decision: we will deploy payments service going forward for the team."
    )
    assert len(out) == 1
    assert out[0].tags == ("deploy", "payments")


def test_distill_unknown_tags_not_extracted() -> None:
    out = HeuristicDistiller().distill(
        "Decision: we will use random words going forward for the team."
    )
    assert len(out) == 1
    assert out[0].tags == ()


# --- J. Unicode / robustness --------------------------------------------------------------


def test_distill_unicode_input_does_not_raise() -> None:
    text = "Decision: we adopted 結婚 going forward \U0001f389 for the payments deploy."
    out = HeuristicDistiller().distill(text)
    assert isinstance(out, list)
    for item in out:
        assert isinstance(item, DistillerCandidate)


def test_distill_crlf_normalized() -> None:
    text_lf = (
        "Decision: we will use Postgres going forward for the payments deploy.\n"
        "\n"
        "Rule: always re-verify webhook timestamps from Stripe."
    )
    text_crlf = text_lf.replace("\n", "\r\n")
    distiller = HeuristicDistiller()
    out_lf = [dataclasses.asdict(c) for c in distiller.distill(text_lf)]
    out_crlf = [dataclasses.asdict(c) for c in distiller.distill(text_crlf)]
    assert out_lf == out_crlf


# --- K. Protocol integration --------------------------------------------------------------


def test_heuristic_satisfies_distiller_protocol() -> None:
    assert isinstance(HeuristicDistiller(), Distiller)


def test_heuristic_name_is_stable() -> None:
    assert HeuristicDistiller().name == "heuristic/v1"


def test_distill_returns_list_of_distiller_candidate() -> None:
    out = HeuristicDistiller().distill(_DETERMINISM_TEXT)
    assert len(out) >= 1
    for item in out:
        assert isinstance(item, DistillerCandidate)


# --- L. Error signature variants ----------------------------------------------------------


def test_distill_extracts_traceback_marker() -> None:
    text = (
        "Traceback (most recent call last):\n"
        '  File "x.py", line 1, in <module>\n'
        "    raise ValueError\n"
        "ValueError: bad input from the deploy of payments service today."
    )
    out = HeuristicDistiller().distill(text)
    assert len(out) >= 1
    assert any("Traceback" in c.error_signatures for c in out)


def test_distill_extracts_http_error_with_explicit_label() -> None:
    text = (
        "Decision: we adopted retry-with-jitter going forward; got HTTP 502 from the "
        "upstream service today during the payments deploy."
    )
    out = HeuristicDistiller().distill(text)
    assert len(out) == 1
    assert any("502" in sig for sig in out[0].error_signatures)
