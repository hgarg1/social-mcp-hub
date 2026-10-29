"""OAuth resource-server configuration for a remotely hosted personal MCP."""
import asyncio
from dataclasses import dataclass
import os
from urllib.parse import urlsplit

import jwt
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl


@dataclass(frozen=True)
class HttpSettings:
    public_url: str
    issuer: str
    jwks_url: str
    audience: str
    owner_subject: str
    scope: str = "openid"
    additional_origins: tuple[str, ...] = ()

    def __post_init__(self):
        for name in ("public_url", "issuer", "jwks_url"):
            value = urlsplit(getattr(self, name))
            if value.scheme != "https" or not value.netloc or value.username or value.password or value.query or value.fragment:
                raise ValueError(f"{name} must be an HTTPS URL without credentials, query or fragment")
        if urlsplit(self.public_url).path != "/mcp":
            raise ValueError("public_url must end in /mcp")
        if not self.audience.strip() or not self.owner_subject.strip() or not self.scope.strip():
            raise ValueError("OAuth audience, owner subject and scope are required")

    @classmethod
    def from_env(cls) -> "HttpSettings":
        names = {"public_url": "X_MCP_PUBLIC_URL", "issuer": "X_MCP_AUTH_ISSUER",
                 "jwks_url": "X_MCP_AUTH_JWKS_URL", "audience": "X_MCP_AUTH_AUDIENCE",
                 "owner_subject": "X_MCP_OWNER_SUBJECT"}
        values = {key: os.environ.get(name, "").strip() for key, name in names.items()}
        missing = [names[key] for key, value in values.items() if not value]
        if missing:
            raise ValueError("HTTP mode requires: " + ", ".join(missing))
        origins = tuple(value.strip() for value in os.environ.get("X_MCP_ALLOWED_ORIGINS", "").split(",") if value.strip())
        return cls(**values, scope=os.environ.get("X_MCP_AUTH_SCOPE", "openid"), additional_origins=origins)

    def auth_settings(self) -> AuthSettings:
        return AuthSettings(issuer_url=AnyHttpUrl(self.issuer),
                            resource_server_url=AnyHttpUrl(self.public_url), required_scopes=[self.scope],
                            # The verifier checks the exact configured JWT audience.
                            validate_token_resource=False)

    def transport_security(self) -> TransportSecuritySettings:
        url = urlsplit(self.public_url)
        return TransportSecuritySettings(enable_dns_rebinding_protection=True,
            allowed_hosts=[url.netloc], allowed_origins=[f"{url.scheme}://{url.netloc}", *self.additional_origins])


class OwnerTokenVerifier:
    """Trust only signed, unexpired API tokens for this audience and the owner.

    Login, PKCE, consent, client registration and refresh tokens are delegated to
    the configured OAuth provider. This class never creates tokens or users.
    """

    def __init__(self, settings: HttpSettings):
        self.settings = settings
        self.jwks = jwt.PyJWKClient(settings.jwks_url, cache_jwk_set=True, lifespan=300, timeout=10)

    def _verify(self, token: str) -> AccessToken | None:
        if not token or len(token) > 16_384:
            return None
        try:
            # Never accept symmetric algorithms or a JWKS URL from the token.
            if jwt.get_unverified_header(token).get("alg") not in ("RS256", "ES256"):
                return None
            key = self.jwks.get_signing_key_from_jwt(token).key
            claims = jwt.decode(token, key, algorithms=["RS256", "ES256"],
                audience=self.settings.audience, issuer=self.settings.issuer,
                options={"require": ["exp", "iss", "aud", "sub"]})
            if claims["sub"] != self.settings.owner_subject:
                return None
            raw_scope = claims.get("scope", "")
            if not isinstance(raw_scope, str):
                return None
            scopes = raw_scope.split()
            if self.settings.scope not in scopes:
                return None
            client_id = claims.get("client_id") or claims.get("azp")
            if not isinstance(client_id, str) or not client_id:
                return None
            return AccessToken(token=token, client_id=client_id, scopes=scopes,
                expires_at=int(claims["exp"]), subject=claims["sub"],
                resource=self.settings.public_url, claims={"iss": self.settings.issuer})
        except (jwt.PyJWTError, ValueError, TypeError, KeyError):
            # Authentication failures never log tokens or claim contents.
            return None

    async def verify_token(self, token: str) -> AccessToken | None:
        # PyJWKClient may fetch/refresh keys; never block the MCP event loop.
        return await asyncio.to_thread(self._verify, token)
