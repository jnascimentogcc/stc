"""Screens in the documentation facts, features and diagrams (Phase 6.3)."""

from pathlib import Path

import psycopg
import pytest

from stc.db.migrate import migrate
from stc.docs import diagrams
from stc.docs import plan as docs_plan
from stc.docs.facts import (
    AppFacts,
    Call,
    Loc,
    Owner,
    Screen,
    ScreenField,
    ScreenUse,
    build_facts,
)
from stc.docs.features import detect_features
from stc.docs.model import Static
from stc.docs.screens import (
    Picture,
    navigation,
    programs_of,
    screens_used_by,
    validation_variables,
)
from stc.indexer.chunk_store import chunk_pending
from stc.indexer.pipeline import host_aliases, scan_root
from stc.indexer.registry import sync_registry
from stc.indexer.signal_store import extract_pending, rebuild_relations

SAMPLE = Path(__file__).parents[1] / "examples" / "cobol-bank-root"


def _field(name: str, var: str | None, mode: str = "input") -> ScreenField:
    return ScreenField(name, mode, "9(5)", None, 4, 10, "Label", var, (), Loc("src/A.cbl", 9))


def _facts() -> AppFacts:
    facts = AppFacts("app", "app", "v1")
    for name in ("MENU", "ENTRY"):
        facts.programs[name] = Loc(f"src/{name}.cbl", 1)
        facts.file_of_program[name] = f"src/{name}.cbl"
    facts.screens["MENU-SCR"] = Screen("MENU-SCR", Loc("src/MENU.cbl", 5))
    facts.screens["ENTRY-SCR"] = Screen(
        "ENTRY-SCR",
        Loc("src/ENTRY.cbl", 5),
        fields=[_field("F1", "WS-A"), _field("F2", None, "output")],
    )
    facts.screen_uses = [
        ScreenUse(Owner("program", "MENU"), "MENU-SCR", "ACCEPT", Loc("src/MENU.cbl", 20)),
        ScreenUse(Owner("program", "ENTRY"), "ENTRY-SCR", "DISPLAY", Loc("src/ENTRY.cbl", 20)),
        ScreenUse(Owner("program", "ENTRY"), "?DYN", "SEND MAP", Loc("src/ENTRY.cbl", 30)),
    ]
    facts.calls = [Call(Owner("program", "MENU"), "ENTRY", False, Loc("src/MENU.cbl", 21))]
    return facts


def test_navigation_follows_calls_between_programs_that_use_screens() -> None:
    facts = _facts()
    steps = navigation(facts)
    assert [(s.source, s.target, s.program, s.callee) for s in steps] == [
        ("MENU-SCR", "ENTRY-SCR", "MENU", "ENTRY")
    ]
    assert navigation(facts, ["ENTRY"]) == []


def test_screens_of_programs_leave_out_run_time_maps() -> None:
    facts = _facts()
    assert screens_used_by(facts, ["MENU", "ENTRY"]) == ["MENU-SCR", "ENTRY-SCR"]
    assert programs_of(facts, "ENTRY-SCR") == ["ENTRY"]
    assert validation_variables(facts, ["ENTRY-SCR"]) == ["WS-A"]


def test_features_carry_their_screens_and_their_files() -> None:
    documented, _ = detect_features(_facts(), 10)
    (feature,) = documented
    assert feature.entry == "MENU" and feature.screens == ["MENU-SCR", "ENTRY-SCR"]
    text = diagrams.feature_diagram(_facts(), feature) or ""
    assert "shows" in text and "ENTRY-SCR" in text and "input" in text
    flow = diagrams.screen_flow_diagram(_facts()) or ""
    assert "MENU calls ENTRY" in flow


def test_screens_tables() -> None:
    table = docs_plan._screens_table(_facts()) or ""
    assert "`ENTRY-SCR`" in table and "`F1`" in table and "`WS-A`" in table
    assert "`MENU-SCR`" in (docs_plan._screens_summary(_facts()) or "")


@pytest.fixture
def conn(temp_db_url: str):  # type: ignore[no-untyped-def]
    migrate(temp_db_url)
    with psycopg.connect(temp_db_url, autocommit=True) as connection:
        sync_registry(connection, scan_root(SAMPLE))
        chunk_pending(connection, SAMPLE)
        extract_pending(connection, SAMPLE)
        rebuild_relations(connection, host_aliases(SAMPLE))
        connection.execute("UPDATE applications SET indexed_version = 'v1'")
        yield connection


def test_teller_sample_screens_in_the_facts_and_pages(conn: psycopg.Connection) -> None:
    app_id = conn.execute("SELECT id FROM applications WHERE name = 'teller'").fetchone()[0]  # type: ignore[index]
    facts = build_facts(conn, app_id, "teller")
    assert set(facts.screens) == {"TRANSFER-SCREEN", "TLRQRY"}
    transfer = facts.screens["TRANSFER-SCREEN"]
    amount = next(f for f in transfer.fields if f.name == "SC-AMOUNT")
    assert (amount.mode, amount.pic, amount.var, amount.label) == (
        "input",
        "9(7)V99",
        "WS-AMOUNT",
        "Amount:",
    )
    assert facts.screens["TLRQRY"].mapset == "TLRSET"
    actions = {(u.owner.name, u.screen, u.action) for u in facts.screen_uses}
    assert ("TLRQRY", "TLRQRY", "SEND MAP") in actions
    assert ("TLRXFER", "TRANSFER-SCREEN", "ACCEPT") in actions

    plan = docs_plan.build_plan(conn, "teller", 10)
    feature = next(p for p in plan.pages if p.path == "features/program-tlrxfer.md")
    rules = next(part for part in feature.parts if part.id == "rules")
    assert any("WS-MAX-TRANSFER" in item.text for item in rules.context.items)
    assert any("Field SC-AMOUNT of screen TRANSFER-SCREEN" in line for line in rules.context.facts)
    statics = " ".join(b.markdown for b in feature.blocks if isinstance(b, Static))
    assert "## Screens" in statics  # no flow section: this feature has a single screen
    interfaces = next(p for p in plan.pages if p.path == "interfaces.md")
    assert "## Screens" in " ".join(b.markdown for b in interfaces.blocks if isinstance(b, Static))


def test_picture_clauses_become_form_rules() -> None:
    from stc.docs.screens import form_rules, parse_picture

    assert parse_picture("9(7)V99") == Picture("number", 9, 2, False)
    assert parse_picture("S9(5)") == Picture("number", 5, 0, True)
    assert parse_picture("X(30)") == Picture("text", 30)
    assert parse_picture("AAA") == Picture("alpha", 3)
    assert parse_picture("-Z(9)9.99") == Picture("number", 12, 2, True)
    assert parse_picture("COMP-3") is None and parse_picture(None) is None
    amount = ScreenField("A", "input", "9(7)V99", None, 1, 1, None, "V", ("required",), Loc("a", 1))
    assert form_rules(amount) == ("decimal", "7 integer digits, 2 decimals; not negative; required")
    pin = ScreenField("P", "input", "X(4)", None, 1, 1, None, "V", ("secure",), Loc("a", 1))
    assert form_rules(pin) == ("password", "up to 4 characters")
    shown = ScreenField("M", "output", "X(40)", None, 1, 1, None, "V", (), Loc("a", 1))
    assert form_rules(shown)[0] == "read-only"


def test_default_stack_gets_a_frontend_only_when_there_are_screens() -> None:
    from stc.spec.stack import DEFAULT_STACK, adapt_default_stack

    assert "Angular" in adapt_default_stack(DEFAULT_STACK, True, True)
    assert "Angular" not in adapt_default_stack(DEFAULT_STACK, True, False)
    mine = "Language: Go."
    assert adapt_default_stack(mine, False, True) == mine


def test_teller_spec_has_the_screen_parts_and_the_forms(conn: psycopg.Connection) -> None:
    from stc.spec.plan import build_spec_plan
    from stc.spec.stack import DEFAULT_STACK

    plan = build_spec_plan(conn, "teller", 10, DEFAULT_STACK)
    page = next(p for p in plan.pages if p.path == "features/program-tlrxfer.md")
    parts = [p.id for p in page.parts]
    assert parts.index("spec-screens") < parts.index("spec-data")
    assert "spec-api" in parts
    tasks = next(p for p in page.parts if p.id == "spec-tasks")
    assert "frontend" in tasks.instructions and "Angular" in tasks.instructions
    screens = next(p for p in page.parts if p.id == "spec-screens")
    assert any("Field SC-AMOUNT" in line for line in screens.context.facts)
    statics = " ".join(b.markdown for b in page.blocks if isinstance(b, Static))
    assert "## Forms to build" in statics and "decimal" in statics and "password" in statics
    index = next(p for p in plan.pages if p.path == "index.md")
    assert "Angular" in " ".join(b.markdown for b in index.blocks if isinstance(b, Static))
