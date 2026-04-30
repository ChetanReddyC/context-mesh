"""Tests for `DeterministicEmbeddingProvider` and `EmbeddingProvider` protocol."""

from __future__ import annotations

import dataclasses
import math

import pytest

from context_mesh.embeddings import DeterministicEmbeddingProvider, EmbeddingProvider


def test_embed_text_returns_list_of_floats() -> None:
    provider = DeterministicEmbeddingProvider()
    vec = provider.embed_text("hello")
    assert isinstance(vec, list)
    assert all(isinstance(v, float) for v in vec)


def test_embed_text_length_matches_dimensions() -> None:
    provider = DeterministicEmbeddingProvider()
    assert provider.dimensions == 384
    assert len(provider.embed_text("hello")) == 384


def test_embed_text_deterministic_across_calls() -> None:
    provider = DeterministicEmbeddingProvider()
    a = provider.embed_text("the quick brown fox")
    b = provider.embed_text("the quick brown fox")
    assert a == b


def test_embed_text_different_texts_different_vectors() -> None:
    provider = DeterministicEmbeddingProvider()
    a = provider.embed_text("alpha")
    b = provider.embed_text("beta")
    assert a != b


def test_embed_text_normalized_unit_length() -> None:
    provider = DeterministicEmbeddingProvider()
    vec = provider.embed_text("normalize me")
    norm = math.sqrt(sum(v * v for v in vec))
    assert math.isclose(norm, 1.0, abs_tol=1e-9)


def test_embed_text_empty_string_works() -> None:
    provider = DeterministicEmbeddingProvider()
    vec = provider.embed_text("")
    assert len(vec) == 384
    norm = math.sqrt(sum(v * v for v in vec))
    assert math.isclose(norm, 1.0, abs_tol=1e-9)


def test_embed_text_unicode_works() -> None:
    provider = DeterministicEmbeddingProvider()
    a = provider.embed_text("héllo 世界")
    b = provider.embed_text("héllo 世界")
    assert a == b
    assert len(a) == 384


def test_embed_batch_returns_list_of_lists() -> None:
    provider = DeterministicEmbeddingProvider()
    out = provider.embed_batch(["a", "b", "c"])
    assert isinstance(out, list)
    assert all(isinstance(v, list) for v in out)
    assert len(out) == 3
    assert all(len(v) == 384 for v in out)


def test_embed_batch_matches_individual_calls() -> None:
    provider = DeterministicEmbeddingProvider()
    texts = ["one", "two", "three"]
    batch = provider.embed_batch(texts)
    individual = [provider.embed_text(t) for t in texts]
    assert batch == individual


def test_embed_batch_empty_list() -> None:
    provider = DeterministicEmbeddingProvider()
    assert provider.embed_batch([]) == []


def test_seed_salt_changes_output() -> None:
    a = DeterministicEmbeddingProvider(seed_salt="alpha").embed_text("text")
    b = DeterministicEmbeddingProvider(seed_salt="beta").embed_text("text")
    assert a != b


def test_protocol_conformance() -> None:
    provider = DeterministicEmbeddingProvider()
    assert isinstance(provider, EmbeddingProvider) is True


def test_custom_dimensions() -> None:
    provider = DeterministicEmbeddingProvider(dimensions=128)
    vec = provider.embed_text("custom")
    assert len(vec) == 128
    norm = math.sqrt(sum(v * v for v in vec))
    assert math.isclose(norm, 1.0, abs_tol=1e-9)


def test_name_is_stable() -> None:
    provider = DeterministicEmbeddingProvider()
    assert provider.name == "deterministic/hash-v1"


def test_provider_is_frozen() -> None:
    provider = DeterministicEmbeddingProvider()
    with pytest.raises(dataclasses.FrozenInstanceError):
        provider.name = "mutated"  # type: ignore[misc]
