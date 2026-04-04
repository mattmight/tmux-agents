"""Tests for SSH transport foundation (M13).

Covers: RemoteCommandRunner command assembly, SSH config parser,
get_runner() factory, error classification, and ServerRef host field.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tmux_agents.config import RemoteHostConfig, TmuxAgentsConfig
from tmux_agents.errors import ErrorCode, SSHError
from tmux_agents.refs import ServerRef
from tmux_agents.ssh.config_parser import list_ssh_hosts, validate_host_alias
from tmux_agents.ssh.runner import RemoteCommandRunner, ssh_reachable
from tmux_agents.tmux.command_runner import CommandRunner, get_runner

# ---------------------------------------------------------------------------
# ServerRef host field
# ---------------------------------------------------------------------------


class TestServerRefHost:
    def test_default_host_is_none(self):
        ref = ServerRef(socket_path="/tmp/tmux-501/default", socket_name="default")
        assert ref.host is None

    def test_explicit_host(self):
        ref = ServerRef(
            socket_path="/tmp/tmux-1000/default",
            socket_name="default",
            host="enterprise-a",
        )
        assert ref.host == "enterprise-a"

    def test_host_in_json_roundtrip(self):
        ref = ServerRef(
            socket_path="/tmp/tmux-1000/default",
            socket_name="default",
            host="enterprise-a",
        )
        data = ref.model_dump()
        assert data["host"] == "enterprise-a"
        restored = ServerRef.model_validate(data)
        assert restored.host == "enterprise-a"
        assert restored == ref

    def test_none_host_in_json_roundtrip(self):
        ref = ServerRef(socket_path="/tmp/tmux-501/default", socket_name="default")
        data = ref.model_dump()
        assert data["host"] is None
        restored = ServerRef.model_validate(data)
        assert restored == ref

    def test_different_hosts_are_not_equal(self):
        local = ServerRef(socket_path="/tmp/tmux-501/default", socket_name="default")
        remote = ServerRef(
            socket_path="/tmp/tmux-501/default",
            socket_name="default",
            host="enterprise-a",
        )
        assert local != remote

    def test_same_host_same_socket_are_equal(self):
        a = ServerRef(
            socket_path="/tmp/tmux-1000/default",
            socket_name="default",
            host="enterprise-a",
        )
        b = ServerRef(
            socket_path="/tmp/tmux-1000/default",
            socket_name="default",
            host="enterprise-a",
        )
        assert a == b


# ---------------------------------------------------------------------------
# RemoteHostConfig
# ---------------------------------------------------------------------------


class TestRemoteHostConfig:
    def test_basic_config(self):
        cfg = RemoteHostConfig(alias="enterprise-a")
        assert cfg.alias == "enterprise-a"
        assert cfg.display_name is None
        assert cfg.extra_socket_paths == []
        assert cfg.extra_socket_names == []

    def test_full_config(self):
        cfg = RemoteHostConfig(
            alias="enterprise-a",
            display_name="Enterprise A",
            extra_socket_paths=["/tmp/tmux-1000/custom"],
            extra_socket_names=["work"],
        )
        assert cfg.display_name == "Enterprise A"
        assert len(cfg.extra_socket_paths) == 1

    def test_hosts_in_tmux_agents_config(self):
        cfg = TmuxAgentsConfig(
            hosts=[RemoteHostConfig(alias="enterprise-a")],
        )
        assert len(cfg.hosts) == 1
        assert cfg.hosts[0].alias == "enterprise-a"

    def test_default_config_has_no_hosts(self):
        cfg = TmuxAgentsConfig()
        assert cfg.hosts == []


# ---------------------------------------------------------------------------
# SSH config parser
# ---------------------------------------------------------------------------


class TestSshConfigParser:
    def test_parse_hosts(self, tmp_path: Path):
        config_file = tmp_path / "ssh_config"
        config_file.write_text(
            textwrap.dedent("""\
            Host enterprise-a
                HostName 192.168.1.100
                User admin

            Host dev-box
                HostName dev.example.com

            Host *
                ServerAliveInterval 60
        """)
        )
        hosts = list_ssh_hosts(config_file)
        assert "enterprise-a" in hosts
        assert "dev-box" in hosts
        assert "*" not in hosts

    def test_parse_multiple_hosts_on_one_line(self, tmp_path: Path):
        config_file = tmp_path / "ssh_config"
        config_file.write_text("Host alpha beta gamma\n    User test\n")
        hosts = list_ssh_hosts(config_file)
        assert hosts == ["alpha", "beta", "gamma"]

    def test_missing_config_returns_empty(self, tmp_path: Path):
        hosts = list_ssh_hosts(tmp_path / "nonexistent")
        assert hosts == []

    def test_validate_host_alias_found(self, tmp_path: Path):
        config_file = tmp_path / "ssh_config"
        config_file.write_text("Host enterprise-a\n    HostName 10.0.0.1\n")
        assert validate_host_alias("enterprise-a", config_file) is True

    def test_validate_host_alias_not_found(self, tmp_path: Path):
        config_file = tmp_path / "ssh_config"
        config_file.write_text("Host enterprise-a\n    HostName 10.0.0.1\n")
        assert validate_host_alias("unknown-host", config_file) is False

    def test_wildcard_excluded(self, tmp_path: Path):
        config_file = tmp_path / "ssh_config"
        config_file.write_text("Host *.example.com\n    User test\n")
        hosts = list_ssh_hosts(config_file)
        assert hosts == []

    def test_question_mark_excluded(self, tmp_path: Path):
        config_file = tmp_path / "ssh_config"
        config_file.write_text("Host dev?\n    User test\n")
        hosts = list_ssh_hosts(config_file)
        assert hosts == []


# ---------------------------------------------------------------------------
# RemoteCommandRunner command assembly
# ---------------------------------------------------------------------------


class TestRemoteCommandRunnerAssembly:
    def test_basic_command(self):
        runner = RemoteCommandRunner(
            "enterprise-a",
            ssh_bin="/usr/bin/ssh",
            ssh_options=["-o", "BatchMode=yes"],
        )
        tmux_args = runner._tmux_args()
        assert tmux_args == ["tmux"]
        prefix = runner._ssh_prefix()
        assert prefix[0] == "/usr/bin/ssh"
        assert prefix[-1] == "enterprise-a"

    def test_socket_name(self):
        runner = RemoteCommandRunner(
            "enterprise-a",
            socket_name="work",
            ssh_bin="/usr/bin/ssh",
            ssh_options=[],
        )
        args = runner._tmux_args()
        assert args == ["tmux", "-L", "work"]

    def test_socket_path(self):
        runner = RemoteCommandRunner(
            "enterprise-a",
            socket_path="/tmp/tmux-1000/custom",
            ssh_bin="/usr/bin/ssh",
            ssh_options=[],
        )
        args = runner._tmux_args()
        assert args == ["tmux", "-S", "/tmp/tmux-1000/custom"]

    def test_no_start_flag(self):
        runner = RemoteCommandRunner(
            "enterprise-a",
            ssh_bin="/usr/bin/ssh",
            ssh_options=[],
        )
        args = runner._tmux_args(no_start=True)
        assert "-N" in args

    def test_both_socket_name_and_path_raises(self):
        with pytest.raises(ValueError, match="not both"):
            RemoteCommandRunner(
                "enterprise-a",
                socket_name="work",
                socket_path="/tmp/custom",
            )


# ---------------------------------------------------------------------------
# RemoteCommandRunner error classification
# ---------------------------------------------------------------------------


class TestRemoteCommandRunnerErrors:
    def _make_runner(self):
        return RemoteCommandRunner("enterprise-a", ssh_bin="/usr/bin/ssh", ssh_options=[])

    def test_auth_failed(self):
        runner = self._make_runner()
        with pytest.raises(SSHError) as exc_info:
            runner._raise_if_ssh_error(
                "Permission denied (publickey).", ["ssh", "..."], "list-sessions"
            )
        assert exc_info.value.envelope.code == ErrorCode.SSH_AUTH_FAILED

    def test_host_unknown(self):
        runner = self._make_runner()
        with pytest.raises(SSHError) as exc_info:
            runner._raise_if_ssh_error(
                "ssh: Could not resolve hostname enterprise-a",
                ["ssh", "..."],
                "list-sessions",
            )
        assert exc_info.value.envelope.code == ErrorCode.SSH_HOST_UNKNOWN

    def test_connection_refused(self):
        runner = self._make_runner()
        with pytest.raises(SSHError) as exc_info:
            runner._raise_if_ssh_error(
                "ssh: connect to host enterprise-a port 22: Connection refused",
                ["ssh", "..."],
                "list-sessions",
            )
        assert exc_info.value.envelope.code == ErrorCode.SSH_CONNECTION_FAILED

    def test_connection_timed_out(self):
        runner = self._make_runner()
        with pytest.raises(SSHError) as exc_info:
            runner._raise_if_ssh_error(
                "ssh: connect to host enterprise-a port 22: Connection timed out",
                ["ssh", "..."],
                "list-sessions",
            )
        assert exc_info.value.envelope.code == ErrorCode.SSH_CONNECTION_FAILED

    def test_non_ssh_error_does_not_raise(self):
        runner = self._make_runner()
        # A regular tmux error should not raise SSHError
        runner._raise_if_ssh_error(
            "no server running on /tmp/tmux-1000/default",
            ["ssh", "..."],
            "list-sessions",
        )

    def test_timeout_on_subprocess(self):
        runner = self._make_runner()
        import subprocess

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(["ssh"], 15)):
            with pytest.raises(SSHError) as exc_info:
                runner.run("list-sessions")
            assert exc_info.value.envelope.code == ErrorCode.SSH_TIMEOUT

    def test_ssh_binary_not_found(self):
        runner = RemoteCommandRunner("enterprise-a", ssh_bin="/nonexistent/ssh", ssh_options=[])
        with patch("subprocess.run", side_effect=FileNotFoundError()):
            with pytest.raises(SSHError) as exc_info:
                runner.run("list-sessions")
            assert exc_info.value.envelope.code == ErrorCode.SSH_CONNECTION_FAILED


# ---------------------------------------------------------------------------
# RemoteCommandRunner.run() with mocked subprocess
# ---------------------------------------------------------------------------


class TestRemoteCommandRunnerRun:
    def test_successful_run(self):
        runner = RemoteCommandRunner(
            "enterprise-a",
            ssh_bin="/usr/bin/ssh",
            ssh_options=["-o", "BatchMode=yes"],
        )
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "$0\n$1\n"
        mock_proc.stderr = ""

        with patch("subprocess.run", return_value=mock_proc) as mock_run:
            result = runner.run("list-sessions", "-F", "#{session_id}")

        assert result.ok
        assert result.stdout == ["$0", "$1"]
        # Verify the command includes ssh prefix + login shell wrapper
        called_cmd = mock_run.call_args[0][0]
        assert called_cmd[0] == "/usr/bin/ssh"
        # The tmux command is wrapped in $SHELL -lc '...'
        shell_arg = called_cmd[-1]
        assert "$SHELL -lc" in shell_arg
        assert "tmux" in shell_arg
        assert "list-sessions" in shell_arg

    def test_is_server_alive_true(self):
        runner = RemoteCommandRunner("enterprise-a", ssh_bin="/usr/bin/ssh", ssh_options=[])
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "$0"
        mock_proc.stderr = ""

        with patch("subprocess.run", return_value=mock_proc):
            assert runner.is_server_alive() is True

    def test_is_server_alive_false_on_ssh_error(self):
        runner = RemoteCommandRunner("enterprise-a", ssh_bin="/usr/bin/ssh", ssh_options=[])
        mock_proc = MagicMock()
        mock_proc.returncode = 255
        mock_proc.stdout = ""
        mock_proc.stderr = "Permission denied (publickey)."

        with patch("subprocess.run", return_value=mock_proc):
            assert runner.is_server_alive() is False


# ---------------------------------------------------------------------------
# get_runner() factory
# ---------------------------------------------------------------------------


class TestGetRunnerFactory:
    def test_none_returns_local(self):
        runner = get_runner(None, socket_name="default")
        assert isinstance(runner, CommandRunner)

    def test_local_string_returns_local(self):
        runner = get_runner("local", socket_path="/tmp/tmux-501/default")
        assert isinstance(runner, CommandRunner)

    def test_host_returns_remote(self):
        runner = get_runner("enterprise-a", socket_name="default")
        assert isinstance(runner, RemoteCommandRunner)
        assert runner.host == "enterprise-a"
        assert runner.socket_name == "default"

    def test_host_with_socket_path(self):
        runner = get_runner("enterprise-a", socket_path="/tmp/tmux-1000/custom")
        assert isinstance(runner, RemoteCommandRunner)
        assert runner.socket_path == "/tmp/tmux-1000/custom"


# ---------------------------------------------------------------------------
# ssh_reachable()
# ---------------------------------------------------------------------------


class TestSshReachable:
    def test_reachable(self):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "ok\n"

        with patch("subprocess.run", return_value=mock_proc):
            assert ssh_reachable("enterprise-a") is True

    def test_unreachable(self):
        mock_proc = MagicMock()
        mock_proc.returncode = 255
        mock_proc.stdout = ""

        with patch("subprocess.run", return_value=mock_proc):
            assert ssh_reachable("enterprise-a") is False

    def test_timeout(self):
        import subprocess

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(["ssh"], 5)):
            assert ssh_reachable("enterprise-a") is False

    def test_no_ssh_binary(self):
        with patch("shutil.which", return_value=None):
            assert ssh_reachable("enterprise-a") is False
