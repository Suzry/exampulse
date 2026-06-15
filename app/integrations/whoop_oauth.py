from __future__ import annotations

import secrets
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlencode, urlparse, parse_qs

import httpx
from rich.console import Console
from sqlmodel import Session

from app.core.config import Settings, get_settings
from app.storage.repositories import get_oauth_token, upsert_oauth_token
from app.utils.time import utc_now

AUTH_URL = "https://api.prod.whoop.com/oauth/oauth2/auth"
TOKEN_URL = "https://api.prod.whoop.com/oauth/oauth2/token"


class OAuthError(RuntimeError):
    pass


def build_authorization_url(settings: Settings, state: str) -> str:
    return f"{AUTH_URL}?{urlencode({
        'response_type': 'code',
        'client_id': settings.whoop_client_id,
        'redirect_uri': settings.whoop_redirect_uri,
        'scope': settings.whoop_scopes,
        'state': state,
    })}"


def _exchange_token(data: dict[str, str]) -> dict:
    response = httpx.post(TOKEN_URL, data=data, timeout=30)
    response.raise_for_status()
    return response.json()


def exchange_authorization_code(code: str, settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    return _exchange_token(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.whoop_redirect_uri,
            "client_id": settings.whoop_client_id,
            "client_secret": settings.whoop_client_secret,
        }
    )


def refresh_access_token(
    session: Session, settings: Settings | None = None, force: bool = False
):
    settings = settings or get_settings()
    token = get_oauth_token(session)
    if token is None:
        raise OAuthError("No WHOOP token found. Run `exampulse auth` first.")
    if not force and token.expires_at > utc_now():
        return token
    if not token.refresh_token:
        raise OAuthError("WHOOP refresh token missing. Re-run `exampulse auth`.")

    payload = _exchange_token(
        {
            "grant_type": "refresh_token",
            "refresh_token": token.refresh_token,
            "client_id": settings.whoop_client_id,
            "client_secret": settings.whoop_client_secret,
            "scope": settings.whoop_scopes,
        }
    )
    return upsert_oauth_token(
        session,
        access_token=payload["access_token"],
        refresh_token=payload.get("refresh_token") or token.refresh_token,
        expires_in=int(payload.get("expires_in", 3600)),
        scope=payload.get("scope"),
        token_type=payload.get("token_type"),
    )


def run_local_oauth_flow(
    session: Session,
    settings: Settings | None = None,
    console: Console | None = None,
) -> None:
    settings = settings or get_settings()
    console = console or Console()
    if not settings.whoop_client_id or not settings.whoop_client_secret:
        raise OAuthError("Set WHOOP_CLIENT_ID and WHOOP_CLIENT_SECRET in `.env` first.")

    state = secrets.token_urlsafe(32)
    parsed_redirect = urlparse(settings.whoop_redirect_uri)
    result: dict[str, str] = {}

    class CallbackHandler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A002
            return

        def do_GET(self):  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path != settings.callback_path:
                self.send_response(404)
                self.end_headers()
                return

            query = parse_qs(parsed.query)
            if query.get("state", [""])[0] != state:
                result["error"] = "Invalid OAuth state returned by WHOOP."
            elif "error" in query:
                result["error"] = query["error"][0]
            else:
                result["code"] = query.get("code", [""])[0]

            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                b"<html><body><h1>Exampulse connected.</h1>"
                b"<p>You can close this tab and return to the terminal.</p>"
                b"</body></html>"
            )

    server = HTTPServer((parsed_redirect.hostname or "localhost", settings.callback_port), CallbackHandler)
    auth_url = build_authorization_url(settings, state)
    console.print("Opening WHOOP authorization page...")
    console.print(f"Callback: {settings.whoop_redirect_uri}")
    webbrowser.open(auth_url)
    server.handle_request()
    server.server_close()

    if "error" in result:
        raise OAuthError(result["error"])
    if not result.get("code"):
        raise OAuthError("Authorization code was not returned by WHOOP.")

    payload = exchange_authorization_code(result["code"], settings)
    upsert_oauth_token(
        session,
        access_token=payload["access_token"],
        refresh_token=payload.get("refresh_token"),
        expires_in=int(payload.get("expires_in", 3600)),
        scope=payload.get("scope"),
        token_type=payload.get("token_type"),
    )
