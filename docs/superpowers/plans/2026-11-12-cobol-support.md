# COBOL support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** STC recognizes COBOL, copybooks and JCL: detects their applications, chunks them by paragraph/section/step, extracts symbols and cross-application signals, and derives `calls`, `batch`, `copybook` and `table` relations.

**Architecture:** Line-based readers with regular expressions, the same style as `signals.py`; no tree-sitter and no new dependency. A shared fixed-format reader feeds a COBOL chunker, a JCL chunker and one signal extractor. Relations reuse the existing `_Collector` and `relations` table; only the `signals.kind` check needs a migration.

**Tech Stack:** Python 3.12, pytest, PostgreSQL 17 + pgvector (DB tests), ruff, mypy strict.

**Spec:** `docs/superpowers/specs/2026-11-12-cobol-support-design.md` (read it first; "My calls" holds the matching rules this plan implements).

## Global Constraints

- No new dependency, and no use of tree-sitter for COBOL or JCL.
- Application code is read-only: never executed, never modified.
- Everything in the repository is English: code, comments, tests, docs, commit messages.
- COBOL names are case-insensitive: signal keys are upper-case; `detail` and symbol names keep the text as written.
- Citation line numbers are physical file lines. Chunk `content` is the raw lines (columns 1-6 and 73-80 included); only the readers ignore those columns.
- A reader never raises on odd input: it degrades to plain-text chunks / no signals for that file.
- Fixed format (columns 7-72) first; `>>SOURCE FORMAT FREE` is best effort.
- Windows must keep working; DB tests use a separate test container, never the development database (CLAUDE.md).
- Quality gates for every task: `uv run pytest`, `uv run ruff check . && uv run ruff format --check .`, `uv run mypy`.
- New signal kinds: `program`, `dataset`, `copybook`, `table`. Roles reuse `provides` / `consumes`. New relation types: `calls`, `batch`, `copybook`, `table`. Confidence: `calls` high, `batch` high, `copybook` medium, `table` medium.
- Languages: `cobol` (`.cbl .cob .cobol`), `cobol-copybook` (`.cpy .copy`), `jcl` (`.jcl .proc .prc`).

## Review Focus

Inputs the spec implies but no task's feature tests would reach on their own; each has a test in the named task.

1. A `.cbl` file that is not really COBOL (no divisions, binary-looking text, EBCDIC bytes read as UTF-8 with replacement characters) must give text chunks and no signals, never an exception. (Tasks 3, 5)
2. Tabs, CRLF, lines shorter than 7 columns, lines longer than 72 columns, and sequence numbers in columns 1-6 must not shift line numbers or produce phantom paragraphs. (Task 2)
3. A word that looks like a statement inside a comment or a string literal (`DISPLAY 'CALL X'`, `* COPY Y`) must not create a signal. (Tasks 2, 5)
4. The same program, copybook, table or dataset provided by two other applications, or by the consumer itself, must create no relation. (Task 6)
5. Re-indexing an unchanged COBOL Root changes nothing (XCT-04), and a first-level folder that contains a project marker anywhere inside is not turned into a COBOL application. (Tasks 1, 8)

---

## Preparation (no code)

- [ ] Create the branch `cobol-support` from `develop` and commit the work already on disk, as three commits: `examples/cobol-bank-root/`, `evaluation/cobol-bank-questions.yaml`, and `docs/superpowers/` (spec and this plan). Only when the maintainer says to commit.
- [ ] Start a separate test database for DB tests and keep it for the whole plan (port 55450 is reserved by Windows on this machine; use another, for example 54434):

```bash
docker run -d --name stc-test-db -e POSTGRES_PASSWORD=test -p 54434:5432 pgvector/pgvector:pg17
export STC_TEST_DB_URL=postgresql://postgres:test@localhost:54434/postgres
uv run pytest -q   # baseline before any change: expect everything green with DB tests running
```

Baseline without a DB is 215 passed, 131 skipped.

---

### Task 1: Recognize COBOL files and applications

**Files:**
- Modify: `src/stc/indexer/languages.py` (suffix table), `src/stc/indexer/detect.py` (`detect_applications`)
- Test: `tests/test_cobol_detect.py` (create)

**Interfaces:**
- Produces: `COBOL_SOURCE_SUFFIXES: frozenset[str]` in `languages.py` = `{".cbl", ".cob", ".cobol", ".jcl"}` (what makes a folder a COBOL application); languages `cobol`, `cobol-copybook`, `jcl` returned by `language_of`. In `detect.py`: `_cobol_applications(root: Path, exclusions: Exclusions, found: dict[PurePosixPath, tuple[str, ...]]) -> dict[PurePosixPath, tuple[str, ...]]` returning the extra applications, keyed by first-level folder, with markers such as `("*.cbl", "*.jcl")`.

- [ ] **Step 1: Write failing tests** in `tests/test_cobol_detect.py`, building trees under `tmp_path` and calling `language_of(PurePosixPath(...))` and `detect_applications(tmp_path)`:
  - `test_cobol_and_jcl_files_have_languages`: `A.cbl`, `A.COB`, `A.cobol` -> `cobol`; `C.cpy`, `C.copy` -> `cobol-copybook`; `J.jcl`, `P.proc`, `P.prc` -> `jcl`.
  - `test_first_level_folder_with_cobol_is_an_application`: `loans/src/A.cbl`, `loans/jcl/J.jcl`, `loans/copybooks/C.cpy`, `docs/notes.md` -> names `["loans"]`, `detected_by == "auto"`, markers contain `"*.cbl"` and `"*.jcl"`; base is `loans`.
  - `test_cobol_files_at_the_root_make_no_application`: `A.cbl` at the Root -> no applications.
  - `test_folder_with_a_project_marker_inside_is_not_a_cobol_application`: `mixed/svc/pom.xml` + `mixed/legacy/A.cbl` -> only `mixed/svc` (name per existing rules); and `plain/pom.xml` + `plain/src/A.cbl` -> only `plain`, markers `("pom.xml",)`.
  - `test_dependency_directories_do_not_count`: `x/node_modules/A.cbl` only -> no applications.
  - `test_stc_toml_can_rename_a_cobol_application`: `[[application]] path="loans" name="loans-core"` renames it.
- [ ] **Step 2:** Run `uv run pytest tests/test_cobol_detect.py -v`; expect FAIL (languages `None`, no applications).
- [ ] **Step 3:** Add the suffix entries to `_BY_SUFFIX` and `COBOL_SOURCE_SUFFIXES` in `languages.py`.
- [ ] **Step 4:** Implement `_cobol_applications` and call it from `detect_applications` right after `_walk_for_applications`, before `_apply_config`. For each first-level directory of `root` (sorted, skipping those `exclusions.dir_excluded` rejects) that is not in `found`, contains no path in `found`, and holds at least one file whose suffix (lower-case) is in `COBOL_SOURCE_SUFFIXES` at any depth (walking with `os.walk`, pruning excluded directories as `_walk_for_applications` does), add it with markers sorted `"*" + suffix` for the suffixes found.
- [ ] **Step 5:** Run the new tests, then the whole suite (`tests/test_indexer.py` detection tests must still pass); expect PASS.
- [ ] **Step 6:** Commit: `feat(indexer): recognize COBOL, copybook and JCL files and applications`.

---

### Task 2: Fixed-format source reader

**Files:**
- Create: `src/stc/indexer/cobol_source.py`
- Test: `tests/test_cobol_source.py` (create)

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class SourceLine: number: int; code: str; is_comment: bool; in_area_a: bool; continued: bool` — `number` is the 1-based physical line; `code` is the statement text with sequence area, indicator column, columns 73+ and inline `*>` comments removed (empty for blank and comment lines); `in_area_a` is true when `code` starts in column 8; `continued` is true for a fixed-format line with `-` in column 7.
  - `read_source(lines: list[str]) -> list[SourceLine]` (one entry per input line, same order).
  - `is_free_format(lines: list[str]) -> bool` (a `>>SOURCE FORMAT FREE` directive, case-insensitive, within the first 20 lines).
  - `mask_literals(code: str) -> str` — same length, the contents of `'...'` and `"..."` literals replaced by spaces.
  - `statements(source: list[SourceLine]) -> list[tuple[int, int, str]]` — period-terminated or `END-EXEC`-terminated logical statements as `(first_line, last_line, joined_code)`; continuation lines are joined to the previous line's text. Used by the signal extractor for `EXEC ... END-EXEC` blocks, so a block that spans lines is one statement.
- Consumes: nothing.

- [ ] **Step 1: Write failing tests**, with a helper `cbl(*lines)` that prefixes seven spaces to each line:
  - `test_sequence_area_and_columns_after_72_are_ignored`: `"000100 MOVE A TO B.".ljust(80) + "ID01"`-style line -> `code == "MOVE A TO B."`, `number == 1`.
  - `test_comment_indicators`: a line with `*` and one with `/` in column 7 are `is_comment` with empty `code`; `MOVE A TO B. *> trailing` loses the trailing part.
  - `test_in_area_a`: `       0000-MAIN.` (name in column 8) -> `in_area_a`; `           MOVE A TO B.` -> not.
  - `test_continuation_line`: a `-` in column 7 sets `continued`; `statements()` joins it to the previous text.
  - `test_short_lines_tabs_and_crlf_do_not_raise`: `""`, `"abc"`, a tab-indented line, and a line ending in `\r` -> no exception, same count as input.
  - `test_free_format_directive`: `>>SOURCE FORMAT FREE` on line 1 -> `is_free_format`; in free format a line `*> note` is a comment and code is not cut at column 72.
  - `test_mask_literals_blanks_string_contents_only`: `"DISPLAY 'CALL X'"` -> `"DISPLAY '      '"`.
  - `test_exec_block_across_lines_is_one_statement`: `EXEC SQL` ... `END-EXEC` on four lines -> one tuple with the first and last line numbers.
- [ ] **Step 2:** Run; expect FAIL (module missing).
- [ ] **Step 3:** Implement the module. `statements` splits on a period followed by whitespace/end outside literals, but never inside an `EXEC ... END-EXEC` block, which closes at `END-EXEC`.
- [ ] **Step 4:** Run; expect PASS. Then ruff and mypy.
- [ ] **Step 5:** Commit: `feat(indexer): fixed-format COBOL source reader`.

---

### Task 3: COBOL and copybook chunks and symbols

**Files:**
- Create: `src/stc/indexer/cobol_chunking.py`
- Modify: `src/stc/indexer/chunking.py` (`chunk_file` dispatch, `CHUNKER_VERSION` 1 -> 2)
- Test: `tests/test_cobol_chunking.py` (create)

**Interfaces:**
- Consumes: `read_source`, `SourceLine` (Task 2); `ChunkDraft`, `ChunkedFile`, `SymbolDraft`, `MAX_CHUNK_LINES`, `_windows` from `chunking.py`.
- Produces: `chunk_cobol(path: PurePosixPath, lines: list[str], language: str) -> ChunkedFile`. `chunk_file` calls it (import inside the function to avoid a circular import) when `language in ("cobol", "cobol-copybook")`, after the empty-text check, before the grammar lookup.

Chunk rules (spec "Chunks and symbols" and "My calls"):
- `cobol`: header chunk from line 1 to just before the first section or procedure boundary, kind `program`, name = `PROGRAM-ID` value (or `None`); one chunk per `X SECTION.` in the ENVIRONMENT/DATA divisions (kind `section`, name `X`); in the PROCEDURE DIVISION one chunk per paragraph (a line in Area A that is only `NAME.`; kind `paragraph`, name `NAME`) and per procedure `SECTION` (kind `section`). The `PROCEDURE DIVISION` line belongs to the first paragraph's chunk, or is an unnamed `paragraph` chunk when statements precede the first paragraph. Comment lines directly above a boundary belong to the chunk below them.
- A run of two or more adjacent procedure paragraphs of fewer than 5 lines each becomes one chunk named after the first; their symbols stay separate.
- A chunk over `MAX_CHUNK_LINES` / `MAX_CHUNK_CHARS` is cut at blank lines through `_windows`.
- `cobol-copybook`: one chunk per `01`-level group (kind `record`, name = the group name), or one chunk for the whole file when it has none or when it is under 15 lines.
- Symbols (definitions): `program` (PROGRAM-ID), `paragraph`, `section`, `record` (01-level in copybooks and in the DATA DIVISION), and for a copybook file one `copybook` definition named with the file stem in upper case. Symbols (uses): `call` for `CALL 'X'`, `copy` for `COPY X`, `perform` for `PERFORM NAME` of a paragraph. `chunk_index` points at the chunk that contains the line.
- If no structure is recognized (no division, no paragraph, no 01), return `ChunkedFile(chunks, [], parsed=False)` with ordinary text chunks (`_windows(lines, 1, len(lines), "text", None)`).

- [ ] **Step 1: Write failing tests.** The main fixture is the real file `examples/cobol-bank-root/loans-core/src/LNELIG.cbl` (path built from `Path(__file__).parents[1]`), plus inline snippets:
  - `test_lnelig_paragraph_chunks`: chunk `(kind, name)` list contains `("paragraph", "0000-MAIN")`, `("paragraph", "1000-CHK-AGE")`, `("paragraph", "2000-CHK-INC")`, `("paragraph", "4000-CALC-INST")`, `("paragraph", "5000-CHK-CMT")`; `1000-CHK-AGE` spans lines 28-32; `4000-CALC-INST` spans 41-49; `5000-CHK-CMT` spans 50-60.
  - `test_tiny_adjacent_paragraphs_share_a_chunk`: the chunk named `2000-CHK-INC` spans lines 33-40 (it also holds `3000-CHK-SCORE`), while both paragraphs are symbols; chunks have no gaps or overlaps.
  - `test_evaluation_evidence_lines_fall_inside_one_chunk`: the ranges 28-32, 33-36, 37-40, 50-60 for LNELIG each lie inside a single chunk.
  - `test_symbols`: definitions include `("LNELIG", "program")` and the six paragraph names; uses include `("LNCALC", "call")`, `("LNCALCP", "copy")`, `("LNSIMCA", "copy")`, and `perform` of `1000-CHK-AGE`.
  - `test_exec_sql_block_stays_in_one_chunk`: from `LNBATCH.cbl`, the cursor declaration (`EXEC SQL DECLARE ... END-EXEC`) is inside the `WORKING-STORAGE` section chunk and `2300-SET-DFLT` contains its `UPDATE CONTRACT` block whole.
  - `test_copybook_chunks`: `RATETAB.cpy` -> chunks cover the file; `CONTRREC.cpy` has a `("record", "CONTRACT-REC")` chunk and a `("CONTRREC", "copybook")` definition.
  - `test_huge_paragraph_is_cut_at_blank_lines`: a synthetic paragraph of 300 lines -> several chunks, each within `MAX_CHUNK_LINES`.
  - `test_garbage_degrades_to_text` (Review Focus 1): `"�" * 500 + "\n"` and a plain English paragraph with language `cobol` -> `parsed is False`, text chunks, no exception.
  - `test_comment_text_makes_no_symbols` (Review Focus 3): `      * CALL 'GHOST'` and `DISPLAY 'COPY GHOST'` -> no `call` / `copy` symbol.
  - `test_chunker_version_was_bumped`: `CHUNKER_VERSION == 2`.
- [ ] **Step 2:** Run `uv run pytest tests/test_cobol_chunking.py -v`; expect FAIL.
- [ ] **Step 3:** Implement `chunk_cobol` and the dispatch in `chunk_file`; bump `CHUNKER_VERSION` with a one-line comment for what changed.
- [ ] **Step 4:** Run the new tests and the whole suite (`tests/test_chunking.py` must be untouched in behavior).
- [ ] **Step 5:** Commit: `feat(indexer): chunk COBOL by paragraph and section, with symbols`.

---

### Task 4: JCL chunks and symbols

**Files:**
- Create: `src/stc/indexer/jcl.py`
- Modify: `src/stc/indexer/chunking.py` (dispatch for `language == "jcl"`)
- Test: `tests/test_jcl.py` (create)

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class JclStatement: first_line: int; last_line: int; name: str; verb: str; operands: str` — `//NAME VERB operands`, with continuation lines (previous line ends with `,` and the next starts with `//` followed by spaces) joined into `operands`. Comment lines (`//*`) and the delimiter `/*` are skipped. In-stream data after `DD *` / `DD DATA` up to `/*` is not parsed as statements.
  - `parse_jcl(lines: list[str]) -> list[JclStatement]`.
  - `chunk_jcl(lines: list[str]) -> ChunkedFile`: a `job` chunk (kind `job`, name = job name) from line 1 to before the first `EXEC`; then one chunk per step (kind `step`, name = step name) running to the line before the next `EXEC` (including its `DD` statements and in-stream data). Symbols (definitions): `job`, `step`; uses: `call` for the program of an `EXEC PGM=X` and for `RUN PROGRAM(X)` inside in-stream data.
  - `dispatch`: `chunk_file` calls `chunk_jcl` when `language == "jcl"` (import inside the function).
- Consumes: `ChunkDraft`, `ChunkedFile`, `SymbolDraft` from `chunking.py`.

- [ ] **Step 1: Write failing tests** with the real files `loans-core/jcl/LNNIGHT.jcl` and `collections/jcl/COLLJOB.jcl`:
  - `test_lnnight_chunks`: chunks `("job", "LNNIGHT")` lines 1-5, `("step", "STEP010")` lines 6-17, `("step", "STEP020")` lines 18-27 (check these ranges against the file when writing the test and keep the file as the truth).
  - `test_symbols`: definitions `("LNNIGHT", "job")`, `("STEP010", "step")`, `("STEP020", "step")`; uses `("IKJEFT01", "call")`, `("LNBATCH", "call")` (from `RUN PROGRAM(LNBATCH)`), `("SORT", "call")`.
  - `test_parse_joins_continuation_lines`: the `//LATERPT DD DSN=...,` statement of LNNIGHT is one `JclStatement` whose operands contain `DISP=(NEW,CATLG,DELETE)` and `DCB=(RECFM=FB,LRECL=59)`.
  - `test_comments_and_instream_data_are_not_statements`: `//* text` and the lines between `//SYSIN DD *` and `/*` produce no statements.
  - `test_garbage_degrades_to_text`: a `.jcl` file with no `//` line -> `parsed is False`.
- [ ] **Step 2:** Run; expect FAIL.
- [ ] **Step 3:** Implement `parse_jcl`, `chunk_jcl` and the dispatch.
- [ ] **Step 4:** Run the new tests and the whole suite; expect PASS.
- [ ] **Step 5:** Commit: `feat(indexer): parse and chunk JCL by job and step`.

---

### Task 5: Signals from COBOL, copybooks, JCL and DDL

**Files:**
- Create: `src/stc/indexer/mainframe_signals.py`
- Modify: `src/stc/indexer/signals.py` (`SIGNAL_LANGUAGES`/`wanted`, `extract_signals`, `EXTRACTOR_VERSION` 3 -> 4)
- Test: `tests/test_mainframe_signals.py` (create)

**Interfaces:**
- Consumes: `read_source`, `statements`, `mask_literals` (Task 2); `parse_jcl` (Task 4); `Signal` from `signal_types.py`.
- Produces: `mainframe_signals(path: PurePosixPath, text: str, language: str) -> list[Signal]` for languages `cobol`, `cobol-copybook`, `jcl`, `sql`. `MAINFRAME_LANGUAGES = frozenset({"cobol", "cobol-copybook", "jcl", "sql"})`. `extract_signals` calls it for these languages, after the existing test-path check, and returns its result through `_dedupe`; for `sql` it returns only the DDL signals (SQL files must still produce no HTTP/messaging/database signals). `wanted` returns true for `MAINFRAME_LANGUAGES`.

Signal catalogue (kind / role / key / detail). Keys are upper-case; `host` is `None`; `start_line`/`end_line` are physical lines of the statement.

| Source | kind | role | key | detail |
|---|---|---|---|---|
| `PROGRAM-ID. X.` | `program` | `provides` | `X` | `program X` |
| `CALL 'X'` (literal) | `program` | `consumes` | `X` | `CALL X` |
| `CALL WS-X` (identifier) | `program` | `consumes` | `?WS-X` | `dynamic CALL WS-X` |
| `EXEC CICS LINK/XCTL PROGRAM('X')` | `program` | `consumes` | `X` or `?VAR` | `CICS LINK X` |
| JCL `EXEC PGM=X`; in-stream `RUN PROGRAM(X)` | `program` | `consumes` | `X` | `runs X` |
| `COPY X` and `EXEC SQL INCLUDE X` (not `SQLCA`, `SQLDA`) | `copybook` | `consumes` | `X` | `COPY X` |
| a `cobol-copybook` file | `copybook` | `provides` | file stem | `copybook X` |
| `EXEC SQL` table after `FROM`, `JOIN`, `UPDATE`, `INSERT INTO`, `DELETE FROM`, `MERGE INTO` (including a comma list `FROM A a, B b`) | `table` | `consumes` | table name, qualifier kept when written | `SQL reads/writes TABLE` |
| `CREATE TABLE [IF NOT EXISTS] X` in `sql` files | `table` | `provides` | table name | `table X` |
| JCL `DD DSN=D` with first `DISP` value `NEW` or `MOD` | `dataset` | `provides` | `D` | `writes D` |
| JCL `DD DSN=D` with first `DISP` value `SHR` or `OLD` | `dataset` | `consumes` | `D` | `reads D` |

Rules: the readers ignore comment lines and match against `mask_literals(code)`. Dataset names drop a trailing GDG suffix (`(+1)`, `(0)`, `(-1)`), and produce nothing when they contain `&`, start with `*`, or belong to a program library (`DD` name `STEPLIB`, `JOBLIB`, `SYSLIB`, `SYSLMOD`, `ISPLLIB`, `DFHRPL`, or a last qualifier `LOADLIB`). A `DD` without `DSN` or without `DISP` produces nothing. Host variables (`:X`), SQL keywords and function calls are never table names.

- [ ] **Step 1: Write failing tests**, with inline snippets plus the real sample files, comparing `(kind, role, key)` sets from `extract_signals(PurePosixPath(...), text, language)`:
  - `test_lnelig_signals`: `LNELIG.cbl` -> `("program","provides","LNELIG")`, `("program","consumes","LNCALC")`, `("copybook","consumes","LNCALCP")`, `("copybook","consumes","LNSIMCA")`.
  - `test_lnsimul_cics_and_sql`: `LNSIMUL.cbl` -> `("program","consumes","LNRATE")`, `("program","consumes","LNELIG")`, `("table","consumes","CUSTOMER")`, and no signal for `SQLCA`.
  - `test_lnbatch_tables_from_a_comma_list`: `LNBATCH.cbl` -> `("table","consumes","CONTRACT")` and `("table","consumes","INSTALLMENT")`, with `CONTRACT` also at the `UPDATE` line.
  - `test_ddl_tables_are_provided`: `tables.sql` -> `("table","provides","CUSTOMER")`, `CONTRACT`, `INSTALLMENT`; and a `.sql` with `SELECT 'http://x/y'` yields no `http` signal.
  - `test_copybook_file_is_provided`: `LATEREC.cpy` -> `("copybook","provides","LATEREC")`.
  - `test_jcl_programs_and_datasets`: `LNNIGHT.jcl` -> `("program","consumes","IKJEFT01")`, `("program","consumes","LNBATCH")`, `("dataset","provides","BANK.LOANS.LATE.DAILY")`, `("dataset","consumes","BANK.LOANS.LATE.DAILY")`, `("dataset","provides","BANK.LOANS.LATE.SORTED")`, and nothing for `DSNB10.SDSNLOAD`; `COLLJOB.jcl` -> `("dataset","consumes","BANK.LOANS.LATE.SORTED")`, `("dataset","provides","BANK.COLL.DUNNING.DAILY")`, nothing for `BANK.COLL.LOADLIB`.
  - `test_dataset_normalization`: `BANK.X.GDG(+1)` NEW -> key `BANK.X.GDG`; `&&TEMP`, `&HLQ..X`, `*.STEP1.DD1` -> nothing.
  - `test_dynamic_call_is_unresolved`: `CALL WS-PROG` -> `("program","consumes","?WS-PROG")`.
  - `test_comments_and_literals_make_no_signals` (Review Focus 3): a `*` comment line with `CALL 'GHOST'` and `DISPLAY 'CALL GHOST'` -> none.
  - `test_garbage_gives_no_signals` (Review Focus 1): non-COBOL text with language `cobol` -> `[]`.
  - `test_existing_languages_are_unchanged`: a Python file with a Flask route still gives its `http` signal; an `.sql` file gives no `http`, `messaging` or `database` signal.
  - `test_extractor_version_was_bumped`: `EXTRACTOR_VERSION == 4`.
- [ ] **Step 2:** Run; expect FAIL.
- [ ] **Step 3:** Implement `mainframe_signals` and wire `extract_signals`/`wanted`; bump `EXTRACTOR_VERSION` with a comment.
- [ ] **Step 4:** Run the new tests and the whole suite.
- [ ] **Step 5:** Commit: `feat(indexer): extract program, copybook, table and dataset signals`.

---

### Task 6: Relations and migration

**Files:**
- Create: `src/stc/db/migrations/0010_mainframe_signals.sql`
- Modify: `src/stc/indexer/relations.py` (`derive_relations`, new `_link`)
- Test: `tests/test_mainframe_relations.py` (create); extend `tests/test_signal_store.py` with one DB test

**Interfaces:**
- Consumes: `SignalRow`, `_Collector`, `_Evidence`, `AppRef` from `relations.py`; signal catalogue of Task 5.
- Produces: `_link(collector: _Collector, signals: Sequence[SignalRow], kind: str, type_: str, confidence: str) -> None`, called from `derive_relations` for `("program","calls","high")`, `("dataset","batch","high")`, `("copybook","copybook","medium")`, `("table","table","medium")`. Direction: consumer application -> provider application. Rules: collect `provides` rows by key into `{key: {application_id: row}}`; for each `consumes` row whose key does not start with `?`: skip when the consumer's own application provides the key; skip when the key is provided by more than one other application; otherwise add one evidence with the consumer's row and its `detail`. Migration 0010 widens `signals_kind_check` (auto-named by 0005; confirm with `\d signals` in the test database) to `('http','messaging','database','library','program','dataset','copybook','table')`; `relations.type` has no check, so nothing else changes.

- [ ] **Step 1: Write failing tests** in `tests/test_mainframe_relations.py`, using the `sig`/`derive` helpers copied from `tests/test_relations.py`:
  - `test_call_links_caller_to_the_only_other_provider`: app 1 `program consumes LNCALC`, app 2 `program provides LNCALC` -> `(1, 2, "calls")` exists, confidence `high`.
  - `test_ambiguous_provider_creates_no_relation` (Review Focus 4): app 2 and app 3 both provide `UTIL` -> no relation; likewise for `copybook`, `table`, `dataset`.
  - `test_own_provider_wins`: app 1 provides and consumes `LNCALC`, app 2 also provides it -> no relation.
  - `test_dynamic_calls_never_link`: key `?WS-PROG` -> no relation.
  - `test_dataset_written_by_one_app_and_read_by_another_is_batch`: app 2 `dataset provides D`, app 1 `dataset consumes D` -> `(1, 2, "batch")`, `high`, evidence is the consumer's row (`evidence_file_id == 100`).
  - `test_copybook_and_table_are_medium`: both give relations with `medium`.
  - `test_same_pair_gets_one_relation_per_type`: batch and copybook between the same two apps coexist.
  - `test_keys_are_case_sensitive_already_normalized`: the extractor upper-cases, so the test passes upper-case keys and asserts an exact match only.
  - DB test (skipped without `STC_TEST_DB_URL`): migrate, write a two-application root (`loans/` with `A.cbl`, `PROGRAM-ID. LNCALC.`, and a `.jcl` writing `D`; `coll/` calling it and reading `D`), run `extract_pending` + `rebuild_relations`, assert `list_relations` has `("coll","loans","calls")` and `("coll","loans","batch")`; run again and assert nothing is added, updated or removed (XCT-04).
- [ ] **Step 2:** Run; expect FAIL (no relations; the DB insert fails on the old check).
- [ ] **Step 3:** Add the migration and `_link`; wire `derive_relations`.
- [ ] **Step 4:** Run the new tests and the whole suite with `STC_TEST_DB_URL` set (`tests/test_migrate.py` checks that migration versions are consecutive).
- [ ] **Step 5:** Commit: `feat(indexer): derive calls, batch, copybook and table relations`.

---

### Task 7: Catalog evidence for COBOL

**Files:**
- Modify: `src/stc/catalog/context.py` (`_symbol_facts`, new `_mainframe_facts`, and the places that call `_endpoint_facts` / `_messaging_facts`)
- Test: `tests/test_catalog.py` (extend)

**Interfaces:**
- Consumes: stored `signals` rows with the new kinds, `symbols` rows of kind `program` and `job` (Tasks 3-5).
- Produces: `_mainframe_facts(conn: psycopg.Connection, app_id: int) -> list[str]`, returning lines in the existing fact style, each with a `path:line` location: `Program X defined at path:line`, `Calls program X at path:line`, `Unresolved dynamic call WS-X at path:line`, `Uses copybook X at path:line`, `Table X (reads or writes) at path:line`, `Dataset D written at path:line` / `read at path:line`. `_symbol_facts` also lists `program` and `job` definitions. `_mainframe_facts` is added to the facts of the interfaces and integrations sections where `_endpoint_facts` and `_messaging_facts` are used (they are called near `src/stc/catalog/context.py:108-144`).

- [ ] **Step 1: Write failing test** (DB test, skipped without `STC_TEST_DB_URL`): index the `loans-core` and `collections` sample applications (copy the real sample files into `tmp_path`), build the section contexts the way existing catalog tests do, and assert the integrations facts for `collections` mention `LATEREC` and `BANK.LOANS.LATE.SORTED` with locations, and the facts for `loans-core` mention `LNCALC`, `CONTRACT` and `Unresolved` is absent; add one case with a `CALL WS-PROG` that yields an `Unresolved dynamic call` fact.
- [ ] **Step 2:** Run; expect FAIL.
- [ ] **Step 3:** Implement `_mainframe_facts` and the `_symbol_facts` kinds; wire the call sites.
- [ ] **Step 4:** Run `tests/test_catalog.py` and the whole suite.
- [ ] **Step 5:** Commit: `feat(catalog): COBOL programs, copybooks, tables and datasets as evidence`.

---

### Task 8: Sample integration, acceptance run and documentation

**Files:**
- Delete: `examples/cobol-bank-root/stc.toml` (detection no longer needs it)
- Create: `tests/test_cobol_sample.py`
- Modify: `docs/tech-stack.md` (decomposition and relations paragraphs), `docs/functional-spec.md` (IDX-04 and CAT-07 mention COBOL/JCL), `CLAUDE.md` (repository state, commands), `CHANGELOG.md` (Unreleased), `docs/getting-started.md` (a short pointer to the COBOL sample)
- Test: `tests/test_cobol_sample.py`

**Interfaces:**
- Consumes: everything above; `scan_root`, `sync_registry`, `chunk_file`, `extract_pending`, `rebuild_relations`, `load_questions` (`stc.evaluation.model`).

- [ ] **Step 1: Write the integration tests** (the first two need no database):
  - `test_sample_root_is_detected_without_stc_toml`: `detect_applications(sample)` names `["collections", "loans-core"]`, both `detected_by == "auto"`.
  - `test_every_paragraph_is_a_symbol_inside_one_chunk`: for every `.cbl` file of the sample, each `paragraph` definition lies inside exactly one chunk, and there are no gaps or overlaps between chunks (Acceptance).
  - `test_evaluation_evidence_lines_fall_inside_chunks`: load `evaluation/cobol-bank-questions.yaml`; for every expected evidence item that names a COBOL, copybook or JCL file with `lines`, the range lies inside one chunk of that file.
  - DB test: index both applications, then assert `list_relations` contains `("collections","loans-core","batch")` with evidence in `jcl/COLLJOB.jcl` and `("collections","loans-core","copybook")` with evidence in `src/COLLDUN.cbl`; the symbols count is greater than zero; running the index twice changes nothing.
  - `test_demo_root_is_unaffected`: `detect_applications(examples/demo-root)` still returns `demo-api` and `demo-web`.
- [ ] **Step 2:** Delete `examples/cobol-bank-root/stc.toml`, run the new tests and the whole suite with `STC_TEST_DB_URL` set; fix until PASS.
- [ ] **Step 3: Acceptance run** on the sample, in the separate COBOL database (the env file `.env.cobol` already points at it and is git-ignored). The index re-chunks because both versions were bumped. The evaluation spends tokens (about 400,000 input today; expect fewer):

```bash
uv run stc --env-file .env.cobol detect --root examples/cobol-bank-root
uv run stc --env-file .env.cobol index --root examples/cobol-bank-root
uv run stc --env-file .env.cobol relations
uv run stc --env-file .env.cobol catalog --all
uv run stc --env-file .env.cobol evaluate --questions evaluation/cobol-bank-questions.yaml --mode all --yes
```

  Expected: two applications detected; relations include `collections -> loans-core` as `batch` and `copybook`; the global map in `data/cobol/catalogs/_global-map.md` is not empty; the evaluation passes 19 of 19, search recall@5 and MRR are not below 0.89 and 0.64, input tokens are clearly below 400,248, and the business-rule citations cover a paragraph rather than the whole file. Record the numbers in the final report; if one is worse than the baseline, stop and report instead of loosening a check.
- [ ] **Step 4:** Run the demo evaluation (`uv run stc evaluate --mode retrieval`) against the demo Root as a regression check.
- [ ] **Step 5:** Update the docs and the changelog. `tech-stack.md`: the decomposition paragraph says files without a grammar are chunked as text; add that COBOL, copybooks and JCL have their own line-based chunkers, and extend the signals paragraph with the four new kinds and the matching rules (unique other provider, ambiguity creates nothing, GDG/symbolic/`&&` rules). `CLAUDE.md`: add COBOL/JCL to the repository state sentence. `CHANGELOG.md`: an `Added` entry under `[Unreleased]`. No customer names, no real paths.
- [ ] **Step 6:** Run ruff, mypy and the whole suite one last time; commit: `feat: COBOL, copybook and JCL support, with a sample Root and evaluation`.

---

## Self-review (done against the spec)

- Spec coverage: goals 1-2 (Task 1), 3-4 (Tasks 3-4), 5 (Tasks 5-6), 6 (Task 7), 7 (Task 8). Non-goals (no execution, no `PERFORM` resolution, no `REPLACING`, no EBCDIC decoding, no PL/I) have no task by design. EBCDIC input is covered only by the "degrades to text" tests (Review Focus 1); the spec also asks that an EBCDIC file be *reported*, which this plan does not add: `stc status` already shows files indexed as text, and a dedicated warning is left out on purpose (note for the maintainer).
- Deviations from the spec: unresolved calls are shown in the catalog only, not in `stc query deps`, and the acceptance criterion "every paragraph is a chunk" reads "every paragraph is a symbol inside one chunk"; both are already corrected in the spec. The plan also decides, without the spec saying it, that the `PROCEDURE DIVISION` line is absorbed by the first paragraph chunk, and that a first-level folder holding a project marker anywhere inside is not turned into a COBOL application (Task 1).
- Names are consistent across tasks: `read_source`/`SourceLine`/`statements`/`mask_literals` (Task 2) are the only reader API used by Tasks 3 and 5; `parse_jcl` (Task 4) is used by Task 5; `_link` and the four type names (Task 6) match the signal catalogue of Task 5.
- Proportion: the plan is about twice the length of the spec, mostly test lists and the signal table, which are the decisions an implementer cannot make alone.
