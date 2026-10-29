"""Encrypted, durable OAuth 2 tokens with cross-process refresh serialization."""
import json
import time

from cryptography.fernet import Fernet, InvalidToken
import httpx
import psycopg

from .auth import parse_token_response, token_request_options
from .config import Settings
from .errors import XError


def cipher(settings: Settings) -> Fernet:
    try:
        return Fernet(settings.token_encryption_key.encode())
    except (ValueError, TypeError) as exc:
        raise XError("Set a valid X_TOKEN_ENCRYPTION_KEY for the durable token store.") from exc


def decrypt_tokens(settings: Settings, encrypted: str) -> dict:
    try:
        tokens = json.loads(cipher(settings).decrypt(encrypted.encode()))
        if not isinstance(tokens, dict) or not tokens.get("access_token"):
            raise ValueError("Missing access token")
        return tokens
    except (InvalidToken, ValueError, TypeError) as exc:
        raise XError("Stored X credentials cannot be decrypted. Check the token encryption key.") from exc


def database_access_token(settings: Settings, transport=None) -> str:
    try:
        with psycopg.connect(settings.database_url, connect_timeout=15) as conn:
            conn.execute("SET LOCAL lock_timeout = '45s'")
            row = conn.execute("SELECT encrypted_tokens FROM x_mcp.oauth_tokens WHERE id = 'primary' FOR UPDATE").fetchone()
            if row is None:
                raise XError("Durable X login is missing. Seed it from an authorized OAuth 2 login.")
            tokens = decrypt_tokens(settings, row[0])
            if float(tokens.get("expires_at", 0)) > time.time() + 60:
                return tokens["access_token"]
            if not tokens.get("refresh_token") or not settings.client_id:
                raise XError("Durable X login cannot refresh. Authorize the X account again.")
            # Keep the row locked until the rotated token has been committed.
            # Other workers re-read the new token instead of reusing the old refresh token.
            with httpx.Client(timeout=30, transport=transport) as client:
                response = client.post("https://api.x.com/2/oauth2/token", **token_request_options(settings, {
                    "grant_type": "refresh_token", "refresh_token": tokens["refresh_token"],
                }))
            refreshed = parse_token_response(response, tokens)
            encrypted = cipher(settings).encrypt(json.dumps(refreshed).encode()).decode()
            conn.execute("UPDATE x_mcp.oauth_tokens SET encrypted_tokens = %s, updated_at = now() WHERE id = 'primary'", (encrypted,))
            return refreshed["access_token"]
    except XError:
        raise
    except (psycopg.Error, httpx.RequestError, OSError, ValueError) as exc:
        # Never expose DSNs, SQL parameters or token endpoint response bodies.
        raise XError("Durable X token lookup/refresh failed. Check database connectivity and OAuth login; no automatic retry was attempted.") from exc


def seed_tokens(settings: Settings, tokens: dict, *, replace_existing: bool = False) -> None:
    encrypted = cipher(settings).encrypt(json.dumps(tokens).encode()).decode()
    try:
        with psycopg.connect(settings.database_url, connect_timeout=15) as conn:
            query = "INSERT INTO x_mcp.oauth_tokens (id, encrypted_tokens) VALUES ('primary', %s)"
            if replace_existing:
                query += " ON CONFLICT (id) DO UPDATE SET encrypted_tokens = EXCLUDED.encrypted_tokens, updated_at = now()"
            conn.execute(query, (encrypted,))
    except psycopg.Error as exc:
        raise XError("Token seed failed. Existing credentials were not overwritten.") from exc
