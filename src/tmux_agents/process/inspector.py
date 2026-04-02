"""psutil-based process-tree inspection.

Walks the process tree rooted at a given PID and returns structured info
about each process. Used by agent detection to classify panes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import psutil

from tmux_agents.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class ProcessInfo:
    """Snapshot of a single process."""

    pid: int
    name: str
    exe: str | None = None
    cmdline: list[str] = field(default_factory=list)


def get_process_tree(root_pid: int) -> list[ProcessInfo]:
    """Walk the process tree rooted at root_pid and return all descendants.

    Returns a list starting with the root process, followed by its
    descendants in breadth-first order. Returns an empty list if the
    root process does not exist or is inaccessible.
    """
    try:
        root = psutil.Process(root_pid)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        log.debug("process_not_found", pid=root_pid)
        return []

    result: list[ProcessInfo] = []
    queue = [root]
    while queue:
        proc = queue.pop(0)
        try:
            info = ProcessInfo(
                pid=proc.pid,
                name=proc.name(),
                exe=_safe_exe(proc),
                cmdline=_safe_cmdline(proc),
            )
            result.append(info)
            queue.extend(proc.children(recursive=False))
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    return result


def find_in_tree(root_pid: int, name_pattern: str) -> list[ProcessInfo]:
    """Find processes in a tree whose name contains the given pattern.

    Args:
        root_pid: PID of the root process.
        name_pattern: Substring to match against process name (case-insensitive).

    Returns:
        List of matching ProcessInfo objects.
    """
    tree = get_process_tree(root_pid)
    pattern = name_pattern.lower()
    return [p for p in tree if pattern in p.name.lower() or _matches_cmdline(p, pattern)]


def _safe_exe(proc: psutil.Process) -> str | None:
    try:
        return proc.exe()
    except (psutil.AccessDenied, psutil.ZombieProcess, OSError):
        return None


def _safe_cmdline(proc: psutil.Process) -> list[str]:
    try:
        return proc.cmdline()
    except (psutil.AccessDenied, psutil.ZombieProcess, OSError):
        return []


def _matches_cmdline(info: ProcessInfo, pattern: str) -> bool:
    """Check if any cmdline argument contains the pattern."""
    return any(pattern in arg.lower() for arg in info.cmdline)
