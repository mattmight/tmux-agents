"""Configuration loading for tmux-agents.

Config is loaded from (in priority order):
  1. CLI flags / environment variables
  2. Project-local .tmux-agents.toml
  3. User-level ~/.config/tmux-agents/config.toml
  4. Built-in defaults

For M0, only defaults are implemented. File-based config is a stub.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class McpConfig(BaseModel):
    """MCP server configuration."""

    http_host: str = "127.0.0.1"
    http_port: int = 8766
    safe_mode: bool = True


class LoggingConfig(BaseModel):
    """Logging configuration."""

    level: str = "INFO"
    format: str = "json"


class RemoteHostConfig(BaseModel):
    """Configuration for a remote machine reachable via SSH."""

    alias: str = Field(..., description="SSH Host alias from ~/.ssh/config, e.g. 'enterprise-a'")
    display_name: str | None = Field(
        default=None, description="Human-friendly name; defaults to alias"
    )
    extra_socket_paths: list[str] = Field(default_factory=list)
    extra_socket_names: list[str] = Field(default_factory=list)


class TmuxAgentsConfig(BaseModel):
    """Root configuration object."""

    extra_socket_paths: list[str] = Field(default_factory=list)
    extra_socket_names: list[str] = Field(default_factory=list)
    hosts: list[RemoteHostConfig] = Field(default_factory=list)
    mcp: McpConfig = Field(default_factory=McpConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)


def load_config(**overrides: Any) -> TmuxAgentsConfig:
    """Load configuration with overrides.

    For M0, returns defaults merged with any explicit overrides.
    File-based loading (TOML) will be added in a later milestone.
    """
    return TmuxAgentsConfig(**overrides)
