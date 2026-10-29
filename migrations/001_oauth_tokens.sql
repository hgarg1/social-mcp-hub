CREATE SCHEMA IF NOT EXISTS x_mcp;
CREATE TABLE IF NOT EXISTS x_mcp.oauth_tokens (
    id text PRIMARY KEY CHECK (id = 'primary'),
    encrypted_tokens text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
REVOKE ALL ON SCHEMA x_mcp FROM PUBLIC;
REVOKE ALL ON x_mcp.oauth_tokens FROM PUBLIC;
