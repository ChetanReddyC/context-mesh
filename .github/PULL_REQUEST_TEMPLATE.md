# Pull Request

## Summary

What does this PR do? One paragraph.

## Why

What problem does it solve? What's the user-visible benefit?

## Approach

How does it solve the problem? Any non-obvious choices?

## Tests

What tests were added or updated? How was the change verified?

## Documentation

What docs were updated? Any new docs added?

## Breaking Changes

Any backwards-incompatible changes? If yes, why and what's the migration path?

## Checklist

- [ ] Tests pass locally (`pytest`).
- [ ] Type-check passes (`mypy --strict src/`).
- [ ] Lint passes (`ruff check`).
- [ ] Format is correct (`ruff format --check`).
- [ ] Documentation updated.
- [ ] Eval harness still passes (if retrieval/ranking/distillation touched).
- [ ] Performance benchmarks not regressed (if performance-sensitive change).
- [ ] No AI attribution in commits ("Co-Authored-By: Claude" forbidden).
- [ ] Scope is matched (no unrelated changes in this PR).
- [ ] Linked to issue: # ___

## Reviewer Guidance

(Optional) What should the reviewer focus on? Are there parts of the change that need special scrutiny?
