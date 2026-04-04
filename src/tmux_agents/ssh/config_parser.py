"""Parse ~/.ssh/config to discover and validate Host aliases."""

from __future__ import annotations

import re
from pathlib import Path

_HOST_RE = re.compile(r"^\s*Host\s+(.+)$", re.IGNORECASE)


def _default_ssh_config_path() -> Path:
    return Path.home() / ".ssh" / "config"


def list_ssh_hosts(config_path: Path | None = None) -> list[str]:
    """Return all Host aliases defined in the SSH config.

    Excludes wildcard entries (containing ``*`` or ``?``).
    """
    path = config_path or _default_ssh_config_path()
    if not path.exists():
        return []

    hosts: list[str] = []
    for line in path.read_text().splitlines():
        m = _HOST_RE.match(line)
        if not m:
            continue
        for token in m.group(1).split():
            if "*" not in token and "?" not in token:
                hosts.append(token)
    return hosts


def validate_host_alias(alias: str, config_path: Path | None = None) -> bool:
    """Check whether *alias* is a defined Host entry in the SSH config."""
    return alias in list_ssh_hosts(config_path)
