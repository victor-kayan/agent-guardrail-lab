"""Bounded JSON reads and atomic writes for local experiment artifacts."""

import json
import os
import tempfile
from pathlib import Path


def save_json(path: Path, value: object) -> None:
    data = json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}-",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read_json(path: Path, max_bytes: int = 25_000_000):
    with path.open("rb") as stream:
        data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError(f"JSON artifact exceeds the {max_bytes} byte limit")

    def unique_fields(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate JSON field: {key}")
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError(f"Non-finite JSON value: {value}")

    try:
        return json.loads(
            data.decode("utf-8-sig"),
            object_pairs_hook=unique_fields,
            parse_constant=reject_constant,
        )
    except RecursionError as exc:
        raise ValueError("JSON artifact is nested too deeply") from exc
