"""Embedding provider interface.

Implementations turn text into fixed-dimension float vectors that the storage
layer persists into `vec_nodes` (sqlite-vec) and `vector_meta`. The interface
is a `typing.Protocol` so third-party providers can satisfy it structurally
without inheriting from a base class.

Mesh integration is deferred to P2-Unit 5 (`Mesh.search`). The only consumer
of this interface in P2-Unit 1 is the test suite.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Embeds text into fixed-dimension float vectors.

    Conforming implementations expose two read-only attributes (`name`,
    `dimensions`) and two methods (`embed_text`, `embed_batch`). Vectors
    returned by `embed_text` MUST have length equal to `dimensions`; this is
    re-validated at the `Mesh.set_vector` boundary against the v1 schema's
    fixed 384-dim `vec_nodes.embedding` column.
    """

    @property
    def name(self) -> str:
        """Stable provider identifier persisted with each vector."""
        ...

    @property
    def dimensions(self) -> int:
        """Fixed embedding length produced by `embed_text` / `embed_batch`."""
        ...

    def embed_text(self, text: str) -> list[float]:
        """Embed a single text. Length of the returned list must equal `self.dimensions`."""
        ...

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple texts.

        `output[i]` is the embedding of `texts[i]`. Empty input returns `[]`.
        """
        ...


__all__ = ["EmbeddingProvider"]
