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

Expect six `Applied 000N_...` lines (running it again prints `Database is up to date.`).

## 5. Detect and index (no external calls)

```bash
uv run stc detect --root examples/demo-root
```

Expect two applications: `demo-api` (marker `pyproject.toml`) and `demo-web` (`package.json`).

```bash
uv run stc index --root examples/demo-root --skip-embeddings
```

Expect `demo-api` with one **secret** (`credentials.json`, excluded by file name and never
indexed), 10 new chunks, and a last line such as `Relations: 1 (1 new, 0 updated, 0 removed)`.
`--skip-embeddings` stops before any external call.

The relation comes from `demo-web` calling `http://demo-api/greet/...`, an endpoint that `demo-api`
exposes. List it with its evidence (file and lines of the call):

```bash
uv run stc relations
```

Expect `demo-web -> demo-api  [http, high]` and `evidence: demo-web/src/client.ts:2-2`.

## 6. See what would be embedded (still no external calls)

```bash
uv run stc embed --dry-run
```

Expect `10 chunk(s) to embed ... about 360 tokens (rough estimate)`.

## 7. Embed for real (sends the demo code to OpenAI)

```bash
uv run stc embed
```

Expect `Embedded 10 chunk(s) ...`. It costs a fraction of a cent. Running it again embeds nothing.

Optionally, `uv run stc check-providers` makes one tiny call per configured model to verify keys,
model names and the 1536 embedding dimensions.

## 8. Generate a catalog (optional, sends the demo code to the LLM)

First see what would be sent, without any external call:

```bash
uv run stc catalog demo-api --dry-run
```

Expect `5 of 7 sections have evidence to send to ...` and a rough token estimate. Then generate it
(set `STC_LLM_CATALOG_MODEL` in `.env` first; it costs a few cents at most):

```bash
uv run stc catalog demo-api
```

The result is `catalogs/demo-api.md` in the repository root. Open it: every statement ends with
`evidence: file:start-end` that you can check, endpoints and relations come from the static
analysis, and each section starts with an empty `stc:manual` block where you can write
corrections that survive regeneration. Running the command again says `Up to date` and calls
nothing.

Every `stc catalog` run also rewrites `catalogs/_global-map.md`: the applications, the relation
graph (Mermaid), shared databases, common libraries and applications with no relationships. It is
built from the database without calling the LLM, so you can refresh it any time for free:

```bash
uv run stc map
```

With many applications, `uv run stc catalog --all --dry-run` prints one line per application and
the total estimate; drop `--dry-run` to generate them all (already up-to-date ones are skipped).

## 9. Ask the query engine

The same functions the chat and the MCP server will use are available from the command line
(`--json` prints the structured result, every result lists file and lines):

```bash
uv run stc query apps
uv run stc query deps demo-web
uv run stc query grep greet -i
uv run stc query symbol greet --contains
uv run stc query read demo-api app.py --start 1 --end 8
uv run stc query search "endpoint that greets a person"
uv run stc query read demo-api credentials.json
```

The last one must be refused (`forbidden`): the file holds a fake secret. `search` embeds your
question with the embeddings provider (a tiny call) and combines it with text and name matches;
without embeddings in the database it says so and uses text only.

## 10. Ask a question, as the chat does

Set `STC_LLM_CHAT_MODEL` in `.env` (it can be the same model as the catalog one). If it is a
reasoning model, also set `STC_LLM_CHAT_REASONING_EFFORT=none`: such models refuse tool calls on
the Chat Completions API otherwise (HTTP 400 mentioning `reasoning_effort`). Then:

```bash
uv run stc ask "Which applications call demo-api, and where?"
```

The tool calls the model makes appear on stderr; the answer is printed with citations, and a
`warning:` line lists any citation that could not be matched to evidence retrieved in that turn.
Add `--app demo-web` to restrict the question to one application, or `--profile functional` for
an answer in business language.

The same agent is behind the HTTP API (`uv run uvicorn stc.api.app:app`):

```bash
curl -s -X POST localhost:8000/api/conversations -H 'content-type: application/json' -d '{}'
curl -N -X POST localhost:8000/api/conversations/<id>/messages -H 'content-type: application/json' -d '{"content": "What does demo-api do?"}'
```

The second call streams Server-Sent Events (`tool_call`, `tool_result`, `text`, `done`).

## 11. Use the chat in the browser

Start the API and the web app, each in its own terminal (the API needs `STC_LLM_CHAT_MODEL` and,
for reasoning models, `STC_LLM_CHAT_REASONING_EFFORT=none`, as in step 10):

```bash
uv run uvicorn stc.api.app:app --port 8000
```
```bash
cd frontend && npm install && npm start
```

Open <http://localhost:4200>. Ask "Which applications call demo-api, and where?": the steps the
agent takes appear while it works, then the answer writes itself as the model produces it. Click
a source, or a reference such as `demo-web/src/client.ts:2-2` inside the answer, to see the cited
lines with syntax highlighting; references marked *unverified* (dashed, with a warning) did not
match anything the tools returned, so check them yourself.
Use *Scope* to limit a conversation to some applications and *Answer style* for business
language. The theme follows your system and can be toggled in the header.

## 12. Use it from Claude (MCP)

The MCP server gives a model the same tools you just tried. For Claude Code, from any folder
(replace the path with your clone; `uv run --directory` makes `.env` found):

```bash
claude mcp add stc -- uv run --directory /path/to/stc stc serve-mcp
```

For Claude Desktop, add this to `claude_desktop_config.json` and restart the app:

```json
{
  "mcpServers": {
    "stc": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/stc", "stc", "serve-mcp"]
    }
  }
}
```

Then ask, for example, "Which applications call demo-api, and where?". The model should call
`get_dependencies` and answer with `demo-web/src/client.ts:2-2`. For shared use, set
`STC_MCP_TOKEN` and run `uv run stc serve-mcp --transport http`; clients send
`Authorization: Bearer <token>` to `http://host:8765/mcp`.

## 13. Check the result in the database

```bash
docker exec stc-dev-db psql -U postgres -d stc -c "select provider, model, vector_dims(embedding) as dims, count(*) from chunk_embeddings group by 1,2,3"
```

Expect one row: your provider and model, `1536`, and `10`. To see how the code was split:

```bash
docker exec stc-dev-db psql -U postgres -d stc -c "select f.path, c.kind, c.name, c.start_line, c.end_line from chunks c join files f on f.id = c.file_id order by f.path, c.start_line"
```

## 14. Measure the quality

`evaluation/questions.yaml` holds seven reference questions for the demo (where an endpoint is,
who calls whom, what is withheld...). Search quality needs no model:

```bash
uv run stc evaluate
```

You get recall@1/@5/@10 and MRR of the expected code, for the hybrid search and for words only,
and the run is saved in `data/evaluations/`. To also ask the chat model every question (a few
cents) and check that the answers cite the expected code, admit what they cannot know and do not
leak the secret file:

```bash
uv run stc evaluate --mode all
```

Run it again after changing chunking, embeddings, prompts or models: it compares with the
previous run and lists the questions that started failing or passing.

## 15. Keep it up to date

After you change code in the Root, see what the system thinks needs doing:

```bash
uv run stc status --check
```

It lists the applications that are new or gone and the files added, changed and removed since the
last run, plus catalogs that are missing or older than the code. Then:

```bash
uv run stc index                  # reprocesses only what changed
uv run stc reindex demo-api       # derives one application again, changed or not
uv run stc catalog --all          # regenerates the catalogs whose inputs changed
uv run stc status                 # the last runs, with their outcome
```

## 16. Improve what the system knows

**Uncertain relations.** Static analysis rates some relations low or medium (a call through an
environment variable, a shared topic name). Let the model confirm or reject them; it only judges
what was already found, using the code around the evidence:

```bash
uv run stc relations --confirm --dry-run    # how many, and about how many tokens
uv run stc relations --confirm
uv run stc relations                        # each relation with the verdict and the reason
```

A confirmed relation counts as high confidence; a rejected one disappears from the map, the
catalogs and the queries (and is not judged again). Run `stc catalog --all` afterwards so the
catalogs stop mentioning rejected ones.

**Corrections from the chat.** When an answer is wrong, click *Incorrect* or *Incomplete* and
write what is right. Tick *Also propose this as a correction to an application's catalog* and pick
the application and the section. Nothing changes yet. You review the proposals:

```bash
uv run stc corrections list
uv run stc corrections approve 3 --note "Checked with the team"
uv run stc corrections reject 4
```

Approving writes the text into that section's manual block in `catalogs/<application>.md`: it is
shown first, kept on every regeneration and given to the model as authoritative. Run
`stc catalog <application>` to regenerate the section with it. `stc status` tells you when proposals
are waiting.

## More than one folder

`STC_ROOT` is one folder. To index applications that live in several, list the others in a
`sources.toml` (next to your `.env`, or anywhere with `STC_SOURCES=<file>`):

```toml
[[sources]]
name = "legacy"                 # lower case letters, digits, - and _
path = "D:/code/legacy"         # relative paths are relative to this file
```

`STC_ROOT`, if set, is the source called `default`. Every source is scanned like the Root: it has
its own `stc.toml`, and application names must be unique across sources (rename one in its
`stc.toml`). An application remembers its source, so relations between applications of different
folders work and the chat reads each file from the right place.

```bash
uv run stc detect                      # every source
uv run stc index --source legacy       # only one (also: reindex, status --check, detect)
```

### GitHub

A source can also be a GitHub organization or user. `stc sync` clones its repositories (shallow and
read-only; nothing is ever pushed) into `STC_DATA/sources/<name>/`, and `stc index` then reads that
folder like any other:

```toml
[[sources]]
name = "my-org"
type = "github"
owner = "my-org"                # an organization or a user
include = ["svc-*"]             # optional filters on the repository name
exclude = ["*-archive"]
# exclude_archived = true       # default; exclude_forks = false by default
# branch = "main"               # default: the default branch of each repository
# repositories = ["svc-a"]      # only these, by name
# api_url = "https://github.example.com/api/v3"   # GitHub Enterprise
```

Create a **fine-grained personal access token** (or use a service account) limited to the
organization and the repositories you want, with read-only **Contents** and **Metadata**, and put it
in `.env` as `STC_GITHUB_TOKEN`. If the organization uses SAML SSO the token must be authorized for
it, otherwise the listing comes back empty or denied. Without a token only public repositories can
be read, with a low rate limit.

```bash
uv run stc sync --dry-run              # what would be cloned or updated
uv run stc sync                        # clone or update
uv run stc index --source my-org       # then index what changed
```

For a **user** (not an organization), GitHub lists the private repositories only to that user's own
token: the token must belong to `owner`, and a fine-grained token needs "All repositories" (or the
private ones selected) under its repository access. For someone else's account only public
repositories exist for you.

Each sync resets the clones to the remote, so local changes in them are lost (they are copies).
A repository that stops being listed (archived, renamed, excluded) stays on disk and indexed until
you delete its folder; `stc sync` tells you which ones. The code version of an application is the
commit of its clone.

Docker Compose mounts only `STC_ROOT`; run the `stc` commands on your machine to use more sources.

## 17. Run everything with Docker Compose

Instead of steps 1 to 14 on your machine, the whole system can run in containers: database,
migrations, API, MCP server and the chat UI. In `.env` set `POSTGRES_PASSWORD`,
`STC_APP_PASSWORD` (letters and digits, it goes into a URL), `STC_MCP_TOKEN`, `STC_ROOT` (the
folder with the applications on your machine, for example `./examples/demo-root`) and the
OpenAI key and models. On Linux also set `STC_UID` and `STC_GID` to your user, so `catalogs/`
and `data/` are yours. Then:

```bash
docker compose up -d --build
docker compose run --rm cli index
docker compose run --rm cli catalog --all
```

The chat is at <http://localhost:8080>, the API at <http://localhost:8000> and the MCP server
at <http://localhost:8765/mcp> (send `Authorization: Bearer <STC_MCP_TOKEN>`); all are bound to
`127.0.0.1`. Any `stc` command works through `docker compose run --rm cli <command>`.

## 18. Clean up

```bash
docker rm -f stc-dev-db       # the throwaway database of steps 1 to 14
docker compose down -v        # the Compose setup of step 16 (-v also deletes its database)
```

## Troubleshooting

| Message | Cause |
|---|---|
| `STC_DB_MIGRATION_URL is not set` / `STC_DB_URL is not set` | `.env` is missing or you are not in the repository root. |
| `password authentication failed for user "stc_app"` | `STC_APP_PASSWORD` in `.env` differs from the one used when `migrate-db` ran; run `migrate-db` again. |
| `connection refused` | The container is not running yet or the port in the URLs is wrong. |
| Every command takes minutes before failing or working | The database port is published on `127.0.0.1` only and the URL says `localhost`: on Windows `localhost` is tried on IPv6 first and times out. Use `127.0.0.1` in the URLs. |
| `Embeddings skipped: openai: OPENAI_API_KEY is not set` | Key (or `STC_EMBEDDINGS_MODEL`) missing in `.env`. |
| `expected 1536 dimensions, got N` | The embeddings model does not return 1536 dimensions; use a `text-embedding-3-*` model or set `STC_EMBEDDINGS_DIMENSIONS=1536`. |
