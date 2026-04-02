"""Tests for capture service: tail, history, screen modes, and delta reads."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

from tmux_agents.models import CaptureMode, CaptureResult, DeltaResult, ScreenTarget
from tmux_agents.services.capture_service import (
    _compute_delta,
    _pane_states,
    capture_pane,
    read_pane_delta,
    reset_pane_state,
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
def capture_server(short_tmp, tmux_bin):
    """Server with a pane running shell. We send known text into it."""
    sock = str(short_tmp / "c")
    subprocess.run(
        [tmux_bin, "-S", sock, "new-session", "-d", "-s", "cap", "-n", "main"],
        check=True,
        timeout=10,
    )
    time.sleep(0.3)

    # Get pane ID
    result = subprocess.run(
        [tmux_bin, "-S", sock, "list-panes", "-a", "-F", "#{pane_id}"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    pane_id = result.stdout.strip().split("\n")[0]

    # Send some known text
    for line in ["hello world", "line two", "line three"]:
        subprocess.run(
            [tmux_bin, "-S", sock, "send-keys", "-t", pane_id, f"echo {line}", "Enter"],
            check=True,
            timeout=5,
        )
    time.sleep(0.3)

    yield sock, pane_id
    # Clear state
    reset_pane_state(pane_id)
    subprocess.run(
        [tmux_bin, "-S", sock, "kill-server"],
        timeout=5,
        capture_output=True,
    )


# -- Unit tests: delta diff algorithm ----------------------------------------


class TestComputeDelta:
    def test_identical(self):
        assert _compute_delta("hello\nworld", "hello\nworld") == ""

    def test_empty_old(self):
        assert _compute_delta("", "new content") == "new content"

    def test_appended_lines(self):
        old = "line1\nline2"
        new = "line1\nline2\nline3\nline4"
        delta = _compute_delta(old, new)
        assert delta == "line3\nline4"

    def test_prefix_match(self):
        old = "abc"
        new = "abc\ndef"
        delta = _compute_delta(old, new)
        assert "def" in delta

    def test_completely_different(self):
        assert _compute_delta("old stuff", "totally new") is None

    def test_overlap_detection(self):
        old = "a\nb\nc"
        new = "b\nc\nd\ne"
        delta = _compute_delta(old, new)
        assert delta == "d\ne"


class TestPaneState:
    def test_get_state_creates(self):
        pane_id = "%test_state"
        reset_pane_state(pane_id)
        from tmux_agents.services.capture_service import _get_state

        state = _get_state(pane_id)
        assert state.seq == 0
        assert state.last_content == ""
        reset_pane_state(pane_id)

    def test_reset_clears(self):
        pane_id = "%test_reset"
        from tmux_agents.services.capture_service import _get_state

        state = _get_state(pane_id)
        state.seq = 5
        state.last_content = "data"
        reset_pane_state(pane_id)
        assert pane_id not in _pane_states


# -- Integration: tail capture -----------------------------------------------


class TestCaptureTail:
    def test_captures_content(self, capture_server):
        sock, pane_id = capture_server
        result = capture_pane(pane_id, mode=CaptureMode.TAIL, lines=50, socket_path=sock)
        assert isinstance(result, CaptureResult)
        assert result.pane_id == pane_id
        assert result.mode == CaptureMode.TAIL
        assert result.screen_used == ScreenTarget.PRIMARY
        assert result.line_count > 0
        assert "hello world" in result.content

    def test_small_tail(self, capture_server):
        """Tail with a small line count still returns content."""
        sock, pane_id = capture_server
        result = capture_pane(pane_id, mode=CaptureMode.TAIL, lines=5, socket_path=sock)
        assert result.line_count > 0

    def test_seq_increments(self, capture_server):
        sock, pane_id = capture_server
        r1 = capture_pane(pane_id, mode=CaptureMode.TAIL, lines=50, socket_path=sock)
        r2 = capture_pane(pane_id, mode=CaptureMode.TAIL, lines=50, socket_path=sock)
        assert r2.seq == r1.seq + 1


# -- Integration: history capture --------------------------------------------


class TestCaptureHistory:
    def test_bounded_slice(self, capture_server):
        sock, pane_id = capture_server
        result = capture_pane(pane_id, mode=CaptureMode.HISTORY, start=0, end=2, socket_path=sock)
        assert result.mode == CaptureMode.HISTORY
        assert result.line_count > 0


# -- Integration: screen capture ---------------------------------------------


class TestCaptureScreen:
    def test_auto_on_shell(self, capture_server):
        """Shell pane (no alternate screen) → auto should use primary."""
        sock, pane_id = capture_server
        result = capture_pane(
            pane_id, mode=CaptureMode.SCREEN, screen=ScreenTarget.AUTO, socket_path=sock
        )
        assert result.screen_used == ScreenTarget.PRIMARY
        assert result.line_count > 0

    def test_primary_explicit(self, capture_server):
        sock, pane_id = capture_server
        result = capture_pane(
            pane_id,
            mode=CaptureMode.SCREEN,
            screen=ScreenTarget.PRIMARY,
            socket_path=sock,
        )
        assert result.screen_used == ScreenTarget.PRIMARY

    def test_alternate_on_shell(self, capture_server):
        """Alternate capture on a shell pane gives empty content (no TUI)."""
        sock, pane_id = capture_server
        result = capture_pane(
            pane_id,
            mode=CaptureMode.SCREEN,
            screen=ScreenTarget.ALTERNATE,
            socket_path=sock,
        )
        assert result.screen_used == ScreenTarget.ALTERNATE
        # Shell is not in alternate screen, content is empty
        assert not result.content.strip()


class TestCaptureAlternateScreen:
    def test_less_uses_alternate(self, short_tmp, tmux_bin):
        """A pane running `less` should be in alternate screen mode."""
        sock = str(short_tmp / "alt")
        subprocess.run(
            [
                tmux_bin,
                "-S",
                sock,
                "new-session",
                "-d",
                "-s",
                "alt",
                "-n",
                "main",
                "less /etc/hosts",
            ],
            check=True,
            timeout=10,
        )
        time.sleep(0.5)

        result = subprocess.run(
            [tmux_bin, "-S", sock, "list-panes", "-a", "-F", "#{pane_id}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        pane_id = result.stdout.strip().split("\n")[0]

        try:
            cap = capture_pane(
                pane_id,
                mode=CaptureMode.SCREEN,
                screen=ScreenTarget.AUTO,
                socket_path=sock,
            )
            # less should use alternate screen; auto detects and captures it.
            # In detached sessions, alternate capture may return empty causing
            # fallback to primary — both are acceptable.
            assert cap.line_count > 0
            if cap.screen_used == ScreenTarget.ALTERNATE:
                # If we got alternate, it should have content
                assert "localhost" in cap.content.lower() or "host" in cap.content.lower()
        finally:
            reset_pane_state(pane_id)
            subprocess.run(
                [tmux_bin, "-S", sock, "kill-server"],
                timeout=5,
                capture_output=True,
            )


# -- Integration: delta reads ------------------------------------------------


class TestReadPaneDelta:
    def test_initial_read(self, capture_server):
        sock, pane_id = capture_server
        result = read_pane_delta(pane_id, after_seq=0, socket_path=sock)
        assert isinstance(result, DeltaResult)
        assert result.from_seq == 0
        assert result.to_seq > 0
        assert result.reset_required is False
        assert result.total_new_lines > 0
        assert len(result.chunks) > 0

    def test_delta_after_new_output(self, capture_server, tmux_bin):
        sock, pane_id = capture_server

        # Initial capture
        r1 = read_pane_delta(pane_id, after_seq=0, socket_path=sock)
        seq1 = r1.to_seq

        # Send new text
        subprocess.run(
            [tmux_bin, "-S", sock, "send-keys", "-t", pane_id, "echo DELTA_MARKER", "Enter"],
            check=True,
            timeout=5,
        )
        time.sleep(0.3)

        # Delta read
        r2 = read_pane_delta(pane_id, after_seq=seq1, socket_path=sock)
        assert r2.from_seq == seq1
        assert r2.to_seq > seq1
        assert r2.reset_required is False
        # Should contain the new output
        all_content = "".join(c.content for c in r2.chunks)
        assert "DELTA_MARKER" in all_content

    def test_no_change_empty_delta(self, capture_server):
        sock, pane_id = capture_server

        r1 = read_pane_delta(pane_id, after_seq=0, socket_path=sock)
        r2 = read_pane_delta(pane_id, after_seq=r1.to_seq, socket_path=sock)
        assert r2.total_new_lines == 0 or r2.chunks == []

    def test_desync_recovery(self, capture_server):
        """Out-of-sync seq should return reset_required=True."""
        sock, pane_id = capture_server
        # Initial read
        read_pane_delta(pane_id, after_seq=0, socket_path=sock)
        # Use a bogus seq that doesn't match
        r = read_pane_delta(pane_id, after_seq=9999, socket_path=sock)
        assert r.reset_required is True
        assert r.total_new_lines > 0

    def test_seq_monotonic(self, capture_server):
        sock, pane_id = capture_server
        seqs = []
        for _ in range(3):
            r = capture_pane(pane_id, mode=CaptureMode.TAIL, lines=50, socket_path=sock)
            seqs.append(r.seq)
        assert seqs == sorted(seqs)
        assert len(set(seqs)) == 3  # all unique


# -- CLI tests ---------------------------------------------------------------


class TestCaptureCli:
    def test_capture_help(self):
        from click.testing import CliRunner

        from tmux_agents.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["capture", "--help"])
        assert result.exit_code == 0
        assert "--pane" in result.output
        assert "--lines" in result.output
        assert "--screen" in result.output

    def test_delta_help(self):
        from click.testing import CliRunner

        from tmux_agents.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["delta", "--help"])
        assert result.exit_code == 0
        assert "--pane" in result.output
        assert "--after-seq" in result.output

    def test_capture_json(self, capture_server):
        import json

        from click.testing import CliRunner

        from tmux_agents.cli.main import cli

        _sock, pane_id = capture_server
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "capture", "--pane", pane_id])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["pane_id"] == pane_id
        assert "content" in data
        assert "seq" in data

    def test_delta_json(self, capture_server):
        import json

        from click.testing import CliRunner

        from tmux_agents.cli.main import cli

        _sock, pane_id = capture_server
        runner = CliRunner()
        result = runner.invoke(cli, ["--json", "delta", "--pane", pane_id, "--after-seq", "0"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "from_seq" in data
        assert "to_seq" in data
        assert "chunks" in data


# -- Dead pane tests ---------------------------------------------------------


@pytest.fixture
def dead_pane_server(short_tmp, tmux_bin):
    """Server with a dead pane (process exited, remain-on-exit keeps pane visible)."""
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

    # Kill the shell to make the pane dead
    subprocess.run(["kill", "-9", pid], timeout=5)
    time.sleep(0.5)

    yield sock, pane_id
    subprocess.run(
        [tmux_bin, "-S", sock, "kill-server"],
        timeout=5,
        capture_output=True,
    )


class TestDeadPaneCapture:
    def test_capture_dead_pane_raises(self, dead_pane_server):
        from tmux_agents.errors import PaneDeadError

        sock, pane_id = dead_pane_server
        with pytest.raises(PaneDeadError):
            capture_pane(pane_id, mode=CaptureMode.TAIL, lines=50, socket_path=sock)

    def test_delta_dead_pane_raises(self, dead_pane_server):
        from tmux_agents.errors import PaneDeadError

        sock, pane_id = dead_pane_server
        with pytest.raises(PaneDeadError):
            read_pane_delta(pane_id, after_seq=0, socket_path=sock)
