import base64
import json

import httpx
import pytest

from x_account_mcp.client import XClient
from x_account_mcp.config import Settings
from x_account_mcp.errors import XError


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_image_upload_returns_media_id():
    image = base64.b64encode(b"\x89PNG\r\n\x1a\nfixture").decode()
    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"data": {"id": "42"}})
        assert request.url.path == "/2/media/upload"
        assert json.loads(request.content) == {"media": image, "media_category": "tweet_image"}
        return httpx.Response(200, json={"data": {"id": "123", "expires_after_secs": 86400}})
    api = XClient(Settings(access_token="test-token"), httpx.MockTransport(handler))
    assert (await api.upload_image(image))["data"]["id"] == "123"


@pytest.mark.anyio
@pytest.mark.parametrize("image", ["invalid!", "data:image/png;base64,AAAA", base64.b64encode(b"text").decode()])
async def test_invalid_upload_rejected_without_network(image):
    def handler(_):
        pytest.fail("Invalid images must not leave the server")
    api = XClient(Settings(access_token="test-token"), httpx.MockTransport(handler))
    with pytest.raises(XError):
        await api.upload_image(image)
