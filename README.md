<p align="center">
  <img src="docs/images/hero.jpg" alt="The hub's X integration, connected to ChatGPT." width="100%">
</p>

# Social MCP Hub

A personal [MCP](https://modelcontextprotocol.io) hub for managing X and Instagram from compatible AI assistants. One Python service exposes 21 tools behind an owner-only OAuth login, with separate encrypted credentials for each platform.

The public endpoint remains `https://x-account-mcp.onrender.com/mcp` so existing sign-in and chatbot connections keep working. The project is now [social-mcp-hub](https://github.com/hgarg1/social-mcp-hub); the main launch command is `social-mcp-hub`. Existing X tool names, imports and launch commands remain compatible.

| Integration | Available operations | Deployment status |
| --- | --- | --- |
| X | Read/create/edit/delete posts, image uploads, native Article drafts and publishing | Live; connected through ChatGPT |
| Instagram | Professional-account lookup, media listing, photo/Reel containers and explicit publishing | Implemented and mock-tested; live login awaits Creator/Business conversion and Meta setup |
| X profile edits | Profile fields, avatar and banner tools | Legacy OAuth 1.0a only; unavailable in the deployed OAuth 2 setup |

Instagram is optional: `instagram_capabilities` works without credentials, and an unconnected Instagram account does not block X. Personal Instagram accounts are unsupported. See [INSTAGRAM.md](INSTAGRAM.md) for setup and current limits.

The assistant sends structured tool arguments; platform credentials stay on the server. X Articles and Instagram media containers have separate preparation and publishing calls so preparing content does not immediately make it public.

Local clients use stdio. Remote clients use Streamable HTTP on an OAuth resource server that admits a single owner. Posts and Articles go through X API v2. Profile edits use the legacy v1.1 account endpoints. Authentication is OAuth. The server stores no passwords and does not scrape a browser session.

## Architecture

<p align="center">
  <img src="docs/images/flow.png" alt="ChatGPT authorizes with AuthKit, this server checks the token, then calls the X API. X credentials stay in one encrypted Postgres row." width="100%">
</p>

ChatGPT or another MCP client signs in through WorkOS AuthKit, then sends its access token to the hub. The hub verifies the token and permits only the configured owner. WorkOS handles client login, registration and consent; X and Instagram independently authorize the hub to act on their accounts.

```mermaid
flowchart LR
  client[ChatGPT / Codex / MCP client]
  idp[WorkOS AuthKit]
  mcp[Social MCP Hub on Render]
  db[(Neon Postgres: separate encrypted provider records)]
  x[X API]
  ig[Instagram API: pending account connection]

  client -->|Authorization code + PKCE| idp
  idp -->|RS256 or ES256 access token| client
  client -->|Streamable HTTP /mcp| mcp
  idp -->|JWKS| mcp
  mcp -->|Official user-context API| x
  mcp -->|Instagram Login token| ig
  mcp <-->|Fernet encryption and row-locked refresh| db
```

Every accepted token is RS256 or ES256 and must match the configured issuer, audience, expiry, owner `sub`, scope, and a `client_id` or `azp`. The JWKS URL comes from configuration, never from the token. `/health` returns `{"status":"ok"}` and nothing else. Anonymous calls to `/mcp` get a 401 and the protected-resource metadata.

X OAuth 2 tokens are a different system from the chatbot login. They live in one Postgres row, encrypted with Fernet. When the access token is inside a minute of expiry, the worker locks that row, refreshes at X, and commits the rotated refresh token before any other worker can reuse the old one. The host can sleep. The login survives the restart. Losing the encryption key means signing in again. A crash between X's refresh and the commit can mean the same thing, and the server says so.

Instagram has its own encrypted row and token lifecycle. Its long-lived access token renews on use when fewer than seven days remain, while still valid and at least 24 hours old. If it expires during a long period without use, reconnecting is necessary. Neither provider requires a paid Render disk or background refresh worker. Free hosting quotas and cold starts still apply; X API access has separate costs.

The deployment recipe is [DEPLOYMENT.md](DEPLOYMENT.md).

## Pictures the tools can carry

`upload_image` takes a PNG or JPEG as raw base64. It checks the file signature and this server's 5 MiB cap, then hands the bytes to X. A tool argument cannot point at a local path or an arbitrary URL.

<table>
  <tr>
    <td width="50%" align="center"><img src="docs/images/hello-world.jpg" alt="Paper-cut robot waving beside a monitor that reads Hello world." width="100%"></td>
    <td width="50%" align="center"><img src="docs/images/article.jpg" alt="Paper-cut robot lifting the top page from a stack of illustrated sheets." width="100%"></td>
  </tr>
  <tr>
    <td align="center">A post image. The monitor text is part of the picture. The tool sends the bytes.</td>
    <td align="center">An Article is a draft first. A cover is an uploaded media id. Publish is a separate call and returns a different post id.</td>
  </tr>
</table>

## X tools

| Tool | What it does |
| --- | --- |
| `get_account` | Read the authenticated profile, username, ID, and public metrics |
| `get_post` | Read a post, including edit metadata and Article fields when X returns them |
| `list_my_posts` | Read one page of your posts. Pass `meta.next_token` back as `pagination_token` |
| `upload_image` | Upload PNG or JPEG as raw base64. Returns a media ID. Requires `media.write` |
| `create_post` | Publish text, a reply, a quote, or up to four already-uploaded media IDs |
| `update_post` | Edit your own eligible post with `edit_options.previous_post_id`. Returns the new post ID |
| `delete_post` | Permanently delete your own post |
| `preview_article` | Build the Article payload locally. No credentials. No network |
| `create_article_draft` | Create an unpublished native Article. Optional cover is an uploaded image |
| `publish_article` | Publish that Article ID. Returns the public post ID and link |
| `update_profile` | Change display name, bio, location, or website. Omitted fields stay as they are |
| `update_profile_image` | Replace the avatar with base64 PNG, JPEG, or GIF |
| `update_profile_banner` | Replace the banner with base64 PNG, JPEG, or GIF |
| `remove_profile_banner` | Remove the banner |

`x-account://capabilities` exposes the same boundary as an MCP resource.

Writes that change or remove data are annotated for the host. `delete_post` is marked destructive. An edit that X reports as ineligible never becomes a delete followed by a new post. Article preview, draft, and publish are three tools because a draft is not public and the Article ID is not the post ID. The checked Articles API documents draft creation and publishing. This server does not invent an Article update or draft-delete endpoint. `get_post` reads Article data from the published post ID. Deleting an Article's seed post is not treated as guaranteed Article deletion.

X account creation/deletion, @handle changes, DMs, scheduling, threads, and video or GIF uploads are outside this integration. X image tools take raw base64, up to this server's own 5 MiB decoded cap, so a tool argument cannot read an arbitrary local path or fetch an arbitrary URL. X does the real image validation.

## Instagram tools

| Tool | What it does |
| --- | --- |
| `instagram_capabilities` | Describe setup and supported operations without credentials or network calls |
| `instagram_get_account` | Read and verify the connected Creator/Business account |
| `instagram_list_media` | List your media; paginate with `cursors.after` |
| `instagram_create_photo_container` | Prepare an unpublished photo using a public HTTPS JPEG URL |
| `instagram_create_reel_container` | Prepare an unpublished Reel using a public HTTPS video URL |
| `instagram_get_container_status` | Check whether processing is complete |
| `instagram_publish_container` | Publish only after the container reports `FINISHED` |

The publishing flow is **create container → check status → explicitly publish → verify media**. Meta fetches the supplied media URL; the hub does not download it. Generated-media hosting and resumable uploads are not implemented. Carousels, Stories, comments, DMs, profile edits, media editing/deletion and scheduling are also outside this version.

After converting to Creator or Business, configure Instagram Login with `instagram_business_basic` and `instagram_business_content_publish`, then run:

```powershell
.\.venv\Scripts\python.exe -m social_mcp_hub.instagram_auth
```

The helper verifies OAuth state, exchanges the code for a long-lived token, checks the professional account and saves an encrypted login. Full instructions, callback registration, storage settings and live-verification limits are in [INSTAGRAM.md](INSTAGRAM.md).

## Install

Python 3.11 or later. The project supports 3.11 through 3.13. The documented test run used 3.12. From this directory in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
Copy-Item .env.example .env
```

`requirements.lock.txt` pins the verified Windows environment. The dependency range stays on MCP SDK v2.

Point a local host at the virtualenv. Merge this entry into an existing `mcpServers` map:

```json
{
  "mcpServers": {
    "social-mcp-hub": {
      "command": "C:\\path\\to\\social-mcp-hub\\.venv\\Scripts\\python.exe",
      "args": ["-m", "social_mcp_hub.server"]
    }
  }
}
```

`mcp-client.example.json` is the same snippet. An editable install loads `.env` from the project directory, independent of the host's working directory. Environment variables win. Default stdio mode opens no port. Importing the server or listing tools does not contact either platform.

```powershell
.\.venv\Scripts\python.exe -m social_mcp_hub.server
```

That process waits for JSON-RPC on stdin. Launch it from an MCP host for normal use.

For the deployed service, connect a compatible remote MCP client to `https://x-account-mcp.onrender.com/mcp` and complete WorkOS sign-in. Local stdio configuration does not test the hosted OAuth flow. Refresh existing clients' tool metadata after this update, then call `instagram_capabilities` to check discovery without connecting Instagram or publishing anything. See [DEPLOYMENT.md](DEPLOYMENT.md) for hosting and authentication settings.

## Connect the X account

The live deployment uses OAuth 2 with durable encrypted storage. Legacy profile tools are optional and require a separate authentication method.

### Optional legacy OAuth 1.0a

The current v2 OpenAPI specification has no profile-edit endpoint. Profile tools call the legacy `/1.1/account/update_profile.json`, `update_profile_image.json`, `update_profile_banner.json`, and `remove_profile_banner.json` contracts documented by Tweepy. Access depends on what X still allows for your app. A rejection comes back as that error.

Enable Read and Write on the X app, then put the user access token in `.env`:

```dotenv
X_CONSUMER_KEY=your_app_api_key
X_CONSUMER_SECRET=your_app_api_secret
X_OAUTH1_ACCESS_TOKEN=your_user_access_token
X_OAUTH1_ACCESS_TOKEN_SECRET=your_user_access_token_secret
```

Those four values can also authenticate v2 post and Article tools when no OAuth 2 database, explicit access token or saved login is configured. They do not enable Instagram or guarantee that X grants legacy endpoint access.

When both schemes are configured, post and Article tools prefer an explicit OAuth 2 token or a saved login. Before a profile edit, the server checks that the OAuth 1.0a identity is the same account as the post credentials and refuses a cross-account write. A broken saved OAuth 2 login does not fall through to another identity.

### OAuth 2.0 PKCE

1. Create an app in the [X Developer Console](https://console.x.com/). Enable OAuth 2.0 with permission to read and write posts. API access, credits, and Article or edit eligibility have to exist on the account. The server reports X's rejection when they do not.
2. Register the callback `http://127.0.0.1:8765/callback`, or set another registered URI in `X_REDIRECT_URI`.
3. Put the OAuth client ID in `.env` as `X_CLIENT_ID`. For a confidential app, also set `X_CLIENT_SECRET`. These are not the OAuth 1 consumer key and secret.
4. Run `.\.venv\Scripts\python.exe -m x_account_mcp.auth`.

Open the authorization URL, approve the account, and paste the full redirected address into the terminal. The helper does not listen on localhost. A browser error on the callback is expected. Copy the address bar anyway. The helper checks the callback URI and the OAuth state before the PKCE code exchange.

Requested scopes are `tweet.read tweet.write users.read media.write offline.access`. With `X_TOKEN_DATABASE_URL` configured, login saves directly to the encrypted database. Otherwise, it writes `.tokens.json`, which Git ignores. Access tokens refresh before expiration, including refresh-token rotation. Keep local token files private and use one server process per file; the database mode serializes refreshes across processes.

Database storage takes precedence over `X_USER_ACCESS_TOKEN` and local tokens. Without a database, `X_USER_ACCESS_TOKEN` overrides the saved file and cannot refresh. An app-only bearer token cannot operate these account tools. Restart the MCP connection after changing `.env` or logging in.

Call `get_account`. You can pin the numeric ID with `X_EXPECTED_USER_ID` and restart. A token for a different account then fails before a write.

Hosted OAuth 2 rotation uses encrypted Neon Postgres storage on the Free plan, so a Render restart does not require a paid disk. See [durable provider credentials](DEPLOYMENT.md#durable-provider-credentials).

### Profile edits

```json
{"name": "Your Name", "bio": "Building useful things", "website": "https://example.com"}
```

Omit fields you want to keep. `bio: ""`, `location: ""`, or `website: ""` clears that field. Display name cannot be blank. The @handle stays as it is.

Avatar and banner tools accept raw base64. Encode a file in PowerShell and pass `image_base64`:

```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes('C:\path\to\avatar.png'))
```

## Articles

```json
{
  "title": "What I learned building this",
  "body": "## The idea\n\nAn explanation with **bold text** and a [source](https://example.com).",
  "body_format": "markdown"
}
```

`preview_article` returns the exact draft request and does not contact X. `create_article_draft` calls `/2/articles/draft` and returns an Article ID. `publish_article` publishes that ID and returns a different one: the public post ID.

Markdown is encoded as X's documented `markdown` entity inside an atomic DraftJS block, with snake_case range fields. `body_format: "text"` builds ordinary paragraph blocks. An optional `cover_media_id` must already be an uploaded image owned by the account. `upload_image` is how you get that ID.

Normal posts go to `/2/tweets`. The server does not substitute a thread or a long post for an Article. Edits can return a new post ID. Use that ID afterward. X decides the edit window and account eligibility.

## Errors

- Numeric IDs, page size, and the media list are validated in the MCP tool schemas.
- Post edits and deletes load the author and compare it with `/2/users/me`. Every write checks the account and the optional account pin. Profile edits also check the OAuth 1.0a identity.
- Permission errors, credit errors, expired credentials, and rate limits come back as actionable tool errors, including X's reset time when it sends one.
- Network failures are not retried. If a write times out, the outcome may be unknown. Look at the account before submitting it again.
- Deleted posts cannot be recovered. The delete tool advertises that to the host.
- Tool results and post bodies are data. The server does not execute them.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

The suite mocks every X request. It covers ownership, account pinning, the real edit payload, deletion, pagination, Article draft and publish as separate calls, PKCE and state, token rotation, error redaction, rate limits, and a single attempt after an ambiguous write. Profile tests cover OAuth 1 signing, identity matching, form encoding, field clearing, image validation, and an empty banner response. Article and post payloads are checked against `x-openapi.json`, the official contract saved on 2026-10-28. Integration tests launch the real stdio subprocess in current and legacy MCP modes. HTTP tests cover OAuth discovery, anonymous rejection, signed owner tokens, bad audiences, issuers, and scopes, expiry, and an authenticated tool call.

Instagram tests cover unpublished-container creation, readiness checks before publishing, account pinning, OAuth callback validation, encrypted storage, serialized token refresh, pagination without token-bearing URLs, sanitized errors and no blind write retries. Both the new hub entry point and the compatible X entry point are exercised over stdio. The current suite has 74 passing tests, with CI covering Python 3.11–3.13.

Those tests do not post, delete, or spend API credits. X and ChatGPT are live-verified; Instagram remains mock-tested until account conversion, Meta setup and a successful live login/publication.

## References

- [Official X OpenAPI contract](https://docs.x.com/openapi.json)
- [X Articles overview and authentication scopes](https://docs.x.com/x-api/articles/introduction)
- [Create Article draft](https://docs.x.com/x-api/articles/create-draft-article)
- [Publish Article](https://docs.x.com/x-api/articles/publish-article)
- [Create and edit posts](https://docs.x.com/x-api/posts/create-post)
- [OAuth 2.0 authorization code with PKCE](https://docs.x.com/fundamentals/authentication/oauth-2-0/authorization-code)
- [Official MCP Python SDK](https://py.sdk.modelcontextprotocol.io/)
- [Tweepy legacy profile endpoint contracts](https://docs.tweepy.org/en/stable/api.html#tweepy.API.update_profile)
- [Meta's official Instagram Login API collection](https://www.postman.com/meta/instagram/folder/1z5vxzu/instagram-api-with-instagram-login)
- [Instagram setup and verification limits](INSTAGRAM.md)
