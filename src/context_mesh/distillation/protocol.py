"""Distiller Protocol + DistillerCandidate dataclass — the contract every backend satisfies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from context_mesh.api.types import NodeKind
    from context_mesh.distillation.redaction import Finding


@dataclass(frozen=True, slots=True)
class DistillerCandidate:
    body: str
    headline: str
    kind: NodeKind
    tags: tuple[str, ...] = ()
    decisions: tuple[str, ...] = ()
    failed_approaches: tuple[str, ...] = ()
    error_signatures: tuple[str, ...] = ()
    file_paths: tuple[str, ...] = ()
    importance: float = 0.5
    confidence: float = 0.7
    redaction_findings: tuple[Finding, ...] = ()


@runtime_checkable
class Distiller(Protocol):
    @property
    def name(self) -> str: ...

    def distill(self, session_text: str) -> list[DistillerCandidate]: ...


__all__ = ["Distiller", "DistillerCandidate"]
