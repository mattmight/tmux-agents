"""Agent detection service.

Runs three-pass detection on pane snapshots to populate the agent layer:
  Pass 1: Explicit metadata (managed panes with @tmux-agents.meta)
  Pass 2: Process-tree classification via registered profiles
  Pass 3: Tmux hints (weak evidence fallback)
"""

from __future__ import annotations

from tmux_agents.agents.registry import get_profiles
from tmux_agents.logging import get_logger
from tmux_agents.models import (
    AgentInfo,
    Confidence,
    DetectionSource,
    InventorySnapshot,
    PaneSnapshot,
)
from tmux_agents.process.inspector import get_process_tree
from tmux_agents.tmux.command_runner import CommandRunner
from tmux_agents.tmux.metadata_store import read_hook_state, read_pane_metadata

log = get_logger(__name__)


def detect_pane(
    pane: PaneSnapshot,
    runner: CommandRunner | None = None,
) -> AgentInfo:
    """Run three-pass detection on a single pane and return AgentInfo.

    Args:
        pane: Pane snapshot with populated runtime fields.
        runner: CommandRunner for reading metadata. If None, skip metadata pass.
    """
    if pane.runtime.pane_dead:
        return AgentInfo()

    # Pass 1: explicit metadata
    if runner and pane.ref.pane:
        info = _detect_from_metadata(runner, pane.ref.pane.id)
        if info is not None:
            return info

    # Pass 2: process-tree classification
    if pane.runtime.pane_pid:
        info = _detect_from_process_tree(pane.runtime.pane_pid)
        if info is not None:
            return info

    # Pass 3: tmux hints (weak)
    info = _detect_from_hints(pane)
    if info is not None:
        return info

    return AgentInfo()


def detect_inventory(
    inventory: InventorySnapshot,
) -> InventorySnapshot:
    """Enrich all panes in an inventory with detection results.

    Creates CommandRunners per server for metadata reads.
    Mutates pane.agent in place for efficiency.
    """
    for server in inventory.servers:
        runner = CommandRunner(socket_path=server.ref.server.socket_path)
        for session in server.sessions:
            for window in session.windows:
                for pane in window.panes:
                    pane.agent = detect_pane(pane, runner)
    return inventory


def _detect_from_metadata(
    runner: CommandRunner,
    pane_id: str,
) -> AgentInfo | None:
    """Pass 1: check for explicit @tmux-agents.meta user option."""
    meta = read_pane_metadata(runner, pane_id)
    if meta is None:
        return None

    if not meta.get("managed"):
        return None

    agent_kind = meta.get("agent_kind")
    if not agent_kind:
        return None

    hook = read_hook_state(runner, pane_id)

    return AgentInfo(
        detected_kind=agent_kind,
        confidence=Confidence.STRONG,
        source=DetectionSource.EXPLICIT,
        managed=True,
        profile=meta.get("profile", agent_kind),
        evidence={"metadata": meta},
        hook_state=hook,
    )


def _detect_from_process_tree(pane_pid: int) -> AgentInfo | None:
    """Pass 2: walk process tree and match against registered profiles."""
    tree = get_process_tree(pane_pid)
    if not tree:
        return None

    for profile in get_profiles():
        info = profile.match_process_tree(tree)
        if info is not None:
            return info

    return None


def _detect_from_hints(pane: PaneSnapshot) -> AgentInfo | None:
    """Pass 3: try tmux hints (weakest signal)."""
    session_name = pane.ref.session.name if pane.ref.session else None
    window_name = pane.ref.window.name if pane.ref.window else None

    for profile in get_profiles():
        info = profile.match_tmux_hints(
            current_command=pane.runtime.pane_current_command,
            current_path=pane.runtime.pane_current_path,
            session_name=session_name,
            window_name=window_name,
        )
        if info is not None:
            return info

    return None
