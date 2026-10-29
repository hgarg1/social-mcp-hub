import asyncio
import json
from pathlib import Path
import time
from urllib.parse import parse_qs

from cryptography.fernet import Fernet
import httpx
from mcp import Client
import pytest

from social_mcp_hub.instagram import (InstagramClient, InstagramError, InstagramSettings,
                                      InstagramTokens, api_result, public_media_url)
from social_mcp_hub.instagram_auth import authorization_request, callback_code, exchange_code
from x_account_mcp.server import build_server


@pytest.fixture
def anyio_backend():
    return "asyncio"


def connected(tmp_path, handler, **kwargs):
    settings = InstagramSettings(token_file=tmp_path / "tokens", encryption_key=Fernet.generate_key().decode(), **kwargs)
    api = InstagramClient(settings, httpx.MockTransport(handler))
    api.tokens.save({"access_token": "ig-secret", "user_id": "42", "expires_at": time.time() + 60 * 86400,
                     "refreshed_at": time.time()})
    return api


def identity():
    return httpx.Response(200, json={"id": "42", "user_id": "42", "username": "creator", "account_type": "MEDIA_CREATOR"})


@pytest.mark.anyio
async def test_optional_instagram_does_not_break_hub(tmp_path):
    api = InstagramClient(InstagramSettings(token_file=tmp_path / "missing"))
    async with Client(build_server(instagram_api=api)) as client:
        capabilities = await client.call_tool("instagram_capabilities", {})
        assert not capabilities.is_error
        assert "Personal unsupported" in capabilities.structured_content["account_requirement"]
        account = await client.call_tool("instagram_get_account", {})
        assert account.is_error and "not connected" in account.content[0].text
        assert not (await client.call_tool("preview_article", {"title": "X", "body": "Still works"})).is_error


@pytest.mark.anyio
async def test_photo_is_unpublished_until_explicit_publish(tmp_path):
    calls = []
    def handler(request):
        assert request.headers["authorization"] == "Bearer ig-secret"
        calls.append(request)
        if request.url.path.endswith("/me"):
            return identity()
        if request.url.path.endswith("/42/media"):
            assert parse_qs(request.content.decode()) == {"image_url": ["https://cdn.example.com/photo.jpg"], "caption": ["Hello"]}
            return httpx.Response(200, json={"id": "123"})
        if request.url.path.endswith("/123"):
            return httpx.Response(200, json={"id": "123", "status_code": "FINISHED"})
        assert request.url.path.endswith("/42/media_publish")
        assert parse_qs(request.content.decode()) == {"creation_id": ["123"]}
        return httpx.Response(200, json={"id": "456"})
    api = connected(tmp_path, handler)
    created = await api.create_container("IMAGE", "https://cdn.example.com/photo.jpg", "Hello")
    assert created["published"] is False
    assert not any(r.url.path.endswith("media_publish") for r in calls)
    assert (await api.publish("123"))["published"] is True
    assert sum(r.url.path.endswith("media_publish") for r in calls) == 1


@pytest.mark.anyio
async def test_reel_payload_and_not_ready_guard(tmp_path):
    writes = []
    def handler(request):
        if request.url.path.endswith("/me"):
            return identity()
        if request.method == "POST":
            writes.append(request)
            assert parse_qs(request.content.decode()) == {"media_type": ["REELS"], "video_url": ["https://cdn.example.com/reel.mp4"], "share_to_feed": ["false"]}
            return httpx.Response(200, json={"id": "123"})
        return httpx.Response(200, json={"status_code": "IN_PROGRESS"})
    api = connected(tmp_path, handler)
    await api.create_container("REELS", "https://cdn.example.com/reel.mp4", share_to_feed=False)
    with pytest.raises(InstagramError, match="not FINISHED"):
        await api.publish("123")
    assert len(writes) == 1


@pytest.mark.anyio
async def test_account_pin_blocks_write(tmp_path):
    requests = []
    def handler(request):
        requests.append(request)
        return identity()
    api = connected(tmp_path, handler, expected_user_id="99")
    with pytest.raises(InstagramError, match="INSTAGRAM_EXPECTED_USER_ID"):
        await api.create_container("IMAGE", "https://example.com/x.jpg")
    assert len(requests) == 1 and requests[0].method == "GET"


@pytest.mark.anyio
async def test_paging_urls_do_not_leak_tokens(tmp_path):
    def handler(request):
        if request.url.path.endswith("/me"):
            return identity()
        return httpx.Response(200, json={"data": [{"id": "123"}], "paging": {
            "next": "https://graph.instagram.com/media?access_token=ig-secret", "cursors": {"after": "opaque"}}})
    result = await connected(tmp_path, handler).list_media()
    assert result == {"data": [{"id": "123"}], "cursors": {"after": "opaque"}}
    assert "ig-secret" not in json.dumps(result)


@pytest.mark.anyio
async def test_timed_out_publish_is_not_retried(tmp_path):
    writes = []
    def handler(request):
        if request.url.path.endswith("/me"):
            return identity()
        if request.method == "POST":
            writes.append(request)
            raise httpx.ReadTimeout("secret URL/token", request=request)
        return httpx.Response(200, json={"status_code": "FINISHED"})
    with pytest.raises(InstagramError, match="may have succeeded") as exc:
        await connected(tmp_path, handler).publish("123")
    assert "secret URL" not in str(exc.value)
    assert len(writes) == 1


@pytest.mark.parametrize("url", ["http://example.com/a.jpg", "https://127.0.0.1/a", "https://192.168.0.2/a", "https://localhost/a", "https://user:pass@example.com/a", "file:///a", "https://[::1]/a"])
def test_non_public_media_rejected(url):
    with pytest.raises(InstagramError):
        public_media_url(url)


def test_token_file_encrypted_and_expired_login_rejected(tmp_path):
    api = connected(tmp_path, lambda _: pytest.fail("No network"))
    assert "ig-secret" not in api.settings.token_file.read_text()
    tokens = api.tokens._load()
    tokens["expires_at"] = time.time() - 1
    api.tokens.save(tokens)
    with pytest.raises(InstagramError, match="expired"):
        api.tokens._load()


def test_long_lived_token_refresh_saved(tmp_path):
    refreshes = []
    def handler(request):
        refreshes.append(request)
        assert request.url.params["grant_type"] == "ig_refresh_token"
        return httpx.Response(200, json={"access_token": "new-secret", "expires_in": 60 * 86400})
    api = connected(tmp_path, handler)
    api.tokens.save({"access_token": "old", "user_id": "42", "expires_at": time.time() + 2 * 86400,
                     "refreshed_at": time.time() - 58 * 86400})
    assert api.tokens._load()["access_token"] == "new-secret"
    assert api.tokens._load()["access_token"] == "new-secret"
    assert len(refreshes) == 1
    assert "new-secret" not in api.settings.token_file.read_text()


def test_provider_errors_redacted():
    with pytest.raises(InstagramError) as exc:
        api_result(httpx.Response(400, json={"error": {"code": 190, "message": "secret-token"}}))
    assert "secret-token" not in str(exc.value)


def test_oauth_callback_checks_and_requested_scopes():
    settings = InstagramSettings(client_id="app", client_secret="secret")
    url, state = authorization_request(settings)
    assert "instagram_business_content_publish" in url
    callback = settings.redirect_uri + f"?code=good&state={state}"
    assert callback_code(callback, settings.redirect_uri, state) == "good"
    with pytest.raises(InstagramError, match="state"):
        callback_code(callback, settings.redirect_uri, "bad")
    with pytest.raises(InstagramError, match="URI"):
        callback_code(callback.replace("8766", "9999"), settings.redirect_uri, state)


def test_login_exchanges_long_lived_token_and_checks_account_pin():
    calls = []
    def handler(request):
        calls.append(request)
        if request.url.host == "api.instagram.com":
            return httpx.Response(200, json={"access_token": "short", "user_id": "42"})
        if request.url.path == "/access_token":
            return httpx.Response(200, json={"access_token": "long", "expires_in": 5184000})
        return identity()
    settings = InstagramSettings(client_id="app", client_secret="secret", expected_user_id="42")
    tokens, account = exchange_code(settings, "code", httpx.MockTransport(handler))
    assert tokens["user_id"] == "42" and tokens["access_token"] == "long"
    assert tokens["expires_at"] > time.time() + 59 * 86400
    assert len(calls) == 3
    with pytest.raises(InstagramError, match="EXPECTED_USER_ID"):
        exchange_code(InstagramSettings(client_id="app", client_secret="secret", expected_user_id="99"), "code", httpx.MockTransport(handler))
