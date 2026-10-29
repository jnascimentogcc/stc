# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository state

Currently contains **documentation only** in `docs/` (no code, no `pyproject.toml`, no `frontend/`, no tests), plus `LICENSE`. There are no build/lint/test commands to run yet. The stack described below is a **proposal** (v0.1), not something already implemented.

Main branch: `develop` (GitHub remote `jnascimentogcc/stc`).

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
- `docs/ui-spec.md` — global design system (tokens, components, accessibility, i18n, themes). Takes precedence over feature specs on global UI decisions. Exact dependency versions live in `pyproject.toml`/`package.json`, never in the docs.

## Architecture (overview)

- **A single query engine** (Python library). The chat's FastAPI API and the MCP server are thin layers on top of it; never duplicate search logic.
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
- Once code exists, update this file with the real commands (including how to run a single test).
