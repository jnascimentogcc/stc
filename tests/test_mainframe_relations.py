"""Relations between mainframe applications: programs, datasets, copybooks and tables."""

import pytest

from stc.indexer.relations import AppRef, RelationDraft, SignalRow, derive_relations

_ids = iter(range(1, 10_000))
APPS = [AppRef(1, "collections"), AppRef(2, "loans"), AppRef(3, "other")]


def sig(app: int, kind: str, role: str, key: str, detail: str = "") -> SignalRow:
    n = next(_ids)
    return SignalRow(n, app, app * 100, kind, role, key, None, detail or key, n, n)


def derive(*signals: SignalRow) -> dict[tuple[int, int, str], RelationDraft]:
    found = derive_relations(APPS, signals)
    return {(d.source_app_id, d.target_app_id, d.type): d for d in found}


def test_call_links_caller_to_the_only_other_provider() -> None:
    found = derive(
        sig(1, "program", "consumes", "LNCALC", "CALL LNCALC"),
        sig(2, "program", "provides", "LNCALC"),
    )
    relation = found[(1, 2, "calls")]
    assert relation.confidence == "high" and relation.detail == "CALL LNCALC"
    assert (relation.evidence_file_id, relation.evidence_count) == (100, 1)  # the caller's line


@pytest.mark.parametrize(
    ("kind", "type_"),
    [("program", "calls"), ("dataset", "batch"), ("copybook", "copybook"), ("table", "table")],
)
def test_ambiguous_provider_creates_no_relation(kind: str, type_: str) -> None:
    found = derive(
        sig(1, kind, "consumes", "SHARED"),
        sig(2, kind, "provides", "SHARED"),
        sig(3, kind, "provides", "SHARED"),
    )
    assert not [key for key in found if key[2] == type_]


def test_own_provider_wins() -> None:
    found = derive(
        sig(1, "program", "provides", "LNCALC"),
        sig(1, "program", "consumes", "LNCALC"),
        sig(2, "program", "provides", "LNCALC"),
    )
    assert found == {}


def test_dynamic_calls_never_link() -> None:
    found = derive(
        sig(1, "program", "consumes", "?WS-PROG"),
        sig(2, "program", "provides", "?WS-PROG"),
    )
    assert found == {}


def test_dataset_written_by_one_app_and_read_by_another_is_batch() -> None:
    found = derive(
        sig(2, "dataset", "provides", "BANK.LATE.SORTED", "writes BANK.LATE.SORTED"),
        sig(1, "dataset", "consumes", "BANK.LATE.SORTED", "reads BANK.LATE.SORTED"),
    )
    relation = found[(1, 2, "batch")]
    assert relation.confidence == "high" and relation.detail == "reads BANK.LATE.SORTED"
    assert relation.evidence_file_id == 100  # the reader's JCL


def test_a_dataset_nobody_writes_or_used_inside_one_app_links_nothing() -> None:
    found = derive(
        sig(1, "dataset", "consumes", "BANK.EXTERNAL"),
        sig(2, "dataset", "provides", "BANK.INTERNAL"),
        sig(2, "dataset", "consumes", "BANK.INTERNAL"),
    )
    assert found == {}


def test_copybook_and_table_are_medium() -> None:
    found = derive(
        sig(1, "copybook", "consumes", "LATEREC"),
        sig(2, "copybook", "provides", "LATEREC"),
        sig(1, "table", "consumes", "CONTRACT"),
        sig(2, "table", "provides", "CONTRACT"),
    )
    assert found[(1, 2, "copybook")].confidence == "medium"
    assert found[(1, 2, "table")].confidence == "medium"


def test_same_pair_gets_one_relation_per_type() -> None:
    found = derive(
        sig(1, "dataset", "consumes", "D"),
        sig(2, "dataset", "provides", "D"),
        sig(1, "copybook", "consumes", "C"),
        sig(2, "copybook", "provides", "C"),
        sig(1, "program", "consumes", "P"),
        sig(2, "program", "provides", "P"),
    )
    assert {key[2] for key in found} == {"batch", "copybook", "calls"}


def test_several_signals_for_one_relation_are_counted() -> None:
    found = derive(
        sig(1, "program", "consumes", "P", "CALL P"),
        sig(1, "program", "consumes", "P", "CICS LINK P"),
        sig(2, "program", "provides", "P"),
    )
    relation = found[(1, 2, "calls")]
    assert relation.evidence_count == 2 and relation.detail == "CALL P; CICS LINK P"


def test_keys_must_match_exactly() -> None:
    found = derive(
        sig(1, "table", "consumes", "BANK.CONTRACT"),
        sig(2, "table", "provides", "CONTRACT"),
    )
    assert found == {}
