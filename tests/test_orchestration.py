"""Tests for M11 orchestration: spawn-into-window/split, wait-for-pattern, preview."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

from tmux_agents.config import TmuxAgentsConfig
from tmux_agents.errors import TmuxAgentsError
from tmux_agents.services.capture_service import wait_for_pattern
from tmux_agents.services.inventory_service import preview_pane
from tmux_agents.services.spawn_service import (
    _spawn_into_split,
    _spawn_into_window,
)
from tmux_agents.tmux.command_runner import CommandRunner


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
def base_server(short_tmp, tmux_bin):
    """A tmux server with one session to spawn into."""
    sock = str(short_tmp / "o")
    subprocess.run(
        [tmux_bin, "-S", sock, "new-session", "-d", "-s", "base", "-n", "main"],
        check=True,
        timeout=10,
    )
    time.sleep(0.3)
    yield sock
    subprocess.run([tmux_bin, "-S", sock, "kill-server"], timeout=5, capture_output=True)


# -- Spawn into window -------------------------------------------------------


class TestSpawnIntoWindow:
    def test_creates_window_in_session(self, base_server, tmux_bin):
        sock = base_server
        snap = _spawn_into_window(
            agent_kind="claude",
            cmd="sleep 3600",
            target_session="base",
            cwd="/tmp",
            socket_name=None,
            socket_path=sock,
            transport="cli",
        )
        assert snap.ref.pane is not None
        assert snap.agent.managed is True
        assert snap.agent.detected_kind == "claude"

        # Verify the session now has 2 windows
        runner = CommandRunner(socket_path=sock)
        result = runner.run("list-windows", "-t", "base", "-F", "#{window_id}", no_start=True)
        assert result.ok
        assert len(result.stdout) == 2

    def test_pane_has_metadata(self, base_server):
        sock = base_server
        snap = _spawn_into_window(
            agent_kind="codex",
            cmd="sleep 3600",
            target_session="base",
            cwd="/tmp",
            socket_name=None,
            socket_path=sock,
            transport="cli",
        )
        assert snap.agent.detected_kind == "codex"
        assert snap.agent.managed is True


# -- Spawn into split --------------------------------------------------------


class TestSpawnIntoSplit:
    def test_split_horizontal(self, base_server, tmux_bin):
        sock = base_server
        snap = _spawn_into_split(
            agent_kind="claude",
            cmd="sleep 3600",
            target_session="base",
            split_direction="horizontal",
            cwd="/tmp",
            socket_name=None,
            socket_path=sock,
            transport="cli",
        )
        assert snap.ref.pane is not None

        # Verify the first window now has 2 panes
        runner = CommandRunner(socket_path=sock)
        result = runner.run("list-panes", "-t", "base:main", "-F", "#{pane_id}", no_start=True)
        assert result.ok
        assert len(result.stdout) == 2

    def test_split_vertical(self, base_server):
        sock = base_server
        snap = _spawn_into_split(
            agent_kind="gemini",
            cmd="sleep 3600",
            target_session="base",
            split_direction="vertical",
            cwd="/tmp",
            socket_name=None,
            socket_path=sock,
            transport="cli",
        )
        assert snap.ref.pane is not None
        assert snap.agent.detected_kind == "gemini"


# -- Wait for pattern --------------------------------------------------------


class TestWaitForPattern:
    def test_finds_existing_content(self, base_server, tmux_bin):
        sock = base_server
        runner = CommandRunner(socket_path=sock)

        # Get pane id
        result = runner.run("list-panes", "-t", "base", "-F", "#{pane_id}", no_start=True)
        pane_id = result.stdout[0].strip()

        # Send text that matches
        subprocess.run(
            [tmux_bin, "-S", sock, "send-keys", "-t", pane_id, "echo FIND_ME_123", "Enter"],
            check=True,
            timeout=5,
        )
        time.sleep(0.3)

        match = wait_for_pattern(
            pane_id,
            r"FIND_ME_\d+",
            timeout_ms=5000,
            poll_interval_ms=200,
            socket_path=sock,
        )
        assert match.matched_text == "FIND_ME_123"
        assert match.pane_id == pane_id

    def test_timeout_raises(self, base_server):
        sock = base_server
        runner = CommandRunner(socket_path=sock)
        result = runner.run("list-panes", "-t", "base", "-F", "#{pane_id}", no_start=True)
        pane_id = result.stdout[0].strip()

        with pytest.raises(TmuxAgentsError, match="not found"):
            wait_for_pattern(
                pane_id,
                r"IMPOSSIBLE_PATTERN_XYZZY",
                timeout_ms=1000,
                poll_interval_ms=200,
                socket_path=sock,
            )


# -- Preview -----------------------------------------------------------------


class TestPreviewPane:
    def test_preview_has_output_and_tree(self, base_server, tmux_bin):
        sock = base_server
        runner = CommandRunner(socket_path=sock)
        result = runner.run("list-panes", "-t", "base", "-F", "#{pane_id}", no_start=True)
        pane_id = result.stdout[0].strip()

        config = TmuxAgentsConfig(extra_socket_paths=[sock])
        data = preview_pane(pane_id, config)

        assert "pane" in data
        assert "recent_output" in data
        assert "process_tree" in data
        assert isinstance(data["process_tree"], list)
        assert len(data["process_tree"]) >= 1


# -- CLI tests ---------------------------------------------------------------


class TestOrchestrationCli:
    def test_spawn_target_session_help(self):
        from click.testing import CliRunner

        from tmux_agents.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["spawn", "--help"])
        assert result.exit_code == 0
        assert "--target-session" in result.output
        assert "--split" in result.output

    def test_wait_help(self):
        from click.testing import CliRunner

        from tmux_agents.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["wait", "--help"])
        assert result.exit_code == 0
        assert "--pane" in result.output
        assert "--pattern" in result.output
        assert "--timeout" in result.output

    def test_inspect_preview_help(self):
        from click.testing import CliRunner

        from tmux_agents.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["inspect", "--help"])
        assert result.exit_code == 0
        assert "--preview" in result.output
