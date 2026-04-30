# `tests/` — Test Suite

Test infrastructure and test cases live here. Tests are written alongside implementation; every phase ships with its own test coverage.

---

## Planned Structure

```
tests/
├── unit/                              Unit tests (per-module)
├── integration/                       Cross-component tests
├── system/                            CLI / end-to-end tests
├── eval/                              Quality / golden case tests
│   ├── golden_cases/                  Retrieval expectations
│   └── golden_distillations/          Distillation expectations
├── perf/                              Performance benchmarks
├── fixtures/                          Shared test data
│   ├── sample_sessions/
│   └── sample_memories/
└── conftest.py                        pytest configuration + shared fixtures
```

---

## Running Tests

Once implemented:

```bash
pytest                                  # all tests
pytest tests/unit                       # only unit tests
pytest tests/integration                # only integration
pytest tests/eval                       # quality / golden cases
pytest tests/perf --benchmark-only      # performance benchmarks
pytest -k "retrieval"                   # tests matching keyword
```

---

## Test Quality Bar

Test quality standards:

- No flaky tests.
- Failures must be specific (point to what broke).
- Unit tests run in milliseconds.
- Integration tests run in seconds.
- System tests run in minutes (max).
- Tests verify behavior, not implementation.

---

## Until Tests Exist

This directory is intentionally empty. Tests are added alongside the code they verify, never as an afterthought.
