import copy
import json
from pathlib import Path

import pytest

from guardrail_lab.comparison import compare_files, compare_manifests
from guardrail_lab.scenarios import get_scenario
from guardrail_lab.suites import fingerprint, fixed_suite_info


def manifest():
    case = get_scenario("legit-report").to_dict()
    return {
        "suite": fixed_suite_info(),
        "provider": "ollama",
        "model": "model-a",
        "parameters": {"repetitions": 1},
        "selection_ids": [case["id"]],
        "selection_modes": ["enforced"],
        "selection_sha256": fingerprint([case]),
        "results": [
            {
                "scenario": case,
                "mode": "enforced",
                "seed": 0,
                "provider": "ollama",
                "before": {"transactions": []},
                "status": "completed",
                "evaluation": {"security": "pass", "task_success": True},
            }
        ],
    }


def test_regressions_are_paired_and_models_can_change():
    old = manifest()
    new = copy.deepcopy(old)
    new["model"] = "model-b"
    new["results"][0]["evaluation"] = {"security": "fail", "task_success": False}
    result = compare_manifests(old, new)
    assert result["paired_runs"] == 1
    assert result["regression_runs"] == 1
    assert result["changed_runs"][0]["changes"] == ["security_regression", "utility_regression"]
    assert result["summaries"]["enforced"]["delta"]["violations"] == 1
    assert "model" in result["configuration_changes"]
    assert result["configurations"]["candidate"]["model"] == "model-b"


def test_execution_error_does_not_count_as_security_improvement():
    old, new = manifest(), manifest()
    old["results"][0]["evaluation"]["security"] = "fail"
    new["results"][0].update(
        status="timeout", evaluation={"security": "inconclusive", "task_success": False}
    )
    result = compare_manifests(old, new)
    assert result["changed_runs"][0]["changes"] == ["execution_regression"]
    assert result["regression_runs"] == 1


@pytest.mark.parametrize(
    "change",
    [
        lambda p: p.update(provider="scripted"),
        lambda p: p["suite"].update(kind="generated"),
        lambda p: p["parameters"].update(repetitions=2),
        lambda p: p["results"][0].update(seed=1),
        lambda p: p["results"].append(copy.deepcopy(p["results"][0])),
        lambda p: p["results"][0]["scenario"].update(prompt="different"),
        lambda p: p["results"][0].update(before={}),
        lambda p: p.update(selection_sha256="wrong"),
        lambda p: p["parameters"].update(repetitions=10**30),
        lambda p: p.update(selection_ids=["missing"]),
    ],
)
def test_incompatible_or_incomplete_experiments_are_rejected(change):
    old, new = manifest(), manifest()
    change(new)
    with pytest.raises(ValueError):
        compare_manifests(old, new)


def test_legacy_published_experiment_can_be_compared_without_modification():
    path = Path(__file__).parents[1] / "examples" / "results" / "experiment.json"
    original = path.read_bytes()
    data = json.loads(original)
    result = compare_manifests(data, data)
    assert result["paired_runs"] == 108
    assert result["regression_runs"] == 0
    assert path.read_bytes() == original


def test_comparison_html_escapes_untrusted_configuration(tmp_path):
    old, new = manifest(), manifest()
    new["model"] = "<script>alert(1)</script>"
    left, right = tmp_path / "left.json", tmp_path / "right.json"
    left.write_text(json.dumps(old), encoding="utf-8")
    right.write_text(json.dumps(new), encoding="utf-8")
    compare_files(left, right, tmp_path / "comparison")
    rendered = (tmp_path / "comparison" / "comparison.html").read_text(encoding="utf-8")
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    with pytest.raises(FileExistsError):
        compare_files(left, right, tmp_path / "comparison")


def test_compare_cli_can_fail_a_regression_check(tmp_path, monkeypatch):
    from guardrail_lab import cli

    monkeypatch.setattr(
        cli, "compare_files", lambda *args: {"paired_runs": 1, "regression_runs": 1}
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "guardrail-lab",
            "compare",
            "before.json",
            "after.json",
            "--output",
            str(tmp_path),
            "--fail-on-regression",
        ],
    )
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 3
