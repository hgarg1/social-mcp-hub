# Instagram on Social MCP Hub

## Current status

The tools and OAuth helper are implemented and tested with mocked API responses. Live Instagram login/publishing is pending conversion of the owner's Personal account to Creator or Business and Meta app setup. X remains usable throughout. This integration is not live-verified yet.

## Tools

| Tool | Operation |
| --- | --- |
| `instagram_capabilities` | Setup and supported features; no credentials/network |
| `instagram_get_account` | Read and verify the connected professional account |
| `instagram_list_media` | Read media; paginate with `cursors.after` |
| `instagram_create_photo_container` | Prepare an unpublished photo from a public HTTPS JPEG URL |
| `instagram_create_reel_container` | Prepare an unpublished Reel from a public HTTPS video URL |
| `instagram_get_container_status` | Check processing status |
| `instagram_publish_container` | Publish only after status is `FINISHED` |

No carousels, Stories, DMs, comments, profile edits, media edits/deletion, scheduling, music or interactive stickers are implemented. This is not full Instagram CRUD. Meta enforces account eligibility, permissions, media specifications and quotas. The API version defaults to `v25.0` and is configurable; select a version supported by your Meta app.

## Connect after account conversion

1. Switch Instagram to a professional **Creator** or **Business** account.
2. Create a Meta developer app and select the Instagram use case and **API setup with Instagram login**. Confirm access requirements in the dashboard; ensure your account has the required app/tester role and accept invitations. Serving other users may require additional review and verification.
3. Request `instagram_business_basic` and `instagram_business_content_publish`. These are Instagram Login permissions, separate from Facebook Login's Page-scoped permissions.
4. Register the exact redirect URI `http://127.0.0.1:8766/callback` if accepted by your Meta app, or set `INSTAGRAM_REDIRECT_URI` to an exact registered URI. This is independent of X's callback on port 8765 and ChatGPT's WorkOS callback. The helper consumes a pasted callback and does not run a web listener.
5. Store `INSTAGRAM_CLIENT_ID` and `INSTAGRAM_CLIENT_SECRET` in your local `.env`. Use the Instagram-specific ID/secret from Instagram Login setup. Never paste them into a chatbot, MCP argument or Git.
6. Hosted storage requires `migrations/002_instagram_tokens.sql` after migration 001. This deployment already has that empty table. Leave `INSTAGRAM_TOKEN_DATABASE_URL` and `INSTAGRAM_TOKEN_ENCRYPTION_KEY` empty to reuse the existing X database/key, or configure separate overrides. Preserve the encryption key.
7. From the project directory, run:

   ```powershell
   .\.venv\Scripts\python.exe -m social_mcp_hub.instagram_auth
   ```

   Open the displayed URL, approve permissions, then paste the full redirected address into the terminal. A browser connection error at the local callback is expected. OAuth state and the exact callback are verified. The helper exchanges the code for a long-lived token, verifies a professional account, and encrypts the token with the account ID and expiry.
8. Call `instagram_get_account`. Optionally set `INSTAGRAM_EXPECTED_USER_ID` locally and on Render to the returned `user_id` (or `id` if absent), then restart/redeploy. Every operation already verifies the saved account identity; the environment pin adds another check.
9. With the shared hosted database, the login is immediately available to Render. Local `.instagram.tokens.json` storage is for local mode, ignored by Git, and unsuitable for Render's ephemeral filesystem. Local mode requires an encryption key and one server process per token file.
10. Refresh your ChatGPT plugin's tools and start a new conversation. Keep the same public MCP URL and WorkOS login; Instagram needs no separate MCP endpoint.

## Token lifecycle

Long-lived Instagram access tokens renew through `refresh_access_token`; this differs from X's separate rotating refresh token. The implementation refreshes on use when fewer than seven days remain and the token is at least 24 hours old. Expired/revoked tokens require reconnecting. If the hub is unused past expiry, no background job rescues the login.

Database lookup/refresh holds a row lock and commits the encrypted replacement before returning it. Instagram uses `x_mcp.instagram_tokens`, separate from X's `x_mcp.oauth_tokens`. Errors exclude token strings, provider messages and database credentials. Pagination returns cursors instead of provider URLs that can embed credentials.

## Publish a photo or Reel

1. Supply an existing publicly accessible HTTPS JPEG or video URL. Meta fetches the media; the MCP server does not read local paths or download arbitrary URLs. Hosting generated media and resumable uploads are not implemented. Signed URLs must remain valid through processing.
2. Call `instagram_create_photo_container` or `instagram_create_reel_container` with an optional caption. Creation does **not** publish.
3. Check `instagram_get_container_status`. Wait between checks while processing. Investigate `ERROR`/`EXPIRED`; do not recreate blindly.
4. After `FINISHED`, explicitly call `instagram_publish_container`. It rechecks status and returns the published media ID.
5. Verify with `instagram_list_media`, which returns a permalink when Meta supplies one. Network write failures are not automatically retried because publication may have succeeded.

## Official references and verification limits

- [Meta's Instagram Login API collection](https://www.postman.com/meta/instagram/folder/1z5vxzu/instagram-api-with-instagram-login)
- [Instagram Platform](https://developers.facebook.com/docs/instagram-platform/)
- [Content publishing](https://developers.facebook.com/docs/instagram-platform/content-publishing/)
- [Token refresh](https://developers.facebook.com/docs/instagram-platform/reference/refresh_access_token/)
- [App review](https://developers.facebook.com/docs/instagram-platform/app-review)

Meta's documentation returned rate-limit errors during implementation. Its official API collection was available through indexed content; dashboard access requirements and account-specific eligibility must be confirmed during live setup. Tests verify flow separation, payloads, identity checks, encryption/refresh, sanitized errors and no blind write retries; they do not prove live Meta eligibility.
