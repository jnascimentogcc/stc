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
- **Documentation**: the full set of navigable pages, with diagrams, that describes one application (section 4.7); richer than the catalog.
- **Feature**: a capability of an application, detected from an entry point (a batch job, a program nobody calls, a group of endpoints) and documented on its own page.
- **Evidence**: verifiable reference to the origin of a statement (application, file, line range).

## 4. Functional requirements

### 4.1 Indexer — reading and detection

| ID | Requirement |
|---|---|
| IDX-01 | Take the path of a Root as input and traverse it recursively. More than one folder can be declared as *sources* (`sources.toml`): local folders, or the repositories of a GitHub organization or user, cloned read-only and shallow by `stc sync`. Every application remembers the source it came from, and each source keeps its own `stc.toml`. |
| IDX-02 | Automatically detect the applications inside the Root based on project markers (manifests, build files, solution files, etc.), regardless of language. |
| IDX-03 | Let the user correct the detection: include, exclude, merge or split applications, and give them a name. |
| IDX-04 | Support applications in multiple languages and technologies; files in unsupported languages are handled as text and flagged. COBOL, copybooks and JCL are supported (chunked by paragraph and by job step). |
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
| CAT-07 | Detect relationships between applications from evidence (endpoint calls, messaging, shared databases, common internal libraries, configuration; for COBOL and JCL: called programs, shared copybooks, DB2 tables and datasets passed between jobs). |
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

### 4.7 Application documentation

The catalog is a short summary meant for lookup. The **documentation** is the full, navigable description of one application: what it does, how it is built, each feature, its data, its interfaces and how it runs, with diagrams. It is written for a person who must understand or take over the application and has nothing else to read.

| ID | Requirement |
|---|---|
| DOC-01 | Generate, per application, a **documentation set**: several markdown pages in one folder (not one long file), with an index page that lists all pages and a stable path per page. |
| DOC-02 | The set contains, at minimum: an overview (what the application is, what it does for the business, stack, key figures), the architecture (components and how they fit), one page **per feature**, the data model, the interfaces (entry points, programs, jobs, endpoints, datasets, topics), the dependencies on other applications, and configuration and operations (settings, how it is run or scheduled). A page whose subject does not exist in the application (for example, no data model) is omitted or says so, never filled with invention. |
| DOC-03 | **Features are detected by code, not by the model**: entry points (batch jobs, programs nobody calls, endpoint groups, message consumers) are found by static analysis, and each becomes a feature page that lists the code involved (programs, files, tables, datasets). The model only describes what the code of that feature does. Features beyond a configurable limit are listed on the features page as not documented in detail. |
| DOC-04 | **Diagrams are generated from static analysis, in Mermaid text**, inside the markdown: application context, call graph, per-feature flow with the data it touches, control flow of the entry program, and an entity-relationship diagram. Nodes and edges come from facts (symbols, signals, relations, DDL), never from the model, so a diagram cannot show something the code does not contain. Diagrams that would be too large are truncated and say so. |
| DOC-05 | Every statement written by the model carries **verified evidence** (application-relative file and line range), with a confidence level and the gaps, like CAT-02 and CAT-03: a statement with no valid citation is discarded and counted as a gap. A part with no relevant evidence is not sent to the model. Tables built from static analysis carry their evidence too. |
| DOC-06 | The pages are human-editable markdown, versionable, in their own location (`STC_DOCS`, one folder per application). Maintainers' corrections written in marked blocks are **preserved on regeneration and given to the model as authoritative** (as CAT-05); notes of a page that no longer exists are kept in a page of orphaned notes rather than lost. |
| DOC-07 | Regeneration is **incremental per page**: a page is generated again only when what it is built from changed (the code it cites, the relations, the corrections, the model, the language, the documentation schema version). `--force` regenerates everything, `--dry-run` shows what would be sent with a cost estimate, and a failure in one page or application does not stop the others (LCY-06, LCY-07). |
| DOC-08 | The documentation records the code version, date, model and language it was generated with, and is flagged as **outdated** when the application was indexed from a newer version (LCY-08, CHT-11). |
| DOC-09 | Secrets and excluded files are never read, sent or cited (XCT-01); the documentation is written in the configured language (XCT-07). |
| DOC-10 | The documentation can be generated **without any model** (static pages only: facts, tables and diagrams, with the narrative parts marked as not generated), so the structure can be checked and used for free. |
| DOC-11 | **Viewing in the web UI**: choose an application, navigate its pages (grouped navigation, on-page table of contents, links between pages and deep links), read them rendered with sanitized markdown, syntax-highlighted code and **Mermaid diagrams** that follow the light and dark theme, and open the code behind any cited `file:lines` in place. An application without documentation says so and how to generate it; an outdated one shows the warning. |
| DOC-12 | The documentation can be **downloaded** as one markdown file (all pages, in navigation order). |
| DOC-13 | The documentation is **read-only through the API** (list, pages of an application, one page, export). Nothing in the UI writes it: corrections are made in the files and take effect on the next generation. |

### 4.8 Migration specifications

Documentation says what a system is; a **specification** says what a person must rebuild, in another technology, to replace one feature of it. STC does not generate the new code: it produces, per feature, the verified description of the legacy behavior and a plan to rebuild it, for a coding assistant (or a developer) to implement. Applications are read-only and nothing here changes them.

| ID | Requirement |
|---|---|
| SPC-01 | Generate, per application, a **specification set**: an index page and **one specification per feature** (the features of DOC-03), in markdown, with a stable path per page. |
| SPC-02 | The **target stack** is configurable: a text file (`--stack`, `STC_SPEC_STACK`) that describes it freely. A default (Java, Spring Boot, JPA, PostgreSQL, asynchronous REST) is used when none is given. The stack is given to the parts that design for it (data, interfaces, design, acceptance, plan) and is an input of every page: changing it rewrites the specifications. |
| SPC-03 | Each feature specification has: purpose and scope; functional requirements ("The system must ...") in the order the behavior happens; business rules with the exact values; the legacy flow (diagram); data (fields, sizes, precision, keys) and how it maps to the target; interfaces; a proposed design in the target stack; acceptance scenarios (Given/When/Then); an implementation plan of small testable tasks; risks and open questions; and the legacy code to read. |
| SPC-04 | Every statement cites the legacy code it comes from (`file:start-end`), verified against the indexed files as in CAT-02; a statement without a valid citation is discarded and counted as a gap. Design and plan statements cite the legacy code they replace. Confidence levels and gaps are shown; what the code does not show (scheduling, volumes, real data, screens) is listed as an open question, never filled in. |
| SPC-05 | The index lists the features in a **migration order computed from the facts, not by the model**: a feature comes after the ones that write the tables or datasets it reads; cycles are flagged. It also lists the data shared by several features and proposes a strategy. |
| SPC-06 | Specifications are files in `STC_SPECS/<application>/`, incremental per page (same inputs rule as DOC-07, plus the stack), with maintainers' notes preserved between `stc:manual` markers (DOC-08) and `--static-only` / `--dry-run` as in DOC-10. |
| SPC-07 | The files are the product: they are written to be read by a coding assistant (plain markdown, one feature at a time, citations to read the code). The index says how to use them. |

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
8. For an application with batch jobs and COBOL programs, the documentation has a page per job or entry program, each with a diagram of the programs, tables and datasets it touches that matches the code, and every sentence of the narrative cites lines that exist (DOC-03 to DOC-05).
9. A second generation with no changes calls the model zero times; a change to one program regenerates only the pages that depend on it (DOC-07).
10. In the web UI, the documentation of an application opens with its diagrams rendered, and clicking a cited `file:lines` shows that code (DOC-11).
11. For an application with batch jobs and COBOL programs, `stc spec` writes one specification per feature in an order where what writes a table comes before what reads it, every statement cites the legacy code, and a different target stack rewrites the specifications (SPC-02, SPC-05).

## 8. Open questions

1. How is "application" defined in edge cases (monorepos, shared libraries, multiple modules)?
2. Is access control per application or per user needed?
3. Do manually corrected catalogs live in the Root, in their own repository, or elsewhere?
4. What is the expected frequency of code changes (this determines how demanding incremental updating must be)?
5. What are the 20–30 real questions that will form the evaluation set?
6. Should the chat be able to answer from catalogs alone when the code is not available?
7. Should the documentation also be searchable by the chat and the MCP server (as the catalogs are)? Not in the first version: it is read in the web UI.
