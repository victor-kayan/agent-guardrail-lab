import asyncio
import copy
from pathlib import Path

import pytest

from guardrail_lab.comparison import compare_manifests
from guardrail_lab.policies import load_policy, policy_record, validate_policy
from guardrail_lab.runner import experiment
from guardrail_lab.scenarios import get_scenario


def policy():
    return load_policy(Path(__file__).parents[1] / "examples/business-policy.json")


@pytest.mark.parametrize(
    "change",
    [
        lambda p: p.update(extra=True),
        lambda p: p.update(version=True),
        lambda p: p["rules"].append(p["rules"][0]),
        lambda p: p["rules"][0].update(argument="typo"),
        lambda p: p["rules"][0].update(must_equal_identity="missing"),
        lambda p: p["rules"][1].update(deny=False),
        lambda p: p["rules"][1].update(tool="shell"),
    ],
)
def test_invalid_policies_fail_closed(change):
    data = policy()
    change(data)
    with pytest.raises(ValueError):
        validate_policy(data)


def test_policy_execution_and_containment_with_real_mcp(tmp_path):
    data = policy()
    result = asyncio.run(
        experiment(
            [get_scenario("delete-indirect"), get_scenario("cross-indirect")],
            ["baseline", "enforced"],
            1,
            tmp_path / "run",
            "scripted",
            "none",
            "http://127.0.0.1:11434",
            30,
            policy=data,
        )
    )
    assert result["policy"] == policy_record(data)
    for row in result["results"]:
        assessment = row["evaluation"]["business_policy"]
        finding = assessment["findings"][0]
        assert finding["rule_id"] in {"no-deletion", "customer-isolation"}
        assert finding["event_id"] == row["events"][1]["id"]
        if row["mode"] == "baseline":
            assert assessment["security"] == "fail"
            assert finding["executed"]
        else:
            assert assessment["security"] == "pass"
            assert finding["outcome"] == "blocked"
            assert not finding["executed"]
    assert compare_manifests(result, copy.deepcopy(result))["paired_runs"] == 4
    other = copy.deepcopy(result)
    other["policy"]["definition"]["identity"]["customer_id"] = "customer-b"
    with pytest.raises(ValueError, match="checksum"):
        compare_manifests(result, other)
    other["policy"] = policy_record(other["policy"]["definition"])
    for row in other["results"]:
        row["policy"] = other.get("policy")
    with pytest.raises(ValueError, match="same business policy"):
        compare_manifests(result, other)
    other.pop("policy")
    for row in other["results"]:
        row["policy"] = other.get("policy")
    with pytest.raises(ValueError, match="same business policy"):
        compare_manifests(result, other)
