"""Hook bundle generator for Claude Code integration.

Generates a hooks configuration dict for `.claude/settings.json` that
pipes Claude lifecycle events into tmux pane user options via shell commands.
No persistent sidecar needed — hooks write directly to tmux.
"""

from __future__ import annotations

from typing import Any

_GUARD = '[ -n "$TMUX_AGENTS_PANE_ID" ]'
_SET_CMD = 'tmux $TMUX_AGENTS_SOCKET set-option -p -t "$TMUX_AGENTS_PANE_ID" @tmux-agents.hook'
_TS = "$(date -u +%Y-%m-%dT%H:%M:%SZ)"


def _hook_cmd(event: str, extra_json: str = "") -> str:
    """Build a hook shell command that writes event JSON to the pane option."""
    json_body = f'{{"event":"{event}","ts":"\'"{_TS}"\'"{extra_json}}}'
    return f"{_GUARD} && {_SET_CMD} '{json_body}'"


def generate_hooks_config() -> dict[str, Any]:
    """Generate Claude Code hooks configuration for tmux-agents integration.

    Returns a dict suitable for merging into `.claude/settings.json`.
    Each hook writes its event as JSON into the `@tmux-agents.hook` tmux
    pane user option. The `TMUX_AGENTS_PANE_ID` and `TMUX_AGENTS_SOCKET`
    environment variables must be set (tmux-agents spawn does this automatically).
    """
    return {
        "hooks": {
            "Notification": [
                {
                    "matcher": {},
                    "hooks": [
                        {
                            "type": "command",
                            "command": _hook_cmd("Notification"),
                        }
                    ],
                }
            ],
            "SubagentStart": [
                {
                    "matcher": {},
                    "hooks": [
                        {
                            "type": "command",
                            "command": _hook_cmd("SubagentStart"),
                        }
                    ],
                }
            ],
            "SubagentStop": [
                {
                    "matcher": {},
                    "hooks": [
                        {
                            "type": "command",
                            "command": _hook_cmd("SubagentStop"),
                        }
                    ],
                }
            ],
            "Stop": [
                {
                    "matcher": {},
                    "hooks": [
                        {
                            "type": "command",
                            "command": _hook_cmd("Stop"),
                        }
                    ],
                }
            ],
        }
    }
