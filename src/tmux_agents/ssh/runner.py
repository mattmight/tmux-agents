"""Remote tmux command execution over SSH.

``RemoteCommandRunner`` mirrors the ``CommandRunner`` interface but wraps
every tmux invocation in an SSH call to the target host.  SSH ControlMaster
multiplexing amortises connection overhead across commands to the same host.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import ClassVar

from tmux_agents.errors import (
    ErrorCode,
    ErrorContext,
    ErrorEnvelope,
    SSHError,
    TmuxAgentsError,
)
from tmux_agents.logging import get_logger
from tmux_agents.tmux.command_runner import TmuxResult

log = get_logger(__name__)

# Default SSH options for non-interactive, multiplexed connections.
# ControlPath uses /tmp with a short hash to stay within macOS ~104-char
# socket path limit (e.g. mattmight@might.net:2224 would overflow ~/.ssh/).
_DEFAULT_SSH_OPTIONS: list[str] = [
    "-o",
    "BatchMode=yes",
    "-o",
    "ConnectTimeout=5",
    "-o",
    "ControlMaster=auto",
    "-o",
    "ControlPath=/tmp/ta-ssh-%C",
    "-o",
    "ControlPersist=60s",
]


class RemoteCommandRunner:
    """Execute tmux commands on a remote host via SSH.

    Shares the same public interface as ``CommandRunner`` so the two
    are interchangeable through the ``get_runner()`` factory.
    """

    _SSH_BINARY_CACHE: ClassVar[str | None] = None

    def __init__(
        self,
        host: str,
        *,
        socket_name: str | None = None,
        socket_path: str | None = None,
        ssh_bin: str | None = None,
        ssh_options: list[str] | None = None,
    ) -> None:
        if socket_name and socket_path:
            raise ValueError("Specify socket_name (-L) or socket_path (-S), not both")
        self.host = host
        self.socket_name = socket_name
        self.socket_path = socket_path
        self._ssh_bin = ssh_bin
        self._ssh_options = ssh_options if ssh_options is not None else list(_DEFAULT_SSH_OPTIONS)

    @property
    def ssh_bin(self) -> str:
        if self._ssh_bin:
            return self._ssh_bin
        if RemoteCommandRunner._SSH_BINARY_CACHE:
            return RemoteCommandRunner._SSH_BINARY_CACHE
        found = shutil.which("ssh")
        if not found:
            raise SSHError(
                ErrorEnvelope(
                    code=ErrorCode.SSH_CONNECTION_FAILED,
                    message="ssh binary not found on PATH",
                )
            )
        RemoteCommandRunner._SSH_BINARY_CACHE = found
        return found

    def _tmux_args(self, *, no_start: bool = False) -> list[str]:
        """Build the remote tmux command portion (without ssh prefix)."""
        args = ["tmux"]
        if no_start:
            args.append("-N")
        if self.socket_name:
            args.extend(["-L", self.socket_name])
        elif self.socket_path:
            args.extend(["-S", self.socket_path])
        return args

    def _ssh_prefix(self) -> list[str]:
        """Build ``ssh [options] <host>``."""
        return [self.ssh_bin, *self._ssh_options, self.host]

    def run(
        self,
        tmux_cmd: str,
        *args: str,
        no_start: bool = False,
        timeout: float = 15.0,
    ) -> TmuxResult:
        """Execute a tmux command on the remote host via SSH.

        Commands are wrapped in a login shell (``$SHELL -lc``) so that the
        remote user's PATH is fully initialised.  This is necessary because
        non-interactive SSH sessions receive a minimal PATH that often
        excludes directories like ``/opt/homebrew/bin``.
        """
        remote_tmux = [*self._tmux_args(no_start=no_start), tmux_cmd, *args]
        # Quote each arg for safe embedding in a shell string, then wrap
        # in the user's login shell so PATH is correct.
        import shlex

        inner_cmd = shlex.join(remote_tmux)
        # Wrap in $SHELL -lc so the remote login shell sets up PATH.
        # SSH concatenates its args into a single string passed to the
        # remote shell, so $SHELL expands on the remote side.
        escaped_inner = inner_cmd.replace("'", "'\\''")
        full_cmd = [*self._ssh_prefix(), "$SHELL -lc " + f"'{escaped_inner}'"]
        log.debug("ssh_tmux_exec", cmd=full_cmd, host=self.host)

        try:
            proc = subprocess.run(
                full_cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except FileNotFoundError as exc:
            raise SSHError(
                ErrorEnvelope(
                    code=ErrorCode.SSH_CONNECTION_FAILED,
                    message=f"ssh binary not found: {self.ssh_bin}",
                    details={"host": self.host},
                )
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise SSHError(
                ErrorEnvelope(
                    code=ErrorCode.SSH_TIMEOUT,
                    message=f"SSH command timed out after {timeout}s",
                    details={"host": self.host, "cmd": full_cmd},
                    context=ErrorContext(operation=tmux_cmd),
                )
            ) from exc

        stdout_lines = proc.stdout.splitlines() if proc.stdout else []
        stderr_lines = proc.stderr.splitlines() if proc.stderr else []

        # Classify SSH-level failures from stderr.
        if proc.returncode != 0:
            stderr_text = proc.stderr or ""
            self._raise_if_ssh_error(stderr_text, full_cmd, tmux_cmd)

        result = TmuxResult(
            stdout=stdout_lines,
            stderr=stderr_lines,
            returncode=proc.returncode,
            cmd=full_cmd,
        )
        if not result.ok:
            log.debug(
                "ssh_tmux_cmd_failed",
                cmd=full_cmd,
                rc=proc.returncode,
                stderr=stderr_lines,
                host=self.host,
            )
        return result

    def _raise_if_ssh_error(self, stderr_text: str, full_cmd: list[str], operation: str) -> None:
        """Inspect stderr for SSH-specific failures and raise ``SSHError``."""
        lower = stderr_text.lower()
        if "permission denied" in lower:
            raise SSHError(
                ErrorEnvelope(
                    code=ErrorCode.SSH_AUTH_FAILED,
                    message=f"SSH authentication failed for host '{self.host}'",
                    details={"host": self.host, "stderr": stderr_text.strip()},
                    context=ErrorContext(operation=operation),
                )
            )
        if "could not resolve hostname" in lower:
            raise SSHError(
                ErrorEnvelope(
                    code=ErrorCode.SSH_HOST_UNKNOWN,
                    message=f"Unknown SSH host '{self.host}'",
                    details={"host": self.host, "stderr": stderr_text.strip()},
                    context=ErrorContext(operation=operation),
                )
            )
        if "connection refused" in lower or "connection timed out" in lower:
            raise SSHError(
                ErrorEnvelope(
                    code=ErrorCode.SSH_CONNECTION_FAILED,
                    message=f"SSH connection failed to host '{self.host}'",
                    details={"host": self.host, "stderr": stderr_text.strip()},
                    context=ErrorContext(operation=operation),
                )
            )
        # Not an SSH error — let the caller handle the non-zero return code
        # as a normal tmux failure.

    def is_server_alive(self) -> bool:
        """Check if the remote tmux server is running."""
        try:
            result = self.run("list-sessions", "-F", "#{session_id}", no_start=True)
            return result.ok
        except (SSHError, TmuxAgentsError):
            return False


def ssh_reachable(host: str, ssh_bin: str | None = None, timeout: float = 5.0) -> bool:
    """Quick health check: can we SSH to *host* and get a response?"""
    bin_path = ssh_bin or shutil.which("ssh")
    if not bin_path:
        return False
    try:
        proc = subprocess.run(
            [
                bin_path,
                "-o",
                "BatchMode=yes",
                "-o",
                f"ConnectTimeout={int(timeout)}",
                host,
                "echo",
                "ok",
            ],
            capture_output=True,
            text=True,
            timeout=timeout + 2,
        )
        return proc.returncode == 0 and "ok" in proc.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return False
