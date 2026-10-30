# Contributing to STC

Thanks for your interest. Issues and pull requests are welcome.

## Ground rules

- **English everywhere**: code, comments, docs, commit messages and UI strings.
- **Requirements have IDs** in [docs/functional-spec.md](docs/functional-spec.md) (IDX-*, CAT-*, QRY-*, CHT-*, MCP-*, XCT-*). Cite them in issues, commits and tests.
- **No private data**: no customer names, real application names, internal paths or URLs, and no secrets. Use the fixtures in `examples/` or generic examples. Catalogs generated from real code stay in their own private repository.
- **One query engine**: the chat API and the MCP server are thin layers on `stc.query.QueryEngine`; do not duplicate search logic.
- **Facts are deterministic, interpretation is the LLM's**: detection, symbols, relations and locations come from static analysis.

## Setup

```bash
uv sync                          # backend, Python >= 3.12
(cd frontend && npm install)     # frontend
```

## Checks

Run them before opening a pull request:

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy
uv run pytest
(cd frontend && npx prettier --check "src/**/*.{ts,html,css}" && npm test -- --no-watch)
```

Database tests are skipped unless `STC_TEST_DB_URL` points to a throwaway pgvector PostgreSQL where the user can create databases. **Never point it at your development database**: the migration tests change the password of the cluster-wide `stc_app` role.

```bash
docker run -d --name stc-test-db -e POSTGRES_PASSWORD=test -p 55432:5432 pgvector/pgvector:pg17
STC_TEST_DB_URL=postgresql://postgres:test@localhost:55432/postgres uv run pytest
```

## Pull requests

- Keep a pull request to one change, with tests for new behavior.
- Add a line to `CHANGELOG.md` under "Unreleased" for user-visible changes.
- Database changes are new forward-only migrations in `src/stc/db/migrations/`; never edit an applied one.

See [CLAUDE.md](CLAUDE.md) for the architecture overview.
