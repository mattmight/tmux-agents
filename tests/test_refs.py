"""Snapshot tests for canonical ref models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tmux_agents.refs import PaneRef, ServerRef, SessionRef, TargetRef, WindowRef


class TestServerRef:
    def test_schema_snapshot(self, snapshot):
        assert ServerRef.model_json_schema() == snapshot

    def test_round_trip(self):
        ref = ServerRef(socket_path="/tmp/tmux-501/default", socket_name="default")
        assert ServerRef.model_validate_json(ref.model_dump_json()) == ref

    def test_frozen(self):
        ref = ServerRef(socket_path="/tmp/tmux-501/default", socket_name="default")
        with pytest.raises(ValidationError):
            ref.socket_path = "/other"  # type: ignore[misc]


class TestSessionRef:
    def test_schema_snapshot(self, snapshot):
        assert SessionRef.model_json_schema() == snapshot

    def test_round_trip(self):
        ref = SessionRef(id="$3", name="auth-refactor")
        assert SessionRef.model_validate_json(ref.model_dump_json()) == ref


class TestWindowRef:
    def test_schema_snapshot(self, snapshot):
        assert WindowRef.model_json_schema() == snapshot

    def test_round_trip(self):
        ref = WindowRef(id="@7", name="main", index=0)
        assert WindowRef.model_validate_json(ref.model_dump_json()) == ref


class TestPaneRef:
    def test_schema_snapshot(self, snapshot):
        assert PaneRef.model_json_schema() == snapshot

    def test_round_trip(self):
        ref = PaneRef(id="%12", index=0)
        assert PaneRef.model_validate_json(ref.model_dump_json()) == ref


class TestTargetRef:
    def test_schema_snapshot(self, snapshot):
        assert TargetRef.model_json_schema() == snapshot

    def test_full_ref_round_trip(self, sample_target_ref):
        json_str = sample_target_ref.model_dump_json()
        restored = TargetRef.model_validate_json(json_str)
        assert restored == sample_target_ref

    def test_server_only_ref(self, sample_server_ref):
        ref = TargetRef(server=sample_server_ref)
        assert ref.session is None
        assert ref.window is None
        assert ref.pane is None

    def test_canonical_json_shape(self, sample_target_ref, snapshot):
        """The exact JSON shape shown in PLAN.md section 5."""
        assert sample_target_ref.model_dump() == snapshot
