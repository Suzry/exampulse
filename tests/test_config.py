from __future__ import annotations

from pathlib import Path

from app.core.config import get_settings


def test_settings_trim_env_values_and_anchor_relative_sqlite_db(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    for name in (
        "WHOOP_CLIENT_ID",
        "WHOOP_CLIENT_SECRET",
        "WHOOP_REDIRECT_URI",
        "WHOOP_SCOPES",
        "EXAMPULSE_DB",
        "SYNC_INTERVAL_MINUTES",
    ):
        monkeypatch.delenv(name, raising=False)

    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "WHOOP_CLIENT_ID=client-id ",
                "WHOOP_CLIENT_SECRET=client-secret ",
                "WHOOP_REDIRECT_URI=https://example.ngrok-free.dev/callback ",
                "WHOOP_SCOPES=read:sleep offline ",
                "EXAMPULSE_DB=sqlite:///exampulse.db",
                "SYNC_INTERVAL_MINUTES=15 ",
            ]
        ),
        encoding="utf-8",
    )

    get_settings.cache_clear()
    settings = get_settings()
    get_settings.cache_clear()

    assert settings.whoop_client_id == "client-id"
    assert settings.whoop_client_secret == "client-secret"
    assert settings.whoop_redirect_uri == "https://example.ngrok-free.dev/callback"
    assert settings.whoop_scopes == "read:sleep offline"
    assert settings.database_url == f"sqlite:///{(tmp_path / 'exampulse.db').as_posix()}"
    assert settings.sync_interval_minutes == 15
