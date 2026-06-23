from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from dotenv import find_dotenv, load_dotenv


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
    env_dir = _load_env()
    return Settings(
        whoop_client_id=_env("WHOOP_CLIENT_ID", ""),
        whoop_client_secret=_env("WHOOP_CLIENT_SECRET", ""),
        whoop_redirect_uri=_env(
            "WHOOP_REDIRECT_URI", "http://localhost:8711/callback"
        ),
        whoop_scopes=_env(
            "WHOOP_SCOPES",
            "read:sleep read:recovery read:cycles read:workout read:profile offline",
        ),
        database_url=_resolve_database_url(
            _env("EXAMPULSE_DB", "sqlite:///exampulse.db"), env_dir
        ),
        sync_interval_minutes=int(_env("SYNC_INTERVAL_MINUTES", "30")),
    )


def _load_env() -> Path:
    dotenv_path = find_dotenv(usecwd=True)
    if not dotenv_path:
        repo_dotenv = Path(__file__).resolve().parents[2] / ".env"
        if repo_dotenv.exists():
            dotenv_path = str(repo_dotenv)

    if dotenv_path:
        load_dotenv(dotenv_path)
        return Path(dotenv_path).resolve().parent

    load_dotenv()
    return Path.cwd()


def _env(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


def _resolve_database_url(database_url: str, env_dir: Path) -> str:
    sqlite_prefix = "sqlite:///"
    if not database_url.startswith(sqlite_prefix):
        return database_url

    raw_path = database_url.removeprefix(sqlite_prefix)
    if raw_path == ":memory:" or Path(raw_path).is_absolute():
        return database_url

    db_path = (env_dir / raw_path).resolve()
    return f"{sqlite_prefix}{db_path.as_posix()}"
