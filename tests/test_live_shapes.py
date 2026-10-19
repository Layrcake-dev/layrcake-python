"""Regression tests for body shapes seen on the live API (2026-10-18).

The live API wraps some bodies in {"result": ...} that the spec describes as
flat or not at all. The SDK unwraps by shape.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Dict

import httpx
import pytest
import respx

from layrcake import Layrcake

ROOT = Path(__file__).resolve().parent.parent
OP_ID = "6f1c1d2e-3f40-4a8e-9a0e-0b5c3a522f6c"
ME = {
    "key": {
        "id": "k1",
        "name": "QA",
        "display_prefix": "lc_ab",
        "created_at": "2026",
        "scopes": [],
        "oauth_issued": False,
    },
    "workspace": {"id": "w1", "name": "Acme", "risk_tier": "standard", "external_actions_suspended": False},
    "guard": {"spend_month_to_date": "0"},
    "credits": {"available": "12.500000", "reserved": "0"},
}


def test_wrapped_get_me_is_unwrapped(client: Layrcake, api: respx.MockRouter) -> None:
    api.get("/v1/me").respond(200, json={"result": ME})
    me = client.get_me()
    assert me.result["key"]["display_prefix"] == "lc_ab"
    assert me.result["workspace"]["name"] == "Acme"
    assert me.layrcake is None


def test_flat_get_me_still_works(client: Layrcake, api: respx.MockRouter) -> None:
    api.get("/v1/me").respond(200, json=ME)
    assert client.get_me().result["workspace"]["name"] == "Acme"


def test_wrapped_operation_is_unwrapped_by_wait_for_operation(client: Layrcake, api: respx.MockRouter) -> None:
    def op(state: str) -> Dict[str, Any]:
        return {"result": {"operation_id": OP_ID, "kind": "research_company", "state": state, "result": None}}

    api.get(f"/v1/operations/{OP_ID}").mock(
        side_effect=[httpx.Response(200, json=op("running")), httpx.Response(200, json=op("succeeded"))]
    )
    assert client.wait_for_operation(OP_ID)["state"] == "succeeded"


def test_llm_body_is_never_unwrapped(client: Layrcake, api: respx.MockRouter) -> None:
    api.post("/v1/llm/chat").respond(200, json={"result": "looks like an envelope"})
    response = client.llm(model="m", messages=[{"role": "user", "content": "hi"}])
    assert response.result == {"result": "looks like an envelope"}


def test_body_with_other_keys_is_not_unwrapped(client: Layrcake, api: respx.MockRouter) -> None:
    body = {"result": {"x": 1}, "extra": True}
    api.get("/v1/me").respond(200, json=body)
    assert client.get_me().body == body
    assert client.get_me().result == body


def test_accepted_202_stays_whole(client: Layrcake, api: respx.MockRouter) -> None:
    body = {"status": "approved", "approval": {"id": "a1", "state": "approved"}}
    api.post("/v1/reply_to_thread").respond(202, json=body)
    response = client.reply_to_thread(reply_id="r1", body_text="Thanks", confirmation_token="t")
    assert response.result == body


def _load_generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("layrcake_generate", ROOT / "scripts" / "generate.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["layrcake_generate"] = module
    spec.loader.exec_module(module)
    return module


def test_generator_refuses_a_stale_wrapped_override() -> None:
    generator = _load_generator()
    api_spec = json.loads((ROOT / "openapi.json").read_text(encoding="utf-8"))
    fixed = copy.deepcopy(api_spec)
    flat = fixed["paths"]["/v1/me"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    fixed["paths"]["/v1/me"]["get"]["responses"]["200"]["content"]["application/json"]["schema"] = {
        "type": "object",
        "properties": {"result": flat},
        "required": ["result"],
    }
    with pytest.raises(SystemExit, match="get_me: the spec now describes the envelope"):
        generator.build(fixed, json.loads((ROOT / "scripts" / "fallback_summaries.json").read_text(encoding="utf-8")))
