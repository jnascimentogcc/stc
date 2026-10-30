# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-10-29

First pre-release: the whole pipeline works end to end.

### Added

- **Indexer** (`stc index`, `stc reindex`, `stc status`): application detection from manifests (npm, Maven, Python, .NET, Go, Rust, PHP and others), secret exclusion and credential redaction in configuration files, tree-sitter chunking and symbols, static signals and relations between applications, optional LLM confirmation of uncertain relations, embeddings. Incremental and idempotent.
- **Catalogs** (`stc catalog`): one editable markdown catalog per application with verified evidence and confidence levels, manual corrections that survive regeneration, corrections proposed from chat feedback and approved with `stc corrections`, and a deterministic global map (`stc map`).
- **Query engine** (`stc query`): hybrid search (vector + full text, RRF), exact search, symbols, dependencies, catalogs and file reading, with test files demoted in code search.
- **Chat**: FastAPI API with streamed answers (SSE), citations verified against the code, conversations and feedback; Angular UI with scope and profile selection, tool steps, a code viewer with syntax highlighting and a light/dark theme.
- **MCP server** (`stc serve-mcp`) over stdio or HTTP.
- **Evaluation** (`stc evaluate`): recall and answer-quality checks from a file of reference questions, with reports and comparison.
- **Operations**: Docker Compose (database, migrations, API, MCP server, web), `--env-file` support for separate setups.
- **Providers**: OpenAI for the LLM and embeddings, behind provider interfaces.

### Not included yet

- Authentication of the chat: do not expose it beyond a trusted network.
- Anthropic and Voyage providers.
- Gradle and Gemfile manifests.

[0.1.0]: https://github.com/jnascimentogcc/stc/releases/tag/v0.1.0
