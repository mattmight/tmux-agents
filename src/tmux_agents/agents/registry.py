"""Agent profile registry.

Central registry of all known agent profiles. Detection service
iterates registered profiles to classify panes.
"""

from __future__ import annotations

from tmux_agents.agents.base import AgentProfile
from tmux_agents.agents.profiles.claude import ClaudeProfile
from tmux_agents.agents.profiles.codex import CodexProfile
from tmux_agents.agents.profiles.gemini import GeminiProfile


def get_profiles() -> list[AgentProfile]:
    """Return all registered agent detection profiles."""
    return [
        ClaudeProfile(),
        CodexProfile(),
        GeminiProfile(),
    ]


def get_profile(kind: str) -> AgentProfile | None:
    """Return the profile for a given agent kind, or None."""
    for p in get_profiles():
        if p.kind == kind:
            return p
    return None
