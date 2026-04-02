"""Tests for configuration loading."""

from __future__ import annotations

from tmux_agents.config import TmuxAgentsConfig, load_config


class TestConfig:
    def test_defaults(self):
        cfg = load_config()
        assert cfg.mcp.http_host == "127.0.0.1"
        assert cfg.mcp.http_port == 8766
        assert cfg.mcp.safe_mode is True
        assert cfg.logging.level == "INFO"

    def test_override(self):
        cfg = load_config(mcp={"http_port": 9999})
        assert cfg.mcp.http_port == 9999

    def test_schema_snapshot(self, snapshot):
        assert TmuxAgentsConfig.model_json_schema() == snapshot
