import asyncio

from guardrail_lab.agent import local_url
from guardrail_lab.runner import run_case
from guardrail_lab.scenarios import get_scenario


def test_real_stdio_mcp_round_trip_and_enforcement(tmp_path):
    async def check():
        blocked = await run_case(get_scenario("delete-direct"), "enforced", tmp_path, "scripted")
        vulnerable = await run_case(get_scenario("delete-direct"), "baseline", tmp_path, "scripted")
        assert blocked["status"] == vulnerable["status"] == "completed"
        assert blocked["evaluation"]["security"] == "pass"
        assert blocked["evaluation"]["attempt_event_ids"]
        assert blocked["evaluation"]["blocked_event_ids"]
        assert vulnerable["evaluation"]["security"] == "fail"
        assert len(blocked["trace"]["tool_catalog"]) == 4

    asyncio.run(check())


def test_model_endpoint_rejects_remote_urls():
    import pytest

    for url in (
        "https://api.example.com",
        "http://127.0.0.1.evil.com",
        "http://user@localhost",
        "file:///etc",
    ):
        with pytest.raises(ValueError):
            local_url(url)
    assert local_url("http://127.0.0.1:11434/") == "http://127.0.0.1:11434"


def test_protocol_error_preserves_trace_and_prior_violation(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "guardrail_lab.agent.scripted_calls",
        lambda _: [
            ("delete_transaction", {"transaction_id": "a-001"}),
            ("list_transactions", {"wrong_argument": "customer-a"}),
        ],
    )
    result = asyncio.run(run_case(get_scenario("delete-direct"), "baseline", tmp_path, "scripted"))
    assert result["status"] == "error"
    assert result["evaluation"]["security"] == "fail"
    assert len(result["trace"]["calls"]) == 2
    assert result["trace"]["calls"][-1]["protocol_error"] is True
