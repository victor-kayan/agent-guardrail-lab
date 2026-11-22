"""Explicit suite identity; generated attacks never modify the fixed evaluation suite."""

import hashlib
import json
from pathlib import Path

from guardrail_lab.scenarios import Scenario, get_scenario, scenarios
from guardrail_lab.storage import read_json

GENERATED_SCHEMA_VERSION = 1
MAX_CASES = 24
MAX_ATTACK_LENGTH = 3000


def fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


def selection_fingerprint(cases: list[Scenario]) -> str:
    return fingerprint([case.to_dict() for case in cases])


def fixed_suite_info() -> dict:
    return {"id": "fixed-v1", "kind": "fixed", "sha256": selection_fingerprint(scenarios())}


def normalized_attack(attack: str) -> str:
    return " ".join(attack.casefold().split())


def validate_attack(attack: object) -> str:
    if not isinstance(attack, str) or not 20 <= len(attack.strip()) <= MAX_ATTACK_LENGTH:
        raise ValueError(f"Attack must be text between 20 and {MAX_ATTACK_LENGTH} characters")
    if any(ord(char) < 32 and char not in "\n\r\t" for char in attack):
        raise ValueError("Attack contains unsupported control characters")
    return attack.strip()


def generated_case(parent_id: str, attack: object) -> tuple[Scenario, dict]:
    """Only attack text is model-controlled; labels, goals and fixtures stay trusted."""
    parent = get_scenario(parent_id)
    if parent.kind != "adversarial":
        raise ValueError("Generated cases must derive from an adversarial scenario")
    text = validate_attack(attack)
    identifier = f"generated-{parent_id}-{fingerprint([parent_id, text])[:12]}"
    indirect = parent_id.endswith("-indirect")
    case = Scenario(
        id=identifier,
        kind="adversarial",
        goal=parent.goal,
        prompt=parent.prompt if indirect else text,
        document=text if indirect else parent.document,
        expected_total=parent.expected_total,
    )
    return case, {"id": identifier, "parent_id": parent_id, "attack": text}


def load_generated_suite(path: Path) -> tuple[list[Scenario], dict]:
    payload = read_json(path, max_bytes=2_000_000)
    if not isinstance(payload, dict) or type(payload.get("schema_version")) is not int:
        raise ValueError("Invalid generated suite envelope")
    if payload["schema_version"] != GENERATED_SCHEMA_VERSION or payload.get("status") != "complete":
        raise ValueError("Only complete generated suites with schema version 1 can run")
    entries = payload.get("cases")
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_CASES:
        raise ValueError(f"Generated suite must contain between 1 and {MAX_CASES} cases")
    cases, seen = [], set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"id", "parent_id", "attack"}:
            raise ValueError("Generated entries may only contain id, parent_id and attack")
        if not isinstance(entry["parent_id"], str):
            raise ValueError("Invalid parent ID")
        case, canonical = generated_case(entry["parent_id"], entry["attack"])
        if entry != canonical:
            raise ValueError("Generated case ID/content mismatch")
        normalized = normalized_attack(entry["attack"])
        if normalized in seen:
            raise ValueError("Duplicate generated attack")
        seen.add(normalized)
        cases.append(case)
    digest = fingerprint(entries)
    if payload.get("cases_sha256") != digest:
        raise ValueError("Generated suite checksum mismatch")
    if payload.get("fixed_suite") != fixed_suite_info():
        raise ValueError("Generated suite was created for a different fixed suite version")
    return cases, {
        "id": f"generated-{digest[:12]}",
        "kind": "generated",
        "sha256": digest,
        "source_file": path.name,
        "fixed_suite": payload["fixed_suite"],
        "generation": payload.get("generation"),
    }


def load_suite(path: Path | None = None) -> tuple[list[Scenario], dict]:
    return load_generated_suite(path) if path is not None else (scenarios(), fixed_suite_info())


def attack_channel(case: Scenario) -> str:
    if case.kind == "legitimate":
        return "legitimate"
    if case.id.endswith("-indirect") or any(
        case.id.startswith(f"generated-{parent.id}-")
        for parent in scenarios()
        if parent.id.endswith("-indirect")
    ):
        return "indirect"
    return "direct"


def describe_suite(cases: list[Scenario], identity: dict) -> dict:
    coverage: dict[str, dict[str, int]] = {}
    for case in cases:
        channel = attack_channel(case)
        coverage.setdefault(case.goal, {})
        coverage[case.goal][channel] = coverage[case.goal].get(channel, 0) + 1
    return {
        "suite": {key: identity[key] for key in ("id", "kind", "sha256")},
        "case_count": len(cases),
        "selection_sha256": selection_fingerprint(cases),
        "coverage": coverage,
        "cases": [
            {"id": case.id, "goal": case.goal, "channel": attack_channel(case)} for case in cases
        ],
    }
