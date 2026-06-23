from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx
from sqlmodel import Session

from app.integrations.whoop_oauth import refresh_access_token
from app.storage.repositories import get_oauth_token
from app.utils.time import isoformat_utc


class WhoopAPIError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        path: str | None = None,
        response_json: dict[str, Any] | None = None,
        response_text: str = "",
    ):
        super().__init__(message)
        self.status_code = status_code
        self.path = path
        self.response_json = response_json or {}
        self.response_text = response_text


class WhoopClient:
    base_url = "https://api.prod.whoop.com/developer"

    def __init__(self, session: Session, http_client: httpx.Client | None = None):
        self.session = session
        self.http_client = http_client or httpx.Client(timeout=30)

    def _request(self, path: str, params: dict[str, Any] | None = None) -> dict:
        token = refresh_access_token(self.session)
        response = self.http_client.get(
            f"{self.base_url}{path}",
            params=params,
            headers={"Authorization": f"Bearer {token.access_token}"},
        )
        if response.status_code == 401:
            token = refresh_access_token(self.session, force=True)
            response = self.http_client.get(
                f"{self.base_url}{path}",
                params=params,
                headers={"Authorization": f"Bearer {token.access_token}"},
            )

        if response.status_code >= 400:
            response_json = _safe_json(response)
            raise WhoopAPIError(
                f"WHOOP API request failed: {response.status_code}",
                status_code=response.status_code,
                path=path,
                response_json=response_json,
                response_text=response.text[:200],
            )
        return response.json()

    def _paginate(
        self,
        path: str,
        *,
        start: datetime,
        end: datetime,
        limit: int = 25,
    ) -> list[dict]:
        records: list[dict] = []
        next_token: str | None = None
        while True:
            params: dict[str, Any] = {
                "start": isoformat_utc(start),
                "end": isoformat_utc(end),
                "limit": min(limit, 25),
            }
            if next_token:
                params["nextToken"] = next_token

            payload = self._request(path, params=params)
            records.extend(payload.get("records", []))
            next_token = payload.get("next_token")
            if not next_token:
                return records

    def get_sleep_collection(self, start: datetime, end: datetime) -> list[dict]:
        return self._paginate("/v2/activity/sleep", start=start, end=end)

    def get_recovery_collection(self, start: datetime, end: datetime) -> list[dict]:
        return self._paginate("/v2/recovery", start=start, end=end)

    def get_cycle_collection(self, start: datetime, end: datetime) -> list[dict]:
        return self._paginate("/v2/cycle", start=start, end=end)

    def get_sleep_stream(self, sleep_id: str, stream_type: str = "hr") -> dict:
        return self._request(
            f"/v2/activity/sleep/{sleep_id}/stream",
            params={"type": stream_type},
        )

    def is_authenticated(self) -> bool:
        return get_oauth_token(self.session) is not None


def _safe_json(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}
