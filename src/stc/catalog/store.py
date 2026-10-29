"""Builds, writes and records the catalog of one application.

The markdown file in `catalogs/` is the source of truth (CAT-04); the database only indexes it
(`catalogs`, `catalog_sections`). A catalog is regenerated only when its inputs changed: the
application's file contents, its relations, the maintainers' corrections, the model, the language
or the catalog schema (XCT-04)."""

import hashlib
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from stc.catalog.context import AppContext, build_context
from stc.catalog.generate import estimate_tokens, generate_sections
from stc.catalog.model import SCHEMA_VERSION, SectionResult
from stc.catalog.render import CatalogMeta, parse_manual_blocks, render_catalog, section_bodies
from stc.providers import LLMClient


def catalog_path(catalogs_dir: Path, application: str) -> Path:
    return catalogs_dir / (application.replace("/", "--") + ".md")


@dataclass
class Prepared:
    context: AppContext
    path: Path
    manual: dict[str, str]
    inputs_sha256: str
    up_to_date: bool
    estimate_input_tokens: int
    estimate_output_tokens: int


@dataclass
class CatalogOutcome:
    path: Path
    status: str  # complete | partial
    claims: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    failed_sections: list[str] = field(default_factory=list)


def prepare(
    conn: psycopg.Connection,
    application: str,
    catalogs_dir: Path,
    *,
    provider: str,
    model: str,
    language: str,
    force: bool = False,
) -> Prepared:
    context = build_context(conn, application)
    path = catalog_path(catalogs_dir, application)
    manual = parse_manual_blocks(path.read_text(encoding="utf-8")) if path.is_file() else {}
    inputs = _inputs_sha256(conn, context.app_id, manual, provider, model, language)
    row = conn.execute(
        "SELECT inputs_sha256, status FROM catalogs WHERE application_id = %s", (context.app_id,)
    ).fetchone()
    up_to_date = (
        not force
        and path.is_file()
        and row is not None
        and row[0] == inputs
        and row[1] == "complete"
    )
    tokens_in, tokens_out = estimate_tokens(context, language, manual)
    return Prepared(context, path, manual, inputs, up_to_date, tokens_in, tokens_out)


def _inputs_sha256(
    conn: psycopg.Connection,
    app_id: int,
    manual: dict[str, str],
    provider: str,
    model: str,
    language: str,
) -> str:
    digest = hashlib.sha256()
    digest.update(f"{SCHEMA_VERSION}|{provider}|{model}|{language}\n".encode())
    for path, sha in conn.execute(
        "SELECT path, COALESCE(content_sha256, '') FROM files WHERE application_id = %s"
        " AND status IN ('indexed', 'unsupported') ORDER BY path",
        (app_id,),
    ):
        digest.update(f"f|{path}|{sha}\n".encode())
    for row in conn.execute(
        "SELECT source_app_id, target_app_id, type, confidence, detail FROM active_relations"
        " WHERE source_app_id = %s OR target_app_id = %s ORDER BY 1, 2, 3",
        (app_id, app_id),
    ):
        digest.update(("r|" + "|".join(str(c) for c in row) + "\n").encode())
    for section in sorted(manual):
        digest.update(f"m|{section}|{manual[section]}\n".encode())
    return digest.hexdigest()


async def generate_and_store(
    conn: psycopg.Connection,
    llm: LLMClient,
    prepared: Prepared,
    *,
    language: str,
    concurrency: int = 4,
) -> CatalogOutcome:
    context = prepared.context
    results = await generate_sections(
        llm, context, language=language, manual=prepared.manual, concurrency=concurrency
    )
    static = {
        "interfaces": _interfaces_markdown(conn, context.app_id),
        "integrations": _integrations_markdown(conn, context.app_id, context.name),
    }
    bodies = section_bodies(results, static)
    failed = sorted(r.id for r in results.values() if r.failed)
    status = "partial" if failed else "complete"
    meta = CatalogMeta(
        context.name,
        context.code_version,
        datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        llm.provider,
        llm.model,
        language,
        status,
    )
    markdown = render_catalog(meta, bodies, prepared.manual)
    _write_atomic(prepared.path, markdown)
    _record(conn, prepared, meta, markdown, bodies)
    return CatalogOutcome(
        prepared.path,
        status,
        claims=sum(len(r.claims) for r in results.values()),
        input_tokens=sum(r.input_tokens for r in results.values()),
        output_tokens=sum(r.output_tokens for r in results.values()),
        failed_sections=failed,
    )


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".md.tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def _record(
    conn: psycopg.Connection,
    prepared: Prepared,
    meta: CatalogMeta,
    markdown: str,
    bodies: dict[str, str],
) -> None:
    app_id = prepared.context.app_id
    with conn.transaction():
        row = conn.execute(
            "INSERT INTO catalogs (application_id, path, content_sha256, code_version, llm_provider,"  # noqa: E501
            " llm_model, language, generated_at, inputs_sha256, status)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, now(), %s, %s)"
            " ON CONFLICT (application_id) DO UPDATE SET path = EXCLUDED.path,"
            " content_sha256 = EXCLUDED.content_sha256, code_version = EXCLUDED.code_version,"
            " llm_provider = EXCLUDED.llm_provider, llm_model = EXCLUDED.llm_model,"
            " language = EXCLUDED.language, generated_at = now(),"
            " inputs_sha256 = EXCLUDED.inputs_sha256, status = EXCLUDED.status RETURNING id",
            (
                app_id,
                prepared.path.name,
                hashlib.sha256(markdown.encode()).hexdigest(),
                meta.code_version,
                meta.provider,
                meta.model,
                meta.language,
                prepared.inputs_sha256,
                meta.status,
            ),
        ).fetchone()
        assert row is not None
        catalog_id = int(row[0])
        conn.execute("DELETE FROM catalog_sections WHERE catalog_id = %s", (catalog_id,))
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO catalog_sections (catalog_id, section, content) VALUES (%s, %s, %s)",
                [(catalog_id, section, body) for section, body in bodies.items()],
            )


# --------------------------------------------------------------------------------------------
# sections built from static analysis
# --------------------------------------------------------------------------------------------


def _cell(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _interfaces_markdown(conn: psycopg.Connection, app_id: int) -> str:
    exposed = conn.execute(
        "SELECT s.detail, f.path, s.start_line FROM signals s JOIN files f ON f.id = s.file_id"
        " WHERE s.application_id = %s AND s.kind = 'http' AND s.role = 'provides'"
        " ORDER BY f.path, s.start_line",
        (app_id,),
    ).fetchall()
    consumed = conn.execute(
        "SELECT s.detail, f.path, s.start_line FROM signals s JOIN files f ON f.id = s.file_id"
        " WHERE s.application_id = %s AND s.kind = 'http' AND s.role = 'consumes'"
        " ORDER BY f.path, s.start_line LIMIT 200",
        (app_id,),
    ).fetchall()
    lines = ["**Exposed endpoints**", ""]
    if exposed:
        lines += ["| Endpoint | Evidence |", "|---|---|"]
        lines += [f"| {_cell(d)} | `{p}:{n}-{n}` |" for d, p, n in exposed]
    else:
        lines.append("_No exposed endpoints were detected._")
    lines += ["", "**Consumed (outgoing HTTP calls)**", ""]
    lines += (
        [f"- {_cell(d)} — evidence: `{p}:{n}-{n}`" for d, p, n in consumed]
        if consumed
        else ["_No outgoing HTTP calls were detected._"]
    )
    return "\n".join(lines)


def _integrations_markdown(conn: psycopg.Connection, app_id: int, name: str) -> str:
    rows = conn.execute(
        "SELECT s.id, s.name, t.id, t.name, r.type, r.confidence, r.detail, fa.name, f.path,"
        " r.evidence_start_line, r.evidence_end_line"
        " FROM active_relations r JOIN applications s ON s.id = r.source_app_id"
        " JOIN applications t ON t.id = r.target_app_id"
        " LEFT JOIN files f ON f.id = r.evidence_file_id"
        " LEFT JOIN applications fa ON fa.id = f.application_id"
        " WHERE r.source_app_id = %s OR r.target_app_id = %s ORDER BY s.name, t.name, r.type",
        (app_id, app_id),
    ).fetchall()

    def evidence(owner: object, path: object, start: object, end: object) -> str:
        if path is None:
            return ""
        prefix = "" if owner == name else f"{owner}/"
        return f" — evidence: `{prefix}{path}:{start}-{end}`"

    outgoing, incoming, shared = [], [], []
    for sid, sname, _tid, tname, kind, conf, detail, owner, path, start, end in rows:
        text = f"({kind}, {conf} confidence) {_cell(detail)}{evidence(owner, path, start, end)}"
        if kind == "shared_database":
            shared.append(f"- **{tname if sid == app_id else sname}** {text}")
        elif sid == app_id:
            outgoing.append(f"- **{tname}** {text}")
        else:
            incoming.append(f"- **{sname}** {text}")

    databases = conn.execute(
        "SELECT DISTINCT s.key FROM signals s WHERE s.application_id = %s AND s.kind = 'database'"
        " ORDER BY 1",
        (app_id,),
    ).fetchall()
    topics = conn.execute(
        "SELECT s.role, s.key, f.path, s.start_line FROM signals s JOIN files f ON f.id = s.file_id"
        " WHERE s.application_id = %s AND s.kind = 'messaging' ORDER BY s.role, s.key",
        (app_id,),
    ).fetchall()

    def block(title: str, items: list[str], empty: str) -> list[str]:
        return [f"**{title}**", "", *(items or [f"_{empty}_"]), ""]

    out: list[str] = []
    out += block("Calls or depends on", outgoing, "No outgoing relations were detected.")
    out += block("Used by", incoming, "No applications were detected using this one.")
    out += block("Shares a database with", shared, "No shared databases were detected.")
    out += block(
        "Databases", [f"- `{k}`" for (k,) in databases], "No database connections were detected."
    )
    out += block(
        "Messaging",
        [
            f"- {role} `{key}` — evidence: `{path}:{line}-{line}`"
            for role, key, path, line in topics
        ],
        "No messaging topics were detected.",
    )
    return "\n".join(out).rstrip()


__all__ = [
    "CatalogOutcome",
    "Prepared",
    "SectionResult",
    "catalog_path",
    "generate_and_store",
    "prepare",
]
