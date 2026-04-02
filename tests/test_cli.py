"""Tests for CLI entrypoint."""

from __future__ import annotations

from click.testing import CliRunner

from tmux_agents.cli.main import cli


class TestCliHelp:
    def test_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "tmux-agents" in result.output

    def test_version(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "0.1.0" in result.output

    def test_mcp_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["mcp", "--help"])
        assert result.exit_code == 0
        assert "serve-stdio" in result.output
        assert "serve-http" in result.output

    def test_list_runs(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["list"])
        assert result.exit_code == 0

    def test_inspect_missing_pane(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["inspect", "--pane", "%99999"])
        # Should fail gracefully with non-zero exit for missing pane
        assert result.exit_code != 0
