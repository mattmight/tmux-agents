"""Snapshot tests for response models."""

from __future__ import annotations

from tmux_agents.models import (
    AgentInfo,
    Confidence,
    DetectionSource,
    InventorySnapshot,
    PaneDisplay,
    PaneRuntime,
    PaneSnapshot,
)


class TestPaneSnapshot:
    def test_schema_snapshot(self, snapshot):
        assert PaneSnapshot.model_json_schema() == snapshot

    def test_defaults(self, sample_target_ref):
        snap = PaneSnapshot(
            ref=sample_target_ref,
            display=PaneDisplay(index=0, title=""),
            runtime=PaneRuntime(),
        )
        assert snap.agent.confidence == Confidence.NONE
        assert snap.agent.managed is False


class TestAgentInfo:
    def test_schema_snapshot(self, snapshot):
        assert AgentInfo.model_json_schema() == snapshot

    def test_claude_detection(self):
        info = AgentInfo(
            detected_kind="claude",
            confidence=Confidence.STRONG,
            source=DetectionSource.PROCESS_TREE,
            managed=False,
            evidence={"matched_processes": [{"pid": 41298, "name": "claude"}]},
        )
        assert info.confidence == Confidence.STRONG
        assert info.detected_kind == "claude"


class TestInventorySnapshot:
    def test_schema_snapshot(self, snapshot):
        assert InventorySnapshot.model_json_schema() == snapshot

    def test_empty(self):
        inv = InventorySnapshot()
        assert inv.servers == []
        assert inv.timestamp is None
