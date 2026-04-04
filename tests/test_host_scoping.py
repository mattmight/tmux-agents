"""Tests for host-scoped pane state in capture service (M14)."""

from __future__ import annotations

from tmux_agents.services.capture_service import (
    _get_state,
    _pane_states,
    _state_key,
    reset_pane_state,
)


class TestStateKey:
    def test_local_key(self):
        assert _state_key("%0") == ("local", "%0")

    def test_local_explicit_none(self):
        assert _state_key("%0", None) == ("local", "%0")

    def test_remote_key(self):
        assert _state_key("%0", "enterprise-a") == ("enterprise-a", "%0")

    def test_different_hosts_different_keys(self):
        assert _state_key("%0", None) != _state_key("%0", "enterprise-a")

    def test_same_host_same_pane(self):
        assert _state_key("%5", "enterprise-a") == _state_key("%5", "enterprise-a")


class TestHostScopedState:
    def setup_method(self):
        _pane_states.clear()

    def test_independent_state_per_host(self):
        local_state = _get_state("%0")
        remote_state = _get_state("%0", "enterprise-a")

        local_state.seq = 5
        local_state.last_content = "local content"

        remote_state.seq = 10
        remote_state.last_content = "remote content"

        assert local_state.seq == 5
        assert remote_state.seq == 10
        assert local_state.last_content != remote_state.last_content

    def test_same_host_returns_same_state(self):
        s1 = _get_state("%0", "enterprise-a")
        s1.seq = 42
        s2 = _get_state("%0", "enterprise-a")
        assert s2.seq == 42

    def test_reset_local_only(self):
        _get_state("%0").seq = 1
        _get_state("%0", "enterprise-a").seq = 2

        reset_pane_state("%0")

        # Local state should be gone
        assert ("local", "%0") not in _pane_states
        # Remote state should remain
        assert ("enterprise-a", "%0") in _pane_states
        assert _pane_states[("enterprise-a", "%0")].seq == 2

    def test_reset_remote_only(self):
        _get_state("%0").seq = 1
        _get_state("%0", "enterprise-a").seq = 2

        reset_pane_state("%0", "enterprise-a")

        assert ("local", "%0") in _pane_states
        assert ("enterprise-a", "%0") not in _pane_states

    def test_multiple_remote_hosts(self):
        s_a = _get_state("%0", "host-a")
        s_b = _get_state("%0", "host-b")
        s_local = _get_state("%0")

        s_a.seq = 1
        s_b.seq = 2
        s_local.seq = 3

        assert _get_state("%0", "host-a").seq == 1
        assert _get_state("%0", "host-b").seq == 2
        assert _get_state("%0").seq == 3
