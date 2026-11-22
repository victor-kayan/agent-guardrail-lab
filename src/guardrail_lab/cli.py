"""Local generation, inspection, execution, comparison and reporting commands."""

import argparse
import asyncio
import json
import math
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx

from guardrail_lab.agent import doctor
from guardrail_lab.comparison import compare_files
from guardrail_lab.domain import MODES
from guardrail_lab.generation import generate_suite
from guardrail_lab.report import render_report
from guardrail_lab.runner import experiment
from guardrail_lab.storage import read_json
from guardrail_lab.suites import describe_suite, load_suite


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
    listing = commands.add_parser("scenarios", help="Inspect fixed or generated scenarios")
    listing.add_argument("--suite-file", type=Path)
    listing.add_argument("--show-text", action="store_true", help="Print prompts and documents")
    listing.add_argument("--json", action="store_true", dest="as_json")
    compare = commands.add_parser("compare", help="Compare compatible experiment manifests")
    compare.add_argument("baseline", type=Path)
    compare.add_argument("candidate", type=Path)
    compare.add_argument("--output", type=Path, required=True)
    compare.add_argument("--fail-on-regression", action="store_true")
    report = commands.add_parser("report", help="Regenerate HTML from an existing experiment")
    report.add_argument("manifest", type=Path)
    dashboard = commands.add_parser(
        "serve", help="Explore saved artifacts in a local read-only dashboard"
    )
    dashboard.add_argument("--root", type=Path, default=Path("artifacts"))
    dashboard.add_argument("--port", type=int, default=8765)
    run = commands.choices["run"]
    run.add_argument("--provider", choices=("ollama", "scripted", "external"), default="ollama")
    run.add_argument("--agent-id", help="Stable external agent name")
    run.add_argument("--agent-revision", help="External code/configuration revision or digest")
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
            cases, identity = load_suite(args.suite_file)
            description = describe_suite(cases, identity)
            if args.as_json:
                if args.show_text:
                    description["texts"] = [case.to_dict() for case in cases]
                print(json.dumps(description, indent=2, ensure_ascii=False))
            else:
                print(f"Suite: {identity['id']} ({identity['kind']}) — {len(cases)} cases")
                print(f"SHA256: {identity['sha256']}")
                print(f"Coverage: {json.dumps(description['coverage'], sort_keys=True)}")
                for case in cases:
                    print(f"{case.id:22} {case.kind:12} {case.goal}")
                    if args.show_text:
                        print(f"  Prompt: {case.prompt}\n  Document: {case.document}")
        elif args.command == "compare":
            result = compare_files(args.baseline, args.candidate, args.output)
            print(
                f"Paired runs: {result['paired_runs']}; "
                f"regression runs: {result['regression_runs']}"
            )
            print(f"Report: {(args.output / 'comparison.html').resolve()}")
            if args.fail_on_regression and result["regression_runs"]:
                sys.exit(3)
        elif args.command == "serve":
            from guardrail_lab.dashboard import serve

            serve(args.root, args.port)
        elif args.command == "report":
            manifest = read_json(args.manifest)
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
            if args.repetitions < 1 or not math.isfinite(args.timeout) or args.timeout <= 0:
                parser.error("repetitions must be positive and timeout must be finite and positive")
            if args.provider == "external" and (not args.agent_id or not args.agent_revision):
                parser.error(
                    "External agents require --agent-id and --agent-revision; "
                    "--url is the agent base URL"
                )
            available, suite_info = load_suite(args.suite_file)
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
                    (["baseline", "enforced"] if args.provider == "external" else list(MODES))
                    if args.mode == "all"
                    else [args.mode],
                    args.repetitions,
                    output,
                    args.provider,
                    args.model,
                    args.url,
                    args.timeout,
                    progress,
                    suite_info=suite_info,
                    agent_info={"id": args.agent_id, "revision": args.agent_revision},
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
