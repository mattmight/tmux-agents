"""Integration tests for inventory collection and service layer.

Tests use isolated tmux servers with known sessions, windows, and panes
to verify parent-child graph, runtime fields, and edge cases.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

from tmux_agents.config import TmuxAgentsConfig
from tmux_agents.errors import PaneNotFoundError
from tmux_agents.refs import ServerRef
from tmux_agents.services.inventory_service import (
    all_panes,
    get_inventory,
    inspect_pane,
)
from tmux_agents.tmux.command_runner import CommandRunner
from tmux_agents.tmux.inventory import collect_server_inventory


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
def server_with_sessions(short_tmp, tmux_bin):
    """Create a tmux server with 2 sessions, 2 windows in first, 1 window in second."""
    sock = str(short_tmp / "s")
    # Session 1 with 2 windows
    subprocess.run(
        [tmux_bin, "-S", sock, "new-session", "-d", "-s", "alpha", "-n", "main"],
        check=True,
        timeout=10,
    )
    subprocess.run(
        [tmux_bin, "-S", sock, "new-window", "-t", "alpha", "-n", "aux"],
        check=True,
        timeout=10,
    )
    # Session 2 with 1 window
    subprocess.run(
        [tmux_bin, "-S", sock, "new-session", "-d", "-s", "beta", "-n", "work"],
        check=True,
        timeout=10,
    )
    time.sleep(0.3)
    yield sock
    subprocess.run(
        [tmux_bin, "-S", sock, "kill-server"],
        timeout=5,
        capture_output=True,
    )


@pytest.fixture
def server_with_split(short_tmp, tmux_bin):
    """Create a tmux server with 1 session, 1 window, 2 panes (split)."""
    sock = str(short_tmp / "sp")
    subprocess.run(
        [tmux_bin, "-S", sock, "new-session", "-d", "-s", "split-test", "-n", "main"],
        check=True,
        timeout=10,
    )
    subprocess.run(
        [tmux_bin, "-S", sock, "split-window", "-t", "split-test:main"],
        check=True,
        timeout=10,
    )
    time.sleep(0.3)
    yield sock
    subprocess.run(
        [tmux_bin, "-S", sock, "kill-server"],
        timeout=5,
        capture_output=True,
    )


# -- collect_server_inventory tests ------------------------------------------


class TestCollectServerInventory:
    def test_basic_structure(self, server_with_sessions):
        sock = server_with_sessions
        ref = ServerRef(socket_path=sock, socket_name=Path(sock).name)
        runner = CommandRunner(socket_path=sock)
        snap = collect_server_inventory(runner, ref)

        assert snap.display.session_count == 2
        session_names = {s.display.name for s in snap.sessions}
        assert session_names == {"alpha", "beta"}

    def test_parent_child_windows(self, server_with_sessions):
        sock = server_with_sessions
        ref = ServerRef(socket_path=sock, socket_name=Path(sock).name)
        runner = CommandRunner(socket_path=sock)
        snap = collect_server_inventory(runner, ref)

        alpha = next(s for s in snap.sessions if s.display.name == "alpha")
        assert len(alpha.windows) == 2
        window_names = {w.display.name for w in alpha.windows}
        assert "main" in window_names
        assert "aux" in window_names

        beta = next(s for s in snap.sessions if s.display.name == "beta")
        assert len(beta.windows) == 1
        assert beta.windows[0].display.name == "work"

    def test_panes_have_runtime(self, server_with_sessions):
        sock = server_with_sessions
        ref = ServerRef(socket_path=sock, socket_name=Path(sock).name)
        runner = CommandRunner(socket_path=sock)
        snap = collect_server_inventory(runner, ref)

        # Every window should have at least 1 pane
        for session in snap.sessions:
            for window in session.windows:
                assert len(window.panes) >= 1
                for pane in window.panes:
                    # Runtime fields should be populated
                    assert pane.runtime.pane_pid is not None
                    assert pane.runtime.pane_pid > 0
                    assert pane.runtime.pane_current_command is not None
                    assert pane.runtime.pane_width is not None
                    assert pane.runtime.pane_height is not None
                    assert pane.runtime.pane_dead is False

    def test_split_creates_multiple_panes(self, server_with_split):
        sock = server_with_split
        ref = ServerRef(socket_path=sock, socket_name=Path(sock).name)
        runner = CommandRunner(socket_path=sock)
        snap = collect_server_inventory(runner, ref)

        session = snap.sessions[0]
        window = session.windows[0]
        assert len(window.panes) == 2
        # Pane IDs should be unique
        pane_ids = {p.ref.pane.id for p in window.panes}
        assert len(pane_ids) == 2

    def test_refs_contain_full_hierarchy(self, server_with_sessions):
        sock = server_with_sessions
        ref = ServerRef(socket_path=sock, socket_name=Path(sock).name)
        runner = CommandRunner(socket_path=sock)
        snap = collect_server_inventory(runner, ref)

        # Check that pane refs have all four layers
        for session in snap.sessions:
            for window in session.windows:
                for pane in window.panes:
                    assert pane.ref.server == ref
                    assert pane.ref.session is not None
                    assert pane.ref.session.id.startswith("$")
                    assert pane.ref.window is not None
                    assert pane.ref.window.id.startswith("@")
                    assert pane.ref.pane is not None
                    assert pane.ref.pane.id.startswith("%")

    def test_session_ids_are_stable(self, server_with_sessions):
        """Session IDs should start with $ and be tmux-assigned."""
        sock = server_with_sessions
        ref = ServerRef(socket_path=sock, socket_name=Path(sock).name)
        runner = CommandRunner(socket_path=sock)
        snap = collect_server_inventory(runner, ref)

        for session in snap.sessions:
            assert session.ref.session is not None
            assert session.ref.session.id.startswith("$")

    def test_dead_server_returns_empty(self, short_tmp, tmux_bin):
        """Collecting from a non-existent server returns empty snapshot."""
        sock = str(short_tmp / "nosrv")
        ref = ServerRef(socket_path=sock, socket_name="nosrv")
        runner = CommandRunner(socket_path=sock)
        snap = collect_server_inventory(runner, ref)
        assert snap.display.session_count == 0
        assert snap.sessions == []


# -- inventory_service tests -------------------------------------------------


class TestGetInventory:
    def test_discovers_and_collects(self, server_with_sessions):
        sock = server_with_sessions
        config = TmuxAgentsConfig(extra_socket_paths=[sock])
        inventory = get_inventory(config)

        # Should find at least the server we created
        paths = {s.ref.server.socket_path for s in inventory.servers}
        assert sock in paths

        # Check that sessions are present
        our_server = next(s for s in inventory.servers if s.ref.server.socket_path == sock)
        assert our_server.display.session_count == 2

    def test_socket_filter(self, server_with_sessions):
        sock = server_with_sessions
        sock_name = Path(sock).name
        config = TmuxAgentsConfig(extra_socket_paths=[sock])

        # Filter by matching name
        inventory = get_inventory(config, socket_filter=sock_name)
        assert len(inventory.servers) >= 1

        # Filter by non-matching name
        inventory = get_inventory(config, socket_filter="nonexistent_xyz")
        # Should not contain our server (may still have default)
        assert not any(s.ref.server.socket_path == sock for s in inventory.servers)

    def test_has_timestamp(self, server_with_sessions):
        sock = server_with_sessions
        config = TmuxAgentsConfig(extra_socket_paths=[sock])
        inventory = get_inventory(config)
        assert inventory.timestamp is not None


class TestInspectPane:
    def test_finds_pane(self, server_with_sessions):
        sock = server_with_sessions
        config = TmuxAgentsConfig(extra_socket_paths=[sock])
        inventory = get_inventory(config)

        # Get any pane ID from inventory
        panes = all_panes(inventory)
        assert len(panes) > 0
        target_id = panes[0].ref.pane.id

        snap = inspect_pane(target_id, config)
        assert snap.ref.pane.id == target_id
        assert snap.runtime.pane_pid is not None

    def test_not_found_raises(self, server_with_sessions):
        sock = server_with_sessions
        config = TmuxAgentsConfig(extra_socket_paths=[sock])
        with pytest.raises(PaneNotFoundError):
            inspect_pane("%99999", config)


class TestAllPanes:
    def test_flattens(self, server_with_sessions):
        sock = server_with_sessions
        config = TmuxAgentsConfig(extra_socket_paths=[sock])
        inventory = get_inventory(config)
        panes = all_panes(inventory)

        # 2 sessions: alpha has 2 windows (1 pane each), beta has 1 window (1 pane)
        # So at minimum 3 panes from our server
        our_panes = [p for p in panes if p.ref.server.socket_path == sock]
        assert len(our_panes) >= 3


# -- CLI integration tests ---------------------------------------------------


class TestCliList:
    def test_list_json(self, server_with_sessions):
        import json

        from click.testing import CliRunner

        from tmux_agents.cli.main import cli

        sock = server_with_sessions
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "list", "--socket", Path(sock).name])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "servers" in data
        assert "timestamp" in data

    def test_list_human(self, server_with_sessions):
        from click.testing import CliRunner

        from tmux_agents.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["list"])
        assert result.exit_code == 0
        # Should have rendered something (could be "No live" or actual servers)
        assert len(result.output) > 0

    def test_inspect_json(self, server_with_sessions):
        import json

        from click.testing import CliRunner

        from tmux_agents.cli.main import cli

        sock = server_with_sessions
        # First get a pane ID via list
        config = TmuxAgentsConfig(extra_socket_paths=[sock])
        inventory = get_inventory(config)
        panes = all_panes(inventory)
        pane_id = panes[0].ref.pane.id

        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "inspect", "--pane", pane_id])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["ref"]["pane"]["id"] == pane_id

    def test_inspect_not_found(self):
        from click.testing import CliRunner

        from tmux_agents.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["inspect", "--pane", "%99999"])
        assert result.exit_code != 0
