"""Extract a normalized SessionContext from a GitHub Actions event payload."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SUPPORTED_EVENTS = {
    "issue_comment",
    "pull_request",
    "pull_request_review_comment",
    "push",
    "check_run",
}


@dataclass
class SessionContext:
    event_name: str
    repo: str
    title: str
    issue_or_pr_number: int | None
    comment_url: str | None
    user_login: str
    user_prompt: str
    extra_context: dict[str, str] = field(default_factory=dict)
    skip: bool = False
    skip_reason: str | None = None


def _skipped(event_name: str, repo: str, reason: str) -> SessionContext:
    return SessionContext(
        event_name=event_name,
        repo=repo,
        title="",
        issue_or_pr_number=None,
        comment_url=None,
        user_login="",
        user_prompt="",
        skip=True,
        skip_reason=reason,
    )


def _match_prefix(body: str, prefix: str) -> str | None:
    """Return the trimmed prompt if `body` begins with `prefix`; otherwise None.

    A trailing whitespace or newline right after the prefix is required so that
    e.g. `/devin-something` does not accidentally trigger `/devin`.
    """
    if not body:
        return None
    stripped = body.lstrip()
    if not stripped.startswith(prefix):
        return None
    after = stripped[len(prefix) :]
    if after and not after[0].isspace():
        return None
    return after.strip()


def _association_allowed(association: str | None, allowed: list[str]) -> bool:
    if not allowed:
        return True
    return (association or "").upper() in {a.upper() for a in allowed}


def extract(
    event_name: str,
    payload: dict[str, Any],
    repo: str,
    *,
    prompt_prefix: str,
    allowed_associations: list[str],
) -> SessionContext:
    if event_name not in SUPPORTED_EVENTS:
        return _skipped(event_name, repo, f"unsupported event: {event_name}")

    if event_name == "issue_comment":
        return _from_issue_comment(payload, repo, prompt_prefix, allowed_associations)
    if event_name == "pull_request":
        return _from_pull_request(payload, repo)
    if event_name == "pull_request_review_comment":
        return _from_review_comment(payload, repo, prompt_prefix, allowed_associations)
    if event_name == "push":
        return _from_push(payload, repo)
    if event_name == "check_run":
        return _from_check_run(payload, repo)

    return _skipped(event_name, repo, f"unhandled event: {event_name}")


def _from_issue_comment(
    payload: dict[str, Any],
    repo: str,
    prompt_prefix: str,
    allowed_associations: list[str],
) -> SessionContext:
    comment = payload.get("comment") or {}
    issue = payload.get("issue") or {}
    body = comment.get("body", "") or ""

    prompt_body = _match_prefix(body, prompt_prefix)
    if prompt_body is None:
        return _skipped("issue_comment", repo, "comment does not start with prompt prefix")

    if not _association_allowed(comment.get("author_association"), allowed_associations):
        return _skipped(
            "issue_comment",
            repo,
            f"author_association {comment.get('author_association')!r} not allowed",
        )

    number = issue.get("number")
    user_login = (comment.get("user") or {}).get("login", "")
    title = issue.get("title") or f"Issue #{number}"
    is_pr = bool(issue.get("pull_request"))
    extra = {"is_pull_request": "true" if is_pr else "false"}
    if issue.get("html_url"):
        extra["issue_url"] = issue["html_url"]
    return SessionContext(
        event_name="issue_comment",
        repo=repo,
        title=f"[GH] {title}",
        issue_or_pr_number=number,
        comment_url=comment.get("html_url"),
        user_login=user_login,
        user_prompt=prompt_body,
        extra_context=extra,
    )


def _from_pull_request(payload: dict[str, Any], repo: str) -> SessionContext:
    pr = payload.get("pull_request") or {}
    number = pr.get("number")
    title = pr.get("title") or f"PR #{number}"
    body = pr.get("body") or ""
    user_login = (pr.get("user") or {}).get("login", "")
    head = pr.get("head") or {}
    base = pr.get("base") or {}
    extra = {
        "pr_number": str(number) if number is not None else "",
        "pr_head_sha": head.get("sha", ""),
        "pr_head_ref": head.get("ref", ""),
        "pr_base_ref": base.get("ref", ""),
        "pr_action": payload.get("action", ""),
    }
    if pr.get("diff_url"):
        extra["pr_diff_url"] = pr["diff_url"]
    return SessionContext(
        event_name="pull_request",
        repo=repo,
        title=f"[GH] {title}",
        issue_or_pr_number=number,
        comment_url=pr.get("html_url"),
        user_login=user_login,
        user_prompt=f"{title}\n\n{body}".strip(),
        extra_context=extra,
    )


def _from_review_comment(
    payload: dict[str, Any],
    repo: str,
    prompt_prefix: str,
    allowed_associations: list[str],
) -> SessionContext:
    comment = payload.get("comment") or {}
    pr = payload.get("pull_request") or {}
    body = comment.get("body", "") or ""

    prompt_body = _match_prefix(body, prompt_prefix)
    if prompt_body is None:
        return _skipped(
            "pull_request_review_comment",
            repo,
            "review comment does not start with prompt prefix",
        )

    if not _association_allowed(comment.get("author_association"), allowed_associations):
        return _skipped(
            "pull_request_review_comment",
            repo,
            f"author_association {comment.get('author_association')!r} not allowed",
        )

    number = pr.get("number")
    user_login = (comment.get("user") or {}).get("login", "")
    title = pr.get("title") or f"PR #{number}"
    extra: dict[str, str] = {
        "pr_number": str(number) if number is not None else "",
        "file_path": comment.get("path", "") or "",
    }
    line = comment.get("line") or comment.get("original_line")
    if line is not None:
        extra["line"] = str(line)
    if comment.get("commit_id"):
        extra["commit_id"] = comment["commit_id"]
    return SessionContext(
        event_name="pull_request_review_comment",
        repo=repo,
        title=f"[GH] Review comment on {title}",
        issue_or_pr_number=number,
        comment_url=comment.get("html_url"),
        user_login=user_login,
        user_prompt=prompt_body,
        extra_context=extra,
    )


def _from_push(payload: dict[str, Any], repo: str) -> SessionContext:
    head_commit = payload.get("head_commit") or {}
    ref = payload.get("ref", "")
    pusher = payload.get("pusher") or {}
    sender = payload.get("sender") or {}
    user_login = pusher.get("name") or sender.get("login") or ""
    message = head_commit.get("message") or ""
    title = message.splitlines()[0] if message else f"Push to {ref}"
    extra = {
        "ref": ref,
        "before": payload.get("before", "") or "",
        "after": payload.get("after", "") or "",
    }
    if head_commit.get("id"):
        extra["head_commit_sha"] = head_commit["id"]
    if head_commit.get("message"):
        extra["head_commit_message"] = head_commit["message"]
    return SessionContext(
        event_name="push",
        repo=repo,
        title=f"[GH] {title}",
        issue_or_pr_number=None,
        comment_url=None,
        user_login=user_login,
        user_prompt=message,
        extra_context=extra,
    )


def _from_check_run(payload: dict[str, Any], repo: str) -> SessionContext:
    check_run = payload.get("check_run") or {}
    name = check_run.get("name") or "check_run"
    conclusion = check_run.get("conclusion") or ""
    status = check_run.get("status") or ""
    sender = payload.get("sender") or {}
    user_login = sender.get("login", "")
    pull_requests = check_run.get("pull_requests") or []
    number: int | None = None
    if pull_requests:
        number = pull_requests[0].get("number")
    extra = {
        "check_name": name,
        "check_status": status,
        "check_conclusion": conclusion,
    }
    if check_run.get("html_url"):
        extra["check_url"] = check_run["html_url"]
    if check_run.get("head_sha"):
        extra["head_sha"] = check_run["head_sha"]
    return SessionContext(
        event_name="check_run",
        repo=repo,
        title=f"[GH] check_run {name} ({conclusion or status})",
        issue_or_pr_number=number,
        comment_url=check_run.get("html_url"),
        user_login=user_login,
        user_prompt="",
        extra_context=extra,
    )
