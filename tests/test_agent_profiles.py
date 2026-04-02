"""Tests for additional agent profiles: Codex and Gemini detection + spawn."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

from tmux_agents.agents.profiles.codex import CodexProfile
from tmux_agents.agents.profiles.gemini import GeminiProfile
from tmux_agents.agents.registry import get_profile, get_profiles
from tmux_agents.config import TmuxAgentsConfig
from tmux_agents.models import Confidence, DetectionSource
from tmux_agents.process.inspector import ProcessInfo
from tmux_agents.services.inventory_service import all_panes, get_inventory
from tmux_agents.services.spawn_service import (
    SUPPORTED_AGENTS,
    _generate_session_name,
    spawn_agent,
)
from tmux_agents.tmux.command_runner import CommandRunner
from tmux_agents.tmux.metadata_store import write_pane_metadata

# -- Codex Profile unit tests ------------------------------------------------


class TestCodexProfile:
    def test_kind(self):
        assert CodexProfile().kind == "codex"

    def test_match_process_tree_positive(self):
        tree = [
            ProcessInfo(pid=100, name="bash"),
            ProcessInfo(pid=101, name="codex", exe="/usr/local/bin/codex"),
        ]
        result = CodexProfile().match_process_tree(tree)
        assert result is not None
        assert result.detected_kind == "codex"
        assert result.confidence == Confidence.STRONG
        assert result.source == DetectionSource.PROCESS_TREE

    def test_match_process_tree_negative(self):
        tree = [ProcessInfo(pid=100, name="bash"), ProcessInfo(pid=101, name="vim")]
        assert CodexProfile().match_process_tree(tree) is None

    def test_no_false_positive_for_claude(self):
        tree = [ProcessInfo(pid=100, name="claude")]
        assert CodexProfile().match_process_tree(tree) is None

    def test_match_tmux_hints_positive(self):
        result = CodexProfile().match_tmux_hints(
            current_command="codex",
            current_path=None,
            session_name=None,
            window_name=None,
        )
        assert result is not None
        assert result.confidence == Confidence.WEAK

    def test_match_tmux_hints_negative(self):
        result = CodexProfile().match_tmux_hints(
            current_command="python",
            current_path=None,
            session_name=None,
            window_name=None,
        )
        assert result is None


# -- Gemini Profile unit tests -----------------------------------------------


class TestGeminiProfile:
    def test_kind(self):
        assert GeminiProfile().kind == "gemini"

    def test_match_process_tree_positive(self):
        tree = [
            ProcessInfo(pid=100, name="bash"),
            ProcessInfo(pid=101, name="gemini", exe="/usr/local/bin/gemini"),
        ]
        result = GeminiProfile().match_process_tree(tree)
        assert result is not None
        assert result.detected_kind == "gemini"
        assert result.confidence == Confidence.STRONG

    def test_match_process_tree_negative(self):
        tree = [ProcessInfo(pid=100, name="node")]
        assert GeminiProfile().match_process_tree(tree) is None

    def test_no_false_positive_for_claude(self):
        tree = [ProcessInfo(pid=100, name="claude")]
        assert GeminiProfile().match_process_tree(tree) is None

    def test_match_tmux_hints_positive(self):
        result = GeminiProfile().match_tmux_hints(
            current_command="gemini",
            current_path=None,
            session_name=None,
            window_name=None,
        )
        assert result is not None
        assert result.confidence == Confidence.WEAK

    def test_match_tmux_hints_negative(self):
        result = GeminiProfile().match_tmux_hints(
            current_command="make",
            current_path=None,
            session_name=None,
            window_name=None,
        )
        assert result is None


# -- Registry tests ----------------------------------------------------------


class TestRegistryAll:
    def test_all_three_profiles(self):
        profiles = get_profiles()
        kinds = {p.kind for p in profiles}
        assert kinds == {"claude", "codex", "gemini"}

    def test_get_codex(self):
        assert get_profile("codex") is not None
        assert get_profile("codex").kind == "codex"

    def test_get_gemini(self):
        assert get_profile("gemini") is not None
        assert get_profile("gemini").kind == "gemini"


# -- Session name generation -------------------------------------------------


class TestSessionNameGeneration:
    def test_codex_auto(self):
        name = _generate_session_name("codex")
        assert name.startswith("codex-")

    def test_gemini_auto(self):
        name = _generate_session_name("gemini")
        assert name.startswith("gemini-")

    def test_codex_label(self):
        assert _generate_session_name("codex", label="review") == "codex-review"

    def test_gemini_worktree(self):
        assert _generate_session_name("gemini", worktree="feat") == "gemini-wt-feat"


# -- Spawn validation -------------------------------------------------------


class TestSpawnAgentDispatch:
    def test_supported_agents_list(self):
        assert "claude" in SUPPORTED_AGENTS
        assert "codex" in SUPPORTED_AGENTS
        assert "gemini" in SUPPORTED_AGENTS

    def test_unsupported_raises(self):
        from tmux_agents.errors import TmuxAgentsError

        with pytest.raises(TmuxAgentsError, match="Unsupported agent kind"):
            spawn_agent("nonexistent")


# -- Integration: managed metadata detection ---------------------------------


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
def codex_pane_server(short_tmp, tmux_bin):
    """Server with a pane tagged as codex via metadata."""
    sock = str(short_tmp / "cx")
    subprocess.run(
        [tmux_bin, "-S", sock, "new-session", "-d", "-s", "codex-test", "-n", "main"],
        check=True,
        timeout=10,
    )
    time.sleep(0.3)
    runner = CommandRunner(socket_path=sock)
    pane_result = runner.run("list-panes", "-t", "codex-test", "-F", "#{pane_id}", no_start=True)
    pane_id = pane_result.stdout[0].strip()
    write_pane_metadata(
        runner,
        pane_id,
        {
            "schema_version": 1,
            "managed": True,
            "agent_kind": "codex",
            "profile": "codex",
        },
    )
    yield sock, pane_id
    subprocess.run([tmux_bin, "-S", sock, "kill-server"], timeout=5, capture_output=True)


class TestCodexDetectionIntegration:
    def test_codex_metadata_detected(self, codex_pane_server):
        sock, pane_id = codex_pane_server
        config = TmuxAgentsConfig(extra_socket_paths=[sock])
        inventory = get_inventory(config)
        panes = [
            p
            for p in all_panes(inventory)
            if p.ref.pane and p.ref.pane.id == pane_id and p.ref.server.socket_path == sock
        ]
        assert len(panes) == 1
        assert panes[0].agent.detected_kind == "codex"
        assert panes[0].agent.managed is True
        assert panes[0].agent.source == DetectionSource.EXPLICIT

    def test_kind_filter_codex(self, codex_pane_server):
        sock, _pane_id = codex_pane_server
        config = TmuxAgentsConfig(extra_socket_paths=[sock])
        inventory = get_inventory(config)
        for server in inventory.servers:
            for session in server.sessions:
                for window in session.windows:
                    window.panes = [p for p in window.panes if p.agent.detected_kind == "codex"]
        codex_panes = [p for p in all_panes(inventory) if p.ref.server.socket_path == sock]
        assert len(codex_panes) == 1


# -- CLI test ----------------------------------------------------------------


class TestSpawnCliMultiAgent:
    def test_spawn_help_shows_agent_kinds(self):
        from click.testing import CliRunner

        from tmux_agents.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["spawn", "--help"])
        assert result.exit_code == 0
        assert "AGENT_KIND" in result.output or "agent_kind" in result.output.lower()
