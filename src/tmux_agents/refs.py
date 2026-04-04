"""Canonical reference types for tmux entities.

Every snapshot object in the system carries a ref that uniquely identifies it
by socket path + tmux stable ID.  Names are for display; IDs are for execution.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ServerRef(BaseModel):
    """Identity of a tmux server by its socket.

    The optional ``host`` field distinguishes remote machines.  When ``None``
    the server is local.  For remote servers the value is the SSH ``Host``
    alias from ``~/.ssh/config`` (e.g. ``"enterprise-a"``).
    """

    model_config = {"frozen": True}

    socket_path: str = Field(
        ..., description="Absolute path to the tmux socket, e.g. /tmp/tmux-501/default"
    )
    socket_name: str = Field(..., description="Short socket name, e.g. 'default'")
    host: str | None = Field(
        default=None,
        description="SSH Host alias for remote servers, None for local",
    )


class SessionRef(BaseModel):
    """Identity of a tmux session."""

    model_config = {"frozen": True}

    id: str = Field(..., description="Tmux session ID, e.g. '$3'")
    name: str = Field(..., description="Session name, e.g. 'auth-refactor'")


class WindowRef(BaseModel):
    """Identity of a tmux window."""

    model_config = {"frozen": True}

    id: str = Field(..., description="Tmux window ID, e.g. '@7'")
    name: str = Field(..., description="Window name, e.g. 'main'")
    index: int = Field(..., description="Window index within its session")


class PaneRef(BaseModel):
    """Identity of a tmux pane."""

    model_config = {"frozen": True}

    id: str = Field(..., description="Tmux pane ID, e.g. '%12'")
    index: int = Field(..., description="Pane index within its window")


class TargetRef(BaseModel):
    """Full canonical reference for any tmux entity.

    All four layers are always present for pane-level references.
    For session-level references, window and pane may be None.
    """

    model_config = {"frozen": True}

    server: ServerRef
    session: SessionRef | None = None
    window: WindowRef | None = None
    pane: PaneRef | None = None
