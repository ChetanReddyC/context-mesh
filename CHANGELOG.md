# Changelog

All notable changes to `context-mesh` are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added — Phase 0: Foundation
- `pyproject.toml` with src-layout, `uv`-managed deps, ruff/mypy/pytest config (ADR-0001).
- ADR-0001 documenting 20 locked Phase 0 decisions.
- `SqliteVecBackend`, migration runner, and `0001_initial_schema.sql` (v1 schema).
- `structlog`-backed logging configuration and `audit.log()` skeleton.
- `context-mesh init` CLI command (creates `.context-mesh/memory.db` and `config.toml`).
- GitHub Actions CI: ruff lint + format, mypy strict, pytest, wheel build, wheel-install smoke; matrix Python 3.11 / 3.12 / 3.13 on Linux.
- Pre-commit hooks: ruff (lint + format) and standard hygiene checks.
- `CONTRIBUTING.md` extended with local development setup and quality-check workflow.

### Added — Phase 1: Core Memory Operations
- `Mesh.add` / `Mesh.get` / `Mesh.list_nodes` / `Mesh.update` / `Mesh.delete` — Node CRUD with audit on every mutation.
- `Mesh.add_edge` / `Mesh.get_edge` / `Mesh.get_edges` / `Mesh.list_edges` / `Mesh.delete_edge` — Edge CRUD with audit.
- `Mesh.set_vector` / `Mesh.get_vector` / `Mesh.has_vector` / `Mesh.delete_vector` — vector storage with atomic two-table coordination, dimension validation, explicit `replace` semantics, and explicit `vec_nodes` cleanup on node deletion.
- `Mesh.make_node` and `Mesh.make_edge` — convenience constructors with auto uuid4, timestamps, and content_hash.
- `MemoryNode` / `MemoryEdge` dataclasses with `to_row` / `from_row` round-trip and `compute_content_hash`.
- Cross-cutting integration tests verifying full lifecycle, audit integrity, and cascade semantics.

---

## [0.0.0] — Project Initialization

- Repository created.
- Documentation framework established under `docs/`.
- `CLAUDE.md` agent onboarding brief written.
- License (MIT) declared.
- Contributing guidelines written.
