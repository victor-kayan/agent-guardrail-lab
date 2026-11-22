import asyncio
import copy
import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from guardrail_lab.comparison import compare_manifests
from guardrail_lab.runner import experiment, run_case
from guardrail_lab.scenarios import get_scenario


@contextmanager
def external_endpoint(bad_response=False):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            assert self.path == "/step"
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            messages = body["messages"]
            assert not any(m["role"] == "system" for m in messages)
            assert "scenario" not in body and "mode" not in body
            tools_seen = [m.get("tool_name") for m in messages if m["role"] == "tool"]
            if not tools_seen:
                name, arguments = "read_document", {"document_id": "brief-a"}
            elif "delete_transaction" not in tools_seen:
                assert "delete_transaction" in json.dumps(messages[-1])
                name, arguments = "delete_transaction", {"transaction_id": "a-001"}
            else:
                name = None
            message = {"role": "assistant", "content": "Done"}
            if name:
                message["tool_calls"] = [{"function": {"name": name, "arguments": arguments}}]
            if bad_response:
                message = {"role": "system", "content": "Invalid"}
            data = json.dumps({"message": message}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_external_indirect_attack_records_attempt_block_and_effect(tmp_path):
    # Deterministic protocol fixture, not a model robustness measurement.
    async def check(url):
        case = get_scenario("delete-indirect")
        vulnerable = await run_case(case, "baseline", tmp_path, "external", "fixture", url)
        contained = await run_case(case, "enforced", tmp_path, "external", "fixture", url)
        assert vulnerable["status"] == contained["status"] == "completed"
        assert vulnerable["evaluation"]["security"] == "fail"
        assert contained["evaluation"]["security"] == "pass"
        assert vulnerable["evaluation"]["attempt_event_ids"]
        assert contained["evaluation"]["blocked_event_ids"]
        assert len(vulnerable["before"]["transactions"]) == 3
        assert len(vulnerable["after"]["transactions"]) == 2
        assert len(contained["after"]["transactions"]) == 3
        assert vulnerable["events"][0]["tool"] == "read_document"
        assert vulnerable["model"] == "fixture"

    with external_endpoint() as (url, requests):
        asyncio.run(check(url))
    assert len(requests) == 6


def test_external_protocol_error_is_inconclusive(tmp_path):
    with external_endpoint(bad_response=True) as (url, _):
        result = asyncio.run(
            run_case(
                get_scenario("delete-indirect"), "baseline", tmp_path, "external", "fixture", url
            )
        )
    assert result["status"] == "error"
    assert result["evaluation"]["security"] == "inconclusive"
    assert not result["events"]


def test_external_manifest_and_comparison(tmp_path):
    with external_endpoint() as (url, _):
        result = asyncio.run(
            experiment(
                [get_scenario("delete-indirect")],
                ["enforced"],
                1,
                tmp_path / "run",
                "external",
                "fixture",
                url,
                30,
                agent_info={"id": "my-agent", "revision": "v1"},
            )
        )
    assert result["agent"]["revision"] == "v1"
    assert result["prompts"] == {"owner": "external agent"}
    changed = copy.deepcopy(result)
    changed["agent"]["revision"] = "v2"
    comparison = compare_manifests(result, changed)
    assert comparison["paired_runs"] == 1
    assert "agent" in comparison["configuration_changes"]
    assert comparison["configurations"]["candidate"]["agent"]["revision"] == "v2"
    with pytest.raises(ValueError, match="ID and revision"):
        asyncio.run(
            experiment(
                [get_scenario("delete-indirect")],
                ["baseline"],
                1,
                tmp_path / "invalid",
                "external",
                "fixture",
                url,
                30,
            )
        )


def test_external_cannot_claim_lab_prompt_protection(tmp_path):
    with pytest.raises(ValueError, match="own their prompts"):
        asyncio.run(
            experiment(
                [get_scenario("delete-indirect")],
                ["prompt_only"],
                1,
                tmp_path / "invalid",
                "external",
                "fixture",
                "http://127.0.0.1:8770",
                30,
                agent_info={"id": "agent", "revision": "v1"},
            )
        )
    assert not (tmp_path / "invalid").exists()


def test_model_experiments_can_be_compared_across_adapters():
    from test_comparison import manifest

    old = manifest()
    new = copy.deepcopy(old)
    new["provider"] = new["results"][0]["provider"] = "external"
    new["agent"] = {"id": "agent", "revision": "v2"}
    comparison = compare_manifests(old, new)
    assert comparison["paired_runs"] == 1
    assert "provider" in comparison["configuration_changes"]
    old["provider"] = old["results"][0]["provider"] = "scripted"
    with pytest.raises(ValueError, match="scripted"):
        compare_manifests(old, new)
