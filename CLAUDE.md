# CLAUDE.md -- instructions for Claude Code working on tmux-agents

## Project overview
tmux-agents is a Python package with CLI + MCP server for discovering,
monitoring, and orchestrating AI agents running in tmux panes, both locally
and on remote machines via SSH.

## Build and run
- Uses [uv](https://docs.astral.sh/uv/) for Python and dependency management (auto-downloads Python 3.11+)
- Create venv: `uv venv`
- Install: `uv pip install -e ".[dev]"`
- CLI: `tmux-agents --help`
- MCP stdio: `tmux-agents mcp serve-stdio`
- MCP HTTP: `tmux-agents mcp serve-http`

## Test
- Run all tests: `pytest -v`
- Update snapshots: `pytest --snapshot-update`
- Single file: `pytest tests/test_refs.py -v`

## Lint and format
- Lint: `ruff check src/ tests/`
- Format check: `ruff format --check src/ tests/`
- Auto-fix: `ruff check --fix src/ tests/ && ruff format src/ tests/`

## Architecture
- `src/tmux_agents/` -- main package (src layout)
- `refs.py` -- canonical tmux ref models (frozen, immutable)
- `models.py` -- 4-layer snapshot models (ref, display, runtime, agent)
- `errors.py` -- error envelope with codes, context, and typed exceptions
- `config.py` -- configuration with Pydantic models (incl. RemoteHostConfig)
- `ssh/config_parser.py` -- parse ~/.ssh/config for Host aliases
- `ssh/runner.py` -- RemoteCommandRunner (SSH transport for remote tmux)
- `process/remote_inspector.py` -- SSH-based process tree via `ps -eo pid,ppid,comm`
- `logging.py` -- structured logging, ALWAYS to stderr (never stdout)
- `cli/main.py` -- Click CLI entrypoint
- `mcp/` -- MCP server (stdio + HTTP via FastMCP from official mcp SDK)
- `tmux/command_runner.py` -- safe tmux command execution with `-L`, `-S`, `-N` support
- `tmux/socket_discovery.py` -- three-layer socket discovery (default, scan, config)
- `tmux/inventory.py` -- batched list-sessions/windows/panes collection with -F format strings
- `services/inventory_service.py` -- get_inventory(), inspect_pane(), all_panes() composing discovery + collection + detection
- `services/detection_service.py` -- three-pass agent detection (metadata, process-tree, tmux hints)
- `process/inspector.py` -- psutil-based process-tree walker
- `agents/base.py` -- AgentProfile ABC interface
- `agents/registry.py` -- profile registry (currently: ClaudeProfile)
- `agents/profiles/claude.py` -- Claude Code detection via process name + tmux hints
- `tmux/metadata_store.py` -- read/write @tmux-agents.meta pane user option

## Critical conventions
- ALL logging goes to stderr. stdout is reserved for MCP JSON-RPC on stdio transport.
- Use `from tmux_agents.logging import get_logger` for structured logging.
- Every response model has 4 layers: ref, display, runtime, agent.
- Refs use tmux IDs for execution, names for display only.
- Error responses always use ErrorEnvelope with ErrorCode.
- Tests use syrupy for snapshot testing of JSON schemas.
- Python 3.11+ features are allowed (StrEnum, `X | Y` unions, etc.).
- Ruff is configured with `src = ["src"]` for correct first-party import detection.
- All tmux read-only probes must use `-N` flag to prevent accidental server creation.
- tmux 3.2a+ is the enforced minimum version.
- Unix socket paths on macOS have ~104-char limit; integration tests use `/tmp` with short names.
- `ServerRef.host` distinguishes local (None) from remote (SSH alias) servers.
- Use `get_runner(host, *, socket_name, socket_path)` factory to get local or remote runner.
- RemoteCommandRunner wraps tmux commands in SSH with ControlMaster multiplexing.
- SSH errors use `SSHError` exception with codes: SSH_CONNECTION_FAILED, SSH_AUTH_FAILED, SSH_HOST_UNKNOWN, SSH_TIMEOUT.

## CLI commands
- `tmux-agents list [--kind K] [--socket S] [--json]` -- list all sessions/windows/panes
- `tmux-agents inspect --pane %ID [--socket S] [--json]` -- inspect a single pane
- `tmux-agents spawn claude [--session S] [-n N] [--resume R] [--continue] [--worktree W] [--cwd P]` -- spawn managed Claude session
- `tmux-agents spawn claude --target-session S` -- spawn into existing session as new window
- `tmux-agents spawn claude --target-session S --split horizontal` -- split existing pane
- `tmux-agents kill --session S [--socket S]` -- kill a tmux session
- `tmux-agents capture --pane %ID [--lines N] [--mode tail|history|screen] [--screen auto|primary|alternate]` -- capture pane output
- `tmux-agents delta --pane %ID [--after-seq N] [--max-lines N] [--screen S]` -- incremental pane read
- `tmux-agents wait --pane %ID --pattern REGEX [--timeout MS]` -- wait for pattern in output
- `tmux-agents send-text --pane %ID --text "hello"` -- send literal text (no key interpretation)
- `tmux-agents send-keys --pane %ID Enter C-c` -- send key names (interpreted by tmux)
- `tmux-agents tag --pane %ID --agent-kind claude` -- tag a pane with agent metadata
- `tmux-agents channels send --from %0 --to %1 --message "text"` -- inter-pane message
- `tmux-agents channels read --pane %0` -- read channel message
- `tmux-agents channels peers` -- list managed panes for messaging
- `tmux-agents ssh check <HOST>` -- verify SSH connectivity and remote tmux version
- `tmux-agents ssh hosts` -- list configured remote hosts with connectivity status
- `tmux-agents mcp serve-stdio` -- MCP over stdio (for Claude Code local)
- `tmux-agents mcp serve-http [--host H] [--port P] [--no-safe-mode] [--auth-token T]` -- MCP over HTTP

## MCP tools (17 total, all accept optional `host` param for remote targeting)
- `ping` -- health check
- `list_inventory(socket_filter?)` -- full inventory snapshot
- `list_agents(kind?, socket_filter?)` -- list only detected agent panes
- `inspect_target(pane_id, socket_filter?)` -- single pane snapshot
- `spawn_agent(agent_kind, session_name?, ...)` -- spawn managed agent, returns pane snapshot
- `terminate_target(session_id)` -- kill a tmux session (omitted in safe mode)
- `capture_pane(pane_id, mode, lines, start, end, screen)` -- capture pane output (bounded to 25k chars)
- `read_pane_delta(pane_id, after_seq, max_lines, screen)` -- incremental delta read
- `send_text(pane_id, text)` -- send literal text to a pane
- `send_keys(pane_id, keys)` -- send key names (Enter, C-c, etc.)
- `set_metadata(pane_id, agent_kind, profile?)` -- tag a pane with agent metadata
- `list_hosts()` -- list configured remote hosts with connectivity status

## MCP server configuration
- **Safe mode** (default for HTTP): omits destructive tools entirely (terminate_target)
- **Bearer auth**: set TMUX_AGENTS_AUTH_TOKEN env var or --auth-token flag for HTTP
- **Transport security**: DNS rebinding protection enabled by default for HTTP
- **Output bounding**: capture content capped at 25k chars to stay within MCP token limits

## Detection system
- Three-pass detection: (1) explicit @tmux-agents.meta metadata, (2) psutil process-tree walk, (3) tmux hints (weak)
- For remote panes, Pass 2 uses `get_remote_process_tree()` via SSH `ps` instead of psutil
- Managed panes (spawned by tmux-agents) get EXPLICIT/STRONG classification
- Unmanaged agent panes detected via process name in tree → PROCESS_TREE/STRONG
- Supported agents: claude (process "claude"), codex (process "codex"), gemini (process "gemini")
- AgentInfo carries: detected_kind, confidence, source, managed, profile, evidence dict, hook_state
- New profiles: implement AgentProfile ABC in agents/profiles/, register in agents/registry.py

## Hook system (Claude cooperative state)
- Claude Code hooks write lifecycle events to `@tmux-agents.hook` pane user option
- `TMUX_AGENTS_PANE_ID` and `TMUX_AGENTS_SOCKET` env vars injected at spawn time
- `hooks generate` outputs `.claude/settings.json` hooks config
- `hooks status --pane %ID` reads current hook state
- Detection enriches managed panes with `hook_state` in AgentInfo
- Supported events: Notification, SubagentStart, SubagentStop, Stop
- No sidecar needed — hooks write directly to tmux via shell commands

## Capture system
- Three modes: tail (last N lines), history (bounded S-E), screen (auto/primary/alternate)
- Alternate screen detection via `#{alternate_on}` format variable; auto mode tries alternate first for TUI panes
- `-J` flag joins wrapped lines; `-a` for alternate screen capture
- Delta reads via monotonic seq counters + line-overlap diff algorithm
- In-memory per-pane state; `reset_required` flag handles desync recovery
- `services/capture_service.py` is the main module

## Input system
- `send_text` uses `tmux send-keys -l` (literal, no key interpretation)
- `send_keys` uses `tmux send-keys` (key names interpreted: Enter, C-c, Escape, etc.)
- Two separate commands are deliberate to avoid text/key ambiguity bugs
- `tag_pane` writes/merges @tmux-agents.meta metadata
- Three safety classes: inspection (list/inspect/capture/delta), interaction (send/spawn), destructive (kill)

## SSH remote support
- SSH Host alias from ~/.ssh/config is the machine identifier (e.g. "enterprise-a")
- `RemoteHostConfig` in config defines remote hosts with alias, display_name, extra sockets
- `RemoteCommandRunner` wraps tmux commands in SSH: `ssh <alias> tmux ...`
- SSH ControlMaster=auto with ControlPersist=60s for connection multiplexing
- BatchMode=yes to fail fast on auth prompts
- `get_runner(host)` factory dispatches local CommandRunner vs RemoteCommandRunner
- `host: str | None` on ServerRef — None means local, string means SSH alias
- `--host` top-level CLI option threads host through all commands
- `get_inventory(host_filter=)` filters by host; `"local"` = local only, `None` = all
- Capture state keyed by `(host_or_local, pane_id)` to avoid cross-host collisions
- All services accept `host` parameter: capture, input, channels, detection

## Milestones
See PLAN.md for full milestone plan. All milestones (M0-M15) complete.
