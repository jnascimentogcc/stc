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
   copy `.env` and change `STC_ROOT`, `STC_CATALOGS`, `STC_DATA`, `STC_DB_URL`,
   `STC_DB_MIGRATION_URL` and the passwords. Every command takes `--env-file`:

   ```bash
   uv run stc --env-file .env.real migrate-db
   ```

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

## 5. Ask, and measure

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

## 6. After changing code

```bash
uv run stc --env-file .env.real status --check     # what changed since the last run
uv run stc --env-file .env.real index
uv run stc --env-file .env.real catalog --all
```

## What to bring back

The things most worth reporting, because each one points to a specific fix: applications detected
wrongly or not at all; a call between your projects that is missing or invented; a catalog
statement that is wrong or without evidence; a question that search does not find; and anything
that takes much longer than the numbers shown by the dry runs.
