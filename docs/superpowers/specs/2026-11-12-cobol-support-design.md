# COBOL support — design

Status: draft for approval. Nothing in this document is implemented.

## Intent and why

STC must be able to answer questions about mainframe systems written in COBOL, with JCL, DB2 and
CICS, using only their source code. The people asking are mostly after **business rules** ("what
is the income limit for a payroll loan?", "when does a contract go into default?") and, second,
technical questions ("who calls this program?", "which programs write to this table?").

COBOL is a likely part of the 100+ applications STC will meet, and today it is barely supported.
The maintainer has no COBOL code of their own to test with, so the work starts from a fictional
sample system that is already in the repository (see "Baseline").

## Baseline (measured on 2026-11-12, before any change)

Sample Root: `examples/cobol-bank-root/` — two applications, `loans-core` (13 files: copybooks,
online CICS programs, a subroutine, a nightly batch with its JCL, DDL) and `collections` (2
files: a dunning program and its JCL). Reference questions: `evaluation/cobol-bank-questions.yaml`
(19 questions, 14 functional, 4 technical, 1 unanswerable).

| Measure | Today |
|---|---|
| Chat answers that pass `stc evaluate --mode all` | 19 of 19 (two checks were loosened first, they were too strict) |
| Search recall@5 / MRR (hybrid) | 0.89 / 0.64 |
| Input tokens for the 19 answers | about 400,000 (about 21,000 per answer) |
| Chunks | 15, one per file, all plain text, **0 symbols** |
| Catalog statements with verified evidence | `loans-core` 3, `collections` 0; both catalogs are mostly "Gaps" |
| Relations in the global map | **0** (the real dependency, through a dataset and a copybook, is invisible) |
| Citations | whole file (`LNELIG.cbl:1-60`), never the line of the rule |

Today a COBOL or JCL file is indexed as text with no language, because the extension is unknown.
The chat answers well only because the sample is tiny and the model can read whole files. On a
real system with hundreds of programs that stops working.

A spike showed that the tree-sitter COBOL grammar is **not** an option: it failed to parse all 6
programs and all 6 copybooks, stopping at the first `EXEC SQL` / `EXEC CICS` and at `COPY` inside
`FD` or LINKAGE, and found 11 of 24 paragraphs. The language pack that carries it also pulls
tree-sitter 0.26, which this project pins below because of crashes on Windows.

## Goals

1. Recognize COBOL and JCL by file extension and index them as such.
2. Detect a COBOL application without a manual `stc.toml` entry.
3. Chunk COBOL by meaningful unit (paragraph, section, copybook, JCL step) so citations point at
   the lines of the rule and answers need fewer tokens.
4. Extract symbols: programs, paragraphs, copybooks, DB2 tables, JCL jobs and steps.
5. Extract cross-application relations from the code: `CALL`, `COPY`, `EXEC SQL` tables, CICS
   `LINK`/`XCTL`, and from JCL the program run by each step and the datasets read and written.
6. Give catalogs and the global map enough material to describe a COBOL application, including
   the business rules in the PROCEDURE DIVISION and the values in copybooks.
7. Show the improvement with the existing evaluation: same 19 questions, same pass rate or better,
   better search and citation precision, fewer tokens, and a non-empty global map.

## Non-goals

- Compiling, executing or simulating COBOL. Application code stays read-only (CLAUDE.md).
- A full COBOL parser or a complete control-flow graph. `PERFORM` chains are not resolved.
- Preprocessing: no expansion of `COPY ... REPLACING`, no macro or `EXEC SQL INCLUDE` expansion.
- EBCDIC conversion. Files are expected as ASCII or UTF-8; an EBCDIC file is reported, not decoded.
- Other mainframe technologies in this round (PL/I, assembler, Natural, IMS, MQ, VSAM catalogs).
- Changing how any other language is handled.

## Anti-goals (a failure even if it technically works)

- A new heavy or fragile dependency (the tree-sitter language pack, a COBOL toolchain).
- An analyzer that stops parsing at the first construct it does not know and silently drops the
  rest of the file. Unknown constructs must degrade to plain text for that region only.
- Relations that look certain but are guesses. A dynamic `CALL WS-PROG` is recorded as
  unresolved, never attached to a guessed program.
- Regressions on the existing languages or on the demo Root and its evaluation.

## Constraints

- Follows the architecture in `CLAUDE.md`: facts come from deterministic analysis, interpretation
  from the LLM; one query engine; secrets excluded before anything else; incremental, idempotent
  indexing; everything in English; no customer names in the repository.
- Same style as `signals.py`: line-based readers with regular expressions, no external parser.
- Windows must keep working.
- COBOL names are case-insensitive; names are normalized for matching but shown as written.
- Source format: fixed format (columns 7–72, indicator in column 7, `*` comments) first; free
  format (`>>SOURCE FORMAT FREE`) should not break, but is not a priority.

## Decided by the maintainer

- Order: samples first (done), then adapt STC.
- Business rules are the main use; technical questions come second.
- Domain of the samples: bank loans and interest.
- No tree-sitter for COBOL (decided after the spike; the maintainer asked to test it first).
- Files arrive **with extensions**, so detection by extension is enough for now.
- Copybooks arrive **together with the programs**, in the same repository.
- The real code is organized as **one application per top-level folder**, so the first-level
  folder detection rule in "Detecting applications" is the intended one, not just a guess.

## Design

### Languages and files

Add `cobol` (`.cbl`, `.cob`, `.cobol`), `cobol-copybook` (`.cpy`, `.copy`) and `jcl` (`.jcl`,
`.proc`, `.prc`) to the file-name table in `languages.py`. DDL (`.sql`) already has a language.
An unrecognized extension keeps today's behavior (text, flagged).

### Detecting applications

A COBOL application has no marker file, only source files, and its sources are split across
folders (`src/`, `jcl/`, `copybooks/`). Rule (confirmed by the maintainer: one application per
top-level folder): a first-level folder of a source that contains COBOL or JCL files at any depth
is one application, unless a project marker already makes it one, or `stc.toml` says otherwise.
The existing manual corrections (rename, merge, split, exclude) keep working unchanged.

### Chunks and symbols

- COBOL: one chunk per paragraph in the PROCEDURE DIVISION, one per section of the DATA and
  ENVIRONMENT divisions, and a header chunk (IDENTIFICATION DIVISION). A multi-line
  `EXEC SQL ... END-EXEC` / `EXEC CICS ... END-EXEC` stays inside the chunk it belongs to. A chunk
  above the existing size limit is cut at blank lines, as today.
- Copybook: one chunk per `01` group, or the whole file when small. Level-88 condition names and
  `VALUE` clauses stay in the chunk, because they carry business values.
- JCL: one chunk per step (`//NAME EXEC ...` with its `DD` statements), plus a job header chunk.
  In-stream data (`DD *`) stays with its step.
- Symbols (definitions): program (`PROGRAM-ID`), paragraph, section, copybook (file name),
  01-level record, JCL job and step. Symbols (uses): `CALL`, `COPY`, `PERFORM` of a paragraph.
- Chunk and symbol kinds are new strings; no schema change is needed for them.
- The chunker version is raised so existing COBOL files are chunked again.

### Signals and relations

New signal kinds and the relations they produce (this needs a migration that widens the `kind`
check of `signals` and the allowed relation types):

| Signal | Provided by | Consumed by | Relation |
|---|---|---|---|
| `program` | `PROGRAM-ID` | `CALL 'X'`, CICS `LINK`/`XCTL PROGRAM('X')`, JCL `EXEC PGM=X`, `RUN PROGRAM(X)` | calls (high when the target exists in another application) |
| `dataset` | JCL `DD DSN=X` with `DISP=NEW`/`MOD` (written) | JCL `DD DSN=X` with `DISP=SHR`/`OLD` (read) | batch dependency (high) |
| `copybook` | the copybook file | `COPY X` found in another application | shared layout (medium) |
| `database` | — | `EXEC SQL` and DDL naming a table | shared table (low, there is no host) |

Evidence keeps application, file and lines as for the other signals. Dynamic calls
(`CALL WS-PROG`) produce an unresolved signal that is shown, not linked.

### Catalogs and the global map

Catalog generation already uses the stored chunks and symbols; with the new ones, the COBOL
sections (stack, modules, interfaces, integrations) must have evidence. The global map then lists
the new relations through the existing mechanism, with the kind named in plain words. The
generated text must not call a business rule "documented": it cites the code.

### Tests

- Unit tests for each reader, with small inline fixtures, covering fixed-format columns,
  comments, continuation lines, `EXEC SQL`/`CICS` blocks, `COPY` inside `FD`, and malformed input
  (must degrade to text, never raise).
- The sample Root is an integration fixture: detection gives the two applications, the expected
  relation `collections -> loans-core` exists with evidence, and the line ranges used in
  `evaluation/cobol-bank-questions.yaml` fall inside the new chunks.
- The existing test suite and the demo evaluation must still pass.

## Acceptance

On the sample Root, after re-indexing and regenerating catalogs:

- `stc detect` finds `loans-core` and `collections` without `stc.toml`.
- Symbols are non-zero; every paragraph of the six programs is a symbol and lies inside exactly
  one chunk (very short adjacent paragraphs share a chunk, see "Chunks" under "My calls").
- The global map shows the `collections -> loans-core` dependency, with the dataset and copybook
  as evidence.
- `stc evaluate --mode all` on the 19 questions: pass rate not lower than 19/19, search recall@5
  and MRR not lower than the baseline, input tokens clearly lower, citations that point at the
  paragraph (not the whole file) for the business-rule questions.
- The demo Root evaluation is unchanged.

## Left to the builder

Names of the new kinds and symbols; module layout (for example one module per reader); the exact
regular expressions; how continuation lines and the 72-column limit are handled; migration
numbering; how many levels of a `01` group go into one chunk; wording of the relation labels in
the global map.

## My calls (to be checked by the maintainer)

- Extensions list above, including `.proc`/`.prc` for JCL procedures.
- The confidence levels in the relation table, in particular `low` for tables and `medium` for a
  copybook shared by name.
- Not resolving `PERFORM` and not expanding `COPY ... REPLACING`.
- Fixed format first, free format best-effort.

Settled after an independent read of this document (all my calls, change them if they are wrong):

- **Datasets.** Names are upper-cased. A generation data group suffix (`(+1)`, `(0)`, `(-1)`) is
  stripped. A name with a symbolic (`&HLQ..X`, `&SYSUID`) or a temporary one (`&&TEMP`) creates no
  signal. Written means `DISP` starts with `NEW` or `MOD` (or the `DD` is `SYSOUT`/`SORTOUT`-style
  output with `NEW`); read means `SHR` or `OLD`. A dataset written and read inside one application
  links nothing. A dataset read by an application and written by none links nothing.
- **Program calls across applications.** A `CALL`/`LINK`/`PGM=` target is matched by its name,
  case-insensitively and with no prefix or suffix rules (the service-name tokens used for HTTP do
  not apply). If exactly one other application defines the program, the relation is created. If
  several do, no relation is created and the signal is kept as ambiguous. A call to a program the
  application defines itself links nothing.
- **Unresolved calls.** A dynamic `CALL WS-PROG` is stored as a `program` signal with role
  `consumes` and a key that starts with `?` (for example `?WS-PROG`), so it needs no schema flag
  beyond the widened `kind`. It is shown in the application's catalog under integrations, never
  in the relation table and not by `stc query deps` (that would change the return type shared by
  the API, the MCP server and the CLI). Guessing the target from a `MOVE` or a `VALUE`
  is out of scope.
- **Relation types.** One new type per mechanism: `calls`, `batch` (dataset) and `copybook`.
  The same pair of applications may have several relations, one per type, each with its own
  evidence; the global map lists them separately. The acceptance criterion therefore expects the
  `collections -> loans-core` pair to appear as `batch` and as `copybook` relations.
- **Copybooks.** A `COPY` of a copybook found in the same application is ignored. If it is found
  in exactly one other application, the relation is `medium`. If the same name exists in more than
  one application, it is ambiguous and no relation is created (it may be a different layout).
- **DB2 tables.** A table named by DDL (`CREATE TABLE`) in an application gives that application
  the role `provides`; `EXEC SQL` on that table in another application gives `consumes`, which
  creates a directed relation at `medium`. Two applications that use a table with no DDL in
  either create no relation, to avoid noise from common names. The key is the table name,
  upper-cased, with the schema qualifier kept when written.
- **Chunks.** A paragraph is a chunk, except that a run of adjacent paragraphs under about 5 lines
  each are merged into one chunk, and a paragraph over the size limit is cut at blank lines.
  "Falls inside a chunk" in the acceptance criteria means the evidence lines are within one
  chunk's range, not that the range equals the paragraph.
- **Lines and text.** Citation line numbers are the physical lines of the file. The chunk text
  stores the raw lines as they are (sequence numbers in columns 1-6 and trailing text after column
  72 included), so hashes, redaction, secret checks and `read_file` all see the real file; only
  the readers ignore those columns. Tabs count as one column and `\r\n` is accepted.

## Open for the maintainer

Nothing. The layout question is answered: see below.
