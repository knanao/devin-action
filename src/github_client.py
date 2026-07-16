"""Small GitHub REST client for posting comments back to the Issue/PR."""

from __future__ import annotations

import requests

DEFAULT_API_URL = "https://api.github.com"
CONNECT_TIMEOUT = 10
READ_TIMEOUT = 30
USER_AGENT = "devin-action/1.0"


class GitHubClient:
    def __init__(
        self,
        token: str,
        *,
        api_url: str = DEFAULT_API_URL,
        session: requests.Session | None = None,
    ) -> None:
        self._token = token
        self._api_url = api_url.rstrip("/")
        self._session = session or requests.Session()

    def post_issue_comment(self, repo: str, number: int, body: str) -> dict:
        """Post a comment on an Issue or PR (issues endpoint works for both)."""
        url = f"{self._api_url}/repos/{repo}/issues/{number}/comments"
        response = self._session.post(
            url,
            json={"body": body},
            headers=self._headers(),
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
        )
        response.raise_for_status()
        try:
            return response.json()
        except ValueError:
            return {}

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": USER_AGENT,
        }


def build_tracking_comment(session_url: str, tracker_id: str) -> str:
    body = "Session started"
    if session_url:
        body = f"Session started: {session_url}"
    return f"{body}\n\n<!-- devin-action:tracker={tracker_id} -->"


def build_failure_comment(message: str) -> str:
    return f":x: Failed to start Devin session: {message}"
