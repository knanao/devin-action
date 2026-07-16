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
from . import resolver as resolver_mod
from .devin_client import (
    DEFAULT_API_VERSION,
    SUPPORTED_API_VERSIONS,
    DevinClient,
    SessionResult,
)
from .errors import (
    DevinActionError,
    DevinAPIError,
    DevinSessionGoneError,
    InvalidInputError,
    MissingInputError,
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
    if not raw:
        return default
    if raw in {"true", "1", "yes", "on"}:
        return True
    if raw in {"false", "0", "no", "off"}:
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
    _write_outputs(
        **{
            "session-id": "",
            "session-url": "",
            "skipped": "true",
            "reused": "false",
        }
    )
    return 0


def _emit_success(*, session_id: str, url: str, reused: bool) -> None:
    _write_outputs(
        **{
            "session-id": session_id,
            "session-url": url,
            "skipped": "false",
            "reused": "true" if reused else "false",
        }
    )


def run() -> int:
    try:
        api_key = _required("devin-api-key")
        org_id = _required("devin-org-id")
    except MissingInputError as exc:
        _error(exc.user_message())
        return 1

    _mask(api_key)

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
        api_version = _optional("api-version", DEFAULT_API_VERSION)
        if api_version not in SUPPORTED_API_VERSIONS:
            raise InvalidInputError(
                "api-version",
                f"must be one of {', '.join(SUPPORTED_API_VERSIONS)}",
            )
        session_reuse = _bool("session-reuse", True)
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

    client = DevinClient(api_key, org_id, api_version=api_version)

    try:
        if _should_try_reuse(ctx, session_reuse):
            reused = _try_send_to_existing(client, ctx, additional_instructions)
            if reused is not None:
                session_id, session_url = reused
                _log(
                    f"Devin session reused: {session_id}"
                    + (f" ({session_url})" if session_url else "")
                )
                _emit_success(session_id=session_id, url=session_url, reused=True)
                return 0

        result = _create_new_session(
            client,
            ctx,
            additional_instructions=additional_instructions,
            tags=tags,
            devin_mode=devin_mode,
            max_acu_limit=max_acu_limit,
            playbook_id=playbook_id,
        )
    except DevinAPIError as exc:
        _error(exc.user_message())
        return 1
    except DevinActionError as exc:
        _error(exc.user_message())
        return 1

    _log(f"Devin session created: {result.session_id} ({result.url})")
    _emit_success(session_id=result.session_id, url=result.url, reused=False)
    return 0


def _should_try_reuse(
    ctx: context_mod.SessionContext, session_reuse_enabled: bool
) -> bool:
    if not session_reuse_enabled:
        return False
    if ctx.force_new:
        return False
    return bool(ctx.thread_key)


def _try_send_to_existing(
    client: DevinClient,
    ctx: context_mod.SessionContext,
    additional_instructions: str,
) -> tuple[str, str] | None:
    """Look up a reusable session and, if found, send the follow-up message.

    Returns (session_id, session_url) on success, or None when we should fall
    back to creating a new session. Session URL is best-effort: the Devin
    message endpoint does not return it on v1, so callers may see an empty
    string when the reused session was originally opened via v1.
    """
    assert ctx.thread_key is not None  # guarded by _should_try_reuse
    session_id = resolver_mod.find_reusable_session(client, ctx.thread_key)
    if not session_id:
        return None

    message = prompt_mod.build_continuation(
        ctx, additional_instructions=additional_instructions
    )
    try:
        client.send_message(session_id, message)
    except DevinSessionGoneError as exc:
        _log(f"Reusable session {session_id} rejected message ({exc.reason}); creating new.")
        return None

    return session_id, _session_url_for(session_id)


def _create_new_session(
    client: DevinClient,
    ctx: context_mod.SessionContext,
    *,
    additional_instructions: str,
    tags: list[str],
    devin_mode: str,
    max_acu_limit: int | None,
    playbook_id: str | None,
) -> SessionResult:
    final_prompt = prompt_mod.build(
        ctx, additional_instructions=additional_instructions
    )
    final_tags = list(tags)
    if ctx.thread_key:
        thread_tag = resolver_mod.thread_tag(ctx.thread_key)
        if thread_tag not in final_tags:
            final_tags.append(thread_tag)
    return client.create_session(
        prompt=final_prompt,
        repo=ctx.repo,
        title=ctx.title or ctx.repo,
        tags=final_tags,
        devin_mode=devin_mode,
        max_acu_limit=max_acu_limit,
        playbook_id=playbook_id,
    )


def _session_url_for(session_id: str) -> str:
    return f"https://app.devin.ai/sessions/{session_id}"


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
