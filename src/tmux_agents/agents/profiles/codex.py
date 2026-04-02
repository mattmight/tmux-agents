"""Codex CLI detection profile.

Detects OpenAI Codex CLI processes in tmux panes via:
- Process tree: look for a process named "codex".
- Tmux hints: pane_current_command contains "codex" (weak).
"""

from __future__ import annotations

from tmux_agents.agents.base import AgentProfile
from tmux_agents.models import AgentInfo, Confidence, DetectionSource
from tmux_agents.process.inspector import ProcessInfo


class CodexProfile(AgentProfile):
    """Detection profile for OpenAI Codex CLI."""

    @property
    def kind(self) -> str:
        return "codex"

    def match_process_tree(self, tree: list[ProcessInfo]) -> AgentInfo | None:
        matched = []
        for proc in tree:
            if _is_codex_process(proc):
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
        if current_command and "codex" in current_command.lower():
            return AgentInfo(
                detected_kind=self.kind,
                confidence=Confidence.WEAK,
                source=DetectionSource.TMUX_HINT,
                managed=False,
                profile=self.kind,
                evidence={"hint": "pane_current_command", "value": current_command},
            )
        return None


def _is_codex_process(proc: ProcessInfo) -> bool:
    """Check if a process looks like Codex CLI."""
    name = proc.name.lower()
    if name == "codex":
        return True
    if proc.exe and proc.exe.lower().endswith("/codex"):
        return True
    return bool(proc.cmdline and proc.cmdline[0].lower().endswith("codex"))
