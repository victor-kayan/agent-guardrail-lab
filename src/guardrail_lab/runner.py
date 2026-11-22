"""Isolated runs, provenance, artifacts, and sequential experiments."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
import json
import math
import platform
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from guardrail_lab.agent import BASE_PROMPT, RULES, doctor, execute_agent
from guardrail_lab.domain import MODES, events, initialize, snapshot
from guardrail_lab.evaluation import evaluate
from guardrail_lab.scenarios import Scenario, scenarios
from guardrail_lab.storage import save_json
from guardrail_lab.suites import fixed_suite_info, selection_fingerprint


def provenance() -> dict:
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, text=True, timeout=5
        ).strip()
        dirty = bool(
            subprocess.check_output(
                ["git", "status", "--porcelain"], stderr=subprocess.DEVNULL, text=True, timeout=5
            ).strip()
        )
    except (OSError, subprocess.SubprocessError):
        commit = "unknown"
        dirty = None
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "commit": commit,
        "working_tree_dirty": dirty,
        "packages": {name: importlib.metadata.version(name) for name in ("mcp", "httpx")},
    }


async def run_case(
    scenario: Scenario,
    mode: str,
    output: Path,
    provider: str = "ollama",
    model: str = "qwen3:4b",
    url: str = "http://127.0.0.1:11434",
    timeout: float = 180,
    seed: int = 0,
) -> dict:
    if mode not in MODES:
        raise ValueError(mode)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Timeout must be finite and positive")
    run_id = f"{scenario.id}-{mode}-{uuid4().hex[:8]}"
    directory = output / run_id
    directory.mkdir(parents=True, exist_ok=False)
    db = directory / "environment.sqlite"
    initialize(db, scenario.document)
    before = snapshot(db)
    started = time.perf_counter()
    trace: dict = {"status": "error", "messages": [], "calls": []}
    error = None
    try:
        trace = await asyncio.wait_for(
            execute_agent(
                db,
                mode,
                scenario,
                provider,
                model,
                url,
                timeout=timeout,
                seed=seed,
                trace=trace,
            ),
            timeout=timeout,
        )
    except TimeoutError:
        trace["status"], error = "timeout", f"Case exceeded {timeout:g} seconds"
    except Exception as exc:
        trace["status"] = "error"
        error = repr(exc)
    elapsed = round(time.perf_counter() - started, 3)
    after, audit = snapshot(db), events(db)
    result = {
        "schema_version": 1,
        "run_id": run_id,
        "scenario": scenario.to_dict(),
        "mode": mode,
        "provider": provider,
        "model": model if provider != "scripted" else "NO_MODEL_SCRIPTED_FIXTURE",
        "seed": seed,
        "status": trace["status"],
        "error": error,
        "elapsed_seconds": elapsed,
        "created_at": datetime.now(UTC).isoformat(),
        "trace": trace,
        "events": audit,
        "before": before,
        "after": after,
        "evaluation": evaluate(scenario, before, after, audit, trace["status"]),
        "scenario_sha256": hashlib.sha256(
            json.dumps(scenario.to_dict(), sort_keys=True).encode()
        ).hexdigest(),
    }
    save_json(directory / "result.json", result)
    return result


async def experiment(
    selected: list[Scenario],
    modes: list[str],
    repetitions: int,
    output: Path,
    provider: str,
    model: str,
    url: str,
    timeout: float,
    progress=None,
    suite_info: dict | None = None,
    agent_info: dict | None = None,
) -> dict:
    identity = suite_info or fixed_suite_info()
    if provider not in {"ollama", "external", "scripted"}:
        raise ValueError("Unknown provider")
    if provider == "external":
        from guardrail_lab.agent import local_url

        local_url(url)
        if not agent_info or not agent_info.get("id") or not agent_info.get("revision"):
            raise ValueError("External agents require an ID and revision")
        if "prompt_only" in modes:
            raise ValueError("External agents own their prompts; use baseline or enforced")
    if type(repetitions) is not int or repetitions < 1:
        raise ValueError("Repetitions must be a positive integer")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Timeout must be finite and positive")
    if not selected:
        raise ValueError("An experiment needs at least one case")
    if len({case.id for case in selected}) != len(selected):
        raise ValueError("Duplicate scenario IDs in selection")
    if not modes or len(set(modes)) != len(modes) or not set(modes).issubset(MODES):
        raise ValueError("Select distinct, valid modes")
    if identity.get("kind") == "fixed":
        fixed = {case.id: case for case in scenarios()}
        if identity != fixed_suite_info() or any(fixed.get(case.id) != case for case in selected):
            raise ValueError("Fixed suite identity cannot be used for changed or generated cases")
    elif identity.get("kind") != "generated":
        raise ValueError("Unknown suite kind")
    output.mkdir(parents=True, exist_ok=False)
    model_info = None
    if provider == "ollama":
        health = await doctor(url, model)
        model_info = next(
            (item for item in health["installed_models"] if item["name"] == model), None
        )
    manifest = {
        "schema_version": 1,
        "suite": identity,
        "selection_sha256": selection_fingerprint(selected),
        "selection_ids": [case.id for case in selected],
        "selection_modes": modes,
        "provenance": provenance(),
        "provider": provider,
        "model": model if provider != "scripted" else None,
        "agent": {**agent_info, "endpoint": url} if provider == "external" else None,
        "model_info": model_info,
        "prompts": (
            {"base": BASE_PROMPT, "security_rules": RULES}
            if provider != "external"
            else {"owner": "external agent"}
        ),
        "parameters": {
            "num_ctx": 4096,
            "num_predict": 768,
            "temperature": 0,
            "max_steps": 8,
            "timeout_seconds": timeout,
            "repetitions": repetitions,
        },
        "results": [],
    }
    # Persist after every case so an interrupted run still has reviewable evidence.
    save_json(output / "experiment.json", manifest)
    for repetition in range(repetitions):
        for scenario in selected:
            for mode in modes:
                result = await run_case(
                    scenario, mode, output, provider, model, url, timeout, repetition
                )
                manifest["results"].append(result)
                save_json(output / "experiment.json", manifest)
                if progress:
                    progress(result)
    return manifest
