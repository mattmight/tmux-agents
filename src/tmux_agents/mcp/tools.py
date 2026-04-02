"""MCP tool definitions for tmux-agents.

All tools are registered on a shared FastMCP instance.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from tmux_agents.__about__ import __version__

MAX_CAPTURE_CHARS = 25000  # MCP SDK warns at 10k tokens; cap output


def register_tools(server: FastMCP, safe_mode: bool = False) -> None:
    """Register all MCP tools on the server.

    Args:
        safe_mode: If True, omit destructive tools (terminate_target).
    """

    @server.tool()
    def ping() -> dict:
        """Health check -- returns server version and status."""
        return {
            "status": "ok",
            "version": __version__,
            "server": "tmux-agents",
        }

    @server.tool()
    def list_inventory(socket_filter: str | None = None) -> dict:
        """List all tmux sessions, windows, and panes across discovered servers.

        Args:
            socket_filter: Optional socket name to limit discovery to.

        Returns:
            Full inventory snapshot with servers, sessions, windows, panes.
        """
        from tmux_agents.services.inventory_service import get_inventory

        inventory = get_inventory(socket_filter=socket_filter)
        return inventory.model_dump(mode="json")

    @server.tool()
    def list_agents(kind: str | None = None, socket_filter: str | None = None) -> dict:
        """List only panes with detected agents, optionally filtered by kind.

        Args:
            kind: Filter by agent kind (e.g. "claude"). None returns all agents.
            socket_filter: Optional socket name to limit discovery to.

        Returns:
            List of pane snapshots for detected agents.
        """
        from tmux_agents.services.inventory_service import all_panes, get_inventory

        inventory = get_inventory(socket_filter=socket_filter)
        panes = all_panes(inventory)
        agents = [
            p
            for p in panes
            if p.agent.detected_kind is not None
            and (kind is None or p.agent.detected_kind == kind)
        ]
        return {
            "agents": [a.model_dump(mode="json") for a in agents],
            "count": len(agents),
        }

    @server.tool()
    def inspect_target(pane_id: str, socket_filter: str | None = None) -> dict:
        """Inspect a specific tmux pane in detail.

        Args:
            pane_id: Tmux pane ID, e.g. "%12".
            socket_filter: Optional socket name to limit search to.

        Returns:
            Full 4-layer pane snapshot (ref, display, runtime, agent).
        """
        from tmux_agents.services.inventory_service import inspect_pane

        snap = inspect_pane(pane_id, socket_filter=socket_filter)
        return snap.model_dump(mode="json")

    @server.tool()
    def spawn_agent(
        agent_kind: str = "claude",
        session_name: str | None = None,
        claude_session_name: str | None = None,
        resume: str | None = None,
        continue_session: bool = False,
        worktree: str | None = None,
        cwd: str | None = None,
        target_session: str | None = None,
        split_direction: str | None = None,
    ) -> dict:
        """Spawn a managed agent session in a detached tmux session.

        Args:
            agent_kind: Agent type to spawn ("claude", "codex", or "gemini").
            session_name: Tmux session name. Auto-generated if omitted.
            claude_session_name: Claude -n <name> for the Claude session.
            resume: Resume a named Claude session (--resume).
            continue_session: Continue last Claude session (--continue).
            worktree: Git worktree name for Claude (--worktree).
            cwd: Working directory. Defaults to server's cwd.

        Returns:
            Full 4-layer pane snapshot of the newly created pane.
        """
        from tmux_agents.services.spawn_service import spawn_agent as _spawn

        snap = _spawn(
            agent_kind,
            session_name=session_name,
            claude_session_name=claude_session_name,
            resume=resume,
            continue_session=continue_session,
            worktree=worktree,
            cwd=cwd,
            transport="mcp",
            target_session=target_session,
            split_direction=split_direction,
        )
        return snap.model_dump(mode="json")

    # -- Destructive tools (omitted in safe mode) ---
    if not safe_mode:

        @server.tool()
        def terminate_target(session_id: str) -> dict:
            """Kill a tmux session by ID or name.

            Args:
                session_id: Session ID (e.g. "$3") or name to kill.

            Returns:
                Status dict with success flag.
            """
            from tmux_agents.services.spawn_service import kill_session

            ok = kill_session(session_id)
            return {"status": "ok" if ok else "error", "session": session_id}

    @server.tool()
    def capture_pane(
        pane_id: str,
        mode: str = "tail",
        lines: int = 200,
        start: int | None = None,
        end: int | None = None,
        screen: str = "auto",
    ) -> dict:
        """Capture output from a tmux pane.

        Args:
            pane_id: Tmux pane ID, e.g. "%12".
            mode: "tail" (last N lines), "history" (bounded slice), or "screen".
            lines: Number of lines for tail mode (default 200).
            start: Start line for history mode.
            end: End line for history mode.
            screen: Screen target: "auto", "primary", or "alternate".

        Returns:
            Capture result with content, line count, screen used, seq number.
        """
        from tmux_agents.models import CaptureMode, ScreenTarget
        from tmux_agents.services.capture_service import (
            capture_pane as _capture,
        )

        result = _capture(
            pane_id,
            mode=CaptureMode(mode),
            lines=lines,
            start=start,
            end=end,
            screen=ScreenTarget(screen),
        )
        data = result.model_dump(mode="json")
        # Bound output to stay within MCP token limits
        if len(data.get("content", "")) > MAX_CAPTURE_CHARS:
            data["content"] = data["content"][:MAX_CAPTURE_CHARS]
            data["truncated"] = True
        return data

    @server.tool()
    def read_pane_delta(
        pane_id: str,
        after_seq: int = 0,
        max_lines: int = 5000,
        screen: str = "auto",
    ) -> dict:
        """Read incremental output from a pane since a previous capture.

        Args:
            pane_id: Tmux pane ID, e.g. "%12".
            after_seq: Seq number from previous capture/delta. 0 for initial.
            max_lines: Maximum lines to return.
            screen: Screen target: "auto", "primary", or "alternate".

        Returns:
            Delta result with from_seq, to_seq, chunks, reset_required.
        """
        from tmux_agents.models import ScreenTarget
        from tmux_agents.services.capture_service import (
            read_pane_delta as _delta,
        )

        result = _delta(
            pane_id,
            after_seq=after_seq,
            max_lines=max_lines,
            screen=ScreenTarget(screen),
        )
        return result.model_dump(mode="json")

    @server.tool()
    def send_text(pane_id: str, text: str) -> dict:
        """Send literal text to a pane. No key interpretation.

        Use this to type text into a pane. "Enter" sends the five
        characters E-n-t-e-r, not a carriage return. Use send_keys
        for control keys.

        Args:
            pane_id: Tmux pane ID, e.g. "%12".
            text: The literal text to send.

        Returns:
            Status dict.
        """
        from tmux_agents.services.input_service import send_text as _send

        ok = _send(pane_id, text)
        return {"status": "ok" if ok else "error", "pane_id": pane_id}

    @server.tool()
    def send_keys(pane_id: str, keys: list[str]) -> dict:
        """Send key names to a pane. Keys are interpreted by tmux.

        Common keys: Enter, Escape, Tab, Space, BSpace,
        C-c, C-d, C-z, C-l, Up, Down, Left, Right.

        Args:
            pane_id: Tmux pane ID, e.g. "%12".
            keys: List of key names to send.

        Returns:
            Status dict.
        """
        from tmux_agents.services.input_service import send_keys as _send

        ok = _send(pane_id, *keys)
        return {"status": "ok" if ok else "error", "pane_id": pane_id, "keys": keys}

    @server.tool()
    def set_metadata(pane_id: str, agent_kind: str, profile: str | None = None) -> dict:
        """Tag a pane with agent metadata.

        Writes or updates the @tmux-agents.meta pane user option to mark
        a pane as a known agent kind.

        Args:
            pane_id: Tmux pane ID, e.g. "%12".
            agent_kind: Agent kind identifier, e.g. "claude".
            profile: Agent profile name. Defaults to agent_kind.

        Returns:
            Status dict.
        """
        from tmux_agents.services.input_service import tag_pane

        ok = tag_pane(pane_id, agent_kind=agent_kind, profile=profile)
        return {"status": "ok" if ok else "error", "pane_id": pane_id}

    @server.tool()
    def read_hook_state(pane_id: str) -> dict:
        """Read the current Claude hook lifecycle state from a managed pane.

        Returns the last hook event written by the Claude Code hooks bridge,
        or null if no hooks have fired.

        Args:
            pane_id: Tmux pane ID, e.g. "%12".

        Returns:
            Hook state dict with event, ts, and optional data fields.
        """
        from tmux_agents.services.inventory_service import inspect_pane
        from tmux_agents.tmux.command_runner import CommandRunner
        from tmux_agents.tmux.metadata_store import read_hook_state as _read

        snap = inspect_pane(pane_id)
        runner = CommandRunner(socket_path=snap.ref.server.socket_path)
        state = _read(runner, pane_id)
        return {"pane_id": pane_id, "state": state}

    @server.tool()
    def wait_for_pattern(
        pane_id: str,
        pattern: str,
        timeout_ms: int = 30000,
        poll_interval_ms: int = 500,
        screen: str = "auto",
    ) -> dict:
        """Wait for a regex pattern to appear in pane output.

        Polls pane capture until the pattern matches or timeout expires.

        Args:
            pane_id: Tmux pane ID, e.g. "%12".
            pattern: Regular expression to match.
            timeout_ms: Timeout in milliseconds (default 30000).
            poll_interval_ms: Poll interval in milliseconds (default 500).
            screen: Screen target: "auto", "primary", or "alternate".

        Returns:
            Match result with matched_text and line_number.
        """
        from tmux_agents.models import ScreenTarget
        from tmux_agents.services.capture_service import (
            wait_for_pattern as _wait,
        )

        match = _wait(
            pane_id,
            pattern,
            timeout_ms=timeout_ms,
            poll_interval_ms=poll_interval_ms,
            screen=ScreenTarget(screen),
        )
        return match.model_dump(mode="json")

    @server.tool()
    def send_channel_message(sender_pane_id: str, receiver_pane_id: str, message: str) -> dict:
        """Send a message from one managed pane to another.

        Uses tmux pane user options as the transport. The message
        is written to the receiver's @tmux-agents.channel option.

        Args:
            sender_pane_id: Sender pane ID, e.g. "%0".
            receiver_pane_id: Receiver pane ID, e.g. "%1".
            message: Message text.

        Returns:
            Status dict.
        """
        from tmux_agents.services.channel_service import send_message

        ok = send_message(sender_pane_id, receiver_pane_id, message)
        return {"status": "ok" if ok else "error", "receiver": receiver_pane_id}

    @server.tool()
    def read_channel_messages(pane_id: str) -> dict:
        """Read the current channel message for a pane.

        Args:
            pane_id: Pane ID to read channel from, e.g. "%0".

        Returns:
            Channel message envelope or null if no message.
        """
        from tmux_agents.services.channel_service import read_messages

        msg = read_messages(pane_id)
        return {"pane_id": pane_id, "message": msg}
