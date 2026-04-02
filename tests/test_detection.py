"""Tests for process inspection, agent detection, and metadata store.

Covers:
- Process inspector (unit + live)
- Claude profile matching (unit)
- Metadata store (integration with live tmux)
- Detection service three-pass logic (integration)
- False-positive resistance for shell/vim panes
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

from tmux_agents.agents.profiles.claude import ClaudeProfile
from tmux_agents.agents.registry import get_profile, get_profiles
from tmux_agents.config import TmuxAgentsConfig
from tmux_agents.models import (
    Confidence,
    DetectionSource,
)
from tmux_agents.process.inspector import ProcessInfo, find_in_tree, get_process_tree
from tmux_agents.services.inventory_service import all_panes, get_inventory
from tmux_agents.tmux.command_runner import CommandRunner
from tmux_agents.tmux.metadata_store import (
    read_pane_metadata,
    write_pane_metadata,
)

# -- Fixtures ----------------------------------------------------------------


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
def isolated_server(short_tmp, tmux_bin):
    """Single session server for detection tests."""
    sock = str(short_tmp / "d")
    subprocess.run(
        [tmux_bin, "-S", sock, "new-session", "-d", "-s", "test", "-n", "main"],
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


def _get_first_pane_id(tmux_bin: str, sock: str) -> str:
    """Get the first pane ID from an isolated server."""
    result = subprocess.run(
        [tmux_bin, "-S", sock, "list-panes", "-a", "-F", "#{pane_id}"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    return result.stdout.strip().split("\n")[0]


# -- Process Inspector tests -------------------------------------------------


class TestProcessInspector:
    def test_current_process_tree(self):
        """Should be able to walk our own process tree."""
        tree = get_process_tree(os.getpid())
        assert len(tree) >= 1
        assert tree[0].pid == os.getpid()
        assert tree[0].name != ""

    def test_nonexistent_pid(self):
        """Should return empty list for a non-existent PID."""
        tree = get_process_tree(999999999)
        assert tree == []

    def test_find_in_tree_self(self):
        """Should find python in our own process tree."""
        matches = find_in_tree(os.getpid(), "python")
        assert len(matches) >= 1

    def test_find_in_tree_no_match(self):
        """Should return empty for nonsense pattern."""
        matches = find_in_tree(os.getpid(), "xyzzy_nonexistent_process")
        assert matches == []


# -- Claude Profile tests (unit) --------------------------------------------


class TestClaudeProfile:
    def test_kind(self):
        p = ClaudeProfile()
        assert p.kind == "claude"

    def test_match_process_tree_positive(self):
        """Should match a tree containing a 'claude' process."""
        tree = [
            ProcessInfo(pid=100, name="zsh"),
            ProcessInfo(pid=101, name="claude", exe="/usr/local/bin/claude"),
        ]
        p = ClaudeProfile()
        result = p.match_process_tree(tree)
        assert result is not None
        assert result.detected_kind == "claude"
        assert result.confidence == Confidence.STRONG
        assert result.source == DetectionSource.PROCESS_TREE
        assert len(result.evidence["matched_processes"]) == 1

    def test_match_process_tree_negative(self):
        """Should NOT match a tree with only shell/vim."""
        tree = [
            ProcessInfo(pid=100, name="zsh"),
            ProcessInfo(pid=101, name="vim"),
        ]
        p = ClaudeProfile()
        result = p.match_process_tree(tree)
        assert result is None

    def test_match_process_tree_shell(self):
        """Shell-only tree should not match."""
        tree = [ProcessInfo(pid=100, name="bash")]
        p = ClaudeProfile()
        assert p.match_process_tree(tree) is None

    def test_match_process_tree_build_tools(self):
        """Common build tools should not match."""
        tree = [
            ProcessInfo(pid=100, name="zsh"),
            ProcessInfo(pid=101, name="node"),
            ProcessInfo(pid=102, name="npm"),
        ]
        p = ClaudeProfile()
        assert p.match_process_tree(tree) is None

    def test_match_tmux_hints_positive(self):
        """Should match if pane_current_command is 'claude'."""
        p = ClaudeProfile()
        result = p.match_tmux_hints(
            current_command="claude",
            current_path="/repo",
            session_name="work",
            window_name="main",
        )
        assert result is not None
        assert result.confidence == Confidence.WEAK
        assert result.source == DetectionSource.TMUX_HINT

    def test_match_tmux_hints_negative(self):
        """Should not match for generic commands."""
        p = ClaudeProfile()
        for cmd in ["bash", "vim", "python", "node", "make", "cargo"]:
            result = p.match_tmux_hints(
                current_command=cmd,
                current_path=None,
                session_name=None,
                window_name=None,
            )
            assert result is None, f"False positive for {cmd}"


# -- Registry tests ----------------------------------------------------------


class TestRegistry:
    def test_get_profiles(self):
        profiles = get_profiles()
        assert len(profiles) >= 1
        assert any(p.kind == "claude" for p in profiles)

    def test_get_profile_by_kind(self):
        assert get_profile("claude") is not None
        assert get_profile("nonexistent") is None


# -- Metadata Store tests (integration) --------------------------------------


class TestMetadataStore:
    def test_write_and_read(self, isolated_server, tmux_bin):
        sock = isolated_server
        pane_id = _get_first_pane_id(tmux_bin, sock)
        runner = CommandRunner(socket_path=sock)

        meta = {
            "schema_version": 1,
            "managed": True,
            "agent_kind": "claude",
            "profile": "claude",
        }
        assert write_pane_metadata(runner, pane_id, meta) is True

        read_back = read_pane_metadata(runner, pane_id)
        assert read_back is not None
        assert read_back["managed"] is True
        assert read_back["agent_kind"] == "claude"

    def test_read_missing(self, isolated_server, tmux_bin):
        sock = isolated_server
        pane_id = _get_first_pane_id(tmux_bin, sock)
        runner = CommandRunner(socket_path=sock)

        result = read_pane_metadata(runner, pane_id)
        assert result is None


# -- Detection Service integration tests ------------------------------------


class TestDetectPaneIntegration:
    def test_shell_pane_not_detected(self, isolated_server, tmux_bin):
        """A plain shell pane should have no agent detected."""
        sock = isolated_server
        config = TmuxAgentsConfig(extra_socket_paths=[sock])
        inventory = get_inventory(config)
        panes = [p for p in all_panes(inventory) if p.ref.server.socket_path == sock]

        assert len(panes) >= 1
        for pane in panes:
            # Shell panes should not be classified as any agent
            assert pane.agent.detected_kind is None or pane.agent.confidence == Confidence.NONE

    def test_managed_pane_explicit(self, isolated_server, tmux_bin):
        """A pane with @tmux-agents.meta should detect as explicit managed."""
        sock = isolated_server
        pane_id = _get_first_pane_id(tmux_bin, sock)
        runner = CommandRunner(socket_path=sock)

        meta = {
            "schema_version": 1,
            "managed": True,
            "agent_kind": "claude",
            "profile": "claude",
        }
        write_pane_metadata(runner, pane_id, meta)

        config = TmuxAgentsConfig(extra_socket_paths=[sock])
        inventory = get_inventory(config)
        panes = [p for p in all_panes(inventory) if p.ref.server.socket_path == sock]

        managed = [p for p in panes if p.ref.pane and p.ref.pane.id == pane_id]
        assert len(managed) == 1
        assert managed[0].agent.detected_kind == "claude"
        assert managed[0].agent.source == DetectionSource.EXPLICIT
        assert managed[0].agent.managed is True
        assert managed[0].agent.confidence == Confidence.STRONG


class TestKindFiltering:
    def test_kind_filter_excludes_non_agents(self, isolated_server, tmux_bin):
        """--kind claude should return no panes when none are Claude."""
        sock = isolated_server
        config = TmuxAgentsConfig(extra_socket_paths=[sock])
        inventory = get_inventory(config)

        # Filter to only claude panes
        for server in inventory.servers:
            for session in server.sessions:
                for window in session.windows:
                    window.panes = [p for p in window.panes if p.agent.detected_kind == "claude"]

        panes = [p for p in all_panes(inventory) if p.ref.server.socket_path == sock]
        assert len(panes) == 0

    def test_kind_filter_includes_managed(self, isolated_server, tmux_bin):
        """--kind claude should include panes with managed metadata."""
        sock = isolated_server
        pane_id = _get_first_pane_id(tmux_bin, sock)
        runner = CommandRunner(socket_path=sock)
        write_pane_metadata(
            runner,
            pane_id,
            {"schema_version": 1, "managed": True, "agent_kind": "claude", "profile": "claude"},
        )

        config = TmuxAgentsConfig(extra_socket_paths=[sock])
        inventory = get_inventory(config)

        for server in inventory.servers:
            for session in server.sessions:
                for window in session.windows:
                    window.panes = [p for p in window.panes if p.agent.detected_kind == "claude"]

        panes = [p for p in all_panes(inventory) if p.ref.server.socket_path == sock]
        assert len(panes) == 1
        assert panes[0].agent.detected_kind == "claude"
