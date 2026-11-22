"""Promptfoo custom Python provider; no cloud generation or LLM grading."""

import asyncio
import json
import os
import tempfile
from pathlib import Path

from guardrail_lab.runner import run_case
from guardrail_lab.suites import load_suite


def call_api(prompt, options, context):
    config = options.get("config", {})
    request = json.loads(prompt)
    suite_file = config.get("suite_file")
    suite_path = Path(suite_file) if suite_file else None
    if suite_path is not None and not suite_path.is_absolute():
        suite_path = Path(__file__).resolve().parents[2] / suite_path
    cases, identity = load_suite(suite_path)
    scenario = next((case for case in cases if case.id == request["scenario_id"]), None)
    if scenario is None:
        raise ValueError("Unknown scenario ID for the selected suite")
    with tempfile.TemporaryDirectory(prefix="guardrail-promptfoo-") as folder:
        result = asyncio.run(
            run_case(
                scenario,
                config["mode"],
                Path(folder),
                config.get("provider", "ollama"),
                os.environ.get("LAB_MODEL", config.get("model", "qwen3:4b")),
                timeout=config.get("timeout", 180),
            )
        )
    return {
        "output": json.dumps(
            {
                "evaluation": result["evaluation"],
                "status": result["status"],
                "provider": result["provider"],
                "events": result["events"],
                "error": result["error"],
            }
        ),
        "metadata": {
            "mode": config["mode"],
            "scenario_id": scenario.id,
            "suite_id": identity["id"],
            "suite_kind": identity["kind"],
            "suite_sha256": identity["sha256"],
        },
    }
