"""Plans, writes and reads the documentation of one application.

The markdown pages in `STC_DOCS/<application>/` are the source of truth (DOC-06); `_meta.json` in
the same folder lists the pages in navigation order and remembers what each one was built from, so
a page is regenerated only when its inputs changed (DOC-07, XCT-04): the facts and evidence it is
made of, the maintainers' corrections, the model, the language or the documentation schema. The
database is only read."""

import hashlib
import json
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from stc.catalog.model import SectionResult
from stc.docs.generate import (
    PART_TASKS,
    SPEC_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    estimate_tokens,
    generate_parts,
)
from stc.docs.model import ORPHANS_PAGE, SCHEMA_VERSION, Page, Part, Plan, Static
from stc.docs.plan import build_plan
from stc.docs.render import (
    NOT_GENERATED,
    NOTES,
    RenderMeta,
    parse_manual_blocks,
    render_claims,
    render_orphans,
    render_page,
)
from stc.indexer.chunk_store import CHUNK_VERSION
from stc.indexer.signal_store import SIGNALS_VERSION
from stc.providers import LLMClient

META_FILE = "_meta.json"
Planner = Callable[[psycopg.Connection, str, int], Plan]
STATIC_PROVIDER, STATIC_MODEL = "none", "static"


def docs_path(base: Path, application: str) -> Path:
    return base / application.replace("/", "--")


@dataclass
class PageMeta:
    path: str
    title: str
    group: str
    inputs_sha256: str
    status: str  # complete | partial | static


@dataclass
class Meta:
    application: str
    code_version: str | None
    generated_at: str
    provider: str
    model: str
    language: str
    status: str
    pages: list[PageMeta] = field(default_factory=list)
    skipped_features: list[str] = field(default_factory=list)
    schema: int = SCHEMA_VERSION


def read_meta(directory: Path) -> Meta | None:
    path = directory / META_FILE
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        pages = [PageMeta(**p) for p in raw.pop("pages", [])]
        return Meta(**raw, pages=pages)
    except (ValueError, TypeError, KeyError):
        return None  # unreadable: treated as not generated yet


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def collect_manual(directory: Path) -> dict[str, str]:
    """The maintainers' blocks of every page of the folder, by block key."""
    found: dict[str, str] = {}
    if directory.is_dir():
        for file in sorted(directory.rglob("*.md")):
            found.update(parse_manual_blocks(file.read_text(encoding="utf-8")))
    return found


def _page_hash(page: Page, manual: dict[str, str], provider: str, model: str, language: str) -> str:
    digest = hashlib.sha256()
    versions = f"{SCHEMA_VERSION}|{CHUNK_VERSION}|{SIGNALS_VERSION}"
    # A page with no narrative part is facts only: the model and the language do not change it.
    writer = f"{provider}|{model}|{language}" if page.parts else "-"
    digest.update(f"{versions}|{writer}|{page.title}|{page.group}\n".encode())
    if page.parts:  # the instructions the model gets are inputs too: a better prompt re-writes
        digest.update(SYSTEM_PROMPT.encode())
        digest.update(SPEC_SYSTEM_PROMPT.encode())
    for block in page.blocks:
        if isinstance(block, Static):
            digest.update(f"s|{block.markdown}\n".encode())
            continue
        digest.update(f"p|{block.id}|{block.style}|{PART_TASKS[block.id]}\n".encode())
        digest.update(f"t|{block.instructions}\n".encode())
        digest.update(f"m|{manual.get(page.key(block.id), '')}\n".encode())
        for fact in block.context.facts:
            digest.update(f"f|{fact}\n".encode())
        for item in block.context.items:
            digest.update(f"i|{item.label}|{item.text}\n".encode())
    digest.update(f"n|{manual.get(page.key(NOTES), '')}\n".encode())
    return digest.hexdigest()


def _valid_keys(plan: Plan) -> set[str]:
    return {page.key(NOTES) for page in plan.pages} | {
        page.key(part.id) for page in plan.pages for part in page.parts
    }


@dataclass
class Prepared:
    plan: Plan
    directory: Path
    manual: dict[str, str]
    hashes: dict[str, str]
    stale: list[Page]
    removed: list[str]  # pages of the previous run that no longer exist
    orphans: dict[str, str]
    previous: Meta | None
    static_only: bool
    provider: str
    model: str
    estimate_input_tokens: int
    estimate_output_tokens: int

    @property
    def up_to_date(self) -> bool:
        return not self.stale and not self.removed and self._orphans_unchanged()

    def _orphans_unchanged(self) -> bool:
        page = self.directory / ORPHANS_PAGE
        if not self.orphans:
            return not page.exists()
        return (
            page.is_file() and parse_manual_blocks(page.read_text(encoding="utf-8")) == self.orphans
        )


@dataclass
class Outcome:
    directory: Path
    status: str
    pages_written: int
    pages_total: int
    claims: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    failed_parts: list[str] = field(default_factory=list)


def prepare(
    conn: psycopg.Connection,
    application: str,
    docs_base: Path,
    *,
    provider: str,
    model: str,
    language: str,
    max_features: int = 15,
    force: bool = False,
    static_only: bool = False,
    planner: Planner = build_plan,
) -> Prepared:
    plan = planner(conn, application, max_features)
    directory = docs_path(docs_base, application)
    previous = read_meta(directory)
    manual = collect_manual(directory)
    if static_only:
        provider, model = STATIC_PROVIDER, STATIC_MODEL
    old = {p.path: p for p in previous.pages} if previous else {}
    hashes = {p.path: _page_hash(p, manual, provider, model, language) for p in plan.pages}
    stale: list[Page] = []
    for page in plan.pages:
        before = old.get(page.path)
        missing = before is None or not (directory / page.path).is_file()
        if static_only:
            keeps_text = before is not None and before.status != "static" and bool(page.parts)
            changed = missing or (
                not keeps_text and before is not None and before.inputs_sha256 != hashes[page.path]
            )  # noqa: E501
        else:
            changed = (
                force
                or missing
                or before is None
                or before.inputs_sha256 != hashes[page.path]
                or before.status != "complete"
            )
        if changed:
            stale.append(page)
    in_plan = {p.path for p in plan.pages}
    removed = [path for path in old if path not in in_plan and path != ORPHANS_PAGE]
    valid = _valid_keys(plan)
    orphans = {k: v for k, v in manual.items() if k not in valid}
    tokens_in, tokens_out = (
        (0, 0) if static_only else estimate_tokens(stale, language, manual, application)
    )
    return Prepared(
        plan, directory, manual, hashes, stale, removed, orphans, previous, static_only,
        provider, model, tokens_in, tokens_out,
    )  # fmt: skip


def _overall(statuses: list[str]) -> str:
    for status in ("partial", "static"):
        if status in statuses:
            return status
    return "complete"


async def generate_and_store(
    llm: LLMClient | None,
    prepared: Prepared,
    *,
    language: str,
    concurrency: int = 4,
) -> Outcome:
    """Write the stale pages (and the notes of removed ones). `llm=None` writes the static parts
    only and marks the narrative as not generated (DOC-10)."""
    plan, directory = prepared.plan, prepared.directory
    results: dict[str, SectionResult] = {}
    if llm is not None and prepared.stale:
        results = await generate_parts(
            llm, prepared.stale, plan.files, plan.application,
            language=language, manual=prepared.manual, concurrency=concurrency,
        )  # fmt: skip
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    render_meta = RenderMeta(plan.code_version, now, prepared.provider, prepared.model, language)
    old = {p.path: p for p in prepared.previous.pages} if prepared.previous else {}
    statuses = {path: meta.status for path, meta in old.items()}
    failed: list[str] = []
    for page in prepared.stale:
        bodies: dict[str, str] = {}
        page_failed = False
        for part in page.parts:
            result = results.get(page.key(part.id))
            if result is None:
                bodies[part.id] = NOT_GENERATED
                continue
            bodies[part.id] = render_claims(result, part.style)
            if result.failed:
                page_failed = True
                failed.append(page.key(part.id))
        status = (
            "partial" if page_failed else ("static" if page.parts and llm is None else "complete")
        )
        statuses[page.path] = status
        text = render_page(page, render_meta, status, bodies, prepared.manual)
        _write_atomic(directory / page.path, text)

    for path in prepared.removed:
        (directory / path).unlink(missing_ok=True)
        statuses.pop(path, None)
        parent = (directory / path).parent
        if parent != directory and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
    orphans_changed = not prepared._orphans_unchanged()
    extra: list[PageMeta] = []
    orphan_file = directory / ORPHANS_PAGE
    if prepared.orphans:
        text = render_orphans(prepared.orphans, render_meta)
        if not orphan_file.is_file() or orphan_file.read_text(encoding="utf-8") != text:
            _write_atomic(orphan_file, text)
        extra.append(PageMeta(ORPHANS_PAGE, "Orphaned notes", "Reference", "", "complete"))
    else:
        orphan_file.unlink(missing_ok=True)

    pages = [
        PageMeta(
            p.path, p.title, p.group, prepared.hashes[p.path], statuses.get(p.path, "complete")
        )
        for p in plan.pages
    ] + extra
    previous = prepared.previous
    changed = bool(prepared.stale or prepared.removed or orphans_changed) or previous is None
    status = _overall([p.status for p in pages])
    if changed:
        meta = Meta(
            plan.application, plan.code_version,
            now if prepared.stale or previous is None else previous.generated_at,
            prepared.provider if prepared.stale or previous is None else previous.provider,
            prepared.model if prepared.stale or previous is None else previous.model,
            language, status, pages, plan.skipped_features,
        )  # fmt: skip
        _write_atomic(directory / META_FILE, json.dumps(asdict(meta), indent=2) + "\n")
    return Outcome(
        directory, status, len(prepared.stale), len(plan.pages),
        claims=sum(len(r.claims) for r in results.values()),
        input_tokens=sum(r.input_tokens for r in results.values()),
        output_tokens=sum(r.output_tokens for r in results.values()),
        failed_parts=sorted(failed),
    )  # fmt: skip


__all__ = [
    "Meta",
    "Outcome",
    "PageMeta",
    "Part",
    "Prepared",
    "docs_path",
    "generate_and_store",
    "prepare",
    "read_meta",
]
