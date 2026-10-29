-- Separate provider record; preserve the existing X token row and constraints.
CREATE TABLE IF NOT EXISTS x_mcp.instagram_tokens (
    id text PRIMARY KEY CHECK (id = 'primary'),
    encrypted_tokens text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
REVOKE ALL ON x_mcp.instagram_tokens FROM PUBLIC;
