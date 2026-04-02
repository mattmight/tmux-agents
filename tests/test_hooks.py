"""Tests for Claude cooperative state bridge: hook generator, metadata, CLI."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

from tmux_agents.hooks.generator import generate_hooks_config
from tmux_agents.tmux.command_runner import CommandRunner
from tmux_agents.tmux.metadata_store import read_hook_state, write_hook_state

# -- Hook generator tests ----------------------------------------------------


class TestHookGenerator:
    def test_returns_dict_with_hooks_key(self):
        config = generate_hooks_config()
        assert isinstance(config, dict)
        assert "hooks" in config

    def test_notification_hook_present(self):
        config = generate_hooks_config()
        assert "Notification" in config["hooks"]

    def test_subagent_start_hook_present(self):
        config = generate_hooks_config()
        assert "SubagentStart" in config["hooks"]

    def test_subagent_stop_hook_present(self):
        config = generate_hooks_config()
        assert "SubagentStop" in config["hooks"]

    def test_stop_hook_present(self):
        config = generate_hooks_config()
        assert "Stop" in config["hooks"]

    def test_hook_commands_reference_env_vars(self):
        config = generate_hooks_config()
        for event_name, entries in config["hooks"].items():
            for entry in entries:
                for hook in entry["hooks"]:
                    cmd = hook["command"]
                    assert "$TMUX_AGENTS_PANE_ID" in cmd, f"{event_name} missing PANE_ID"
                    assert "$TMUX_AGENTS_SOCKET" in cmd, f"{event_name} missing SOCKET"

    def test_hook_commands_have_guard(self):
        config = generate_hooks_config()
        for event_name, entries in config["hooks"].items():
            for entry in entries:
                for hook in entry["hooks"]:
                    cmd = hook["command"]
                    assert '[ -n "$TMUX_AGENTS_PANE_ID" ]' in cmd, f"{event_name} missing guard"

    def test_output_is_serializable_json(self):
        config = generate_hooks_config()
        dumped = json.dumps(config)
        reparsed = json.loads(dumped)
        assert reparsed == config


# -- Hook state metadata tests -----------------------------------------------


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
def hook_server(short_tmp, tmux_bin):
    sock = str(short_tmp / "h")
    subprocess.run(
        [tmux_bin, "-S", sock, "new-session", "-d", "-s", "hook", "-n", "main"],
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
    subprocess.run([tmux_bin, "-S", sock, "kill-server"], timeout=5, capture_output=True)


class TestHookStateMetadata:
    def test_write_and_read_hook(self, hook_server):
        sock, pane_id = hook_server
        runner = CommandRunner(socket_path=sock)
        state = {"event": "Notification", "ts": "2026-03-31T18:20:41Z"}
        assert write_hook_state(runner, pane_id, state) is True
        read_back = read_hook_state(runner, pane_id)
        assert read_back is not None
        assert read_back["event"] == "Notification"

    def test_read_hook_missing(self, hook_server):
        sock, pane_id = hook_server
        runner = CommandRunner(socket_path=sock)
        assert read_hook_state(runner, pane_id) is None

    def test_hook_overwrite(self, hook_server):
        sock, pane_id = hook_server
        runner = CommandRunner(socket_path=sock)
        write_hook_state(runner, pane_id, {"event": "SubagentStart", "ts": "t1"})
        write_hook_state(runner, pane_id, {"event": "SubagentStop", "ts": "t2"})
        state = read_hook_state(runner, pane_id)
        assert state["event"] == "SubagentStop"


class TestHookStateInDetection:
    def test_hook_state_in_agent_info(self, hook_server):
        """Managed pane with hook state should have it in AgentInfo."""
        from tmux_agents.config import TmuxAgentsConfig
        from tmux_agents.services.inventory_service import all_panes, get_inventory
        from tmux_agents.tmux.metadata_store import write_pane_metadata

        sock, pane_id = hook_server
        runner = CommandRunner(socket_path=sock)

        write_pane_metadata(
            runner,
            pane_id,
            {"schema_version": 1, "managed": True, "agent_kind": "claude", "profile": "claude"},
        )
        write_hook_state(runner, pane_id, {"event": "Notification", "ts": "t1"})

        config = TmuxAgentsConfig(extra_socket_paths=[sock])
        inventory = get_inventory(config)
        panes = [
            p
            for p in all_panes(inventory)
            if p.ref.pane and p.ref.pane.id == pane_id and p.ref.server.socket_path == sock
        ]
        assert len(panes) == 1
        assert panes[0].agent.hook_state is not None
        assert panes[0].agent.hook_state["event"] == "Notification"

    def test_hook_state_none_when_not_set(self, hook_server):
        """Managed pane without hook data should have hook_state=None."""
        from tmux_agents.config import TmuxAgentsConfig
        from tmux_agents.services.inventory_service import all_panes, get_inventory
        from tmux_agents.tmux.metadata_store import write_pane_metadata

        sock, pane_id = hook_server
        runner = CommandRunner(socket_path=sock)

        write_pane_metadata(
            runner,
            pane_id,
            {"schema_version": 1, "managed": True, "agent_kind": "claude", "profile": "claude"},
        )

        config = TmuxAgentsConfig(extra_socket_paths=[sock])
        inventory = get_inventory(config)
        panes = [
            p
            for p in all_panes(inventory)
            if p.ref.pane and p.ref.pane.id == pane_id and p.ref.server.socket_path == sock
        ]
        assert len(panes) == 1
        assert panes[0].agent.hook_state is None


# -- CLI tests ---------------------------------------------------------------


class TestHooksCli:
    def test_hooks_help(self):
        from click.testing import CliRunner

        from tmux_agents.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["hooks", "--help"])
        assert result.exit_code == 0
        assert "generate" in result.output
        assert "status" in result.output

    def test_hooks_generate_json(self):
        from click.testing import CliRunner

        from tmux_agents.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["hooks", "generate"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "hooks" in data

    def test_hooks_status_help(self):
        from click.testing import CliRunner

        from tmux_agents.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["hooks", "status", "--help"])
        assert result.exit_code == 0
        assert "--pane" in result.output

    def test_hooks_status_missing_pane(self):
        from click.testing import CliRunner

        from tmux_agents.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["hooks", "status", "--pane", "%99999"])
        assert result.exit_code != 0
