"""Tests for tmux command runner: version parsing, command building, and live execution."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tmux_agents.errors import TmuxNotFoundError, TmuxVersionError
from tmux_agents.tmux.command_runner import (
    CommandRunner,
    TmuxVersion,
    check_version,
    parse_version,
)

# -- Version parsing (pure unit tests) --------------------------------------


class TestParseVersion:
    def test_standard(self):
        v = parse_version("tmux 3.4")
        assert v.major == 3
        assert v.minor == 4
        assert v.patch == ""

    def test_with_patch(self):
        v = parse_version("tmux 3.2a")
        assert v.major == 3
        assert v.minor == 2
        assert v.patch == "a"

    def test_next_prefix(self):
        v = parse_version("tmux next-3.5")
        assert v.major == 3
        assert v.minor == 5

    def test_raw_preserved(self):
        v = parse_version("tmux 3.4")
        assert v.raw == "tmux 3.4"

    def test_garbage_raises(self):
        with pytest.raises(TmuxVersionError):
            parse_version("not a version")


class TestTmuxVersionComparison:
    def test_ge_equal(self):
        v = TmuxVersion(major=3, minor=2, patch="a")
        assert v >= (3, 2)

    def test_ge_higher(self):
        v = TmuxVersion(major=3, minor=4)
        assert v >= (3, 2)

    def test_lt_lower(self):
        v = TmuxVersion(major=3, minor=1)
        assert v < (3, 2)

    def test_lt_major(self):
        v = TmuxVersion(major=2, minor=9)
        assert v < (3, 2)

    def test_ge_higher_major(self):
        v = TmuxVersion(major=4, minor=0)
        assert v >= (3, 2)

    def test_str(self):
        v = TmuxVersion(major=3, minor=2, patch="a", raw="tmux 3.2a")
        assert str(v) == "tmux 3.2a"

    def test_str_no_raw(self):
        v = TmuxVersion(major=3, minor=4)
        assert str(v) == "3.4"


# -- CommandRunner construction (unit tests) ---------------------------------


class TestCommandRunnerInit:
    def test_default_no_socket(self):
        r = CommandRunner()
        assert r.socket_name is None
        assert r.socket_path is None

    def test_socket_name(self):
        r = CommandRunner(socket_name="work")
        assert r.socket_name == "work"

    def test_socket_path(self):
        r = CommandRunner(socket_path="/custom/sock")
        assert r.socket_path == "/custom/sock"

    def test_both_raises(self):
        with pytest.raises(ValueError, match="not both"):
            CommandRunner(socket_name="a", socket_path="/b")


class TestBaseArgs:
    def test_bare(self):
        r = CommandRunner(tmux_bin="/usr/bin/tmux")
        assert r._base_args() == ["/usr/bin/tmux"]

    def test_no_start(self):
        r = CommandRunner(tmux_bin="/usr/bin/tmux")
        assert r._base_args(no_start=True) == ["/usr/bin/tmux", "-N"]

    def test_socket_name(self):
        r = CommandRunner(socket_name="work", tmux_bin="/usr/bin/tmux")
        assert r._base_args() == ["/usr/bin/tmux", "-L", "work"]

    def test_socket_path(self):
        r = CommandRunner(socket_path="/s", tmux_bin="/usr/bin/tmux")
        assert r._base_args() == ["/usr/bin/tmux", "-S", "/s"]

    def test_combined(self):
        r = CommandRunner(socket_name="work", tmux_bin="/usr/bin/tmux")
        args = r._base_args(no_start=True)
        assert args == ["/usr/bin/tmux", "-N", "-L", "work"]


# -- Binary resolution -------------------------------------------------------


class TestTmuxBinResolution:
    def setup_method(self):
        CommandRunner._BINARY_CACHE = None

    def teardown_method(self):
        CommandRunner._BINARY_CACHE = None

    def test_explicit_bin(self):
        r = CommandRunner(tmux_bin="/my/tmux")
        assert r.tmux_bin == "/my/tmux"

    def test_which_not_found(self):
        with patch("tmux_agents.tmux.command_runner.shutil.which", return_value=None):
            r = CommandRunner()
            with pytest.raises(TmuxNotFoundError):
                _ = r.tmux_bin

    def test_which_found(self):
        with patch(
            "tmux_agents.tmux.command_runner.shutil.which",
            return_value="/usr/local/bin/tmux",
        ):
            r = CommandRunner()
            assert r.tmux_bin == "/usr/local/bin/tmux"


# -- Live tmux tests (require tmux installed) --------------------------------


@pytest.fixture
def tmux_bin():
    """Skip tests if tmux is not installed."""
    import shutil

    path = shutil.which("tmux")
    if path is None:
        pytest.skip("tmux not installed")
    return path


class TestLiveVersionProbe:
    def test_check_version_succeeds(self, tmux_bin):
        CommandRunner._BINARY_CACHE = None
        version = check_version()
        assert version.major >= 3
        assert version >= (3, 2)

    def test_bad_version_raises(self):
        """Simulate a too-old tmux by mocking the run output."""
        runner = CommandRunner(tmux_bin="/usr/bin/tmux")
        with patch.object(runner, "run") as mock_run:
            from tmux_agents.tmux.command_runner import TmuxResult

            mock_run.return_value = TmuxResult(stdout=["tmux 2.9"], stderr=[], returncode=0)
            with pytest.raises(TmuxVersionError, match="below minimum"):
                check_version(runner)


class TestLiveCommandExecution:
    def test_run_version(self, tmux_bin):
        r = CommandRunner()
        result = r.run("-V")
        assert result.ok
        assert any("tmux" in line for line in result.stdout)

    def test_no_start_nonexistent_socket(self, tmux_bin):
        """Running with -N on a socket that doesn't exist should not create a server."""
        r = CommandRunner(socket_name="tmux_agents_test_nonexistent_xyzzy")
        result = r.run("list-sessions", no_start=True)
        assert not result.ok  # server doesn't exist, no error just non-zero exit
