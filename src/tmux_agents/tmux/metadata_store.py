"""Pane metadata storage via tmux user options.

Stores and retrieves JSON blobs in pane-scoped user options:
- `@tmux-agents.meta` — stable managed-pane identity (written at spawn)
- `@tmux-agents.hook` — volatile hook lifecycle state (updated by Claude hooks)
"""

from __future__ import annotations

import json
from typing import Any

from tmux_agents.logging import get_logger
from tmux_agents.tmux.command_runner import CommandRunner

log = get_logger(__name__)

METADATA_OPTION = "@tmux-agents.meta"


def read_pane_metadata(
    runner: CommandRunner,
    pane_id: str,
) -> dict[str, Any] | None:
    """Read the @tmux-agents.meta user option from a pane.

    Returns the parsed JSON dict, or None if not set or unparseable.
    """
    result = runner.run(
        "show-options",
        "-p",
        "-v",
        "-t",
        pane_id,
        METADATA_OPTION,
        no_start=True,
    )
    if not result.ok or not result.stdout:
        return None

    raw = result.output.strip()
    if not raw:
        return None

    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, TypeError):
        log.debug("metadata_parse_error", pane_id=pane_id, raw=raw)

    return None


def write_pane_metadata(
    runner: CommandRunner,
    pane_id: str,
    metadata: dict[str, Any],
) -> bool:
    """Write metadata as JSON to the @tmux-agents.meta pane user option.

    Returns True on success.
    """
    blob = json.dumps(metadata, separators=(",", ":"))
    result = runner.run(
        "set-option",
        "-p",
        "-t",
        pane_id,
        METADATA_OPTION,
        blob,
    )
    if not result.ok:
        log.warning("metadata_write_failed", pane_id=pane_id, stderr=result.stderr)
    return result.ok


# -- Hook state (volatile lifecycle events) ----------------------------------

HOOK_OPTION = "@tmux-agents.hook"


def read_hook_state(
    runner: CommandRunner,
    pane_id: str,
) -> dict[str, Any] | None:
    """Read the @tmux-agents.hook user option from a pane.

    Returns the parsed JSON dict of the last hook event, or None.
    """
    result = runner.run("show-options", "-p", "-v", "-t", pane_id, HOOK_OPTION, no_start=True)
    if not result.ok or not result.stdout:
        return None

    raw = result.output.strip()
    if not raw:
        return None

    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, TypeError):
        log.debug("hook_state_parse_error", pane_id=pane_id, raw=raw)

    return None


def write_hook_state(
    runner: CommandRunner,
    pane_id: str,
    state: dict[str, Any],
) -> bool:
    """Write hook state JSON to the @tmux-agents.hook pane user option."""
    blob = json.dumps(state, separators=(",", ":"))
    result = runner.run("set-option", "-p", "-t", pane_id, HOOK_OPTION, blob)
    if not result.ok:
        log.warning("hook_state_write_failed", pane_id=pane_id, stderr=result.stderr)
    return result.ok


# -- Channel messages (inter-pane coordination) ------------------------------

CHANNEL_OPTION = "@tmux-agents.channel"


def read_channel(
    runner: CommandRunner,
    pane_id: str,
) -> dict[str, Any] | None:
    """Read the @tmux-agents.channel user option from a pane."""
    result = runner.run("show-options", "-p", "-v", "-t", pane_id, CHANNEL_OPTION, no_start=True)
    if not result.ok or not result.stdout:
        return None
    raw = result.output.strip()
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, TypeError):
        log.debug("channel_parse_error", pane_id=pane_id, raw=raw)
    return None


def write_channel(
    runner: CommandRunner,
    pane_id: str,
    message: dict[str, Any],
) -> bool:
    """Write a channel message to the @tmux-agents.channel pane user option."""
    blob = json.dumps(message, separators=(",", ":"))
    result = runner.run("set-option", "-p", "-t", pane_id, CHANNEL_OPTION, blob)
    if not result.ok:
        log.warning("channel_write_failed", pane_id=pane_id, stderr=result.stderr)
    return result.ok
