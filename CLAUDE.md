# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository state

Backend skeleton, OpenAI provider adapters (`src/stc/providers/`, tested against mocked HTTP only until `stc check-providers` is run with a real key), the database layer (`src/stc/db/`: migration runner and schema 0001–0006) and indexer stages 1–7 except LLM confirmation of relations (`src/stc/indexer/`: application detection, hygiene/secrets, file registry, tree-sitter chunking and symbols, signals and static relations, embeddings) and per-application catalogs (`src/stc/catalog/`: context, LLM generation with verified evidence, markdown with manual corrections, plus the deterministic global map in `globalmap.py`) and the query engine (`src/stc/query/`: hybrid search, grep, symbols, catalogs, map, dependencies, file reading), the MCP server (`src/stc/mcp_server.py`) the evaluation harness (`src/stc/evaluation/`, `stc evaluate`) and the chat backend (`src/stc/chat/` agent with verified citations, `src/stc/api/chat.py` conversations + SSE): `pyproject.toml`, package `stc` in `src/` (CLI, settings, `/api/health`), tests, `Dockerfile`, `docker-compose.yml` (`db` + `api`), and the chat UI in `frontend/` (Angular: conversations, scope and profile, streamed answers with tool steps, verified citations that open the cited code, feedback; Tailwind with the ui-spec tokens, SpartanNG helm (button, textarea, alert), marked + DOMPurify, Lucide icons, light/dark toggle, Transloco `en`, Vitest). The LLM confirmation of relations, the LLM confirmation of relations, `reindex`/`status`, and the Docker `migrate`/`mcp`/`web` services are **not implemented yet**; the stack in `docs/tech-stack.md` is still a proposal beyond the skeleton.

Main branch: `develop` (GitHub remote `jnascimentogcc/stc`).

## Commands

Backend, from the repo root (uses [uv](https://docs.astral.sh/uv/); Python >= 3.12):

```bash
uv sync                                   # create .venv and install deps (incl. dev group)
uv run pytest                             # all tests
uv run pytest tests/test_cli.py::test_version   # a single test
uv run ruff check . && uv run ruff format --check .
uv run mypy                               # strict
uv run stc --help                         # CLI (`version`, `migrate-db`, `detect`, `index`, `relations`, `embed`, `catalog`, `map`, `query`, `serve-mcp`, `ask`, `evaluate`, `check-providers` work; the rest are stubs)
uv run stc migrate-db                     # needs STC_DB_MIGRATION_URL (+ STC_APP_PASSWORD for the stc_app role)
uv run stc detect --root <dir>            # list detected applications (no DB)
uv run stc index --root <dir> [--dry-run] # detect + hygiene + register + chunk + embed (needs STC_DB_URL unless --dry-run; --skip-embeddings to stop after chunking)
uv run stc relations                      # list application relations with confidence and evidence (--refresh to re-derive)
uv run stc catalog <application>|--all [--dry-run] # generate catalog(s) + refresh _global-map.md in STC_CATALOGS (needs STC_LLM_CATALOG_MODEL)
uv run stc serve-mcp [--transport http]   # MCP server (stdio; http needs STC_MCP_TOKEN)
uv run stc ask "which apps call X?" [--app A] # one question through the chat agent (needs STC_LLM_CHAT_MODEL)
uv run stc evaluate [--mode all]          # reference questions: search recall (free) and answer quality; saved in STC_DATA/evaluations
uv run stc map                            # rebuild the global map from the DB (no LLM)
uv run stc query search "how are invoices paid" # also: apps, catalog, map, deps, catalogs, grep, symbol, read, status (--json)
uv run stc embed --dry-run                # estimate the embedding run (no external call)
uv run uvicorn stc.api.app:app --reload   # API on :8000, health at /api/health
docker compose up --build                 # needs .env (copy .env.example)
```

Never point `STC_TEST_DB_URL` at the development database (e.g. `stc-dev-db`): the migration tests set the password of the cluster-wide `stc_app` role, which would break `STC_DB_URL`. Use a separate container on another port. DB tests are skipped unless `STC_TEST_DB_URL` points to a pgvector PostgreSQL where the user can create databases (each test gets a throwaway database). For example:

```bash
docker run -d --name stc-test-db -e POSTGRES_PASSWORD=test -p 55432:5432 pgvector/pgvector:pg17
STC_TEST_DB_URL=postgresql://postgres:test@localhost:55432/postgres uv run pytest
```

Frontend, from `frontend/` (npm, Angular CLI, Vitest):

```bash
npm install
npm start                                 # dev server on :4200 (proxies /api to :8000; run the API with uvicorn)
npm run build
npm test -- --no-watch                    # all tests
npm test -- --no-watch --include src/app/app.spec.ts   # a single spec file
npx prettier --check "src/**/*.{ts,html,css}"
```

`src/styles/tokens.css` is a copy of the normative tokens in `docs/ui-spec.md` §3.2; keep them in sync. `src/styles/spartan-theme.css` aliases them to the semantic names helm components expect (note: helm `primary` = ui-spec `--accent`; helm `accent` = `--accent-soft`). Dark theme is `[data-theme="dark"]`, toggled by `ThemeService`. Translations live in `public/i18n/<lang>.json`.

SpartanNG components are generated into `src/app/ui/` (config in `components.json`, import alias `@spartan-ng/helm/*`). Add one with `npx ng g @spartan-ng/cli:ui <name> --defaults </dev/null` (it also writes a `.nx` cache, which is git-ignored). Component specs use `provideTestTransloco()` from `src/app/testing.ts`, which loads the real `en.json`, so a missing translation key fails a test. Icons: `@ng-icons/lucide` via `provideIcons`.

## Language

**Everything in the repository is written in English**: code, identifiers, comments, docs, README, commit messages, UI strings, catalogs and API/CLI names. Only the chat conversation with the maintainer is in Portuguese.

The docs in `docs/` are already in English. Output language of catalogs and answers is English by default and configurable via `STC_LANGUAGE` (XCT-07); the UI default locale is `en` (other locales optional).

## What STC is

**STC** comes from Linus Torvalds' quote *"Talk is cheap. Show me the code"*: the system answers with the code as proof (evidence), not with talk.

A system to understand and query 100+ applications **without documentation**, using only their source code. An indexer reads a folder (the "Root"), generates a **catalog** per application and a **global map** of relationships; a chat and an MCP server answer technical/functional questions, always with **evidence** (application, file, lines, code version).

## Open source

The project is **open source under the MIT license** (`LICENSE`, copyright Jorge Nascimento). When creating the `README.md` and other top-level files (CONTRIBUTING, etc.):

- Write for an external audience: what it is, what it is for, how to install and run, how to contribute. Include the origin of the name (quote above).
- Do not include customer names, real application names, internal paths, URLs or private data; use only generic examples or the test applications (fixtures).
- Never version secrets: `.env` stays out of Git, only `.env.example` is committed. Catalogs generated from real code live in their own private Git repository, never in this one.

## Documents (normative sources)

- `docs/functional-spec.md` — functional requirements with IDs (IDX-*, CAT-*, LCY-*, QRY-*, CHT-*, MCP-*, XCT-*) and acceptance criteria. Cite these IDs when implementing/testing.
- `docs/tech-stack.md` — proposed stack, indexing pipeline, data model, environment variables (`STC_*`), decisions and open items.
- `docs/getting-started.md` — step-by-step run of the pipeline on `examples/demo-root/` (two tiny fake applications, safe to send to an LLM provider).
- `docs/ui-spec.md` — global design system (tokens, components, accessibility, i18n, themes). Takes precedence over feature specs on global UI decisions. Exact dependency versions live in `pyproject.toml`/`package.json`, never in the docs.

## Architecture (overview)

- **A single query engine** (`stc.query.QueryEngine`, Python library). The chat's FastAPI API and the MCP server are thin layers on top of it; never duplicate search logic.
- **Facts are deterministic, interpretation is the LLM's**: detection of applications, symbols (tree-sitter), relationships, hashes and location come from code/static analysis; the LLM summarizes and answers with verifiable evidence and a confidence level/gaps.
- **Application code is read-only**: never modified nor executed.
- **Secrets** are excluded (detect-secrets + filename rules) *before* any processing, sending to the LLM/embeddings, or answering; never log code or secrets.
- **Storage**: PostgreSQL 17 + pgvector (hybrid vector + `tsvector`/`pg_trgm`, RRF + reranking); relationship graph in tables with recursive CTEs; background tasks with PostgreSQL-based workers (`FOR UPDATE SKIP LOCKED`), no broker.
- **Markdown catalogs** in `./catalogs/<app>.md` (+ `_global-map.md`), in their own Git repository, are the **source of truth**; the DB only holds the index. Manual corrections live in marked blocks that regeneration preserves (CAT-04/05).
- **Incremental, idempotent indexing** (SHA-256 per file + `git diff`); a failure in one application does not stop the others.
- **Models/LLM configurable via env vars**, never hardcoded. LLM and embeddings sit behind own interfaces (`LLMClient`, `EmbeddingsClient`); the starting provider is **OpenAI**, with Anthropic (and Voyage) as configurable alternatives.
- **Frontend**: Angular (standalone, signals, OnPush) + Tailwind + SpartanNG, i18n with Transloco, always-sanitized markdown (marked + DOMPurify), SSE streaming.

## Planned conventions

- `stc` CLI (Typer): `index`, `reindex`, `status`, `catalog`, `evaluate`, `migrate-db`, `serve-mcp`.
- Python package `stc` in `src/`; numbered SQL migrations in `src/stc/db/migrations/`; two DB roles (schema owner and `stc_app`).
- Quality: pytest, ruff, mypy, Vitest; quality evaluation via `stc evaluate` with `evaluation/questions.yaml`.
