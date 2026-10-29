"""Hub entry point; retain the existing X module and tools for compatibility."""
from x_account_mcp.server import build_server, main

if __name__ == "__main__":
    main()
