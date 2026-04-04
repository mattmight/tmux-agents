"""Safe tmux command execution gateway.

All tmux interaction flows through this module. It handles:
- Binary discovery and version probing
- Socket targeting via -L (named) and -S (path) flags
- The -N flag on read-only probes to prevent accidental server creation
- Structured result/error returns
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import ClassVar

from tmux_agents.errors import (
    ErrorCode,
    ErrorContext,
    ErrorEnvelope,
    TmuxAgentsError,
    TmuxNotFoundError,
    TmuxVersionError,
)
from tmux_agents.logging import get_logger

log = get_logger(__name__)

# Minimum supported tmux version (control-mode format subscriptions, libtmux floor)
MIN_TMUX_VERSION = (3, 2)
MIN_TMUX_VERSION_STR = "3.2a"

# Pattern: "tmux 3.4" or "tmux 3.2a" or "tmux next-3.5"
_VERSION_RE = re.compile(r"(?:next-)?(\d+)\.(\d+)([a-z])?")


@dataclass(frozen=True)
class TmuxVersion:
    """Parsed tmux version."""

    major: int
    minor: int
    patch: str = ""
    raw: str = ""

    def __ge__(self, other: tuple[int, int]) -> bool:
        return (self.major, self.minor) >= other

    def __lt__(self, other: tuple[int, int]) -> bool:
        return (self.major, self.minor) < other

    def __str__(self) -> str:
        return self.raw or f"{self.major}.{self.minor}{self.patch}"


@dataclass(frozen=True)
class TmuxResult:
    """Result of a tmux command execution."""

    stdout: list[str]
    stderr: list[str]
    returncode: int
    cmd: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def output(self) -> str:
        """Joined stdout lines."""
        return "\n".join(self.stdout)


class CommandRunner:
    """Gateway for executing tmux commands against a specific socket.

    Each CommandRunner instance targets one tmux server identified by
    either a socket name (-L) or socket path (-S).
    """

    _BINARY_CACHE: ClassVar[str | None] = None

    def __init__(
        self,
        *,
        socket_name: str | None = None,
        socket_path: str | None = None,
        tmux_bin: str | None = None,
    ) -> None:
        if socket_name and socket_path:
            raise ValueError("Specify socket_name (-L) or socket_path (-S), not both")
        self.socket_name = socket_name
        self.socket_path = socket_path
        self._tmux_bin = tmux_bin

    @property
    def tmux_bin(self) -> str:
        """Resolve the tmux binary path, caching the result."""
        if self._tmux_bin:
            return self._tmux_bin
        if CommandRunner._BINARY_CACHE:
            return CommandRunner._BINARY_CACHE
        found = shutil.which("tmux")
        if not found:
            raise TmuxNotFoundError(
                ErrorEnvelope(
                    code=ErrorCode.TMUX_NOT_FOUND,
                    message="tmux binary not found on PATH",
                )
            )
        CommandRunner._BINARY_CACHE = found
        return found

    def _base_args(self, *, no_start: bool = False) -> list[str]:
        """Build the common prefix: tmux [-N] [-L name | -S path]."""
        args = [self.tmux_bin]
        if no_start:
            args.append("-N")
        if self.socket_name:
            args.extend(["-L", self.socket_name])
        elif self.socket_path:
            args.extend(["-S", self.socket_path])
        return args

    def run(
        self,
        tmux_cmd: str,
        *args: str,
        no_start: bool = False,
        timeout: float = 10.0,
    ) -> TmuxResult:
        """Execute a tmux command and return the result.

        Args:
            tmux_cmd: The tmux subcommand, e.g. "list-sessions".
            args: Additional arguments to the subcommand.
            no_start: If True, pass -N to prevent starting a new server.
            timeout: Seconds before timing out.
        """
        full_cmd = [*self._base_args(no_start=no_start), tmux_cmd, *args]
        log.debug("tmux_exec", cmd=full_cmd)
        try:
            proc = subprocess.run(
                full_cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except FileNotFoundError as exc:
            raise TmuxNotFoundError(
                ErrorEnvelope(
                    code=ErrorCode.TMUX_NOT_FOUND,
                    message=f"tmux binary not found: {self.tmux_bin}",
                )
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise TmuxAgentsError(
                ErrorEnvelope(
                    code=ErrorCode.TIMEOUT,
                    message=f"tmux command timed out after {timeout}s",
                    details={"cmd": full_cmd},
                    context=ErrorContext(operation=tmux_cmd),
                )
            ) from exc

        stdout_lines = proc.stdout.splitlines() if proc.stdout else []
        stderr_lines = proc.stderr.splitlines() if proc.stderr else []

        result = TmuxResult(
            stdout=stdout_lines,
            stderr=stderr_lines,
            returncode=proc.returncode,
            cmd=full_cmd,
        )
        if not result.ok:
            log.debug("tmux_cmd_failed", cmd=full_cmd, rc=proc.returncode, stderr=stderr_lines)
        return result

    def is_server_alive(self) -> bool:
        """Check if the targeted tmux server is running, without starting it."""
        result = self.run("list-sessions", "-F", "#{session_id}", no_start=True)
        return result.ok


def get_runner(
    host: str | None = None,
    *,
    socket_name: str | None = None,
    socket_path: str | None = None,
) -> CommandRunner:
    """Factory: return a local ``CommandRunner`` or a ``RemoteCommandRunner``.

    When *host* is ``None`` or ``"local"`` the runner targets the local
    machine.  Otherwise it wraps commands in SSH to the given host alias.
    """
    if host is None or host == "local":
        return CommandRunner(socket_name=socket_name, socket_path=socket_path)

    from tmux_agents.ssh.runner import RemoteCommandRunner

    return RemoteCommandRunner(host, socket_name=socket_name, socket_path=socket_path)  # type: ignore[return-value]


def parse_version(raw: str) -> TmuxVersion:
    """Parse a tmux version string like 'tmux 3.4' or 'tmux 3.2a'."""
    m = _VERSION_RE.search(raw)
    if not m:
        raise TmuxVersionError(
            ErrorEnvelope(
                code=ErrorCode.TMUX_VERSION_UNSUPPORTED,
                message=f"Cannot parse tmux version from: {raw!r}",
                details={"raw_version": raw},
            )
        )
    return TmuxVersion(
        major=int(m.group(1)),
        minor=int(m.group(2)),
        patch=m.group(3) or "",
        raw=raw.strip(),
    )


def check_version(runner: CommandRunner | None = None) -> TmuxVersion:
    """Probe the tmux version and enforce the minimum requirement.

    Returns the parsed version on success; raises TmuxVersionError otherwise.
    """
    r = runner or CommandRunner()
    result = r.run("-V")  # "tmux -V" is not a subcommand but works via run
    if not result.ok:
        raise TmuxVersionError(
            ErrorEnvelope(
                code=ErrorCode.TMUX_VERSION_UNSUPPORTED,
                message="Failed to query tmux version",
                details={"stderr": result.stderr},
            )
        )
    raw = result.output
    version = parse_version(raw)
    if version < MIN_TMUX_VERSION:
        raise TmuxVersionError(
            ErrorEnvelope(
                code=ErrorCode.TMUX_VERSION_UNSUPPORTED,
                message=(
                    f"tmux {version} is below minimum {MIN_TMUX_VERSION_STR}; please upgrade tmux"
                ),
                details={"detected": str(version), "minimum": MIN_TMUX_VERSION_STR},
            )
        )
    log.info("tmux_version_ok", version=str(version))
    return version


def check_pane_alive(runner: CommandRunner, pane_id: str) -> None:
    """Raise PaneDeadError if the target pane is dead.

    Uses tmux display-message to check #{pane_dead} without a full inventory.
    """
    from tmux_agents.errors import (
        ErrorCode,
        ErrorContext,
        ErrorEnvelope,
        PaneDeadError,
    )

    result = runner.run("display-message", "-p", "-t", pane_id, "#{pane_dead}", no_start=True)
    if result.ok and result.stdout and result.stdout[0].strip() == "1":
        raise PaneDeadError(
            ErrorEnvelope(
                code=ErrorCode.PANE_DEAD,
                message=f"Pane {pane_id} is dead; use 'tmux-agents list' to find live panes",
                details={"pane_id": pane_id},
                context=ErrorContext(operation="check_pane_alive"),
            )
        )
