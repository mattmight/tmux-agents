"""Snapshot tests for error envelope."""

from __future__ import annotations

from tmux_agents.errors import (
    ErrorCode,
    ErrorContext,
    ErrorEnvelope,
    TmuxAgentsError,
)


class TestErrorEnvelope:
    def test_schema_snapshot(self, snapshot):
        assert ErrorEnvelope.model_json_schema() == snapshot

    def test_minimal(self):
        env = ErrorEnvelope(code=ErrorCode.UNKNOWN, message="something went wrong")
        assert env.details == {}
        assert env.context is None

    def test_with_context(self, sample_target_ref):
        env = ErrorEnvelope(
            code=ErrorCode.PANE_NOT_FOUND,
            message="Pane %99 not found",
            context=ErrorContext(operation="capture_pane", target=sample_target_ref),
        )
        assert env.context is not None
        assert env.context.operation == "capture_pane"

    def test_exception_carries_envelope(self):
        env = ErrorEnvelope(code=ErrorCode.TMUX_NOT_FOUND, message="tmux not on PATH")
        exc = TmuxAgentsError(env)
        assert exc.envelope is env
        assert str(exc) == "tmux not on PATH"
