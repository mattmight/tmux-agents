"""Inventory service: composing socket discovery and inventory collection.

Provides the primary entry points consumed by both CLI and MCP:
- get_inventory() — full inventory across all discovered servers
- inspect_target() — detailed snapshot for a single pane
"""

from __future__ import annotations

from tmux_agents.config import RemoteHostConfig, TmuxAgentsConfig
from tmux_agents.errors import (
    ErrorCode,
    ErrorContext,
    ErrorEnvelope,
    PaneNotFoundError,
)
from tmux_agents.logging import get_logger
from tmux_agents.models import InventorySnapshot, PaneSnapshot
from tmux_agents.refs import ServerRef
from tmux_agents.tmux.command_runner import CommandRunner, get_runner
from tmux_agents.tmux.inventory import collect_inventory
from tmux_agents.tmux.socket_discovery import (
    _discover_remote_sockets,
    discover_sockets,
)

log = get_logger(__name__)


def get_inventory(
    config: TmuxAgentsConfig | None = None,
    *,
    socket_filter: str | None = None,
    host_filter: str | None = None,
) -> InventorySnapshot:
    """Collect full inventory across all discovered live tmux servers.

    Args:
        config: Optional config with extra sockets and remote hosts.
        socket_filter: If set, only include servers matching this socket name.
        host_filter: If set, only include servers on this host.
            ``"local"`` means local-only; ``None`` means all hosts.

    Returns:
        InventorySnapshot with all servers, sessions, windows, and panes.
    """
    discovered = discover_sockets(config)

    # Ad-hoc remote discovery: if host_filter targets a host not already in
    # the discovered set (and not "local"), probe it on the fly.
    if host_filter and host_filter != "local":
        known_hosts = {ds.ref.host for ds in discovered if ds.ref.host}
        if host_filter not in known_hosts:
            try:
                adhoc = _discover_remote_sockets(RemoteHostConfig(alias=host_filter))
                discovered.extend(adhoc)
            except Exception:
                log.warning("adhoc_remote_discovery_failed", host=host_filter)

    pairs: list[tuple[ServerRef, CommandRunner]] = []
    for ds in discovered:
        if not ds.alive:
            continue
        if socket_filter and ds.ref.socket_name != socket_filter:
            continue
        if host_filter is not None:
            if host_filter == "local":
                if ds.ref.host is not None:
                    continue
            elif ds.ref.host != host_filter:
                continue
        # For remote hosts, use socket_name (-L) since the socket_path is a
        # synthetic placeholder — the real path lives on the remote filesystem.
        if ds.ref.host:
            runner = get_runner(ds.ref.host, socket_name=ds.ref.socket_name)
        else:
            runner = get_runner(None, socket_path=ds.ref.socket_path)
        pairs.append((ds.ref, runner))

    inventory = collect_inventory(pairs)

    from tmux_agents.services.detection_service import detect_inventory

    detect_inventory(inventory)

    return inventory


def inspect_pane(
    pane_id: str,
    config: TmuxAgentsConfig | None = None,
    *,
    socket_filter: str | None = None,
    host_filter: str | None = None,
) -> PaneSnapshot:
    """Find and return a detailed snapshot for a single pane by its ID.

    Args:
        pane_id: Tmux pane ID, e.g. "%12".
        config: Optional config with extra sockets.
        socket_filter: If set, only search servers matching this socket name.
        host_filter: If set, only search servers on this host.

    Raises:
        PaneNotFoundError: If the pane is not found in any live server.
    """
    inventory = get_inventory(config, socket_filter=socket_filter, host_filter=host_filter)
    for server in inventory.servers:
        for session in server.sessions:
            for window in session.windows:
                for pane in window.panes:
                    if pane.ref.pane and pane.ref.pane.id == pane_id:
                        return pane

    raise PaneNotFoundError(
        ErrorEnvelope(
            code=ErrorCode.PANE_NOT_FOUND,
            message=f"Pane {pane_id} not found in any live tmux server",
            context=ErrorContext(operation="inspect_pane"),
        )
    )


def all_panes(inventory: InventorySnapshot) -> list[PaneSnapshot]:
    """Flatten an inventory into a list of all pane snapshots."""
    panes: list[PaneSnapshot] = []
    for server in inventory.servers:
        for session in server.sessions:
            for window in session.windows:
                panes.extend(window.panes)
    return panes


def preview_pane(
    pane_id: str,
    config: TmuxAgentsConfig | None = None,
    *,
    socket_filter: str | None = None,
    host_filter: str | None = None,
    output_lines: int = 30,
) -> dict:
    """Return a rich preview of a pane: snapshot + recent output + process tree.

    Returns a dict combining the pane snapshot, captured output, and process info.
    """
    snap = inspect_pane(pane_id, config, socket_filter=socket_filter, host_filter=host_filter)
    host = snap.ref.server.host

    from tmux_agents.models import CaptureMode, ScreenTarget
    from tmux_agents.services.capture_service import capture_pane

    cap = capture_pane(
        pane_id,
        mode=CaptureMode.SCREEN,
        lines=output_lines,
        screen=ScreenTarget.AUTO,
        socket_path=snap.ref.server.socket_path,
        host=host,
    )

    tree = []
    if snap.runtime.pane_pid:
        if host:
            from tmux_agents.process.remote_inspector import get_remote_process_tree

            raw_tree = get_remote_process_tree(host, snap.runtime.pane_pid)
        else:
            from tmux_agents.process.inspector import get_process_tree

            raw_tree = get_process_tree(snap.runtime.pane_pid)
        tree = [{"pid": p.pid, "name": p.name} for p in raw_tree]

    return {
        "pane": snap.model_dump(mode="json"),
        "recent_output": cap.content,
        "output_lines": cap.line_count,
        "screen_used": cap.screen_used,
        "process_tree": tree,
    }
