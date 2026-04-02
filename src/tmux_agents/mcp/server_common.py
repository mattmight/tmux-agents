"""Shared MCP server creation and tool registration.

Both stdio and HTTP entrypoints use the same FastMCP instance with the same
tool catalog. Transport-specific wiring happens in stdio.py and http.py.

Safe mode (default for HTTP) omits destructive tools entirely rather than
rejecting them at runtime.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP


def create_server(*, safe_mode: bool = False) -> FastMCP:
    """Create and configure the shared FastMCP server instance.

    Args:
        safe_mode: If True, omit destructive tools (terminate_target).
    """
    server = FastMCP("tmux-agents")

    from tmux_agents.mcp.tools import register_tools

    register_tools(server, safe_mode=safe_mode)

    return server
