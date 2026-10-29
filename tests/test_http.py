import time
from types import SimpleNamespace

from cryptography.hazmat.primitives.asymmetric import rsa
import httpx
import jwt
import pytest

from x_account_mcp.http_auth import HttpSettings, OwnerTokenVerifier
from x_account_mcp.server import build_server


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def config():
    return HttpSettings(public_url="https://mcp.example.com/mcp", issuer="https://identity.example.com",
                        jwks_url="https://identity.example.com/jwks", audience="x-account-api", owner_subject="owner-42")


@pytest.fixture
def signer(config, monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    verifier = OwnerTokenVerifier(config)
    monkeypatch.setattr(verifier.jwks, "get_signing_key_from_jwt", lambda _: SimpleNamespace(key=key.public_key()))
    def token(**overrides):
        claims = {"iss": config.issuer, "aud": config.audience, "sub": config.owner_subject,
                  "exp": int(time.time()) + 600, "scope": config.scope, "client_id": "test-host", **overrides}
        return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-key"})
    return verifier, token


@pytest.mark.anyio
async def test_owner_access_token_accepted(signer):
    verifier, token = signer
    result = await verifier.verify_token(token())
    assert result.subject == "owner-42" and result.client_id == "test-host"


@pytest.mark.anyio
@pytest.mark.parametrize("changes", [
    {"iss": "https://attacker.example/"}, {"aud": "some-other-api"}, {"sub": "someone-else"},
    {"exp": 1}, {"scope": "unrelated"}, {"client_id": ""}, {"nbf": 9999999999},
])
async def test_tokens_for_other_users_or_resources_rejected(signer, changes):
    verifier, token = signer
    assert await verifier.verify_token(token(**changes)) is None


@pytest.mark.anyio
async def test_unsigned_and_symmetric_tokens_rejected(signer):
    verifier, _ = signer
    assert await verifier.verify_token("not-a-jwt") is None
    assert await verifier.verify_token(jwt.encode({"sub": "owner-42"}, "attacker-secret" * 3, algorithm="HS256")) is None


def test_http_requires_all_auth_settings(monkeypatch):
    for name in ("X_MCP_PUBLIC_URL", "X_MCP_AUTH_ISSUER", "X_MCP_AUTH_JWKS_URL", "X_MCP_AUTH_AUDIENCE", "X_MCP_OWNER_SUBJECT"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValueError, match="HTTP mode requires"):
        HttpSettings.from_env()


@pytest.mark.anyio
async def test_http_auth_metadata_and_authenticated_tool_call(config, signer):
    verifier, token = signer
    server = build_server(http_settings=config, token_verifier=verifier)
    app = server.streamable_http_app(stateless_http=True, json_response=True,
                                     transport_security=config.transport_security())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://mcp.example.com") as client:
            health = await client.get("/health")
            assert health.json() == {"status": "ok"}
            missing = await client.post("/mcp", json={})
            assert missing.status_code == 401
            assert "resource_metadata=" in missing.headers["www-authenticate"]
            metadata = await client.get("/.well-known/oauth-protected-resource/mcp")
            assert metadata.status_code == 200
            assert metadata.json()["resource"] == config.public_url
            assert metadata.json()["authorization_servers"] == [config.issuer]
            invalid = await client.post("/mcp", headers={"Authorization": "Bearer not-valid"}, json={})
            assert invalid.status_code == 401
            headers = {"Authorization": "Bearer " + token(), "Accept": "application/json, text/event-stream",
                       "MCP-Protocol-Version": "2025-11-25"}
            result = await client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1,
                "method": "tools/call", "params": {"name": "preview_article", "arguments": {"title": "Test", "body": "Hello"}}})
            assert result.status_code == 200, result.text
            payload = result.json()["result"]
            assert payload["structuredContent"]["format"] == "x_article"
            wrong_host = await client.post("/mcp", headers={**headers, "Host": "evil.example"}, json={})
            assert wrong_host.status_code == 421
