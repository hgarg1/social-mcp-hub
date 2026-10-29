"""OAuth 1.0a signing for legacy profile endpoints and optional v2 access."""
import httpx
from oauthlib.oauth1 import Client

from .config import Settings
from .errors import XError


class OAuth1Auth(httpx.Auth):
    requires_request_body = True

    def __init__(self, settings: Settings):
        if not settings.has_oauth1:
            raise XError("Profile edits require X_CONSUMER_KEY, X_CONSUMER_SECRET, "
                         "X_OAUTH1_ACCESS_TOKEN and X_OAUTH1_ACCESS_TOKEN_SECRET with Read and Write access.")
        self.signer = Client(settings.consumer_key, client_secret=settings.consumer_secret,
                             resource_owner_key=settings.oauth1_access_token,
                             resource_owner_secret=settings.oauth1_access_secret)

    def auth_flow(self, request: httpx.Request):
        # OAuth 1 signs form bodies, not JSON or multipart bodies.
        body = request.content.decode("utf-8") if request.headers.get("content-type", "").startswith(
            "application/x-www-form-urlencoded") else None
        _, headers, _ = self.signer.sign(str(request.url), http_method=request.method,
                                        body=body, headers=dict(request.headers))
        request.headers.update(headers)
        yield request
