"""Structured error envelope for tmux-agents.

Every error response follows the same shape, whether returned from CLI or MCP.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from tmux_agents.refs import TargetRef


class ErrorCode(StrEnum):
    """Canonical error codes."""

    # General
    UNKNOWN = "unknown"
    INVALID_ARGUMENT = "invalid_argument"
    NOT_FOUND = "not_found"
    PERMISSION_DENIED = "permission_denied"
    TIMEOUT = "timeout"

    # Tmux-specific
    TMUX_NOT_FOUND = "tmux_not_found"
    TMUX_VERSION_UNSUPPORTED = "tmux_version_unsupported"
    SOCKET_NOT_FOUND = "socket_not_found"
    SESSION_NOT_FOUND = "session_not_found"
    PANE_NOT_FOUND = "pane_not_found"
    PANE_DEAD = "pane_dead"
    COMMAND_FAILED = "command_failed"

    # Agent-specific
    SPAWN_FAILED = "spawn_failed"
    DETECTION_FAILED = "detection_failed"
    CAPTURE_FAILED = "capture_failed"
    PATTERN_TIMEOUT = "pattern_timeout"

    # MCP-specific
    MCP_TRANSPORT_ERROR = "mcp_transport_error"


class ErrorContext(BaseModel):
    """Which operation and target produced the error."""

    operation: str = Field(..., description="The operation that failed, e.g. 'capture_pane'")
    target: TargetRef | None = None


class ErrorEnvelope(BaseModel):
    """Structured error returned by all operations."""

    code: ErrorCode
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    context: ErrorContext | None = None


class TmuxAgentsError(Exception):
    """Base exception carrying a structured ErrorEnvelope."""

    def __init__(self, envelope: ErrorEnvelope) -> None:
        self.envelope = envelope
        super().__init__(envelope.message)


class TmuxNotFoundError(TmuxAgentsError):
    """Raised when tmux binary is not found."""


class TmuxVersionError(TmuxAgentsError):
    """Raised when tmux version is unsupported."""


class SocketNotFoundError(TmuxAgentsError):
    """Raised when a tmux socket does not exist."""


class PaneNotFoundError(TmuxAgentsError):
    """Raised when a pane reference cannot be resolved."""


class PaneDeadError(TmuxAgentsError):
    """Raised when an operation targets a dead pane."""
