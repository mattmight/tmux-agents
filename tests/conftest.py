"""Shared test fixtures for tmux-agents."""

from __future__ import annotations

import pytest

from tmux_agents.refs import PaneRef, ServerRef, SessionRef, TargetRef, WindowRef


@pytest.fixture
def sample_server_ref() -> ServerRef:
    return ServerRef(socket_path="/tmp/tmux-501/default", socket_name="default")


@pytest.fixture
def sample_target_ref(sample_server_ref: ServerRef) -> TargetRef:
    return TargetRef(
        server=sample_server_ref,
        session=SessionRef(id="$3", name="auth-refactor"),
        window=WindowRef(id="@7", name="main", index=0),
        pane=PaneRef(id="%12", index=0),
    )
