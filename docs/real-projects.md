# Trying STC on real projects

A checklist for the first run on code that is not the demo. It keeps the test apart from your
development setup (own database, own Root, own catalogs) and puts the cost in front of you before
every step that spends tokens. Commands are run from the repository root and use a separate
settings file, so nothing touches the demo setup.

## 0. A separate setup

1. **A database of its own.** A throwaway container with a named volume, so the data survives
   restarts but never mixes with another setup. Publish it on `127.0.0.1` and use `127.0.0.1`, not
   `localhost`, in the URLs: on Windows `localhost` is tried on IPv6 first and each attempt can
   wait for a timeout, which makes every command take minutes.

   ```bash
   docker run -d --name stc-real-db -e POSTGRES_PASSWORD=<choose> -e POSTGRES_DB=stc \
     -p 127.0.0.1:55440:5432 -v stc-real-data:/var/lib/postgresql/data pgvector/pgvector:pg17
   ```

2. **A settings file of its own**, for example `.env.real` (git-ignored like every `.env.*`):
   copy `.env.example` and change the paths, the database and the passwords (the table below).
   Every command takes `--env-file` (or `STC_ENV_FILE`); a variable already set in the shell wins
   over the file, and the values are never printed:

   ```bash
   uv run stc --env-file .env.real migrate-db
   ```

   | Variable | What to set for a real run |
   |---|---|
   | `STC_ROOT`, `STC_CATALOGS`, `STC_DOCS`, `STC_SPECS`, `STC_DATA` | Folders of their own, so nothing lands next to the demo. `STC_ROOT` is read-only. More folders and GitHub organizations go in a `sources.toml` (`stc sync`, token in `STC_GITHUB_TOKEN`, read-only) |
   | `STC_DB_URL`, `STC_DB_MIGRATION_URL`, `STC_APP_PASSWORD`, `POSTGRES_PASSWORD` | The database of step 0.1 (`127.0.0.1`, port 55440 above). The migration URL is the owner; the app URL is the `stc_app` role |
   | `STC_LLM_PROVIDER`, `OPENAI_API_KEY` (or `STC_OPENAI_API_KEY`) | OpenAI is the provider available. **Your code is sent to it**: use a provider and an account your organization allows for that code |
   | `STC_LLM_CATALOG_MODEL`, `STC_LLM_CHAT_MODEL`, `STC_LLM_DOCS_MODEL` | One model per job. The docs model falls back to the catalog model. `STC_LLM_CHAT_REASONING_EFFORT` tunes the chat |
   | `STC_EMBEDDINGS_PROVIDER`, `STC_EMBEDDINGS_MODEL`, `STC_EMBEDDINGS_DIMENSIONS` | Changing the model or the dimensions later means embedding everything again |
   | `STC_LANGUAGE` | Language of catalogs, docs, specs and answers (default English) |
   | `STC_CONCURRENCY`, `STC_LLM_REQUESTS_PER_MIN` | Lower both if the provider answers 429 (rate limit) |
   | `STC_SPEC_STACK` | Text file describing the target technology for `stc spec` (also `--stack`) |
   | `STC_MCP_TOKEN`, `STC_MCP_HOST`, `STC_MCP_PORT` | Only for the MCP server over HTTP; the token is required |

   Never put real values in a file that is versioned, and never paste a key in a chat or a log.

3. **A Root with clones, not the originals.** STC only reads, but a clone keeps the experiment
   away from your working copies and carries the Git history (the code version shown next to every
   piece of evidence). One folder, one subfolder per project:

   ```bash
   git clone <path-or-url-of-project-1> <root>/project-1
   git clone <path-or-url-of-project-2> <root>/project-2
   ```

4. **Catalogs in their own Git repository** (`git init` the folder in `STC_CATALOGS`): they are
   generated from your code, so they are private and are versioned apart from this repository.

## 1. What is detected, and what is left out (free, no database)

```bash
uv run stc --env-file .env.real detect
uv run stc --env-file .env.real index --dry-run
```

Check that the applications are the ones you expect, that nothing generated is counted
(`target/`, `build/`, `dist/`, `node_modules/`, minified files), and read the list of files held
back as secrets. If something is wrong, fix it in a `stc.toml` at the Root (rename, merge or split
applications, exclude patterns, `hosts` aliases) and run the dry run again.

## 2. Index (embeddings cost tokens, the estimate is shown first)

```bash
uv run stc --env-file .env.real index
uv run stc --env-file .env.real status
uv run stc --env-file .env.real relations
```

For a front end that calls a back end, `relations` should list the call with its evidence. If it
does not, the usual causes are a host that is not the application's name (add it under `hosts` in
`stc.toml`), a base URL read from configuration (static analysis then matches the path of the
call with the endpoints the other applications expose, and resolves Spring `${property}` prefixes
from `application.properties` or `application.yml`), or a path too generic to say who serves it.

## 3. Relations the analysis is unsure about (a few cents)

```bash
uv run stc --env-file .env.real relations --confirm --dry-run
uv run stc --env-file .env.real relations --confirm
```

Read the reasons the model gives; they show whether it judges from the code or guesses.

## 4. Catalogs

```bash
uv run stc --env-file .env.real catalog --all --dry-run
uv run stc --env-file .env.real catalog --all
```

Open two or three catalogs and check, section by section: do the statements have evidence you can
open and confirm? Are the entities and the configuration the real ones (these sections rely on
heuristics about folder and class names, the likeliest to miss)? Do the gaps list what is really
missing? Write the corrections you find in the `stc:manual` blocks: they are kept on every
regeneration.

## 5. Documentation and migration specifications

```bash
uv run stc --env-file .env.real docs <application> --dry-run
uv run stc --env-file .env.real docs <application>
uv run stc --env-file .env.real spec <application> --stack my-target-stack.txt --dry-run
uv run stc --env-file .env.real spec <application> --stack my-target-stack.txt
```

Both show the pages to write and a token estimate first (`--dry-run` makes no model call;
`--static-only` on `docs` and `spec` writes only what comes from the index). Start with one
application: a specification costs more than a catalog because it writes one page per feature.
The target stack is a free-text file (`--stack` or `STC_SPEC_STACK`); keep it outside this
repository if it names your technology. Check that the features are the ones you expect: for
PHP, routes are found from Laravel, Slim/Lumen and Symfony, and a handler can name its classes
(`Class::method(`, `new Class`); a plain front controller with no router yields no feature.
Statements that cite code the model could not confirm are reported, and each feature page has a
`.spec.json` sidecar (stable IDs) that `stc-tests` reads. Use `-o DIR` to write somewhere else, for
example the new project: a set written to a new folder is generated from scratch.

## 6. Acceptance tests (optional, a separate program)

`stc-tests` drafts scenarios from the sidecars and a Java template runs them in the client's own
CI. It is a separate tool with its own settings (`stc-tests/CLAUDE.md`), and it uses a model too,
so the rule of step 0 applies: send only code you are allowed to send.

```bash
cd stc-tests/tool
uv run stc-tests --env-file ../../.env.real ingest <application> --specs ../../specs --output <tests-folder> --dry-run
uv run stc-tests --env-file ../../.env.real scenarios <application> --catalog ../template/catalog/actions.json
uv run stc-tests review <application>                 # nothing runs until a person approves it
uv run stc-tests approve <application> TC-0001 --by "Your Name"
uv run stc-tests mapping <application>                # interface mapping draft (YAML)
uv run stc-tests approve-mapping <application> <program> --by "Your Name"
```

| Variable | Meaning |
|---|---|
| `STC_TESTS_LLM_MODEL`, `OPENAI_API_KEY`, `OPENAI_BASE_URL` | The scenario writer's model and provider |
| `STC_SPECS`, `STC_TESTS`, `STC_TESTS_CATALOG` | Where the sidecars are read, where drafts and state are written, and the action catalog |

Rules worth knowing: drafts are never approved by the tool (`--by` must be a person); a feature
file that exists is not overwritten (the new draft goes to `<feature>.proposed.feature`); a
mapping with open questions cannot be approved, and it needs the OpenAPI of the target, which the
tool will not invent; legacy code is never executed, COBOL in particular. If the legacy is
executable (for example a PHP HTTP API), the template runs the same scenarios against it first with
`SUBJECT=legacy` (see the template README). The tests repository is the client's, not this one.

## 7. Ask, and measure

```bash
uv run stc --env-file .env.real ask "Which applications call <one of yours>, and where?"
uv run stc --env-file .env.real serve-mcp          # for Claude Code or Claude Desktop
```

Write 10 to 20 questions you already know the answer to, with the file and lines that prove it,
in a file of your own (not in this repository: it names your code), mixing: where something is
implemented, who calls whom, what a configuration key does, a question for a non-technical reader,
and two or three the code cannot answer (the answer must admit it) such as how users log in when
there is no login code.

```bash
uv run stc --env-file .env.real evaluate --questions my-questions.yaml
uv run stc --env-file .env.real evaluate --questions my-questions.yaml --mode all
```

The first line measures search without any model; the second also asks the chat model and checks
the citations. See `evaluation/questions.yaml` for the format. Run it again after every change to
the Root or to the settings: it compares with the previous run.

## 8. After changing code

```bash
uv run stc --env-file .env.real status --check     # what changed since the last run
uv run stc --env-file .env.real index
uv run stc --env-file .env.real catalog --all
uv run stc --env-file .env.real spec <application>  # only the pages whose inputs changed are rewritten
uv run stc-tests ingest <application> --dry-run     # (from stc-tests/tool) new, changed and retired requirements
```

`stc reindex <application>` derives one application again even if nothing changed, for example
after upgrading STC. Corrections proposed from the chat wait for `stc corrections approve <id>`.

## Cleaning up

To start again from zero: remove the database container and its volume (`docker rm -fv
stc-real-db`, `docker volume rm stc-real-data`), then the folders in `STC_CATALOGS`, `STC_DOCS`,
`STC_SPECS` and `STC_DATA`, and the tests folder. Keep the `.env.real` file. Check with `git status`
that nothing generated from your code sits inside this repository.

## What to bring back

The things most worth reporting, because each one points to a specific fix: applications detected
wrongly or not at all; a call between your projects that is missing or invented; a catalog
statement that is wrong or without evidence; a question that search does not find; and anything
that takes much longer than the numbers shown by the dry runs.
