"""CLI: guardrail-lab doctor | scenarios | generate | run | report."""

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx

from guardrail_lab.agent import doctor
from guardrail_lab.domain import MODES
from guardrail_lab.generation import generate_suite
from guardrail_lab.report import render_report
from guardrail_lab.runner import experiment
from guardrail_lab.scenarios import scenarios
from guardrail_lab.suites import fixed_suite_info, load_generated_suite


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Local MCP guardrail evaluation using synthetic data"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("doctor", help="Check local Ollama and model availability")
    for command in [
        check,
        commands.add_parser("run", help="Run a sequential experiment"),
        commands.add_parser("generate", help="Generate a separate exploratory suite with local AI"),
    ]:
        command.add_argument("--model", default="qwen3:4b")
        command.add_argument("--url", default="http://127.0.0.1:11434")
    commands.add_parser("scenarios", help="List the fixed scenario suite")
    report = commands.add_parser("report", help="Regenerate HTML from an existing experiment")
    report.add_argument("manifest", type=Path)
    run = commands.choices["run"]
    run.add_argument("--provider", choices=("ollama", "scripted"), default="ollama")
    run.add_argument("--mode", choices=(*MODES, "all"), default="all")
    run.add_argument("--scenario", action="append", help="Select a case ID; repeat for several")
    run.add_argument("--repetitions", type=int, default=1)
    run.add_argument("--timeout", type=float, default=180)
    run.add_argument("--output", type=Path)
    run.add_argument(
        "--suite-file", type=Path, help="Explicitly use generated cases instead of fixed-v1"
    )
    generate = commands.choices["generate"]
    generate.add_argument("--count", type=int, default=6)
    generate.add_argument("--seed", type=int, default=0)
    generate.add_argument("--timeout", type=float, default=90)
    generate.add_argument(
        "--output", type=Path, required=True, help="New directory for a frozen suite"
    )
    args = parser.parse_args()
    try:
        if args.command == "doctor":
            result = asyncio.run(doctor(args.url, args.model))
            print(json.dumps(result, indent=2))
            if not result["available"]:
                sys.exit(1)
        elif args.command == "scenarios":
            for case in scenarios():
                print(f"{case.id:22} {case.kind:12} {case.goal}")
        elif args.command == "report":
            manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
            destination = args.manifest.with_name("report.html")
            render_report(manifest, destination)
            print(destination.resolve())
        elif args.command == "generate":
            payload = asyncio.run(
                generate_suite(
                    args.output,
                    args.model,
                    args.url,
                    args.count,
                    args.seed,
                    args.timeout,
                    progress=lambda entry: print(f"Generated: {entry['id']}", flush=True),
                )
            )
            print(
                f"Generated {len(payload['cases'])} exploratory cases: "
                f"{(args.output / 'suite.json').resolve()}"
            )
            print(
                "Fixed evaluation suite unchanged. "
                "Review generated text before interpreting results."
            )
        else:
            if args.repetitions < 1 or args.timeout <= 0:
                parser.error("repetitions and timeout must be positive")
            available, suite_info = (
                load_generated_suite(args.suite_file)
                if args.suite_file
                else (scenarios(), fixed_suite_info())
            )
            selected = [case for case in available if not args.scenario or case.id in args.scenario]
            if args.scenario and set(args.scenario) - {case.id for case in selected}:
                parser.error("Unknown scenario ID for the selected suite")
            if args.provider == "ollama":
                health = asyncio.run(doctor(args.url, args.model))
                if not health["available"]:
                    parser.error(f"Model not installed. Run: ollama pull {args.model}")
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            output = args.output or Path("artifacts") / f"{stamp}-{uuid4().hex[:6]}"
            print(
                f"Provider: {args.provider}. "
                + (
                    "SCRIPTED FIXTURE, NOT AN AI EVALUATION."
                    if args.provider == "scripted"
                    else args.model
                ),
                flush=True,
            )
            print(f"Suite: {suite_info['id']} ({suite_info['kind']})", flush=True)

            def progress(row):
                print(
                    f"{row['scenario']['id']:22} {row['mode']:12} "
                    f"{row['evaluation']['security']:12} {row['status']} "
                    f"{row['elapsed_seconds']}s",
                    flush=True,
                )

            manifest = asyncio.run(
                experiment(
                    selected,
                    list(MODES) if args.mode == "all" else [args.mode],
                    args.repetitions,
                    output,
                    args.provider,
                    args.model,
                    args.url,
                    args.timeout,
                    progress,
                    suite_info=suite_info,
                )
            )
            render_report(manifest, output / "report.html")
            print(f"Report: {(output / 'report.html').resolve()}")
            if any(row["status"] != "completed" for row in manifest["results"]):
                sys.exit(2)
    except (OSError, ValueError, httpx.HTTPError) as exc:
        parser.exit(1, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
