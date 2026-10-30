# Changelog

All notable changes are listed here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.0] - 2026-10-30

Applications no longer have to live in one local folder.

### Added

- **Sources**: applications can come from several folders. `STC_ROOT` stays the source called `default`; more are declared in `sources.toml` (`STC_SOURCES`). `stc detect`, `index`, `reindex` and `status` accept `--source`. Each application remembers its source (migration 0009), so the chat and the MCP server read every file from the right folder.
- **GitHub as a source** (`type = "github"` in `sources.toml`): `stc sync [--source NAME] [--dry-run]` lists the repositories of an organization or user (filters: `include`, `exclude`, archived, forks, a branch, or an explicit list) and keeps a shallow, read-only clone of each under `STC_DATA/sources/<name>/`. The token (`STC_GITHUB_TOKEN`) reaches `git` only through the environment, for the host of the repository, and is never logged. A repository that fails does not stop the others. The code version of an application is the commit of its clone. For a user (not an organization) whose token is their own, the private repositories are listed too.

### Notes

- Migration 0009 adds `applications.source`: run `stc migrate-db` after upgrading.
- `stc index --source NAME` only processes the files of that source; pending files of other sources wait for their own run.
- One token (`STC_GITHUB_TOKEN`) serves every GitHub source. GitLab, GitHub App authentication and Docker Compose support for extra sources are not included yet.

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

[0.2.0]: https://github.com/jnascimentogcc/stc/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/jnascimentogcc/stc/releases/tag/v0.1.0
