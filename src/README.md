# `src/` — Source Code

The Python source for `context-mesh` lives here under the `src/context_mesh/` package. The planned module layout is documented below; some modules land in later phases.

---

## Planned Module Structure

Once implementation begins, the structure will be:

```
src/
└── context_mesh/
    ├── __init__.py
    ├── api/                       Public library API (Mesh class, etc.)
    ├── storage/                   Storage backends + migrations
    │   ├── backends/
    │   │   ├── sqlite_vec.py     Default SQLite + sqlite-vec implementation
    │   │   ├── postgres.py       (v1.x — Postgres + pgvector)
    │   │   └── ...
    │   └── migrations/            Numbered SQL migration files
    │       ├── 0001_initial_schema.sql
    │       └── ...
    ├── retrieval/                 Retrieval engine (vector + graph + ranking)
    ├── distillation/              Distillation engine
    │   ├── distillers/
    │   │   ├── claude_cli.py
    │   │   ├── heuristic.py
    │   │   └── ...
    │   ├── redaction.py
    │   └── classifier.py          Memory-kind classification
    ├── embeddings/                Embedding providers
    │   ├── huggingface.py
    │   ├── openai.py
    │   └── ...
    ├── sync/                      Federation logic
    ├── adapters/                  Source adapters
    │   ├── entire.py
    │   ├── agent_memory.py
    │   └── ...
    ├── frontends/                 Tool frontends for agent runtimes
    │   ├── claude_code.py
    │   ├── cursor.py
    │   └── http_server.py
    ├── cli/                       CLI command implementations
    ├── hooks/                     Event hook system
    ├── observability/             Logging, tracing, metrics
    └── config.py                  Configuration loading
```

---

## Implementation Order

Modules are implemented in phase order:

1. **Phase 0:** package skeleton + storage interface + sqlite_vec backend.
2. **Phase 1:** core CRUD on storage.
3. **Phase 2:** embeddings + retrieval.
4. **Phase 3:** distillation.
5. **Phase 4:** CLI + frontends.
6. **Phase 5:** adapters.
7. **Phase 6:** sync.
8. **Phase 7:** lifecycle (decay, promotion, supersession).
9. **Phase 8-9:** polish, release.

---

## Code Standards

All code in this directory follows the standards in `CONTRIBUTING.md`:

- `ruff format` for formatting (no exceptions).
- `mypy --strict` type-checking.
- Tests for every public function.
- Comments only when WHY is non-obvious.

