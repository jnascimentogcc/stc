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
| Code analysis | **tree-sitter** (`tree-sitter` pinned below 0.26 plus the official per-language grammar wheels) | Chunking by function/class/module and symbol extraction in all supported languages (IDX-08) |
| Secret detection | **detect-secrets** + filename rules | Runs before any other processing (IDX-06, XCT-01) |
| Literal search | **PostgreSQL** (`LIKE`/regex over chunk content, pg_trgm index) | Exact text and patterns over the indexed, non-secret files, per line; no ripgrep process, so secrets cannot be reached and the Root can stay read-only (QRY-07) |
| Version history | **git** (CLI, `subprocess`) | Change detection and indexed version per application (LCY-02, LCY-08) |
| MCP | **Official MCP SDK for Python** (`mcp`) | stdio and HTTP (streamable) transports |
| LLM client | **Official OpenAI SDK** (launch) and **official Anthropic SDK** (future), behind an own interface | Provider chosen by configuration (§5) |
| Embeddings client | **OpenAI SDK** (launch) behind an own interface; Voyage or a local model as alternatives | Replaceable provider (§5) |
| Background tasks | **Own workers on PostgreSQL** (`FOR UPDATE SKIP LOCKED`) | No external broker (§8) |
| CLI | `stc` (**Typer**) | `index`, `reindex`, `status`, `catalog`, `evaluate`, `migrate-db`, `serve-mcp` |
| Packaging | **hatchling**, environments with **uv** | `stc` package in `src/` |
| Quality | **pytest**, **ruff**, **mypy**, **httpx2** (test client) | See §7 |
| Logs | **structlog** (JSON) | Never logs source code or secrets |

## 3. Data

- **PostgreSQL 17** (`pgvector/pgvector:pg17`) as the only database.
- **pgvector in use**: vectors for code chunks and catalogs (HNSW index). Each vector stores the embedding provider, model and version; changing model implies reindexing.
- **Lexical search**: `tsvector` (text) and **pg_trgm** (identifiers and name fragments). Hybrid search combines vector + lexical with *reciprocal rank fusion* and reranking (§5).
- **Query engine** (`src/stc/query/`, class `QueryEngine`; CLI `stc query ...`): the only implementation of the questions the chat and the MCP server will ask. Synchronous methods over one connection; every result carries application, file, lines and the indexed code version (QRY-09), pages are capped at 50 items (`has_more`, `offset`) and an empty result says so in `notes` (QRY-10). `search_code` (QRY-04) fuses three rankings with reciprocal rank fusion (k=60): semantic (embedding distance, only vectors of the configured provider/model), lexical (`tsvector`, OR of the query words and the parts of camelCase/snake_case words) and by name (trigram similarity with the chunk's symbol); it can be restricted by application, language and path prefix/glob, and falls back to lexical only, saying so, when no embedder is available. Semantic search has no similarity threshold: the nearest chunks are always returned and the score and `matched_by` tell how well they match. `search_catalogs` (QRY-05) is full-text over the catalog sections (their embeddings are not generated yet). `grep` (literal or regex, per line, case option) and `find_symbol` (exact or contains, definitions or also usages) are exact (QRY-07). `get_catalog` (QRY-02) reads the markdown file, by section, and flags a catalog older than the indexed code (CHT-11); `get_global_map`, `get_dependencies` (QRY-03: `depends_on` / `used_by` / both, up to 6 hops, cycle-safe recursive CTE, `shared_database` counts both ways, with the evidence of each link), `list_applications` (QRY-01: name, technology = language or library, related to another application) and `status`. `read_file` (QRY-06) reads from the Root, refuses secret and excluded files without opening them and paths outside the application, caps a call at 400 lines (`next_start_line`) and flags a file changed after indexing. Errors are `QueryError` with a stable code (`unknown_application`, `invalid_argument`, `not_found`, `forbidden`, `unavailable`) for MCP-07. The query embedder creates a client per call because the SDK pools connections per event loop.
- **MCP server** (`src/stc/mcp_server.py`, `stc serve-mcp`; official SDK `mcp` 2.x, `MCPServer`): a thin layer over `QueryEngine` with ten tools (`list_applications`, `get_catalog`, `get_global_map`, `get_dependencies`, `search_catalogs`, `search_code`, `grep_code`, `find_symbol`, `read_file`, `indexing_status`; MCP-01), all annotated read-only (MCP-05) and described for a model to pick them (MCP-03), plus two resources, `stc://global-map` and `stc://catalog/{+application}` (MCP-02). Server instructions tell the client how to combine them and to admit when nothing was found. Results are structured JSON; every location also has a ready-to-cite `ref` (`application/file:start-end`) and the code version (MCP-04). Lists are paginated and capped by the engine, the global map text is cut at 60,000 characters (MCP-06). Engine errors become tool errors starting with the stable code (`forbidden: ...`, `unknown_application: ...`; MCP-07). Each call opens its own database connection in a worker thread; if the query cannot be embedded (no key, provider down) `search_code` degrades to lexical and says so. Transports: **stdio** for local clients, and **streamable HTTP** at `/mcp` (`--transport http`) behind a mandatory bearer token (`STC_MCP_TOKEN`, constant-time comparison; the server refuses to start without it). Bound to `STC_MCP_HOST` (127.0.0.1 by default) and `STC_MCP_PORT` (8765); on a non-loopback host DNS-rebinding protection is turned off because the token is the protection (put TLS in front, §8).
- **Chat** (`src/stc/chat/`, `src/stc/api/chat.py`; CLI `stc ask`): an agent loop (CHT-02) over the **same tools as the MCP server** (it calls the in-process `MCPServer`, so names, schemas, descriptions, scope and errors cannot drift), with at most 8 model turns; the last one is forced without tools and told to say what it could not establish. The system prompt fixes the rules: answer only from tool results, cite `application/file:start-end` exactly as given, separate proven from inferred from unknown (CHT-04), flag outdated catalogs and stale files (CHT-11), write for the conversation profile, `technical` or `functional` (CHT-07), in `STC_LANGUAGE`. After the answer, every `path:lines` citation is **checked against what the tools returned in that turn**: it is verified only if a result contained that file with a range covering those lines (including references inside catalog text); unverified ones are reported in `warnings` rather than dropped (CHT-03). A conversation can be limited to some applications (CHT-06): the `QueryEngine` takes a `scope`, and every listing, search, read, catalog and dependency call outside it is refused (`forbidden`); the global map is removed in a scoped conversation because it spans every application, and relations of in-scope applications may still name outside ones. Only the question and the final answer (with citations) are stored, not the tool traces; the last 12 messages are the context of a follow-up (CHT-05). Endpoints (`/api`): `GET /applications`; `POST|GET /conversations`, `GET|PATCH|DELETE /conversations/{id}` (title, scope, profile; CHT-08); `POST /conversations/{id}/messages` answers with **Server-Sent Events** `tool_call`, `tool_result`, `text`, then `done` (answer, citations, warnings, usage, message id) or `error`; `POST /messages/{id}/feedback` (`incorrect` or `incomplete`, optional correction; CHT-09). A correction is only stored (`proposal_status: none`): turning it into a catalog change (CHT-10) is not implemented. Limitation: the provider interface streams text only, so each model turn completes before the answer is sent in pieces; tool progress is live, the answer text is not token-by-token. There is no user authentication yet (single-user installation behind the proxy, §11).
- **Relationship graph in tables** (application → application, with type and evidence) queried with recursive CTEs for transitive dependencies (QRY-03). No graph database is used.
- **Main tables**: `applications`, `files`, `chunks`, `chunk_embeddings`, `symbols`, `relations`, `signals`, `catalogs`, `catalog_sections`, `catalog_section_embeddings`, `indexing_runs`, `tasks`, `conversations`, `messages`, `feedback` (plus `schema_migrations`). Embeddings are stored at **1536 dimensions** in a separate table per owner; models with a different native size must be configured to output 1536 (OpenAI `text-embedding-3-*` support this), otherwise a new migration and a full reindex are needed.
- **Two database roles**: the schema owner (migrations) and `stc_app` (API and workers, without owner privileges).
- **Numbered SQL migrations** in `src/stc/db/migrations/`, applied by `stc migrate-db` (forward-only, one transaction per migration, checksum-verified, serialized with an advisory lock). The same command creates or updates the `stc_app` role from `STC_APP_PASSWORD` and grants it data access only (no DDL, no access to `schema_migrations`). **Driver**: `psycopg 3` (`binary`, `pool`).
- **Catalogs and global map as markdown** in `./catalogs/<application>.md` and `./catalogs/_global-map.md`, in their own Git repository. It is the source of truth: the database only holds the index. Manual corrections live in marked blocks that regeneration preserves (CAT-04, CAT-05).
- **Volumes**: the Root is mounted read-only; `./catalogs` and `./data` (caches, indexing reports) are writable.

## 4. Indexing pipeline

1. **Detection**: walks the Root, applies exclusions and identifies applications through marker files (`package.json`, `pom.xml`, `*.csproj`, `pyproject.toml`, `go.mod`, …), with manual corrections in `stc.toml` (IDX-02, IDX-03). A directory with a marker is an application and owns its subtree (nested markers do not split it); workspace roots (npm workspaces, Maven parent POMs, Cargo/Gradle workspaces) are not applications, detection descends into them. `stc.toml` at the Root can rename, include, exclude, merge (`paths = [...]`) and split applications, and add gitignore-style exclusion patterns; see the docstring of `stc/indexer/rootconfig.py`.
2. **Hygiene**: noise exclusions and secret detection; whatever is excluded is recorded without its value.
3. **Decomposition**: tree-sitter produces chunks and symbols (IDX-08); files in languages without a grammar are chunked as text and flagged (IDX-04). Grammars come from the official per-language wheels, pinned in `uv.lock`, so there are no runtime downloads (Python, JavaScript, TypeScript/TSX, Java, C#, Go, Rust, Ruby, PHP, Kotlin, C, C++ for now). Each function, method or class is one chunk (with its decorators and the comment right above it); the code between definitions becomes `module` chunks; a class over the size limit becomes a header chunk plus one chunk per member (named `Class.member`); anything still over 120 lines or 6,000 characters is cut at blank lines; Markdown is split at headings and everything else as text. Symbols are the definitions (function, method, class, interface, ...) and the call sites, each linked to its chunk and line range (QRY-07). Chunks are re-created only when a file's content hash or the chunker version changes, and an unchanged chunk keeps its row, so only new or changed chunks need embeddings. A file that turns into a secret loses its chunks and symbols immediately.
4. **Incremental**: SHA-256 per file plus `git diff` between versions; only changed files and affected applications are reprocessed (LCY-01 to LCY-04).
5. **Embeddings**: only for chunks that have no vector or a stale one (another provider, model or input format), in batches with a few requests in flight (`STC_CONCURRENCY`) and retries with backoff for retryable errors. The text sent is the chunk content plus its context (`application/path`, kind and qualified name), capped at 8,000 characters. Progress is committed after every group of requests, so a failed or interrupted run resumes where it stopped. Before embedding, `stc` prints the number of chunks and a rough token estimate (characters / 4) and asks for confirmation above 1,000,000 tokens (`--yes` to skip; `stc embed --dry-run` only estimates, with no external call) (LCY-07). Changing the embeddings model replaces the old vectors on the next run. Secret files never reach this stage.
6. **Relations** (CAT-07): signals are extracted from each new or changed file by static analysis (regular expressions over the source, no execution) and stored per file (`signals`); relations are then derived from the signals of all applications and synced (`relations`, one row per source, target and type, with the strongest evidence, a confidence level and the count of supporting signals). Direction is who calls or depends on whom. Signals: endpoints an application exposes (Flask/FastAPI/Django, Express/Nest, Spring/JAX-RS/Ktor, ASP.NET, Go, Rails, Laravel, OpenAPI specs) and the HTTP calls it makes (absolute URLs, relative client calls, `${BASE}/path` interpolations, `*_URL`-style variable names); Kafka/RabbitMQ/SQS-style publishers and subscribers with literal topic names; database connection strings reduced to `engine://host:port/db` (credentials are never kept, and a file containing them is already excluded as a secret); packages an application provides and depends on, from `package.json`, `pyproject.toml`, `requirements*.txt`, `pom.xml`, `*.csproj`, `go.mod`, `Cargo.toml` and `composer.json`. Matching rules: a URL host equal to an application name (ignoring suffixes such as `-service`, `-api`) or to a host declared with `hosts = [...]` in `stc.toml` is `high`; a service-name environment variable or a unique exact endpoint path is `medium`; a unique path-suffix match is `low`; ambiguous matches, generic paths (`/health`, `/api`, ...) and public hostnames never create a relation. Two applications using the same database are linked by an undirected `shared_database` relation (`low` when the host is a generic name such as `localhost` or `db`). Gradle and Gemfile manifests, topics read from configuration, and the LLM confirmation of uncertain candidates are not done yet.
7. **Catalog per application** (CAT-01 to CAT-05, CAT-08; `stc catalog <application>`): a markdown file `STC_CATALOGS/<application>.md` (`/` in a name becomes `--`) with nine sections: functional purpose, business description (non-technical), stack and versions, module structure, entities and data model, exposed and consumed interfaces, integrations and relations, configuration and environment variables, main flows and entry points. Two of them (interfaces, integrations) are built from the static analysis (endpoints, calls, relations, databases, topics) with no LLM; the other seven are written by the LLM, one structured-output request per section, from context that code assembles from the database (README and docs, manifests, entry points, entity definitions, configuration and environment variables, endpoint handlers, symbols, relations) and that fits a fixed budget per section; a section with no relevant evidence is not sent to the model and is reported as a gap. Every statement carries a confidence level and citations (`file:start-end`); each citation is verified against the indexed files and their line counts, statements left without a valid citation are discarded and counted as a gap, and secret or excluded files can never be cited (CAT-02, CAT-03). Source code is treated as untrusted data in the prompt. Malformed model output is retried once; a section that still fails marks the catalog `partial` (exit code 1) while the rest is kept. Maintainers write corrections between the `stc:manual` markers at the top of each section; they are preserved on regeneration (including for sections that no longer exist) and given to the model as authoritative for that section (CAT-05). A catalog is regenerated only when its inputs changed (file hashes, relations, corrections, model, language, schema version), unless `--force`; `--dry-run` prints how many sections would be sent and a rough token estimate (LCY-07), and a run above 1,000,000 estimated input tokens asks for confirmation. The database records the catalog (`catalogs`, `catalog_sections`); the file is the source of truth. The catalog is not committed to Git by `stc`: `catalogs/` is meant to be its own private repository.
8. **Global map** (CAT-06; `stc map`, also refreshed by every `stc catalog` run): `STC_CATALOGS/_global-map.md`, built from the database with no LLM call, so it is free, deterministic and always consistent with the relations table (LCY-03). It lists every application with the purpose line of its catalog, the relations as a Mermaid graph and a table with type, confidence and evidence, the groups of related applications (connected components), databases shared by several applications, messaging topics with their publishers and subscribers, libraries used by several applications, and the applications with no relationships. A `stc:manual` notes block at the top is preserved on regeneration. `stc catalog --all` generates every indexed application one after another, prints one line per application and a global estimate before calling the LLM (asking for confirmation above 1,000,000 estimated input tokens, `--yes` to skip), skips the ones that are up to date, and a failure in one application does not stop the others (LCY-06, LCY-07); the exit code is 1 if any is not complete. Grouping by business domain would need the LLM and is not done yet.

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
- **OpenAI adapters** (`stc/providers/openai_adapter.py`) use the Chat Completions API (tool calls, JSON-schema structured output, streaming) and the Embeddings API (requests sent in batches, `dimensions` set to `STC_EMBEDDINGS_DIMENSIONS`, 1536 by default to match the `vector(1536)` columns; a response of another size is rejected). `STC_OPENAI_BASE_URL` points them at Azure or any OpenAI-compatible gateway. Calls are spaced by a rate limiter (`STC_LLM_REQUESTS_PER_MIN`) and failures become `ProviderError` with a `retryable` flag (connection errors, 408/409/429 and 5xx are retryable) that never includes prompts or the key. `stc check-providers` makes one tiny real call per client to verify keys and models.
- **Providers and models are configurable** through environment variables (`STC_LLM_PROVIDER`, `STC_LLM_CATALOG_MODEL`, `STC_LLM_CHAT_MODEL`, `STC_LLM_CHAT_REASONING_EFFORT` / `STC_LLM_CATALOG_REASONING_EFFORT` (optional; reasoning models reject tool calls on Chat Completions unless the chat one is `none`), `STC_EMBEDDINGS_PROVIDER`, `STC_EMBEDDINGS_MODEL`), never hardcoded.
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

The chat UI (`frontend/src/app/chat/`, one route: `/` for a new conversation and `/c/:id`) has: a conversation list (open, create, delete with confirmation), a scope and answer-style control (all applications or a selection; technical or functional; CHT-06, CHT-07), the messages, and a composer (Enter sends, Shift+Enter adds a line, Stop abandons the wait). While a question is answered it shows the agent's tool calls with their status and then the answer text from the SSE stream (`fetch` + `ReadableStream`, parsed in `api/sse.ts`); a failure keeps the question and offers Try again. Every answer ends with its **sources**: a source that was verified against the tool results opens the cited lines (`GET /api/files/slice`, code version shown, "changed after indexing" flagged), an unverified one is shown with a text label and a warning, never as proof (CHT-03, CHT-11). "Incorrect" / "Incomplete" open a form for an optional correction (CHT-09). Answers are rendered with **marked** and always sanitized with **DOMPurify** (scripts, handlers and `javascript:` links removed; links open with `rel="noopener noreferrer"`). State lives in one signal store (`ChatStore`); the API client uses `fetch` for everything so the streaming and the rest share one path. Not done: syntax highlighting with Shiki (code is shown monospaced with line numbers), saved UI preferences beyond the theme, and user authentication. In development `ng serve` proxies `/api` to `localhost:8000` (`proxy.conf.json`); serving the built app next to the API is part of the Docker `web` service, which does not exist yet.

## 7. Quality and evaluation

- **pytest** with *fixtures* of small applications in several languages (the indexer's test set, with no real customer code).
- **Quality evaluation (XCT-08)**: `stc evaluate` runs a set of reference questions from `evaluation/questions.yaml` (question, expected applications, expected evidence) and measures:
  - search *recall* (the expected evidence appears in the top-k);
  - answer correctness, with an objective criterion whenever possible;
  - rate of answers without evidence.
- **How it works** (`src/stc/evaluation/`, `stc evaluate`): the questions file is validated strictly (typos are errors). A question has a `kind` (`answerable`, or `unanswerable`: the answer must admit the code does not say), an optional `scope` and `profile` (as in a conversation), and `expect`: application names the answer must mention, **evidence** (application, file, optional line range) that the answer must cite and that search must retrieve, and `contains` / `contains_any` / `not_contains` words (the last one is how a leak of a secret is tested). Two stages: **search** (`--mode retrieval`, the default, no model calls; one tiny embedding call per question when embeddings are configured) measures recall@1/@5/@k and MRR of the expected code for the hybrid search *and* for words only, so the contribution of the embeddings is visible; **answers** (`--mode answers|all`, asks the chat agent each question after a confirmation, up to 8 model calls each) measures the share of answers that satisfy every check, *evidence recall* (expected code cited by a **verified** citation), *citation precision* (verified / all citations), the rate of answerable questions answered with no verified evidence, the rate of unanswerable ones that admitted it, tool steps and tokens. Matching is by file and overlapping lines, never by the model's wording, so no model is used to grade (an LLM judge can be added later for open questions).
- **Reports**: every run is stored in `STC_DATA/evaluations/<UTC time>-NN.json` (settings, the code version of each application, a hash of the question file, per-question results including the answer text) and is compared with the previous one (`--baseline file|latest|none`): changed metrics with better/worse, questions that now fail or pass, and a warning when the question set changed. `--fail-under 0.8` makes the exit code 1 when the answers passed (or, in search mode, recall@k) fall below that value, for CI. The shipped `evaluation/questions.yaml` fits the demo Root; questions about real applications belong in the private repository next to the catalogs.
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
- **tree-sitter version**: `tree-sitter` 0.26.0 corrupts the heap on Windows (random access violations, reproduced without any STC code); it is pinned to `<0.26` until a fixed release is verified.
- **Deeper static analysis** (SCIP/LSIF, call graph): candidate for phase 2 to increase relation precision.
- **Hosting and proxy**: to be decided (local machine, own server or cloud).
- **Capacity**: a full indexing has CPU and LLM-call peaks; size it after the pilot with 3–5 applications.
