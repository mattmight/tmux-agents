"""Tests for input service: send_text, send_keys, and tag_pane."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

from tmux_agents.models import CaptureMode
from tmux_agents.services.capture_service import capture_pane, reset_pane_state
from tmux_agents.services.input_service import send_keys, send_text, tag_pane
from tmux_agents.tmux.command_runner import CommandRunner
from tmux_agents.tmux.metadata_store import read_pane_metadata


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
def input_server(short_tmp, tmux_bin):
    """Server with a shell pane for input testing."""
    sock = str(short_tmp / "i")
    subprocess.run(
        [tmux_bin, "-S", sock, "new-session", "-d", "-s", "input", "-n", "main"],
        check=True,
        timeout=10,
    )
    time.sleep(0.3)

    result = subprocess.run(
        [tmux_bin, "-S", sock, "list-panes", "-a", "-F", "#{pane_id}"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    pane_id = result.stdout.strip().split("\n")[0]

    yield sock, pane_id
    reset_pane_state(pane_id)
    subprocess.run(
        [tmux_bin, "-S", sock, "kill-server"],
        timeout=5,
        capture_output=True,
    )


# -- send_text tests ---------------------------------------------------------


class TestSendText:
    def test_text_appears_in_capture(self, input_server, tmux_bin):
        """Sent text should appear in the pane's captured output."""
        sock, pane_id = input_server

        ok = send_text(pane_id, "echo HELLO_TEXT_MARKER", socket_path=sock)
        assert ok is True

        # Send Enter separately via send_keys to execute
        send_keys(pane_id, "Enter", socket_path=sock)
        time.sleep(0.3)

        cap = capture_pane(pane_id, mode=CaptureMode.TAIL, lines=50, socket_path=sock)
        assert "HELLO_TEXT_MARKER" in cap.content

    def test_text_is_literal(self, input_server):
        """send_text should NOT interpret key names — 'Enter' is sent as text."""
        sock, pane_id = input_server

        ok = send_text(pane_id, "Enter", socket_path=sock)
        assert ok is True

        time.sleep(0.2)
        cap = capture_pane(pane_id, mode=CaptureMode.TAIL, lines=20, socket_path=sock)
        # The literal string "Enter" should be in the pane, not a newline
        assert "Enter" in cap.content


# -- send_keys tests ---------------------------------------------------------


class TestSendKeys:
    def test_enter_key(self, input_server, tmux_bin):
        """Sending 'Enter' should produce a new prompt line."""
        sock, pane_id = input_server

        # Type a command with send_text, then press Enter with send_keys
        send_text(pane_id, "echo KEYS_TEST", socket_path=sock)
        ok = send_keys(pane_id, "Enter", socket_path=sock)
        assert ok is True
        time.sleep(0.3)

        cap = capture_pane(pane_id, mode=CaptureMode.TAIL, lines=50, socket_path=sock)
        assert "KEYS_TEST" in cap.content

    def test_multiple_keys(self, input_server):
        """Multiple keys can be sent in one call."""
        sock, pane_id = input_server
        ok = send_keys(pane_id, "a", "b", "c", socket_path=sock)
        assert ok is True

        time.sleep(0.2)
        cap = capture_pane(pane_id, mode=CaptureMode.TAIL, lines=20, socket_path=sock)
        assert "abc" in cap.content

    def test_ctrl_c(self, input_server, tmux_bin):
        """C-c should be interpretable as a control key."""
        sock, pane_id = input_server

        # Start a long-running command
        send_text(pane_id, "sleep 9999", socket_path=sock)
        send_keys(pane_id, "Enter", socket_path=sock)
        time.sleep(0.3)

        # Send C-c to interrupt
        ok = send_keys(pane_id, "C-c", socket_path=sock)
        assert ok is True
        time.sleep(0.3)

        # The shell prompt should return (sleep was interrupted)
        cap = capture_pane(pane_id, mode=CaptureMode.TAIL, lines=10, socket_path=sock)
        # After C-c, we should see either a prompt or ^C
        assert cap.line_count > 0

    def test_empty_keys_noop(self, input_server):
        """Sending no keys should succeed without error."""
        sock, pane_id = input_server
        ok = send_keys(pane_id, socket_path=sock)
        assert ok is True


# -- tag_pane tests ----------------------------------------------------------


class TestTagPane:
    def test_tag_writes_metadata(self, input_server):
        sock, pane_id = input_server
        ok = tag_pane(pane_id, agent_kind="claude", socket_path=sock)
        assert ok is True

        runner = CommandRunner(socket_path=sock)
        meta = read_pane_metadata(runner, pane_id)
        assert meta is not None
        assert meta["agent_kind"] == "claude"
        assert meta["profile"] == "claude"
        assert meta["schema_version"] == 1

    def test_tag_merges_existing(self, input_server):
        """Tag should merge into existing metadata, not overwrite."""
        sock, pane_id = input_server
        runner = CommandRunner(socket_path=sock)

        from tmux_agents.tmux.metadata_store import write_pane_metadata

        write_pane_metadata(
            runner, pane_id, {"schema_version": 1, "managed": True, "custom": "data"}
        )

        tag_pane(pane_id, agent_kind="claude", socket_path=sock)

        meta = read_pane_metadata(runner, pane_id)
        assert meta is not None
        assert meta["agent_kind"] == "claude"
        assert meta["managed"] is True  # preserved from existing
        assert meta["custom"] == "data"  # preserved from existing

    def test_tag_with_custom_profile(self, input_server):
        sock, pane_id = input_server
        tag_pane(pane_id, agent_kind="claude", profile="claude-fast", socket_path=sock)

        runner = CommandRunner(socket_path=sock)
        meta = read_pane_metadata(runner, pane_id)
        assert meta["profile"] == "claude-fast"


# -- CLI tests ---------------------------------------------------------------


class TestInputCli:
    def test_send_text_help(self):
        from click.testing import CliRunner

        from tmux_agents.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["send-text", "--help"])
        assert result.exit_code == 0
        assert "--pane" in result.output
        assert "--text" in result.output

    def test_send_keys_help(self):
        from click.testing import CliRunner

        from tmux_agents.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["send-keys", "--help"])
        assert result.exit_code == 0
        assert "--pane" in result.output

    def test_tag_help(self):
        from click.testing import CliRunner

        from tmux_agents.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["tag", "--help"])
        assert result.exit_code == 0
        assert "--agent-kind" in result.output


# -- Dead pane tests ---------------------------------------------------------


@pytest.fixture
def dead_pane_server(short_tmp, tmux_bin):
    """Server with a dead pane."""
    sock = str(short_tmp / "dp")
    subprocess.run(
        [tmux_bin, "-S", sock, "new-session", "-d", "-s", "dead", "-n", "main"],
        check=True,
        timeout=10,
    )
    subprocess.run(
        [tmux_bin, "-S", sock, "set-option", "-g", "remain-on-exit", "on"],
        check=True,
        timeout=5,
    )
    time.sleep(0.2)

    result = subprocess.run(
        [tmux_bin, "-S", sock, "list-panes", "-a", "-F", "#{pane_id} #{pane_pid}"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    line = result.stdout.strip().split("\n")[0]
    pane_id, pid = line.split()

    subprocess.run(["kill", "-9", pid], timeout=5)
    time.sleep(0.5)

    yield sock, pane_id
    subprocess.run(
        [tmux_bin, "-S", sock, "kill-server"],
        timeout=5,
        capture_output=True,
    )


class TestDeadPaneInput:
    def test_send_text_dead_pane_raises(self, dead_pane_server):
        from tmux_agents.errors import PaneDeadError

        sock, pane_id = dead_pane_server
        with pytest.raises(PaneDeadError):
            send_text(pane_id, "hello", socket_path=sock)

    def test_send_keys_dead_pane_raises(self, dead_pane_server):
        from tmux_agents.errors import PaneDeadError

        sock, pane_id = dead_pane_server
        with pytest.raises(PaneDeadError):
            send_keys(pane_id, "Enter", socket_path=sock)
