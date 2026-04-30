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

---

## [0.0.0] — Project Initialization

- Repository created.
- Documentation framework established under `docs/` and `planning/`.
- `CLAUDE.md` agent onboarding brief written.
- License (MIT) declared.
- Contributing guidelines written.
