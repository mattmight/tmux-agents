"""Gemini CLI detection profile.

Detects Google Gemini CLI processes in tmux panes via:
- Process tree: look for a process named "gemini".
- Tmux hints: pane_current_command contains "gemini" (weak).
"""

from __future__ import annotations

from tmux_agents.agents.base import AgentProfile
from tmux_agents.models import AgentInfo, Confidence, DetectionSource
from tmux_agents.process.inspector import ProcessInfo


class GeminiProfile(AgentProfile):
    """Detection profile for Google Gemini CLI."""

    @property
    def kind(self) -> str:
        return "gemini"

    def match_process_tree(self, tree: list[ProcessInfo]) -> AgentInfo | None:
        matched = []
        for proc in tree:
            if _is_gemini_process(proc):
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
        if current_command and "gemini" in current_command.lower():
            return AgentInfo(
                detected_kind=self.kind,
                confidence=Confidence.WEAK,
                source=DetectionSource.TMUX_HINT,
                managed=False,
                profile=self.kind,
                evidence={"hint": "pane_current_command", "value": current_command},
            )
        return None


def _is_gemini_process(proc: ProcessInfo) -> bool:
    """Check if a process looks like Gemini CLI."""
    name = proc.name.lower()
    if name == "gemini":
        return True
    if proc.exe and proc.exe.lower().endswith("/gemini"):
        return True
    return bool(proc.cmdline and proc.cmdline[0].lower().endswith("gemini"))
