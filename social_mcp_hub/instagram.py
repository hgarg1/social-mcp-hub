"""Instagram Login API: professional account reads and container publishing.

No import-time network calls, browser cookies, or implicit publication.
"""
import asyncio
from dataclasses import dataclass, field
import ipaddress
import json
import os
from pathlib import Path
import re
import time
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken
import httpx
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field
import psycopg

from x_account_mcp.config import ROOT, Settings


class InstagramError(ToolError):
    """Credential-free error safe to return through MCP."""


@dataclass(frozen=True)
class InstagramSettings:
    client_id: str = ""
    client_secret: str = field(default="", repr=False)
    redirect_uri: str = "http://127.0.0.1:8766/callback"
    api_version: str = "v25.0"
    expected_user_id: str = ""
    token_file: Path = ROOT / ".instagram.tokens.json"
    database_url: str = field(default="", repr=False)
    encryption_key: str = field(default="", repr=False)

    @classmethod
    def from_env(cls):
        shared = Settings.from_env()
        path = Path(os.environ.get("INSTAGRAM_TOKEN_FILE", ".instagram.tokens.json")).expanduser()
        return cls(client_id=os.environ.get("INSTAGRAM_CLIENT_ID", "").strip(),
                   client_secret=os.environ.get("INSTAGRAM_CLIENT_SECRET", "").strip(),
                   redirect_uri=os.environ.get("INSTAGRAM_REDIRECT_URI", cls.redirect_uri),
                   api_version=os.environ.get("INSTAGRAM_API_VERSION", cls.api_version),
                   expected_user_id=os.environ.get("INSTAGRAM_EXPECTED_USER_ID", "").strip(),
                   token_file=path if path.is_absolute() else ROOT / path,
                   database_url=os.environ.get("INSTAGRAM_TOKEN_DATABASE_URL", "").strip() or shared.database_url,
                   encryption_key=os.environ.get("INSTAGRAM_TOKEN_ENCRYPTION_KEY", "").strip() or shared.token_encryption_key)


def api_result(response: httpx.Response) -> dict:
    try:
        data = response.json()
    except ValueError:
        raise InstagramError("Instagram returned an unreadable response. Inspect the account before retrying a write.") from None
    if not isinstance(data, dict):
        raise InstagramError("Instagram returned an unexpected response.")
    if response.is_error or "error" in data:
        error = data.get("error", {})
        code = error.get("code") if isinstance(error, dict) else None
        # Do not return provider messages, URLs, headers or token-bearing bodies.
        suffix = f" (code {code})" if isinstance(code, int) else ""
        if response.status_code == 401 or code == 190:
            raise InstagramError("Instagram login expired or was revoked; run instagram-login again." + suffix)
        raise InstagramError(f"Instagram API rejected the request (HTTP {response.status_code}){suffix}. Check permissions, media eligibility and API limits. No automatic retry was attempted.")
    return data


def public_media_url(value: str) -> str:
    url = urlsplit(value)
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.fragment:
        raise InstagramError("Media must have a public HTTPS URL without credentials or a fragment.")
    host = url.hostname.lower().rstrip(".")
    if host == "localhost" or host.endswith((".localhost", ".local")) or "." not in host:
        raise InstagramError("Local media URLs are not supported.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise InstagramError("Private media addresses are not supported.")
    # Only Meta fetches this URL; the MCP server does not download arbitrary URLs.
    return value


class InstagramTokens:
    def __init__(self, settings: InstagramSettings, transport=None):
        self.settings, self.transport = settings, transport
        self.lock = asyncio.Lock()

    def _cipher(self):
        try:
            return Fernet(self.settings.encryption_key.encode())
        except (TypeError, ValueError):
            raise InstagramError("Configure the token encryption key before connecting Instagram.") from None

    def _decode(self, encrypted):
        try:
            tokens = json.loads(self._cipher().decrypt(encrypted.encode()))
            if not isinstance(tokens, dict) or not tokens.get("access_token") or not tokens.get("user_id"):
                raise ValueError()
            return tokens
        except (InvalidToken, ValueError, TypeError):
            raise InstagramError("Stored Instagram login cannot be decrypted or is invalid. Check the encryption key or reconnect.") from None

    def _encode(self, tokens):
        return self._cipher().encrypt(json.dumps(tokens).encode()).decode()

    def save(self, tokens):
        encrypted = self._encode(tokens)
        try:
            if self.settings.database_url:
                with psycopg.connect(self.settings.database_url, connect_timeout=15) as conn:
                    conn.execute("INSERT INTO x_mcp.instagram_tokens (id, encrypted_tokens) VALUES ('primary', %s) ON CONFLICT (id) DO UPDATE SET encrypted_tokens = EXCLUDED.encrypted_tokens, updated_at = now()", (encrypted,))
            else:
                path = self.settings.token_file
                temp = path.with_suffix(path.suffix + ".tmp")
                temp.write_text(encrypted, encoding="utf-8")
                temp.replace(path)
        except (psycopg.Error, OSError):
            raise InstagramError("Could not save Instagram login. Check token storage and apply migration 002.") from None

    def _refresh(self, tokens):
        now = time.time()
        expires = float(tokens.get("expires_at", 0))
        if expires <= now + 60:
            raise InstagramError("Instagram login expired. Run instagram-login again after converting to Creator or Business.")
        if expires - now > 7 * 86400:
            return tokens
        if now - float(tokens.get("refreshed_at", now)) < 86400:
            return tokens
        with httpx.Client(timeout=30, transport=self.transport) as client:
            result = api_result(client.get("https://graph.instagram.com/refresh_access_token", params={
                "grant_type": "ig_refresh_token", "access_token": tokens["access_token"]}))
        if not result.get("access_token") or float(result.get("expires_in", 0)) <= 0:
            raise InstagramError("Instagram did not return a renewable token; reconnect Instagram.")
        return {**tokens, "access_token": result["access_token"], "expires_at": now + float(result["expires_in"]), "refreshed_at": now}

    def _load(self):
        missing = "Instagram is not connected. Personal accounts are unsupported; convert to Creator or Business, configure the Meta app, then run instagram-login."
        try:
            if self.settings.database_url:
                with psycopg.connect(self.settings.database_url, connect_timeout=15) as conn:
                    conn.execute("SET LOCAL lock_timeout = '45s'")
                    row = conn.execute("SELECT encrypted_tokens FROM x_mcp.instagram_tokens WHERE id = 'primary' FOR UPDATE").fetchone()
                    if row is None:
                        raise InstagramError(missing)
                    old = self._decode(row[0])
                    new = self._refresh(old)
                    if new != old:
                        conn.execute("UPDATE x_mcp.instagram_tokens SET encrypted_tokens = %s, updated_at = now() WHERE id = 'primary'", (self._encode(new),))
                    return new
            if not self.settings.token_file.exists():
                raise InstagramError(missing)
            old = self._decode(self.settings.token_file.read_text(encoding="utf-8"))
            new = self._refresh(old)
            if old != new:
                self.save(new)
            return new
        except InstagramError:
            raise
        except (psycopg.Error, httpx.RequestError, OSError, ValueError, TypeError):
            raise InstagramError("Instagram token lookup/refresh failed. Check storage, migration 002 and login. No automatic retry was attempted.") from None

    async def load(self):
        async with self.lock:
            return await asyncio.to_thread(self._load)


class InstagramClient:
    def __init__(self, settings=None, transport=None):
        self.settings = settings or InstagramSettings.from_env()
        if not re.fullmatch(r"v[0-9]+\.0", self.settings.api_version):
            raise ValueError("INSTAGRAM_API_VERSION must look like v25.0")
        self.transport = transport
        self.tokens = InstagramTokens(self.settings, transport)
        self.base_url = f"https://graph.instagram.com/{self.settings.api_version}"

    async def request(self, method, path, tokens, *, params=None, data=None):
        try:
            async with httpx.AsyncClient(timeout=30, transport=self.transport) as client:
                response = await client.request(method, self.base_url + path,
                    headers={"Authorization": "Bearer " + tokens["access_token"]}, params=params, data=data)
            return api_result(response)
        except httpx.RequestError:
            raise InstagramError("Instagram network request failed; a write may have succeeded. Inspect the account before retrying. No automatic retry was attempted.") from None

    async def identity(self):
        tokens = await self.tokens.load()
        account = await self.request("GET", "/me", tokens, params={"fields": "id,user_id,username,account_type"})
        identity = str(account.get("user_id") or account.get("id") or "")
        if not identity or identity != str(tokens["user_id"]):
            raise InstagramError("Instagram identity does not match the saved login; reconnect.")
        if self.settings.expected_user_id and identity != self.settings.expected_user_id:
            raise InstagramError("Instagram account does not match INSTAGRAM_EXPECTED_USER_ID.")
        if account.get("account_type") not in ("BUSINESS", "MEDIA_CREATOR"):
            raise InstagramError("Instagram publishing requires a Creator or Business account.")
        return tokens, account, identity

    async def account(self):
        _, account, _ = await self.identity()
        return account

    async def list_media(self, limit=20, after=None):
        tokens, _, identity = await self.identity()
        params = {"fields": "id,caption,media_type,media_url,permalink,timestamp", "limit": limit}
        if after:
            params["after"] = after
        result = await self.request("GET", f"/{identity}/media", tokens, params=params)
        # Provider next/previous URLs may embed access tokens. Return opaque cursors only.
        return {"data": result.get("data", []), "cursors": result.get("paging", {}).get("cursors", {})}

    async def create_container(self, kind, media_url, caption="", share_to_feed=True):
        public_media_url(media_url)
        tokens, _, identity = await self.identity()
        payload = {"caption": caption}
        if kind == "IMAGE":
            payload["image_url"] = media_url
        else:
            payload.update(media_type="REELS", video_url=media_url, share_to_feed=str(share_to_feed).lower())
        result = await self.request("POST", f"/{identity}/media", tokens, data=payload)
        return {**result, "published": False, "next_step": "Check instagram_get_container_status, then explicitly call instagram_publish_container once FINISHED."}

    async def container_status(self, container_id):
        tokens, _, _ = await self.identity()
        return await self.request("GET", f"/{container_id}", tokens, params={"fields": "id,status_code"})

    async def publish(self, container_id):
        tokens, _, identity = await self.identity()
        status = await self.request("GET", f"/{container_id}", tokens, params={"fields": "id,status_code"})
        if status.get("status_code") != "FINISHED":
            raise InstagramError("Container is not FINISHED; check its status before publishing. Do not recreate or republish blindly.")
        result = await self.request("POST", f"/{identity}/media_publish", tokens, data={"creation_id": container_id})
        return {**result, "published": True}


def register_instagram_tools(server, api=None):
    api = api or InstagramClient()
    read = ToolAnnotations(read_only_hint=True, open_world_hint=True)
    write = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=True)
    numeric_id = Annotated[str, Field(pattern=r"^[0-9]{1,30}$")]

    @server.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))
    async def instagram_capabilities() -> dict[str, Any]:
        """Describe Instagram setup and supported tools without contacting Meta or requiring credentials."""
        return {"platform": "instagram", "account_requirement": "Creator or Business; Personal unsupported",
                "login": "Instagram Login", "permissions": ["instagram_business_basic", "instagram_business_content_publish"],
                "supported": ["account", "list_media", "photo_container", "reel_container", "container_status", "publish_container"],
                "not_implemented": ["carousels", "stories", "edit/delete media", "profile edits", "comments", "DMs", "scheduling"],
                "setup": "See INSTAGRAM.md; run instagram-login after conversion and Meta app setup. Media requires a public HTTPS URL."}

    @server.tool(annotations=read)
    async def instagram_get_account() -> dict[str, Any]:
        """Read the connected Instagram professional account. Personal accounts must convert before connecting."""
        return await api.account()

    @server.tool(annotations=read)
    async def instagram_list_media(limit: Annotated[int, Field(ge=1, le=100)] = 20,
                                   after: Annotated[str, Field(min_length=1, max_length=2048)] | None = None) -> dict[str, Any]:
        """List your Instagram media; pass cursors.after for the next page. Returns cursors rather than token-bearing paging URLs."""
        return await api.list_media(limit, after)

    @server.tool(annotations=write)
    async def instagram_create_photo_container(image_url: Annotated[str, Field(min_length=1, max_length=8192)],
                                              caption: Annotated[str, Field(max_length=2200)] = "") -> dict[str, Any]:
        """Prepare an unpublished Instagram photo from a public HTTPS JPEG URL. Meta fetches the URL. Check status and explicitly publish afterward."""
        return await api.create_container("IMAGE", image_url, caption)

    @server.tool(annotations=write)
    async def instagram_create_reel_container(video_url: Annotated[str, Field(min_length=1, max_length=8192)],
                                             caption: Annotated[str, Field(max_length=2200)] = "",
                                             share_to_feed: bool = True) -> dict[str, Any]:
        """Prepare an unpublished Reel from a public HTTPS video URL meeting Meta requirements. Processing is asynchronous; check status before publication."""
        return await api.create_container("REELS", video_url, caption, share_to_feed)

    @server.tool(annotations=read)
    async def instagram_get_container_status(container_id: numeric_id) -> dict[str, Any]:
        """Check processing status. FINISHED means ready; ERROR/EXPIRED require investigation. This tool never publishes."""
        return await api.container_status(container_id)

    @server.tool(annotations=write)
    async def instagram_publish_container(container_id: numeric_id) -> dict[str, Any]:
        """Make a FINISHED Instagram photo/Reel container public. Requires explicit publishing intent. Do not retry an ambiguous write blindly."""
        return await api.publish(container_id)
