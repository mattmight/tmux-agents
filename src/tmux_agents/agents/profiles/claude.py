"""Claude Code detection profile.

Detects Claude Code processes in tmux panes via:
- Process tree: look for a process named "claude" (Claude Code changed its
  process title from "node" to "claude").
- Tmux hints: pane_current_command contains "claude" (weak, fallback only).
"""

from __future__ import annotations

from tmux_agents.agents.base import AgentProfile
from tmux_agents.models import AgentInfo, Confidence, DetectionSource
from tmux_agents.process.inspector import ProcessInfo


class ClaudeProfile(AgentProfile):
    """Detection profile for Claude Code."""

    @property
    def kind(self) -> str:
        return "claude"

    def match_process_tree(self, tree: list[ProcessInfo]) -> AgentInfo | None:
        matched = []
        for proc in tree:
            if _is_claude_process(proc):
                matched.append({"pid": proc.pid, "name": proc.name})

        if not matched:
            return None

        return AgentInfo(
            detected_kind=self.kind,
            confidence=Confidence.STRONG,
            source=DetectionSource.PROCESS_TREE,
            managed=False,
            profile=self.kind,
            evidence={
                "root_pid": tree[0].pid if tree else None,
                "matched_processes": matched,
            },
        )

    def match_tmux_hints(
        self,
        *,
        current_command: str | None,
        current_path: str | None,
        session_name: str | None,
        window_name: str | None,
    ) -> AgentInfo | None:
        if current_command and "claude" in current_command.lower():
            return AgentInfo(
                detected_kind=self.kind,
                confidence=Confidence.WEAK,
                source=DetectionSource.TMUX_HINT,
                managed=False,
                profile=self.kind,
                evidence={"hint": "pane_current_command", "value": current_command},
            )
        return None


def _is_claude_process(proc: ProcessInfo) -> bool:
    """Check if a process looks like Claude Code."""
    name = proc.name.lower()
    if name == "claude":
        return True
    # Also match if the executable path ends with /claude
    if proc.exe and proc.exe.lower().endswith("/claude"):
        return True
    # Check cmdline for claude as the main command
    return bool(proc.cmdline and proc.cmdline[0].lower().endswith("claude"))
