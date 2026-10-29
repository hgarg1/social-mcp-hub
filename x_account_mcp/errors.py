from mcp.server.mcpserver.exceptions import ToolError


class XError(ToolError):
    """An actionable, credential-free error that MCP may show the caller."""
