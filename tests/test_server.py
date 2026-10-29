import base64
import hashlib
import json
from pathlib import Path
import sys
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import jsonschema
from mcp import Client
from mcp.client.stdio import StdioServerParameters
import pytest

from x_account_mcp.articles import article_payload
from x_account_mcp.auth import (TokenProvider, authorization_request, callback_code,
                                read_tokens, save_tokens)
from x_account_mcp.client import XClient
from x_account_mcp.config import Settings
from x_account_mcp.errors import XError
from x_account_mcp.server import build_server

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def anyio_backend():
    return "asyncio"


def make_api(handler, **settings):
    return XClient(Settings(access_token="secret-user-token", **settings), httpx.MockTransport(handler))


def ok(data):
    return httpx.Response(200, json={"data": data})


def contract(name, value):
    spec = json.loads((ROOT / "x-openapi.json").read_text(encoding="utf-8"))
    schema = {"$ref": "#/components/schemas/" + name, "components": spec["components"]}
    jsonschema.Draft202012Validator(schema).validate(value)


@pytest.mark.parametrize("body_format", ["text", "markdown"])
def test_native_article_matches_official_contract(body_format):
    payload = article_payload("An Article", "# Heading\n\nUnicode: 🌎\n\n**Bold** and a [link](https://example.com)", body_format, "123")
    contract("ArticleCreateDraftRequest", payload)
    assert "text" not in payload  # Not a normal post payload.
    assert payload["cover_media"]["media_id"] == "123"
    if body_format == "markdown":
        state = payload["content_state"]
        assert state["blocks"][0]["entity_ranges"][0]["key"] == 0
        assert state["entities"][0]["value"]["data"]["markdown"].startswith("# Heading")


@pytest.mark.anyio
async def test_post_update_uses_real_edit_api_and_new_id():
    requests = []
    def handler(request):
        requests.append(request)
        assert request.headers["authorization"] == "Bearer secret-user-token"
        if request.url.path == "/2/users/me":
            return ok({"id": "42", "username": "owner"})
        if request.method == "GET":
            return ok({"id": "123", "author_id": "42", "edit_controls": {"is_edit_eligible": True}})
        payload = json.loads(request.content)
        assert request.method == "POST" and request.url.path == "/2/tweets"
        assert payload == {"text": "Edited", "edit_options": {"previous_post_id": "123"}}
        contract("CreatePostsRequest", payload)
        return ok({"id": "124", "text": "Edited"})
    result = await make_api(handler).update_post("123", "Edited")
    assert result["data"]["id"] == "124"
    assert all(r.method != "DELETE" for r in requests)


@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["delete", "edit"])
async def test_ownership_mismatch_never_writes(operation):
    requests = []
    def handler(request):
        requests.append(request)
        return ok({"id": "42"}) if request.url.path == "/2/users/me" else ok({"id": "123", "author_id": "99"})
    api = make_api(handler)
    with pytest.raises(XError, match="does not belong"):
        if operation == "delete":
            await api.delete_post("123")
        else:
            await api.update_post("123", "Changed")
    assert all(r.method == "GET" for r in requests)


@pytest.mark.anyio
async def test_account_pin_prevents_posting():
    requests = []
    def handler(request):
        requests.append(request)
        return ok({"id": "99"})
    with pytest.raises(XError, match="X_EXPECTED_USER_ID"):
        await make_api(handler, expected_user_id="42").create_post("Hello")
    assert len(requests) == 1 and requests[0].method == "GET"


@pytest.mark.anyio
async def test_delete_own_post():
    requests = []
    def handler(request):
        requests.append(request)
        if request.url.path == "/2/users/me":
            return ok({"id": "42"})
        if request.method == "GET":
            return ok({"id": "123", "author_id": "42"})
        assert request.method == "DELETE" and request.url.path == "/2/tweets/123"
        return ok({"deleted": True})
    assert (await make_api(handler).delete_post("123"))["data"]["deleted"]
    assert len(requests) == 3


@pytest.mark.anyio
async def test_create_post_with_media_reply_and_quote():
    def handler(request):
        if request.method == "GET":
            return ok({"id": "42"})
        payload = json.loads(request.content)
        assert payload["reply"]["in_reply_to_tweet_id"] == "100"
        assert payload["quote_tweet_id"] == "101"
        assert payload["media"]["media_ids"] == ["102"]
        contract("CreatePostsRequest", payload)
        return ok({"id": "123"})
    assert (await make_api(handler).create_post("Hello", "100", "101", ["102"]))["data"]["id"] == "123"


@pytest.mark.anyio
async def test_pagination_token_preserved():
    def handler(request):
        if request.url.path == "/2/users/me":
            return ok({"id": "42"})
        assert request.url.path == "/2/users/42/tweets"
        assert request.url.params["max_results"] == "5"
        assert request.url.params["pagination_token"] == "next-token"
        assert "article" in request.url.params["tweet.fields"]
        return httpx.Response(200, json={"data": [{"id": "123"}], "meta": {"next_token": "later"}})
    result = await make_api(handler).list_posts(5, "next-token")
    assert result["meta"]["next_token"] == "later"


@pytest.mark.anyio
async def test_article_draft_and_publish_are_separate_calls():
    writes = []
    def handler(request):
        if request.method == "GET":
            return ok({"id": "42"})
        writes.append(request)
        if request.url.path == "/2/articles/draft":
            contract("ArticleCreateDraftRequest", json.loads(request.content))
            return ok({"id": "700", "title": "Title"})
        assert request.url.path == "/2/articles/700/publish"
        assert request.content == b""
        return ok({"post_id": "800"})
    api = make_api(handler)
    result = await api.create_article_draft(article_payload("Title", "Body"))
    assert result["data"]["id"] == "700" and len(writes) == 1
    published = await api.publish_article("700")
    assert published["data"]["post_id"] == "800"
    assert published["post_url"] == "https://x.com/i/web/status/800"


@pytest.mark.anyio
@pytest.mark.parametrize("status,hint", [(401, "OAuth user token"), (402, "credits"), (403, "permissions"), (429, "Rate limit")])
async def test_errors_are_actionable_and_redact_token(status, hint):
    api = make_api(lambda _: httpx.Response(status, json={"detail": "Rejected secret-user-token"},
                                            headers={"x-rate-limit-reset": "1900000000"}))
    with pytest.raises(XError) as error:
        await api.account()
    assert hint in str(error.value)
    assert "secret-user-token" not in str(error.value)
    if status == 429:
        assert "1900000000" in str(error.value)


@pytest.mark.anyio
async def test_timeout_does_not_retry_write():
    writes = []
    def handler(request):
        if request.method == "GET":
            return ok({"id": "42"})
        writes.append(request)
        raise httpx.ReadTimeout("Do not leak request headers", request=request)
    with pytest.raises(XError, match="outcome is unknown"):
        await make_api(handler).create_post("Hello")
    assert len(writes) == 1


@pytest.mark.anyio
async def test_ineligible_edit_does_not_write():
    def handler(request):
        assert request.method == "GET"
        if request.url.path == "/2/users/me":
            return ok({"id": "42"})
        return ok({"id": "123", "author_id": "42", "edit_controls": {"is_edit_eligible": False}})
    with pytest.raises(XError, match="not eligible"):
        await make_api(handler).update_post("123", "Changed")


def test_pkce_and_callback_validation():
    settings = Settings(client_id="client-id")
    url, state, verifier = authorization_request(settings)
    query = parse_qs(urlsplit(url).query)
    expected_challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert query["code_challenge"] == [expected_challenge]
    assert query["code_challenge_method"] == ["S256"]
    assert "offline.access" in query["scope"][0]
    valid = settings.redirect_uri + "?code=abc&state=" + state
    assert callback_code(valid, settings.redirect_uri, state) == "abc"
    with pytest.raises(XError, match="state"):
        callback_code(valid, settings.redirect_uri, "wrong")
    with pytest.raises(XError, match="does not match"):
        callback_code(valid.replace("127.0.0.1", "evil.example"), settings.redirect_uri, state)
    with pytest.raises(XError, match="denied"):
        callback_code(settings.redirect_uri + "?error=access_denied&state=" + state, settings.redirect_uri, state)


@pytest.mark.anyio
async def test_refresh_rotates_saved_tokens(tmp_path):
    path = tmp_path / "tokens.json"
    save_tokens(path, {"access_token": "old", "refresh_token": "old-refresh", "expires_at": 1})
    def handler(request):
        assert request.url == "https://api.x.com/2/oauth2/token"
        assert parse_qs(request.content.decode())["refresh_token"] == ["old-refresh"]
        return httpx.Response(200, json={"access_token": "new", "refresh_token": "new-refresh", "expires_in": 7200})
    provider = TokenProvider(Settings(client_id="id", token_file=path), httpx.MockTransport(handler))
    assert await provider.access_token() == "new"
    assert read_tokens(path)["refresh_token"] == "new-refresh"
    assert read_tokens(path)["expires_at"] > time.time() + 7000
    assert await provider.access_token() == "new"  # No second refresh.


@pytest.mark.anyio
async def test_missing_credentials_is_clear(tmp_path):
    provider = TokenProvider(Settings(token_file=tmp_path / "missing.json"))
    with pytest.raises(XError, match="x-account-login"):
        await provider.access_token()


@pytest.mark.anyio
async def test_mcp_schemas_validation_preview_and_errors(tmp_path):
    def no_network(_):
        pytest.fail("Invalid input and previews must not make API requests")
    api = XClient(Settings(token_file=tmp_path / "missing.json"), httpx.MockTransport(no_network))
    async with Client(build_server(api), raise_exceptions=True) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        assert set(tools) == {"get_account", "get_post", "list_my_posts", "create_post", "update_post",
                              "delete_post", "preview_article", "create_article_draft", "publish_article",
                              "update_profile", "update_profile_image", "update_profile_banner", "remove_profile_banner", "upload_image"}
        assert tools["delete_post"].annotations.destructive_hint is True
        assert tools["preview_article"].annotations.read_only_hint is True
        preview = await client.call_tool("preview_article", {"title": "Title", "body": "**Hello**"})
        assert not preview.is_error
        assert preview.structured_content["format"] == "x_article"
        invalid = await client.call_tool("delete_post", {"post_id": "../users/me"})
        assert invalid.is_error
        invalid_count = await client.call_tool("list_my_posts", {"max_results": 101})
        assert invalid_count.is_error
        missing = await client.call_tool("get_account", {})
        assert missing.is_error and "x-account-login" in missing.content[0].text


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["auto", "legacy"])
async def test_actual_stdio_server(mode, tmp_path):
    params = StdioServerParameters(command=sys.executable,
        args=["-m", "x_account_mcp.server"], cwd=str(ROOT),
        env={"X_USER_ACCESS_TOKEN": "", "X_TOKEN_FILE": str(tmp_path / "missing.json")})
    async with Client(params, mode=mode) as client:
        assert len((await client.list_tools()).tools) == 14
        result = await client.call_tool("preview_article", {"title": "Stdio test", "body": "Hello"})
        assert not result.is_error
        assert result.structured_content["published"] is False
