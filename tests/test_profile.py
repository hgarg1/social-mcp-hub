import base64
from urllib.parse import parse_qs

import httpx
import pytest

from x_account_mcp.client import XClient
from x_account_mcp.config import Settings
from x_account_mcp.errors import XError


@pytest.fixture
def anyio_backend():
    return "asyncio"


def settings(tmp_path, *, oauth2=True):
    return Settings(access_token="oauth2-user-token" if oauth2 else "", token_file=tmp_path / "missing.json",
        consumer_key="consumer-key", consumer_secret="consumer-secret",
        oauth1_access_token="oauth1-token", oauth1_access_secret="oauth1-secret")


def identity(request, *, legacy_id="42"):
    if request.url.path == "/2/users/me":
        return httpx.Response(200, json={"data": {"id": "42", "username": "owner"}})
    if request.url.path == "/1.1/account/verify_credentials.json":
        assert request.headers["authorization"].startswith("OAuth ")
        assert "oauth_signature=" in request.headers["authorization"]
        assert "oauth_token=" in request.headers["authorization"]
        return httpx.Response(200, json={"id_str": legacy_id, "screen_name": "owner"})
    return None


@pytest.mark.anyio
async def test_profile_update_signs_form_and_preserves_omitted_fields(tmp_path):
    writes = []
    def handler(request):
        response = identity(request)
        if response is not None:
            return response
        writes.append(request)
        assert request.url.path == "/1.1/account/update_profile.json"
        assert request.headers["authorization"].startswith("OAuth ")
        assert request.headers["content-type"] == "application/x-www-form-urlencoded"
        data = parse_qs(request.content.decode(), keep_blank_values=True)
        assert data == {"name": ["Name & Co"], "description": [""], "skip_status": ["true"]}
        assert "location" not in data and "url" not in data
        return httpx.Response(200, json={"id_str": "42", "name": "Name & Co", "description": ""})
    api = XClient(settings(tmp_path), httpx.MockTransport(handler))
    result = await api.update_profile(name="Name & Co", bio="")
    assert result["description"] == "" and len(writes) == 1


@pytest.mark.anyio
async def test_profile_identity_mismatch_never_writes(tmp_path):
    def handler(request):
        assert request.method == "GET"
        return identity(request, legacy_id="99")
    with pytest.raises(XError, match="different account"):
        await XClient(settings(tmp_path), httpx.MockTransport(handler)).update_profile(bio="Bio")


@pytest.mark.anyio
async def test_oauth1_can_authenticate_posts_without_oauth2(tmp_path):
    def handler(request):
        assert request.headers["authorization"].startswith("OAuth ")
        if request.method == "GET":
            return identity(request)
        assert request.url.path == "/2/tweets"
        assert request.headers["content-type"] == "application/json"
        return httpx.Response(201, json={"data": {"id": "123", "text": "Hello"}})
    result = await XClient(settings(tmp_path, oauth2=False), httpx.MockTransport(handler)).create_post("Hello")
    assert result["data"]["id"] == "123"


@pytest.mark.anyio
@pytest.mark.parametrize("banner", [False, True])
async def test_profile_images_are_signed_and_base64_survives_form_encoding(tmp_path, banner):
    # A tiny fixture with the PNG signature; X performs full image validation.
    image = base64.b64encode(b"\x89PNG\r\n\x1a\n\xfb\xff\xff").decode()
    def handler(request):
        response = identity(request)
        if response is not None:
            return response
        expected = "update_profile_banner" if banner else "update_profile_image"
        assert request.url.path == f"/1.1/account/{expected}.json"
        assert request.headers["authorization"].startswith("OAuth ")
        data = parse_qs(request.content.decode())
        assert data["banner" if banner else "image"] == [image]
        return httpx.Response(200 if banner else 201, content=b"" if banner else b'{"id_str":"42"}')
    result = await XClient(settings(tmp_path), httpx.MockTransport(handler)).update_profile_picture(image, banner=banner)
    assert result == ({"success": True} if banner else {"id_str": "42"})


@pytest.mark.anyio
async def test_remove_banner_accepts_empty_success(tmp_path):
    def handler(request):
        response = identity(request)
        if response is not None:
            return response
        assert request.url.path == "/1.1/account/remove_profile_banner.json"
        assert request.method == "POST" and request.content == b""
        return httpx.Response(200)
    result = await XClient(settings(tmp_path), httpx.MockTransport(handler)).remove_profile_banner()
    assert result == {"success": True}


@pytest.mark.anyio
@pytest.mark.parametrize("image", ["not base64!", "data:image/png;base64,AAAA", base64.b64encode(b"secret text file").decode()])
async def test_bad_images_never_make_requests(tmp_path, image):
    def handler(_):
        pytest.fail("Invalid images must be rejected locally")
    with pytest.raises(XError):
        await XClient(settings(tmp_path), httpx.MockTransport(handler)).update_profile_picture(image)


@pytest.mark.anyio
async def test_missing_profile_credentials_is_actionable(tmp_path):
    api = XClient(Settings(access_token="oauth2", token_file=tmp_path / "missing.json"))
    with pytest.raises(XError, match="X_CONSUMER_KEY"):
        await api.update_profile(bio="New")


@pytest.mark.anyio
async def test_no_op_profile_rejected_locally(tmp_path):
    with pytest.raises(XError, match="at least one"):
        await XClient(settings(tmp_path)).update_profile()


@pytest.mark.anyio
async def test_legacy_endpoint_denied_is_reported_without_retry(tmp_path):
    writes = []
    def handler(request):
        response = identity(request)
        if response is not None:
            return response
        writes.append(request)
        return httpx.Response(403, json={"errors": [{"code": 453, "message": "Endpoint access denied"}]})
    with pytest.raises(XError, match="Endpoint access denied"):
        await XClient(settings(tmp_path), httpx.MockTransport(handler)).update_profile(bio="New")
    assert len(writes) == 1
