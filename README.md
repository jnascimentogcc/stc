# STC

> *"Talk is cheap. Show me the code."* — Linus Torvalds

STC understands and answers questions about large collections of applications **that have no documentation**, using only their source code. Point it at a folder with 100+ applications and ask things like *"which applications talk to the billing service?"* or *"where is this field validated?"*. Every answer comes with the evidence that backs it: application, file, line range and indexed code version.

The name is the philosophy: the system does not just talk, it shows the code.

## Status

**Early design stage.** The repository currently contains the specifications only; there is no runnable code yet. Everything described under "How it works" is the planned design (v0.1) and may change.

## How it works

1. **Index.** The indexer walks a read-only folder (the *Root*), detects the applications in it, strips secrets, splits the code into meaningful units (functions, classes, modules) with tree-sitter, and keeps everything up to date incrementally.
2. **Catalog.** For each application it generates a human-readable, editable markdown **catalog** (purpose, stack, data model, endpoints, integrations, main flows, …), where every statement carries evidence and a confidence level, and gaps are listed instead of guessed. A **global map** shows how the applications relate to each other.
3. **Query.** A single query engine searches catalogs and code (semantic + exact + symbol search) and reads files. It is exposed through:
   - a **chat** (web UI) that iterates over the engine until it has enough evidence, and separates what is *proven in the code* from what is *inferred*;
   - an **MCP server**, so tools such as Claude Code, Claude Desktop or Cursor can query the same knowledge.

Design principles:

- Facts (applications, symbols, relations, locations) come from deterministic analysis; the LLM interprets and always cites evidence.
- Application code is read-only: it is never modified or executed.
- Secrets are never indexed, sent to external services or returned in answers.
- Bring your own model provider: OpenAI first, Anthropic and others by configuration.

## Planned stack

Python (FastAPI, Typer CLI), PostgreSQL 17 + pgvector, tree-sitter, an MCP server, and an Angular + Tailwind + SpartanNG chat UI, shipped with Docker Compose. See [docs/tech-stack.md](docs/tech-stack.md).

## Documentation

| Document | Contents |
|---|---|
| [docs/functional-spec.md](docs/functional-spec.md) | What the system does: requirements, acceptance criteria, open questions |
| [docs/tech-stack.md](docs/tech-stack.md) | Architecture, data model, indexing pipeline, AI providers, deployment |
| [docs/ui-spec.md](docs/ui-spec.md) | Global UI design system and patterns |
| [docs/getting-started.md](docs/getting-started.md) | Run the pipeline on the demo applications, step by step |

## Contributing

The project is at the specification stage, so the most useful contributions right now are feedback on the specs: open an issue to discuss requirements, the open questions in the functional spec, or the technology choices. Code contributions will be welcome once the foundations land; a `CONTRIBUTING.md` will follow.

Everything in this repository (code, docs, commits, UI strings) is written in English.

## License

[MIT](LICENSE) © 2026 Jorge Nascimento
