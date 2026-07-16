"""Locate a reusable Devin session for a given GitHub thread.

Sessions are tagged with a canonical thread tag (see :func:`thread_tag`) at
creation time; on subsequent triggers for the same thread we look them up via
the Devin list-sessions API and pick the most recent still-live one.
"""

from __future__ import annotations

import sys

from .devin_client import DevinClient, SessionSummary

THREAD_TAG_PREFIX = "devin-action:thread:"


def thread_tag(thread_key: str) -> str:
    """Return the canonical Devin tag for a GitHub thread key.

    `thread_key` is expected to be `owner/repo#number` as produced by
    :func:`src.context._thread_key`.
    """
    return f"{THREAD_TAG_PREFIX}{thread_key}"


def find_reusable_session(
    client: DevinClient,
    thread_key: str,
    *,
    limit: int = 50,
) -> str | None:
    """Return the most recent reusable session id for `thread_key`, or None.

    We re-check the tag membership client-side because the Devin API's tag
    filter semantics are not fully documented, and we do not want to
    accidentally attach a message to a session from a different thread.
    """
    tag = thread_tag(thread_key)
    try:
        candidates = client.list_sessions(tags=[tag], limit=limit)
    except Exception as exc:  # noqa: BLE001 — resolver must never break creation flow
        # Surface the failure so users can debug, but keep the caller alive to
        # fall back to creating a new session.
        print(
            f"::warning::Session lookup failed ({exc.__class__.__name__}: {exc}); "
            "creating a new session instead.",
            flush=True,
            file=sys.stderr,
        )
        return None

    matching: list[SessionSummary] = [
        s for s in candidates if tag in s.tags and client.is_reusable(s)
    ]
    if not matching:
        return None
    matching.sort(key=lambda s: s.updated_ts, reverse=True)
    return matching[0].session_id
