# Deploy Social MCP Hub

## Current deployment

- Repository: https://github.com/hgarg1/social-mcp-hub
- Public MCP endpoint: https://x-account-mcp.onrender.com/mcp
- Render service ID: srv-dbhcdtom7kps73eh98l0, Free compute, Oregon
- Production WorkOS issuer: https://everlasting-glade-44.authkit.app
- Encrypted provider token storage: Neon Postgres Free

The public URL intentionally retains its original hostname. A display-name/repository rename does not require changing OAuth audiences, discovery or existing ChatGPT connections. If changing the URL later, update the WorkOS Resource Indicator, audience, server public URL and clients together.

Existing unprefixed tools are X tools. Instagram tools use the instagram_ prefix. Existing x-account-mcp and x-account-login commands remain supported. New launches can use social-mcp-hub or python -m social_mcp_hub.server.

## Render commands

Build: pip install -r requirements.lock.txt && pip install --no-deps .

Preferred start: social-mcp-hub --transport http --host 0.0.0.0

The currently configured x-account-mcp start command is a compatibility alias for the same hub. PORT is provided by Render. /health is public and returns only status, with no API calls. /mcp requires authentication. Discovery is at /.well-known/oauth-protected-resource/mcp.

## Shared owner authentication

Keep the existing X_MCP_* environment names for compatibility; they protect all hub tools, including Instagram:

| Variable | Purpose |
| --- | --- |
| X_MCP_PUBLIC_URL | Exact HTTPS MCP URL ending /mcp |
| X_MCP_AUTH_ISSUER | Exact production WorkOS issuer, preserving its slash behavior |
| X_MCP_AUTH_JWKS_URL | Trusted provider signing-key endpoint |
| X_MCP_AUTH_AUDIENCE | WorkOS Resource Indicator; the public MCP URL |
| X_MCP_OWNER_SUBJECT | The single authorized WorkOS user ID |
| X_MCP_AUTH_SCOPE | openid |
| X_MCP_ALLOWED_ORIGINS | Optional exact origins, comma-separated |

Configure WorkOS Connect CIMD/DCR, Resource Indicator and refresh support for clients. The server validates signature, issuer, audience, expiry, scope, client identity and owner subject. Other users cannot access the tools simply by signing into WorkOS. Tokens and errors never log credentials.

## Durable provider credentials

X uses X_CLIENT_ID, X_CLIENT_SECRET, X_TOKEN_DATABASE_URL and X_TOKEN_ENCRYPTION_KEY. Keep X_EXPECTED_USER_ID to pin the X identity. Migrations/001_oauth_tokens.sql creates the encrypted X row table. Login through python -m x_account_mcp.auth saves directly to the database when configured. It serializes rotating refresh-token use with a Postgres row lock. No paid disk or background refresh worker is required.

Instagram uses a separate table created by migrations/002_instagram_tokens.sql. That empty table has been prepared on this deployment. It reuses the database/encryption key by default; optional INSTAGRAM_TOKEN_DATABASE_URL and INSTAGRAM_TOKEN_ENCRYPTION_KEY override them. See INSTAGRAM.md for conversion, Meta setup, login, tools and limitations. Instagram is currently unconnected; this does not prevent X from working.

X_VERIFY_ACCOUNT_ON_START=true performs one authenticated X account lookup per process startup. It avoids a database/API call on every health probe.

Never overwrite or regenerate the encryption key during redeployment. Missing keys, revoked credentials, expired Instagram tokens or a crash between a remote refresh and database commit can require reconnecting.

## Deployment checks

1. Push to main and monitor the specific automatic deployment until live.
2. Verify /health returns 200, anonymous /mcp returns 401, and discovery advertises the production issuer.
3. Confirm startup logs verify the configured X account.
4. Refresh tool metadata in ChatGPT and start a new conversation. Existing sign-in remains valid subject to provider expiry/revocation.
5. Call preview_article and instagram_capabilities without publishing. Test real Instagram account lookup/publication only after account conversion and credentials are configured.

Render Free can sleep and cold starts can delay client initialization. Neon/Render free quotas still apply. X API access has separate costs. Instagram live permissions and eligibility remain to be verified in Meta's dashboard.
