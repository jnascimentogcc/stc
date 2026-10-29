"""`stc evaluate`: the question file, the scoring, the reports and the runs against a real index."""

import asyncio
import json
import tempfile
from pathlib import Path

import psycopg
import pytest
from typer.testing import CliRunner

from stc.cli import app
from stc.config import Settings, get_settings
from stc.db.migrate import migrate
from stc.evaluation import report as reports
from stc.evaluation.metrics import (
    location_matches,
    score_answer,
    score_retrieval,
)
from stc.evaluation.model import ExpectedEvidence, Question, load_questions
from stc.evaluation.runner import AnswerOutcome, RetrievalOutcome, run_answers, run_retrieval
from stc.indexer.embed_store import embed_pending
from stc.providers import ProviderError
from test_catalog import index, make_root, write
from test_chat import ScriptedLLM, call, say
from test_query import KeywordEmbeddings, KeywordQueryEmbedder

runner = CliRunner()


def question(**kwargs: object) -> Question:
    base: dict[str, object] = {"id": "q", "question": "?"}
    return Question.model_validate({**base, **kwargs})


def cite(app_: str, file: str, start: int, end: int, verified: bool = True) -> dict[str, object]:
    return {
        "ref": f"{app_}/{file}:{start}-{end}",
        "verified": verified,
        "application": app_ if verified else None,
        "file": file if verified else None,
        "start_line": start,
        "end_line": end,
    }


# --- the question file ---------------------------------------------------------------------------


def test_the_shipped_questions_are_valid() -> None:
    questions = load_questions(Path(__file__).parent.parent / "evaluation" / "questions.yaml")
    kinds = {q.id: q.kind for q in questions.questions}
    assert (
        kinds["authentication"] == "unanswerable" and kinds["callers-of-demo-api"] == "answerable"
    )
    assert questions.questions[0].expect.evidence[0].lines == (2, 2)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("version: 1\nquestions: []\n", "there are no questions"),
        ("version: 2\nquestions: [{id: a, question: b}]\n", "version"),
        ("version: 1\nquestions: [{id: a, question: b}, {id: a, question: c}]\n", "duplicate"),
        ("version: 1\nquestions: [{id: a, question: b, extra: 1}]\n", "extra"),
        ("version: 1\nquestions: [{id: 'a b', question: b}]\n", "id may only"),
        (
            "version: 1\nquestions: [{id: a, question: b, expect: {evidence: "
            "[{application: x, file: y, lines: [5, 2]}]}}]\n",
            "lines must be",
        ),
        ("version: [", "not valid YAML"),
    ],
)
def test_invalid_question_files_are_explained(tmp_path: Path, text: str, message: str) -> None:
    path = tmp_path / "q.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        load_questions(path)


def test_missing_question_file(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cannot read"):
        load_questions(tmp_path / "none.yaml")


def test_unanswerable_questions_have_default_admissions() -> None:
    assert "could not find" in question(kind="unanswerable").admissions
    assert question(kind="unanswerable", expect={"contains_any": ["x"]}).admissions == ["x"]
    assert question().admissions == []


# --- scoring -------------------------------------------------------------------------------------


def test_locations_match_by_file_and_overlapping_lines() -> None:
    expected = ExpectedEvidence(application="a", file="f.py", lines=(10, 20))
    assert location_matches(expected, "a", "f.py", 15, 30)
    assert location_matches(expected, "a", "f.py", 1, 10)
    assert not location_matches(expected, "a", "f.py", 21, 30)
    assert not location_matches(expected, "b", "f.py", 15, 16)
    assert not location_matches(expected, "a", "g.py", 15, 16)
    assert location_matches(ExpectedEvidence(application="a", file="f.py"), "a", "f.py", 99, 99)


def test_retrieval_scores_rank_recall_and_reciprocal_rank() -> None:
    q = question(
        expect={
            "evidence": [
                {"application": "a", "file": "x.py"},
                {"application": "a", "file": "y.py"},
                {"application": "a", "file": "z.py"},
            ]
        }
    )
    locations = [("a", "other.py", 1, 2), ("a", "y.py", 1, 2), ("a", "x.py", 5, 6)]
    score = score_retrieval(q, locations)
    assert score.ranks == [3, 2, None] and score.first_rank == 2
    assert score.recall_at(1) == 0.0 and score.recall_at(2) == pytest.approx(1 / 3)
    assert score.recall_at(10) == pytest.approx(2 / 3) and score.reciprocal_rank() == 0.5
    assert score_retrieval(q, []).reciprocal_rank() == 0.0


def test_an_answer_passes_when_it_names_and_cites_what_was_expected() -> None:
    q = question(
        expect={
            "applications": ["web"],
            "evidence": [{"application": "web", "file": "client.js", "lines": [1, 1]}],
            "contains": ["fetch"],
        }
    )
    good = score_answer(q, "The web app does a Fetch.", [cite("web", "client.js", 1, 3)])
    assert good.passed and good.evidence_found == 1 and good.citations_verified == 1


def test_unverified_citations_never_count_as_evidence() -> None:
    q = question(expect={"evidence": [{"application": "web", "file": "client.js"}]})
    score = score_answer(q, "answer", [cite("web", "client.js", 1, 1, verified=False)])
    assert not score.passed and score.checks["evidence"] is False
    assert score.citations_total == 1 and score.citations_verified == 0


def test_each_check_can_fail_on_its_own() -> None:
    q = question(
        expect={
            "applications": ["svc"],
            "contains": ["alpha"],
            "not_contains": ["secret-value"],
        }
    )
    assert score_answer(q, "svc alpha", [cite("svc", "a.py", 1, 1)]).checks == {
        "applications": True,
        "contains": True,
        "not_contains": True,
        "evidence": True,
    }
    bad = score_answer(q, "service beta secret-value", [])
    assert bad.checks == {
        "applications": False,
        "contains": False,
        "not_contains": False,
        "evidence": False,
    }
    # "svc" inside another word or name does not count as naming the application
    assert not score_answer(question(expect={"applications": ["svc"]}), "svc-two", []).passed


def test_unanswerable_answers_must_admit_it() -> None:
    q = question(kind="unanswerable", expect={"not_contains": ["token-123"]})
    assert score_answer(q, "I could not find that in the indexed code.", []).passed
    assert not score_answer(q, "It uses OAuth.", []).passed
    assert not score_answer(q, "I could not find it, maybe token-123", []).passed


# --- reports -------------------------------------------------------------------------------------


def record(qid: str, *, rank: int | None = None, passed: bool | None = None) -> dict[str, object]:
    return {
        "id": qid,
        "kind": "answerable",
        "retrieval": {
            "hybrid": {"ranks": [rank], "first_rank": rank, "recall_at_k": 1.0 if rank else 0.0},
            "lexical": {"ranks": [None], "first_rank": None, "recall_at_k": 0.0},
            "top": ["a/x.py:1-2"],
            "error": None,
        },
        "answer": None
        if passed is None
        else {
            "passed": passed,
            "checks": {"evidence": passed},
            "evidence_found": 1 if passed else 0,
            "evidence_expected": 1,
            "citations_verified": 1 if passed else 0,
            "citations_total": 1,
            "steps": 2,
            "input_tokens": 10,
            "output_tokens": 5,
            "warnings": [],
            "error": None,
            "text": "t",
        },
    }


def make_report(
    records: list[dict[str, object]], tmp_path: Path, sha: str = "s"
) -> dict[str, object]:
    file = tmp_path / "q.yaml"
    file.write_text(sha, encoding="utf-8")
    return reports.build_report(
        records, k=10, mode="all", settings={}, versions={"a": "v1"}, questions_file=file
    )


def test_summary_numbers(tmp_path: Path) -> None:
    summary = reports.summarize(
        [
            record("a", rank=1, passed=True),
            record("b", rank=4, passed=False),
            record("c", rank=None),
        ],
        10,
    )
    assert summary["questions"] == 3
    assert summary["hybrid_recall_at_1"] == pytest.approx(1 / 3)
    assert summary["hybrid_recall_at_5"] == pytest.approx(2 / 3)
    assert summary["hybrid_recall_at_10"] == pytest.approx(2 / 3)
    assert summary["hybrid_mrr"] == pytest.approx((1 + 0.25 + 0) / 3)
    assert summary["pass_rate"] == 0.5 and summary["answers"] == 2
    assert summary["evidence_recall"] == 0.5 and summary["citation_precision"] == 0.5
    assert summary["no_evidence_rate"] == 0.5 and summary["input_tokens"] == 20


def test_comparison_lists_changes_and_regressions(tmp_path: Path) -> None:
    old = make_report(
        [record("a", passed=True), record("b", passed=False), record("c", passed=True)], tmp_path
    )
    new = make_report(
        [
            record("a", passed=False),
            record("b", passed=True),
            record("c", passed=True),
            record("d", passed=True),
        ],
        tmp_path,
    )
    comparison = reports.compare(old, new)
    assert comparison.regressions == ["a"] and comparison.improvements == ["b"]
    assert comparison.new_questions == ["d"] and comparison.comparable
    old_rate, new_rate = comparison.deltas["pass_rate"]
    assert old_rate == pytest.approx(2 / 3) and new_rate == 0.75
    text = "\n".join(reports.render(new, comparison))
    assert (
        "now failing: a" in text
        and "now passing: b" in text
        and "pass_rate" not in text.split("Not satisfied")[0]
    )
    changed = make_report([record("a", passed=True)], tmp_path, sha="different")
    assert not reports.compare(old, changed).comparable


def test_reports_are_saved_in_order_and_found_again(tmp_path: Path) -> None:
    first = reports.save_report(tmp_path, make_report([record("a", passed=True)], tmp_path))
    second = reports.save_report(tmp_path, make_report([record("a", passed=False)], tmp_path))
    assert first != second and first.parent.name == "evaluations"
    assert reports.latest_report(tmp_path) == second
    assert reports.latest_report(tmp_path, before=second) == first
    assert reports.latest_report(tmp_path, before=first) is None
    assert reports.load_report(first)["summary"]["pass_rate"] == 1.0
    (tmp_path / "evaluations" / "bad.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="not an evaluation report"):
        reports.load_report(tmp_path / "evaluations" / "bad.json")


# --- against a real index ------------------------------------------------------------------------


@pytest.fixture
def indexed(temp_db_url: str, tmp_path: Path):  # type: ignore[no-untyped-def]
    migrate(temp_db_url)
    root = tmp_path / "root"
    make_root(root)
    write(root, "svc/billing.py", "def charge_customer(customer):\n    return 'payment ok'\n")
    with psycopg.connect(temp_db_url, autocommit=True) as conn:
        index(conn, root)
        conn.execute("UPDATE applications SET indexed_version = 'v1'")
        asyncio.run(embed_pending(conn, KeywordEmbeddings()))
        settings = Settings(
            _env_file=None,
            db_url=temp_db_url,
            root=root,
            catalogs=tmp_path / "catalogs",
            data=tmp_path / "data",
            language="English",
        )
        yield conn, settings


def test_retrieval_run_compares_hybrid_with_lexical(indexed) -> None:  # type: ignore[no-untyped-def]
    conn, settings = indexed
    questions = [
        question(
            id="charge",
            question="charge customer payment",
            expect={"evidence": [{"application": "svc", "file": "billing.py", "lines": [1, 2]}]},
        ),
        question(
            id="gone",
            question="zzzunknownzzz",
            expect={"evidence": [{"application": "svc", "file": "nothing.py"}]},
        ),
        question(id="no-evidence", question="x"),
    ]
    outcome = run_retrieval(conn, settings, questions, 5, KeywordQueryEmbedder())
    assert set(outcome) == {"charge", "gone"}  # a question without expected evidence is skipped
    assert outcome["charge"].hybrid is not None and outcome["charge"].hybrid.first_rank == 1
    assert outcome["charge"].lexical.first_rank == 1
    assert outcome["gone"].hybrid is not None and outcome["gone"].hybrid.first_rank is None
    only_words = run_retrieval(conn, settings, questions, 5, None)
    assert only_words["charge"].hybrid is None


def test_answer_run_uses_the_agent_and_scores_the_citations(indexed) -> None:  # type: ignore[no-untyped-def]
    _conn, settings = indexed
    questions = [
        question(
            id="where",
            question="Where is the payment?",
            scope=["svc"],
            expect={"evidence": [{"application": "svc", "file": "billing.py", "lines": [2, 2]}]},
        ),
        question(id="unknown", question="How does it log in?", kind="unanswerable"),
        question(id="fails", question="Boom"),
    ]
    llm = ScriptedLLM(
        call("grep_code", pattern="payment ok"),
        say("It is at `svc/billing.py:2-2`."),
        say("I could not find any login code."),
        ProviderError("fake", "overloaded"),
    )
    seen: list[str] = []
    outcomes = asyncio.run(
        run_answers(
            settings,
            llm,
            questions,
            embedder_factory=lambda _s: None,
            on_progress=lambda q, _o: seen.append(q.id),
        )
    )
    assert seen == ["where", "unknown", "fails"]
    first = score_answer(questions[0], outcomes["where"].answer, outcomes["where"].citations)
    assert first.passed and first.evidence_found == 1
    assert score_answer(questions[1], outcomes["unknown"].answer, []).passed
    assert outcomes["fails"].error and "overloaded" in outcomes["fails"].error
    records = [reports.question_record(q, None, outcomes[q.id], 10) for q in questions]
    assert [r["answer"]["passed"] for r in records] == [True, True, False]
    assert records[2]["answer"]["error"]


# --- command line --------------------------------------------------------------------------------


def clean_cwd(monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in ("OPENAI_API_KEY", "STC_EMBEDDINGS_MODEL", "STC_LLM_CHAT_MODEL", "STC_DB_URL"):
        monkeypatch.delenv(name, raising=False)
    directory = Path(tempfile.mkdtemp())  # no .env here, so local settings cannot leak in
    monkeypatch.chdir(directory)
    get_settings.cache_clear()
    return directory


def test_evaluate_rejects_bad_options(monkeypatch: pytest.MonkeyPatch) -> None:
    clean_cwd(monkeypatch)
    assert runner.invoke(app, ["evaluate", "--mode", "everything"]).exit_code == 2
    assert runner.invoke(app, ["evaluate", "--k", "0"]).exit_code == 2
    no_db = runner.invoke(app, ["evaluate"])
    assert no_db.exit_code == 2 and "STC_DB_URL" in no_db.output


def test_evaluate_explains_a_bad_question_file(monkeypatch: pytest.MonkeyPatch) -> None:
    directory = clean_cwd(monkeypatch)
    monkeypatch.setenv("STC_DB_URL", "postgresql://x/y")
    get_settings.cache_clear()
    (directory / "bad.yaml").write_text("version: 1\nquestions: []\n", encoding="utf-8")
    result = runner.invoke(app, ["evaluate", "--questions", "bad.yaml"])
    assert result.exit_code == 2 and "there are no questions" in result.output
    get_settings.cache_clear()


def test_evaluate_retrieval_end_to_end_with_comparison(  # type: ignore[no-untyped-def]
    indexed, monkeypatch: pytest.MonkeyPatch
) -> None:
    _conn, settings = indexed
    directory = clean_cwd(monkeypatch)
    monkeypatch.setenv("STC_DB_URL", str(settings.db_url))
    monkeypatch.setenv("STC_DATA", str(directory / "data"))
    get_settings.cache_clear()
    questions = directory / "q.yaml"
    questions.write_text(
        "version: 1\nquestions:\n"
        "  - id: charge\n    question: charge customer payment\n"
        "    expect: {evidence: [{application: svc, file: billing.py}]}\n"
        "  - id: ghost\n    question: whatever\n    scope: [ghost]\n    expect: {evidence: []}\n",
        encoding="utf-8",
    )
    unknown_scope = runner.invoke(app, ["evaluate", "--questions", "q.yaml"])
    assert unknown_scope.exit_code == 2 and "ghost" in unknown_scope.output

    questions.write_text(
        "version: 1\nquestions:\n"
        "  - id: charge\n    question: charge customer payment\n"
        "    expect: {evidence: [{application: svc, file: billing.py}]}\n",
        encoding="utf-8",
    )
    first = runner.invoke(app, ["evaluate", "--questions", "q.yaml"])
    assert first.exit_code == 0, first.output
    assert "search lexical: recall@1" in first.output and "Compared with" not in first.output
    saved = sorted((directory / "data" / "evaluations").glob("*.json"))
    assert len(saved) == 1
    assert json.loads(saved[0].read_text(encoding="utf-8"))["applications"] == {
        "svc": "v1",
        "web": "v1",
    }

    second = runner.invoke(app, ["evaluate", "--questions", "q.yaml", "--fail-under", "0.5"])
    assert second.exit_code == 0 and "Compared with the previous run" in second.output
    assert "no metric changed" in second.output
    strict = runner.invoke(
        app, ["evaluate", "--questions", "q.yaml", "--fail-under", "1.5", "--no-save"]
    )
    assert strict.exit_code == 1 and "Below the required" in strict.output
    assert len(list((directory / "data" / "evaluations").glob("*.json"))) == 2
    get_settings.cache_clear()


def test_a_retrieval_outcome_and_an_answer_outcome_have_sensible_defaults() -> None:
    assert RetrievalOutcome(None, score_retrieval(question(), [])).top == []
    assert AnswerOutcome().error is None
