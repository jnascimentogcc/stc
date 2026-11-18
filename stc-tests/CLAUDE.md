# CLAUDE.md (stc-tests)

Guidance for work inside `stc-tests/`, the test application that sits beside STC in this monorepo.
The root `CLAUDE.md` still applies (everything in English, no private data, no secrets).

## Separation

- `stc-tests` and `src/stc` are separate programs. They share **only** the contract in `contracts/`
  (JSON Schema and fixtures), read as files. Neither imports the other; `tool/tests/test_independence.py`
  and its twin check this.
- The tool reads what `stc spec` wrote (`--specs`, default `STC_SPECS`) and writes its work to
  `--output` (default `STC_TESTS`). It refuses a major contract version it does not know.
- Design and decisions: `docs/proposal-test-solution.md`. Requirements: `docs/functional-spec.md` §4.8.

## Layout

- `tool/` — Python (uv, Typer, pytest, ruff, mypy strict): reader, drift state, minimal LLM client, scenario writer; later mapping and more agents.
- `spike/` — Java experiment that validated the stack (Cucumber, REST Assured, JUnit 5). Not the template.
- `template/` — Java repository the client runs in its own CI: sample scenarios and steps, the scenario linter, `specs/` for the sidecars, and its own copy of `contract/` (kept identical to `contracts/` by a test in `tool/`). `mvn test` needs no target; `mvn -Pacceptance test` needs `TARGET_URL`.

## Commands (from `stc-tests/tool/`)

```bash
uv sync
uv run stc-tests check --all --specs DIR
uv run stc-tests ingest APP --specs DIR --output DIR [--dry-run]
uv run stc-tests scenarios APP --catalog PATH [--force] [--dry-run]   # needs STC_TESTS_LLM_MODEL
uv run stc-tests review|approve|reject APP ...   # human approval of drafts (--by is a person)
uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest
```

Never execute the legacy application, COBOL in particular; never send real code to an LLM.
