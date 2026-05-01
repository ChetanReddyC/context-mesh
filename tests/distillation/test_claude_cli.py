"""Tests for `ClaudeCliDistiller` — JSON parsing, redaction, fallback, subprocess shape.

Every test injects fakes for both ``subprocess_runner`` and ``which`` so the real
``claude`` CLI is never invoked. The runner fakes return a ``CompletedProcess`` (or raise)
without spawning a process; the ``which`` fakes return a fixed path or ``None``.
"""

from __future__ import annotations

import logging
import subprocess
from typing import TYPE_CHECKING, Any

import pytest

from context_mesh._logging import configure_logging
from context_mesh.distillation import (
    DEFAULT_PROMPT,
    ClaudeCliDistiller,
    Distiller,
    DistillerCandidate,
    HeuristicDistiller,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    SubprocessRunner = Callable[..., "subprocess.CompletedProcess[str]"]
    WhichFn = Callable[[str], str | None]


# --- Helpers ------------------------------------------------------------------------------


def _ok_runner(
    stdout: str,
    *,
    returncode: int = 0,
    stderr: str = "",
) -> SubprocessRunner:
    def runner(cmd: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=list(cmd), returncode=returncode, stdout=stdout, stderr=stderr
        )

    return runner


def _capturing_runner(
    stdout: str,
    *,
    returncode: int = 0,
    stderr: str = "",
) -> tuple[SubprocessRunner, dict[str, Any]]:
    captured: dict[str, Any] = {}

    def runner(cmd: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["argv"] = list(cmd)
        captured["kwargs"] = dict(kwargs)
        captured["stdin"] = kwargs.get("input")
        return subprocess.CompletedProcess(
            args=list(cmd), returncode=returncode, stdout=stdout, stderr=stderr
        )

    return runner, captured


def _raising_runner(exc: BaseException) -> SubprocessRunner:
    def runner(cmd: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise exc

    return runner


def _always_which(path: str = "/usr/local/bin/claude") -> WhichFn:
    def which(_binary: str) -> str | None:
        return path

    return which


def _never_which() -> WhichFn:
    def which(_binary: str) -> str | None:
        return None

    return which


def _exploding_which() -> WhichFn:
    def which(_binary: str) -> str | None:
        raise AssertionError("which must not be called")

    return which


def _exploding_runner() -> SubprocessRunner:
    def runner(cmd: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise AssertionError("subprocess_runner must not be called")

    return runner


class _SpyFallback:
    """Records calls to ``distill()`` and returns a configurable list of candidates."""

    def __init__(self, candidates: list[DistillerCandidate]) -> None:
        self._candidates = list(candidates)
        self.last_input: str | None = None
        self.call_count: int = 0

    @property
    def name(self) -> str:
        return "spy/v1"

    def distill(self, session_text: str) -> list[DistillerCandidate]:
        self.last_input = session_text
        self.call_count += 1
        return list(self._candidates)


class _ExplodingFallback:
    """Fallback that fails the test if invoked."""

    @property
    def name(self) -> str:
        return "exploding/v1"

    def distill(self, session_text: str) -> list[DistillerCandidate]:
        raise AssertionError(f"fallback must not be called; got {session_text!r}")


_SENTINEL_CANDIDATE: DistillerCandidate = DistillerCandidate(
    body="from-fallback",
    headline="from-fallback-headline",
    kind="semantic",
)


def _candidate_json(
    *,
    body: str = "Body text",
    headline: str = "Headline",
    kind: str = "semantic",
    **extra: Any,
) -> dict[str, Any]:
    obj: dict[str, Any] = {"body": body, "headline": headline, "kind": kind}
    obj.update(extra)
    return obj


def _candidates_payload(*candidates: dict[str, Any]) -> str:
    import json as _json

    return _json.dumps({"candidates": list(candidates)})


# --- A. Construction + protocol conformance ----------------------------------------------


def test_satisfies_distiller_protocol() -> None:
    assert isinstance(ClaudeCliDistiller(), Distiller)


def test_name_is_claude_cli_v1() -> None:
    assert ClaudeCliDistiller().name == "claude-cli/v1"


def test_default_prompt_contains_schema_keywords() -> None:
    for keyword in ("candidates", "episodic", "semantic", "procedural", "STRICT JSON", "<= 120"):
        assert keyword in DEFAULT_PROMPT, f"missing keyword: {keyword!r}"


def test_default_fallback_is_heuristic() -> None:
    distiller = ClaudeCliDistiller()
    assert isinstance(distiller._fallback, HeuristicDistiller)
    assert distiller._fallback.__class__.__name__ == "HeuristicDistiller"


# --- B. Empty input short-circuit --------------------------------------------------------


@pytest.mark.parametrize("text", ["", "   ", "\n\n", "\t"])
def test_distill_empty_returns_empty_list_without_invoking_runner_or_which(text: str) -> None:
    distiller = ClaudeCliDistiller(
        subprocess_runner=_exploding_runner(),
        which=_exploding_which(),
        fallback=_ExplodingFallback(),
    )
    assert distiller.distill(text) == []


# --- C. Happy paths (CLI returns valid JSON) ---------------------------------------------


def test_single_candidate_round_trips() -> None:
    payload = _candidates_payload(
        _candidate_json(
            body="Body text",
            headline="Headline",
            kind="semantic",
            tags=["deploy"],
            decisions=["chose Postgres"],
            importance=0.8,
            confidence=0.9,
        )
    )
    distiller = ClaudeCliDistiller(
        subprocess_runner=_ok_runner(payload),
        which=_always_which(),
        fallback=_ExplodingFallback(),
    )
    out = distiller.distill("session content here")
    assert len(out) == 1
    c = out[0]
    assert c.body == "Body text"
    assert c.headline == "Headline"
    assert c.kind == "semantic"
    assert c.tags == ("deploy",)
    assert c.decisions == ("chose Postgres",)
    assert isinstance(c.tags, tuple)
    assert isinstance(c.decisions, tuple)
    assert c.importance == 0.8
    assert c.confidence == 0.9


def test_multiple_candidates_emitted_in_order() -> None:
    payload = _candidates_payload(
        _candidate_json(headline="first", body="b1"),
        _candidate_json(headline="second", body="b2", kind="episodic"),
        _candidate_json(headline="third", body="b3", kind="procedural"),
    )
    distiller = ClaudeCliDistiller(
        subprocess_runner=_ok_runner(payload),
        which=_always_which(),
        fallback=_ExplodingFallback(),
    )
    out = distiller.distill("session")
    assert [c.headline for c in out] == ["first", "second", "third"]
    assert [c.kind for c in out] == ["semantic", "episodic", "procedural"]


def test_cli_envelope_unwrapped() -> None:
    # CLI's --output-format json wraps the model response in {"result": "...", ...}.
    inner = '{"candidates":[{"body":"X","headline":"H","kind":"semantic"}]}'
    import json as _json

    envelope = _json.dumps({"result": inner, "session_id": "abc", "total_cost_usd": 0.01})
    distiller = ClaudeCliDistiller(
        subprocess_runner=_ok_runner(envelope),
        which=_always_which(),
        fallback=_ExplodingFallback(),
    )
    out = distiller.distill("session")
    assert len(out) == 1
    assert out[0].kind == "semantic"
    assert out[0].body == "X"
    assert out[0].headline == "H"


def test_top_level_json_without_envelope_works() -> None:
    payload = '{"candidates":[{"body":"X","headline":"H","kind":"semantic"}]}'
    distiller = ClaudeCliDistiller(
        subprocess_runner=_ok_runner(payload),
        which=_always_which(),
        fallback=_ExplodingFallback(),
    )
    out = distiller.distill("session")
    assert len(out) == 1
    assert out[0].body == "X"


def test_noise_session_returns_empty_list_no_fallback() -> None:
    distiller = ClaudeCliDistiller(
        subprocess_runner=_ok_runner('{"candidates":[]}'),
        which=_always_which(),
        fallback=_ExplodingFallback(),
    )
    assert distiller.distill("just chatter") == []


# --- D. JSON tolerance -------------------------------------------------------------------


def test_chatty_prelude_tolerated() -> None:
    stdout = 'Sure thing!\n{"candidates":[{"body":"X","headline":"H","kind":"semantic"}]}\nDone.'
    distiller = ClaudeCliDistiller(
        subprocess_runner=_ok_runner(stdout),
        which=_always_which(),
        fallback=_ExplodingFallback(),
    )
    out = distiller.distill("session")
    assert len(out) == 1
    assert out[0].body == "X"


def test_markdown_fence_tolerated() -> None:
    stdout = '```json\n{"candidates":[{"body":"X","headline":"H","kind":"semantic"}]}\n```'
    distiller = ClaudeCliDistiller(
        subprocess_runner=_ok_runner(stdout),
        which=_always_which(),
        fallback=_ExplodingFallback(),
    )
    out = distiller.distill("session")
    assert len(out) == 1
    assert out[0].body == "X"


def test_one_bad_candidate_dropped_others_kept() -> None:
    payload = _candidates_payload(
        _candidate_json(body="b1", headline="h1", kind="semantic"),
        _candidate_json(body="b2", headline="h2", kind="invalid"),
        _candidate_json(body="b3", headline="h3", kind="procedural"),
    )
    distiller = ClaudeCliDistiller(
        subprocess_runner=_ok_runner(payload),
        which=_always_which(),
        fallback=_ExplodingFallback(),
    )
    out = distiller.distill("session")
    assert [c.headline for c in out] == ["h1", "h3"]


def test_candidate_with_empty_body_dropped() -> None:
    payload = _candidates_payload(
        _candidate_json(body="", headline="bad", kind="semantic"),
        _candidate_json(body="ok-body", headline="good", kind="semantic"),
    )
    distiller = ClaudeCliDistiller(
        subprocess_runner=_ok_runner(payload),
        which=_always_which(),
        fallback=_ExplodingFallback(),
    )
    out = distiller.distill("session")
    assert len(out) == 1
    assert out[0].headline == "good"


def test_candidate_with_empty_headline_dropped() -> None:
    payload = _candidates_payload(
        _candidate_json(body="ok", headline="", kind="semantic"),
        _candidate_json(body="ok2", headline="kept", kind="semantic"),
    )
    distiller = ClaudeCliDistiller(
        subprocess_runner=_ok_runner(payload),
        which=_always_which(),
        fallback=_ExplodingFallback(),
    )
    out = distiller.distill("session")
    assert len(out) == 1
    assert out[0].headline == "kept"


@pytest.mark.parametrize("bad_kind", ["garbage", None, "", "Episodic", "EPISODIC", 42])
def test_candidate_with_invalid_kind_dropped(bad_kind: Any) -> None:
    payload = _candidates_payload(
        _candidate_json(body="b", headline="h", kind=bad_kind),
        _candidate_json(body="ok", headline="kept", kind="semantic"),
    )
    distiller = ClaudeCliDistiller(
        subprocess_runner=_ok_runner(payload),
        which=_always_which(),
        fallback=_ExplodingFallback(),
    )
    out = distiller.distill("session")
    assert len(out) == 1
    assert out[0].headline == "kept"


def test_headline_truncated_to_120_chars_with_ellipsis() -> None:
    long_headline = "A" * 200
    payload = _candidates_payload(
        _candidate_json(body="b", headline=long_headline, kind="semantic")
    )
    distiller = ClaudeCliDistiller(
        subprocess_runner=_ok_runner(payload),
        which=_always_which(),
        fallback=_ExplodingFallback(),
    )
    out = distiller.distill("session")
    assert len(out) == 1
    assert len(out[0].headline) <= 120
    assert out[0].headline.endswith("...")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (2.5, 1.0),
        (-1.0, 0.0),
        (0.5, 0.5),
        ("abc", 0.5),
        (None, 0.5),
        (True, 0.5),
    ],
)
def test_importance_clamped_to_unit_range(raw: Any, expected: float) -> None:
    payload = _candidates_payload(
        _candidate_json(body="b", headline="h", kind="semantic", importance=raw)
    )
    distiller = ClaudeCliDistiller(
        subprocess_runner=_ok_runner(payload),
        which=_always_which(),
        fallback=_ExplodingFallback(),
    )
    out = distiller.distill("session")
    assert len(out) == 1
    assert out[0].importance == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (2.5, 1.0),
        (-1.0, 0.0),
        (0.5, 0.5),
        ("abc", 0.7),
        (None, 0.7),
        (True, 0.7),
    ],
)
def test_confidence_clamped_to_unit_range(raw: Any, expected: float) -> None:
    payload = _candidates_payload(
        _candidate_json(body="b", headline="h", kind="semantic", confidence=raw)
    )
    distiller = ClaudeCliDistiller(
        subprocess_runner=_ok_runner(payload),
        which=_always_which(),
        fallback=_ExplodingFallback(),
    )
    out = distiller.distill("session")
    assert len(out) == 1
    assert out[0].confidence == expected


def test_list_fields_coerced_to_tuples() -> None:
    payload = _candidates_payload(
        _candidate_json(body="b", headline="h", kind="semantic", tags=["a", "b"])
    )
    distiller = ClaudeCliDistiller(
        subprocess_runner=_ok_runner(payload),
        which=_always_which(),
        fallback=_ExplodingFallback(),
    )
    out = distiller.distill("session")
    assert out[0].tags == ("a", "b")
    assert isinstance(out[0].tags, tuple)


def test_non_list_field_becomes_empty_tuple() -> None:
    payload = _candidates_payload(
        _candidate_json(body="b", headline="h", kind="semantic", tags="single")
    )
    distiller = ClaudeCliDistiller(
        subprocess_runner=_ok_runner(payload),
        which=_always_which(),
        fallback=_ExplodingFallback(),
    )
    out = distiller.distill("session")
    assert out[0].tags == ()


def test_list_field_drops_non_strings() -> None:
    payload = _candidates_payload(
        _candidate_json(
            body="b",
            headline="h",
            kind="semantic",
            tags=[1, "deploy", None, True, "auth"],
        )
    )
    distiller = ClaudeCliDistiller(
        subprocess_runner=_ok_runner(payload),
        which=_always_which(),
        fallback=_ExplodingFallback(),
    )
    out = distiller.distill("session")
    # Per impl: int/float kept (excluding bool), None dropped, bool dropped.
    assert out[0].tags == ("1", "deploy", "auth")


def test_missing_optional_fields_use_defaults() -> None:
    payload = _candidates_payload(_candidate_json(body="b", headline="h", kind="semantic"))
    distiller = ClaudeCliDistiller(
        subprocess_runner=_ok_runner(payload),
        which=_always_which(),
        fallback=_ExplodingFallback(),
    )
    out = distiller.distill("clean session text with no secrets")
    assert len(out) == 1
    c = out[0]
    assert c.tags == ()
    assert c.decisions == ()
    assert c.failed_approaches == ()
    assert c.error_signatures == ()
    assert c.file_paths == ()
    assert c.importance == 0.5
    assert c.confidence == 0.7
    assert isinstance(c.redaction_findings, tuple)


# --- E. Fallback paths -------------------------------------------------------------------


def test_fallback_when_cli_not_on_path() -> None:
    spy = _SpyFallback([_SENTINEL_CANDIDATE])
    distiller = ClaudeCliDistiller(
        subprocess_runner=_exploding_runner(),
        which=_never_which(),
        fallback=spy,
    )
    out = distiller.distill("original session text")
    assert spy.call_count == 1
    assert spy.last_input == "original session text"
    assert out == [_SENTINEL_CANDIDATE]


def test_fallback_when_cli_returns_nonzero() -> None:
    spy = _SpyFallback([_SENTINEL_CANDIDATE])
    distiller = ClaudeCliDistiller(
        subprocess_runner=_ok_runner("", returncode=2, stderr="claude: ENOENT"),
        which=_always_which(),
        fallback=spy,
    )
    out = distiller.distill("original session text")
    assert spy.call_count == 1
    assert spy.last_input == "original session text"
    assert out == [_SENTINEL_CANDIDATE]


def test_fallback_when_cli_times_out() -> None:
    spy = _SpyFallback([_SENTINEL_CANDIDATE])
    distiller = ClaudeCliDistiller(
        subprocess_runner=_raising_runner(subprocess.TimeoutExpired(cmd=["claude"], timeout=60.0)),
        which=_always_which(),
        fallback=spy,
    )
    out = distiller.distill("session")
    assert spy.call_count == 1
    assert out == [_SENTINEL_CANDIDATE]


def test_fallback_when_cli_spawn_raises_filenotfound() -> None:
    spy = _SpyFallback([_SENTINEL_CANDIDATE])
    distiller = ClaudeCliDistiller(
        subprocess_runner=_raising_runner(FileNotFoundError("no such file")),
        which=_always_which(),
        fallback=spy,
    )
    out = distiller.distill("session")
    assert spy.call_count == 1
    assert out == [_SENTINEL_CANDIDATE]


def test_fallback_when_stdout_unparseable() -> None:
    spy = _SpyFallback([_SENTINEL_CANDIDATE])
    distiller = ClaudeCliDistiller(
        subprocess_runner=_ok_runner("hello world no json here"),
        which=_always_which(),
        fallback=spy,
    )
    out = distiller.distill("session")
    assert spy.call_count == 1
    assert out == [_SENTINEL_CANDIDATE]


def test_fallback_when_top_level_missing_candidates_key() -> None:
    spy = _SpyFallback([_SENTINEL_CANDIDATE])
    distiller = ClaudeCliDistiller(
        subprocess_runner=_ok_runner('{"foo":"bar"}'),
        which=_always_which(),
        fallback=spy,
    )
    out = distiller.distill("session")
    assert spy.call_count == 1
    assert out == [_SENTINEL_CANDIDATE]


def test_fallback_when_stdout_empty() -> None:
    spy = _SpyFallback([_SENTINEL_CANDIDATE])
    distiller = ClaudeCliDistiller(
        subprocess_runner=_ok_runner(""),
        which=_always_which(),
        fallback=spy,
    )
    out = distiller.distill("session")
    assert spy.call_count == 1
    assert out == [_SENTINEL_CANDIDATE]


# --- E2. Structured-warning capture ------------------------------------------------------


@pytest.fixture()
def _structlog_bridged() -> None:
    """Configure structlog so its events route through stdlib logging, so caplog captures them."""
    configure_logging("DEBUG", json_output=True)


@pytest.mark.parametrize(
    ("scenario", "expected_reason"),
    [
        ("cli_not_on_path", "cli_not_on_path"),
        ("cli_timeout", "cli_timeout"),
        ("cli_spawn_failed", "cli_spawn_failed"),
        ("cli_nonzero_exit", "cli_nonzero_exit"),
        ("invalid_json", "invalid_json"),
    ],
)
def test_fallback_logs_structured_warning_for_each_failure_mode(
    scenario: str,
    expected_reason: str,
    _structlog_bridged: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    spy = _SpyFallback([])

    if scenario == "cli_not_on_path":
        runner: SubprocessRunner = _exploding_runner()
        which: WhichFn = _never_which()
    elif scenario == "cli_timeout":
        runner = _raising_runner(subprocess.TimeoutExpired(cmd=["claude"], timeout=60.0))
        which = _always_which()
    elif scenario == "cli_spawn_failed":
        runner = _raising_runner(FileNotFoundError("no such file"))
        which = _always_which()
    elif scenario == "cli_nonzero_exit":
        runner = _ok_runner("", returncode=2, stderr="boom")
        which = _always_which()
    else:
        runner = _ok_runner("not json at all")
        which = _always_which()

    distiller = ClaudeCliDistiller(
        subprocess_runner=runner,
        which=which,
        fallback=spy,
    )

    with caplog.at_level(logging.WARNING, logger="context_mesh.distillation.claude_cli"):
        distiller.distill("session text")

    matching = [
        rec
        for rec in caplog.records
        if rec.levelno == logging.WARNING and "claude_cli.fallback" in rec.getMessage()
    ]
    assert matching, f"no warning record for scenario {scenario!r}; saw {caplog.records}"
    assert any(f'"reason": "{expected_reason}"' in rec.getMessage() for rec in matching), (
        f"no record carried reason={expected_reason!r}; saw {[r.getMessage() for r in matching]}"
    )


# --- F. Redaction (security-critical) ----------------------------------------------------


_SECRET = "sk_live_abcdefghijklmnopqrstuvwx"
_TEXT_WITH_SECRET = f"see key {_SECRET} for payments"


def test_session_text_redacted_before_being_sent_to_cli() -> None:
    payload = _candidates_payload(_candidate_json(body="b", headline="h", kind="semantic"))
    runner, captured = _capturing_runner(payload)
    distiller = ClaudeCliDistiller(
        subprocess_runner=runner,
        which=_always_which(),
        fallback=_ExplodingFallback(),
    )
    distiller.distill(_TEXT_WITH_SECRET)
    stdin = captured["stdin"]
    assert isinstance(stdin, str)
    assert _SECRET not in stdin
    assert "[REDACTED:STRIPE_KEY]" in stdin


def test_redaction_findings_propagate_to_candidates() -> None:
    payload = _candidates_payload(_candidate_json(body="b", headline="h", kind="semantic"))
    distiller = ClaudeCliDistiller(
        subprocess_runner=_ok_runner(payload),
        which=_always_which(),
        fallback=_ExplodingFallback(),
    )
    out = distiller.distill(_TEXT_WITH_SECRET)
    assert len(out) == 1
    assert len(out[0].redaction_findings) >= 1
    assert any(f.kind == "STRIPE_KEY" for f in out[0].redaction_findings)


def test_redaction_findings_shared_across_candidates() -> None:
    payload = _candidates_payload(
        _candidate_json(body="b1", headline="h1", kind="semantic"),
        _candidate_json(body="b2", headline="h2", kind="episodic"),
        _candidate_json(body="b3", headline="h3", kind="procedural"),
    )
    distiller = ClaudeCliDistiller(
        subprocess_runner=_ok_runner(payload),
        which=_always_which(),
        fallback=_ExplodingFallback(),
    )
    out = distiller.distill(_TEXT_WITH_SECRET)
    assert len(out) == 3
    baseline = out[0].redaction_findings
    assert baseline != ()
    for c in out[1:]:
        assert c.redaction_findings == baseline


def test_fallback_receives_original_unredacted_text() -> None:
    spy = _SpyFallback([_SENTINEL_CANDIDATE])
    distiller = ClaudeCliDistiller(
        subprocess_runner=_exploding_runner(),
        which=_never_which(),
        fallback=spy,
    )
    distiller.distill(_TEXT_WITH_SECRET)
    assert spy.last_input == _TEXT_WITH_SECRET


# --- G. Subprocess invocation shape ------------------------------------------------------


def test_command_includes_print_and_json_format_flags() -> None:
    payload = _candidates_payload(_candidate_json(body="b", headline="h", kind="semantic"))
    runner, captured = _capturing_runner(payload)
    distiller = ClaudeCliDistiller(
        subprocess_runner=runner,
        which=_always_which("/custom/path/claude"),
        fallback=_ExplodingFallback(),
    )
    distiller.distill("session")
    argv = captured["argv"]
    assert argv[0] == "/custom/path/claude"
    assert "-p" in argv
    assert "--output-format" in argv
    assert "json" in argv
    fmt_idx = argv.index("--output-format")
    assert argv[fmt_idx + 1] == "json"


def test_extra_args_appended_to_command() -> None:
    payload = _candidates_payload(_candidate_json(body="b", headline="h", kind="semantic"))
    runner, captured = _capturing_runner(payload)
    distiller = ClaudeCliDistiller(
        subprocess_runner=runner,
        which=_always_which(),
        fallback=_ExplodingFallback(),
        extra_args=("--model", "claude-sonnet-4-5"),
    )
    distiller.distill("session")
    argv = captured["argv"]
    json_idx = argv.index("json")
    tail = argv[json_idx + 1 :]
    assert "--model" in tail
    assert "claude-sonnet-4-5" in tail


def test_prompt_sent_via_stdin_not_argv() -> None:
    payload = _candidates_payload(_candidate_json(body="b", headline="h", kind="semantic"))
    runner, captured = _capturing_runner(payload)
    distiller = ClaudeCliDistiller(
        subprocess_runner=runner,
        which=_always_which(),
        fallback=_ExplodingFallback(),
    )
    sentinel = "SENTINEL_DEADBEEF_42"
    distiller.distill(f"begin session {sentinel} end session")
    stdin = captured["stdin"]
    argv = captured["argv"]
    assert isinstance(stdin, str)
    assert sentinel in stdin
    assert all(sentinel not in arg for arg in argv)


def test_prompt_template_substitution_uses_replace_not_format() -> None:
    payload = _candidates_payload(_candidate_json(body="b", headline="h", kind="semantic"))
    runner, captured = _capturing_runner(payload)
    distiller = ClaudeCliDistiller(
        subprocess_runner=runner,
        which=_always_which(),
        fallback=_ExplodingFallback(),
    )
    # Literal braces in session text would crash str.format; .replace() must be used.
    distiller.distill("payload contains {not_a_field} as a literal")
    stdin = captured["stdin"]
    assert isinstance(stdin, str)
    assert "{not_a_field}" in stdin


# --- H. Custom prompt --------------------------------------------------------------------


def test_custom_prompt_template_used() -> None:
    payload = _candidates_payload(_candidate_json(body="b", headline="h", kind="semantic"))
    runner, captured = _capturing_runner(payload)
    distiller = ClaudeCliDistiller(
        subprocess_runner=runner,
        which=_always_which(),
        fallback=_ExplodingFallback(),
        prompt_template="CUSTOM HEAD: {session_text} :TAIL",
    )
    distiller.distill("hello")
    assert captured["stdin"] == "CUSTOM HEAD: hello :TAIL"


# --- I. DistillerCandidate validity ------------------------------------------------------


def test_returned_objects_are_distiller_candidates() -> None:
    payload = _candidates_payload(_candidate_json(body="b", headline="h", kind="semantic"))
    distiller = ClaudeCliDistiller(
        subprocess_runner=_ok_runner(payload),
        which=_always_which(),
        fallback=_ExplodingFallback(),
    )
    out = distiller.distill("session")
    assert len(out) == 1
    assert isinstance(out[0], DistillerCandidate)


# --- J. Timeout passed to runner ---------------------------------------------------------


def test_timeout_seconds_passed_to_runner() -> None:
    payload = _candidates_payload(_candidate_json(body="b", headline="h", kind="semantic"))
    runner, captured = _capturing_runner(payload)
    distiller = ClaudeCliDistiller(
        subprocess_runner=runner,
        which=_always_which(),
        fallback=_ExplodingFallback(),
        timeout_seconds=12.5,
    )
    distiller.distill("session")
    assert captured["kwargs"]["timeout"] == 12.5
