# Getting started: run the pipeline on the demo applications

This walks through the whole indexing pipeline (detection, hygiene, chunking, embeddings) on the
two tiny applications in `examples/demo-root/`. Every command is run **from the repository
root**, in any terminal (Git Bash, PowerShell, ...). Only the last step talks to an external
service, and it sends nothing but those demo files.

Requirements: [uv](https://docs.astral.sh/uv/), Docker, and (for step 7) an OpenAI API key.

## 1. Install the dependencies

```bash
uv sync
```

## 2. Start PostgreSQL with pgvector

A throwaway container on port 55432 (change the port if it is taken):

```bash
docker run -d --name stc-dev-db -e POSTGRES_PASSWORD=test -e POSTGRES_DB=stc -p 55432:5432 pgvector/pgvector:pg17
```

Wait a few seconds, then check it is ready (it should say "accepting connections"):

```bash
docker exec stc-dev-db pg_isready -U postgres -d stc
```

## 3. Configure `.env`

Copy `.env.example` to `.env` in the repository root (the commands read it from the current
directory) and set at least these values:

```
STC_DB_MIGRATION_URL=postgresql://postgres:test@localhost:55432/stc
STC_APP_PASSWORD=apppass
STC_DB_URL=postgresql://stc_app:apppass@localhost:55432/stc

OPENAI_API_KEY=<your key>
STC_EMBEDDINGS_MODEL=<an OpenAI embeddings model, e.g. text-embedding-3-small>
STC_LLM_CATALOG_MODEL=<model>
STC_LLM_CHAT_MODEL=<model>
```

`STC_DB_MIGRATION_URL` is the owner of the database (creates the schema); `STC_DB_URL` is the
limited `stc_app` role that the application uses, created for you by the next step.

## 4. Create the schema

```bash
uv run stc migrate-db
```

Expect four `Applied 000N_...` lines (running it again prints `Database is up to date.`).

## 5. Detect and index (no external calls)

```bash
uv run stc detect --root examples/demo-root
```

Expect two applications: `demo-api` (marker `pyproject.toml`) and `demo-web` (`package.json`).

```bash
uv run stc index --root examples/demo-root --skip-embeddings
```

Expect `demo-api` with one **secret** (`credentials.json`, excluded by file name and never
indexed) and, at the end, 7 new chunks. `--skip-embeddings` stops before any external call.

## 6. See what would be embedded (still no external calls)

```bash
uv run stc embed --dry-run
```

Expect `7 chunk(s) to embed ... about 230 tokens (rough estimate)`.

## 7. Embed for real (sends the demo code to OpenAI)

```bash
uv run stc embed
```

Expect `Embedded 7 chunk(s) ...`. It costs a fraction of a cent. Running it again embeds nothing.

Optionally, `uv run stc check-providers` makes one tiny call per configured model to verify keys,
model names and the 1536 embedding dimensions.

## 8. Check the result in the database

```bash
docker exec stc-dev-db psql -U postgres -d stc -c "select provider, model, vector_dims(embedding) as dims, count(*) from chunk_embeddings group by 1,2,3"
```

Expect one row: your provider and model, `1536`, and `7`. To see how the code was split:

```bash
docker exec stc-dev-db psql -U postgres -d stc -c "select f.path, c.kind, c.name, c.start_line, c.end_line from chunks c join files f on f.id = c.file_id order by f.path, c.start_line"
```

## 9. Clean up

```bash
docker rm -f stc-dev-db
```

## Troubleshooting

| Message | Cause |
|---|---|
| `STC_DB_MIGRATION_URL is not set` / `STC_DB_URL is not set` | `.env` is missing or you are not in the repository root. |
| `password authentication failed for user "stc_app"` | `STC_APP_PASSWORD` in `.env` differs from the one used when `migrate-db` ran; run `migrate-db` again. |
| `connection refused` | The container is not running yet or the port in the URLs is wrong. |
| `Embeddings skipped: openai: OPENAI_API_KEY is not set` | Key (or `STC_EMBEDDINGS_MODEL`) missing in `.env`. |
| `expected 1536 dimensions, got N` | The embeddings model does not return 1536 dimensions; use a `text-embedding-3-*` model or set `STC_EMBEDDINGS_DIMENSIONS=1536`. |
