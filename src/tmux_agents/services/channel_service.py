"""Channel service: inter-pane messaging for coordinated agents.

Provides local messaging between managed tmux panes using the
`@tmux-agents.channel` pane user option as the transport. No daemon
needed — tmux is the message bus.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from tmux_agents.config import TmuxAgentsConfig
from tmux_agents.logging import get_logger
from tmux_agents.services.inventory_service import all_panes, get_inventory
from tmux_agents.tmux.command_runner import CommandRunner, get_runner
from tmux_agents.tmux.metadata_store import read_channel, write_channel

log = get_logger(__name__)


def _get_runner(
    pane_id: str,
    socket_path: str | None = None,
    config: TmuxAgentsConfig | None = None,
    host: str | None = None,
) -> CommandRunner:
    if socket_path:
        return get_runner(host, socket_path=socket_path)
    from tmux_agents.services.inventory_service import inspect_pane

    snap = inspect_pane(pane_id, config, host_filter=host)
    if snap.ref.server.host:
        return get_runner(snap.ref.server.host, socket_name=snap.ref.server.socket_name)
    return get_runner(None, socket_path=snap.ref.server.socket_path)


def send_message(
    sender_pane_id: str,
    receiver_pane_id: str,
    message: str,
    *,
    socket_path: str | None = None,
    config: TmuxAgentsConfig | None = None,
    host: str | None = None,
) -> bool:
    """Send a message from one pane to another via the channel option.

    Writes a JSON envelope with sender, message, and timestamp to the
    receiver's `@tmux-agents.channel` pane user option.

    The *host* parameter targets the receiver's host.

    Returns True on success.
    """
    runner = _get_runner(receiver_pane_id, socket_path, config, host)
    envelope: dict[str, Any] = {
        "from": sender_pane_id,
        "message": message,
        "ts": datetime.now(UTC).isoformat(),
    }
    ok = write_channel(runner, receiver_pane_id, envelope)
    if ok:
        log.info(
            "channel_message_sent",
            sender=sender_pane_id,
            receiver=receiver_pane_id,
        )
    return ok


def read_messages(
    pane_id: str,
    *,
    socket_path: str | None = None,
    config: TmuxAgentsConfig | None = None,
    host: str | None = None,
) -> dict[str, Any] | None:
    """Read the current channel message for a pane.

    Returns the message envelope dict, or None if no message.
    """
    runner = _get_runner(pane_id, socket_path, config, host)
    return read_channel(runner, pane_id)


def list_channel_peers(
    config: TmuxAgentsConfig | None = None,
) -> list[dict[str, Any]]:
    """List all managed panes available for channel messaging.

    Returns a list of dicts with pane_id, agent_kind, session_name,
    socket_path, and host for each managed pane.
    """
    inventory = get_inventory(config)
    panes = all_panes(inventory)
    peers: list[dict[str, Any]] = []
    for p in panes:
        if p.agent.managed and p.ref.pane:
            peers.append(
                {
                    "pane_id": p.ref.pane.id,
                    "agent_kind": p.agent.detected_kind,
                    "session_name": p.ref.session.name if p.ref.session else None,
                    "socket_path": p.ref.server.socket_path,
                    "host": p.ref.server.host,
                }
            )
    return peers
