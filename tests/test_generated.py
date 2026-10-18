"""The generated surface matches openapi.json."""

from __future__ import annotations

import inspect
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Set

import pytest

from layrcake import AsyncLayrcake, Layrcake

ROOT = Path(__file__).resolve().parent.parent
SPEC: Dict[str, Any] = json.loads((ROOT / "openapi.json").read_text(encoding="utf-8"))
OPERATIONS = [(path, method, op) for path, item in SPEC["paths"].items() for method, op in item.items()]
SDK_OPTIONS = {"idempotency_key", "extra_headers", "extra_query", "extra_body", "timeout"}
NOT_EXPOSED = {("llm", "stream")}


def test_generated_code_is_up_to_date() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "generate.py"), "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(("path", "method", "op"), OPERATIONS, ids=[op["operationId"] for _, _, op in OPERATIONS])
def test_method_arguments_match_the_spec(path: str, method: str, op: Dict[str, Any]) -> None:
    op_id = op["operationId"]
    expected: Set[str] = set()
    schema = (op.get("requestBody") or {}).get("content", {}).get("application/json", {}).get("schema", {})
    expected |= {name for name in schema.get("properties", {}) if (op_id, name) not in NOT_EXPOSED}
    expected |= {p["name"] for p in op.get("parameters", []) if p["in"] == "query"}
    path_count = sum(1 for p in op.get("parameters", []) if p["in"] == "path")
    takes_key = any(p["in"] == "header" and p["name"].lower() == "idempotency-key" for p in op.get("parameters", []))

    for cls in (Layrcake, AsyncLayrcake):
        params = inspect.signature(getattr(cls, op_id)).parameters
        positional = [p for p in params.values() if p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD][1:]
        keyword = {name for name, p in params.items() if p.kind is inspect.Parameter.KEYWORD_ONLY}
        assert len(positional) == path_count
        assert ("idempotency_key" in keyword) == takes_key
        extra = {"cursor"} if op_id == "get_operation" else set()
        assert keyword - SDK_OPTIONS == expected | extra
        if cls is AsyncLayrcake:
            assert inspect.iscoroutinefunction(getattr(cls, op_id))
