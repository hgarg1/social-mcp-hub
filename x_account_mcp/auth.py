"""OAuth 2.0 PKCE login and refresh. Tokens never become tool arguments."""
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import tempfile
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from .config import Settings
from .errors import XError

TOKEN_URL = "https://api.x.com/2/oauth2/token"
SCOPES = "tweet.read tweet.write users.read offline.access"


def read_tokens(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or not isinstance(value.get("access_token"), str):
            raise ValueError("Invalid token document")
        if not value["access_token"]:
            raise ValueError("Empty token")
        return value
    except (OSError, ValueError) as exc:
        raise XError("No valid saved login. Run x-account-login or set X_USER_ACCESS_TOKEN.") from exc


def save_tokens(path: Path, tokens: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(tokens, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def token_request_options(settings: Settings, data: dict) -> dict:
    options = {"data": {**data, "client_id": settings.client_id}}
    if settings.client_secret:
        options["auth"] = httpx.BasicAuth(settings.client_id, settings.client_secret)
    return options


def parse_token_response(response: httpx.Response, previous: dict | None = None) -> dict:
    # Do not include token endpoint bodies or credentials in exceptions/logs.
    if response.status_code != 200:
        raise XError(f"X OAuth returned HTTP {response.status_code}. Check app credentials or sign in again.")
    try:
        result = response.json()
        if not isinstance(result, dict) or not isinstance(result.get("access_token"), str):
            raise ValueError("Missing token")
        if not result["access_token"]:
            raise ValueError("Empty token")
        if "expires_in" in result:
            result["expires_at"] = time.time() + float(result["expires_in"])
        elif previous is not None:
            raise ValueError("Refresh response omitted token lifetime")
        if previous and "refresh_token" not in result and previous.get("refresh_token"):
            result["refresh_token"] = previous["refresh_token"]
        return result
    except (ValueError, TypeError) as exc:
        raise XError("X returned an invalid OAuth response. Sign in again.") from exc


class TokenProvider:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.transport = transport
        self._lock = asyncio.Lock()

    async def access_token(self) -> str:
        if self.settings.access_token:
            return self.settings.access_token
        async with self._lock:
            tokens = read_tokens(self.settings.token_file)
            try:
                expires_at = float(tokens.get("expires_at", float("inf")))
            except (TypeError, ValueError) as exc:
                raise XError("Saved token expiration is invalid. Sign in again.") from exc
            if expires_at > time.time() + 60:
                return tokens["access_token"]
            if not self.settings.client_id or not tokens.get("refresh_token"):
                raise XError("X login expired. Run x-account-login to sign in again.")
            try:
                async with httpx.AsyncClient(timeout=30, transport=self.transport) as client:
                    response = await client.post(TOKEN_URL, **token_request_options(self.settings, {
                        "grant_type": "refresh_token", "refresh_token": tokens["refresh_token"],
                    }))
            except httpx.RequestError as exc:
                raise XError("X OAuth refresh failed over the network. Sign in again if it persists.") from exc
            refreshed = parse_token_response(response, tokens)
            try:
                save_tokens(self.settings.token_file, refreshed)
            except OSError as exc:
                raise XError("Refreshed credentials could not be saved. Check token-file permissions.") from exc
            return refreshed["access_token"]


def authorization_request(settings: Settings) -> tuple[str, str, str]:
    verifier = secrets.token_urlsafe(64)
    state = secrets.token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    url = "https://x.com/i/oauth2/authorize?" + urlencode({
        "response_type": "code", "client_id": settings.client_id,
        "redirect_uri": settings.redirect_uri, "scope": SCOPES, "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256",
    })
    return url, state, verifier


def callback_code(callback: str, redirect_uri: str, state: str) -> str:
    actual, expected = urlsplit(callback), urlsplit(redirect_uri)
    if (actual.scheme, actual.netloc, actual.path) != (expected.scheme, expected.netloc, expected.path):
        raise XError("Callback URL does not match X_REDIRECT_URI.")
    query = parse_qs(actual.query)
    supplied_state = query.get("state", [""])
    if len(supplied_state) != 1 or not secrets.compare_digest(supplied_state[0], state):
        raise XError("OAuth state did not match. Start login again.")
    codes = query.get("code", [])
    if "error" in query or len(codes) != 1 or not codes[0]:
        raise XError("X authorization was denied or no authorization code was returned.")
    return codes[0]


def main() -> None:
    settings = Settings.from_env()
    try:
        if not settings.client_id:
            raise XError("Set X_CLIENT_ID in the project's .env file first.")
        url, state, verifier = authorization_request(settings)
        print("Open this URL in your browser and authorize your X account:\n" + url)
        print("After redirect, copy the FULL address-bar URL, even if localhost cannot connect.")
        callback = input("Callback URL: ").strip()
        code = callback_code(callback, settings.redirect_uri, state)
        with httpx.Client(timeout=30) as client:
            response = client.post(TOKEN_URL, **token_request_options(settings, {
                "grant_type": "authorization_code", "code": code,
                "redirect_uri": settings.redirect_uri, "code_verifier": verifier,
            }))
        save_tokens(settings.token_file, parse_token_response(response))
        print("Login saved. Restart the MCP connection, then call get_account to verify identity.")
        if settings.access_token:
            print("Remove X_USER_ACCESS_TOKEN if you want to use the saved, refreshable login.")
    except (XError, httpx.RequestError, OSError, EOFError, KeyboardInterrupt) as exc:
        message = str(exc) if isinstance(exc, XError) else "Login interrupted or failed. Check connectivity and file permissions."
        raise SystemExit(message) from None


if __name__ == "__main__":
    main()
