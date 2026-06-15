from __future__ import annotations

from datetime import UTC, datetime

from app.integrations.whoop_client import WhoopClient


class FakeWhoopClient(WhoopClient):
    def __init__(self):
        self.calls = []

    def _request(self, path, params=None):
        self.calls.append((path, params))
        if len(self.calls) == 1:
            return {"records": [{"id": "one"}], "next_token": "next-page"}
        return {"records": [{"id": "two"}]}


def test_pagination_uses_next_token() -> None:
    client = FakeWhoopClient()
    records = client.get_sleep_collection(
        datetime(2026, 6, 1, tzinfo=UTC),
        datetime(2026, 6, 15, tzinfo=UTC),
    )

    assert records == [{"id": "one"}, {"id": "two"}]
    assert client.calls[1][1]["nextToken"] == "next-page"
