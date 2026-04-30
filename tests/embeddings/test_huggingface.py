"""Tests for `HuggingFaceProvider` using `httpx.MockTransport`.

Every test injects a mock transport via the private ``_client`` parameter so
the real HuggingFace Inference API is never contacted.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

import httpx
import pytest

from context_mesh.embeddings import EmbeddingProvider, HuggingFaceProvider

if TYPE_CHECKING:
    from collections.abc import Callable


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_default_construction_uses_minilm_l6_v2() -> None:
    provider = HuggingFaceProvider()
    assert provider.model_id == "sentence-transformers/all-MiniLM-L6-v2"
    assert provider.dimensions == 384
    assert provider.api_key_env == "HF_API_KEY"
    assert provider.timeout_seconds == 30.0


def test_name_property_strips_org_prefix() -> None:
    assert HuggingFaceProvider().name == "huggingface/all-MiniLM-L6-v2"
    custom = HuggingFaceProvider(model_id="BAAI/bge-small-en")
    assert custom.name == "huggingface/bge-small-en"


def test_provider_is_frozen_and_protocol_conformant() -> None:
    provider = HuggingFaceProvider()
    assert isinstance(provider, EmbeddingProvider)
    with pytest.raises(dataclasses.FrozenInstanceError):
        provider.dimensions = 768  # type: ignore[misc]


def test_explicit_api_key_used() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json=[0.0] * 384)

    provider = HuggingFaceProvider(api_key="explicit-key")
    with _client(handler) as client:
        provider.embed_text("hello", _client=client)

    assert captured["auth"] == "Bearer explicit-key"


def test_env_var_api_key_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HF_API_KEY", "env-key")
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json=[0.0] * 384)

    provider = HuggingFaceProvider()
    with _client(handler) as client:
        provider.embed_text("hello", _client=client)

    assert captured["auth"] == "Bearer env-key"


def test_missing_api_key_raises_on_first_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HF_API_KEY", raising=False)

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        return httpx.Response(200, json=[0.0] * 384)

    provider = HuggingFaceProvider()
    with _client(handler) as client, pytest.raises(RuntimeError, match="HF_API_KEY"):
        provider.embed_text("hello", _client=client)


def test_explicit_api_key_overrides_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HF_API_KEY", "env-key")
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json=[0.0] * 384)

    provider = HuggingFaceProvider(api_key="explicit-key")
    with _client(handler) as client:
        provider.embed_text("hello", _client=client)

    assert captured["auth"] == "Bearer explicit-key"


def test_embed_text_sends_correct_request() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["body"] = request.read()
        return httpx.Response(200, json=[0.0] * 384)

    provider = HuggingFaceProvider(api_key="k")
    with _client(handler) as client:
        provider.embed_text("hello", _client=client)

    assert captured["method"] == "POST"
    assert isinstance(captured["url"], str)
    assert captured["url"].endswith("/sentence-transformers/all-MiniLM-L6-v2")
    import json as _json

    assert _json.loads(captured["body"]) == {"inputs": "hello"}  # type: ignore[arg-type]


def test_embed_text_parses_flat_array_response() -> None:
    expected = [0.1] * 384

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=expected)

    provider = HuggingFaceProvider(api_key="k")
    with _client(handler) as client:
        vec = provider.embed_text("hello", _client=client)

    assert vec == expected
    assert len(vec) == 384


def test_embed_text_parses_nested_array_response() -> None:
    inner = [0.2] * 384

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[inner])

    provider = HuggingFaceProvider(api_key="k")
    with _client(handler) as client:
        vec = provider.embed_text("hello", _client=client)

    assert vec == inner


def test_embed_text_validates_dimensions() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[0.0] * 100)

    provider = HuggingFaceProvider(api_key="k")
    with _client(handler) as client, pytest.raises(ValueError, match="100-dim"):
        provider.embed_text("hello", _client=client)


def test_embed_text_returns_list_of_floats() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[1] * 384)

    provider = HuggingFaceProvider(api_key="k")
    with _client(handler) as client:
        vec = provider.embed_text("hello", _client=client)

    assert all(isinstance(v, float) for v in vec)
    assert vec == [1.0] * 384


def test_embed_batch_sends_array_input() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read()
        return httpx.Response(200, json=[[0.0] * 384, [0.0] * 384])

    provider = HuggingFaceProvider(api_key="k")
    with _client(handler) as client:
        provider.embed_batch(["a", "b"], _client=client)

    import json as _json

    assert _json.loads(captured["body"]) == {"inputs": ["a", "b"]}  # type: ignore[arg-type]


def test_embed_batch_parses_response() -> None:
    response_data = [[0.1] * 384, [0.2] * 384]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response_data)

    provider = HuggingFaceProvider(api_key="k")
    with _client(handler) as client:
        vectors = provider.embed_batch(["a", "b"], _client=client)

    assert vectors == response_data
    assert all(isinstance(v, float) for row in vectors for v in row)


def test_embed_batch_empty_list_short_circuits() -> None:
    counter = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        counter["calls"] += 1
        return httpx.Response(200, json=[])

    provider = HuggingFaceProvider(api_key="k")
    with _client(handler) as client:
        result = provider.embed_batch([], _client=client)

    assert result == []
    assert counter["calls"] == 0


def test_401_raises_runtime_error_with_clear_message() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid token"})

    provider = HuggingFaceProvider(api_key="secret-do-not-leak")
    with _client(handler) as client, pytest.raises(RuntimeError) as excinfo:
        provider.embed_text("hello", _client=client)

    message = str(excinfo.value)
    assert "authentication failed" in message
    assert "401" in message
    assert "secret-do-not-leak" not in message


def test_429_raises_runtime_error_with_retry_after_hint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": "12"}, json={"error": "too many"})

    provider = HuggingFaceProvider(api_key="k")
    with _client(handler) as client, pytest.raises(RuntimeError) as excinfo:
        provider.embed_text("hello", _client=client)

    message = str(excinfo.value)
    assert "rate limit" in message
    assert "12" in message


def test_timeout_raises_runtime_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow")

    provider = HuggingFaceProvider(api_key="k")
    with _client(handler) as client, pytest.raises(RuntimeError) as excinfo:
        provider.embed_text("hello", _client=client)

    message = str(excinfo.value)
    assert "timed out" in message
    assert "30" in message
