"""Tests for inter-pane channel messaging."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

from tmux_agents.config import TmuxAgentsConfig
from tmux_agents.services.channel_service import list_channel_peers, read_messages, send_message
from tmux_agents.tmux.command_runner import CommandRunner
from tmux_agents.tmux.metadata_store import read_channel, write_channel, write_pane_metadata


@pytest.fixture
def tmux_bin():
    path = shutil.which("tmux")
    if not path:
        pytest.skip("tmux not installed")
    return path


@pytest.fixture
def short_tmp():
    d = tempfile.mkdtemp(prefix="ta-", dir="/tmp")
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def two_pane_server(short_tmp, tmux_bin):
    """Server with 2 panes in one session for messaging tests."""
    sock = str(short_tmp / "ch")
    subprocess.run(
        [tmux_bin, "-S", sock, "new-session", "-d", "-s", "chan", "-n", "main"],
        check=True,
        timeout=10,
    )
    subprocess.run(
        [tmux_bin, "-S", sock, "split-window", "-t", "chan:main"],
        check=True,
        timeout=10,
    )
    time.sleep(0.3)

    result = subprocess.run(
        [tmux_bin, "-S", sock, "list-panes", "-t", "chan", "-F", "#{pane_id}"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    pane_ids = [p.strip() for p in result.stdout.strip().split("\n")]
    assert len(pane_ids) == 2

    yield sock, pane_ids[0], pane_ids[1]
    subprocess.run([tmux_bin, "-S", sock, "kill-server"], timeout=5, capture_output=True)


# -- Metadata store channel tests -------------------------------------------


class TestChannelReadWrite:
    def test_write_and_read(self, two_pane_server):
        sock, pane_a, _pane_b = two_pane_server
        runner = CommandRunner(socket_path=sock)
        msg = {"from": pane_a, "message": "hello", "ts": "t1"}
        assert write_channel(runner, pane_a, msg) is True
        read_back = read_channel(runner, pane_a)
        assert read_back is not None
        assert read_back["message"] == "hello"

    def test_read_missing(self, two_pane_server):
        sock, pane_a, _pane_b = two_pane_server
        runner = CommandRunner(socket_path=sock)
        assert read_channel(runner, pane_a) is None


# -- Channel service tests --------------------------------------------------


class TestSendMessage:
    def test_send_and_receive(self, two_pane_server):
        sock, pane_a, pane_b = two_pane_server
        ok = send_message(pane_a, pane_b, "hello from A", socket_path=sock)
        assert ok is True

        msg = read_messages(pane_b, socket_path=sock)
        assert msg is not None
        assert msg["from"] == pane_a
        assert msg["message"] == "hello from A"
        assert "ts" in msg

    def test_overwrite_on_new_message(self, two_pane_server):
        sock, pane_a, pane_b = two_pane_server
        send_message(pane_a, pane_b, "first", socket_path=sock)
        send_message(pane_a, pane_b, "second", socket_path=sock)

        msg = read_messages(pane_b, socket_path=sock)
        assert msg["message"] == "second"

    def test_bidirectional(self, two_pane_server):
        sock, pane_a, pane_b = two_pane_server
        send_message(pane_a, pane_b, "A→B", socket_path=sock)
        send_message(pane_b, pane_a, "B→A", socket_path=sock)

        msg_b = read_messages(pane_b, socket_path=sock)
        msg_a = read_messages(pane_a, socket_path=sock)
        assert msg_b["message"] == "A→B"
        assert msg_a["message"] == "B→A"


class TestListPeers:
    def test_finds_managed_panes(self, two_pane_server):
        sock, pane_a, _pane_b = two_pane_server
        runner = CommandRunner(socket_path=sock)
        write_pane_metadata(
            runner,
            pane_a,
            {"schema_version": 1, "managed": True, "agent_kind": "claude", "profile": "claude"},
        )

        config = TmuxAgentsConfig(extra_socket_paths=[sock])
        peers = list_channel_peers(config)
        managed = [p for p in peers if p["pane_id"] == pane_a]
        assert len(managed) == 1
        assert managed[0]["agent_kind"] == "claude"

    def test_no_managed_returns_empty(self, two_pane_server):
        sock, _pane_a, _pane_b = two_pane_server
        # Don't write metadata — no managed panes in this server
        config = TmuxAgentsConfig(extra_socket_paths=[sock])
        peers = list_channel_peers(config)
        our_peers = [p for p in peers if p["socket_path"] == sock]
        assert len(our_peers) == 0


# -- CLI tests ---------------------------------------------------------------


class TestChannelsCli:
    def test_channels_help(self):
        from click.testing import CliRunner

        from tmux_agents.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["channels", "--help"])
        assert result.exit_code == 0
        assert "send" in result.output
        assert "read" in result.output
        assert "peers" in result.output

    def test_channels_send_help(self):
        from click.testing import CliRunner

        from tmux_agents.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["channels", "send", "--help"])
        assert result.exit_code == 0
        assert "--from" in result.output
        assert "--to" in result.output
        assert "--message" in result.output
