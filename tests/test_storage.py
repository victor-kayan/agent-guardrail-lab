import json
import math

import pytest

from guardrail_lab.generation import generate_suite
from guardrail_lab.storage import read_json, save_json


def test_failed_atomic_replace_preserves_previous_result(tmp_path, monkeypatch):
    path = tmp_path / "experiment.json"
    save_json(path, {"valid": True})

    def fail(*args):
        raise OSError("simulated interruption")

    monkeypatch.setattr("guardrail_lab.storage.os.replace", fail)
    with pytest.raises(OSError, match="interruption"):
        save_json(path, {"valid": False})
    assert read_json(path) == {"valid": True}
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("text", ['{"value":1,"value":2}', '{"value":NaN}', '{"value":Infinity}'])
def test_ambiguous_or_non_finite_json_is_rejected(tmp_path, text):
    path = tmp_path / "bad.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        read_json(path)


def test_read_limit_is_applied_to_bytes_actually_read(tmp_path):
    path = tmp_path / "large.json"
    path.write_text(json.dumps({"data": "x" * 100}), encoding="utf-8")
    with pytest.raises(ValueError, match="limit"):
        read_json(path, max_bytes=50)


@pytest.mark.parametrize("timeout", [math.nan, math.inf, -math.inf, 0])
def test_invalid_generation_timeout_fails_before_network_or_writes(tmp_path, timeout):
    import asyncio

    path = tmp_path / "new"
    with pytest.raises(ValueError, match="finite and positive"):
        asyncio.run(generate_suite(path, timeout=timeout))
    assert not path.exists()


def test_invalid_run_timeout_fails_before_model_connection(monkeypatch):
    from guardrail_lab import cli

    monkeypatch.setattr("sys.argv", ["guardrail-lab", "run", "--timeout", "nan"])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
