from __future__ import annotations

import pytest
import responses

from src.devin_client import DEFAULT_BASE_URL, DevinClient
from src.errors import (
    DevinAuthError,
    DevinNotFoundError,
    DevinPermissionError,
    DevinRateLimitError,
    DevinServerError,
    DevinValidationError,
)

ORG = "org-test"
API_KEY = "cog_test"
URL = f"{DEFAULT_BASE_URL}/v3/organizations/{ORG}/sessions"
URL_V1 = f"{DEFAULT_BASE_URL}/v1/sessions"


def _make_client(api_version: str = "v3") -> DevinClient:
    return DevinClient(API_KEY, ORG, api_version=api_version)


def test_rejects_unsupported_api_version():
    with pytest.raises(ValueError):
        DevinClient(API_KEY, ORG, api_version="v2")


@responses.activate
def test_v1_uses_org_less_endpoint():
    responses.add(
        responses.POST,
        URL_V1,
        json={"session_id": "sess_v1", "url": "https://app.devin.ai/sessions/sess_v1"},
        status=200,
    )
    result = _call(_make_client("v1"))
    assert result.session_id == "sess_v1"
    assert responses.calls[0].request.url == URL_V1


def _call(client: DevinClient):
    return client.create_session(
        prompt="hi",
        repo="knanao/example",
        github_token="ghs_xxx",
        title="test",
        tags=["github-action"],
        devin_mode="normal",
        max_acu_limit=None,
        playbook_id=None,
    )


@responses.activate
def test_success_returns_session_id_and_url():
    responses.add(
        responses.POST,
        URL,
        json={"session_id": "sess_1", "url": "https://app.devin.ai/sessions/sess_1"},
        status=200,
    )
    result = _call(_make_client())
    assert result.session_id == "sess_1"
    assert result.url == "https://app.devin.ai/sessions/sess_1"


@responses.activate
def test_success_falls_back_to_id_and_session_url_fields():
    responses.add(
        responses.POST,
        URL,
        json={"id": "sess_2", "session_url": "https://app.devin.ai/sessions/sess_2"},
        status=200,
    )
    result = _call(_make_client())
    assert result.session_id == "sess_2"
    assert result.url == "https://app.devin.ai/sessions/sess_2"


@responses.activate
def test_sends_expected_payload():
    responses.add(
        responses.POST,
        URL,
        json={"session_id": "sess_x", "url": "https://app.devin.ai/sessions/sess_x"},
        status=200,
    )
    client = _make_client()
    client.create_session(
        prompt="P",
        repo="owner/repo",
        github_token="ghs_secret",
        title="T",
        tags=["a", "b"],
        devin_mode="fast",
        max_acu_limit=5,
        playbook_id="pb_1",
    )
    call = responses.calls[0]
    body = call.request.body
    if isinstance(body, bytes):
        body = body.decode("utf-8")
    import json as _json
    payload = _json.loads(body)
    assert payload["prompt"] == "P"
    assert payload["repos"] == ["owner/repo"]
    assert payload["devin_mode"] == "fast"
    assert payload["max_acu_limit"] == 5
    assert payload["playbook_id"] == "pb_1"
    assert payload["title"] == "T"
    assert payload["tags"] == ["a", "b"]
    secret = payload["session_secrets"][0]
    assert secret["key"] == "GITHUB_TOKEN"
    assert secret["value"] == "ghs_secret"
    assert secret["sensitive"] is True
    assert call.request.headers["Authorization"] == f"Bearer {API_KEY}"


@responses.activate
def test_401_raises_auth_error():
    responses.add(responses.POST, URL, json={"detail": "nope"}, status=401)
    with pytest.raises(DevinAuthError):
        _call(_make_client())


@responses.activate
def test_403_raises_permission_error():
    responses.add(responses.POST, URL, json={"detail": "denied"}, status=403)
    with pytest.raises(DevinPermissionError):
        _call(_make_client())


@responses.activate
def test_404_raises_not_found_error():
    responses.add(responses.POST, URL, json={"detail": "missing"}, status=404)
    with pytest.raises(DevinNotFoundError):
        _call(_make_client())


@responses.activate
def test_422_extracts_first_msg():
    responses.add(
        responses.POST,
        URL,
        json={"detail": [{"loc": ["body", "repos"], "msg": "field required"}]},
        status=422,
    )
    with pytest.raises(DevinValidationError) as exc:
        _call(_make_client())
    assert "field required" in exc.value.user_message()


@responses.activate
def test_429_raises_rate_limit_after_retries():
    # urllib3 Retry will retry then re-raise the same status; we allow multiple hits.
    for _ in range(5):
        responses.add(responses.POST, URL, json={}, status=429)
    with pytest.raises(DevinRateLimitError):
        _call(_make_client())


@responses.activate
def test_500_retries_then_raises_server_error():
    for _ in range(5):
        responses.add(responses.POST, URL, body="oops", status=500)
    with pytest.raises(DevinServerError) as exc:
        _call(_make_client())
    assert exc.value.status == 500


@responses.activate
def test_500_then_200_retries_and_succeeds():
    responses.add(responses.POST, URL, body="oops", status=500)
    responses.add(
        responses.POST,
        URL,
        json={"session_id": "sess_ok", "url": "https://app.devin.ai/sessions/sess_ok"},
        status=200,
    )
    result = _call(_make_client())
    assert result.session_id == "sess_ok"
    # Should have hit the URL at least twice.
    assert len(responses.calls) >= 2


@responses.activate
def test_missing_session_id_in_2xx_is_server_error():
    responses.add(responses.POST, URL, json={"unrelated": True}, status=200)
    with pytest.raises(DevinServerError):
        _call(_make_client())
