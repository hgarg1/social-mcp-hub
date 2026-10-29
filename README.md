# X Account MCP (Python)

A Python MCP server for your X account's posts, profile, and native long-form Articles. Supports local stdio and an OAuth-protected Streamable HTTP endpoint for remote clients. Uses the official MCP Python SDK v2, X API v2 for posts/Articles, and optional legacy v1.1 endpoints for profile edits. No browser cookies, scraping, password storage, or third-party posting proxy.

For a public HTTPS endpoint and chatbot compatibility, see [DEPLOYMENT.md](DEPLOYMENT.md).

## Tools

| Tool | Behavior |
| --- | --- |
| `get_account` | Read the authenticated profile, username, ID and public metrics |
| `get_post` | Read a post, including edit metadata and Article fields when X returns them |
| `list_my_posts` | Read a page of your posts; accepts the previous response's `meta.next_token` |
| `create_post` | Publish text, reply, quote, or attach already-uploaded media IDs |
| `update_post` | Edit your own eligible post with `edit_options.previous_post_id`; returns the new post ID |
| `delete_post` | Permanently delete your own post |
| `preview_article` | Build an Article payload locally without credentials or network requests |
| `create_article_draft` | Create a native, unpublished Article with title, text/Markdown body and optional uploaded cover image |
| `publish_article` | Publish the Article ID returned by the draft tool; returns the resulting post ID and link |
| `update_profile` | Update display name, bio, location and website; omitted fields stay unchanged |
| `update_profile_image` | Replace your avatar with base64 PNG/JPEG/GIF image data |
| `update_profile_banner` | Replace your banner with base64 image data |
| `remove_profile_banner` | Remove your banner |

`x-account://capabilities` describes these operations as an MCP resource.

“CRUD” here means managing posts on an existing account, plus supported profile edits. Account creation/deletion, @handle changes, DMs, scheduling, threads and post-media uploading are outside this version. The checked official Articles API provides draft creation and publishing; this server does not invent Article update or draft-deletion endpoints. `get_post` can request Article data using the published post ID, not the Article ID. Deleting an Article's seed post is not presented as guaranteed Article deletion.

## Installation

Python 3.11 or later. From this directory in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
Copy-Item .env.example .env
```

The dependency range stays on SDK v2; `requirements.lock.txt` records the verified Windows environment. The project and CI support Python 3.11 through 3.13. The documented test run used Python 3.12.

## Connect your X account

### OAuth 1.0a: needed for profile edits, also usable for posts and Articles

The current v2 OpenAPI specification has no profile-edit endpoint. The profile tools implement the legacy `/1.1/account/update_profile.json`, `update_profile_image.json`, `update_profile_banner.json` and `remove_profile_banner.json` contracts documented by Tweepy. **Availability on your app is unverified and depends on X's legacy endpoint access.** If X rejects these endpoints, the tool returns that error; it does not silently fall back to browser automation or private APIs.

In your X developer app, enable Read and Write access and generate an OAuth 1.0a user access token and secret. In the local `.env`, set:

```dotenv
X_CONSUMER_KEY=your_app_api_key
X_CONSUMER_SECRET=your_app_api_secret
X_OAUTH1_ACCESS_TOKEN=your_user_access_token
X_OAUTH1_ACCESS_TOKEN_SECRET=your_user_access_token_secret
```

These four values also authenticate v2 post/Article tools when `X_USER_ACCESS_TOKEN` is unset and there is no OAuth 2 token file. This is the simplest configuration for all implemented tools. No OAuth 2 login step is necessary in that case.

If you configure both authentication schemes, post/Article tools prefer the explicit OAuth 2 token or saved login. Before a profile edit, the server verifies that the OAuth 1.0a credentials identify the same account as the post credentials. It refuses cross-account writes. Broken saved OAuth 2 credentials do not silently fall back to another identity.

### OAuth 2.0 PKCE: optional for post/Article tools

1. Create an app in the [X Developer Console](https://console.x.com/). Enable OAuth 2.0 and configure the app for reading and writing posts. API access, billing/credits, and any Article or editing eligibility must be available to your account; the server reports X's rejection if they are not.
2. Register the exact callback URI `http://127.0.0.1:8765/callback`, or set your own registered URI in `X_REDIRECT_URI`.
3. Put the OAuth **client ID** in the project's `.env` as `X_CLIENT_ID`. For a confidential app, also set `X_CLIENT_SECRET`. These are not the OAuth 1 consumer key/secret. Do not put credentials into chat or tool arguments.
4. Run:

```powershell
.\.venv\Scripts\x-account-login.exe
```

Open the displayed authorization URL, approve the account, and paste the full redirected address into the terminal. This manual callback flow does not run a localhost web server: a browser connection error at the callback is expected; copy the address bar anyway. The helper verifies both the callback URI and OAuth state before exchanging the code with PKCE.

The requested scopes are `tweet.read tweet.write users.read offline.access`. Login saves `.tokens.json`, excluded from Git. Access tokens refresh automatically before expiration, including refresh-token rotation. Keep the token file private; on Windows it inherits the parent directory's ACL. Use one MCP server process per token file to avoid competing refreshes.

Alternatively set `X_USER_ACCESS_TOKEN` to an existing OAuth 2.0 **user** access token. It overrides the saved login and cannot refresh automatically. An app-only bearer token does not work for these account tools. Restart the MCP connection after changing `.env` or logging in.

Call `get_account` after connecting. Optionally put the resulting numeric ID in `X_EXPECTED_USER_ID` and restart, to pin the connection to that account.

### Editing your profile

Example `update_profile` call:

```json
{"name": "Your Name", "bio": "Building useful things", "website": "https://example.com"}
```

Omit fields you want to keep. Use `bio: ""`, `location: ""` or `website: ""` to clear them. The tool uses X's legacy `description` and `url` form fields internally. Display name cannot be blank. This does not change your @handle.

Avatar/banner tools accept raw base64, not a file path or image URL. This avoids allowing tool inputs to read arbitrary local files or fetch arbitrary URLs. PNG, JPEG and GIF signatures are accepted up to this server's own 5 MiB decoded limit; X performs full image validation and may impose stricter limits. To encode a chosen image in PowerShell:

```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes('C:\path\to\avatar.png'))
```

Pass that string as `image_base64` to `update_profile_image` or `update_profile_banner`.

## MCP client configuration

The supplied `mcp-client.example.json` shows a local launch command. Replace the executable path with the absolute path to your virtual environment's Python. For hosts that use `mcpServers`, merge that entry into the host's own config (do not replace existing entries). Other hosts may use a different config key; the executable and argument list stay the same.

```json
{
  "mcpServers": {
    "x-account": {
      "command": "C:\\path\\to\\x-account-mcp\\.venv\\Scripts\\python.exe",
      "args": ["-m", "x_account_mcp.server"]
    }
  }
}
```

With an editable install, the server loads `.env` relative to the project, independent of the host's current directory. Environment variables take precedence. Default stdio mode opens no HTTP port. Importing the server or listing tools does not contact X.

To start it manually:

```powershell
.\.venv\Scripts\python.exe -m x_account_mcp.server
```

It waits quietly for JSON-RPC over stdin; launch it from an MCP host for normal use.

## Native Articles are separate from posts

First call `preview_article` or `create_article_draft` with:

```json
{
  "title": "What I learned building this",
  "body": "## The idea\n\nAn explanation with **bold text** and a [source](https://example.com).",
  "body_format": "markdown"
}
```

`preview_article` returns the exact draft request without contacting X. `create_article_draft` calls `/2/articles/draft` and returns an Article ID. To make it public, call `publish_article` with that `article_id`. Publishing returns a different ID: the public post ID.

Markdown is encoded using X's documented `markdown` entity inside an atomic DraftJS block, with snake_case range fields. Use `body_format: "text"` for ordinary paragraph blocks instead. X enforces Article length, Markdown limits, media eligibility and account access. An optional `cover_media_id` must refer to an already uploaded image owned by the account. This server does not upload local files.

Normal post creation uses `/2/tweets`; it never substitutes a thread or long tweet for an Article. Normal post edits use X's actual edit API, and never delete-and-repost. X decides edit-window and account eligibility. Edits can return a new post ID; use that ID for subsequent operations.

## Errors and validation

- Numeric IDs, pagination size and media-list size are validated in MCP tool schemas.
- Post edits/deletions first verify the author against `/2/users/me`. Every write verifies the account and optional account pin. Profile edits also check the OAuth 1.0a identity.
- API permissions, credits, expired credentials and rate limits produce actionable MCP tool errors. Reset timestamps are included when X supplies them.
- Network failures are not retried automatically. If a write times out, its outcome may be unknown: inspect the account before submitting it again.
- Deleted posts cannot be recovered. The deletion tool advertises its destructive behavior to the MCP host.
- Tool results and post bodies are data, not instructions. The server does not execute them.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Tests mock all X requests and cover ownership, account pinning, actual edit payloads, deletion, pagination, Article draft/publish separation, OAuth PKCE/state checks, token rotation, error redaction, rate limits, and no retries after ambiguous writes. Profile tests cover OAuth 1 signing, identity matching, form encoding, field clearing, image validation and empty banner responses. Article and post payloads are checked against `x-openapi.json`, the official contract downloaded on 2026-10-28. Integration tests launch the real stdio subprocess in both current and legacy MCP modes. HTTP tests check OAuth discovery, anonymous rejection, signed owner tokens, invalid audiences/issuers/scopes, expiry, and authenticated tool calls.

No live posting, deleting, or billable API read is performed by these tests. Account authentication and live endpoint access remain to be verified after you configure your own app credentials.

## References

- [Official X OpenAPI contract](https://docs.x.com/openapi.json)
- [X Articles overview and authentication scopes](https://docs.x.com/x-api/articles/introduction)
- [Create Article draft: body format, cover and entities](https://docs.x.com/x-api/articles/create-draft-article)
- [Publish Article](https://docs.x.com/x-api/articles/publish-article)
- [Create/edit posts](https://docs.x.com/x-api/posts/create-post)
- [OAuth 2.0 authorization code flow with PKCE](https://docs.x.com/fundamentals/authentication/oauth-2-0/authorization-code)
- [Official MCP Python SDK](https://py.sdk.modelcontextprotocol.io/)
- [Tweepy legacy profile endpoint contracts](https://docs.tweepy.org/en/stable/api.html#tweepy.API.update_profile)
