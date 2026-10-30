# STC — Functional Specification (v0.1)

> Status: initial draft. Scope: functionality. Out of this document: the technology stack and the UI specification (defined in separate specs: `docs/tech-stack.md` and `docs/ui-spec.md`).

## 1. Purpose

> **STC** stands for *"Talk is cheap. Show me the code"* (Linus Torvalds). The project is open source (MIT).

Make it possible to understand and query a large set (100+) of applications **without documentation**, using only their source code. The system reads a folder that contains the applications, builds structured knowledge about them, and answers **technical** and **functional** questions, always referencing the origin of the answer.

## 2. Components

| Component | Responsibility |
|---|---|
| **Indexer** | Reads the folder, detects the applications, extracts knowledge and keeps it up to date. |
| **Query engine** | Shared core that searches and returns knowledge and evidence. It exists only once. |
| **Chat** | Natural-language conversation about the knowledge, built on the query engine. |
| **MCP** | Exposes the query engine's capabilities to external MCP clients. |

Chat and MCP are clients of the same engine; they do not duplicate search logic.

## 3. Concepts

- **Root**: folder provided by the user that contains the applications.
- **Application**: independent logical unit inside the Root (e.g. service, module, project).
- **Catalog**: structured document generated per application.
- **Global map**: cross-cutting view derived from all catalogs.
- **Evidence**: verifiable reference to the origin of a statement (application, file, line range).

## 4. Functional requirements

### 4.1 Indexer — reading and detection

| ID | Requirement |
|---|---|
| IDX-01 | Take the path of a Root as input and traverse it recursively. More than one folder can be declared as *sources* (`sources.toml`); every application remembers the source it came from, and each source keeps its own `stc.toml`. |
| IDX-02 | Automatically detect the applications inside the Root based on project markers (manifests, build files, solution files, etc.), regardless of language. |
| IDX-03 | Let the user correct the detection: include, exclude, merge or split applications, and give them a name. |
| IDX-04 | Support applications in multiple languages and technologies; files in unsupported languages are handled as text and flagged. |
| IDX-05 | By default, ignore third-party dependencies, build artifacts, binaries, generated files and lockfiles, with a configurable exclusion list. |
| IDX-06 | Detect and exclude secrets (credentials, keys, tokens, environment files) before any processing; report what was excluded without exposing the value. |
| IDX-07 | Record per file: application, path, language, size, status and content identifier. |
| IDX-08 | Decompose the code into meaningful units (functions, classes, modules) rather than fixed-size blocks, preserving location (file and lines). |
| IDX-09 | Configuration files, database schemas, API definitions, scripts and existing documentation (README, comments) are also sources of knowledge. |

### 4.2 Indexer — extracted knowledge

| ID | Requirement |
|---|---|
| CAT-01 | Generate one catalog per application with, at minimum: functional purpose, stack and versions, module structure, entities and data model, exposed endpoints/interfaces, consumed interfaces, external integrations, configuration and environment variables, main flows, and entry points. |
| CAT-02 | Every statement in the catalog must have associated evidence. |
| CAT-03 | Mark in the catalog the confidence level and the gaps (what could not be determined), instead of inferring without basis. |
| CAT-04 | Catalogs are stored in a human-readable, human-editable and versionable format. |
| CAT-05 | Manual corrections to a catalog are preserved in future regenerations and take precedence over generated content. |
| CAT-06 | Generate the global map: list of applications, relationships between applications (who calls/depends on whom), shared databases and services, common technologies, and applications with no relationships. |
| CAT-07 | Detect relationships between applications from evidence (endpoint calls, messaging, shared databases, common internal libraries, configuration). |
| CAT-08 | Allow a functional/business description per application, in non-technical language. |

### 4.3 Indexer — lifecycle

| ID | Requirement |
|---|---|
| LCY-01 | Initial full indexing and **incremental** indexing: reprocess only what changed. |
| LCY-02 | Detect changes by content comparison and, when available, by version history. |
| LCY-03 | When an application changes, update that application's catalog and the affected parts of the global map. |
| LCY-04 | Removed or moved files are removed/updated in the knowledge. |
| LCY-05 | Allow reindexing a specific application or the whole Root on demand. |
| LCY-06 | Report progress, errors per file/application and a final summary; a failure in one application does not stop the others. |
| LCY-07 | Estimate and report processing volume and cost before a full indexing. |
| LCY-08 | Record which code version each piece of knowledge corresponds to, to detect outdated answers. |

### 4.4 Query engine

| ID | Requirement |
|---|---|
| QRY-01 | List applications with filters (technology, name, relationship to another application). |
| QRY-02 | Return an application's catalog, in full or by section. |
| QRY-03 | Return the global map and an application's direct and transitive dependencies (upstream and downstream). |
| QRY-04 | Search code by meaning and by exact term, combined, with the ability to restrict to one or more applications, languages or paths. |
| QRY-05 | Search the catalogs (functional knowledge) separately from the code. |
| QRY-06 | Read a file or line range, respecting secret exclusions. |
| QRY-07 | Search by literal pattern/text in the code (equivalent to grep) and by symbol (definitions and usages). |
| QRY-08 | Answer cross-cutting questions (e.g. "which applications use X?") using the global map before descending into the code. |
| QRY-09 | All results include evidence (application, file, lines) and the indexed code version. |
| QRY-10 | State explicitly when there is not enough information, instead of producing an unsupported answer. |
| QRY-11 | Order results by relevance and allow pagination. |

### 4.5 Chat

| ID | Requirement |
|---|---|
| CHT-01 | Accept natural-language questions (technical, functional and business) about one or several applications. |
| CHT-02 | Decide on its own which engine capabilities to use (catalog, map, search, read) and iterate until it has enough evidence. |
| CHT-03 | Each answer cites the sources (application, file, lines) that support it. |
| CHT-04 | Distinguish in the answer what is **proven in the code** from what is **inferred**, and admit when it does not know. |
| CHT-05 | Keep conversation context (follow-up questions, references to previous answers). |
| CHT-06 | Allow restricting a conversation to a scope: one application, a set of applications, or the whole Root. |
| CHT-07 | Adapt the level of the answer to the requested profile (technical or functional/business). |
| CHT-08 | Save, resume and delete conversations. |
| CHT-09 | Allow the user to flag an answer as incorrect or incomplete, with an optional correction. |
| CHT-10 | A flagged correction can be proposed as a change to the application's catalog (subject to approval). |
| CHT-11 | Indicate when the knowledge used may be outdated relative to the code. |

### 4.6 MCP

| ID | Requirement |
|---|---|
| MCP-01 | Expose the engine's capabilities as tools: list applications, get catalog, get global map, get dependencies, search catalogs, search code, search by pattern/symbol, read file, indexing status. |
| MCP-02 | Also expose catalogs and the global map as queryable resources. |
| MCP-03 | Each tool has a description and parameters clear enough for a model to choose and use it correctly. |
| MCP-04 | Tool responses include evidence and version, just like the engine. |
| MCP-05 | MCP operations are read-only with respect to the code; any operation that changes knowledge (e.g. reindex, approve correction) is separate and explicit. |
| MCP-06 | Limit response size and paginate, so as not to saturate the client's context. |
| MCP-07 | Return structured, understandable errors (nonexistent application, indexing in progress, no results). |

## 5. Cross-cutting requirements

| ID | Requirement |
|---|---|
| XCT-01 | **Security**: secrets are never indexed, sent to external services, or returned in answers. |
| XCT-02 | **Data control**: the user knows which parts of the code are sent to external services and can define exclusions per application. |
| XCT-03 | **Configuration**: Root, exclusions, per-application profiles and processing parameters are defined in explicit configuration. |
| XCT-04 | **Idempotency**: repeating an indexing with no code changes does not change the result. |
| XCT-05 | **Traceability**: a record of what was indexed, when, with which version and with what result. |
| XCT-06 | **Scale**: support 100+ applications with interactive response times for queries. |
| XCT-07 | **Language**: catalogs and answers are in English by default, with the language configurable (globally or per conversation); identifiers and technical terms are preserved as in the code. |
| XCT-08 | **Evaluation**: support for a set of reference questions with expected answers, runnable after every relevant change, to measure answer and search quality. |

## 6. Out of scope (v0.1)

- Technology stack and implementation choices.
- User interface specification.
- Changing the applications' code (the system is read-only).
- Running the applications or dynamic analysis.
- Access control per user/application (to be confirmed).

## 7. Acceptance criteria (initial)

1. Given a Root with 100+ applications, the indexer detects the applications and generates a catalog for each one, without intervention, with a list of gaps.
2. A second indexing with no changes reprocesses nothing; a change to a file updates only the affected application.
3. A cross-cutting question ("which applications communicate with application X?") is answered from the global map, with evidence.
4. A detail question ("where is field Y validated?") returns the correct file and lines.
5. A question with no possible answer in the code results in an answer that admits it, without invention.
6. The same question asked in the chat and through MCP produces equivalent evidence.
7. No secret present in the code appears in indexes, catalogs or answers.

## 8. Open questions

1. How is "application" defined in edge cases (monorepos, shared libraries, multiple modules)?
2. Is access control per application or per user needed?
3. Do manually corrected catalogs live in the Root, in their own repository, or elsewhere?
4. What is the expected frequency of code changes (this determines how demanding incremental updating must be)?
5. What are the 20–30 real questions that will form the evaluation set?
6. Should the chat be able to answer from catalogs alone when the code is not available?
