"""Batched tmux inventory collection.

Collects sessions, windows, and panes per socket using a small number of
`list-sessions`, `list-windows -a`, and `list-panes -a` calls with custom
-F format strings. Builds normalized parent-child graph of snapshot objects.
"""

from __future__ import annotations

from datetime import UTC, datetime

from tmux_agents.logging import get_logger
from tmux_agents.models import (
    AgentInfo,
    InventorySnapshot,
    PaneDisplay,
    PaneRuntime,
    PaneSnapshot,
    ServerDisplay,
    ServerSnapshot,
    SessionDisplay,
    SessionSnapshot,
    WindowDisplay,
    WindowSnapshot,
)
from tmux_agents.refs import (
    PaneRef,
    ServerRef,
    SessionRef,
    TargetRef,
    WindowRef,
)
from tmux_agents.tmux.command_runner import CommandRunner

log = get_logger(__name__)

# Field separator for tmux -F format strings (unlikely to appear in values)
_SEP = "\t"

# -- Format strings ----------------------------------------------------------
# Each format returns tab-separated fields, one line per entity.

_SESSION_FMT = _SEP.join(
    [
        "#{session_id}",
        "#{session_name}",
        "#{session_windows}",
        "#{session_attached}",
    ]
)

_WINDOW_FMT = _SEP.join(
    [
        "#{window_id}",
        "#{window_name}",
        "#{window_index}",
        "#{window_panes}",
        "#{session_id}",
    ]
)

_PANE_FMT = _SEP.join(
    [
        "#{pane_id}",
        "#{pane_index}",
        "#{pane_pid}",
        "#{pane_current_command}",
        "#{pane_current_path}",
        "#{pane_dead}",
        "#{pane_width}",
        "#{pane_height}",
        "#{pane_title}",
        "#{window_id}",
        "#{session_id}",
    ]
)


def _int_or(val: str, default: int = 0) -> int:
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def _bool_flag(val: str) -> bool:
    return val in ("1", "true", "yes")


def collect_server_inventory(
    runner: CommandRunner,
    server_ref: ServerRef,
) -> ServerSnapshot:
    """Collect full inventory for a single tmux server.

    Issues three batched commands: list-sessions, list-windows -a, list-panes -a.
    Parses their output and assembles the snapshot graph.
    """
    # -- Collect raw data ----------------------------------------------------
    sess_result = runner.run("list-sessions", "-F", _SESSION_FMT, no_start=True)
    win_result = runner.run("list-windows", "-a", "-F", _WINDOW_FMT, no_start=True)
    pane_result = runner.run("list-panes", "-a", "-F", _PANE_FMT, no_start=True)

    if not sess_result.ok:
        log.warning("inventory_sessions_failed", stderr=sess_result.stderr)
        return ServerSnapshot(
            ref=TargetRef(server=server_ref),
            display=ServerDisplay(socket_name=server_ref.socket_name, session_count=0),
        )

    # -- Parse sessions ------------------------------------------------------
    sessions: dict[str, dict] = {}
    for line in sess_result.stdout:
        parts = line.split(_SEP)
        if len(parts) < 4:
            continue
        sid, sname, swindows, sattached = parts[0], parts[1], parts[2], parts[3]
        sessions[sid] = {
            "ref": SessionRef(id=sid, name=sname),
            "display": SessionDisplay(
                name=sname,
                window_count=_int_or(swindows),
                attached=_bool_flag(sattached),
            ),
            "windows": {},
        }

    # -- Parse windows -------------------------------------------------------
    if win_result.ok:
        for line in win_result.stdout:
            parts = line.split(_SEP)
            if len(parts) < 5:
                continue
            wid, wname, widx, wpanes, sid = (
                parts[0],
                parts[1],
                parts[2],
                parts[3],
                parts[4],
            )
            if sid not in sessions:
                continue
            sessions[sid]["windows"][wid] = {
                "ref": WindowRef(id=wid, name=wname, index=_int_or(widx)),
                "display": WindowDisplay(
                    name=wname,
                    index=_int_or(widx),
                    pane_count=_int_or(wpanes),
                ),
                "panes": [],
            }

    # -- Parse panes ---------------------------------------------------------
    if pane_result.ok:
        for line in pane_result.stdout:
            parts = line.split(_SEP)
            if len(parts) < 11:
                continue
            (
                pid_str,
                pidx,
                ppid,
                pcmd,
                ppath,
                pdead,
                pwidth,
                pheight,
                ptitle,
                wid,
                sid,
            ) = (
                parts[0],
                parts[1],
                parts[2],
                parts[3],
                parts[4],
                parts[5],
                parts[6],
                parts[7],
                parts[8],
                parts[9],
                parts[10],
            )
            if sid not in sessions:
                continue
            win_dict = sessions[sid]["windows"]
            if wid not in win_dict:
                continue
            pane_ref = PaneRef(id=pid_str, index=_int_or(pidx))
            target = TargetRef(
                server=server_ref,
                session=sessions[sid]["ref"],
                window=win_dict[wid]["ref"],
                pane=pane_ref,
            )
            pane_snap = PaneSnapshot(
                ref=target,
                display=PaneDisplay(index=_int_or(pidx), title=ptitle),
                runtime=PaneRuntime(
                    pane_pid=_int_or(ppid) or None,
                    pane_current_command=pcmd or None,
                    pane_current_path=ppath or None,
                    pane_dead=_bool_flag(pdead),
                    pane_width=_int_or(pwidth) or None,
                    pane_height=_int_or(pheight) or None,
                ),
                agent=AgentInfo(),
            )
            win_dict[wid]["panes"].append(pane_snap)

    # -- Assemble snapshot graph ---------------------------------------------
    session_snapshots: list[SessionSnapshot] = []
    for _sid, sdata in sessions.items():
        window_snapshots: list[WindowSnapshot] = []
        for _wid, wdata in sdata["windows"].items():
            window_snapshots.append(
                WindowSnapshot(
                    ref=TargetRef(
                        server=server_ref,
                        session=sdata["ref"],
                        window=wdata["ref"],
                    ),
                    display=wdata["display"],
                    panes=wdata["panes"],
                )
            )
        session_snapshots.append(
            SessionSnapshot(
                ref=TargetRef(server=server_ref, session=sdata["ref"]),
                display=sdata["display"],
                windows=window_snapshots,
            )
        )

    server_snap = ServerSnapshot(
        ref=TargetRef(server=server_ref),
        display=ServerDisplay(
            socket_name=server_ref.socket_name,
            session_count=len(session_snapshots),
        ),
        sessions=session_snapshots,
    )
    log.info(
        "inventory_collected",
        socket=server_ref.socket_name,
        sessions=len(session_snapshots),
    )
    return server_snap


def collect_inventory(
    sockets: list[tuple[ServerRef, CommandRunner]],
) -> InventorySnapshot:
    """Collect inventory across multiple tmux servers.

    Args:
        sockets: List of (ServerRef, CommandRunner) pairs for live servers.

    Returns:
        InventorySnapshot with all servers, timestamped.
    """
    servers = [collect_server_inventory(runner, ref) for ref, runner in sockets]
    return InventorySnapshot(servers=servers, timestamp=datetime.now(UTC))
