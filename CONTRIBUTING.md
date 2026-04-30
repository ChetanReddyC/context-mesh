# Contributing to context-mesh

Thanks for considering a contribution. This project takes quality seriously, so please read this document before opening a PR.

---

## Before You Start

Read these in order:

1. `README.md` — what the project is.
2. `docs/ARCHITECTURE.md` — how it's structured.
3. `docs/DESIGN_PRINCIPLES.md` — the values guiding every decision.

If your contribution doesn't fit within the project's current direction, open an issue first to discuss before writing code.

---

## Local Development Setup

This project uses [`uv`](https://docs.astral.sh/uv/) for dependency and Python management. CI runs the same commands you run locally — no surprises.

### One-time setup

```bash
# Install uv if you don't have it
curl -LsSf https://astral.sh/uv/install.sh | sh   # macOS / Linux
# Windows: powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# Sync dependencies (runtime + dev) into .venv
uv sync --all-groups

# Install pre-commit hooks (uses uvx so no project dependency added)
uvx pre-commit install
```

### Running quality checks locally

These four commands are exactly what CI runs. If they pass locally, CI will pass.

```bash
uv run ruff check src tests          # lint
uv run ruff format --check src tests # format check
uv run mypy                          # type check (strict)
uv run pytest                        # tests
```

To auto-fix lint and format issues:

```bash
uv run ruff check --fix src tests
uv run ruff format src tests
```

### Pre-commit hooks

Pre-commit runs ruff (lint + format) and standard hygiene hooks (trailing whitespace, end-of-file fixer, YAML/TOML validity, merge-conflict markers, large-file check) on every `git commit`. mypy and pytest run only in CI to keep commits fast.

If a hook auto-fixes a file, the commit is rejected with the fixes applied to the working tree — re-`git add` and re-commit.

To run hooks against the entire repo on demand:

```bash
uvx pre-commit run --all-files
```

### Commit conventions

Use [Conventional Commits](https://www.conventionalcommits.org/) format: `type(scope): subject` (≤72 chars, imperative mood). Allowed types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `build`, `ci`. The convention is followed by hand; no commit-message linter is enforced.

Never include AI authorship trailers (e.g., `Co-Authored-By: Claude`).

---

## How To Contribute

### 1. Reporting Bugs

Open a GitHub issue. Include:

- What you tried to do.
- What you expected to happen.
- What actually happened.
- Reproduction steps (smallest case that reproduces the bug).
- Your environment (OS, Python version, `context-mesh` version).

### 2. Suggesting Features

Open a GitHub issue tagged `proposal`. Include:

- The problem you're trying to solve.
- The proposed solution.
- Alternatives you considered.
- Why this fits the project's scope and design principles.

We are intentionally conservative with features. A proposal is an invitation to discuss, not a commitment to build.

### 3. Writing Documentation

Documentation contributions are welcomed and reviewed at the same bar as code:

- Clear, concrete writing.
- Accurate to current behavior.
- No marketing fluff.

### 4. Submitting Code

Steps:

1. Open or claim an existing issue. Don't write code without alignment on direction.
2. Fork the repository.
3. Create a branch from `main` named after the issue (e.g., `fix-123-search-pagination`).
4. Write code following the standards below.
5. Write or update tests.
6. Update relevant documentation.
7. Open a pull request against `main`.

---

## Code Standards

### Python Style

- **Formatter:** `ruff format`. CI fails on unformatted code.
- **Linter:** `ruff check` with the project's `pyproject.toml` config.
- **Type hints:** required on all public APIs and most internal code.
- **Type checker:** `mypy --strict` on `src/`. Must pass.

### Naming

- Modules: `snake_case`.
- Classes: `PascalCase`.
- Functions and variables: `snake_case`.
- Constants: `SCREAMING_SNAKE_CASE`.

### Comments

- Default to none. Code should be readable on its own.
- Add a comment ONLY when the WHY is non-obvious — a hidden constraint, a subtle invariant, a workaround for a specific bug.
- Never explain WHAT the code does — let identifiers speak for themselves.
- Never reference the current task, fix, or PR ("used by X", "added for Y") — that belongs in the PR description.

### Tests

- Every new feature ships with tests.
- Every bug fix ships with a regression test.
- Tests live under `tests/` mirroring the `src/` structure.
- Test names describe the behavior being verified, not the function being tested.

### Commits

- Use imperative mood: "Add X" not "Added X."
- Keep messages crisp; one-line subject + optional body.
- Reference issue numbers in the body, not the subject.
- Squash trivial commits before opening the PR.

**Forbidden in commits:**
- "Co-Authored-By: Claude" or any AI authorship attribution.
- "WIP" commits left in the final PR (squash before opening).
- Commits that only fix typos in the most recent commit (amend instead).

### Pull Requests

PR descriptions follow this template (also in `.github/PULL_REQUEST_TEMPLATE.md`):

```markdown
## Summary
What does this PR do? One paragraph.

## Why
What problem does it solve? What's the user-visible benefit?

## Approach
How does it solve the problem? Any non-obvious choices?

## Tests
What tests were added/updated?

## Documentation
What docs were updated?

## Checklist
- [ ] Tests pass locally.
- [ ] Type-check passes (`mypy --strict`).
- [ ] Lint passes (`ruff check`).
- [ ] Format is correct (`ruff format`).
- [ ] Documentation updated.
- [ ] No AI attribution in commits.
- [ ] Scope is matched (no unrelated changes).
```

PRs that don't follow the template will be asked to update. PRs that mix unrelated changes will be asked to split.

---

## Code Review

Every PR is reviewed by at least one maintainer (currently Chetan).

Reviewers look for:

1. **Correctness** — does it work, including edge cases?
2. **Tests** — are the tests meaningful and complete?
3. **Style** — does it match the project's patterns?
4. **Scope** — is the change tightly bounded?
5. **Documentation** — are docs updated?
6. **Performance** — does it regress benchmarks?
7. **Security** — does it introduce vulnerabilities or relax safety?

Reviewers may request changes, ask questions, or suggest alternatives. PRs are merged when all comments are resolved and the maintainer approves.

---

## Quality Bar

This project ships at production-grade open-source quality. That means:

- No half-finished features. If a feature isn't complete, don't merge it.
- No flaky tests. If a test is flaky, fix it or delete it.
- No unjustified complexity. If something is hard to understand, simplify or document.
- No silent regressions. Every quality regression is investigated, even if not blocked.

---

## What We Won't Accept

- **Sweeping refactors** that aren't tied to a concrete user-facing benefit.
- **Stylistic changes** that aren't justified by readability or tooling.
- **New dependencies** added without discussion.
- **Backwards-incompatible changes** without major-version bump.
- **AI-generated code** that wasn't read, understood, and verified by a human contributor.
- **Code that violates the design principles** in `docs/DESIGN_PRINCIPLES.md`.

---

## Code Of Conduct

See `CODE_OF_CONDUCT.md`.

The short version: be kind, be specific, be honest. Disagreement is welcome; disrespect isn't.

---

## License

By contributing, you agree your contributions are licensed under the MIT License (see `LICENSE.md`).

---

## Questions

Open a GitHub issue or start a discussion. We respond, sometimes slowly, always honestly.
