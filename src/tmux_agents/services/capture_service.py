"""Capture service: bounded pane output capture and delta reads.

Provides capture_pane() for snapshot reads and read_pane_delta() for
incremental reads. Uses tmux capture-pane with -p/-S/-E/-a/-J flags.
Alternate-screen detection uses #{alternate_on} format variable.

Delta tracking uses in-memory per-pane state with monotonic seq counters.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from tmux_agents.config import TmuxAgentsConfig
from tmux_agents.errors import (
    ErrorCode,
    ErrorContext,
    ErrorEnvelope,
    TmuxAgentsError,
)
from tmux_agents.logging import get_logger
from tmux_agents.models import (
    CaptureChunk,
    CaptureMode,
    CaptureResult,
    DeltaResult,
    PatternMatch,
    ScreenTarget,
)
from tmux_agents.tmux.command_runner import CommandRunner

log = get_logger(__name__)


# -- In-memory pane state tracking -------------------------------------------


@dataclass
class _PaneState:
    seq: int = 0
    last_content: str = ""
    prev_content: str = ""  # content before last capture, for delta diffs
    last_line_count: int = 0


_pane_states: dict[str, _PaneState] = {}


def _get_state(pane_id: str) -> _PaneState:
    if pane_id not in _pane_states:
        _pane_states[pane_id] = _PaneState()
    return _pane_states[pane_id]


def reset_pane_state(pane_id: str) -> None:
    """Remove tracked state for a pane."""
    _pane_states.pop(pane_id, None)


# -- Runner resolution -------------------------------------------------------


def _get_runner(
    pane_id: str,
    socket_path: str | None = None,
    config: TmuxAgentsConfig | None = None,
) -> CommandRunner:
    if socket_path:
        return CommandRunner(socket_path=socket_path)
    from tmux_agents.services.inventory_service import inspect_pane

    snap = inspect_pane(pane_id, config)
    return CommandRunner(socket_path=snap.ref.server.socket_path)


# -- Alternate screen detection ----------------------------------------------


def _is_alternate_screen(runner: CommandRunner, pane_id: str) -> bool:
    result = runner.run("display-message", "-p", "-t", pane_id, "#{alternate_on}", no_start=True)
    if not result.ok or not result.stdout:
        return False
    return result.stdout[0].strip() == "1"


# -- Core capture logic ------------------------------------------------------


def _do_capture(
    runner: CommandRunner,
    pane_id: str,
    *,
    start: int | str | None = None,
    end: int | str | None = None,
    alternate: bool = False,
    join_wrapped: bool = True,
) -> str:
    args: list[str] = ["-p", "-t", pane_id]
    if alternate:
        args.append("-a")
    else:
        if start is not None:
            args.extend(["-S", str(start)])
        if end is not None:
            args.extend(["-E", str(end)])
    if join_wrapped:
        args.append("-J")

    result = runner.run("capture-pane", *args, no_start=True)
    if not result.ok:
        # "no alternate screen" is expected when -a is used on a non-alternate pane
        stderr_text = " ".join(result.stderr)
        if alternate and "no alternate screen" in stderr_text:
            return ""
        raise TmuxAgentsError(
            ErrorEnvelope(
                code=ErrorCode.CAPTURE_FAILED,
                message=f"capture-pane failed for {pane_id}: {stderr_text}",
                details={"pane_id": pane_id, "stderr": result.stderr},
                context=ErrorContext(operation="capture_pane"),
            )
        )
    return result.output


def _capture_screen(
    runner: CommandRunner,
    pane_id: str,
    *,
    screen: ScreenTarget,
    lines: int,
    join_wrapped: bool,
) -> tuple[str, ScreenTarget]:
    if screen == ScreenTarget.ALTERNATE:
        content = _do_capture(runner, pane_id, alternate=True, join_wrapped=join_wrapped)
        return content, ScreenTarget.ALTERNATE

    if screen == ScreenTarget.PRIMARY:
        content = _do_capture(runner, pane_id, start=f"-{lines}", join_wrapped=join_wrapped)
        return content, ScreenTarget.PRIMARY

    # AUTO: try alternate if pane is in alternate screen mode
    if _is_alternate_screen(runner, pane_id):
        content = _do_capture(runner, pane_id, alternate=True, join_wrapped=join_wrapped)
        if content.strip():
            return content, ScreenTarget.ALTERNATE

    content = _do_capture(runner, pane_id, start=f"-{lines}", join_wrapped=join_wrapped)
    return content, ScreenTarget.PRIMARY


# -- Public API: capture_pane ------------------------------------------------


def capture_pane(
    pane_id: str,
    *,
    mode: CaptureMode = CaptureMode.TAIL,
    lines: int = 200,
    start: int | None = None,
    end: int | None = None,
    screen: ScreenTarget = ScreenTarget.AUTO,
    join_wrapped: bool = True,
    socket_path: str | None = None,
    config: TmuxAgentsConfig | None = None,
) -> CaptureResult:
    """Capture output from a tmux pane."""
    runner = _get_runner(pane_id, socket_path, config)
    from tmux_agents.tmux.command_runner import check_pane_alive

    check_pane_alive(runner, pane_id)
    now = datetime.now(UTC)
    state = _get_state(pane_id)

    # Stash previous content for delta computation
    state.prev_content = state.last_content

    if mode == CaptureMode.TAIL:
        content = _do_capture(runner, pane_id, start=f"-{lines}", join_wrapped=join_wrapped)
        screen_used = ScreenTarget.PRIMARY
    elif mode == CaptureMode.HISTORY:
        content = _do_capture(runner, pane_id, start=start, end=end, join_wrapped=join_wrapped)
        screen_used = ScreenTarget.PRIMARY
    elif mode == CaptureMode.SCREEN:
        content, screen_used = _capture_screen(
            runner, pane_id, screen=screen, lines=lines, join_wrapped=join_wrapped
        )
    else:
        content = _do_capture(runner, pane_id, start=f"-{lines}", join_wrapped=join_wrapped)
        screen_used = ScreenTarget.PRIMARY

    content = content.rstrip("\n")
    content_lines = content.split("\n") if content else []
    line_count = len(content_lines)
    truncated = mode == CaptureMode.TAIL and line_count >= lines

    state.seq += 1
    state.last_content = content
    state.last_line_count = line_count

    return CaptureResult(
        pane_id=pane_id,
        mode=mode,
        screen_used=screen_used,
        content=content,
        line_count=line_count,
        truncated=truncated,
        seq=state.seq,
        timestamp=now,
    )


# -- Public API: read_pane_delta ---------------------------------------------


def read_pane_delta(
    pane_id: str,
    *,
    after_seq: int = 0,
    max_lines: int = 5000,
    screen: ScreenTarget = ScreenTarget.AUTO,
    socket_path: str | None = None,
    config: TmuxAgentsConfig | None = None,
) -> DeltaResult:
    """Read incremental output from a pane since a previous capture."""
    state = _get_state(pane_id)
    now = datetime.now(UTC)

    # Perform a fresh capture (this updates state and stashes prev_content)
    cap = capture_pane(
        pane_id,
        mode=CaptureMode.SCREEN,
        lines=max_lines,
        screen=screen,
        socket_path=socket_path,
        config=config,
    )

    new_content = cap.content
    new_seq = cap.seq

    # Initial read
    if after_seq == 0:
        return _make_delta(pane_id, 0, new_seq, new_content, reset=False, ts=now)

    # Desync: client's seq doesn't match our previous seq
    expected_prev = new_seq - 1
    if after_seq != expected_prev:
        return _make_delta(pane_id, after_seq, new_seq, new_content, reset=True, ts=now)

    # In sync — compute delta from prev_content to new content
    delta_text = _compute_delta(state.prev_content, new_content)

    if delta_text is None:
        # Content completely changed
        return _make_delta(pane_id, after_seq, new_seq, new_content, reset=True, ts=now)

    return _make_delta(pane_id, after_seq, new_seq, delta_text, reset=False, ts=now)


def _make_delta(
    pane_id: str,
    from_seq: int,
    to_seq: int,
    content: str,
    *,
    reset: bool,
    ts: datetime,
) -> DeltaResult:
    lines = content.split("\n") if content else []
    line_count = len(lines) if content else 0
    chunks = [CaptureChunk(content=content, line_count=line_count)] if content else []
    return DeltaResult(
        pane_id=pane_id,
        from_seq=from_seq,
        to_seq=to_seq,
        chunks=chunks,
        total_new_lines=line_count,
        reset_required=reset,
        timestamp=ts,
    )


def _compute_delta(old_content: str, new_content: str) -> str | None:
    """Compute appended delta. Returns None if content fully changed (reset)."""
    if old_content == new_content:
        return ""
    if not old_content:
        return new_content

    old_lines = old_content.split("\n")
    new_lines = new_content.split("\n")

    # Find overlap: trailing lines of old matching leading lines of new
    max_overlap = min(len(old_lines), len(new_lines))
    overlap = 0
    for candidate in range(max_overlap, 0, -1):
        if old_lines[-candidate:] == new_lines[:candidate]:
            overlap = candidate
            break

    if overlap > 0:
        delta_lines = new_lines[overlap:]
        return "\n".join(delta_lines) if delta_lines else ""

    # No overlap — check prefix match
    if new_content.startswith(old_content):
        return new_content[len(old_content) :].lstrip("\n")

    # Completely different
    return None


# -- Wait for pattern --------------------------------------------------------


def wait_for_pattern(
    pane_id: str,
    pattern: str,
    *,
    timeout_ms: int = 30000,
    poll_interval_ms: int = 500,
    screen: ScreenTarget = ScreenTarget.AUTO,
    socket_path: str | None = None,
    config: TmuxAgentsConfig | None = None,
) -> PatternMatch:
    """Poll pane output until a regex pattern matches or timeout expires.

    Args:
        pane_id: Tmux pane ID.
        pattern: Regular expression to match against captured output.
        timeout_ms: Maximum wait time in milliseconds.
        poll_interval_ms: Poll interval in milliseconds.
        screen: Screen target for captures.
        socket_path: Direct socket path.
        config: Config for discovery.

    Returns:
        PatternMatch with the matched text and line number.

    Raises:
        TmuxAgentsError: With PATTERN_TIMEOUT code if timeout expires.
    """
    import re
    import time

    from tmux_agents.errors import ErrorCode, ErrorContext, ErrorEnvelope, TmuxAgentsError

    compiled = re.compile(pattern)
    deadline = time.monotonic() + timeout_ms / 1000.0
    poll_sec = poll_interval_ms / 1000.0

    while True:
        cap = capture_pane(
            pane_id,
            mode=CaptureMode.SCREEN,
            screen=screen,
            socket_path=socket_path,
            config=config,
        )
        lines = cap.content.split("\n") if cap.content else []
        for i, line in enumerate(lines):
            m = compiled.search(line)
            if m:
                return PatternMatch(
                    pane_id=pane_id,
                    pattern=pattern,
                    matched_text=m.group(0),
                    line_number=i,
                    timestamp=datetime.now(UTC),
                )

        if time.monotonic() >= deadline:
            raise TmuxAgentsError(
                ErrorEnvelope(
                    code=ErrorCode.PATTERN_TIMEOUT,
                    message=f"Pattern '{pattern}' not found in pane {pane_id} "
                    f"within {timeout_ms}ms",
                    details={"pane_id": pane_id, "pattern": pattern},
                    context=ErrorContext(operation="wait_for_pattern"),
                )
            )

        time.sleep(poll_sec)
