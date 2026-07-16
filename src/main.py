"""GitHub Action entrypoint."""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

from . import context as context_mod
from . import prompt as prompt_mod
from .devin_client import DevinClient, SessionResult
from .errors import (
    DevinActionError,
    DevinAPIError,
    InvalidInputError,
    MissingInputError,
)
from .github_client import (
    GitHubClient,
    build_failure_comment,
    build_tracking_comment,
)


def _log(message: str) -> None:
    print(message, flush=True)


def _error(message: str) -> None:
    print(f"::error::{message}", flush=True, file=sys.stderr)


def _mask(value: str) -> None:
    if not value:
        return
    print(f"::add-mask::{value}", flush=True)


def _required(name: str) -> str:
    value = os.environ.get(f"INPUT_{name.upper().replace('-', '_')}", "").strip()
    if not value:
        raise MissingInputError(name)
    return value


def _optional(name: str, default: str = "") -> str:
    value = os.environ.get(f"INPUT_{name.upper().replace('-', '_')}")
    if value is None:
        return default
    stripped = value.strip()
    return stripped if stripped else default


def _bool(name: str, default: bool) -> bool:
    raw = _optional(name, "").lower()
    if raw == "":
        return default
    if raw in {"true", "1", "yes", "y"}:
        return True
    if raw in {"false", "0", "no", "n"}:
        return False
    raise InvalidInputError(name, "must be a boolean (true/false)")


def _int_or_none(name: str) -> int | None:
    raw = _optional(name, "")
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise InvalidInputError(name, "must be an integer") from exc


def _list(name: str, default: str) -> list[str]:
    raw = _optional(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def _load_event_payload(path: str | None) -> dict[str, Any]:
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _write_outputs(**outputs: str) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        for key, value in outputs.items():
            _log(f"[output] {key}={value}")
        return
    with Path(output_path).open("a", encoding="utf-8") as fh:
        for key, value in outputs.items():
            if "\n" in value:
                delimiter = f"__EOF_{uuid.uuid4().hex}__"
                fh.write(f"{key}<<{delimiter}\n{value}\n{delimiter}\n")
            else:
                fh.write(f"{key}={value}\n")


def _emit_skip(reason: str) -> int:
    _log(f"Skipping: {reason}")
    _write_outputs(**{"session-id": "", "session-url": "", "skipped": "true"})
    return 0


def _post_failure_comment(
    github_token: str,
    repo: str,
    number: int | None,
    message: str,
) -> None:
    if number is None:
        return
    try:
        client = GitHubClient(github_token)
        client.post_issue_comment(repo, number, build_failure_comment(message))
    except Exception as exc:  # noqa: BLE001 — best-effort notification
        _log(f"Failed to post failure comment: {exc}")


def run() -> int:
    try:
        api_key = _required("devin-api-key")
        org_id = _required("devin-org-id")
        github_token = _required("github-token")
    except MissingInputError as exc:
        _error(exc.user_message())
        return 1

    _mask(api_key)
    _mask(github_token)

    try:
        prompt_prefix = _optional("prompt-prefix", "/devin")
        additional_instructions = _optional("additional-instructions", "")
        devin_mode = _optional("devin-mode", "normal")
        max_acu_limit = _int_or_none("max-acu-limit")
        tags = _list("tags", "github-action")
        playbook_id = _optional("playbook-id", "") or None
        allowed_associations = _list(
            "allowed-associations", "OWNER,MEMBER,COLLABORATOR"
        )
        post_comment = _bool("post-comment", True)
    except InvalidInputError as exc:
        _error(exc.user_message())
        return 1

    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    event_path = os.environ.get("GITHUB_EVENT_PATH")

    if not event_name:
        _error("GITHUB_EVENT_NAME is not set")
        return 1
    if not repo:
        _error("GITHUB_REPOSITORY is not set")
        return 1

    payload = _load_event_payload(event_path)

    try:
        ctx = context_mod.extract(
            event_name,
            payload,
            repo,
            prompt_prefix=prompt_prefix,
            allowed_associations=allowed_associations,
        )
    except Exception as exc:  # noqa: BLE001 — payload malformed
        _error(f"Failed to parse event payload: {exc}")
        return 1

    if ctx.skip:
        return _emit_skip(ctx.skip_reason or "context extractor requested skip")

    tracker_id: str | None = None
    if post_comment and ctx.issue_or_pr_number is not None:
        tracker_id = str(uuid.uuid4())

    final_prompt = prompt_mod.build(
        ctx,
        additional_instructions=additional_instructions,
        tracker_id=tracker_id,
    )

    client = DevinClient(api_key, org_id)
    try:
        result: SessionResult = client.create_session(
            prompt=final_prompt,
            repo=repo,
            github_token=github_token,
            title=ctx.title or repo,
            tags=tags,
            devin_mode=devin_mode,
            max_acu_limit=max_acu_limit,
            playbook_id=playbook_id,
        )
    except DevinAPIError as exc:
        _error(exc.user_message())
        if post_comment:
            _post_failure_comment(
                github_token, repo, ctx.issue_or_pr_number, exc.user_message()
            )
        return 1
    except DevinActionError as exc:
        _error(exc.user_message())
        return 1

    _log(f"Devin session created: {result.session_id} ({result.url})")

    if post_comment and ctx.issue_or_pr_number is not None and tracker_id is not None:
        try:
            gh = GitHubClient(github_token)
            gh.post_issue_comment(
                repo,
                ctx.issue_or_pr_number,
                build_tracking_comment(result.url, tracker_id),
            )
        except Exception as exc:  # noqa: BLE001 — non-fatal
            _log(f"Failed to post tracking comment: {exc}")

    _write_outputs(
        **{
            "session-id": result.session_id,
            "session-url": result.url,
            "skipped": "false",
        }
    )
    return 0


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
