from __future__ import annotations

import pytest
import responses

from src.devin_client import DEFAULT_BASE_URL, DevinClient, SessionSummary
from src.errors import (
    DevinAuthError,
    DevinNotFoundError,
    DevinPermissionError,
    DevinRateLimitError,
    DevinServerError,
    DevinSessionGoneError,
    DevinValidationError,
)

ORG = "org-test"
API_KEY = "cog_test"
URL = f"{DEFAULT_BASE_URL}/v3/organizations/{ORG}/sessions"
URL_V1 = f"{DEFAULT_BASE_URL}/v1/sessions"
MESSAGE_URL = f"{DEFAULT_BASE_URL}/v3/organizations/{ORG}/sessions/devin-abc/messages"
MESSAGE_URL_V1 = f"{DEFAULT_BASE_URL}/v1/sessions/devin-abc/message"


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
    assert "session_secrets" not in payload
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


class TestListSessions:
    @responses.activate
    def test_v3_returns_normalized_summaries(self):
        responses.add(
            responses.GET,
            URL,
            json={
                "items": [
                    {
                        "session_id": "devin-1",
                        "tags": ["devin-action:thread:knanao/example#42", "github-action"],
                        "status": "running",
                        "status_detail": "working",
                        "updated_at": 1_700_000_100,
                    },
                    {
                        "session_id": "devin-2",
                        "tags": ["devin-action:thread:knanao/example#42"],
                        "status": "exit",
                        "status_detail": "finished",
                        "updated_at": 1_700_000_050,
                    },
                ]
            },
            status=200,
        )
        client = _make_client()
        summaries = client.list_sessions(
            tags=["devin-action:thread:knanao/example#42"]
        )
        assert [s.session_id for s in summaries] == ["devin-1", "devin-2"]
        # tag filter propagated as query
        req_url = responses.calls[0].request.url
        assert "tags=devin-action%3Athread%3Aknanao%2Fexample%2342" in req_url
        assert "is_archived=false" in req_url

    @responses.activate
    def test_v1_uses_v1_shape(self):
        responses.add(
            responses.GET,
            URL_V1,
            json={
                "sessions": [
                    {
                        "session_id": "sess_1",
                        "status": "working",
                        "status_enum": "working",
                        "tags": ["devin-action:thread:knanao/example#42"],
                        "updated_at": "2025-01-02T03:04:05Z",
                    }
                ]
            },
            status=200,
        )
        client = _make_client("v1")
        summaries = client.list_sessions(
            tags=["devin-action:thread:knanao/example#42"]
        )
        assert summaries[0].session_id == "sess_1"
        assert summaries[0].status == "working"
        assert summaries[0].updated_ts > 0

    @responses.activate
    def test_error_propagates(self):
        responses.add(responses.GET, URL, json={"detail": "denied"}, status=403)
        client = _make_client()
        with pytest.raises(DevinPermissionError):
            client.list_sessions(tags=["x"])


class TestIsReusable:
    def _summary(self, **overrides) -> SessionSummary:
        base = dict(session_id="s", tags=[], status="running", status_detail=None)
        base.update(overrides)
        return SessionSummary(**base)

    def test_v3_running_is_reusable(self):
        assert _make_client().is_reusable(self._summary(status="running"))

    def test_v3_suspended_is_reusable(self):
        assert _make_client().is_reusable(self._summary(status="suspended"))

    def test_v3_exit_not_reusable(self):
        assert not _make_client().is_reusable(self._summary(status="exit"))

    def test_v3_running_with_dead_detail_not_reusable(self):
        s = self._summary(status="running", status_detail="out_of_credits")
        assert not _make_client().is_reusable(s)

    def test_v1_working_reusable(self):
        assert _make_client("v1").is_reusable(self._summary(status="working"))

    def test_v1_finished_not_reusable(self):
        assert not _make_client("v1").is_reusable(self._summary(status="finished"))


class TestSendMessage:
    @responses.activate
    def test_v3_success(self):
        responses.add(
            responses.POST,
            MESSAGE_URL,
            json={"session_id": "devin-abc", "status": "running"},
            status=200,
        )
        client = _make_client()
        client.send_message("devin-abc", "please continue")
        import json as _json
        body = _json.loads(responses.calls[0].request.body)
        assert body == {"message": "please continue"}

    @responses.activate
    def test_404_raises_session_gone(self):
        responses.add(responses.POST, MESSAGE_URL, json={"detail": "missing"}, status=404)
        client = _make_client()
        with pytest.raises(DevinSessionGoneError):
            client.send_message("devin-abc", "hi")

    @responses.activate
    def test_v1_success_null_body(self):
        responses.add(responses.POST, MESSAGE_URL_V1, body="null", status=200)
        client = _make_client("v1")
        client.send_message("devin-abc", "hi")

    @responses.activate
    def test_v1_suspended_detail_raises_session_gone(self):
        responses.add(
            responses.POST,
            MESSAGE_URL_V1,
            json={"detail": "session is already suspended"},
            status=200,
        )
        client = _make_client("v1")
        with pytest.raises(DevinSessionGoneError):
            client.send_message("devin-abc", "hi")

    @responses.activate
    def test_500_raises_server_error(self):
        for _ in range(5):
            responses.add(responses.POST, MESSAGE_URL, body="oops", status=500)
        client = _make_client()
        with pytest.raises(DevinServerError):
            client.send_message("devin-abc", "hi")
