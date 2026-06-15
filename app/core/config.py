from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urlparse

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    whoop_client_id: str
    whoop_client_secret: str
    whoop_redirect_uri: str
    whoop_scopes: str
    database_url: str
    sync_interval_minutes: int

    @property
    def callback_port(self) -> int:
        parsed = urlparse(self.whoop_redirect_uri)
        if parsed.port is None:
            return 443 if parsed.scheme == "https" else 80
        return parsed.port

    @property
    def callback_path(self) -> str:
        parsed = urlparse(self.whoop_redirect_uri)
        return parsed.path or "/callback"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    load_dotenv()
    return Settings(
        whoop_client_id=os.getenv("WHOOP_CLIENT_ID", ""),
        whoop_client_secret=os.getenv("WHOOP_CLIENT_SECRET", ""),
        whoop_redirect_uri=os.getenv(
            "WHOOP_REDIRECT_URI", "http://localhost:8711/callback"
        ),
        whoop_scopes=os.getenv(
            "WHOOP_SCOPES",
            "read:sleep read:recovery read:cycles read:workout read:profile offline",
        ),
        database_url=os.getenv("EXAMPULSE_DB", "sqlite:///exampulse.db"),
        sync_interval_minutes=int(os.getenv("SYNC_INTERVAL_MINUTES", "30")),
    )
