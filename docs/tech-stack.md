# STC — Technology Stack (v0.1)

> Status: initial proposal, dated 2026-10-29. Describes what is **proposed**, not what already exists. The functional requirements are in `docs/functional-spec.md` and the interface rules in `docs/ui-spec.md`. Exact versions are pinned in `pyproject.toml` and `frontend/package.json` (normative source); this document only gives the baseline.

## 1. Overview

```
Root (folder with 100+ applications, mounted read-only)
   │
   ▼
Indexer (CLI + workers) ──► PostgreSQL 17 + pgvector ◄── Query engine (Python library)
   │   │                         ▲  catalogs (index),                 ▲              ▲
   │   │                         │  chunks, symbols, relations        │              │
   │   └─► ./catalogs (markdown versioned in Git)                     │              │
   │                                                                  │              │
   └─► LLM + embeddings (OpenAI at launch)            FastAPI API ────┘          MCP server
                                                            │                    (stdio / HTTP)
                                                  Browser ──┴──► Angular (chat)
```

Cross-cutting principles:

- **A single query engine.** The chat API and the MCP server are thin layers over the same library; neither duplicates search logic (functional spec, §2).
- **Facts are deterministic, interpretation is the LLM's.** Detection of applications, symbols, relations, hashes and location (file/lines) come from code and static analysis. The LLM summarizes, classifies and answers, always with verifiable evidence.
- **Application code is read-only.** It is never modified or executed.

## 2. Backend

| Area | Technology | Notes |
|---|---|---|
| Language | **Python ≥ 3.12** | Strict typing; English identifiers and domain terms |
| API | **FastAPI** + **Uvicorn** | Chat responses streamed over SSE |
| Models and validation | **Pydantic ≥ 2**, **jsonschema** | Structured LLM output validated against a schema before being accepted |
| Code analysis | **tree-sitter** (`tree-sitter` + `tree-sitter-language-pack`) | Chunking by function/class/module and symbol extraction in all supported languages (IDX-08) |
| Secret detection | **detect-secrets** + filename rules | Runs before any other processing (IDX-06, XCT-01) |
| Literal search | **ripgrep** (process) | Exact patterns and text over the current Root (QRY-07) |
| Version history | **git** (CLI, `subprocess`) | Change detection and indexed version per application (LCY-02, LCY-08) |
| MCP | **Official MCP SDK for Python** (`mcp`) | stdio and HTTP (streamable) transports |
| LLM client | **Official OpenAI SDK** (launch) and **official Anthropic SDK** (future), behind an own interface | Provider chosen by configuration (§5) |
| Embeddings client | **OpenAI SDK** (launch) behind an own interface; Voyage or a local model as alternatives | Replaceable provider (§5) |
| Background tasks | **Own workers on PostgreSQL** (`FOR UPDATE SKIP LOCKED`) | No external broker (§8) |
| CLI | `stc` (**Typer**) | `index`, `reindex`, `status`, `catalog`, `evaluate`, `migrate-db`, `serve-mcp` |
| Packaging | **hatchling**, environments with **uv** | `stc` package in `src/` |
| Quality | **pytest**, **ruff**, **mypy**, **httpx** (test client) | See §7 |
| Logs | **structlog** (JSON) | Never logs source code or secrets |

## 3. Data

- **PostgreSQL 17** (`pgvector/pgvector:pg17`) as the only database.
- **pgvector in use**: vectors for code chunks and catalogs (HNSW index). Each vector stores the embedding provider, model and version; changing model implies reindexing.
- **Lexical search**: `tsvector` (text) and **pg_trgm** (identifiers and name fragments). Hybrid search combines vector + lexical with *reciprocal rank fusion* and reranking (§5).
- **Relationship graph in tables** (application → application, with type and evidence) queried with recursive CTEs for transitive dependencies (QRY-03). No graph database is used.
- **Main tables**: `applications`, `files`, `chunks`, `chunk_embeddings`, `symbols`, `relations`, `catalogs`, `catalog_sections`, `catalog_section_embeddings`, `indexing_runs`, `tasks`, `conversations`, `messages`, `feedback` (plus `schema_migrations`). Embeddings are stored at **1536 dimensions** in a separate table per owner; models with a different native size must be configured to output 1536 (OpenAI `text-embedding-3-*` support this), otherwise a new migration and a full reindex are needed.
- **Two database roles**: the schema owner (migrations) and `stc_app` (API and workers, without owner privileges).
- **Numbered SQL migrations** in `src/stc/db/migrations/`, applied by `stc migrate-db` (forward-only, one transaction per migration, checksum-verified, serialized with an advisory lock). The same command creates or updates the `stc_app` role from `STC_APP_PASSWORD` and grants it data access only (no DDL, no access to `schema_migrations`). **Driver**: `psycopg 3` (`binary`, `pool`).
- **Catalogs and global map as markdown** in `./catalogs/<application>.md` and `./catalogs/_global-map.md`, in their own Git repository. It is the source of truth: the database only holds the index. Manual corrections live in marked blocks that regeneration preserves (CAT-04, CAT-05).
- **Volumes**: the Root is mounted read-only; `./catalogs` and `./data` (caches, indexing reports) are writable.

## 4. Indexing pipeline

1. **Detection**: walks the Root, applies exclusions and identifies applications through marker files (`package.json`, `pom.xml`, `*.csproj`, `pyproject.toml`, `go.mod`, …), with manual corrections in `stc.toml` (IDX-02, IDX-03).
2. **Hygiene**: noise exclusions and secret detection; whatever is excluded is recorded without its value.
3. **Decomposition**: tree-sitter produces chunks and symbols; files in languages without a grammar enter as text and are flagged (IDX-04).
4. **Incremental**: SHA-256 per file plus `git diff` between versions; only changed files and affected applications are reprocessed (LCY-01 to LCY-04).
5. **Embeddings**: only for new or changed chunks, in batches.
6. **Relations**: extracted by static analysis and heuristics (URLs and endpoints, queues and topics, database connections, internal libraries, configuration) and then confirmed by the LLM with evidence (CAT-07).
7. **Catalog per application**: LLM with structured output and context assembled from symbols, manifests, schemas and entry points; each statement carries evidence and a confidence level (CAT-01 to CAT-03).
8. **Global map**: built from the catalogs and the relations table.

Applications are processed in parallel, with a limit on concurrency and on requests per minute to the LLM. A failure in one application does not stop the others (LCY-06). The cost estimate before a full indexing (LCY-07) uses the token count of the chunks.

## 5. Artificial intelligence

| Use | Provider | Notes |
|---|---|---|
| Catalog and global map generation | **OpenAI** (launch) or **Anthropic** (Claude), by configuration | Structured output validated against a schema; *prompt caching* and batch API for bulk generation, when the provider offers them |
| Chat | **OpenAI** (launch) or **Anthropic** (Claude), with *tool use* | Same toolset as the MCP; agent loop on the server; streamed responses |
| Code and catalog embeddings | **OpenAI** (launch), behind an interface | Alternatives via configuration: Voyage AI (code model) or a local model |
| Reranking | Disabled at launch; **Voyage AI** (reranker) as a future option | OpenAI has no dedicated reranker; the hybrid list (vector + lexical with RRF) works without it |

- **Launch provider: OpenAI**, for both LLM and embeddings, since it is the only key currently available. **Anthropic** comes in later through configuration alone, without changing the query engine or the pipeline.
- **Provider interface**: the code talks to two own interfaces (`LLMClient` and `EmbeddingsClient`) and never to an SDK directly. Each provider is an adapter that translates *tool use*, structured output and streaming into a common internal format. Providers can be mixed (for example, chat on Anthropic and embeddings on OpenAI).
- **Providers and models are configurable** through environment variables (`STC_LLM_PROVIDER`, `STC_LLM_CATALOG_MODEL`, `STC_LLM_CHAT_MODEL`, `STC_EMBEDDINGS_PROVIDER`, `STC_EMBEDDINGS_MODEL`), never hardcoded.
- **Changing the embeddings provider or model requires reindexing** the vectors (each vector stores provider, model and version), because dimensions and vector space differ. Changing the LLM does not require reindexing, but catalogs should be regenerated if homogeneity is wanted; the provider used is recorded in each catalog (XCT-05).
- **Language of generated output** follows `STC_LANGUAGE` (English by default; XCT-07).
- **What data leaves the infrastructure**: code excerpts and application metadata go to the configured LLM and embeddings providers (OpenAI at launch). Secrets never leave (IDX-06). Per-application exclusions in `stc.toml` (XCT-02).
- See §8 on the option of local models for sensitive applications.

## 6. Frontend (chat)

Follows `docs/ui-spec.md`; this table only fixes the libraries.

| Area | Technology |
|---|---|
| Framework | **Angular** (*standalone* components, **signals**, `OnPush`), TypeScript |
| Styles | **Tailwind CSS** with the UI-spec tokens (`class-variance-authority`, `clsx`, `tailwind-merge`) |
| Components | **SpartanNG** (`brain` and `helm`, on top of `@angular/cdk`) |
| Icons | **ng-icons** with **Lucide** |
| i18n | **Transloco** (`public/i18n/en.json` as the default locale; further locales optional) |
| Typography | **DM Sans** and **IBM Plex Mono** (`@fontsource`) |
| Answer markdown | **marked** + **DOMPurify** (HTML always sanitized) |
| Code in citations | **Shiki** (syntax highlighting) |
| Streaming | **SSE** with `fetch` and `ReadableStream` |
| Build and tests | **Angular CLI / `@angular/build`**, **Vitest** + **jsdom**, **Prettier**, npm |

## 7. Quality and evaluation

- **pytest** with *fixtures* of small applications in several languages (the indexer's test set, with no real customer code).
- **Quality evaluation (XCT-08)**: `stc evaluate` runs a set of reference questions from `evaluation/questions.yaml` (question, expected applications, expected evidence) and measures:
  - search *recall* (the expected evidence appears in the top-k);
  - answer correctness, with an objective criterion whenever possible;
  - rate of answers without evidence.
- The results of each run are stored so that changes to chunking, embeddings, reranking or prompts can be compared.
- **Frontend**: Vitest for components; the chat flow is covered by an end-to-end test with a simulated API.
- **CI**: ruff, mypy, pytest and Vitest on every push.

## 8. Infrastructure and deployment

| Area | Technology |
|---|---|
| Containers | **Docker** + **Docker Compose**. Services: `db`, `migrate`, `api`, `worker`, `mcp`, `web` |
| Backend image | `python:3.12-slim` with `git` and `ripgrep`, unprivileged user, *healthcheck* on `/api/health` |
| Frontend image | Build on Node LTS, served by unprivileged **nginx** (read-only filesystem) |
| MCP | **stdio** transport for local use (Claude Code, Claude Desktop, Cursor) and **HTTP** with a token for shared use |
| Network | API, workers and database on the internal network only; `web` and `mcp` (HTTP) exposed behind the proxy |
| Secrets | `.env` via `env_file` at runtime; never in the repository or in the images |
| Code | Git, GitHub (`jnascimentogcc/stc`), `develop` branch |

The reverse proxy and hosting are yet to be decided (§11).

## 9. Main environment variables

| Variable | Purpose |
|---|---|
| `STC_ROOT` | Folder with the applications (mounted read-only) |
| `STC_CATALOGS`, `STC_DATA` | Folders for the catalogs and for working data |
| `STC_DB_URL`, `STC_DB_MIGRATION_URL` | Connection for the API/workers and for migrations |
| `POSTGRES_PASSWORD`, `STC_APP_PASSWORD` | Database passwords |
| `STC_LLM_PROVIDER` (`openai` by default; `anthropic`), `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` (only when used), `STC_LLM_CATALOG_MODEL`, `STC_LLM_CHAT_MODEL` | LLM |
| `STC_EMBEDDINGS_PROVIDER` (`openai` by default; `voyage`, `local`), `STC_EMBEDDINGS_MODEL`, `VOYAGE_API_KEY` (only when used), `STC_RERANK_MODEL` (empty = no reranking) | Embeddings and reranking |
| `STC_LANGUAGE` | Language of generated catalogs and answers (`en` by default) |
| `STC_CONCURRENCY`, `STC_LLM_REQUESTS_PER_MIN` | Parallelism and rate limit |
| `STC_MCP_TOKEN` | MCP authentication over HTTP |
| `STC_ENVIRONMENT`, `STC_PUBLIC_URL` | Execution mode and public address |

Full, commented template in `.env.example`.

## 10. Security (summary)

Root mounted read-only; secrets excluded before any indexing or external call and never returned in answers; sanitized HTML in answers; MCP over HTTP with a token; unprivileged containers; database role without owner privileges; logs without code or secrets. User authentication and per-application access control are yet to be defined (§11).

## 11. Decisions and open items

- **No external task queue**: PostgreSQL workers are enough for the expected scale; if volume grows, evaluate a broker.
- **No graph database**: relations in tables with recursive CTEs. Revisit if impact queries become slow.
- **Chat authentication**: to be defined. Starting hypothesis: single-user installation behind the proxy; later, cookie-based sessions.
- **Per-application access control**: out of scope in v0.1 (functional spec, §6).
- **Embeddings and LLM provider**: OpenAI at launch; Anthropic to be added once a key is available. Reassess by quality on code, and decide whether sensitive applications require local models or an EU-based provider.
- **Deeper static analysis** (SCIP/LSIF, call graph): candidate for phase 2 to increase relation precision.
- **Hosting and proxy**: to be decided (local machine, own server or cloud).
- **Capacity**: a full indexing has CPU and LLM-call peaks; size it after the pilot with 3–5 applications.
