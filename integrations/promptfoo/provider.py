"""Promptfoo custom Python provider; no cloud generation or LLM grading."""

import asyncio
import json
import os
import tempfile
from pathlib import Path

from guardrail_lab.runner import run_case
from guardrail_lab.scenarios import get_scenario


def call_api(prompt, options, context):
    config = options.get("config", {})
    request = json.loads(prompt)
    scenario = get_scenario(request["scenario_id"])
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
        "metadata": {"mode": config["mode"], "scenario_id": scenario.id},
    }
