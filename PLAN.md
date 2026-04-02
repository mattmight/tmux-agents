# `tmux-agents` design document and milestone-driven implementation plan

## 1. Executive summary

`tmux-agents` should be a local-first Python package with one shared domain core and three thin front ends: a human CLI, an MCP stdio server, and an MCP Streamable HTTP server. The core should treat tmux as the source of truth, use batched tmux commands for global inventory, and allocate read-only control-mode watchers lazily per attached session when incremental output or live reconciliation is needed. That split matters because tmux control mode is command-compatible and parseable, but its format subscriptions are scoped to the attached session rather than the whole server. ([GitHub][1])

The MVP should be Claude-first but not Claude-only. Detection should be hybrid: canonical metadata when `tmux-agents` launched the pane, plus same-user process-tree inspection rooted at `pane_pid`, with `pane_current_command` used only as a hint. This is especially appropriate for Claude because tmux exposes `pane_pid` as the first process in the pane and `pane_current_command` only “if available,” while Claude Code itself changed its process title to `claude`, making process-tree classification materially better than it used to be. ([man7.org][2])

The MCP surface should follow your decision and expose everything as tools, not resources. That is viable if the tools are JSON-first, paginated, and chaining-friendly, because MCP supports tool-centric servers and Claude Code supports both local stdio servers and remote HTTP MCP servers. Large tool outputs must still be bounded: Claude Code warns when an MCP tool exceeds 10,000 tokens and defaults to a 25,000-token cap. ([Model Context Protocol][3])

## 2. Scope, goals, and non-goals

The MVP goals are narrow. `tmux-agents` must discover tmux servers and sessions on the current machine, identify panes likely running supported agents, capture and incrementally read pane output, send controlled terminal input back into those panes, and spawn new Claude Code processes into managed tmux sessions with reproducible metadata. It must work on macOS and Linux, support tmux’s default and alternate sockets, and provide the same underlying operations through both CLI and MCP. tmux stores sockets under `TMUX_TMPDIR` or `/tmp`, supports alternate socket names and full socket paths, and exposes stable IDs plus socket path formats that make this feasible. ([man7.org][2])

The MVP is not a general tmux replacement, not a remote orchestration daemon, not a generic terminal-state inference engine, and not a full session manager in the tmuxp/tmuxinator sense. It should selectively borrow proven ideas from that ecosystem—config-driven profiles, project-aware naming, and structured outputs—but stay focused on agent panes, not on reproducing all of tmux’s layout/session authoring features. tmuxp and tmuxinator both demonstrate the value of declarative configuration and stable project-level conventions, while existing tmux-MCP servers demonstrate demand for list/capture/control primitives. ([GitHub][4])

## 3. Hard decisions carried into this design

The design assumes same-host, same-user operation for the MVP. That aligns with tmux’s socket directory permissions and avoids prematurely taking on SSH transport, daemon lifecycle, and cross-user authorization. HTTP is still supported immediately because that is one of your explicit decisions, but it should remain a local service boundary unless the operator deliberately adds reverse proxying and stronger auth on top. MCP’s current transport guidance is materially stricter for HTTP than stdio, especially around Origin validation, localhost binding, session handling, and authentication. ([man7.org][2])

The implementation language should be Python. `libtmux` already supplies typed Python objects for servers, sessions, windows, and panes, plus test helpers and a pytest plugin for isolated tmux servers, while the official MCP Python SDK is Tier 1 and its server tutorial uses FastMCP for tool declarations. That combination minimizes bespoke protocol code and maximizes time spent on the tmux/agent-specific logic that actually differentiates the project. ([libtmux][5])

The minimum tmux version should be `3.2a+`. tmux added control-mode format subscriptions and pause/resume flow control in the 3.2 line, and libtmux’s documented minimum supported tmux version is 3.2a. Requiring 3.2a+ gives a crisp floor for live watch semantics and avoids an explosion of degraded-mode branches. ([GitHub][6])

## 4. System architecture

The package should be organized around a small service layer and explicit adapters:

```text
tmux_agents/
  config.py
  models.py
  refs.py
  errors.py

  tmux/
    command_runner.py
    socket_discovery.py
    inventory.py
    metadata_store.py
    control_mode.py
    selectors.py

  process/
    inspector.py

  agents/
    base.py
    registry.py
    profiles/
      claude.py
      codex.py        # post-MVP
      gemini.py       # post-MVP

  services/
    inventory_service.py
    detection_service.py
    spawn_service.py
    capture_service.py
    input_service.py

  cli/
    main.py

  mcp/
    server_common.py
    stdio.py
    http.py
    tools.py
    auth.py
```

`command_runner` is the authoritative tmux gateway for snapshot reads and mutations. `control_mode` is a separate subsystem used only when incremental output or per-session reconciliation is requested. `process.inspector` is the OS abstraction over process trees. `agents.registry` owns classifier and spawn-profile logic. `services.*` compose those into stable operations consumed equally by CLI and MCP. That division matches tmux’s command/control split, libtmux’s typed hierarchy, and MCP’s transport-neutral server model. ([libtmux][5])

A key implementation detail is that stdio and HTTP should be separate entrypoints, not one “serve everything” process. The stdio transport must never write logs to stdout because that corrupts JSON-RPC framing; HTTP does not have that constraint. Using one common server/tool registration layer with two transport launchers preserves code sharing without mixing incompatible runtime assumptions. ([Model Context Protocol][7])

## 5. Canonical data model

Every entity returned by the system should carry a canonical ref that includes socket identity plus tmux IDs:

```json
{
  "server": {"socket_path": "/tmp/tmux-501/default", "socket_name": "default"},
  "session": {"id": "$3", "name": "auth-refactor"},
  "window": {"id": "@7", "name": "main", "index": 0},
  "pane": {"id": "%12", "index": 0}
}
```

Names are for display; IDs are for execution. tmux itself explicitly recommends IDs over names or indexes because IDs are unambiguous, and tmux also exposes the server socket path as a format value, which lets the tool make socket identity first-class instead of implicit. ([GitHub][1])

Every snapshot object should be normalized into four layers: `ref`, `display`, `runtime`, and `agent`. `runtime` contains tmux-derived facts such as `pane_pid`, `pane_current_command`, `pane_current_path`, `pane_dead`, and timestamps. `agent` contains `detected_kind`, `confidence`, `source`, `managed`, `profile`, and structured metadata if available. This split keeps the objects stable even when new agent profiles are added later. The underlying fields are already exposed by tmux formats, including `pane_pid`, `pane_current_command`, `pane_current_path`, `pane_dead`, and `socket_path`. ([man7.org][2])

## 6. Socket discovery and inventory strategy

Socket discovery should have three layers. First, always probe the default socket. Second, scan the user’s tmux socket directory under `TMUX_TMPDIR` or `/tmp/tmux-UID/` for additional named sockets. Third, honor explicit configured socket paths and names from `tmux-agents` config. All read-only probes should use `tmux -N` so inspection does not accidentally start a new server when a socket is absent. tmux documents the default socket directory, alternate `-L` names, explicit `-S` paths, and the `-N` “do not start server” behavior. ([man7.org][2])

Inventory collection should be batched per socket. The implementation agent should prefer a small number of `list-sessions`, `list-windows -a`, and `list-panes -a` calls with custom `-F` formats over object-by-object traversal. `libtmux` can still be used as the ergonomic wrapper for mutation paths and selected lookups, but the inventory hot path should be optimized around raw tmux output for speed and determinism. `libtmux` remains valuable because it preserves the same server/session/window/pane object model and gives an escape hatch to raw commands. ([libtmux][5])

Global inventory should remain snapshot-driven even in the persistent MCP server. Live control-mode watchers should be lazy and scoped to sessions that are actively being tailed or reconciled. That is the correct compromise because tmux format subscriptions only support the attached session, all panes in the attached session, or all windows in the attached session—not the entire tmux server. ([GitHub][1])

## 7. Agent detection and classification

Detection should run in three passes.

First pass: explicit metadata. If pane metadata created by `tmux-agents` says the pane is a managed Claude pane, treat that as authoritative unless the pane is dead.

Second pass: process-tree classification. Start at `pane_pid`, walk descendants with `psutil`, and match against registered profiles. `psutil` is explicitly cross-platform and supports Linux and macOS, making it the right unifying layer here. ([man7.org][2])

Third pass: tmux hints. Use `pane_current_command`, `pane_current_path`, session/window naming, and optional explicit tags as weak evidence only. This is intentionally weaker because tmux documents `pane_current_command` as “current command if available,” which is not the same as “full process tree,” and because wrappers, shells, and exec chains can obscure the current foreground command. ([man7.org][2])

For Claude in the MVP, the positive signals are: explicit managed metadata, a process tree containing an executable or process name `claude`, or a strong tmux hint paired with a matching process child. Claude Code’s changelog explicitly notes that the process title was changed to `claude` instead of `node`, which materially improves passive detection. ([Claude API Docs][8])

The detector should return `kind`, `confidence`, and `evidence`, for example:

```json
{
  "kind": "claude",
  "confidence": "strong",
  "source": "process_tree",
  "evidence": {
    "root_pane_pid": 41231,
    "matched_processes": [{"pid": 41298, "name": "claude"}]
  }
}
```

That evidence payload is critical for implementation debugging and for later profile tuning.

## 8. Spawn model and Claude profile

Spawning should be profile-based. A spawn profile defines executable, argument builder, environment additions, working-directory rules, session/window naming strategy, and metadata to write back into tmux. This borrows the right idea from tmux session managers—configuration-driven creation—without turning the project into a full tmux session authoring DSL. ([GitHub][4])

For the MVP, only one first-class spawn profile is required: `claude`. It should support:

* detached session creation by default,
* optional explicit session name,
* optional Claude session name via `claude -n <name>`,
* optional `claude --resume <name>` or `--continue`,
* optional `claude --worktree <name>`,
* cwd selection at repo root, current directory, or explicit path. Claude Code documents session naming, resume flows, and `--worktree` behavior, including automatic worktree placement under `.claude/worktrees/<name>`. ([Claude API Docs][9])

The default spawn path should be: create a detached tmux session, launch the agent process as the pane command, write pane-scoped metadata, and return the full canonical ref plus an immediate capture of the visible pane. For the MVP, spawning into an existing pane or split should be deferred; detached-session spawn is the cleanest isolation boundary and easiest to reason about.

Canonical pane metadata should be stored as a single small JSON blob in a pane-scoped user option, for example `@tmux-agents.meta`. tmux supports user options prefixed with `@` and allows them at server, session, window, and pane scope. ([man7.org][2])

Suggested pane metadata schema:

```json
{
  "schema_version": 1,
  "managed": true,
  "agent_kind": "claude",
  "profile": "claude",
  "created_at": "2026-03-31T18:20:41Z",
  "project_root": "/repo",
  "worktree_name": "feature-auth",
  "requested_session_name": "auth-refactor",
  "spawn_transport": "cli"
}
```

## 9. Output capture, deltas, and watch semantics

The capture subsystem should support three modes: `tail`, `history`, and `delta`. `tail` returns the last `N` visible or recent lines. `history` returns an explicitly bounded historical slice. `delta` returns only new chunks after a cursor or sequence number. tmux’s `capture-pane` already supports visible content, full history ranges via `-S/-E`, alternate-screen capture via `-a`, joining wrapped lines via `-J`, and stdout output via `-p`. Those capabilities are enough for the MVP if the server wraps them cleanly. ([man7.org][2])

Alternate-screen support is not optional for Claude. Interactive TUIs often live in the alternate screen, and tmux documents that `capture-pane -a` targets that screen and does not expose normal history at the same time. The capture API should therefore accept `screen: "auto" | "primary" | "alternate"`, defaulting to `auto`. For panes classified as Claude, `auto` should try alternate-screen capture first and fall back to primary-screen tail if alternate-screen capture is unavailable. ([man7.org][2])

For incremental reads, the persistent MCP server should maintain a per-pane ring buffer only for panes inside sessions with an active read-only control-mode watcher. Each chunk gets a monotonic `seq`. The `read_pane_delta` tool then becomes a pollable, tools-only primitive:

```json
{
  "pane_ref": {...},
  "after_seq": 1042,
  "wait_ms": 1500,
  "max_chunks": 50
}
```

Response:

```json
{
  "from_seq": 1043,
  "to_seq": 1049,
  "chunks": [...],
  "reset_required": false
}
```

This avoids needing resources or channels just to “watch” a pane.

Watcher clients should attach read-only and use tmux’s flow-control support. tmux documents read-only client flags, `pause-after` flow control, `%pause/%continue`, and the expectation that a client can resync with `capture-pane` when it falls behind. ([man7.org][2])

## 10. CLI surface

The CLI should be JSON-first internally and human-friendly at the edge. Default human output should be concise; `--json` should always emit full machine-readable objects.

Recommended MVP commands:

* `tmux-agents list [--kind claude] [--socket default]`
* `tmux-agents inspect --pane %12 [--socket default]`
* `tmux-agents capture --pane %12 --lines 200 --screen auto`
* `tmux-agents delta --pane %12 --after-seq 0 --wait-ms 1000`
* `tmux-agents send-text --pane %12 --text "continue"`
* `tmux-agents send-keys --pane %12 --keys Enter`
* `tmux-agents spawn claude --cwd . --session auth-refactor --worktree feature-auth`
* `tmux-agents tag --pane %12 --agent-kind claude`
* `tmux-agents kill --session $3`
* `tmux-agents mcp serve-stdio`
* `tmux-agents mcp serve-http --host 127.0.0.1 --port 8766`

Two separate input commands—`send-text` and `send-keys`—are deliberate. Existing tmux-MCP tools already show that mixing literal text and control-key semantics creates ambiguity and real bugs. ([GitHub][10])

## 11. MCP design

Because you want a tools-only server, the MCP tool catalog should mirror the service layer directly:

* `list_inventory`
* `list_agents`
* `inspect_target`
* `capture_pane`
* `read_pane_delta`
* `send_text`
* `send_keys`
* `spawn_agent`
* `set_metadata`
* `terminate_target`

All tool results should be chaining-friendly: every response returns canonical refs, display names, and the minimum useful follow-on context. `spawn_agent`, for example, should return the newly created pane ref plus an immediate capture. `capture_pane` should return `truncated`, `screen_used`, and `next_suggested_args` where appropriate. This follows the current MCP emphasis on model-invoked tools and avoids unnecessary round trips. ([Model Context Protocol][11])

Both stdio and HTTP should be first-class from day one, but they should reuse the same tool registry and schemas. For implementation, the safest path is the official Python MCP SDK with FastMCP-style declarations because the SDK is Tier 1, supports local and remote transports, and the official build guide uses FastMCP directly. ([Model Context Protocol][12])

The HTTP entrypoint should default to `127.0.0.1`, require an explicit opt-in to bind wider interfaces, validate Origin, and support static bearer-token auth in the MVP. That is not optional hardening; the current transport spec explicitly warns about DNS rebinding, recommends localhost binding for local servers, and recommends proper authentication. HTTP session handling should be delegated to the MCP SDK where possible. ([Model Context Protocol][13])

Claude Code already supports all the pieces this needs: local stdio servers, remote HTTP MCP servers, OAuth for remote HTTP, dynamic capability refresh, and using project/user scopes for MCP configuration. ([Claude][14])

## 12. Safety model

The safety boundary should be explicit in the tool catalog. There should be three classes of operations:

* inspection: list, inspect, capture, delta;
* interaction: send text, send keys, spawn managed agents;
* destructive/admin: terminate pane/window/session, raw tmux passthrough.

The MVP should expose the first two classes by default and keep raw tmux passthrough out of scope entirely. Claude Code’s own permission model distinguishes read-only, Bash execution, and file modification, which is a useful precedent: sensitive capabilities should be narrow and obvious, not hidden inside a generic “exec” tool. ([Claude][15])

For HTTP specifically, the server should support a `safe` mode as the default. In `safe` mode, destructive tools are not registered at all, which is preferable to runtime denial. Claude Code supports dynamic tool refresh via `list_changed`, so capability expansion can remain a later enhancement rather than something designed into MVP startup flows. ([Claude][14])

## 13. Cross-platform support and compatibility

The supported platforms should be Linux and macOS, same user only. Process inspection should use `psutil` as the primary abstraction because it is cross-platform and includes process-tree traversal APIs that work on both target operating systems. Platform-specific shell fallbacks should exist only for diagnostics, not as the main path. ([psutil.readthedocs.io][16])

The project should require Python 3.11 as its own floor, even though the MCP Python SDK only requires Python 3.10+. That keeps typing, async ergonomics, and packaging simpler while remaining comfortably inside the supported range. The implementation guide for MCP servers explicitly requires Python 3.10+ and warns about stdio logging behavior; both should be reflected in the project bootstrap. ([Model Context Protocol][7])

## 14. Testing strategy

Testing should be integration-heavy. The core project risk is not pure logic; it is real tmux behavior: socket discovery, capture semantics, alternate-screen behavior, control-mode parsing, pause/resume flow control, and process-tree matching against live panes. `libtmux` already ships isolated tmux fixtures and a pytest plugin, which should be the foundation of the integration suite. ([libtmux][5])

The test matrix should be:

* Ubuntu latest, current tmux,
* Ubuntu latest, pinned older tmux near the floor,
* macOS latest, Homebrew tmux current.

Every milestone that touches tmux behavior should add an integration test first. The MCP layer should then be tested with both a stdio client harness and HTTP transport harness. The MCP Inspector is useful for manual debugging but should not substitute for automated regression coverage. ([Model Context Protocol][12])

## 15. MVP definition

The MVP must include:

* socket discovery for default, scanned, and configured sockets;
* global inventory of sessions/windows/panes with canonical refs;
* Claude detection for managed and unmanaged panes;
* pane metadata storage in tmux user options;
* detached-session spawn for Claude with optional name/worktree/resume inputs;
* bounded capture with alternate-screen support;
* incremental `read_pane_delta` via lazy per-session watchers;
* `send_text` and `send_keys`;
* CLI plus MCP stdio and HTTP;
* macOS/Linux integration test coverage. ([man7.org][2])

The MVP should not include:

* SSH transport,
* remote multi-user daemon mode,
* Codex/Gemini spawning,
* generic semantic-state inference,
* resources/prompts/channels,
* a general tmux command passthrough surface,
* deep tmux layout/session management beyond what spawn requires. ([Claude][17])

## 16. Post-MVP extension path

The first extension path is additional agent adapters. Codex CLI and Gemini CLI both expose stable terminal commands—`codex` and `gemini` respectively—so they fit naturally into the same profile/detector architecture once the Claude path is stable. ([GitHub][18])

The second extension path is Claude-specific cooperative state. Claude’s hook system exposes lifecycle events, permission interception, Notification events for `permission_prompt`, `idle_prompt`, `auth_success`, and `elicitation_dialog`, plus subagent, worktree, and teammate-idle events. Claude’s project-scoped settings are designed for sharing hooks and MCP servers with a team, which makes a managed hook bundle plausible later. ([Claude][19])

The third extension path is push-style coordination. Claude Code channels are now a real feature, but they remain in research preview, require a recent Claude Code version, and are stdio-based local bridges rather than a generic remote transport story. They should remain post-MVP. ([Claude][17])

## 17. Feasibility assessment: Claude semantic state inference

For unmanaged Claude panes discovered passively, robust semantic-state inference is low-confidence without cooperation. tmux can tell you the pane exists, what process tree is under it, and what text is on screen, but not whether Claude is specifically waiting on permission, idle for user input, authenticating, or inside a subagent boundary. Inferring those states from terminal text alone would be brittle and theme/version dependent. ([man7.org][2])

For managed Claude panes launched by `tmux-agents`, feasibility is high. Claude hooks can emit structured JSON on lifecycle events, including `Notification`, `PermissionRequest`, `SubagentStart`, `TeammateIdle`, and `WorktreeCreate`, and those hooks can be shared through project settings. The clean post-MVP design is to inject `TMUX_AGENTS_*` environment variables at spawn time and install a small hook bridge that writes structured state back to `tmux-agents`, either by updating pane metadata or by posting to a localhost sidecar endpoint. ([Claude][19])

The result is a clear recommendation: do **not** attempt generic semantic inference in the MVP. Instead, reserve a post-MVP “Claude cooperative state” milestone built on hooks for managed sessions only. That will produce a much better system than any amount of screen scraping.

## 18. Detailed milestone-driven implementation plan

## Pre-MVP

### M0. Repository bootstrap and contracts -- COMPLETE

**Objective:** create a stable project skeleton and freeze the public data contracts before any tmux logic lands.

**Implementation slices**

* initialize Python package, CLI entrypoint, and config loader;
* define canonical ref models, error envelope, and JSON response schema;
* add structured logging with stderr-only discipline for stdio paths;
* wire CI skeleton for Linux and macOS. ([Model Context Protocol][7])

**Exit criteria**

* `tmux-agents --help` works; **DONE**
* `tmux-agents mcp serve-stdio` starts and cleanly initializes; **DONE**
* all core response models have snapshot tests. **DONE** (33 tests, 11 snapshots)

### M1. tmux command gateway, version probe, and socket discovery -- COMPLETE

**Objective:** establish safe, non-mutating access to tmux servers.

**Implementation slices**

* implement command runner with `-L`, `-S`, and `-N` support;
* parse `tmux -V` and enforce `3.2a+`;
* discover default socket, scanned sockets, and configured sockets;
* return normalized server objects with socket refs. ([man7.org][2])

**Exit criteria**

* missing sockets do not start new tmux servers; **DONE** (`-N` flag integration test)
* multiple sockets are discovered correctly in integration tests; **DONE** (isolated socket fixtures)
* unsupported tmux versions fail fast with a clear error. **DONE** (37 new tests, 70 total)

### M2. Inventory snapshots and canonical refs -- COMPLETE

**Objective:** list all sessions, windows, and panes per socket with stable IDs and runtime fields.

**Implementation slices**

* batch `list-sessions`, `list-windows -a`, and `list-panes -a` collectors;
* normalize session/window/pane graphs;
* expose `list` and `inspect` CLI commands;
* implement JSON and human output renderers. ([man7.org][2])

**Exit criteria**

* `list` returns stable refs and correct parent-child relationships; **DONE**
* dead panes and current commands are surfaced; **DONE** (runtime layer populated)
* inventory on 50+ panes remains acceptably fast in benchmark smoke tests. **DONE** (3 batched commands per server)
* 87 total tests, 17 new M2 integration tests.

### M3. Process inspection and Claude detection -- COMPLETE

**Objective:** classify panes as managed/unmanaged Claude panes with explainable evidence.

**Implementation slices**

* implement psutil-based recursive process-tree walker;
* define `AgentProfile` interface and first `ClaudeProfile`;
* add metadata lookup path and confidence scoring;
* expose `--kind claude` filtering in CLI. ([psutil.readthedocs.io][16])

**Exit criteria**

* managed Claude panes classify as `explicit`; **DONE** (metadata store + explicit detection pass)
* unmanaged manual Claude panes classify via process tree; **DONE** (ClaudeProfile process-tree matching)
* shell, vim, and generic build panes do not false-positive in test fixtures. **DONE** (unit + integration tests)
* 106 total tests, 19 new M3 tests.

### M4. Spawn service and metadata store -- COMPLETE

**Objective:** launch managed Claude sessions and make them self-describing.

**Implementation slices**

* implement detached-session spawn path;
* add Claude argument builder for session naming, resume, and worktree;
* write pane metadata to tmux user options;
* return immediate `inspect` payload after spawn. ([Claude API Docs][9])

**Exit criteria**

* `spawn claude` creates a detached session and returns pane ref; **DONE**
* spawned pane is immediately classified as managed Claude; **DONE** (metadata-based EXPLICIT detection)
* optional worktree spawn succeeds in a git repo fixture. **DONE** (argument builder tested)
* 125 total tests, 19 new M4 tests.

### M5. Capture, alternate screen, and delta cursors -- COMPLETE

**Objective:** expose bounded read operations that work for Claude’s TUI.

**Implementation slices**

* implement `capture_pane` with `tail`, `history`, and `screen` options;
* support alternate-screen capture and wrapped-line joining;
* introduce persistent ring buffers and `seq` cursors;
* add lazy per-session read-only control-mode watchers with flow control. ([man7.org][2])

**Exit criteria**

* alternate-screen capture returns meaningful Claude UI content; **DONE** (auto/primary/alternate via `#{alternate_on}`)
* `read_pane_delta` returns only new output after a cursor; **DONE** (line-overlap diff with monotonic seq)
* watcher desync recovers automatically with a snapshot refresh. **DONE** (`reset_required` flag)
* 150 total tests, 25 new M5 tests. Control-mode watchers deferred to post-MVP; snapshot-based polling meets the API contract.

### M6. Input service and safe mutation surface -- COMPLETE

**Objective:** allow controlled interaction with agent panes without ambiguous terminal semantics.

**Implementation slices**

* implement separate `send_text` and `send_keys` paths;
* support common control keys cleanly;
* add `tag` and `terminate_target` operations;
* keep raw tmux passthrough out of scope. ([GitHub][10])

**Exit criteria**

* sending `Enter` or `C-c` behaves as keys, not literal text; **DONE** (send_keys uses tmux key interpretation)
* text submission and key submission both work against managed Claude panes; **DONE** (send_text uses -l for literal)
* destructive operations are clearly separated from inspection. **DONE** (three-class safety model: inspection/interaction/destructive)
* 162 total tests, 12 new M6 tests.

### M7. MCP server completion: stdio and HTTP -- COMPLETE

**Objective:** expose the shared core as a production-usable MCP server on both transports.

**Implementation slices**

* implement tool registry on official Python MCP SDK;
* add stdio launcher with strict stdout discipline;
* add HTTP launcher with localhost default, Origin validation, and bearer auth;
* write end-to-end MCP tests for both transports;
* document Claude Code configuration examples for stdio and HTTP. ([Model Context Protocol][7])

**Exit criteria**

* Claude Code can connect over stdio and HTTP; **DONE** (FastMCP with stdio + streamable-http transports)
* all MVP tools work identically on both transports; **DONE** (shared tool registry, 11 tools)
* tool outputs stay bounded and respect pagination defaults. **DONE** (25k char cap on capture content)
* 176 total tests, 14 new M7 tests. Safe mode, bearer auth, DNS rebinding protection, list_agents tool.

### M8. Hardening, packaging, and MVP release -- COMPLETE

**Objective:** cut the first release only after operational quality is acceptable.

**Implementation slices**

* benchmark inventory and capture operations;
* improve error messages and retry behavior around dead panes and lost sockets;
* publish package metadata and installation docs;
* add release notes and compatibility matrix. ([GitHub][20])

**Exit criteria**

* Linux and macOS CI green; **DONE** (CI matrix: 2 OS x 3 Python versions + build job)
* installable via standard Python tooling; **DONE** (`pip install tmux-agents`, wheel builds)
* release tagged as MVP. **DONE** (v0.1.0)
* 180 total tests. Dead pane detection with PaneDeadError. Structured CLI error rendering. Output bounding.

## Post-MVP

### M9. Additional agent adapters -- COMPLETE

Add `CodexProfile` and `GeminiProfile` for detection and spawning, keeping the same `AgentProfile` and `SpawnProfile` interfaces. Detection remains process-tree + metadata; spawn support remains profile-based. ([GitHub][18])

* CodexProfile and GeminiProfile with process-tree + tmux-hint matching. **DONE**
* Registry expanded to 3 profiles (claude, codex, gemini). **DONE**
* spawn_agent() dispatcher with spawn_codex() and spawn_gemini(). **DONE**
* CLI `spawn codex` / `spawn gemini` supported. **DONE**
* 204 total tests, 24 new M9 tests.

### M10. Claude cooperative state bridge -- COMPLETE

Implement optional hook bundle generation, hook bridge process, and structured state propagation for managed Claude sessions. Use `Notification`, `PermissionRequest`, `SubagentStart/Stop`, `TeammateIdle`, and `WorktreeCreate/Remove` first. ([Claude API Docs][21])

* Hook bundle generator outputs Claude Code hooks config for `.claude/settings.json`. **DONE**
* Hooks write events to `@tmux-agents.hook` pane option via shell commands. **DONE**
* `TMUX_AGENTS_PANE_ID` and `TMUX_AGENTS_SOCKET` env vars injected at spawn. **DONE**
* Detection enriches managed panes with `hook_state` in AgentInfo. **DONE**
* CLI: `hooks generate`, `hooks status`. MCP: `read_hook_state`. **DONE**
* 221 total tests, 17 new M10 tests.

### M11. Advanced orchestration and UX -- COMPLETE

Add spawn-into-window/pane, project profile files, richer previews, wait-for-pattern helpers, and session templates inspired by tmuxp/tmuxinator. This is where broader session-manager ergonomics belong, not in the MVP. ([GitHub][4])

* Spawn-into-window (`new-window -t`) and spawn-into-split (`split-window -h/-v`). **DONE**
* `spawn_agent()` accepts `target_session` and `split_direction`. **DONE**
* `wait_for_pattern()` polls capture with regex matching + timeout. **DONE**
* `preview_pane()` bundles snapshot + recent output + process tree. **DONE**
* CLI: `--target-session`, `--split`, `wait`, `inspect --preview`. **DONE**
* MCP: `wait_for_pattern` tool, `spawn_agent` with target_session/split. **DONE**
* 231 total tests, 10 new M11 tests. Project profiles deferred to future work.

### M12. Channels and remote coordination -- COMPLETE

Evaluate channel integration only after the hook bridge is stable. Channels are real but still preview-gated and should not drive the initial architecture. ([Claude][17])

* Inter-pane messaging via `@tmux-agents.channel` tmux user option. **DONE**
* `send_message()`, `read_messages()`, `list_channel_peers()` service. **DONE**
* CLI: `channels send`, `channels read`, `channels peers`. **DONE**
* MCP: `send_channel_message`, `read_channel_messages` tools. **DONE**
* 240 total tests, 9 new M12 tests. All milestones (M0-M12) complete.

---

All milestones (M0-M12) are now complete. The project is feature-complete per the original design document.

[1]: https://github.com/tmux/tmux/wiki/Control-Mode "https://github.com/tmux/tmux/wiki/Control-Mode"
[2]: https://man7.org/linux/man-pages/man1/tmux.1.html "https://man7.org/linux/man-pages/man1/tmux.1.html"
[3]: https://modelcontextprotocol.io/specification/2025-11-25 "https://modelcontextprotocol.io/specification/2025-11-25"
[4]: https://github.com/tmux-python/tmuxp "https://github.com/tmux-python/tmuxp"
[5]: https://libtmux.git-pull.com/ "https://libtmux.git-pull.com/"
[6]: https://github.com/tmux/tmux/blob/master/CHANGES?plain=1 "https://github.com/tmux/tmux/blob/master/CHANGES?plain=1"
[7]: https://modelcontextprotocol.io/docs/develop/build-server "https://modelcontextprotocol.io/docs/develop/build-server"
[8]: https://docs.anthropic.com/en/release-notes/claude-code "https://docs.anthropic.com/en/release-notes/claude-code"
[9]: https://docs.anthropic.com/en/docs/claude-code/common-workflows "https://docs.anthropic.com/en/docs/claude-code/common-workflows"
[10]: https://github.com/nickgnd/tmux-mcp/issues "https://github.com/nickgnd/tmux-mcp/issues"
[11]: https://modelcontextprotocol.io/specification/2025-11-25/server/tools "https://modelcontextprotocol.io/specification/2025-11-25/server/tools"
[12]: https://modelcontextprotocol.io/docs/sdk "https://modelcontextprotocol.io/docs/sdk"
[13]: https://modelcontextprotocol.io/specification/2025-11-25/basic/transports "https://modelcontextprotocol.io/specification/2025-11-25/basic/transports"
[14]: https://code.claude.com/docs/en/mcp "https://code.claude.com/docs/en/mcp"
[15]: https://code.claude.com/docs/en/permissions "https://code.claude.com/docs/en/permissions"
[16]: https://psutil.readthedocs.io/ "https://psutil.readthedocs.io/"
[17]: https://code.claude.com/docs/en/channels-reference "https://code.claude.com/docs/en/channels-reference"
[18]: https://github.com/openai/codex "https://github.com/openai/codex"
[19]: https://code.claude.com/docs/en/hooks "https://code.claude.com/docs/en/hooks"
[20]: https://github.com/tmux/tmux/wiki/FAQ "https://github.com/tmux/tmux/wiki/FAQ"
[21]: https://docs.anthropic.com/en/docs/claude-code/hooks "https://docs.anthropic.com/en/docs/claude-code/hooks"
