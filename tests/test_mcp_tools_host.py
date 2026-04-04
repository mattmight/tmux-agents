"""Tests for host parameter on MCP tools (M15).

Verifies all MCP tools accept the host parameter and the new list_hosts tool.
"""

from __future__ import annotations

from typing import ClassVar

from tmux_agents.mcp.server_common import create_server


class TestAllToolsHaveHostParam:
    """Every tool (except list_hosts) should accept a host parameter."""

    TOOLS_WITHOUT_HOST: ClassVar[set[str]] = {"list_hosts"}

    def test_all_tools_have_host(self):
        server = create_server(safe_mode=False)
        tools = server._tool_manager.list_tools()
        for tool in tools:
            if tool.name in self.TOOLS_WITHOUT_HOST:
                continue
            props = tool.parameters.get("properties", {})
            assert "host" in props, f"Tool '{tool.name}' missing 'host' parameter"

    def test_host_is_optional_on_all_tools(self):
        server = create_server(safe_mode=False)
        tools = server._tool_manager.list_tools()
        for tool in tools:
            if tool.name in self.TOOLS_WITHOUT_HOST:
                continue
            required = tool.parameters.get("required", [])
            assert "host" not in required, f"Tool '{tool.name}' has 'host' as required"


class TestListHostsTool:
    def test_list_hosts_registered(self):
        server = create_server(safe_mode=False)
        tool_names = {t.name for t in server._tool_manager.list_tools()}
        assert "list_hosts" in tool_names

    def test_list_hosts_in_safe_mode(self):
        server = create_server(safe_mode=True)
        tool_names = {t.name for t in server._tool_manager.list_tools()}
        assert "list_hosts" in tool_names

    def test_list_hosts_no_required_params(self):
        server = create_server(safe_mode=False)
        tools = {t.name: t for t in server._tool_manager.list_tools()}
        required = tools["list_hosts"].parameters.get("required", [])
        assert required == []


class TestToolCount:
    def test_unsafe_has_more_tools_than_safe(self):
        """Unsafe mode has terminate_target, safe does not."""
        safe = create_server(safe_mode=True)
        unsafe = create_server(safe_mode=False)
        safe_tools = safe._tool_manager.list_tools()
        unsafe_tools = unsafe._tool_manager.list_tools()
        assert len(unsafe_tools) == len(safe_tools) + 1

    def test_list_hosts_in_both_modes(self):
        """list_hosts is present in both safe and unsafe modes."""
        safe_names = {t.name for t in create_server(safe_mode=True)._tool_manager.list_tools()}
        unsafe_names = {t.name for t in create_server(safe_mode=False)._tool_manager.list_tools()}
        assert "list_hosts" in safe_names
        assert "list_hosts" in unsafe_names


class TestPingWithHost:
    def test_ping_schema_has_host(self):
        server = create_server(safe_mode=False)
        tools = {t.name: t for t in server._tool_manager.list_tools()}
        props = tools["ping"].parameters.get("properties", {})
        assert "host" in props
