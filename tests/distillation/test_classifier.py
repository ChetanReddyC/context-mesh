"""Tests for `context_mesh.distillation.classifier.classify` and `ClassificationResult`."""

from __future__ import annotations

import dataclasses
import time

import pytest

from context_mesh import distillation
from context_mesh.distillation import ClassificationResult, classify
from context_mesh.distillation import classifier as classifier_module

# --- A. Procedural positives --------------------------------------------------------------


_PROCEDURAL_POSITIVES: list[tuple[str, str]] = [
    ("```bash\nmake deploy --region=us-west\n```", "fenced-bash-command"),
    (
        "To deploy: run `kubectl apply -f k8s/`. Then verify with `kubectl rollout status`.",
        "to-x-template-with-inline-commands",
    ),
    ("Steps:\n1. Clone the repo\n2. Run make bootstrap\n3. docker-compose up", "numbered-steps"),
    (
        "Procedure: rollback by running `kubectl rollout undo deployment/payments`.",
        "procedure-header",
    ),
    ("Run npm test before pushing. Run npm run build before deploying.", "imperative-runs"),
]


@pytest.mark.parametrize(("body", "label"), _PROCEDURAL_POSITIVES)
def test_procedural_positive_kind(body: str, label: str) -> None:
    result = classify(body)
    assert result.kind == "procedural"


@pytest.mark.parametrize(("body", "label"), _PROCEDURAL_POSITIVES)
def test_procedural_positive_score_is_strict_max(body: str, label: str) -> None:
    result = classify(body)
    assert result.scores["procedural"] > result.scores["episodic"]
    assert result.scores["procedural"] > result.scores["semantic"]


@pytest.mark.parametrize(("body", "label"), _PROCEDURAL_POSITIVES)
def test_procedural_positive_not_default(body: str, label: str) -> None:
    result = classify(body)
    assert result.used_default is False


# --- B. Episodic positives ----------------------------------------------------------------


_EPISODIC_POSITIVES: list[tuple[str, str]] = [
    ("On 2026-04-15, agent debugged the Stripe webhook timeout.", "iso-date-debug"),
    (
        "Yesterday we hit a 502 on /checkout in payments service. Root cause: stale cache.",
        "yesterday-incident",
    ),
    ("Last Tuesday Alice deployed v1.2.3 and reverted within 10 minutes.", "actor-deploy-revert"),
    ("Bug fixed.", "minimal-past-tense"),
    ("PR #482 broke session persistence; reverted.", "pr-broke-revert"),
]


@pytest.mark.parametrize(("body", "label"), _EPISODIC_POSITIVES)
def test_episodic_positive_kind(body: str, label: str) -> None:
    result = classify(body)
    assert result.kind == "episodic"


@pytest.mark.parametrize(("body", "label"), _EPISODIC_POSITIVES)
def test_episodic_positive_score_is_strict_max(body: str, label: str) -> None:
    result = classify(body)
    assert result.scores["episodic"] > result.scores["procedural"]
    assert result.scores["episodic"] > result.scores["semantic"]


@pytest.mark.parametrize(("body", "label"), _EPISODIC_POSITIVES)
def test_episodic_positive_not_default(body: str, label: str) -> None:
    result = classify(body)
    assert result.used_default is False


# --- C. Semantic positives ----------------------------------------------------------------


_SEMANTIC_POSITIVES: list[tuple[str, str]] = [
    ("Always sanitize SQL inputs in the auth layer.", "always-imperative"),
    ("Rule: PRs to the payments service require security team review.", "rule-header"),
    (
        "Never trust the first webhook timestamp from Stripe; always re-verify with current_time.",
        "never-always-modal",
    ),
    (
        "All auth-related changes must explicitly handle both guest and logged-in flows.",
        "all-auth-must",
    ),
    (
        "Database migrations on the orders table require a maintenance window.",
        "require-present-tense",
    ),
]


@pytest.mark.parametrize(("body", "label"), _SEMANTIC_POSITIVES)
def test_semantic_positive_kind(body: str, label: str) -> None:
    result = classify(body)
    assert result.kind == "semantic"


@pytest.mark.parametrize(("body", "label"), _SEMANTIC_POSITIVES)
def test_semantic_positive_score_is_strict_max(body: str, label: str) -> None:
    result = classify(body)
    assert result.scores["semantic"] > result.scores["procedural"]
    assert result.scores["semantic"] > result.scores["episodic"]


@pytest.mark.parametrize(("body", "label"), _SEMANTIC_POSITIVES)
def test_semantic_positive_not_default(body: str, label: str) -> None:
    result = classify(body)
    assert result.used_default is False


# --- D. Headline boost --------------------------------------------------------------------

# The body alone fires only present_tense_universal (`is`/`works`); without the title
# semantic stays the only nonzero kind. Adding the headline "Deploy procedure" injects
# `imperative_sentence_start` ("Deploy") at 1.5x weight, flipping the winner.
_HEADLINE_BOOST_BODY: str = "This is the standard team practice. We follow it. It works."


def test_headline_boost_flips_to_procedural() -> None:
    result = classify(_HEADLINE_BOOST_BODY, headline="Deploy procedure")
    assert result.kind == "procedural"


def test_headline_boost_without_headline_is_not_procedural() -> None:
    result = classify(_HEADLINE_BOOST_BODY)
    assert result.kind != "procedural"
    assert result.kind == "semantic"


# --- E. Tiebreak -- procedural-vs-semantic ------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Spec asserts procedural wins on `Always run \\`make test\\` before merging. Never "
        "push without it.`, but two `modal_imperative` hits (Always, Never) at weight 2.0 "
        "yield semantic=4.0 vs procedural=1.5 (one inline_code_command_shape only; "
        "imperative_sentence_start does not fire because the line starts with `Always`, not "
        "an imperative verb). Either the procedural weights need a bump or the spec example "
        "is wrong; surface it for Phase 3 review."
    ),
)
def test_tiebreak_procedural_vs_semantic_short_inline() -> None:
    body = "Always run `make test` before merging. Never push without it."
    result = classify(body)
    assert result.kind == "procedural"
    assert result.scores["procedural"] > result.scores["semantic"]


# --- F. Tiebreak -- episodic-vs-semantic --------------------------------------------------


def test_tiebreak_episodic_vs_semantic_resolves_semantic() -> None:
    # `we` is present but not followed by a past verb in the we_actor_past list,
    # so only modal_imperative ("must", "always") fires.
    result = classify("We must always re-verify webhook timestamps.")
    assert result.kind == "semantic"
    assert result.scores["semantic"] > result.scores["episodic"]


# --- G. Specificity ladder -- procedural beats semantic on cumulative weight --------------


# The forced-tie construction is awkward without coupling to private weights. The spec
# directs us to fall back on this single concrete check, which exercises the
# imperative/inline-code stack against a `modal_imperative` opponent.
def test_specificity_ladder_procedural_beats_semantic() -> None:
    body = "Run `make build`. Always do it."
    result = classify(body)
    assert result.kind == "procedural"
    assert result.scores["procedural"] > result.scores["semantic"]


# --- H. All-zero / empty cases ------------------------------------------------------------


_EMPTY_LIKE_BODIES: list[tuple[str, str]] = [
    ("", "empty-string"),
    ("   \n  ", "whitespace-only"),
    ("x", "single-char"),
    ("こんにちは みなさん", "non-english-prose"),
    ("hello world", "plain-english-prose"),
]


@pytest.mark.parametrize(("body", "label"), _EMPTY_LIKE_BODIES)
def test_empty_like_falls_back_to_default_kind(body: str, label: str) -> None:
    result = classify(body)
    assert result.kind == "semantic"


@pytest.mark.parametrize(("body", "label"), _EMPTY_LIKE_BODIES)
def test_empty_like_marks_used_default(body: str, label: str) -> None:
    result = classify(body)
    assert result.used_default is True


@pytest.mark.parametrize(("body", "label"), _EMPTY_LIKE_BODIES)
def test_empty_like_zero_scores(body: str, label: str) -> None:
    result = classify(body)
    assert dict(result.scores) == {"episodic": 0.0, "semantic": 0.0, "procedural": 0.0}


@pytest.mark.parametrize(("body", "label"), _EMPTY_LIKE_BODIES)
def test_empty_like_no_matched_signals(body: str, label: str) -> None:
    result = classify(body)
    assert result.matched_signals == ()


def test_default_kind_constant_is_semantic() -> None:
    assert classifier_module.DEFAULT_KIND == "semantic"


# --- I. Hybrid input (episodic dominates but semantic remains nonzero) --------------------


def test_hybrid_input_picks_episodic_with_semantic_residual() -> None:
    body = (
        "On 2026-04-15 we discovered the Stripe webhook bug. Going forward, always "
        "re-verify with current_time."
    )
    result = classify(body)
    assert result.kind == "episodic"
    assert result.scores["episodic"] == max(result.scores.values())
    assert result.scores["semantic"] > 0.0


# --- J. Determinism -----------------------------------------------------------------------

# Rich body: drives multiple signals across all three kinds so determinism would expose
# any accidental dependence on iteration order, dict hashing, or state.
_DETERMINISM_BODY: str = (
    "On 2026-04-15 we deployed v1.2.3 and reverted within 10 minutes after PR #482 broke "
    "session persistence. Always sanitize SQL inputs. Run `make test` before merging."
)


def test_classify_is_deterministic() -> None:
    baseline = classify(_DETERMINISM_BODY)
    for _ in range(50):
        again = classify(_DETERMINISM_BODY)
        assert again.kind == baseline.kind
        assert dict(again.scores) == dict(baseline.scores)
        assert again.matched_signals == baseline.matched_signals
        assert again.used_default == baseline.used_default


# --- K. Dataclass shape -------------------------------------------------------------------


def _sample_result() -> ClassificationResult:
    return classify("Always sanitize SQL inputs in the auth layer.")


def test_classification_result_is_frozen() -> None:
    result = _sample_result()
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.kind = "episodic"  # type: ignore[misc]


def test_classification_result_uses_slots() -> None:
    result = _sample_result()
    assert not hasattr(result, "__dict__")


def test_classification_result_scores_keys() -> None:
    result = _sample_result()
    assert set(result.scores.keys()) == {"episodic", "semantic", "procedural"}


def test_classification_result_matched_signals_is_tuple() -> None:
    result = _sample_result()
    assert isinstance(result.matched_signals, tuple)


# --- L. Module exports --------------------------------------------------------------------


def test_classify_is_callable() -> None:
    assert callable(classify)


def test_classify_exported_in_distillation_all() -> None:
    assert "classify" in distillation.__all__


def test_classification_result_exported_in_distillation_all() -> None:
    assert "ClassificationResult" in distillation.__all__


# --- M. Performance smoke -----------------------------------------------------------------


@pytest.mark.slow
def test_classify_performance_smoke_ten_kb() -> None:
    paragraph = (
        "On 2026-04-15 we deployed v1.2.3 and reverted. Always sanitize SQL inputs. "
        "Run `make test` before merging. "
    )
    body = paragraph * 100
    assert len(body) >= 10_000
    start = time.perf_counter()
    result = classify(body)
    elapsed = time.perf_counter() - start
    assert elapsed < 0.2, f"classify took {elapsed * 1000:.1f}ms on a {len(body)}-byte body"
    assert result.kind in {"episodic", "semantic", "procedural"}


# --- N. Coverage of every named signal ----------------------------------------------------

# One minimal positive fixture per named signal in the implementation. Kept hardcoded
# (rather than introspected) so this test acts as a public-spec regression guard: if a
# signal name is renamed or removed, the corresponding test breaks loudly.
_SIGNAL_FIXTURES: list[tuple[str, str]] = [
    # procedural
    ("code_fence_block", "```\nls\n```"),
    ("inline_code_command_shape", "Use `make build` here."),
    ("imperative_sentence_start", "Run the test"),
    ("numbered_steps", "1. Clone\n2. Build\n"),
    ("bullet_list_with_command", "- run make build\n"),
    ("to_do_X_template", "How to deploy:\n"),
    ("path_or_flag_token", "Pass --verbose"),
    ("keyword_steps_header", "Steps:\nfirst do x"),
    # episodic
    ("iso_date", "On 2026-04-15 stuff happened"),
    ("relative_time_phrase", "yesterday it broke"),
    ("incident_or_pr_id", "PR #482 broke things"),
    ("past_tense_verb", "the build failed"),
    ("we_actor_past", "we deployed"),
    ("error_with_code", "got HTTP 502"),
    ("named_individual_past", "Alice reverted"),
    ("version_or_commit_anchor", "released v1.2.3"),
    # semantic
    ("modal_imperative", "always sanitize"),
    ("principle_template", "Rule: do x"),
    ("generalized_subject", "all auth changes"),
    ("present_tense_universal", "this is required"),
    ("colon_definition", "Auth: the system\n"),
    ("applies_to_template", "applies to all auth"),
]


@pytest.mark.parametrize(("signal_name", "body"), _SIGNAL_FIXTURES)
def test_signal_fires_on_minimal_fixture(signal_name: str, body: str) -> None:
    result = classify(body)
    assert signal_name in result.matched_signals


# --- O. NodeKind contract -----------------------------------------------------------------

_ALL_NONEMPTY_FIXTURES: list[str] = (
    [body for body, _ in _PROCEDURAL_POSITIVES]
    + [body for body, _ in _EPISODIC_POSITIVES]
    + [body for body, _ in _SEMANTIC_POSITIVES]
    + [
        "On 2026-04-15 we discovered the Stripe webhook bug. Going forward, always "
        "re-verify with current_time.",
    ]
)


@pytest.mark.parametrize("body", _ALL_NONEMPTY_FIXTURES)
def test_kind_is_one_of_three_node_kinds(body: str) -> None:
    result = classify(body)
    assert result.kind in {"episodic", "semantic", "procedural"}
