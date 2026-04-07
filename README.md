# tmux-agents

Tmux agent orchestration with CLI and MCP server -- local and remote via SSH.

Discover, monitor, and control AI coding agents (Claude Code, Codex, Gemini)
running in tmux sessions across multiple machines -- from the command line or
through the Model Context Protocol.

## Status

**All milestones complete (M0-M15).** Discovers tmux servers (local and remote
via SSH), detects Claude/Codex/Gemini agents, spawns managed sessions (including
into existing windows/splits), captures pane output, reads deltas, sends
text/keys, waits for patterns, integrates with Claude Code hooks, and provides
inter-pane channel messaging. Everything exposed via CLI and MCP (stdio + HTTP).
Remote machine support uses SSH `Host` aliases from `~/.ssh/config`. All MCP
tools accept an optional `host` parameter.
See [PLAN.md](PLAN.md) for the design document.

## Requirements

- [uv](https://docs.astral.sh/uv/) (manages Python 3.11+ automatically)
- tmux 3.2a+
- macOS or Linux

## Installation

```bash
git clone https://github.com/mattmight/tmux-agents.git
cd tmux-agents
uv venv && uv pip install -e .
```

To add it to your PATH:

```bash
make install   # symlinks bin/tmux-agents to ~/bin/
```

For development:

```bash
uv venv && uv pip install -e ".[dev]"
```

## Quick start

```bash
# List all tmux sessions, windows, and panes
tmux-agents list

# List with JSON output
tmux-agents --json list

# List only Claude agent panes
tmux-agents list --kind claude

# Spawn a managed Claude session
tmux-agents spawn claude --session my-project --cwd /path/to/repo

# Spawn Codex or Gemini agents
tmux-agents spawn codex --session review --cwd /path/to/repo
tmux-agents spawn gemini --session research

# Spawn into an existing session as a new window
tmux-agents spawn claude --target-session my-project

# Spawn as a split pane
tmux-agents spawn claude --target-session my-project --split horizontal

# Spawn with Claude --worktree
tmux-agents spawn claude --worktree feature-auth

# Capture pane output (tail, history, or screen mode)
tmux-agents capture --pane %0 --lines 100
tmux-agents capture --pane %0 --screen auto   # tries alternate screen first

# Incremental delta reads
tmux-agents --json delta --pane %0 --after-seq 0

# Wait for a pattern to appear in pane output
tmux-agents wait --pane %0 --pattern "\\$ " --timeout 10000

# Send literal text to a pane
tmux-agents send-text --pane %0 --text "continue"

# Send key names (Enter, C-c, Escape, etc.)
tmux-agents send-keys --pane %0 Enter

# Tag a pane as an agent
tmux-agents tag --pane %0 --agent-kind claude

# Inter-pane channel messaging
tmux-agents channels send --from %0 --to %1 --message "ready"
tmux-agents channels read --pane %1
tmux-agents channels peers

# Inspect a specific pane
tmux-agents inspect --pane %0

# Kill a session
tmux-agents kill --session my-project

# Generate Claude Code hooks config for lifecycle integration
tmux-agents hooks generate

# Check hook state on a managed pane
tmux-agents hooks status --pane %0

# Check SSH connectivity to a remote host
tmux-agents ssh check enterprise-a

# List configured remote hosts with status
tmux-agents ssh hosts

# List agents on a remote host
tmux-agents --host enterprise-a list

# Spawn a Claude agent on a remote host
tmux-agents --host enterprise-a spawn claude --cwd /home/user/project

# Capture remote pane output
tmux-agents --host enterprise-a capture --pane %0

# Start MCP server (stdio, for Claude Code)
tmux-agents mcp serve-stdio

# Start MCP server (HTTP, with auth)
TMUX_AGENTS_AUTH_TOKEN=mytoken tmux-agents mcp serve-http --port 8766
```

## Claude Code integration

**Stdio (local, recommended):**

Add to `.claude/settings.json` or project settings:

```json
{
  "mcpServers": {
    "tmux-agents": {
      "command": "tmux-agents",
      "args": ["mcp", "serve-stdio"]
    }
  }
}
```

**HTTP (remote):**

```json
{
  "mcpServers": {
    "tmux-agents": {
      "url": "http://localhost:8766/mcp",
      "headers": {
        "Authorization": "Bearer mytoken"
      }
    }
  }
}
```

## Troubleshooting

**"tmux binary not found on PATH"** -- Install tmux via your package manager
(`brew install tmux` on macOS, `apt install tmux` on Ubuntu).

**"tmux X.X is below minimum 3.2a"** -- tmux-agents requires tmux 3.2a or
newer. Upgrade via your package manager.

**"Pane %XX is dead"** -- The target pane's process has exited. Use
`tmux-agents list` to find live panes, or `tmux-agents spawn` to create new ones.

**"Pane %XX not found"** -- The pane ID does not exist in any running tmux
server. Pane IDs change across tmux server restarts.

## Development

```bash
uv pip install -e ".[dev]"
pytest -v
ruff check src/ tests/
ruff format src/ tests/
```

## License

MIT
