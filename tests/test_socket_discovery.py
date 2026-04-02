"""Tests for socket discovery: default, scanned, and configured sockets."""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from tmux_agents.config import TmuxAgentsConfig
from tmux_agents.tmux.socket_discovery import (
    _socket_dir,
    discover_sockets,
)

# -- Unit tests for socket_dir ----------------------------------------------


class TestSocketDir:
    def test_default_uses_tmp(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("TMUX_TMPDIR", None)
            d = _socket_dir()
            assert d == Path(f"/tmp/tmux-{os.geteuid()}")

    def test_respects_tmux_tmpdir(self):
        with patch.dict(os.environ, {"TMUX_TMPDIR": "/custom/tmp"}):
            d = _socket_dir()
            assert d == Path(f"/custom/tmp/tmux-{os.geteuid()}")


# -- Integration tests (require tmux) ----------------------------------------
# NOTE: Unix sockets have a ~104-char path limit on macOS, so we use /tmp
# with short names instead of pytest's tmp_path.


@pytest.fixture
def tmux_bin():
    """Skip if tmux is not installed."""
    import shutil

    path = shutil.which("tmux")
    if not path:
        pytest.skip("tmux not installed")
    return path


@pytest.fixture
def short_tmp():
    """Create a short temp dir under /tmp to avoid socket path length limits."""
    d = tempfile.mkdtemp(prefix="ta-", dir="/tmp")
    yield Path(d)
    # Best-effort cleanup
    import shutil

    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def isolated_socket(short_tmp, tmux_bin):
    """Create an isolated tmux server on a unique socket, yield the path, then kill it."""
    sock = str(short_tmp / "s")
    subprocess.run(
        [tmux_bin, "-S", sock, "new-session", "-d", "-s", "test"],
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
def two_isolated_sockets(short_tmp, tmux_bin):
    """Create two isolated tmux servers on unique sockets."""
    socks = []
    for name in ["a", "b"]:
        sock = str(short_tmp / name)
        subprocess.run(
            [tmux_bin, "-S", sock, "new-session", "-d", "-s", f"s-{name}"],
            check=True,
            timeout=10,
        )
        socks.append(sock)
    time.sleep(0.3)
    yield socks
    for sock in socks:
        subprocess.run(
            [tmux_bin, "-S", sock, "kill-server"],
            timeout=5,
            capture_output=True,
        )


class TestDiscoverWithIsolatedSockets:
    def test_finds_configured_socket(self, isolated_socket):
        """A socket in extra_socket_paths should be discovered as alive."""
        config = TmuxAgentsConfig(extra_socket_paths=[isolated_socket])
        results = discover_sockets(config)
        configured = [d for d in results if d.source == "config"]
        assert len(configured) >= 1
        match = next(d for d in configured if d.ref.socket_path == isolated_socket)
        assert match.alive is True

    def test_finds_multiple_configured_sockets(self, two_isolated_sockets):
        """Both configured sockets should be discovered."""
        config = TmuxAgentsConfig(extra_socket_paths=two_isolated_sockets)
        results = discover_sockets(config)
        configured_paths = {d.ref.socket_path for d in results if d.source == "config"}
        for sock in two_isolated_sockets:
            assert sock in configured_paths

    def test_dead_socket_excluded_by_default(self, short_tmp, tmux_bin):
        """A socket file that exists but whose server is dead should not appear."""
        sock = str(short_tmp / "d")
        subprocess.run(
            [tmux_bin, "-S", sock, "new-session", "-d", "-s", "tmp"],
            check=True,
            timeout=10,
        )
        time.sleep(0.2)
        subprocess.run(
            [tmux_bin, "-S", sock, "kill-server"],
            timeout=5,
            capture_output=True,
        )
        time.sleep(0.2)
        config = TmuxAgentsConfig(extra_socket_paths=[sock])
        results = discover_sockets(config, include_dead=False)
        dead_matches = [d for d in results if d.ref.socket_path == sock]
        assert all(d.alive for d in dead_matches) or len(dead_matches) == 0

    def test_dead_socket_included_when_requested(self, short_tmp, tmux_bin):
        """include_dead=True should include sockets whose file exists but server is dead."""
        sock = str(short_tmp / "d2")
        subprocess.run(
            [tmux_bin, "-S", sock, "new-session", "-d", "-s", "tmp"],
            check=True,
            timeout=10,
        )
        time.sleep(0.2)
        subprocess.run(
            [tmux_bin, "-S", sock, "kill-server"],
            timeout=5,
            capture_output=True,
        )
        time.sleep(0.2)
        # Socket file may or may not still exist after kill-server
        if Path(sock).exists():
            config = TmuxAgentsConfig(extra_socket_paths=[sock])
            results = discover_sockets(config, include_dead=True)
            dead_matches = [d for d in results if d.ref.socket_path == sock]
            assert len(dead_matches) == 1
            assert dead_matches[0].alive is False

    def test_nonexistent_configured_path_skipped(self):
        """A configured path that doesn't exist should be silently skipped."""
        config = TmuxAgentsConfig(extra_socket_paths=["/nonexistent/tmux-agents-test-socket"])
        results = discover_sockets(config)
        assert not any(
            d.ref.socket_path == "/nonexistent/tmux-agents-test-socket" for d in results
        )

    def test_dedup_by_path(self, isolated_socket):
        """The same socket path listed twice should only appear once."""
        config = TmuxAgentsConfig(extra_socket_paths=[isolated_socket, isolated_socket])
        results = discover_sockets(config)
        matches = [d for d in results if d.ref.socket_path == isolated_socket]
        assert len(matches) == 1


class TestNoServerCreation:
    def test_discovery_does_not_start_servers(self, tmux_bin):
        """Probing a non-existent named socket must not create a tmux server.

        This is the critical -N flag test from M1 exit criteria.
        """
        fake_name = "tmux_agents_no_create_test"
        fake_path = _socket_dir() / fake_name
        assert not fake_path.exists(), f"Socket {fake_path} unexpectedly exists"
        config = TmuxAgentsConfig(extra_socket_names=[fake_name])
        discover_sockets(config)
        assert not fake_path.exists(), f"Discovery created server at {fake_path}!"
