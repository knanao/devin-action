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
GH_COMMENT_URL = "https://api.github.com/repos/knanao/example/issues/42/comments"


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
    monkeypatch.setenv("INPUT_GITHUB_TOKEN", "ghs_test")
    monkeypatch.setenv("INPUT_PROMPT_PREFIX", "/devin")
    monkeypatch.setenv("INPUT_ADDITIONAL_INSTRUCTIONS", "")
    monkeypatch.setenv("INPUT_DEVIN_MODE", "normal")
    monkeypatch.setenv("INPUT_MAX_ACU_LIMIT", "")
    monkeypatch.setenv("INPUT_TAGS", "github-action")
    monkeypatch.setenv("INPUT_PLAYBOOK_ID", "")
    monkeypatch.setenv("INPUT_ALLOWED_ASSOCIATIONS", "OWNER,MEMBER,COLLABORATOR")
    monkeypatch.setenv("INPUT_POST_COMMENT", "true")
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
    responses.add(
        responses.POST,
        DEVIN_URL,
        json={"session_id": "sess_1", "url": "https://app.devin.ai/sessions/sess_1"},
        status=200,
    )
    responses.add(responses.POST, GH_COMMENT_URL, json={"id": 1}, status=201)

    rc = main_mod.run()
    assert rc == 0

    outputs = _read_outputs(env["output_file"])
    assert outputs["session-id"] == "sess_1"
    assert outputs["session-url"] == "https://app.devin.ai/sessions/sess_1"
    assert outputs["skipped"] == "false"

    devin_call = responses.calls[0]
    payload = json.loads(devin_call.request.body)
    assert payload["session_secrets"][0]["value"] == "ghs_test"
    assert "<user_input>" in payload["prompt"]
    assert "devin-action:tracker=" in payload["prompt"]

    gh_call = responses.calls[1]
    gh_body = json.loads(gh_call.request.body)
    assert "https://app.devin.ai/sessions/sess_1" in gh_body["body"]
    assert "<!-- devin-action:tracker=" in gh_body["body"]


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
def test_devin_error_posts_failure_comment(env, monkeypatch):
    _stage_event(env["tmp_path"], monkeypatch, "issue_comment.json", "issue_comment")
    responses.add(responses.POST, DEVIN_URL, json={"detail": "bad key"}, status=401)
    responses.add(responses.POST, GH_COMMENT_URL, json={"id": 1}, status=201)

    rc = main_mod.run()
    assert rc == 1

    outputs = _read_outputs(env["output_file"])
    # No outputs were written by _emit_skip; only failure path was taken.
    assert outputs == {} or outputs.get("skipped") != "true"

    # Failure comment was posted.
    gh_calls = [c for c in responses.calls if "api.github.com" in c.request.url]
    assert len(gh_calls) == 1
    body = json.loads(gh_calls[0].request.body)
    assert body["body"].startswith(":x: Failed to start Devin session:")


@responses.activate
def test_post_comment_false_disables_tracking_comment_and_cleanup(env, monkeypatch):
    monkeypatch.setenv("INPUT_POST_COMMENT", "false")
    _stage_event(env["tmp_path"], monkeypatch, "issue_comment.json", "issue_comment")
    responses.add(
        responses.POST,
        DEVIN_URL,
        json={"session_id": "sess_1", "url": "https://app.devin.ai/sessions/sess_1"},
        status=200,
    )

    rc = main_mod.run()
    assert rc == 0

    payload = json.loads(responses.calls[0].request.body)
    assert "devin-action:tracker=" not in payload["prompt"]
    # Only Devin was called, no GitHub comment.
    assert all("api.github.com" not in c.request.url for c in responses.calls)


@responses.activate
def test_push_event_skips_tracking_comment_but_still_creates_session(env, monkeypatch):
    _stage_event(env["tmp_path"], monkeypatch, "push.json", "push")
    responses.add(
        responses.POST,
        DEVIN_URL,
        json={"session_id": "sess_push", "url": "https://app.devin.ai/sessions/sess_push"},
        status=200,
    )

    rc = main_mod.run()
    assert rc == 0
    # No issue number → no tracking comment attempted.
    assert all("api.github.com" not in c.request.url for c in responses.calls)
    outputs = _read_outputs(env["output_file"])
    assert outputs["session-id"] == "sess_push"
