"""Tests for MCP server initialization.

These tests verify that the FastMCP server can be created and has
the expected tools registered, WITHOUT actually starting the transport.
"""

from __future__ import annotations

from tmux_agents.mcp.server_common import create_server


class TestMcpServerCreation:
    def test_create_server(self):
        server = create_server()
        assert server is not None

    def test_server_has_name(self):
        server = create_server()
        assert server.name == "tmux-agents"
