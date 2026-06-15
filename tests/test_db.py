from __future__ import annotations

from sqlmodel import Session, SQLModel, create_engine

from app.core.models import OAuthToken
from app.storage.repositories import get_oauth_token, upsert_oauth_token


def test_db_initializes_and_upserts_token() -> None:
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        token = upsert_oauth_token(
            session,
            access_token="access-1",
            refresh_token="refresh-1",
            expires_in=3600,
            scope="offline",
            token_type="bearer",
        )
        assert token.id is not None

        updated = upsert_oauth_token(
            session,
            access_token="access-2",
            refresh_token="refresh-2",
            expires_in=3600,
            scope="offline",
            token_type="bearer",
        )
        stored = get_oauth_token(session)

    assert isinstance(updated, OAuthToken)
    assert stored is not None
    assert stored.access_token == "access-2"
    assert stored.refresh_token == "refresh-2"
