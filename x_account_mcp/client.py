"""Small X API client; never retries a write whose outcome may be unknown."""
import base64
import binascii
import re
from urllib.parse import urlencode

import httpx

from .auth import TokenProvider
from .config import Settings
from .errors import XError
from .oauth1 import OAuth1Auth

POST_FIELDS = "author_id,created_at,edit_controls,edit_history_tweet_ids,public_metrics,article,note_tweet"


def require_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,19}", value):
        raise XError("X IDs must be strings containing 1 to 19 digits.")
    return value


class XClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.transport = transport
        self.tokens = TokenProvider(settings, transport)

    async def request(self, method: str, path: str, *, params: dict | None = None, payload: dict | None = None,
                      version: str = "2", oauth1: bool = False, form: dict | None = None,
                      allow_empty: bool = False) -> dict:
        # Never fall back from a broken OAuth 2 login to a different identity.
        use_oauth1 = oauth1 or (self.settings.has_oauth1 and not self.settings.access_token
                               and not self.settings.token_file.exists())
        token = "" if use_oauth1 else await self.tokens.access_token()
        headers = {"Accept": "application/json"}
        auth = OAuth1Auth(self.settings) if use_oauth1 else None
        if token:
            headers["Authorization"] = "Bearer " + token
        kwargs: dict = {"params": params}
        if form is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            kwargs["content"] = urlencode(form).encode("utf-8")
        elif payload is not None:
            kwargs["json"] = payload
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=10), transport=self.transport, auth=auth) as client:
                response = await client.request(method, f"https://api.x.com/{version}/" + path,
                                               headers=headers, **kwargs)
        except httpx.RequestError as exc:
            message = "X API network request failed."
            if method != "GET":
                message += " The write outcome is unknown; inspect your account before retrying to avoid duplicates."
            raise XError(message) from exc
        if allow_empty and response.is_success and not response.content:
            return {"success": True}
        try:
            result = response.json()
        except ValueError as exc:
            raise XError(f"X returned a non-JSON response (HTTP {response.status_code}).") from exc
        if not isinstance(result, dict):
            raise XError("X returned an unexpected response shape.")
        if response.is_error:
            # Only surface diagnostic text, never the full request/response.
            legacy_errors = result.get("errors")
            legacy_message = legacy_errors[0].get("message") if isinstance(legacy_errors, list) and legacy_errors and isinstance(legacy_errors[0], dict) else None
            detail = str(result.get("detail") or result.get("title") or legacy_message or "Request rejected")[:800]
            for secret in (token, self.settings.consumer_key, self.settings.consumer_secret,
                           self.settings.oauth1_access_token, self.settings.oauth1_access_secret):
                if secret:
                    detail = detail.replace(secret, "[redacted]")
            hint = {
                401: "Use a valid OAuth user token; sign in again if it expired.",
                402: "Check X developer billing and API credits.",
                403: "Check app permissions, account eligibility and endpoint access.",
                429: "Rate limit reached; wait before retrying.",
            }.get(response.status_code, "")
            reset = response.headers.get("x-rate-limit-reset")
            if response.status_code == 429 and reset and reset.isdigit():
                hint += f" Reset time (Unix seconds): {reset}."
            raise XError(f"X API HTTP {response.status_code}: {detail} {hint}".strip())
        # Preserve partial-success errors rather than discarding them.
        if result.get("errors") and not result.get("data"):
            raise XError("X returned API errors without data. Check endpoint access and the requested ID.")
        return result

    async def account(self) -> dict:
        result = await self.request("GET", "users/me", params={
            "user.fields": "description,public_metrics,profile_image_url,verified,created_at,url",
        })
        user = result.get("data")
        if not isinstance(user, dict) or not user.get("id"):
            raise XError("X did not return the authenticated user. An app-only bearer token is insufficient.")
        if self.settings.expected_user_id and user["id"] != self.settings.expected_user_id:
            raise XError("Authenticated account does not match X_EXPECTED_USER_ID.")
        return result

    async def upload_image(self, image_base64: str) -> dict:
        if len(image_base64) > 7_000_000:
            raise XError("Image exceeds the 5 MiB decoded-image limit.")
        try:
            raw = base64.b64decode(image_base64, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise XError("Supply raw base64 image data without a data: prefix.") from exc
        if len(raw) > 5 * 1024 * 1024:
            raise XError("Image exceeds the 5 MiB decoded-image limit.")
        if not raw.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")):
            raise XError("This tool accepts PNG or JPEG images only.")
        await self.account()
        return await self.request("POST", "media/upload", payload={
            "media": image_base64, "media_category": "tweet_image",
        })

    async def get_post(self, post_id: str) -> dict:
        return await self.request("GET", "tweets/" + require_id(post_id), params={"tweet.fields": POST_FIELDS})

    async def own_post(self, post_id: str) -> dict:
        account = await self.account()
        result = await self.get_post(post_id)
        post = result.get("data", {})
        if post.get("author_id") != account["data"]["id"]:
            raise XError("This post does not belong to the authenticated account.")
        return result

    async def list_posts(self, max_results: int = 20, pagination_token: str | None = None) -> dict:
        account = await self.account()
        params = {"max_results": max_results, "tweet.fields": POST_FIELDS}
        if pagination_token:
            params["pagination_token"] = pagination_token
        return await self.request("GET", f"users/{require_id(account['data']['id'])}/tweets", params=params)

    async def create_post(self, text: str, reply_to: str | None = None, quote_post_id: str | None = None,
                          media_ids: list[str] | None = None) -> dict:
        if not text.strip() and not media_ids:
            raise XError("Supply post text or uploaded media IDs.")
        await self.account()
        payload: dict = {"text": text}
        if reply_to:
            payload["reply"] = {"in_reply_to_tweet_id": require_id(reply_to)}
        if quote_post_id:
            payload["quote_tweet_id"] = require_id(quote_post_id)
        if media_ids:
            payload["media"] = {"media_ids": [require_id(media_id) for media_id in media_ids]}
        return await self.request("POST", "tweets", payload=payload)

    async def update_post(self, post_id: str, text: str) -> dict:
        if not text.strip():
            raise XError("Updated text cannot be blank.")
        original = await self.own_post(post_id)
        if original["data"].get("article"):
            raise XError("Article body editing is not supported by this API. This tool edits normal posts only.")
        if original["data"].get("edit_controls", {}).get("is_edit_eligible") is False:
            raise XError("X reports that this post is not eligible for editing.")
        return await self.request("POST", "tweets", payload={
            "text": text, "edit_options": {"previous_post_id": require_id(post_id)},
        })

    async def delete_post(self, post_id: str) -> dict:
        await self.own_post(post_id)
        return await self.request("DELETE", "tweets/" + require_id(post_id))

    async def create_article_draft(self, payload: dict) -> dict:
        await self.account()
        return await self.request("POST", "articles/draft", payload=payload)

    async def publish_article(self, article_id: str) -> dict:
        await self.account()
        result = await self.request("POST", f"articles/{require_id(article_id)}/publish")
        post_id = result.get("data", {}).get("post_id")
        if post_id:
            result["post_url"] = "https://x.com/i/web/status/" + require_id(post_id)
        return result

    async def profile_identity(self) -> None:
        # Check these credentials first, then match both auth schemes to one account.
        OAuth1Auth(self.settings)
        account = await self.account()
        legacy = await self.request("GET", "account/verify_credentials.json", version="1.1", oauth1=True,
                                    params={"skip_status": "true", "include_entities": "false"})
        if str(legacy.get("id_str") or legacy.get("id") or "") != account["data"]["id"]:
            raise XError("OAuth 1.0a profile credentials belong to a different account than the post credentials.")

    async def update_profile(self, *, name: str | None = None, bio: str | None = None,
                             location: str | None = None, website: str | None = None) -> dict:
        values = {"name": name, "description": bio, "location": location, "url": website}
        form = {key: value for key, value in values.items() if value is not None}
        if not form:
            raise XError("Supply at least one profile field. Empty bio/location/website clears that field.")
        if name is not None and not name.strip():
            raise XError("Display name must not be blank.")
        await self.profile_identity()
        return await self.request("POST", "account/update_profile.json", version="1.1", oauth1=True,
                                  form={**form, "skip_status": "true"})

    async def update_profile_picture(self, image_base64: str, *, banner: bool = False) -> dict:
        # Reject before decoding very large inputs. 5 MiB is this server's own cap;
        # X can impose smaller limits. No arbitrary local-file reads or URL fetches.
        if len(image_base64) > 7_000_000:
            raise XError("Image exceeds this server's 5 MiB decoded-image limit.")
        try:
            raw = base64.b64decode(image_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise XError("Supply raw base64 image data, without a data: URL prefix.") from exc
        if len(raw) > 5 * 1024 * 1024:
            raise XError("Image exceeds this server's 5 MiB decoded-image limit.")
        if not raw.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a")):
            raise XError("Profile images must be PNG, JPEG or GIF. X validates full image contents and limits.")
        await self.profile_identity()
        endpoint = "account/update_profile_banner.json" if banner else "account/update_profile_image.json"
        form = {"banner" if banner else "image": image_base64}
        if not banner:
            form["skip_status"] = "true"
        return await self.request("POST", endpoint, version="1.1", oauth1=True, form=form, allow_empty=banner)

    async def remove_profile_banner(self) -> dict:
        await self.profile_identity()
        return await self.request("POST", "account/remove_profile_banner.json", version="1.1", oauth1=True,
                                  allow_empty=True)
