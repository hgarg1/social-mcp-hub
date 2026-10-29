# Public MCP deployment

## Recommended setup

Run one Python web service on **Render**, with **WorkOS AuthKit / Connect** as its OAuth authorization server. The MCP server handles your X account, while AuthKit handles chatbot client login, PKCE, client registration and refresh tokens. These are separate systems from X's own OAuth credentials.

The server is a personal integration: it accepts only the configured owner's identity. Publishing this source repository does not make your account public or turn it into a multi-user X SaaS.

This is a prepared deployment path, not a deployment that has already been provisioned or tested against your live identity provider. No paid infrastructure is created by the repository.

Why Render:

- It runs ordinary Python web services and gives each service a public `onrender.com` hostname with managed HTTPS.
- The process binds to `0.0.0.0` and Render's `PORT` environment variable.
- Start with the smallest Free web compute plan for a trial. Free services sleep after 15 minutes of inactivity and can take about a minute to wake. For routine chatbot use, the smallest paid web compute plan avoids that Free sleep behavior; confirm the current price in the Dashboard before selecting it.
- Store X credentials as service environment variables. With OAuth 1.0a credentials, this application needs no database or writable token storage. OAuth 2 rotating token files require a persistent volume and one instance, so do not use an ephemeral local token file for a hosted production login.

A normal Python service on Railway, Fly.io or a VPS is also structurally suitable, but Render is the deployment path documented here. Do not deploy the default stdio command as a web service.

## Public endpoint and OAuth

The public MCP URL will be:

```text
https://YOUR-SERVICE.onrender.com/mcp
```

This is a placeholder shape, not a live URL. `/health` is public and returns only `{"status":"ok"}`. `/mcp` requires a signed bearer token. OAuth resource metadata is available at `/.well-known/oauth-protected-resource/mcp`.

HTTP startup refuses to proceed without all of the following:

| Environment variable | Value |
| --- | --- |
| `X_MCP_PUBLIC_URL` | Exact public HTTPS URL ending in `/mcp` |
| `X_MCP_AUTH_ISSUER` | Exact `issuer` string from the OAuth provider's discovery metadata, preserving any trailing slash |
| `X_MCP_AUTH_JWKS_URL` | Provider's trusted HTTPS JWKS endpoint |
| `X_MCP_AUTH_AUDIENCE` | Expected API access-token audience; for AuthKit configure the public MCP URL as a Resource Indicator and use that same URL here |
| `X_MCP_OWNER_SUBJECT` | Your stable user ID (`sub`) in the OAuth provider, not your X user ID or email address |
| `X_MCP_AUTH_SCOPE` | Default `openid`; optionally use a provider-supported custom access scope |
| `X_MCP_ALLOWED_ORIGINS` | Optional comma-separated exact origins for clients that send browser Origin headers; no wildcard is needed |

Tokens must use RS256 or ES256 and contain valid signature, issuer, audience, expiry, matching owner subject, required scope and a requesting `client_id` or `azp`. The audience and owner checks are enforced even if a different user successfully signs into the provider. A general sign-in alone never grants access to your X tools.

`openid` is deliberately used for AuthKit's dynamically registered clients, which receive its standard OIDC scopes. Owner and audience pinning provide the access restriction for this personal server. Do not assume that AuthKit DCR clients can request arbitrary custom permission scopes without additional provider configuration.

### Configure AuthKit / Connect

1. Create an AuthKit environment and enable a login method for your own user. Record your user ID as `X_MCP_OWNER_SUBJECT`.
2. Under Connect configuration, enable **Client ID Metadata Document (CIMD)** and **Dynamic Client Registration (DCR)** to cover newer and older MCP clients.
3. Register the exact public `/mcp` endpoint as a **Resource Indicator**. Set it as the default too if you want to accommodate clients that omit `resource`.
4. Use the provider's exact metadata `issuer` as `X_MCP_AUTH_ISSUER`. AuthKit's Connect JWKS endpoint is `https://YOUR-AUTHKIT-DOMAIN/oauth2/jwks`; use that as `X_MCP_AUTH_JWKS_URL`.
5. Set `X_MCP_AUTH_AUDIENCE` to the public `/mcp` Resource Indicator. Keep `X_MCP_AUTH_SCOPE=openid` for the standard dynamic-client flow.
6. Enable/verify refresh-token support and `offline_access` in provider discovery if clients should stay connected. Restrict permitted client redirects according to the provider's MCP guidance.

The code implements the resource-server side, not its own login page or token issuer. An existing provider is necessary; filling random strings into these fields does not create OAuth.

## Create the Render web service

1. In Render choose **New → Web Service**, connect your GitHub repo, and select branch `main` and the Python runtime. Use Python 3.12, the locally tested version, if selecting a runtime version.
2. Build command:

   ```sh
   pip install -r requirements.lock.txt && pip install --no-deps .
   ```

3. Start command:

   ```sh
   x-account-mcp --transport http --host 0.0.0.0
   ```

   The CLI reads the port from Render's `PORT`. Use `/health` for the health-check path.

4. Choose the smallest Free compute plan for testing, or the smallest paid plan for regular availability after reviewing its price. Do not add a database for the OAuth 1.0a environment-variable setup.
5. In the service's Environment section, set the HTTP/OAuth values above and your four X OAuth 1.0a values: `X_CONSUMER_KEY`, `X_CONSUMER_SECRET`, `X_OAUTH1_ACCESS_TOKEN`, `X_OAUTH1_ACCESS_TOKEN_SECRET`. Optionally pin `X_EXPECTED_USER_ID`. These secrets belong in the Render Dashboard, never in GitHub or build arguments.
6. Use the actual Render-assigned hostname for `X_MCP_PUBLIC_URL` and in the AuthKit Resource Indicator. If the first startup happened before these values were known, finish configuring them and redeploy. The fail-closed startup is expected until configuration is complete.
7. Verify `/health` succeeds, an anonymous request to `/mcp` gets HTTP 401, discovery advertises the correct issuer, and the owner can authenticate and call `preview_article`.
8. Call `get_account` and confirm the X identity before making a write. Actual X API access, credits, Article eligibility and legacy profile access remain account-dependent and must be checked live.

If using the public Git repository connection rather than Render's Git-provider integration, Render does not enable auto-deploys for that source type. Connect GitHub directly if you want automatic deployments on pushes.

## Chatbot and harness compatibility

The target is standard Streamable HTTP plus OAuth discovery, not an unrestricted URL that anyone can call. Use the same `/mcp` endpoint in each client and complete the owner's provider sign-in. Each client can create its own OAuth client registration.

| Client | Connection path |
| --- | --- |
| Claude / Claude Code | Add a remote HTTP MCP server and authenticate; local stdio remains available |
| Codex and other MCP IDE/CLI clients | Add the remote URL with OAuth, or use stdio locally |
| ChatGPT | Add a custom MCP server/plugin where account and workspace permissions permit it; configure OAuth and scan the tools |
| Grok.com | Connectors → New Connector → Custom; enter the public URL and authenticate |
| Other applications | Must actually implement custom MCP, Streamable HTTP and the configured OAuth flow |

This does not mean every chatbot UI supports arbitrary MCP servers or that each product has been tested against this deployment. Current and legacy protocol paths are exercised locally, but end-to-end OAuth registration, consent and token refresh must be verified with each chosen client after hosting.

## Sources checked 2026-10-28

- [Render web services, URLs and port binding](https://render.com/docs/web-services)
- [Render Free sleep and filesystem limits](https://render.com/docs/free)
- [Current Render compute plans](https://render.com/docs/compute-plans)
- [Render environment variables and secrets](https://render.com/docs/configure-environment-variables)
- [WorkOS AuthKit MCP authorization and Resource Indicators](https://workos.com/docs/authkit/mcp)
- [WorkOS Connect token claims and scope limitations](https://workos.com/docs/authkit/connect/token-claims)
- [MCP SDK HTTP resource-server authorization](https://py.sdk.modelcontextprotocol.io/run/authorization/)
- [Claude Code MCP](https://code.claude.com/docs/en/mcp)
- [Official OpenAI MCP documentation](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)
- [Grok custom connectors](https://docs.x.ai/grok/connectors)
