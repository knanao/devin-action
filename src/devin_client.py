"""Thin Devin API client used by the action. Supports v1 and v3."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, NoReturn

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .errors import (
    DevinAuthError,
    DevinNetworkError,
    DevinNotFoundError,
    DevinPermissionError,
    DevinRateLimitError,
    DevinServerError,
    DevinSessionGoneError,
    DevinValidationError,
)

DEFAULT_BASE_URL = "https://api.devin.ai"
DEFAULT_API_VERSION = "v3"
SUPPORTED_API_VERSIONS = ("v1", "v3")
CONNECT_TIMEOUT = 10
READ_TIMEOUT = 30
RETRY_STATUS = (429, 500, 502, 503, 504)

# Sessions that can accept a follow-up message (v3 status enum).
_V3_LIVE_STATUS = frozenset(
    {"new", "claimed", "running", "suspended", "resuming"}
)
# status_detail values that indicate a hard failure even if `status` looks live.
_V3_DEAD_STATUS_DETAIL = frozenset(
    {
        "usage_limit_exceeded",
        "out_of_credits",
        "out_of_quota",
        "no_quota_allocation",
        "payment_declined",
        "org_usage_limit_exceeded",
        "total_session_limit_exceeded",
        "error",
    }
)
# v1 session status_enum values that can accept a follow-up message.
_V1_LIVE_STATUS = frozenset(
    {
        "working",
        "resumed",
        "resume_requested",
        "resume_requested_frontend",
        "blocked",
    }
)


@dataclass
class SessionResult:
    session_id: str
    url: str
    raw: dict[str, Any]


@dataclass(frozen=True)
class SessionSummary:
    """Normalized view of a Devin session as returned by list-sessions."""

    session_id: str
    tags: list[str] = field(default_factory=list)
    status: str = ""
    status_detail: str | None = None
    updated_ts: int = 0  # unix seconds, best-effort for sort


def _build_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1.0,
        status_forcelist=RETRY_STATUS,
        allowed_methods=frozenset(["POST", "GET"]),
        raise_on_status=False,
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


class DevinClient:
    def __init__(
        self,
        api_key: str,
        org_id: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        api_version: str = DEFAULT_API_VERSION,
        session: requests.Session | None = None,
    ) -> None:
        if api_version not in SUPPORTED_API_VERSIONS:
            raise ValueError(
                f"unsupported api_version {api_version!r}; "
                f"expected one of {SUPPORTED_API_VERSIONS}"
            )
        self._api_key = api_key
        self._org_id = org_id
        self._base_url = base_url.rstrip("/")
        self._api_version = api_version
        self._session = session or _build_session()

    @property
    def api_version(self) -> str:
        return self._api_version

    def _sessions_url(self) -> str:
        if self._api_version == "v1":
            return f"{self._base_url}/v1/sessions"
        return f"{self._base_url}/v3/organizations/{self._org_id}/sessions"

    def _message_url(self, session_id: str) -> str:
        if self._api_version == "v1":
            return f"{self._base_url}/v1/sessions/{session_id}/message"
        return (
            f"{self._base_url}/v3/organizations/{self._org_id}"
            f"/sessions/{session_id}/messages"
        )

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "User-Agent": "devin-action/1.0",
        }

    def create_session(
        self,
        *,
        prompt: str,
        repo: str,
        title: str,
        tags: list[str],
        devin_mode: str,
        max_acu_limit: int | None,
        playbook_id: str | None,
    ) -> SessionResult:
        url = self._sessions_url()
        body: dict[str, Any] = {
            "prompt": prompt,
            "repos": [repo],
            "title": title,
            "tags": tags,
            "devin_mode": devin_mode,
        }
        if max_acu_limit is not None:
            body["max_acu_limit"] = max_acu_limit
        if playbook_id:
            body["playbook_id"] = playbook_id

        try:
            response = self._session.post(
                url,
                json=body,
                headers=self._headers(),
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            )
        except requests.RequestException as exc:
            raise DevinNetworkError(str(exc)) from exc

        return self._handle_create_response(response)

    def list_sessions(
        self,
        *,
        tags: list[str] | None = None,
        limit: int = 50,
    ) -> list[SessionSummary]:
        """List sessions filtered by tags. Best-effort; single page only.

        Both v1 and v3 accept `tags` as a repeated query parameter. Callers
        should treat the returned list as candidates and re-verify tag matches
        client-side because filter semantics are not fully documented.
        """
        url = self._sessions_url()
        params: list[tuple[str, str]] = []
        for tag in tags or []:
            params.append(("tags", tag))
        if self._api_version == "v1":
            params.append(("limit", str(limit)))
        else:
            params.append(("first", str(min(limit, 200))))
            params.append(("is_archived", "false"))

        try:
            response = self._session.get(
                url,
                params=params,
                headers=self._headers(),
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            )
        except requests.RequestException as exc:
            raise DevinNetworkError(str(exc)) from exc

        if response.status_code >= 300:
            self._raise_for_status(response)
        data = self._json(response)
        return self._parse_session_list(data)

    def send_message(self, session_id: str, message: str) -> None:
        """Send a follow-up message to an existing session.

        Raises DevinSessionGoneError if the session can no longer accept
        messages (404, terminal state, or v1's "already suspended" reply).
        """
        url = self._message_url(session_id)
        try:
            response = self._session.post(
                url,
                json={"message": message},
                headers=self._headers(),
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            )
        except requests.RequestException as exc:
            raise DevinNetworkError(str(exc)) from exc

        status = response.status_code
        if status == 404:
            raise DevinSessionGoneError(session_id, "session not found (404)")
        if 200 <= status < 300:
            if self._api_version == "v1":
                # v1 returns {"detail": "..."} when the session is suspended.
                data = self._json(response)
                detail = data.get("detail") if isinstance(data, dict) else None
                if detail:
                    raise DevinSessionGoneError(session_id, str(detail))
            return
        self._raise_for_status(response)

    def is_reusable(self, summary: SessionSummary) -> bool:
        """Return True if the session should be able to accept a message."""
        if self._api_version == "v3":
            if summary.status not in _V3_LIVE_STATUS:
                return False
            return (summary.status_detail or "") not in _V3_DEAD_STATUS_DETAIL
        return summary.status in _V1_LIVE_STATUS

    def _handle_create_response(self, response: requests.Response) -> SessionResult:
        status = response.status_code
        if 200 <= status < 300:
            data = self._json(response)
            session_id = data.get("session_id") or data.get("id") or ""
            session_url = data.get("url") or data.get("session_url") or ""
            if not session_id:
                raise DevinServerError(status, "response missing session_id")
            return SessionResult(session_id=session_id, url=session_url, raw=data)
        self._raise_for_status(response)

    def _raise_for_status(self, response: requests.Response) -> NoReturn:
        status = response.status_code
        if status == 401:
            raise DevinAuthError
        if status == 403:
            raise DevinPermissionError(self._org_id)
        if status == 404:
            raise DevinNotFoundError(self._org_id)
        if status == 422:
            raise DevinValidationError(self._extract_422_detail(response))
        if status == 429:
            raise DevinRateLimitError
        raise DevinServerError(status, _truncate(response.text, 500))

    def _parse_session_list(self, data: dict[str, Any]) -> list[SessionSummary]:
        raw_items: list[Any]
        if self._api_version == "v1":
            raw_items = data.get("sessions") or []
        else:
            raw_items = data.get("items") or []
        summaries: list[SessionSummary] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            session_id = item.get("session_id") or item.get("id") or ""
            if not session_id:
                continue
            tags = item.get("tags") or []
            if not isinstance(tags, list):
                tags = []
            if self._api_version == "v1":
                status = item.get("status_enum") or item.get("status") or ""
                status_detail = None
            else:
                status = item.get("status") or ""
                status_detail = item.get("status_detail")
            summaries.append(
                SessionSummary(
                    session_id=session_id,
                    tags=[str(t) for t in tags],
                    status=str(status),
                    status_detail=str(status_detail) if status_detail else None,
                    updated_ts=_to_ts(item.get("updated_at")),
                )
            )
        return summaries

    @staticmethod
    def _json(response: requests.Response) -> dict[str, Any]:
        try:
            data = response.json()
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _extract_422_detail(response: requests.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return _truncate(response.text, 200)
        detail = payload.get("detail")
        if isinstance(detail, list) and detail:
            first = detail[0]
            if isinstance(first, dict) and first.get("msg"):
                return str(first["msg"])
        if isinstance(detail, str):
            return detail
        return _truncate(response.text, 200)


def _to_ts(value: Any) -> int:
    if value is None:
        return 0
    # bool is a subclass of int; guard it before the int/float branch so that
    # True/False are not silently coerced into timestamps 1/0.
    if isinstance(value, bool):
        return 0
    if isinstance(value, int | float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(
                datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
            )
        except ValueError:
            return 0
    return 0


def _truncate(text: str, limit: int) -> str:
    if text is None:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + "…"
