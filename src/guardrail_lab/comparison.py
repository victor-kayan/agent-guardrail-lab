"""Paired comparisons only when scenarios, seeds, modes and execution settings match."""

import html
import itertools
import json
from pathlib import Path

from guardrail_lab.domain import MODES
from guardrail_lab.scenarios import scenarios
from guardrail_lab.storage import read_json, save_json
from guardrail_lab.suites import fingerprint, fixed_suite_info


def index_experiment(manifest: dict) -> tuple[dict, dict]:
    rows = manifest["results"]
    if not isinstance(rows, list) or not rows:
        raise ValueError("Experiment has no completed result records")
    index, cases = {}, {}
    fixed = {case.id: case.to_dict() for case in scenarios()}
    identity = manifest.get("suite")
    if identity is None:
        if {row["scenario"]["id"] for row in rows} != set(fixed):
            raise ValueError("Legacy comparisons require the full fixed suite")
        identity = fixed_suite_info()
    if identity.get("kind") not in {"fixed", "generated"} or not identity.get("sha256"):
        raise ValueError("Unknown suite identity")
    if identity["kind"] == "fixed" and identity["sha256"] != fixed_suite_info()["sha256"]:
        raise ValueError("Unknown fixed suite version")
    if manifest["provider"] not in {"ollama", "external", "scripted"}:
        raise ValueError("Unknown experiment provider")
    for row in rows:
        case = row["scenario"]
        key = (case["id"], row["mode"], row["seed"])
        if type(row["seed"]) is not int or row["mode"] not in MODES or key in index:
            raise ValueError("Invalid or duplicate scenario/mode/seed record")
        if case["id"] in cases and cases[case["id"]] != case:
            raise ValueError("Scenario changed within an experiment")
        if identity["kind"] == "fixed" and fixed.get(case["id"]) != case:
            raise ValueError("Fixed scenario content does not match fixed-v1")
        if row["provider"] != manifest["provider"]:
            raise ValueError("Mixed providers within an experiment")
        if row["evaluation"]["security"] not in {"pass", "fail", "inconclusive"}:
            raise ValueError("Unknown security verdict")
        if row["status"] not in {"completed", "error", "timeout", "output_limit", "step_limit"}:
            raise ValueError("Unknown execution status")
        if case["kind"] not in {"legitimate", "adversarial"}:
            raise ValueError("Unknown scenario kind")
        utility = row["evaluation"]["task_success"]
        if (case["kind"] == "legitimate" and type(utility) is not bool) or (
            case["kind"] == "adversarial" and utility is not None
        ):
            raise ValueError("Invalid utility verdict for this scenario kind")
        if row["status"] != "completed" and row["evaluation"]["security"] == "pass":
            raise ValueError("An incomplete run cannot have a passing security verdict")
        cases[case["id"]] = case
        index[key] = row
    selection = manifest.get("selection_ids", sorted(cases))
    modes = manifest.get("selection_modes", sorted({row["mode"] for row in rows}))
    repetitions = manifest["parameters"]["repetitions"]
    if type(repetitions) is not int or not 1 <= repetitions <= len(rows):
        raise ValueError("Invalid repetition count")
    if not isinstance(selection, list) or len(set(selection)) != len(selection):
        raise ValueError("Invalid scenario selection")
    if not isinstance(modes, list) or not modes or len(set(modes)) != len(modes):
        raise ValueError("Invalid mode selection")
    if not set(modes).issubset(MODES) or len(selection) * len(modes) * repetitions != len(rows):
        raise ValueError("Experiment is incomplete for its declared selection")
    if set(selection) != set(cases) or set(index) != set(
        itertools.product(selection, modes, range(repetitions))
    ):
        raise ValueError("Experiment is incomplete for its declared selection")
    digest = fingerprint([cases[identifier] for identifier in selection])
    if manifest.get("selection_sha256", digest) != digest:
        raise ValueError("Scenario selection checksum mismatch")
    return index, {"kind": identity["kind"], "sha256": identity["sha256"]}


def summarize(rows: list[dict]) -> dict:
    legitimate = [row for row in rows if row["scenario"]["kind"] == "legitimate"]
    return {
        "runs": len(rows),
        "violations": sum(row["evaluation"]["security"] == "fail" for row in rows),
        "legitimate_successes": sum(
            row["evaluation"]["task_success"] is True for row in legitimate
        ),
        "legitimate_runs": len(legitimate),
        "incomplete_runs": sum(row["status"] != "completed" for row in rows),
    }


def compare_manifests(baseline: dict, candidate: dict) -> dict:
    try:
        left, left_identity = index_experiment(baseline)
        right, right_identity = index_experiment(candidate)
        if left_identity != right_identity or set(left) != set(right):
            raise ValueError("Experiments must use the same suite, scenarios, modes and seeds")
        if (baseline["provider"] == "scripted") != (candidate["provider"] == "scripted"):
            raise ValueError("Cannot compare scripted fixtures with model experiments")
        if baseline["parameters"] != candidate["parameters"]:
            raise ValueError(
                "Execution parameters must match; model and prompt changes are allowed"
            )
        changed, regression_count = [], 0
        for key in sorted(left):
            old, new = left[key], right[key]
            if old["scenario"] != new["scenario"] or old["before"] != new["before"]:
                raise ValueError(
                    "Scenario content or initial environment changed between experiments"
                )
            flags = []
            if old["evaluation"]["security"] == "pass" and new["evaluation"]["security"] == "fail":
                flags.append("security_regression")
            if old["evaluation"]["security"] == "fail" and new["evaluation"]["security"] == "pass":
                flags.append("security_improvement")
            if old["status"] == "completed" and new["status"] != "completed":
                flags.append("execution_regression")
            if old["status"] == new["status"] == "completed":
                prior, current = (
                    old["evaluation"]["task_success"],
                    new["evaluation"]["task_success"],
                )
                if prior is True and current is False:
                    flags.append("utility_regression")
                elif prior is False and current is True:
                    flags.append("utility_improvement")
            if flags:
                regression_count += any(flag.endswith("regression") for flag in flags)
                changed.append(
                    {
                        "scenario_id": key[0],
                        "mode": key[1],
                        "seed": key[2],
                        "changes": flags,
                        "baseline_status": old["status"],
                        "candidate_status": new["status"],
                    }
                )
        summaries = {}
        for mode in sorted({key[1] for key in left}):
            prior = summarize([row for key, row in left.items() if key[1] == mode])
            current = summarize([row for key, row in right.items() if key[1] == mode])
            summaries[mode] = {
                "baseline": prior,
                "candidate": current,
                "delta": {name: current[name] - prior[name] for name in prior},
            }
        configuration_changes = [
            name
            for name in ("model", "model_info", "prompts", "provenance", "provider", "agent")
            if baseline.get(name) != candidate.get(name)
        ]
        if any(
            left[key].get("trace", {}).get("tool_catalog")
            != right[key].get("trace", {}).get("tool_catalog")
            for key in left
        ):
            configuration_changes.append("tool_catalog")
        configurations = {
            label: {
                "model": manifest.get("model"),
                "agent": manifest.get("agent"),
                "model_digest": (manifest.get("model_info") or {}).get("digest"),
                "commit": manifest.get("provenance", {}).get("commit"),
                "working_tree_dirty": manifest.get("provenance", {}).get("working_tree_dirty"),
            }
            for label, manifest in (("baseline", baseline), ("candidate", candidate))
        }
        return {
            "schema_version": 1,
            "suite": left_identity,
            "paired_runs": len(left),
            "regression_runs": regression_count,
            "configuration_changes": configuration_changes,
            "configurations": configurations,
            "parameters": baseline["parameters"],
            "summaries": summaries,
            "changed_runs": changed,
            "note": "Paired descriptive counts, not statistical significance or proof of safety.",
        }
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f"Invalid experiment manifest: {exc}") from exc


def compare_files(baseline: Path, candidate: Path, output: Path) -> dict:
    left, right = read_json(baseline), read_json(candidate)
    result = compare_manifests(left, right)
    result["sources"] = {
        "baseline": {"name": baseline.name, "sha256": fingerprint(left)},
        "candidate": {"name": candidate.name, "sha256": fingerprint(right)},
    }
    output.mkdir(parents=True, exist_ok=False)
    save_json(output / "comparison.json", result)
    escaped = html.escape(json.dumps(result, ensure_ascii=False, indent=2))
    table = []
    for mode, summary in result["summaries"].items():
        for label, values in summary.items():
            display_label = "reference" if label == "baseline" else label
            utility = (
                values["legitimate_successes"]
                if label == "delta"
                else f"{values['legitimate_successes']}/{values['legitimate_runs']}"
            )
            table.append(
                "<tr>"
                + "".join(
                    f"<td>{html.escape(str(value))}</td>"
                    for value in (
                        mode,
                        display_label,
                        values["runs"],
                        values["violations"],
                        utility,
                        values["incomplete_runs"],
                    )
                )
                + "</tr>"
            )
    document = f"""<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width"><title>Guardrail comparison</title>
<style>body{{font:16px system-ui;max-width:1100px;margin:40px auto;padding:0 20px}}
table{{border-collapse:collapse;display:block;overflow:auto}}
td,th{{padding:12px;border:1px solid #aaa}}
pre{{white-space:pre-wrap;overflow-wrap:anywhere}}</style>
<h1>Guardrail experiment comparison</h1><p>Paired runs: {result["paired_runs"]}.
Runs with regressions: {result["regression_runs"]}.</p>
<p>Delta = candidate − baseline. Negative violation counts indicate fewer observed failures.
Execution errors are reported separately and never count as security improvements.</p>
<table><tr><th>Mode</th><th>Version</th><th>Runs</th><th>Violations</th>
<th>Legitimate completed/total</th><th>Incomplete runs</th></tr>{"".join(table)}</table>
<h2>Changes and evidence</h2><pre>{escaped}</pre></html>"""
    (output / "comparison.html").write_text(document, encoding="utf-8")
    return result
