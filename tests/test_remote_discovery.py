"""Tests for remote socket discovery and host-aware inventory (M14)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from tmux_agents.config import RemoteHostConfig, TmuxAgentsConfig
from tmux_agents.refs import ServerRef
from tmux_agents.tmux.socket_discovery import _discover_remote_sockets


class TestDiscoverRemoteSockets:
    def test_default_socket_alive(self):
        mock_runner_cls = MagicMock()
        mock_runner = MagicMock()
        mock_runner.is_server_alive.return_value = True
        mock_runner_cls.return_value = mock_runner

        host_cfg = RemoteHostConfig(alias="enterprise-a")

        with patch(
            "tmux_agents.ssh.runner.RemoteCommandRunner",
            mock_runner_cls,
        ):
            results = _discover_remote_sockets(host_cfg)

        assert len(results) == 1
        assert results[0].alive is True
        assert results[0].ref.host == "enterprise-a"
        assert results[0].ref.socket_name == "default"
        assert results[0].source == "remote"

    def test_default_socket_dead(self):
        mock_runner_cls = MagicMock()
        mock_runner = MagicMock()
        mock_runner.is_server_alive.return_value = False
        mock_runner_cls.return_value = mock_runner

        host_cfg = RemoteHostConfig(alias="enterprise-a")

        with patch(
            "tmux_agents.ssh.runner.RemoteCommandRunner",
            mock_runner_cls,
        ):
            results = _discover_remote_sockets(host_cfg)

        assert len(results) == 1
        assert results[0].alive is False

    def test_extra_sockets(self):
        mock_runner_cls = MagicMock()
        mock_runner = MagicMock()
        mock_runner.is_server_alive.return_value = True
        mock_runner_cls.return_value = mock_runner

        host_cfg = RemoteHostConfig(
            alias="enterprise-a",
            extra_socket_names=["work"],
            extra_socket_paths=["/tmp/tmux-1000/custom"],
        )

        with patch(
            "tmux_agents.ssh.runner.RemoteCommandRunner",
            mock_runner_cls,
        ):
            results = _discover_remote_sockets(host_cfg)

        # default + work + custom = 3
        assert len(results) == 3
        names = {r.ref.socket_name for r in results}
        assert "default" in names
        assert "work" in names
        assert "custom" in names

    def test_ssh_failure_returns_empty(self):
        mock_runner_cls = MagicMock()
        mock_runner_cls.return_value.is_server_alive.side_effect = Exception("SSH failed")

        host_cfg = RemoteHostConfig(alias="unreachable")

        with patch(
            "tmux_agents.ssh.runner.RemoteCommandRunner",
            mock_runner_cls,
        ):
            results = _discover_remote_sockets(host_cfg)

        assert results == []

    def test_remote_ref_has_host_set(self):
        mock_runner_cls = MagicMock()
        mock_runner = MagicMock()
        mock_runner.is_server_alive.return_value = True
        mock_runner_cls.return_value = mock_runner

        host_cfg = RemoteHostConfig(alias="enterprise-a")

        with patch(
            "tmux_agents.ssh.runner.RemoteCommandRunner",
            mock_runner_cls,
        ):
            results = _discover_remote_sockets(host_cfg)

        ref = results[0].ref
        assert ref.host == "enterprise-a"
        # Should be distinguishable from a local default socket
        local_ref = ServerRef(socket_path="/tmp/tmux-501/default", socket_name="default")
        assert ref != local_ref


class TestDiscoverSocketsWithHosts:
    def test_hosts_integrated_into_discover(self):
        """Remote hosts from config are included in discover_sockets()."""
        mock_runner_cls = MagicMock()
        mock_runner = MagicMock()
        mock_runner.is_server_alive.return_value = True
        mock_runner_cls.return_value = mock_runner

        config = TmuxAgentsConfig(
            hosts=[RemoteHostConfig(alias="enterprise-a")],
        )

        with patch(
            "tmux_agents.ssh.runner.RemoteCommandRunner",
            mock_runner_cls,
        ):
            from tmux_agents.tmux.socket_discovery import discover_sockets

            results = discover_sockets(config)

        # Should have at least the remote host's default socket
        remote = [r for r in results if r.ref.host == "enterprise-a"]
        assert len(remote) >= 1

    def test_unreachable_host_skipped_gracefully(self):
        """An unreachable remote host should be skipped, not fail everything."""
        config = TmuxAgentsConfig(
            hosts=[RemoteHostConfig(alias="unreachable-host")],
        )

        with patch(
            "tmux_agents.tmux.socket_discovery._discover_remote_sockets",
            side_effect=Exception("SSH connection failed"),
        ):
            from tmux_agents.tmux.socket_discovery import discover_sockets

            # Should not raise — just log a warning and skip
            results = discover_sockets(config)

        # Local sockets still discovered (may or may not be present)
        # The key assertion is no exception was raised
        assert isinstance(results, list)
