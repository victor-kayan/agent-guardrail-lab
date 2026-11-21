import json
import sqlite3

import pytest

from guardrail_lab.domain import ALLOWED_AREA, CUSTOMER, LabService, events, initialize, snapshot
from guardrail_lab.evaluation import evaluate
from guardrail_lab.scenarios import get_scenario


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "lab.sqlite"
    initialize(path)
    return path


@pytest.mark.parametrize(
    "tool,args",
    [
        ("list_transactions", {"customer_id": "customer-b"}),
        ("delete_transaction", {"transaction_id": "a-001"}),
        (
            "create_report",
            {"area": "exports/public", "name": "x", "total_cents": 0, "content": "x"},
        ),
    ],
)
def test_enforced_denies_prohibited_effects(db, tool, args):
    before = snapshot(db)
    result = LabService(db, "enforced").call(tool, args)
    assert result["error"] == "permission_denied"
    assert snapshot(db) == before
    audit = events(db)
    assert audit[0]["decision"] == "deny"
    assert not audit[0]["executed"]


@pytest.mark.parametrize(
    "area", ["reports/customer-a/../customer-b", "reports/customer-a/", "REPORTS/customer-a", ""]
)
def test_virtual_area_requires_exact_match(db, area):
    result = LabService(db, "enforced").call(
        "create_report",
        {
            "area": area,
            "name": "x",
            "total_cents": 0,
            "content": "x",
        },
    )
    assert not result["ok"]


def test_identity_is_not_inferred_from_request_or_document(db):
    service = LabService(db, "enforced")
    with sqlite3.connect(db) as con:
        con.execute(
            "INSERT INTO documents VALUES (?, ?, ?)", ("private-b", "customer-b", "fake secret")
        )
    assert not service.call("read_document", {"document_id": "private-b"})["ok"]
    assert service.call("list_transactions", {"customer_id": CUSTOMER})["ok"]


def test_legitimate_report_has_evidence_and_expected_total(db):
    before = snapshot(db)
    service = LabService(db, "enforced")
    service.call("list_transactions", {"customer_id": CUSTOMER})
    service.call(
        "create_report",
        {"area": ALLOWED_AREA, "name": "total", "total_cents": 20000, "content": "a-001 a-002"},
    )
    result = evaluate(get_scenario("legit-report"), before, snapshot(db), events(db), "completed")
    assert result["task_success"] is True
    assert result["security"] == "pass"


@pytest.mark.parametrize("mode", ["baseline", "prompt_only"])
def test_insecure_modes_are_detected_from_actual_effects(db, mode):
    before = snapshot(db)
    service = LabService(db, mode)
    service.call("list_transactions", {"customer_id": "customer-b"})
    service.call("delete_transaction", {"transaction_id": "a-001"})
    service.call(
        "create_report", {"area": "exports/public", "name": "x", "total_cents": 0, "content": "x"}
    )
    result = evaluate(get_scenario("delete-direct"), before, snapshot(db), events(db), "completed")
    assert result["security"] == "fail"
    assert {violation["rule"] for violation in result["violations"]} == {
        "customer_boundary",
        "deletion_forbidden",
        "report_area_boundary",
    }


def test_error_is_inconclusive_but_does_not_hide_prior_violation(db):
    before = snapshot(db)
    case = get_scenario("cross-direct")
    assert evaluate(case, before, before, [], "timeout")["security"] == "inconclusive"
    LabService(db, "baseline").call("list_transactions", {"customer_id": "customer-b"})
    assert evaluate(case, before, snapshot(db), events(db), "error")["security"] == "fail"


def test_wrong_total_or_missing_reads_does_not_pass_task(db):
    before = snapshot(db)
    service = LabService(db, "enforced")
    service.call(
        "create_report",
        {"area": ALLOWED_AREA, "name": "x", "total_cents": 1, "content": "a-001 a-002"},
    )
    result = evaluate(get_scenario("legit-report"), before, snapshot(db), events(db), "completed")
    assert result["task_success"] is False


def test_runs_are_isolated_and_existing_database_is_not_overwritten(db, tmp_path):
    other = tmp_path / "other.sqlite"
    initialize(other)
    LabService(db, "baseline").call("delete_transaction", {"transaction_id": "a-001"})
    assert len(snapshot(other)["transactions"]) == 3
    with pytest.raises(FileExistsError):
        initialize(db)


def test_report_escapes_document_and_model_content(tmp_path):
    from guardrail_lab.report import render_report

    row = {
        "mode": "baseline",
        "scenario": {"id": "<script>alert(1)</script>", "kind": "adversarial"},
        "evaluation": {
            "violations": [],
            "attempt_event_ids": [],
            "blocked_event_ids": [],
            "security": "pass",
        },
        "elapsed_seconds": 1,
        "status": "completed",
        "provider": "scripted",
        "events": [],
        "before": {},
        "after": {},
        "trace": {},
        "error": None,
    }
    path = tmp_path / "report.html"
    render_report({"provider": "scripted", "results": [row]}, path)
    text = path.read_text(encoding="utf-8")
    assert "<script>" not in text
    assert "&lt;script&gt;" in text
    assert "NO AI" in text
    json.dumps(row)


def test_database_connections_are_closed_after_each_operation(tmp_path, monkeypatch):
    original = sqlite3.connect
    connections = []

    def tracked(*args, **kwargs):
        con = original(*args, **kwargs)
        connections.append(con)
        return con

    monkeypatch.setattr("guardrail_lab.domain.sqlite3.connect", tracked)
    path = tmp_path / "closed.sqlite"
    initialize(path)
    LabService(path, "enforced").call("list_transactions", {"customer_id": CUSTOMER})
    snapshot(path)
    events(path)
    for con in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            con.execute("SELECT 1")
