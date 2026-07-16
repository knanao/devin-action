from __future__ import annotations

import pytest

from src import context as ctx_mod

DEFAULT_PREFIX = "/devin"
DEFAULT_ALLOWED = ["OWNER", "MEMBER", "COLLABORATOR"]


def _extract(event: str, payload: dict, *, prefix: str = DEFAULT_PREFIX,
             allowed=None) -> ctx_mod.SessionContext:
    return ctx_mod.extract(
        event,
        payload,
        "knanao/example",
        prompt_prefix=prefix,
        allowed_associations=allowed if allowed is not None else DEFAULT_ALLOWED,
    )


class TestIssueComment:
    def test_matches_prefix(self, load_fixture):
        payload = load_fixture("issue_comment.json")
        ctx = _extract("issue_comment", payload)
        assert not ctx.skip
        assert ctx.issue_or_pr_number == 42
        assert ctx.user_login == "knanao"
        assert ctx.user_prompt.startswith("please investigate")
        assert ctx.title == "[GH] Something is broken"
        assert "Repro:" in ctx.issue_body

    def test_issue_body_empty_when_absent(self, load_fixture):
        payload = load_fixture("issue_comment.json")
        payload["issue"].pop("body", None)
        ctx = _extract("issue_comment", payload)
        assert not ctx.skip
        assert ctx.issue_body == ""

    def test_skips_when_prefix_missing(self, load_fixture):
        payload = load_fixture("issue_comment.json")
        payload["comment"]["body"] = "hello without prefix"
        ctx = _extract("issue_comment", payload)
        assert ctx.skip
        assert "prefix" in (ctx.skip_reason or "")

    def test_skips_when_prefix_is_a_substring_boundary(self, load_fixture):
        payload = load_fixture("issue_comment.json")
        payload["comment"]["body"] = "/devinfoo bar"
        ctx = _extract("issue_comment", payload)
        assert ctx.skip

    def test_skips_when_association_disallowed(self, load_fixture):
        payload = load_fixture("issue_comment.json")
        payload["comment"]["author_association"] = "NONE"
        ctx = _extract("issue_comment", payload)
        assert ctx.skip
        assert "author_association" in (ctx.skip_reason or "")

    def test_pr_flag_set_for_issue_comment_on_pr(self, load_fixture):
        payload = load_fixture("issue_comment_pr.json")
        ctx = _extract("issue_comment", payload)
        assert not ctx.skip
        assert ctx.extra_context.get("is_pull_request") == "true"
        assert ctx.user_prompt == "rebase this branch onto main"


class TestPullRequest:
    def test_extracts_title_body_and_refs(self, load_fixture):
        payload = load_fixture("pull_request.json")
        ctx = _extract("pull_request", payload)
        assert not ctx.skip
        assert ctx.issue_or_pr_number == 12
        assert ctx.extra_context["pr_head_sha"] == "abc123"
        assert ctx.extra_context["pr_base_ref"] == "main"
        assert ctx.extra_context["pr_action"] == "opened"
        assert "Refactor auth" in ctx.user_prompt


class TestReviewComment:
    def test_extracts_file_and_line(self, load_fixture):
        payload = load_fixture("pull_request_review_comment.json")
        ctx = _extract("pull_request_review_comment", payload)
        assert not ctx.skip
        assert ctx.issue_or_pr_number == 12
        assert ctx.extra_context["file_path"] == "src/auth/session.py"
        assert ctx.extra_context["line"] == "42"

    def test_skip_when_prefix_missing(self, load_fixture):
        payload = load_fixture("pull_request_review_comment.json")
        payload["comment"]["body"] = "nit"
        ctx = _extract("pull_request_review_comment", payload)
        assert ctx.skip


class TestPush:
    def test_extracts_ref_and_commit(self, load_fixture):
        payload = load_fixture("push.json")
        ctx = _extract("push", payload)
        assert not ctx.skip
        assert ctx.extra_context["ref"] == "refs/heads/main"
        assert ctx.extra_context["head_commit_sha"] == "bbb222"
        assert ctx.user_login == "knanao"


class TestCheckRun:
    def test_extracts_conclusion_and_pr(self, load_fixture):
        payload = load_fixture("check_run.json")
        ctx = _extract("check_run", payload)
        assert not ctx.skip
        assert ctx.extra_context["check_conclusion"] == "failure"
        assert ctx.issue_or_pr_number == 12


class TestUnsupportedEvent:
    def test_returns_skip(self):
        ctx = _extract("workflow_dispatch", {})
        assert ctx.skip
        assert "unsupported" in (ctx.skip_reason or "")


class TestThreadKey:
    def test_issue_comment_sets_thread_key(self, load_fixture):
        payload = load_fixture("issue_comment.json")
        ctx = _extract("issue_comment", payload)
        assert ctx.thread_key == "knanao/example#42"
        assert ctx.force_new is False

    def test_review_comment_sets_thread_key(self, load_fixture):
        payload = load_fixture("pull_request_review_comment.json")
        ctx = _extract("pull_request_review_comment", payload)
        assert ctx.thread_key == "knanao/example#12"

    def test_pull_request_sets_thread_key(self, load_fixture):
        payload = load_fixture("pull_request.json")
        ctx = _extract("pull_request", payload)
        assert ctx.thread_key == "knanao/example#12"

    def test_push_has_no_thread_key(self, load_fixture):
        payload = load_fixture("push.json")
        ctx = _extract("push", payload)
        assert ctx.thread_key is None

    def test_check_run_has_no_thread_key(self, load_fixture):
        payload = load_fixture("check_run.json")
        ctx = _extract("check_run", payload)
        assert ctx.thread_key is None


class TestForceNew:
    def test_issue_comment_devin_new_sets_flag_and_strips(self, load_fixture):
        payload = load_fixture("issue_comment.json")
        payload["comment"]["body"] = "/devin new redo everything"
        ctx = _extract("issue_comment", payload)
        assert not ctx.skip
        assert ctx.force_new is True
        assert ctx.user_prompt == "redo everything"

    def test_word_that_starts_with_new_is_not_force_new(self, load_fixture):
        payload = load_fixture("issue_comment.json")
        payload["comment"]["body"] = "/devin newsletter improvements"
        ctx = _extract("issue_comment", payload)
        assert not ctx.skip
        assert ctx.force_new is False
        assert ctx.user_prompt == "newsletter improvements"

    def test_review_comment_devin_new_sets_flag(self, load_fixture):
        payload = load_fixture("pull_request_review_comment.json")
        payload["comment"]["body"] = "/devin new please rewrite this"
        ctx = _extract("pull_request_review_comment", payload)
        assert not ctx.skip
        assert ctx.force_new is True
        assert ctx.user_prompt == "please rewrite this"


@pytest.mark.parametrize(
    "body, expected",
    [
        ("/devin do the thing", "do the thing"),
        ("  /devin  spaced ", "spaced"),
        ("/devin\nwith newline body", "with newline body"),
    ],
)
def test_prefix_match_variations(body, expected, load_fixture):
    payload = load_fixture("issue_comment.json")
    payload["comment"]["body"] = body
    ctx = _extract("issue_comment", payload)
    assert not ctx.skip
    assert ctx.user_prompt == expected
