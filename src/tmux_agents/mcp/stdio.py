"""MCP stdio transport entrypoint.

CRITICAL: This module must NEVER write to stdout. All logging goes to stderr.
stdout is reserved exclusively for JSON-RPC MCP protocol messages.
"""

from __future__ import annotations

from tmux_agents.logging import get_logger


def run_stdio_server() -> None:
    """Launch the MCP server over stdio transport."""
    log = get_logger("mcp.stdio")
    log.info("initializing_stdio_transport")

    from tmux_agents.mcp.server_common import create_server

    server = create_server()
    log.info("stdio_server_ready")
    server.run(transport="stdio")
