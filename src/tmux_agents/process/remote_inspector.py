"""SSH-based remote process-tree inspection.

Replaces psutil for remote hosts by running ``ps -eo pid,ppid,comm`` over SSH
and parsing the output into ``ProcessInfo`` objects.
"""

from __future__ import annotations

from tmux_agents.logging import get_logger
from tmux_agents.process.inspector import ProcessInfo
from tmux_agents.ssh.runner import RemoteCommandRunner

log = get_logger(__name__)


def get_remote_process_tree(host: str, root_pid: int) -> list[ProcessInfo]:
    """Walk the process tree on a remote host rooted at *root_pid*.

    Uses ``ssh <host> ps -eo pid,ppid,comm`` to fetch all processes, then
    builds a parent→children map and walks descendants of *root_pid* in
    breadth-first order.

    Returns an empty list if the root PID is not found or SSH fails.
    """
    runner = RemoteCommandRunner(
        host,
        ssh_options=[
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=5",
        ],
    )

    try:
        # ps -eo pid,ppid,comm works on both Linux and macOS
        result = runner.run("ps", "-eo", "pid,ppid,comm", no_start=False)
    except Exception:
        log.debug("remote_ps_failed", host=host, root_pid=root_pid)
        return []

    if not result.ok:
        log.debug("remote_ps_nonzero", host=host, rc=result.returncode)
        return []

    return _parse_ps_output(result.stdout, root_pid)


def _parse_ps_output(lines: list[str], root_pid: int) -> list[ProcessInfo]:
    """Parse ``ps -eo pid,ppid,comm`` output and walk descendants of *root_pid*."""
    # Build mapping: pid -> (ppid, comm)
    pid_map: dict[int, tuple[int, str]] = {}
    # Build parent -> children mapping
    children_map: dict[int, list[int]] = {}

    for line in lines:
        parts = line.split(None, 2)
        if len(parts) < 3:
            continue
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
        except ValueError:
            continue  # skip header or malformed lines
        comm = parts[2].strip()
        pid_map[pid] = (ppid, comm)
        children_map.setdefault(ppid, []).append(pid)

    if root_pid not in pid_map:
        return []

    # BFS from root_pid
    result: list[ProcessInfo] = []
    queue = [root_pid]
    while queue:
        pid = queue.pop(0)
        entry = pid_map.get(pid)
        if entry is None:
            continue
        _, comm = entry
        result.append(ProcessInfo(pid=pid, name=comm))
        queue.extend(children_map.get(pid, []))

    return result
