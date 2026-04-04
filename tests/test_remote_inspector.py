"""Tests for SSH-based remote process inspection (M14)."""

from __future__ import annotations

from typing import ClassVar

from tmux_agents.process.remote_inspector import _parse_ps_output


class TestParsePsOutput:
    PS_LINES: ClassVar[list[str]] = [
        "  PID  PPID COMM",
        "    1     0 init",
        "  100     1 bash",
        "  200   100 node",
        "  300   200 claude",
        "  400     1 sshd",
    ]

    def test_walk_from_root(self):
        result = _parse_ps_output(self.PS_LINES, 100)
        pids = [p.pid for p in result]
        # bash(100) -> node(200) -> claude(300)
        assert pids == [100, 200, 300]

    def test_root_process_included(self):
        result = _parse_ps_output(self.PS_LINES, 100)
        assert result[0].pid == 100
        assert result[0].name == "bash"

    def test_child_names(self):
        result = _parse_ps_output(self.PS_LINES, 100)
        names = [p.name for p in result]
        assert "node" in names
        assert "claude" in names

    def test_unrelated_processes_excluded(self):
        result = _parse_ps_output(self.PS_LINES, 100)
        pids = {p.pid for p in result}
        assert 400 not in pids  # sshd is a sibling of bash, not descendant

    def test_single_process(self):
        result = _parse_ps_output(self.PS_LINES, 400)
        assert len(result) == 1
        assert result[0].name == "sshd"

    def test_missing_pid(self):
        result = _parse_ps_output(self.PS_LINES, 9999)
        assert result == []

    def test_empty_output(self):
        result = _parse_ps_output([], 100)
        assert result == []

    def test_header_only(self):
        result = _parse_ps_output(["  PID  PPID COMM"], 100)
        assert result == []

    def test_malformed_lines_skipped(self):
        lines = [
            "  PID  PPID COMM",
            "not a valid line",
            "  100     1 bash",
            "  200   100 node",
        ]
        result = _parse_ps_output(lines, 100)
        assert len(result) == 2
        assert result[0].name == "bash"
        assert result[1].name == "node"

    def test_process_info_fields(self):
        result = _parse_ps_output(self.PS_LINES, 300)
        assert len(result) == 1
        p = result[0]
        assert p.pid == 300
        assert p.name == "claude"
        # Remote inspector only gets comm, not exe/cmdline
        assert p.exe is None
        assert p.cmdline == []

    def test_init_tree(self):
        """Walking from PID 1 should get the full tree."""
        result = _parse_ps_output(self.PS_LINES, 1)
        pids = {p.pid for p in result}
        assert pids == {1, 100, 200, 300, 400}
