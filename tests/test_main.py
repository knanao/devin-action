from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import responses

from src import main as main_mod
from src.devin_client import DEFAULT_BASE_URL

FIXTURES = Path(__file__).parent / "fixtures"
DEVIN_URL = f"{DEFAULT_BASE_URL}/v3/organizations/org-test/sessions"
DEVIN_URL_V1 = f"{DEFAULT_BASE_URL}/v1/sessions"


@pytest.fixture
def env(tmp_path, monkeypatch):
    for key in list(k for k in ()):
        monkeypatch.delenv(key, raising=False)

    output_file = tmp_path / "gha_output"
    output_file.touch()
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))
    monkeypatch.setenv("GITHUB_REPOSITORY", "knanao/example")
    monkeypatch.setenv("INPUT_DEVIN_API_KEY", "cog_test")
    monkeypatch.setenv("INPUT_DEVIN_ORG_ID", "org-test")
    monkeypatch.setenv("INPUT_PROMPT_PREFIX", "/devin")
    monkeypatch.setenv("INPUT_ADDITIONAL_INSTRUCTIONS", "")
    monkeypatch.setenv("INPUT_DEVIN_MODE", "normal")
    monkeypatch.setenv("INPUT_MAX_ACU_LIMIT", "")
    monkeypatch.setenv("INPUT_TAGS", "github-action")
    monkeypatch.setenv("INPUT_PLAYBOOK_ID", "")
    monkeypatch.setenv("INPUT_ALLOWED_ASSOCIATIONS", "OWNER,MEMBER,COLLABORATOR")
    return {"tmp_path": tmp_path, "output_file": output_file}


def _read_outputs(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    lines = path.read_text().splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if "<<" in line:
            key, delim = line.split("<<", 1)
            buf = []
            i += 1
            while i < len(lines) and lines[i] != delim:
                buf.append(lines[i])
                i += 1
            result[key] = "\n".join(buf)
        elif "=" in line:
            key, _, value = line.partition("=")
            result[key] = value
        i += 1
    return result


def _stage_event(tmp_path: Path, monkeypatch, fixture_name: str, event_name: str) -> Path:
    src = FIXTURES / fixture_name
    dest = tmp_path / "event.json"
    shutil.copy(src, dest)
    monkeypatch.setenv("GITHUB_EVENT_NAME", event_name)
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(dest))
    return dest


@responses.activate
def test_happy_path_issue_comment(env, monkeypatch):
    _stage_event(env["tmp_path"], monkeypatch, "issue_comment.json", "issue_comment")
    # No prior session for this thread → resolver returns None → create flow runs.
    responses.add(responses.GET, DEVIN_URL, json={"items": []}, status=200)
    responses.add(
        responses.POST,
        DEVIN_URL,
        json={"session_id": "sess_1", "url": "https://app.devin.ai/sessions/sess_1"},
        status=200,
    )

    rc = main_mod.run()
    assert rc == 0

    outputs = _read_outputs(env["output_file"])
    assert outputs["session-id"] == "sess_1"
    assert outputs["session-url"] == "https://app.devin.ai/sessions/sess_1"
    assert outputs["skipped"] == "false"
    assert outputs["reused"] == "false"

    create_call = next(
        c for c in responses.calls if c.request.method == "POST"
    )
    payload = json.loads(create_call.request.body)
    assert "session_secrets" not in payload
    assert "<user_input>" in payload["prompt"]
    # No GitHub API calls should happen from the action.
    assert all("api.github.com" not in c.request.url for c in responses.calls)


@responses.activate
def test_skip_when_prefix_missing(env, monkeypatch):
    event_path = _stage_event(
        env["tmp_path"], monkeypatch, "issue_comment.json", "issue_comment"
    )
    payload = json.loads(event_path.read_text())
    payload["comment"]["body"] = "not a devin comment"
    event_path.write_text(json.dumps(payload))

    rc = main_mod.run()
    assert rc == 0
    outputs = _read_outputs(env["output_file"])
    assert outputs["skipped"] == "true"
    assert outputs["session-id"] == ""
    # Devin API must NOT be called.
    assert len(responses.calls) == 0


@responses.activate
def test_missing_required_input_exits_1(env, monkeypatch):
    monkeypatch.delenv("INPUT_DEVIN_API_KEY", raising=False)
    _stage_event(env["tmp_path"], monkeypatch, "issue_comment.json", "issue_comment")
    rc = main_mod.run()
    assert rc == 1


@responses.activate
def test_devin_error_returns_non_zero(env, monkeypatch):
    _stage_event(env["tmp_path"], monkeypatch, "issue_comment.json", "issue_comment")
    responses.add(responses.POST, DEVIN_URL, json={"detail": "bad key"}, status=401)

    rc = main_mod.run()
    assert rc == 1

    # No GitHub API calls attempted — the action no longer posts failure comments.
    assert all("api.github.com" not in c.request.url for c in responses.calls)


@responses.activate
def test_api_version_v1_hits_v1_endpoint(env, monkeypatch):
    monkeypatch.setenv("INPUT_API_VERSION", "v1")
    _stage_event(env["tmp_path"], monkeypatch, "issue_comment.json", "issue_comment")
    responses.add(responses.GET, DEVIN_URL_V1, json={"sessions": []}, status=200)
    responses.add(
        responses.POST,
        DEVIN_URL_V1,
        json={"session_id": "sess_v1", "url": "https://app.devin.ai/sessions/sess_v1"},
        status=200,
    )

    rc = main_mod.run()
    assert rc == 0
    create_call = next(c for c in responses.calls if c.request.method == "POST")
    assert create_call.request.url == DEVIN_URL_V1


def test_invalid_api_version_exits_1(env, monkeypatch):
    monkeypatch.setenv("INPUT_API_VERSION", "v2")
    _stage_event(env["tmp_path"], monkeypatch, "issue_comment.json", "issue_comment")
    rc = main_mod.run()
    assert rc == 1


@responses.activate
def test_push_event_creates_session(env, monkeypatch):
    _stage_event(env["tmp_path"], monkeypatch, "push.json", "push")
    responses.add(
        responses.POST,
        DEVIN_URL,
        json={"session_id": "sess_push", "url": "https://app.devin.ai/sessions/sess_push"},
        status=200,
    )

    rc = main_mod.run()
    assert rc == 0
    assert all("api.github.com" not in c.request.url for c in responses.calls)
    outputs = _read_outputs(env["output_file"])
    assert outputs["session-id"] == "sess_push"
    assert outputs["reused"] == "false"
    # Push events do not have a thread_key, so list_sessions must not be called.
    assert all(c.request.method != "GET" for c in responses.calls)


class TestSessionReuse:
    _THREAD_TAG = "devin-action:thread:knanao/example#42"

    @responses.activate
    def test_reuse_sends_message_and_marks_reused(self, env, monkeypatch):
        _stage_event(env["tmp_path"], monkeypatch, "issue_comment.json", "issue_comment")
        # list_sessions returns one live session with the thread tag
        responses.add(
            responses.GET,
            DEVIN_URL,
            json={
                "items": [
                    {
                        "session_id": "devin-existing",
                        "tags": [self._THREAD_TAG],
                        "status": "running",
                        "status_detail": "working",
                        "updated_at": 1_700_000_000,
                    }
                ]
            },
            status=200,
        )
        # send_message succeeds
        message_url = f"{DEVIN_URL}/devin-existing/messages"
        responses.add(
            responses.POST,
            message_url,
            json={"session_id": "devin-existing", "status": "running"},
            status=200,
        )

        rc = main_mod.run()
        assert rc == 0

        outputs = _read_outputs(env["output_file"])
        assert outputs["session-id"] == "devin-existing"
        assert outputs["reused"] == "true"
        assert outputs["skipped"] == "false"
        # No POST to /sessions (create), only GET list + POST /sessions/{id}/messages
        methods = [(c.request.method, c.request.url) for c in responses.calls]
        assert any(m == "GET" and url.startswith(DEVIN_URL) for m, url in methods)
        assert any(m == "POST" and message_url in url for m, url in methods)
        assert not any(
            m == "POST" and url.rstrip("/").endswith("/sessions") for m, url in methods
        )

    @responses.activate
    def test_falls_back_to_create_when_no_live_session(self, env, monkeypatch):
        _stage_event(env["tmp_path"], monkeypatch, "issue_comment.json", "issue_comment")
        # list_sessions returns only terminal sessions
        responses.add(
            responses.GET,
            DEVIN_URL,
            json={
                "items": [
                    {
                        "session_id": "devin-old",
                        "tags": [self._THREAD_TAG],
                        "status": "exit",
                        "status_detail": "finished",
                        "updated_at": 1_700_000_000,
                    }
                ]
            },
            status=200,
        )
        responses.add(
            responses.POST,
            DEVIN_URL,
            json={"session_id": "sess_new", "url": "https://app.devin.ai/sessions/sess_new"},
            status=200,
        )

        rc = main_mod.run()
        assert rc == 0

        outputs = _read_outputs(env["output_file"])
        assert outputs["session-id"] == "sess_new"
        assert outputs["reused"] == "false"

        # Confirm the thread tag was added on creation
        create_call = next(
            c for c in responses.calls
            if c.request.method == "POST" and c.request.url.rstrip("/").endswith("/sessions")
        )
        payload = json.loads(create_call.request.body)
        assert self._THREAD_TAG in payload["tags"]

    @responses.activate
    def test_falls_back_when_send_message_reports_gone(self, env, monkeypatch):
        _stage_event(env["tmp_path"], monkeypatch, "issue_comment.json", "issue_comment")
        responses.add(
            responses.GET,
            DEVIN_URL,
            json={
                "items": [
                    {
                        "session_id": "devin-stale",
                        "tags": [self._THREAD_TAG],
                        "status": "running",
                        "status_detail": "working",
                        "updated_at": 1_700_000_000,
                    }
                ]
            },
            status=200,
        )
        # send_message returns 404 → SessionGone → fallback
        responses.add(
            responses.POST,
            f"{DEVIN_URL}/devin-stale/messages",
            json={"detail": "missing"},
            status=404,
        )
        responses.add(
            responses.POST,
            DEVIN_URL,
            json={"session_id": "sess_new", "url": "https://app.devin.ai/sessions/sess_new"},
            status=200,
        )

        rc = main_mod.run()
        assert rc == 0
        outputs = _read_outputs(env["output_file"])
        assert outputs["session-id"] == "sess_new"
        assert outputs["reused"] == "false"

    @responses.activate
    def test_force_new_skips_lookup(self, env, monkeypatch):
        event_path = _stage_event(
            env["tmp_path"], monkeypatch, "issue_comment.json", "issue_comment"
        )
        payload = json.loads(event_path.read_text())
        payload["comment"]["body"] = "/devin new redo everything"
        event_path.write_text(json.dumps(payload))

        responses.add(
            responses.POST,
            DEVIN_URL,
            json={"session_id": "sess_new", "url": "https://app.devin.ai/sessions/sess_new"},
            status=200,
        )

        rc = main_mod.run()
        assert rc == 0
        outputs = _read_outputs(env["output_file"])
        assert outputs["session-id"] == "sess_new"
        assert outputs["reused"] == "false"
        # No list-sessions call
        assert all(c.request.method != "GET" for c in responses.calls)

    @responses.activate
    def test_session_reuse_disabled_skips_lookup(self, env, monkeypatch):
        monkeypatch.setenv("INPUT_SESSION_REUSE", "false")
        _stage_event(env["tmp_path"], monkeypatch, "issue_comment.json", "issue_comment")
        responses.add(
            responses.POST,
            DEVIN_URL,
            json={"session_id": "sess_new", "url": "https://app.devin.ai/sessions/sess_new"},
            status=200,
        )

        rc = main_mod.run()
        assert rc == 0
        assert all(c.request.method != "GET" for c in responses.calls)
