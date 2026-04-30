"""Deterministic, network-free embedding provider for tests.

Same input text always produces the same output vector, across runs, machines,
and Python versions. Vectors are L2-normalized to unit length so cosine
similarity in retrieval tests behaves like a real embedding space.

Algorithm:
  1. SHA-256 of (text + seed_salt), encoded UTF-8.
  2. First 8 bytes of the digest become a 64-bit seed for ``random.Random``.
  3. Draw ``dimensions`` floats from ``uniform(-1, 1)``.
  4. L2-normalize (clamping zero-norm to a tiny epsilon for safety).
"""

from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass
from typing import Final

_SEED_BYTES: Final[int] = 8
_NORM_EPSILON: Final[float] = 1e-12


@dataclass(frozen=True, slots=True)
class DeterministicEmbeddingProvider:
    """Hash-seeded pseudo-random embedding provider. Test-only."""

    name: str = "deterministic/hash-v1"
    dimensions: int = 384
    seed_salt: str = ""

    def embed_text(self, text: str) -> list[float]:
        digest = hashlib.sha256((text + self.seed_salt).encode("utf-8")).digest()
        seed = int.from_bytes(digest[:_SEED_BYTES], "big")
        rng = random.Random(seed)
        vec = [rng.uniform(-1.0, 1.0) for _ in range(self.dimensions)]
        norm = math.sqrt(sum(v * v for v in vec))
        if norm < _NORM_EPSILON:
            norm = _NORM_EPSILON
        return [v / norm for v in vec]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_text(t) for t in texts]


__all__ = ["DeterministicEmbeddingProvider"]
