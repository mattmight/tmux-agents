"""Three-layer tmux socket discovery.

Layer 1: Default socket — always probe.
Layer 2: Scan the user's tmux socket directory (TMUX_TMPDIR or /tmp/tmux-UID/).
Layer 3: Honor explicit configured socket paths and names.

All probes use -N to avoid starting servers that don't exist.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from tmux_agents.config import TmuxAgentsConfig
from tmux_agents.logging import get_logger
from tmux_agents.refs import ServerRef
from tmux_agents.tmux.command_runner import CommandRunner

log = get_logger(__name__)


@dataclass(frozen=True)
class DiscoveredSocket:
    """A discovered tmux socket with liveness status."""

    ref: ServerRef
    alive: bool
    source: str  # "default", "scan", or "config"


def _socket_dir() -> Path:
    """Return the tmux socket directory for the current user.

    tmux uses TMUX_TMPDIR if set, otherwise /tmp, then appends tmux-UID.
    """
    base = os.environ.get("TMUX_TMPDIR") or "/tmp"
    return Path(base) / f"tmux-{os.geteuid()}"


def _probe_socket(
    *,
    socket_name: str | None = None,
    socket_path: str | None = None,
    source: str,
) -> DiscoveredSocket | None:
    """Probe a single socket and return a DiscoveredSocket if the socket file exists.

    Uses -N to prevent starting a new server.
    """
    if socket_path:
        path = Path(socket_path)
        if not path.exists():
            log.debug("socket_file_missing", path=str(path), source=source)
            return None
        name = path.name
        runner = CommandRunner(socket_path=socket_path)
    elif socket_name:
        # For named sockets, build the expected path to check file existence
        expected = _socket_dir() / socket_name
        if not expected.exists():
            log.debug("socket_file_missing", path=str(expected), source=source)
            return None
        path = expected
        name = socket_name
        runner = CommandRunner(socket_name=socket_name)
    else:
        return None

    alive = runner.is_server_alive()
    ref = ServerRef(socket_path=str(path), socket_name=name)
    log.debug("socket_probed", socket=str(path), alive=alive, source=source)
    return DiscoveredSocket(ref=ref, alive=alive, source=source)


def discover_sockets(
    config: TmuxAgentsConfig | None = None,
    *,
    include_dead: bool = False,
) -> list[DiscoveredSocket]:
    """Discover tmux sockets across all three layers.

    Args:
        config: Optional config providing extra_socket_paths and extra_socket_names.
        include_dead: If True, include sockets whose files exist but server is not alive.

    Returns:
        List of DiscoveredSocket objects, deduplicated by resolved socket path.
    """
    seen_paths: set[str] = set()
    results: list[DiscoveredSocket] = []

    def _add(ds: DiscoveredSocket | None) -> None:
        if ds is None:
            return
        if ds.ref.socket_path in seen_paths:
            return
        seen_paths.add(ds.ref.socket_path)
        if ds.alive or include_dead:
            results.append(ds)

    # Layer 1: default socket
    _add(_probe_socket(socket_name="default", source="default"))

    # Layer 2: scan socket directory
    sock_dir = _socket_dir()
    if sock_dir.is_dir():
        for entry in sorted(sock_dir.iterdir()):
            if entry.name == "default":
                continue  # already probed in layer 1
            if entry.is_socket() or entry.exists():
                _add(_probe_socket(socket_path=str(entry), source="scan"))

    # Layer 3: configured sockets
    cfg = config or TmuxAgentsConfig()
    for path_str in cfg.extra_socket_paths:
        _add(_probe_socket(socket_path=path_str, source="config"))
    for name in cfg.extra_socket_names:
        if name == "default":
            continue  # already probed
        _add(_probe_socket(socket_name=name, source="config"))

    log.info(
        "socket_discovery_complete",
        total=len(results),
        alive=sum(1 for d in results if d.alive),
    )
    return results
