"""Response models for tmux-agents.

Every snapshot object is normalized into four layers:
  ref     -- stable identity (socket path + tmux IDs)
  display -- human-friendly names and labels
  runtime -- tmux-derived facts (pid, current_command, path, dead, timestamps)
  agent   -- detection results (kind, confidence, source, managed, profile)
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from tmux_agents.refs import TargetRef

# -- Display layer ----------------------------------------------------------


class ServerDisplay(BaseModel):
    """Human-friendly server information."""

    socket_name: str
    session_count: int = 0


class SessionDisplay(BaseModel):
    """Human-friendly session information."""

    name: str
    window_count: int = 0
    attached: bool = False


class WindowDisplay(BaseModel):
    """Human-friendly window information."""

    name: str
    index: int
    pane_count: int = 0


class PaneDisplay(BaseModel):
    """Human-friendly pane information."""

    index: int
    title: str = ""


# -- Runtime layer ----------------------------------------------------------


class PaneRuntime(BaseModel):
    """Tmux-derived runtime facts about a pane."""

    pane_pid: int | None = None
    pane_current_command: str | None = None
    pane_current_path: str | None = None
    pane_dead: bool = False
    pane_width: int | None = None
    pane_height: int | None = None


# -- Agent layer ------------------------------------------------------------


class Confidence(StrEnum):
    """Detection confidence level."""

    STRONG = "strong"
    MODERATE = "moderate"
    WEAK = "weak"
    NONE = "none"


class DetectionSource(StrEnum):
    """How an agent was detected."""

    EXPLICIT = "explicit"
    PROCESS_TREE = "process_tree"
    TMUX_HINT = "tmux_hint"
    NONE = "none"


class AgentInfo(BaseModel):
    """Agent detection and classification data for a pane."""

    detected_kind: str | None = None
    confidence: Confidence = Confidence.NONE
    source: DetectionSource = DetectionSource.NONE
    managed: bool = False
    profile: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    hook_state: dict[str, Any] | None = None


# -- Composite snapshots ----------------------------------------------------


class PaneSnapshot(BaseModel):
    """Complete 4-layer snapshot for a pane."""

    ref: TargetRef
    display: PaneDisplay
    runtime: PaneRuntime
    agent: AgentInfo = Field(default_factory=AgentInfo)


class WindowSnapshot(BaseModel):
    """Snapshot for a window with child pane summaries."""

    ref: TargetRef
    display: WindowDisplay
    panes: list[PaneSnapshot] = Field(default_factory=list)


class SessionSnapshot(BaseModel):
    """Snapshot for a session with child window summaries."""

    ref: TargetRef
    display: SessionDisplay
    windows: list[WindowSnapshot] = Field(default_factory=list)


class ServerSnapshot(BaseModel):
    """Snapshot for a tmux server with child session summaries."""

    ref: TargetRef
    display: ServerDisplay
    sessions: list[SessionSnapshot] = Field(default_factory=list)


class InventorySnapshot(BaseModel):
    """Top-level inventory containing all discovered servers."""

    servers: list[ServerSnapshot] = Field(default_factory=list)
    timestamp: datetime | None = None


# -- Capture models ---------------------------------------------------------


class CaptureMode(StrEnum):
    """Capture operation mode."""

    TAIL = "tail"
    HISTORY = "history"
    SCREEN = "screen"


class ScreenTarget(StrEnum):
    """Which screen buffer to capture."""

    AUTO = "auto"
    PRIMARY = "primary"
    ALTERNATE = "alternate"


class CaptureResult(BaseModel):
    """Result of a pane capture operation."""

    pane_id: str
    mode: CaptureMode
    screen_used: ScreenTarget
    content: str
    line_count: int
    truncated: bool = False
    seq: int
    timestamp: datetime


class CaptureChunk(BaseModel):
    """A chunk of new output from a pane delta."""

    content: str
    line_count: int


class DeltaResult(BaseModel):
    """Result of a delta read operation."""

    pane_id: str
    from_seq: int
    to_seq: int
    chunks: list[CaptureChunk] = Field(default_factory=list)
    total_new_lines: int = 0
    reset_required: bool = False
    timestamp: datetime


# -- Orchestration models ---------------------------------------------------


class SpawnTarget(StrEnum):
    """Where to spawn a new agent pane."""

    SESSION = "session"
    WINDOW = "window"
    SPLIT = "split"


class PatternMatch(BaseModel):
    """Result of a wait-for-pattern operation."""

    pane_id: str
    pattern: str
    matched_text: str
    line_number: int
    timestamp: datetime
