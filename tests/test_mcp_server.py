"""End-to-end MCP server tests.

Tests server creation, tool registration, safe mode, auth, and output bounding.
"""

from __future__ import annotations

import asyncio

from tmux_agents.mcp.auth import BearerTokenVerifier
from tmux_agents.mcp.server_common import create_server

# -- Server creation tests ---------------------------------------------------


class TestServerCreation:
    def test_creates_with_all_tools(self):
        server = create_server(safe_mode=False)
        assert server is not None
        assert server.name == "tmux-agents"

    def test_safe_mode_creates(self):
        server = create_server(safe_mode=True)
        assert server is not None


class TestSafeMode:
    def test_safe_mode_omits_terminate(self):
        """Safe mode server should not have terminate_target tool."""
        safe = create_server(safe_mode=True)
        unsafe = create_server(safe_mode=False)

        safe_tools = {t.name for t in safe._tool_manager.list_tools()}
        unsafe_tools = {t.name for t in unsafe._tool_manager.list_tools()}

        assert "terminate_target" not in safe_tools
        assert "terminate_target" in unsafe_tools

    def test_safe_mode_keeps_inspection_tools(self):
        """Safe mode should still have all inspection tools."""
        server = create_server(safe_mode=True)
        tools = {t.name for t in server._tool_manager.list_tools()}

        for expected in [
            "ping",
            "list_inventory",
            "list_agents",
            "inspect_target",
            "capture_pane",
            "read_pane_delta",
        ]:
            assert expected in tools, f"Missing tool: {expected}"

    def test_safe_mode_keeps_interaction_tools(self):
        """Safe mode should still have interaction tools."""
        server = create_server(safe_mode=True)
        tools = {t.name for t in server._tool_manager.list_tools()}

        for expected in ["send_text", "send_keys", "set_metadata", "spawn_agent"]:
            assert expected in tools, f"Missing tool: {expected}"


class TestToolCatalog:
    def test_all_mvp_tools_registered(self):
        """All MVP tools from PLAN section 11 should be registered."""
        server = create_server(safe_mode=False)
        tools = {t.name for t in server._tool_manager.list_tools()}

        expected = {
            "ping",
            "list_inventory",
            "list_agents",
            "inspect_target",
            "capture_pane",
            "read_pane_delta",
            "send_text",
            "send_keys",
            "spawn_agent",
            "set_metadata",
            "terminate_target",
        }
        assert expected.issubset(tools), f"Missing: {expected - tools}"

    def test_tool_count(self):
        server = create_server(safe_mode=False)
        tools = list(server._tool_manager.list_tools())
        assert len(tools) >= 11


# -- Auth tests --------------------------------------------------------------


class TestBearerAuth:
    def test_no_token_allows_all(self):
        verifier = BearerTokenVerifier(expected_token=None)
        assert not verifier.is_configured
        result = asyncio.run(verifier.verify_token("anything"))
        assert result is not None
        assert result.client_id == "anonymous"

    def test_correct_token_passes(self):
        verifier = BearerTokenVerifier(expected_token="secret123")
        assert verifier.is_configured
        result = asyncio.run(verifier.verify_token("secret123"))
        assert result is not None
        assert result.client_id == "bearer"

    def test_wrong_token_rejected(self):
        verifier = BearerTokenVerifier(expected_token="secret123")
        result = asyncio.run(verifier.verify_token("wrong"))
        assert result is None

    def test_empty_token_not_configured(self):
        verifier = BearerTokenVerifier(expected_token="")
        assert not verifier.is_configured


# -- Output bounding tests ---------------------------------------------------


class TestOutputBounding:
    def test_max_capture_chars_defined(self):
        from tmux_agents.mcp.tools import MAX_CAPTURE_CHARS

        assert MAX_CAPTURE_CHARS > 0
        assert MAX_CAPTURE_CHARS <= 50000  # reasonable upper bound


# -- CLI serve tests ---------------------------------------------------------


class TestServeCli:
    def test_serve_stdio_help(self):
        from click.testing import CliRunner

        from tmux_agents.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["mcp", "serve-stdio", "--help"])
        assert result.exit_code == 0

    def test_serve_http_help(self):
        from click.testing import CliRunner

        from tmux_agents.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["mcp", "serve-http", "--help"])
        assert result.exit_code == 0
        assert "--host" in result.output
        assert "--port" in result.output
        assert "--no-safe-mode" in result.output
        assert "--auth-token" in result.output
