from __future__ import annotations

from src import resolver
from src.devin_client import DevinClient, SessionSummary


class _FakeClient:
    """Stand-in for DevinClient that records calls and replays canned data."""

    def __init__(
        self,
        *,
        api_version: str = "v3",
        listings: list[SessionSummary] | None = None,
        raise_on_list: Exception | None = None,
    ) -> None:
        self._api_version = api_version
        self._listings = listings or []
        self._raise = raise_on_list
        self.list_calls: list[list[str]] = []

    @property
    def api_version(self) -> str:  # kept for parity with DevinClient
        return self._api_version

    def list_sessions(self, *, tags: list[str] | None = None, limit: int = 50):
        if self._raise is not None:
            raise self._raise
        self.list_calls.append(list(tags or []))
        return list(self._listings)

    def is_reusable(self, summary: SessionSummary) -> bool:
        # Delegate to the real judgment so tests match production.
        return DevinClient.is_reusable(self, summary)  # type: ignore[arg-type]


THREAD = "knanao/example#42"
TAG = f"devin-action:thread:{THREAD}"


def _s(session_id: str, *, tags: list[str] | None = None, status: str = "running",
       status_detail: str | None = None, updated_ts: int = 0) -> SessionSummary:
    return SessionSummary(
        session_id=session_id,
        tags=tags if tags is not None else [TAG],
        status=status,
        status_detail=status_detail,
        updated_ts=updated_ts,
    )


class TestThreadTag:
    def test_prefixed(self):
        assert resolver.thread_tag(THREAD) == TAG


class TestFindReusableSession:
    def test_returns_none_when_no_results(self):
        client = _FakeClient(listings=[])
        assert resolver.find_reusable_session(client, THREAD) is None
        assert client.list_calls == [[TAG]]

    def test_returns_most_recent_matching_reusable(self):
        client = _FakeClient(
            listings=[
                _s("devin-old", status="running", updated_ts=100),
                _s("devin-new", status="running", updated_ts=200),
                _s("devin-mid", status="running", updated_ts=150),
            ]
        )
        assert resolver.find_reusable_session(client, THREAD) == "devin-new"

    def test_skips_terminal_sessions(self):
        client = _FakeClient(
            listings=[
                _s("devin-dead", status="exit", updated_ts=999),
                _s("devin-live", status="running", updated_ts=100),
            ]
        )
        assert resolver.find_reusable_session(client, THREAD) == "devin-live"

    def test_ignores_wrong_tag_even_if_api_returns_it(self):
        client = _FakeClient(
            listings=[
                _s("devin-other", tags=["devin-action:thread:knanao/example#99"],
                   status="running", updated_ts=999),
                _s("devin-mine", status="running", updated_ts=1),
            ]
        )
        assert resolver.find_reusable_session(client, THREAD) == "devin-mine"

    def test_returns_none_when_list_raises(self):
        client = _FakeClient(raise_on_list=RuntimeError("boom"))
        assert resolver.find_reusable_session(client, THREAD) is None
