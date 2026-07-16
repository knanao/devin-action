"""Thin Devin API V3 client used by the action."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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
    DevinValidationError,
)

DEFAULT_BASE_URL = "https://api.devin.ai"
CONNECT_TIMEOUT = 10
READ_TIMEOUT = 30
RETRY_STATUS = (429, 500, 502, 503, 504)


@dataclass
class SessionResult:
    session_id: str
    url: str
    raw: dict[str, Any]


def _build_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1.0,
        status_forcelist=RETRY_STATUS,
        allowed_methods=frozenset(["POST"]),
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
        session: requests.Session | None = None,
    ) -> None:
        self._api_key = api_key
        self._org_id = org_id
        self._base_url = base_url.rstrip("/")
        self._session = session or _build_session()

    def create_session(
        self,
        *,
        prompt: str,
        repo: str,
        github_token: str,
        title: str,
        tags: list[str],
        devin_mode: str,
        max_acu_limit: int | None,
        playbook_id: str | None,
    ) -> SessionResult:
        url = f"{self._base_url}/v3/organizations/{self._org_id}/sessions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "User-Agent": "devin-action/1.0",
        }
        body: dict[str, Any] = {
            "prompt": prompt,
            "repos": [repo],
            "session_secrets": [
                {"key": "GITHUB_TOKEN", "value": github_token, "sensitive": True},
            ],
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
                headers=headers,
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            )
        except requests.RequestException as exc:
            raise DevinNetworkError(str(exc)) from exc

        return self._handle_response(response)

    def _handle_response(self, response: requests.Response) -> SessionResult:
        status = response.status_code
        if 200 <= status < 300:
            data = self._json(response)
            session_id = data.get("session_id") or data.get("id") or ""
            session_url = data.get("url") or data.get("session_url") or ""
            if not session_id:
                raise DevinServerError(status, "response missing session_id")
            return SessionResult(session_id=session_id, url=session_url, raw=data)

        if status == 401:
            raise DevinAuthError()
        if status == 403:
            raise DevinPermissionError(self._org_id)
        if status == 404:
            raise DevinNotFoundError(self._org_id)
        if status == 422:
            raise DevinValidationError(self._extract_422_detail(response))
        if status == 429:
            raise DevinRateLimitError()
        raise DevinServerError(status, _truncate(response.text, 500))

    @staticmethod
    def _json(response: requests.Response) -> dict[str, Any]:
        try:
            return response.json()
        except ValueError:
            return {}

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


def _truncate(text: str, limit: int) -> str:
    if text is None:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + "…"
