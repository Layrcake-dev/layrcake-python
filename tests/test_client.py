from __future__ import annotations

import json
import uuid
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
import respx

from layrcake import (
    APIResponseValidationError,
    Layrcake,
    LayrcakeError,
    LayrcakeMeta,
    __version__,
)

from .conftest import API_KEY, BASE_URL, envelope

ME = {"key": {"id": "k1"}, "workspace": {"id": "w1", "name": "Acme"}}


def test_sends_bearer_auth_and_sdk_headers(client: Layrcake, api: respx.MockRouter) -> None:
    route = api.get("/v1/me").respond(200, json=ME)
    client.get_me()
    request = route.calls.last.request
    assert request.headers["Authorization"] == f"Bearer {API_KEY}"
    assert request.headers["Accept"] == "application/json"
    assert request.headers["User-Agent"].startswith(f"layrcake-python/{__version__} ")
    assert "Idempotency-Key" not in request.headers
    assert "Content-Type" not in request.headers


def test_extra_headers_cannot_replace_the_api_key(client: Layrcake, api: respx.MockRouter) -> None:
    route = api.get("/v1/me").respond(200, json=ME)
    client.get_me(extra_headers={"Authorization": "Bearer stolen", "X-Layrcake-Run": "run_1"})
    request = route.calls.last.request
    assert request.headers["Authorization"] == f"Bearer {API_KEY}"
    assert request.headers["X-Layrcake-Run"] == "run_1"


def test_default_headers_are_sent(api: respx.MockRouter) -> None:
    route = api.get("/v1/me").respond(200, json=ME)
    with Layrcake(api_key=API_KEY, base_url=BASE_URL, default_headers={"X-Team": "growth"}) as c:
        c.get_me()
    assert route.calls.last.request.headers["X-Team"] == "growth"


def test_api_key_falls_back_to_environment(monkeypatch: pytest.MonkeyPatch, api: respx.MockRouter) -> None:
    monkeypatch.setenv("LAYRCAKE_API_KEY", "  lc_from_env  ")
    route = api.get("/v1/me").respond(200, json=ME)
    with Layrcake(base_url=BASE_URL) as c:
        assert c.api_key == "lc_from_env"
        c.get_me()
    assert route.calls.last.request.headers["Authorization"] == "Bearer lc_from_env"


def test_explicit_api_key_wins_over_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAYRCAKE_API_KEY", "lc_from_env")
    with Layrcake(api_key="lc_explicit") as c:
        assert c.api_key == "lc_explicit"


@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_api_key_is_a_clear_error(monkeypatch: pytest.MonkeyPatch, value: str | None) -> None:
    monkeypatch.delenv("LAYRCAKE_API_KEY", raising=False)
    with pytest.raises(LayrcakeError, match="LAYRCAKE_API_KEY") as info:
        Layrcake(api_key=value)
    assert info.value.code == "unauthorized"
    assert info.value.status is None


def test_base_url_defaults_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LAYRCAKE_BASE_URL", raising=False)
    with Layrcake(api_key=API_KEY) as c:
        assert c.base_url == "https://api.layrcake.dev"
    monkeypatch.setenv("LAYRCAKE_BASE_URL", "https://staging.example.com/")
    with Layrcake(api_key=API_KEY) as c:
        assert c.base_url == "https://staging.example.com"
    with pytest.raises(ValueError, match="base_url"):
        Layrcake(api_key=API_KEY, base_url="api.layrcake.dev")


def test_invalid_max_retries_is_rejected() -> None:
    with pytest.raises(ValueError, match="max_retries"):
        Layrcake(api_key=API_KEY, max_retries=-1)


def test_repr_never_shows_the_key(client: Layrcake) -> None:
    assert API_KEY not in repr(client)
    assert "api.test" in repr(client)


def test_body_omits_arguments_not_given(client: Layrcake, api: respx.MockRouter) -> None:
    route = api.post("/v1/find_leads").respond(200, json=envelope({"leads": [], "total_found": 0}))
    client.find_leads(audience="CFOs at UK fintechs", limit=5, titles=("CFO", "Finance Director"))
    request = route.calls.last.request
    assert request.headers["Content-Type"] == "application/json"
    assert json.loads(request.content) == {
        "audience": "CFOs at UK fintechs",
        "titles": ["CFO", "Finance Director"],
        "limit": 5,
    }


def test_extra_body_and_extra_query_are_merged(client: Layrcake, api: respx.MockRouter) -> None:
    route = api.post("/v1/verify_email").respond(200, json=envelope({"email": "a@b.co"}))
    client.verify_email(email="a@b.co", extra_body={"new_field": True}, extra_query={"trace": "1"})
    request = route.calls.last.request
    assert json.loads(request.content) == {"email": "a@b.co", "new_field": True}
    assert request.url.params["trace"] == "1"


def test_path_params_are_encoded(client: Layrcake, api: respx.MockRouter) -> None:
    route = api.get(url__regex=r"/v1/campaigns/.+/proof").respond(200, json={"campaign_id": "x"})
    client.get_campaign_proof("a/b c")
    assert route.calls.last.request.url.raw_path == b"/v1/campaigns/a%2Fb%20c/proof"


def test_empty_path_param_is_rejected(client: Layrcake) -> None:
    with pytest.raises(ValueError, match="campaign_id"):
        client.get_campaign_proof("")


def test_query_lists_repeat_and_unset_values_are_dropped(client: Layrcake, api: respx.MockRouter) -> None:
    route = api.get("/v1/trail").respond(200, json={"result": {"events": [], "next_cursor": None}})
    client.get_trail(kind=["reply", "bounce"], limit=5)
    params = route.calls.last.request.url.params
    assert params.get_list("kind") == ["reply", "bounce"]
    assert params["limit"] == "5"
    assert "cursor" not in params


def test_envelope_response_is_unwrapped(client: Layrcake, api: respx.MockRouter) -> None:
    api.post("/v1/verify_email").respond(
        200,
        json=envelope(
            {"email": "a@b.co", "status": "valid", "is_catch_all_domain": False, "checked_at": "2026-10-18T00:00:00Z"},
            charged="0.125000",
            available="1234.500001",
            replayed=True,
            route={"used": "balanced", "source": "default"},
        ),
    )
    response = client.verify_email(email="a@b.co")
    assert response.status_code == 200
    assert response.result["email"] == "a@b.co"
    assert isinstance(response.layrcake, LayrcakeMeta)
    assert response.layrcake.credits_charged == Decimal("0.125000")
    assert type(response.layrcake.credits_charged) is Decimal
    assert response.layrcake.credits_available == Decimal("1234.500001")
    assert response.layrcake.raw["credits_charged"] == "0.125000"
    assert response.layrcake.replayed is True
    assert response.layrcake.route == {"used": "balanced", "source": "default"}
    assert response.credits_charged == Decimal("0.125000")
    assert response.request_id == "11111111-1111-4111-8111-111111111111"


def test_bare_response_is_the_result(client: Layrcake, api: respx.MockRouter) -> None:
    api.get("/v1/me").respond(200, json=ME, headers={"X-Request-Id": "req_hdr"})
    response = client.get_me()
    assert response.result["workspace"]["name"] == "Acme"
    assert response.layrcake is None
    assert response.credits_charged is None
    assert response.request_id == "req_hdr"


def test_envelope_without_billing_block(client: Layrcake, api: respx.MockRouter) -> None:
    api.post("/v1/get_replies").respond(200, json={"result": {"replies": []}})
    response = client.get_replies()
    assert response.result == {"replies": []}
    assert response.layrcake is None


def test_accepted_body_is_returned_whole(client: Layrcake, api: respx.MockRouter) -> None:
    body = {"request_id": "req_first", "status": "in_progress"}
    api.post("/v1/launch_campaign").respond(202, json=body)
    response = client.launch_campaign(campaign_id=str(uuid.uuid4()), confirmation_token="tok")
    assert response.status_code == 202
    assert response.result == body
    assert response.request_id == "req_first"


def test_pause_campaign_reads_its_result(client: Layrcake, api: respx.MockRouter) -> None:
    api.post(url__regex=r"/v1/campaigns/.+/pause").respond(200, json={"result": {"held": True}})
    response = client.pause_campaign("c1")
    assert response.result == {"held": True}


def test_csv_export_returns_text(client: Layrcake, api: respx.MockRouter) -> None:
    route = api.get("/v1/trail/export.csv").respond(
        200, text="kind,at\nreply,2026\n", headers={"Content-Type": "text/csv"}
    )
    response = client.export_trail_csv(campaign_id="c1")
    assert response.result == "kind,at\nreply,2026\n"
    assert route.calls.last.request.headers["Accept"] == "text/csv, application/json"


def test_non_json_success_body_is_an_error(client: Layrcake, api: respx.MockRouter) -> None:
    api.get("/v1/me").respond(200, text="<html>proxy</html>")
    with pytest.raises(APIResponseValidationError) as info:
        client.get_me()
    assert info.value.code == "invalid_response"
    assert info.value.status == 200


def test_unreadable_credit_amount_is_an_error(client: Layrcake, api: respx.MockRouter) -> None:
    api.post("/v1/verify_email").respond(200, json=envelope({"email": "a@b.co"}, charged="lots"))
    with pytest.raises(APIResponseValidationError, match="credit amount"):
        client.verify_email(email="a@b.co")


def test_per_call_timeout_is_applied(client: Layrcake, api: respx.MockRouter) -> None:
    route = api.get("/v1/me").respond(200, json=ME)
    client.get_me(timeout=5.0)
    assert route.calls.last.request.extensions["timeout"] == httpx.Timeout(5.0).as_dict()


def test_with_options_shares_the_pool(client: Layrcake, api: respx.MockRouter) -> None:
    api.get("/v1/me").respond(503)
    quick = client.with_options(max_retries=0, timeout=3.0)
    assert quick.max_retries == 0
    assert quick.timeout == 3.0
    assert quick.api_key == client.api_key
    assert quick._transport.client is client._transport.client
    with pytest.raises(LayrcakeError):
        quick.get_me()
    assert api.calls.call_count == 1
    quick.close()
    assert not client._transport.client.is_closed


def test_user_http_client_is_not_closed() -> None:
    http = httpx.Client()
    with Layrcake(api_key=API_KEY, http_client=http):
        pass
    assert not http.is_closed
    http.close()


def test_every_operation_has_a_method() -> None:
    spec = json.loads((Path(__file__).resolve().parent.parent / "openapi.json").read_text(encoding="utf-8"))
    op_ids = {op["operationId"] for item in spec["paths"].values() for op in item.values()}
    for op_id in op_ids:
        assert callable(getattr(Layrcake, op_id)), op_id
