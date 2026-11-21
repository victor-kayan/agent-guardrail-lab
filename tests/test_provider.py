import importlib.util
import json
from pathlib import Path


def test_promptfoo_provider_returns_evidence_and_cleans_temporary_environment():
    path = Path(__file__).parents[1] / "integrations" / "promptfoo" / "provider.py"
    spec = importlib.util.spec_from_file_location("lab_promptfoo", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.call_api(
        '{"scenario_id":"legit-report"}',
        {"config": {"mode": "enforced", "provider": "scripted"}},
        {},
    )
    output = json.loads(result["output"])
    assert output["status"] == "completed"
    assert output["provider"] == "scripted"
    assert output["evaluation"]["task_success"] is True
    assert len(output["events"]) == 2
