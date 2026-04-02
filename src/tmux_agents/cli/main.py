"""CLI entrypoint for tmux-agents.

Provides the `tmux-agents` command with subcommands for inspection,
interaction, and MCP server management.
"""

from __future__ import annotations

import sys

import click

from tmux_agents.__about__ import __version__
from tmux_agents.config import load_config
from tmux_agents.logging import configure_logging


@click.group()
@click.version_option(version=__version__, prog_name="tmux-agents")
@click.option(
    "--json",
    "output_json",
    is_flag=True,
    default=False,
    help="Emit JSON output instead of human-friendly text.",
)
@click.option(
    "--log-level",
    default="WARNING",
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"], case_sensitive=False),
    help="Set log level (logs always go to stderr).",
)
@click.pass_context
def cli(ctx: click.Context, output_json: bool, log_level: str) -> None:
    """tmux-agents: discover, monitor, and orchestrate AI agents in tmux."""
    ctx.ensure_object(dict)
    ctx.obj["output_json"] = output_json
    ctx.obj["config"] = load_config()
    configure_logging(level=log_level, fmt="json" if output_json else "console")


@cli.group()
def mcp() -> None:
    """MCP server commands."""


@mcp.command("serve-stdio")
@click.pass_context
def serve_stdio(ctx: click.Context) -> None:
    """Start the MCP server over stdio transport."""
    configure_logging(level="INFO", fmt="json")

    from tmux_agents.mcp.stdio import run_stdio_server

    run_stdio_server()


@mcp.command("serve-http")
@click.option("--host", default="127.0.0.1", help="Bind address.")
@click.option("--port", default=8766, type=int, help="Bind port.")
@click.option("--no-safe-mode", is_flag=True, help="Enable destructive tools.")
@click.option("--auth-token", default=None, help="Bearer token (or set TMUX_AGENTS_AUTH_TOKEN).")
@click.pass_context
def serve_http(
    ctx: click.Context, host: str, port: int, no_safe_mode: bool, auth_token: str | None
) -> None:
    """Start the MCP server over Streamable HTTP transport."""
    configure_logging(level="INFO", fmt="json")

    from tmux_agents.mcp.http import run_http_server

    run_http_server(host=host, port=port, safe_mode=not no_safe_mode, auth_token=auth_token)


# -- Hook commands -----------------------------------------------------------


@cli.group()
def hooks() -> None:
    """Claude Code hook integration commands."""


@hooks.command("generate")
@click.pass_context
def hooks_generate(ctx: click.Context) -> None:
    """Generate Claude Code hooks config for tmux-agents integration."""
    import json

    from tmux_agents.hooks.generator import generate_hooks_config

    config = generate_hooks_config()
    click.echo(json.dumps(config, indent=2))


@hooks.command("status")
@click.option("--pane", required=True, help="Pane ID, e.g. %%12.")
@click.option("--socket", default=None, help="Tmux socket name.")
@click.pass_context
def hooks_status(ctx: click.Context, pane: str, socket: str | None) -> None:
    """Read current hook state from a managed Claude pane."""
    from tmux_agents.tmux.command_runner import CommandRunner
    from tmux_agents.tmux.metadata_store import read_hook_state

    output_json = ctx.obj["output_json"]

    try:
        # Resolve the runner via inspect
        from tmux_agents.services.inventory_service import inspect_pane

        snap = inspect_pane(pane, ctx.obj["config"])
        runner = CommandRunner(socket_path=snap.ref.server.socket_path)
        state = read_hook_state(runner, pane)
    except Exception as exc:
        _render_error(exc)
        sys.exit(1)

    if output_json:
        import json

        click.echo(json.dumps(state))
    elif state:
        click.echo(f"Event: {state.get('event', 'unknown')}")
        click.echo(f"Time:  {state.get('ts', 'unknown')}")
        if state.get("data"):
            click.echo(f"Data:  {state['data']}")
    else:
        click.echo(f"No hook state set for pane {pane}.")


# -- Channel commands --------------------------------------------------------


@cli.group()
def channels() -> None:
    """Inter-pane messaging between managed agents."""


@channels.command("send")
@click.option("--from", "sender", required=True, help="Sender pane ID.")
@click.option("--to", "receiver", required=True, help="Receiver pane ID.")
@click.option("--message", required=True, help="Message text.")
@click.pass_context
def channels_send(ctx: click.Context, sender: str, receiver: str, message: str) -> None:
    """Send a message from one pane to another."""
    from tmux_agents.services.channel_service import send_message

    try:
        ok = send_message(sender, receiver, message)
    except Exception as exc:
        _render_error(exc)
        sys.exit(1)
    if ok:
        click.echo(f"Message sent from {sender} to {receiver}.")
    else:
        click.echo("Error: failed to send message", err=True)
        sys.exit(1)


@channels.command("read")
@click.option("--pane", required=True, help="Pane ID to read channel from.")
@click.pass_context
def channels_read(ctx: click.Context, pane: str) -> None:
    """Read the current channel message for a pane."""
    import json

    from tmux_agents.services.channel_service import read_messages

    output_json = ctx.obj["output_json"]

    try:
        msg = read_messages(pane)
    except Exception as exc:
        _render_error(exc)
        sys.exit(1)

    if output_json:
        click.echo(json.dumps(msg))
    elif msg:
        click.echo(f"From:    {msg.get('from', 'unknown')}")
        click.echo(f"Time:    {msg.get('ts', 'unknown')}")
        click.echo(f"Message: {msg.get('message', '')}")
    else:
        click.echo(f"No channel message for pane {pane}.")


@channels.command("peers")
@click.pass_context
def channels_peers(ctx: click.Context) -> None:
    """List managed panes available for channel messaging."""
    import json

    from tmux_agents.services.channel_service import list_channel_peers

    output_json = ctx.obj["output_json"]

    try:
        peers = list_channel_peers(ctx.obj["config"])
    except Exception as exc:
        _render_error(exc)
        sys.exit(1)

    if output_json:
        click.echo(json.dumps(peers, indent=2))
    elif peers:
        for p in peers:
            click.echo(f"  {p['pane_id']}  {p['agent_kind'] or '?'}  {p['session_name'] or ''}")
    else:
        click.echo("No managed panes found.")


# -- Inventory commands ------------------------------------------------------


@cli.command("list")
@click.option("--kind", default=None, help="Filter by agent kind (e.g. claude).")
@click.option("--socket", default=None, help="Filter by socket name.")
@click.pass_context
def list_cmd(ctx: click.Context, kind: str | None, socket: str | None) -> None:
    """List tmux sessions, windows, and panes."""
    from tmux_agents.services.inventory_service import get_inventory

    config = ctx.obj["config"]
    output_json = ctx.obj["output_json"]

    try:
        inventory = get_inventory(config, socket_filter=socket)
    except Exception as exc:
        _render_error(exc)
        sys.exit(1)

    if kind:
        # Filter panes by agent kind (M3+ will populate agent.detected_kind)
        for server in inventory.servers:
            for session in server.sessions:
                for window in session.windows:
                    window.panes = [p for p in window.panes if p.agent.detected_kind == kind]

    if output_json:
        click.echo(inventory.model_dump_json(indent=2))
    else:
        _render_inventory_human(inventory)


@cli.command("inspect")
@click.option("--pane", required=True, help="Pane ID, e.g. %%12.")
@click.option("--socket", default=None, help="Filter by socket name.")
@click.option("--preview", is_flag=True, help="Include recent output and process tree.")
@click.pass_context
def inspect_cmd(ctx: click.Context, pane: str, socket: str | None, preview: bool) -> None:
    """Inspect a specific pane in detail."""
    config = ctx.obj["config"]
    output_json = ctx.obj["output_json"]

    try:
        if preview:
            import json as _json

            from tmux_agents.services.inventory_service import preview_pane

            data = preview_pane(pane, config, socket_filter=socket)
            if output_json:
                click.echo(_json.dumps(data, indent=2))
            else:
                _render_pane_human_from_dict(data)
        else:
            from tmux_agents.services.inventory_service import inspect_pane

            snap = inspect_pane(pane, config, socket_filter=socket)
            if output_json:
                click.echo(snap.model_dump_json(indent=2))
            else:
                _render_pane_human(snap)
    except Exception as exc:
        _render_error(exc)
        sys.exit(1)


# -- Spawn commands ----------------------------------------------------------


@cli.command("spawn")
@click.argument("agent_kind", default="claude")
@click.option("--session", "session_name", default=None, help="Tmux session name.")
@click.option("-n", "claude_session_name", default=None, help="Claude session name (-n).")
@click.option("--resume", default=None, help="Resume a named Claude session.")
@click.option("--continue", "continue_session", is_flag=True, help="Continue last Claude session.")
@click.option("--worktree", default=None, help="Claude --worktree name.")
@click.option("--cwd", default=None, help="Working directory (default: current).")
@click.option("--socket", default=None, help="Tmux socket name.")
@click.option("--target-session", default=None, help="Spawn into existing session as new window.")
@click.option(
    "--split",
    "split_direction",
    default=None,
    type=click.Choice(["horizontal", "vertical"]),
    help="Split pane in target session.",
)
@click.pass_context
def spawn_cmd(
    ctx: click.Context,
    agent_kind: str,
    session_name: str | None,
    claude_session_name: str | None,
    resume: str | None,
    continue_session: bool,
    worktree: str | None,
    cwd: str | None,
    socket: str | None,
    target_session: str | None,
    split_direction: str | None,
) -> None:
    """Spawn a managed agent session (claude, codex, or gemini)."""
    from tmux_agents.services.spawn_service import spawn_agent

    output_json = ctx.obj["output_json"]

    try:
        snap = spawn_agent(
            agent_kind,
            session_name=session_name,
            claude_session_name=claude_session_name,
            resume=resume,
            continue_session=continue_session,
            worktree=worktree,
            cwd=cwd,
            socket_name=socket,
            transport="cli",
            target_session=target_session,
            split_direction=split_direction,
        )
    except Exception as exc:
        _render_error(exc)
        sys.exit(1)

    if output_json:
        click.echo(snap.model_dump_json(indent=2))
    else:
        _render_pane_human(snap)


@cli.command("kill")
@click.option("--session", required=True, help="Session ID or name to kill.")
@click.option("--socket", default=None, help="Tmux socket name.")
@click.pass_context
def kill_cmd(ctx: click.Context, session: str, socket: str | None) -> None:
    """Kill a tmux session."""
    from tmux_agents.services.spawn_service import kill_session

    ok = kill_session(session, socket_name=socket)
    if ok:
        click.echo(f"Session '{session}' killed.")
    else:
        click.echo(f"Error: failed to kill session '{session}'", err=True)
        sys.exit(1)


# -- Capture commands --------------------------------------------------------


@cli.command("capture")
@click.option("--pane", required=True, help="Pane ID, e.g. %%12.")
@click.option("--lines", default=200, type=int, help="Lines to capture (tail mode).")
@click.option(
    "--mode",
    default="tail",
    type=click.Choice(["tail", "history", "screen"], case_sensitive=False),
)
@click.option("--start", default=None, type=int, help="Start line (history mode).")
@click.option("--end", default=None, type=int, help="End line (history mode).")
@click.option(
    "--screen",
    default="auto",
    type=click.Choice(["auto", "primary", "alternate"], case_sensitive=False),
)
@click.option("--socket", default=None, help="Tmux socket name.")
@click.pass_context
def capture_cmd(
    ctx: click.Context,
    pane: str,
    lines: int,
    mode: str,
    start: int | None,
    end: int | None,
    screen: str,
    socket: str | None,
) -> None:
    """Capture output from a tmux pane."""
    from tmux_agents.models import CaptureMode, ScreenTarget
    from tmux_agents.services.capture_service import capture_pane

    output_json = ctx.obj["output_json"]

    try:
        result = capture_pane(
            pane,
            mode=CaptureMode(mode),
            lines=lines,
            start=start,
            end=end,
            screen=ScreenTarget(screen),
        )
    except Exception as exc:
        _render_error(exc)
        sys.exit(1)

    if output_json:
        click.echo(result.model_dump_json(indent=2))
    else:
        click.echo(
            f"Pane {result.pane_id}  mode={result.mode}  "
            f"screen={result.screen_used}  lines={result.line_count}  "
            f"seq={result.seq}"
        )
        click.echo("---")
        click.echo(result.content)


@cli.command("delta")
@click.option("--pane", required=True, help="Pane ID, e.g. %%12.")
@click.option("--after-seq", default=0, type=int, help="Seq from previous capture.")
@click.option("--max-lines", default=5000, type=int, help="Max lines to return.")
@click.option(
    "--screen",
    default="auto",
    type=click.Choice(["auto", "primary", "alternate"], case_sensitive=False),
)
@click.option("--socket", default=None, help="Tmux socket name.")
@click.pass_context
def delta_cmd(
    ctx: click.Context,
    pane: str,
    after_seq: int,
    max_lines: int,
    screen: str,
    socket: str | None,
) -> None:
    """Read incremental output from a pane since a previous capture."""
    from tmux_agents.models import ScreenTarget
    from tmux_agents.services.capture_service import read_pane_delta

    output_json = ctx.obj["output_json"]

    try:
        result = read_pane_delta(
            pane,
            after_seq=after_seq,
            max_lines=max_lines,
            screen=ScreenTarget(screen),
        )
    except Exception as exc:
        _render_error(exc)
        sys.exit(1)

    if output_json:
        click.echo(result.model_dump_json(indent=2))
    else:
        reset = " [RESET]" if result.reset_required else ""
        click.echo(
            f"Delta {result.from_seq}→{result.to_seq}  new_lines={result.total_new_lines}{reset}"
        )
        for chunk in result.chunks:
            if chunk.content:
                click.echo(chunk.content)


@cli.command("wait")
@click.option("--pane", required=True, help="Pane ID, e.g. %%12.")
@click.option("--pattern", required=True, help="Regex pattern to wait for.")
@click.option("--timeout", default=30000, type=int, help="Timeout in ms (default 30000).")
@click.option("--poll", default=500, type=int, help="Poll interval in ms (default 500).")
@click.option(
    "--screen",
    default="auto",
    type=click.Choice(["auto", "primary", "alternate"], case_sensitive=False),
)
@click.pass_context
def wait_cmd(
    ctx: click.Context,
    pane: str,
    pattern: str,
    timeout: int,
    poll: int,
    screen: str,
) -> None:
    """Wait for a regex pattern to appear in pane output."""
    from tmux_agents.models import ScreenTarget
    from tmux_agents.services.capture_service import wait_for_pattern

    output_json = ctx.obj["output_json"]

    try:
        match = wait_for_pattern(
            pane,
            pattern,
            timeout_ms=timeout,
            poll_interval_ms=poll,
            screen=ScreenTarget(screen),
        )
    except Exception as exc:
        _render_error(exc)
        sys.exit(1)

    if output_json:
        click.echo(match.model_dump_json(indent=2))
    else:
        click.echo(f"Matched: {match.matched_text!r} (line {match.line_number})")


# -- Input commands ----------------------------------------------------------


@cli.command("send-text")
@click.option("--pane", required=True, help="Pane ID, e.g. %%12.")
@click.option("--text", required=True, help="Literal text to send (no key interpretation).")
@click.option("--socket", default=None, help="Tmux socket name.")
@click.pass_context
def send_text_cmd(ctx: click.Context, pane: str, text: str, socket: str | None) -> None:
    """Send literal text to a pane (no key interpretation)."""
    from tmux_agents.services.input_service import send_text

    try:
        ok = send_text(pane, text)
    except Exception as exc:
        _render_error(exc)
        sys.exit(1)
    if ok:
        click.echo(f"Text sent to {pane}.")
    else:
        click.echo(f"Error: failed to send text to {pane}", err=True)
        sys.exit(1)


@cli.command("send-keys")
@click.option("--pane", required=True, help="Pane ID, e.g. %%12.")
@click.argument("keys", nargs=-1, required=True)
@click.option("--socket", default=None, help="Tmux socket name.")
@click.pass_context
def send_keys_cmd(
    ctx: click.Context, pane: str, keys: tuple[str, ...], socket: str | None
) -> None:
    """Send key names to a pane (Enter, C-c, Escape, etc.)."""
    from tmux_agents.services.input_service import send_keys

    try:
        ok = send_keys(pane, *keys)
    except Exception as exc:
        _render_error(exc)
        sys.exit(1)
    if ok:
        click.echo(f"Keys sent to {pane}: {' '.join(keys)}")
    else:
        click.echo(f"Error: failed to send keys to {pane}", err=True)
        sys.exit(1)


@cli.command("tag")
@click.option("--pane", required=True, help="Pane ID, e.g. %%12.")
@click.option("--agent-kind", required=True, help="Agent kind, e.g. claude.")
@click.option("--socket", default=None, help="Tmux socket name.")
@click.pass_context
def tag_cmd(ctx: click.Context, pane: str, agent_kind: str, socket: str | None) -> None:
    """Tag a pane with agent metadata."""
    from tmux_agents.services.input_service import tag_pane

    try:
        ok = tag_pane(pane, agent_kind=agent_kind)
    except Exception as exc:
        _render_error(exc)
        sys.exit(1)
    if ok:
        click.echo(f"Pane {pane} tagged as {agent_kind}.")
    else:
        click.echo(f"Error: failed to tag {pane}", err=True)
        sys.exit(1)


# -- Error rendering ---------------------------------------------------------


def _render_error(exc: Exception) -> None:
    """Render an error to stderr with structured details if available."""
    from tmux_agents.errors import TmuxAgentsError

    if isinstance(exc, TmuxAgentsError):
        msg = f"Error [{exc.envelope.code}]: {exc.envelope.message}"
    else:
        msg = f"Error: {exc}"
    click.echo(msg, err=True)


# -- Human output renderers --------------------------------------------------


def _render_inventory_human(inventory):  # type: ignore[no-untyped-def]
    """Render inventory as a human-readable tree."""
    from tmux_agents.models import InventorySnapshot

    inv: InventorySnapshot = inventory
    if not inv.servers:
        click.echo("No live tmux servers found.")
        return

    for server in inv.servers:
        click.echo(
            click.style(f"Server: {server.ref.server.socket_name}", bold=True)
            + f"  ({server.ref.server.socket_path})"
        )
        if not server.sessions:
            click.echo("  (no sessions)")
            continue
        for session in server.sessions:
            attached = " [attached]" if session.display.attached else ""
            click.echo(
                f"  Session {session.ref.session.id} "  # type: ignore[union-attr]
                + click.style(session.display.name, fg="cyan")
                + f"  ({session.display.window_count} windows){attached}"
            )
            for window in session.windows:
                click.echo(
                    f"    Window {window.ref.window.id} "  # type: ignore[union-attr]
                    + f"{window.display.name}:{window.display.index}"
                    + f"  ({window.display.pane_count} panes)"
                )
                for pane in window.panes:
                    dead = " [dead]" if pane.runtime.pane_dead else ""
                    cmd = pane.runtime.pane_current_command or "?"
                    path = pane.runtime.pane_current_path or ""
                    agent = ""
                    if pane.agent.detected_kind:
                        agent = click.style(f" [{pane.agent.detected_kind}]", fg="green")
                    click.echo(
                        f"      Pane {pane.ref.pane.id} "  # type: ignore[union-attr]
                        + f"{cmd}"
                        + (f"  {path}" if path else "")
                        + agent
                        + dead
                    )


def _render_pane_human(snap):  # type: ignore[no-untyped-def]
    """Render a single pane snapshot in human-friendly format."""
    from tmux_agents.models import PaneSnapshot

    p: PaneSnapshot = snap
    ref = p.ref
    click.echo(click.style("Pane Details", bold=True))
    click.echo(f"  Pane ID:    {ref.pane.id}")  # type: ignore[union-attr]
    click.echo(f"  Server:     {ref.server.socket_name} ({ref.server.socket_path})")
    if ref.session:
        click.echo(f"  Session:    {ref.session.id} ({ref.session.name})")
    if ref.window:
        click.echo(f"  Window:     {ref.window.id} ({ref.window.name}:{ref.window.index})")
    click.echo(f"  PID:        {p.runtime.pane_pid or 'N/A'}")
    click.echo(f"  Command:    {p.runtime.pane_current_command or 'N/A'}")
    click.echo(f"  Path:       {p.runtime.pane_current_path or 'N/A'}")
    click.echo(f"  Size:       {p.runtime.pane_width}x{p.runtime.pane_height}")
    click.echo(f"  Dead:       {p.runtime.pane_dead}")
    if p.agent.detected_kind:
        click.echo(f"  Agent:      {p.agent.detected_kind} ({p.agent.confidence})")


def _render_pane_human_from_dict(data: dict) -> None:  # type: ignore[type-arg]
    """Render a preview dict (from preview_pane) in human-friendly format."""
    from tmux_agents.models import PaneSnapshot

    snap = PaneSnapshot.model_validate(data["pane"])
    _render_pane_human(snap)

    if data.get("process_tree"):
        click.echo(click.style("Process Tree", bold=True))
        for proc in data["process_tree"]:
            click.echo(f"  {proc['pid']}  {proc['name']}")

    if data.get("recent_output"):
        click.echo(click.style(f"Recent Output ({data['output_lines']} lines)", bold=True))
        click.echo(data["recent_output"])
