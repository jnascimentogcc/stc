# STC

> *"Talk is cheap. Show me the code."* — Linus Torvalds

STC understands and answers questions about large collections of applications **that have no documentation**, using only their source code. Point it at a folder with 100+ applications and ask things like *"which applications talk to the billing service?"* or *"where is this field validated?"*. Every answer comes with the evidence that backs it: application, file, line range and indexed code version.

The name is the philosophy: the system does not just talk, it shows the code.

## Status

**Working, pre-1.0.** The whole pipeline runs end to end: indexer, catalogs, global map, query engine, chat (API + web UI), MCP server, evaluation harness and operations commands (`reindex`, `status`, Docker Compose). It has been exercised on the demo applications and on small real projects (TypeScript, Java/Spring Boot, React).

Not implemented yet: chat authentication (do not expose the chat beyond a trusted network), the Anthropic and Voyage providers (OpenAI is the one available), and Gradle/Gemfile manifest detection. The design is described in [docs/functional-spec.md](docs/functional-spec.md) and may still change.

## How it works

1. **Index.** The indexer walks a read-only folder (the *Root*), detects the applications in it, strips secrets (and redacts credential values in configuration files), splits the code into meaningful units (functions, classes, modules) with tree-sitter, extracts signals (endpoints, outgoing calls, databases, messaging) and derives the relations between applications. Uncertain relations can be confirmed by the LLM. Indexing is incremental and idempotent.
2. **Catalog.** For each application it generates a human-readable, editable markdown **catalog** (purpose, stack, data model, endpoints, integrations, main flows, …), where every statement carries verified evidence and a confidence level, and gaps are listed instead of guessed. A **global map** shows how the applications relate to each other. Manual corrections survive regeneration.
3. **Query.** A single query engine searches catalogs and code (semantic + exact + symbol search), follows dependencies and reads files. It is exposed through:
   - a **chat** (web UI) that iterates over the engine until it has enough evidence, streams the answer, verifies every citation against the code and separates what is *proven in the code* from what is *inferred*;
   - an **MCP server**, so tools such as Claude Code, Claude Desktop or Cursor can query the same knowledge;
   - the `stc query` and `stc ask` commands.
4. **Evaluate.** `stc evaluate` runs a file of reference questions and reports search recall and answer quality, so changes to the indexer or the prompts can be measured.

Design principles:

- Facts (applications, symbols, relations, locations) come from deterministic analysis; the LLM interprets and always cites evidence.
- Application code is read-only: it is never modified or executed.
- Secrets are never indexed, sent to external services or returned in answers.
- Bring your own model provider: OpenAI first, Anthropic and others by configuration.

## Stack

Python 3.12+ (FastAPI, Typer CLI), PostgreSQL 17 + pgvector, tree-sitter, an MCP server, and an Angular + Tailwind + SpartanNG chat UI, shipped with Docker Compose. See [docs/tech-stack.md](docs/tech-stack.md).

## Quick start

Requirements: [uv](https://docs.astral.sh/uv/), Docker, and an OpenAI API key.

```bash
uv sync
docker run -d --name stc-dev-db -e POSTGRES_PASSWORD=test -e POSTGRES_DB=stc -p 55432:5432 pgvector/pgvector:pg17
cp .env.example .env          # set the database URLs, the models and your API key
uv run stc migrate-db
uv run stc index --root examples/demo-root
uv run stc catalog --all
uv run stc ask "which applications call demo-api, and where?"
```

To run everything in containers (database, API, MCP server and chat on port 8080):

```bash
docker compose up -d --build
```

The complete walk-through, including the chat UI, the MCP server and the evaluation, is in [docs/getting-started.md](docs/getting-started.md). `uv run stc --help` lists every command.

The demo applications in `examples/demo-root/` are fake and safe to send to a model provider. Before indexing your own code, read [docs/real-projects.md](docs/real-projects.md).

## Documentation

| Document | Contents |
|---|---|
| [docs/functional-spec.md](docs/functional-spec.md) | What the system does: requirements, acceptance criteria, open questions |
| [docs/tech-stack.md](docs/tech-stack.md) | Architecture, data model, indexing pipeline, AI providers, deployment |
| [docs/ui-spec.md](docs/ui-spec.md) | Global UI design system and patterns |
| [docs/getting-started.md](docs/getting-started.md) | Run the pipeline on the demo applications, step by step |
| [docs/real-projects.md](docs/real-projects.md) | Checklist for the first run on real code |

## Contributing

Issues and pull requests are welcome. Before opening a pull request, run the checks:

```bash
uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy
(cd frontend && npm test -- --no-watch)
```

Database tests are skipped unless `STC_TEST_DB_URL` points to a throwaway pgvector container (never the development database; see [CLAUDE.md](CLAUDE.md)). Requirements have IDs in the functional spec (IDX-*, CAT-*, QRY-*, CHT-*, MCP-*, XCT-*): cite them in issues and commits. See [CONTRIBUTING.md](CONTRIBUTING.md).

Everything in this repository (code, docs, commits, UI strings) is written in English. Catalogs generated from real code never belong here; keep them in their own private repository.

## License

[MIT](LICENSE) © 2026 Jorge Nascimento
