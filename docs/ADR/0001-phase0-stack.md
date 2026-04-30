# ADR 0001: Phase 0 Stack and Foundation Decisions

- **Status:** Accepted (2026-04)
- **Deciders:** Project lead
- **Scope:** Phase 0 of the `context-mesh` roadmap — project scaffolding, dependency baseline, lint/format/type/test tooling, layout, CI shape, and the conventions every later phase inherits.

---

## Context

`context-mesh` is planning-complete and code-not-yet-started. Phase 0 establishes the foundation: a Python package skeleton, dependency contract, tool chain, repository conventions, and CI baseline that every later phase builds on. The choices below are locked for v1.0 — changing them later carries migration cost — so they are recorded here with the rationale that produced them.

This ADR captures **20 locked decisions** in one place. Each later ADR will be narrower in scope.

---

## Decision

The 20 locked Phase 0 decisions, with a one-to-three-line rationale per decision.

### 1. Build/dependency manager: `uv`

`uv` (Astral, now under OpenAI) is the fastest Python project manager and resolver in production. Native support for `[dependency-groups]`, deterministic locking, and reproducible interpreter pinning via `.python-version`. No reason to start a 2026 project on Poetry, pip-tools, or Hatch's environment manager when `uv` covers all three at higher quality.

### 2. Python floor: `>=3.11`

Python 3.11 added `tomllib` to stdlib, `Self` type, exception groups, and >25% perf improvements over 3.10. 3.10 reaches end-of-life in October 2026; starting a fresh project tied to it is wasteful.

### 3. Lint + format: `ruff`

A single Rust-based tool replaces flake8, isort, pyupgrade, and black. Sub-second runs that will hold even at v1.0 size. One configuration block, one CI step, one developer-installed tool.

### 4. Type checker: `mypy --strict`

Strict mode from day one is cheaper than retrofitting types later. The codebase will be small enough during Phase 0–2 that strict mode imposes near-zero friction. Pyright was considered (faster) but mypy's `--strict` ergonomics and ecosystem stability win for a library shipping type stubs to users.

### 5. CLI framework: `Typer`

Typer is built on Click, supports type-hint-driven argument parsing, and integrates with `rich` for help text. Click was the alternative; Typer's typing alignment with the rest of the codebase (mypy-strict) is the deciding factor.

### 6. Logging: `structlog`

Structured logging is non-negotiable for a system whose `audit` table is the source of truth for retrieval quality measurement. Stdlib `logging` requires extensive configuration to produce structured output; `structlog` does it natively, integrates with stdlib for library callers, and renders pretty for CLI use.

### 7. Migrations engine: roll-our-own

Numbered SQL files in `src/context_mesh/storage/migrations/`, applied in order by a thin runner (Unit 2). Alembic is overkill for a v1 schema with one declarative SQL file. yoyo-migrations adds a dependency for what is twenty lines of code. Roll-our-own keeps the dependency footprint minimal and the schema observable as plain SQL.

### 8. CI: GitHub Actions, Linux, Python 3.11/3.12/3.13 matrix

GitHub Actions is the de-facto standard, free for public repos, and the contributor-onboarding tax is zero. Linux-only because `sqlite-vec` extension loading on macOS/Windows runners requires per-platform binary handling that Phase 0 does not need to solve. Multi-version matrix because we promise Python 3.11+ and want immediate signal when a downstream change breaks 3.13.

### 9. `init` scope: minimal

`context-mesh init` (Unit 4) creates exactly the directory, schema, config, and gitignore lines needed to operate. Anything beyond — example data, sample configs, tutorials — comes from explicit `context-mesh examples` later. A minimal init has fewer failure modes and faster first-run.

### 10. Phase 0 tests: at least one real test

Phase 0 ships with a working pytest invocation, the `in_memory_db` fixture, and at least one assertion that exercises the foundation (validated in Unit 2). A skeleton with zero assertions is a configuration test, not a verification test.

### 11. `sqlite-vec`: pinned `==0.1.9`

`sqlite-vec` is pre-1.0 (latest 0.1.9 as of April 2026) with active fixes. Floating an upper bound across pre-1.0 minor versions has caused breakage in adjacent projects. We pin exact, bump deliberately on review. Once `sqlite-vec` reaches 1.0 we move to a `>=1.0,<2.0` range.

### 12. Layout: `src/context_mesh/` (NOT flat)

src-layout prevents the test suite from importing the working directory's `context_mesh/` instead of the installed package, the standard accidental-success pattern in flat-layout projects. The Python Packaging Authority recommends src-layout for libraries; we follow that.

### 13. Dev deps: `[dependency-groups]` (NOT `[project.optional-dependencies]`)

PEP 735 dependency groups are the uv-native, modern way to declare dev tooling. They do not pollute the published wheel's metadata, do not require an `extras` keyword on install, and are the convention `uv sync` is built around. `[project.optional-dependencies]` is for runtime extras (e.g., `context-mesh[postgres]`), not dev tooling.

### 14. Version ranges: default ranges, strict pin only for `sqlite-vec`

Default `>=X.Y,<X+1.0` ranges for stable libraries (Typer, structlog) — small enough to catch breakage, large enough to absorb patch fixes without manual bumps. `sqlite-vec` is the documented exception (Decision 11).

### 15. Lockfile: commit `uv.lock`

`uv.lock` is committed so contributors and CI get byte-identical resolutions. The library-vs-application debate is dated; modern uv-managed libraries lock for development and rely on `pyproject.toml` ranges for downstream consumers.

### 16. Initial schema: single `0001_initial_schema.sql`

One file containing every table from `docs/SCHEMA.md`. Splitting the v1 schema across multiple files implies an ordering or evolution that does not exist yet. Migration 0002 onward will be incremental. Unit 2 owns this file.

### 17. Audit API skeleton

`audit.log()` exists as a stable callsite from Phase 0. Implementation in Unit 3. Defining the callsite shape now means later phases can call it without conditional checks.

### 18. Pre-commit: ruff + hygiene only; mypy/pytest in CI

Pre-commit must stay sub-second to avoid being disabled. ruff lint, ruff format, trailing-whitespace, end-of-file-fixer fit. mypy and pytest belong in CI where slower runs are acceptable. Unit 5 owns the `.pre-commit-config.yaml` file.

### 19. Commit format: Conventional Commits, no enforcement tooling

Conventional Commits give a clean changelog story without a tool gating PRs. Commitlint adds noise for a small contributor base. We follow the convention; we do not enforce it.

### 20. PR shape: one PR `phase 0: foundation`, ~6-8 logical commits, rebase-merge

Phase 0 is a single coherent change. Splitting across PRs invites partial states. Six to eight commits give reviewers a logical reading order; rebase-merge keeps history linear.

---

## Consequences

**Positive:**
- A contributor running `uv sync && uv run pytest` reproduces CI exactly.
- Strict typing from day one catches integration errors before runtime.
- Single-tool lint/format keeps onboarding short.
- src-layout closes off module-shadowing bugs.
- Pinning `sqlite-vec` removes a class of sudden breakage.

**Negative / accepted trade-offs:**
- `uv` is newer than pip+venv; contributors unfamiliar pay a one-page learning cost. Mitigated by a `CONTRIBUTING.md` snippet (Unit 5).
- `mypy --strict` slows local iteration when the type model is wrong. Acceptable cost for the gain.
- Linux-only CI in Phase 0 means we discover Windows/macOS `sqlite-vec` issues later. Acceptable.
- Roll-our-own migration runner means we own a small chunk of code that Alembic users would not own.

---

## Alternatives Considered

- **Poetry vs uv** — Poetry is mature but slower with its own less-interoperable lockfile format. uv wins on speed and tooling alignment.
- **Pyright vs mypy** — Pyright is faster with better inference. mypy's strict-mode UX, plugin ecosystem, and broader typing-PEP coverage tip the balance for a library project. Switchable later.
- **Black + isort + flake8 vs ruff** — Three tools, three configs, three CI steps vs one. ruff's format is now a near-drop-in for Black.
- **Flat layout vs src-layout** — Flat allows accidental import of working-directory copy, masking install issues. src-layout closes the door.
- **Alembic vs roll-our-own migrations** — Alembic is overpowered for our schema and adds a dependency. Adoptable post-v1.0 if needed.

---

## References

- `planning/ROADMAP.md` (Phase 0 section) — exit criteria.
- `docs/SCHEMA.md` — what `0001_initial_schema.sql` (Unit 2) will encode.
- `CLAUDE.md` (Section 8) — operating principles.
- PEP 735 — dependency groups.
- Astral `uv` documentation — project layout, lockfile semantics.
- `sqlite-vec` PyPI release notes — pre-1.0 status confirmation.
