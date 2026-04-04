"""Input service: controlled interaction with tmux panes.

Two deliberately separate paths:
- send_text: literal text via `tmux send-keys -l` (no key interpretation)
- send_keys: key names via `tmux send-keys` (Enter, C-c, etc. interpreted)

This split avoids the ambiguity bugs seen in existing tmux-MCP tools where
mixing literal text and control-key semantics in one command caused real issues.
"""

from __future__ import annotations

from tmux_agents.config import TmuxAgentsConfig
from tmux_agents.logging import get_logger
from tmux_agents.tmux.command_runner import CommandRunner, check_pane_alive, get_runner
from tmux_agents.tmux.metadata_store import read_pane_metadata, write_pane_metadata

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


def send_text(
    pane_id: str,
    text: str,
    *,
    socket_path: str | None = None,
    config: TmuxAgentsConfig | None = None,
    host: str | None = None,
) -> bool:
    """Send literal text to a pane. No key interpretation."""
    runner = _get_runner(pane_id, socket_path, config, host)
    check_pane_alive(runner, pane_id)
    result = runner.run("send-keys", "-l", "-t", pane_id, text)
    if not result.ok:
        log.warning("send_text_failed", pane_id=pane_id, stderr=result.stderr)
    return result.ok


def send_keys(
    pane_id: str,
    *keys: str,
    socket_path: str | None = None,
    config: TmuxAgentsConfig | None = None,
    host: str | None = None,
) -> bool:
    """Send key names to a pane. Keys are interpreted by tmux."""
    if not keys:
        return True
    runner = _get_runner(pane_id, socket_path, config, host)
    check_pane_alive(runner, pane_id)
    result = runner.run("send-keys", "-t", pane_id, *keys)
    if not result.ok:
        log.warning("send_keys_failed", pane_id=pane_id, stderr=result.stderr)
    return result.ok


def tag_pane(
    pane_id: str,
    *,
    agent_kind: str,
    profile: str | None = None,
    socket_path: str | None = None,
    config: TmuxAgentsConfig | None = None,
    host: str | None = None,
) -> bool:
    """Tag a pane with agent metadata, marking it as a known agent."""
    runner = _get_runner(pane_id, socket_path, config, host)
    check_pane_alive(runner, pane_id)

    existing = read_pane_metadata(runner, pane_id) or {}
    existing["agent_kind"] = agent_kind
    existing["profile"] = profile or agent_kind
    existing["managed"] = existing.get("managed", False)
    existing.setdefault("schema_version", 1)

    ok = write_pane_metadata(runner, pane_id, existing)
    if ok:
        log.info("pane_tagged", pane_id=pane_id, agent_kind=agent_kind)
    return ok
