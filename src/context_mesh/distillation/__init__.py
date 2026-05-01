"""Distillation pipeline: redaction, summarization, and structured-field extraction."""

from __future__ import annotations

from context_mesh.distillation.classifier import ClassificationResult, classify
from context_mesh.distillation.claude_cli import DEFAULT_PROMPT, ClaudeCliDistiller
from context_mesh.distillation.heuristic import HeuristicDistiller
from context_mesh.distillation.protocol import Distiller, DistillerCandidate
from context_mesh.distillation.redaction import Finding, redact

__all__ = [
    "DEFAULT_PROMPT",
    "ClassificationResult",
    "ClaudeCliDistiller",
    "Distiller",
    "DistillerCandidate",
    "Finding",
    "HeuristicDistiller",
    "classify",
    "redact",
]
