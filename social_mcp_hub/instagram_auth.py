"""Interactive Instagram Login; never send tokens through MCP tool arguments."""
import secrets
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from .instagram import InstagramError, InstagramSettings, InstagramTokens, api_result

SCOPES = "instagram_business_basic,instagram_business_content_publish"


def authorization_request(settings):
    if not settings.client_id or not settings.client_secret:
        raise InstagramError("Set INSTAGRAM_CLIENT_ID and INSTAGRAM_CLIENT_SECRET from Meta's Instagram Login settings.")
    state = secrets.token_urlsafe(32)
    url = "https://www.instagram.com/oauth/authorize?" + urlencode({
        "client_id": settings.client_id, "redirect_uri": settings.redirect_uri,
        "response_type": "code", "scope": SCOPES, "state": state,
        "enable_fb_login": "0", "force_authentication": "1"})
    return url, state


def callback_code(callback, redirect_uri, state):
    actual, expected = urlsplit(callback), urlsplit(redirect_uri)
    if (actual.scheme, actual.netloc, actual.path) != (expected.scheme, expected.netloc, expected.path):
        raise InstagramError("Callback URI does not match INSTAGRAM_REDIRECT_URI.")
    query = parse_qs(actual.query)
    states, codes = query.get("state", []), query.get("code", [])
    if len(states) != 1 or not secrets.compare_digest(states[0], state):
        raise InstagramError("OAuth state does not match; restart instagram-login.")
    if "error" in query or len(codes) != 1 or not codes[0]:
        raise InstagramError("Instagram authorization did not return a code.")
    return codes[0]


def exchange_code(settings, code, transport=None):
    try:
        with httpx.Client(timeout=30, transport=transport) as client:
            short = api_result(client.post("https://api.instagram.com/oauth/access_token", data={
                "client_id": settings.client_id, "client_secret": settings.client_secret,
                "grant_type": "authorization_code", "redirect_uri": settings.redirect_uri, "code": code}))
            if not short.get("access_token"):
                raise InstagramError("Instagram returned no access token.")
            long = api_result(client.get("https://graph.instagram.com/access_token", params={
                "grant_type": "ig_exchange_token", "client_secret": settings.client_secret,
                "access_token": short["access_token"]}))
            if not long.get("access_token") or float(long.get("expires_in", 0)) <= 0:
                raise InstagramError("Instagram returned no long-lived token.")
            account = api_result(client.get(f"https://graph.instagram.com/{settings.api_version}/me",
                params={"fields": "id,user_id,username,account_type"},
                headers={"Authorization": "Bearer " + long["access_token"]}))
        user_id = str(account.get("user_id") or account.get("id") or "")
        if not user_id or account.get("account_type") not in ("BUSINESS", "MEDIA_CREATOR"):
            raise InstagramError("Convert your Instagram account to Creator or Business before connecting.")
        if settings.expected_user_id and user_id != settings.expected_user_id:
            raise InstagramError("Login does not match INSTAGRAM_EXPECTED_USER_ID; existing credentials were not replaced.")
        now = time.time()
        return {"access_token": long["access_token"], "user_id": user_id,
                "expires_at": now + float(long["expires_in"]), "refreshed_at": now}, account
    except httpx.RequestError:
        raise InstagramError("Instagram login network request failed. Restart the login flow; credentials were not saved.") from None


def main():
    settings = InstagramSettings.from_env()
    try:
        url, state = authorization_request(settings)
        print("Authorize your Creator/Business Instagram account:\n" + url)
        print("This helper has no local web listener. A callback connection error is expected; copy the full address bar URL.")
        callback = input("Full redirected URL: ").strip()
        code = callback_code(callback, settings.redirect_uri, state)
        tokens, account = exchange_code(settings, code)
        InstagramTokens(settings).save(tokens)
        print("Encrypted Instagram login saved for @" + account.get("username", "your account") + ". Restart the MCP connection.")
    except (InstagramError, ValueError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
