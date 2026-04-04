"""Spawn service: launch managed agent sessions in tmux.

Creates detached tmux sessions running agent commands, writes pane
metadata, and returns an immediate snapshot of the new pane.
Supports claude, codex, and gemini agent kinds.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tmux_agents.errors import (
    ErrorCode,
    ErrorContext,
    ErrorEnvelope,
    TmuxAgentsError,
)
from tmux_agents.logging import get_logger
from tmux_agents.models import PaneSnapshot
from tmux_agents.services.inventory_service import inspect_pane
from tmux_agents.tmux.command_runner import CommandRunner, get_runner
from tmux_agents.tmux.metadata_store import write_pane_metadata
from tmux_agents.tmux.socket_discovery import _socket_dir

log = get_logger(__name__)

SUPPORTED_AGENTS = ("claude", "codex", "gemini")


# -- Public dispatch ---------------------------------------------------------


def spawn_agent(
    agent_kind: str,
    *,
    session_name: str | None = None,
    cwd: str | None = None,
    socket_name: str | None = None,
    socket_path: str | None = None,
    transport: str = "cli",
    host: str | None = None,
    # Orchestration
    target_session: str | None = None,
    split_direction: str | None = None,
    # Claude-specific
    claude_session_name: str | None = None,
    resume: str | None = None,
    continue_session: bool = False,
    worktree: str | None = None,
    extra_args: list[str] | None = None,
) -> PaneSnapshot:
    """Spawn a managed agent session. Dispatches to agent-specific logic.

    Args:
        host: SSH host alias for remote spawn. None for local.
        target_session: If set, spawn into this existing session as a new
            window instead of creating a new session.
        split_direction: If set ("horizontal" or "vertical"), split the first
            pane in target_session instead of creating a new window.
    """
    if agent_kind == "claude":
        return spawn_claude(
            session_name=session_name,
            claude_session_name=claude_session_name,
            resume=resume,
            continue_session=continue_session,
            worktree=worktree,
            cwd=cwd,
            socket_name=socket_name,
            socket_path=socket_path,
            extra_args=extra_args,
            transport=transport,
            host=host,
            target_session=target_session,
            split_direction=split_direction,
        )
    if agent_kind == "codex":
        return spawn_codex(
            session_name=session_name,
            cwd=cwd,
            socket_name=socket_name,
            socket_path=socket_path,
            extra_args=extra_args,
            transport=transport,
            host=host,
            target_session=target_session,
            split_direction=split_direction,
        )
    if agent_kind == "gemini":
        return spawn_gemini(
            session_name=session_name,
            cwd=cwd,
            socket_name=socket_name,
            socket_path=socket_path,
            extra_args=extra_args,
            transport=transport,
            host=host,
            target_session=target_session,
            split_direction=split_direction,
        )
    raise TmuxAgentsError(
        ErrorEnvelope(
            code=ErrorCode.INVALID_ARGUMENT,
            message=f"Unsupported agent kind '{agent_kind}'; "
            f"supported: {', '.join(SUPPORTED_AGENTS)}",
            details={"agent_kind": agent_kind, "supported": list(SUPPORTED_AGENTS)},
        )
    )


# -- Shared spawn logic ------------------------------------------------------


def _finalize_spawn(
    *,
    runner: CommandRunner,
    pane_id: str,
    agent_kind: str,
    session_name: str,
    work_dir: str,
    transport: str,
    socket_name: str | None,
    socket_path: str | None,
    host: str | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> PaneSnapshot:
    """Write metadata, inject env vars, and return snapshot for a spawned pane."""
    if agent_kind == "claude":
        socket_flag = ""
        if socket_path:
            socket_flag = f"-S {socket_path}"
        elif socket_name:
            socket_flag = f"-L {socket_name}"
        runner.run("set-environment", "-t", session_name, "TMUX_AGENTS_PANE_ID", pane_id)
        runner.run("set-environment", "-t", session_name, "TMUX_AGENTS_SOCKET", socket_flag)

    metadata: dict[str, Any] = {
        "schema_version": 1,
        "managed": True,
        "agent_kind": agent_kind,
        "profile": agent_kind,
        "created_at": datetime.now(UTC).isoformat(),
        "project_root": work_dir,
        "requested_session_name": session_name,
        "spawn_transport": transport,
        "hooks_injected": agent_kind == "claude",
    }
    if host:
        metadata["host"] = host
    if extra_metadata:
        metadata.update(extra_metadata)

    write_pane_metadata(runner, pane_id, metadata)
    log.info("agent_spawned", kind=agent_kind, session=session_name, pane=pane_id, host=host)

    resolved_socket = _resolve_socket_path(socket_name, socket_path, host=host)
    from tmux_agents.config import TmuxAgentsConfig

    config = TmuxAgentsConfig(extra_socket_paths=[resolved_socket] if resolved_socket else [])
    # Filter by socket name to avoid pane ID collisions across servers
    sock_name = Path(resolved_socket).name if resolved_socket else None
    return inspect_pane(pane_id, config, socket_filter=sock_name, host_filter=host)


def _spawn_detached(
    *,
    agent_kind: str,
    cmd: str,
    session_name: str,
    cwd: str | None,
    socket_name: str | None,
    socket_path: str | None,
    transport: str,
    host: str | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> PaneSnapshot:
    """Create a detached tmux session, write metadata, return snapshot."""
    work_dir = str(Path(cwd).resolve()) if cwd and not host else (cwd or str(Path.cwd()))
    runner = get_runner(host, socket_name=socket_name, socket_path=socket_path)

    result = runner.run("new-session", "-d", "-s", session_name, "-n", "main", "-c", work_dir, cmd)
    if not result.ok:
        raise TmuxAgentsError(
            ErrorEnvelope(
                code=ErrorCode.SPAWN_FAILED,
                message=f"Failed to create session '{session_name}': {' '.join(result.stderr)}",
                details={"session_name": session_name, "stderr": result.stderr, "host": host},
                context=ErrorContext(operation=f"spawn_{agent_kind}"),
            )
        )

    pane_result = runner.run("list-panes", "-t", session_name, "-F", "#{pane_id}", no_start=True)
    if not pane_result.ok or not pane_result.stdout:
        raise TmuxAgentsError(
            ErrorEnvelope(
                code=ErrorCode.SPAWN_FAILED,
                message=f"Session created but no pane in '{session_name}'",
                details={"session_name": session_name, "host": host},
                context=ErrorContext(operation=f"spawn_{agent_kind}"),
            )
        )

    pane_id = pane_result.stdout[0].strip()
    return _finalize_spawn(
        runner=runner,
        pane_id=pane_id,
        agent_kind=agent_kind,
        session_name=session_name,
        work_dir=work_dir,
        transport=transport,
        socket_name=socket_name,
        socket_path=socket_path,
        host=host,
        extra_metadata=extra_metadata,
    )


def _spawn_into_window(
    *,
    agent_kind: str,
    cmd: str,
    target_session: str,
    cwd: str | None,
    socket_name: str | None,
    socket_path: str | None,
    transport: str,
    host: str | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> PaneSnapshot:
    """Create a new window in an existing session."""
    work_dir = str(Path(cwd).resolve()) if cwd and not host else (cwd or str(Path.cwd()))
    runner = get_runner(host, socket_name=socket_name, socket_path=socket_path)

    result = runner.run(
        "new-window",
        "-t",
        target_session,
        "-n",
        "main",
        "-c",
        work_dir,
        "-P",
        "-F",
        "#{pane_id}",
        cmd,
    )
    if not result.ok or not result.stdout:
        raise TmuxAgentsError(
            ErrorEnvelope(
                code=ErrorCode.SPAWN_FAILED,
                message=f"Failed to create window in '{target_session}': "
                f"{' '.join(result.stderr)}",
                details={"target_session": target_session, "stderr": result.stderr, "host": host},
                context=ErrorContext(operation=f"spawn_{agent_kind}"),
            )
        )

    pane_id = result.stdout[0].strip()
    return _finalize_spawn(
        runner=runner,
        pane_id=pane_id,
        agent_kind=agent_kind,
        session_name=target_session,
        work_dir=work_dir,
        transport=transport,
        socket_name=socket_name,
        socket_path=socket_path,
        host=host,
        extra_metadata=extra_metadata,
    )


def _spawn_into_split(
    *,
    agent_kind: str,
    cmd: str,
    target_session: str,
    split_direction: str,
    cwd: str | None,
    socket_name: str | None,
    socket_path: str | None,
    transport: str,
    host: str | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> PaneSnapshot:
    """Split a pane in an existing session."""
    work_dir = str(Path(cwd).resolve()) if cwd and not host else (cwd or str(Path.cwd()))
    runner = get_runner(host, socket_name=socket_name, socket_path=socket_path)

    split_flag = "-h" if split_direction == "horizontal" else "-v"
    result = runner.run(
        "split-window",
        split_flag,
        "-t",
        target_session,
        "-c",
        work_dir,
        "-P",
        "-F",
        "#{pane_id}",
        cmd,
    )
    if not result.ok or not result.stdout:
        raise TmuxAgentsError(
            ErrorEnvelope(
                code=ErrorCode.SPAWN_FAILED,
                message=f"Failed to split in '{target_session}': {' '.join(result.stderr)}",
                details={
                    "target_session": target_session,
                    "split_direction": split_direction,
                    "stderr": result.stderr,
                    "host": host,
                },
                context=ErrorContext(operation=f"spawn_{agent_kind}"),
            )
        )

    pane_id = result.stdout[0].strip()
    return _finalize_spawn(
        runner=runner,
        pane_id=pane_id,
        agent_kind=agent_kind,
        session_name=target_session,
        work_dir=work_dir,
        transport=transport,
        socket_name=socket_name,
        socket_path=socket_path,
        host=host,
        extra_metadata=extra_metadata,
    )


# -- Claude ------------------------------------------------------------------


def _route_spawn(
    *,
    agent_kind: str,
    cmd: str,
    session_name: str,
    cwd: str | None,
    socket_name: str | None,
    socket_path: str | None,
    transport: str,
    host: str | None = None,
    target_session: str | None = None,
    split_direction: str | None = None,
    extra_metadata: dict[str, Any] | None = None,
) -> PaneSnapshot:
    """Route to the right spawn variant based on target_session/split_direction."""
    if target_session and split_direction:
        return _spawn_into_split(
            agent_kind=agent_kind,
            cmd=cmd,
            target_session=target_session,
            split_direction=split_direction,
            cwd=cwd,
            socket_name=socket_name,
            socket_path=socket_path,
            transport=transport,
            host=host,
            extra_metadata=extra_metadata,
        )
    if target_session:
        return _spawn_into_window(
            agent_kind=agent_kind,
            cmd=cmd,
            target_session=target_session,
            cwd=cwd,
            socket_name=socket_name,
            socket_path=socket_path,
            transport=transport,
            host=host,
            extra_metadata=extra_metadata,
        )
    return _spawn_detached(
        agent_kind=agent_kind,
        cmd=cmd,
        session_name=session_name,
        cwd=cwd,
        socket_name=socket_name,
        socket_path=socket_path,
        transport=transport,
        host=host,
        extra_metadata=extra_metadata,
    )


def spawn_claude(
    *,
    session_name: str | None = None,
    claude_session_name: str | None = None,
    resume: str | None = None,
    continue_session: bool = False,
    worktree: str | None = None,
    cwd: str | None = None,
    socket_name: str | None = None,
    socket_path: str | None = None,
    extra_args: list[str] | None = None,
    transport: str = "cli",
    host: str | None = None,
    target_session: str | None = None,
    split_direction: str | None = None,
) -> PaneSnapshot:
    """Spawn a managed Claude Code session."""
    cmd = _build_claude_command(
        claude_session_name=claude_session_name,
        resume=resume,
        continue_session=continue_session,
        worktree=worktree,
        extra_args=extra_args,
    )
    if not session_name:
        session_name = _generate_session_name(
            "claude",
            label=claude_session_name,
            worktree=worktree,
        )
    extra: dict[str, Any] = {}
    if worktree:
        extra["worktree_name"] = worktree
    if claude_session_name:
        extra["claude_session_name"] = claude_session_name
    if resume:
        extra["resume"] = resume

    return _route_spawn(
        agent_kind="claude",
        cmd=cmd,
        session_name=session_name,
        cwd=cwd,
        socket_name=socket_name,
        socket_path=socket_path,
        transport=transport,
        host=host,
        target_session=target_session,
        split_direction=split_direction,
        extra_metadata=extra,
    )


def spawn_codex(
    *,
    session_name: str | None = None,
    cwd: str | None = None,
    socket_name: str | None = None,
    socket_path: str | None = None,
    extra_args: list[str] | None = None,
    transport: str = "cli",
    host: str | None = None,
    target_session: str | None = None,
    split_direction: str | None = None,
) -> PaneSnapshot:
    """Spawn a managed Codex CLI session."""
    cmd = " ".join(["codex", *(extra_args or [])])
    if not session_name:
        session_name = _generate_session_name("codex")
    return _route_spawn(
        agent_kind="codex",
        cmd=cmd,
        session_name=session_name,
        cwd=cwd,
        socket_name=socket_name,
        socket_path=socket_path,
        transport=transport,
        host=host,
        target_session=target_session,
        split_direction=split_direction,
    )


def spawn_gemini(
    *,
    session_name: str | None = None,
    cwd: str | None = None,
    socket_name: str | None = None,
    socket_path: str | None = None,
    extra_args: list[str] | None = None,
    transport: str = "cli",
    host: str | None = None,
    target_session: str | None = None,
    split_direction: str | None = None,
) -> PaneSnapshot:
    """Spawn a managed Gemini CLI session."""
    cmd = " ".join(["gemini", *(extra_args or [])])
    if not session_name:
        session_name = _generate_session_name("gemini")
    return _route_spawn(
        agent_kind="gemini",
        cmd=cmd,
        session_name=session_name,
        cwd=cwd,
        socket_name=socket_name,
        socket_path=socket_path,
        transport=transport,
        host=host,
        target_session=target_session,
        split_direction=split_direction,
    )


# -- Kill --------------------------------------------------------------------


def kill_session(
    session_id: str,
    *,
    socket_name: str | None = None,
    socket_path: str | None = None,
    host: str | None = None,
) -> bool:
    """Kill a tmux session by ID or name."""
    runner = get_runner(host, socket_name=socket_name, socket_path=socket_path)
    result = runner.run("kill-session", "-t", session_id)
    return result.ok


# -- Helpers -----------------------------------------------------------------


def _build_claude_command(
    *,
    claude_session_name: str | None = None,
    resume: str | None = None,
    continue_session: bool = False,
    worktree: str | None = None,
    extra_args: list[str] | None = None,
) -> str:
    parts = ["claude"]
    if claude_session_name:
        parts.extend(["-n", claude_session_name])
    if resume:
        parts.extend(["--resume", resume])
    elif continue_session:
        parts.append("--continue")
    if worktree:
        parts.extend(["--worktree", worktree])
    if extra_args:
        parts.extend(extra_args)
    return " ".join(parts)


def _generate_session_name(
    kind: str,
    *,
    label: str | None = None,
    worktree: str | None = None,
) -> str:
    if label:
        return f"{kind}-{label}"
    if worktree:
        return f"{kind}-wt-{worktree}"
    ts = datetime.now(UTC).strftime("%H%M%S")
    return f"{kind}-{ts}"


def _resolve_socket_path(
    socket_name: str | None,
    socket_path: str | None,
    *,
    host: str | None = None,
) -> str | None:
    if socket_path:
        return socket_path
    if socket_name:
        if host:
            return f"/tmp/tmux-remote/{socket_name}"
        return str(_socket_dir() / socket_name)
    if host:
        return None
    default = _socket_dir() / "default"
    if default.exists():
        return str(default)
    return None
