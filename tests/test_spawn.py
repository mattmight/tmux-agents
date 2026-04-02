"""Integration tests for spawn service and kill.

Tests use isolated tmux servers. Since Claude Code may not be installed
in CI, we spawn a simple long-running command (sleep) and verify:
- Session creation and metadata writing
- Detection as managed
- Kill works
- Argument building logic
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

from tmux_agents.config import TmuxAgentsConfig
from tmux_agents.models import Confidence, DetectionSource
from tmux_agents.services.inventory_service import all_panes, get_inventory
from tmux_agents.services.spawn_service import (
    _build_claude_command,
    _generate_session_name,
    kill_session,
)
from tmux_agents.tmux.command_runner import CommandRunner
from tmux_agents.tmux.metadata_store import read_pane_metadata, write_pane_metadata


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
def spawn_server(short_tmp, tmux_bin):
    """Create a tmux server with a session that simulates a managed spawn.

    Since claude may not be installed, we manually create a session with
    'sleep 3600' and write metadata, mimicking what spawn_claude does.
    """
    sock = str(short_tmp / "sp")
    session_name = "claude-test"

    subprocess.run(
        [
            tmux_bin,
            "-S",
            sock,
            "new-session",
            "-d",
            "-s",
            session_name,
            "-n",
            "main",
            "sleep 3600",
        ],
        check=True,
        timeout=10,
    )
    time.sleep(0.3)

    # Write managed metadata, same as spawn_service would
    runner = CommandRunner(socket_path=sock)
    pane_result = runner.run("list-panes", "-t", session_name, "-F", "#{pane_id}", no_start=True)
    pane_id = pane_result.stdout[0].strip()

    write_pane_metadata(
        runner,
        pane_id,
        {
            "schema_version": 1,
            "managed": True,
            "agent_kind": "claude",
            "profile": "claude",
            "created_at": "2026-03-31T18:20:41Z",
            "project_root": str(short_tmp),
            "requested_session_name": session_name,
            "spawn_transport": "cli",
        },
    )

    yield sock, session_name, pane_id
    subprocess.run(
        [tmux_bin, "-S", sock, "kill-server"],
        timeout=5,
        capture_output=True,
    )


# -- Argument builder unit tests ---------------------------------------------


class TestBuildClaudeCommand:
    def test_bare(self):
        assert _build_claude_command() == "claude"

    def test_session_name(self):
        cmd = _build_claude_command(claude_session_name="auth")
        assert cmd == "claude -n auth"

    def test_resume(self):
        cmd = _build_claude_command(resume="my-session")
        assert cmd == "claude --resume my-session"

    def test_continue(self):
        cmd = _build_claude_command(continue_session=True)
        assert cmd == "claude --continue"

    def test_worktree(self):
        cmd = _build_claude_command(worktree="feature-auth")
        assert cmd == "claude --worktree feature-auth"

    def test_combined(self):
        cmd = _build_claude_command(
            claude_session_name="auth",
            worktree="feature-auth",
        )
        assert "claude -n auth" in cmd
        assert "--worktree feature-auth" in cmd

    def test_resume_overrides_continue(self):
        cmd = _build_claude_command(resume="old", continue_session=True)
        assert "--resume old" in cmd
        assert "--continue" not in cmd

    def test_extra_args(self):
        cmd = _build_claude_command(extra_args=["--verbose", "--model", "opus"])
        assert cmd == "claude --verbose --model opus"


class TestGenerateSessionName:
    def test_from_claude_name(self):
        name = _generate_session_name("claude", label="auth")
        assert name == "claude-auth"

    def test_from_worktree(self):
        name = _generate_session_name("claude", worktree="feature-x")
        assert name == "claude-wt-feature-x"

    def test_auto(self):
        name = _generate_session_name("claude")
        assert name.startswith("claude-")
        assert len(name) > len("claude-")


# -- Integration: spawn simulation -------------------------------------------


class TestSpawnedSessionDetection:
    def test_session_created(self, spawn_server, tmux_bin):
        """The simulated spawn should create a running session."""
        sock, session_name, _pane_id = spawn_server
        runner = CommandRunner(socket_path=sock)
        result = runner.run("list-sessions", "-F", "#{session_name}", no_start=True)
        assert result.ok
        assert session_name in result.stdout

    def test_metadata_written(self, spawn_server):
        """Metadata should be readable from the spawned pane."""
        sock, _session_name, pane_id = spawn_server
        runner = CommandRunner(socket_path=sock)
        meta = read_pane_metadata(runner, pane_id)
        assert meta is not None
        assert meta["managed"] is True
        assert meta["agent_kind"] == "claude"
        assert meta["schema_version"] == 1

    def test_detected_as_managed(self, spawn_server):
        """Spawned pane should be detected as managed Claude via metadata."""
        sock, _session_name, pane_id = spawn_server
        config = TmuxAgentsConfig(extra_socket_paths=[sock])
        inventory = get_inventory(config)

        panes = [
            p
            for p in all_panes(inventory)
            if p.ref.pane and p.ref.pane.id == pane_id and p.ref.server.socket_path == sock
        ]
        assert len(panes) == 1
        pane = panes[0]
        assert pane.agent.detected_kind == "claude"
        assert pane.agent.source == DetectionSource.EXPLICIT
        assert pane.agent.managed is True
        assert pane.agent.confidence == Confidence.STRONG

    def test_kind_filter_finds_managed(self, spawn_server):
        """--kind claude should find the managed pane."""
        sock, _session_name, _pane_id = spawn_server
        config = TmuxAgentsConfig(extra_socket_paths=[sock])
        inventory = get_inventory(config)

        # Apply kind filter
        for server in inventory.servers:
            for session in server.sessions:
                for window in session.windows:
                    window.panes = [p for p in window.panes if p.agent.detected_kind == "claude"]

        managed = [p for p in all_panes(inventory) if p.ref.server.socket_path == sock]
        assert len(managed) == 1


class TestKillSession:
    def test_kill_by_name(self, spawn_server, tmux_bin):
        sock, session_name, _pane_id = spawn_server
        ok = kill_session(session_name, socket_path=sock)
        assert ok is True

        # Verify the session is gone
        runner = CommandRunner(socket_path=sock)
        result = runner.run("list-sessions", no_start=True)
        assert session_name not in (result.output if result.ok else "")


# -- CLI integration ---------------------------------------------------------


class TestSpawnCli:
    def test_spawn_help(self):
        from click.testing import CliRunner

        from tmux_agents.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["spawn", "--help"])
        assert result.exit_code == 0
        assert "--session" in result.output
        assert "--worktree" in result.output
        assert "--resume" in result.output

    def test_kill_help(self):
        from click.testing import CliRunner

        from tmux_agents.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["kill", "--help"])
        assert result.exit_code == 0
        assert "--session" in result.output

    def test_spawn_unsupported_agent(self):
        from click.testing import CliRunner

        from tmux_agents.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["spawn", "nonexistent_agent"])
        assert result.exit_code != 0
        assert "Unsupported" in (result.output + (result.stderr_bytes or b"").decode())
