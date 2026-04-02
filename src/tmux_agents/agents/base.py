"""Agent profile interface.

An AgentProfile defines how to detect a specific agent kind in a tmux pane.
Each profile provides process-tree matching and tmux-hint matching.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from tmux_agents.models import AgentInfo
from tmux_agents.process.inspector import ProcessInfo


class AgentProfile(ABC):
    """Base class for agent detection profiles."""

    @property
    @abstractmethod
    def kind(self) -> str:
        """Agent kind identifier, e.g. 'claude'."""

    @abstractmethod
    def match_process_tree(self, tree: list[ProcessInfo]) -> AgentInfo | None:
        """Attempt to classify from a process tree.

        Returns AgentInfo with PROCESS_TREE source if matched, None otherwise.
        """

    @abstractmethod
    def match_tmux_hints(
        self,
        *,
        current_command: str | None,
        current_path: str | None,
        session_name: str | None,
        window_name: str | None,
    ) -> AgentInfo | None:
        """Attempt to classify from tmux hints (weak evidence).

        Returns AgentInfo with TMUX_HINT source if matched, None otherwise.
        """
