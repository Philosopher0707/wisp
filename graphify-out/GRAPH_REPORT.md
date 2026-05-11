# Graph Report - wisp  (2026-05-10)

## Corpus Check
- 80 files · ~89,862 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 2123 nodes · 6484 edges · 69 communities detected
- Extraction: 32% EXTRACTED · 68% INFERRED · 0% AMBIGUOUS · INFERRED: 4400 edges (avg confidence: 0.5)
- Token cost: 0 input · 0 output

## God Nodes (most connected - your core abstractions)
1. `WispConfig` - 268 edges
2. `WispAgentCore` - 241 edges
3. `SessionManager` - 170 edges
4. `Session` - 159 edges
5. `AgentEvent` - 159 edges
6. `CheckpointManager` - 121 edges
7. `SemanticIndex` - 121 edges
8. `SwarmOrchestrator` - 113 edges
9. `PlanStore` - 104 edges
10. `AgentRole` - 104 edges

## Surprising Connections (you probably didn't know these)
- `Split file content into prefix (before cursor) and suffix (after cursor).      R` --uses--> `WispConfig`  [INFERRED]
  wisp/completion.py → wisp/config.py
- `Build a fill-in-the-middle prompt for code completion.` --uses--> `WispConfig`  [INFERRED]
  wisp/completion.py → wisp/config.py
- `Plugin registry — local install management and remote marketplace.` --uses--> `PluginManifest`  [INFERRED]
  wisp/plugins/registry.py → wisp/plugins/manifest.py
- `# TODO: implement proper semver constraint checking.` --uses--> `PluginManifest`  [INFERRED]
  wisp/plugins/registry.py → wisp/plugins/manifest.py
- `ToolError` --uses--> `PlanStore`  [INFERRED]
  wisp/tools.py → wisp/planner.py

## Communities

### Community 0 - "Agent Decision Rationale"
Cohesion: 0.04
Nodes (240): Interactive REPL — continuous conversation until the user exits., Backward compat: stream to stdout while accumulating., Execute the agent loop for one user turn., Execute a batch of tool calls, return result messages for the model., Extract tool calls from an Ollama chat response., Build a system prompt block listing available skills (discovers skills internall, Build a system prompt block from an already-discovered skills list., The main agent — synchronous API, backward compatible with all existing code. (+232 more)

### Community 1 - "Agent Memory System"
Cohesion: 0.02
Nodes (192): AgentMemory, Agent memory store — persist and retrieve session summaries.  Stores structured, Load recent summaries, optionally filtered by workspace.          Returns newest, Delete all summaries., If file exceeds _MAX_SUMMARIES, keep only the most recent., Format summaries into a system prompt block., Absolute path without symlink resolution. Avoids macOS /tmp→/private/tmp issues., Store and retrieve session summaries. (+184 more)

### Community 2 - "Agent Behavior Rationale"
Cohesion: 0.03
Nodes (115): WispAgentCore — event-driven agent engine with zero I/O.  This is the SDK-facing, Rollback to the most recent checkpoint or a specific one.          Args:, Remove the oldest logical turn (user + response + tool results).      After remo, Spawn parallel subagents for independent tasks.          Args:             specs, Run a full agent loop for a single task, non-interactively.          Returns a d, Check if any hook result should block execution., Collect messages from hook results into a single string., Return modified tool args from hook results, if any hook modified them. (+107 more)

### Community 3 - "Agent Factory"
Cohesion: 0.03
Nodes (83): ABC, AgentFactory, Agent factory — creates WispAgent instances configured for a specific role., Creates role-configured agents for the swarm.      Each agent is a fresh WispAge, Create a new agent for the given role.          Args:             role: One of t, Return the configuration for a role., BaseProvider, Provider abstractions for model backends. (+75 more)

### Community 4 - "ACP Adapter Protocol"
Cohesion: 0.08
Nodes (64): AcpAdapter, main(), ACP (Agent Client Protocol) adapter for Zed integration.  Runs Wisp as an extern, Handle initialize request — handshake with Zed., Create a new ACP session., Load an existing session., Handle a prompt request — this is the main chat interaction.          Returns im, Handle a tool result from Zed (after we sent a tool_call). (+56 more)

### Community 5 - "Plugin Discovery"
Cohesion: 0.05
Nodes (49): discover_plugins(), Auto-discover plugins at startup.  Scans multiple locations for plugins:   1. .w, Scan a directory for plugin subdirectories with plugin.json files., Scan skill directories for subdirectories that contain plugin.json.      This br, Discover plugins from all standard locations.      Discovery order (higher prior, _scan_plugin_dir(), _scan_skill_plugins(), from_file() (+41 more)

### Community 6 - "Repository Map"
Cohesion: 0.04
Nodes (65): _assemble_entries(), _build_signature_from_text(), _compute_pagerank(), _deps_c(), _deps_go(), _deps_java(), _deps_javascript(), _deps_python() (+57 more)

### Community 7 - "Session Management"
Cohesion: 0.05
Nodes (43): create(), _ensure_sessions_dir(), format_session_preview(), from_dict(), _now_iso(), Session persistence for Wisp — save, load, list, and manage conversations.  Sess, Update the updated_at timestamp., Generate a summary of this session's conversation. (+35 more)

### Community 8 - "Checkpoint System"
Cohesion: 0.04
Nodes (28): Checkpoint, CheckpointError, CheckpointStore, _empty_diff_message(), from_dict(), _git_proc(), Git-based workspace checkpoint system for the Wisp AI coding agent.  Provides Ch, Return all checkpoints sorted newest-first. (+20 more)

### Community 9 - "LSP Client"
Cohesion: 0.06
Nodes (43): _format_hover(), _format_locations(), _format_symbols(), _format_symbols_recursive(), LSPServer, LSPServerConfig, LSPServerError, path_to_uri() (+35 more)

### Community 10 - "MCP Integration"
Cohesion: 0.05
Nodes (54): call_tool(), _clear_token(), _connect_http(), connect_server(), _connect_stdio(), disconnect_server(), discover_mcp_configs(), _ensure_token_dir() (+46 more)

### Community 11 - "Hooks System"
Cohesion: 0.04
Nodes (41): allow(), block(), build_hook_context(), collect_block_reasons(), collect_messages(), collect_warnings(), _fallback_result(), from_dict() (+33 more)

### Community 12 - "App Server"
Cohesion: 0.07
Nodes (25): Minimal Textual-based terminal app shell for Wisp., Fallback shim when Textual is unavailable in the environment., Foundational full-screen terminal app for Wisp., Protocol-first app-server methods for Wisp clients., Minimal request handler for app-style runtime methods., Handle one JSON-RPC request against the runtime., WispAppServer, AppEvent (+17 more)

### Community 13 - "Fact Memory"
Cohesion: 0.12
Nodes (35): add_fact(), clear_memory(), _count_facts(), _evict_one(), _fact_content(), format_memory_block(), _get_fact_list(), _get_memory_file() (+27 more)

### Community 14 - "Task Planner"
Cohesion: 0.09
Nodes (20): from_dict(), _generate_plan_id(), _now_iso(), parse_plan_from_text(), Plan, Structured planning and task decomposition for Wisp.  Breaks down user requests, Return the next ready task (all dependencies done, status pending)., Return (done_count, total_count). (+12 more)

### Community 15 - "Code Indexer"
Cohesion: 0.1
Nodes (32): build_index(), CodeIndex, _extract_go(), _extract_javascript(), _extract_python(), _extract_ruby(), _extract_rust(), _extract_symbols() (+24 more)

### Community 16 - "Diff Engine"
Cohesion: 0.09
Nodes (33): apply_edit_with_diff(), apply_edits_to_content(), compute_edit_diff(), _compute_line_diff(), detect_line_ending(), DiffHunk, DiffResult, EditResult (+25 more)

### Community 17 - "Git Context"
Cohesion: 0.11
Nodes (31): commit(), create_branch(), create_pr(), format_git_context(), format_git_status_short(), get_file_diff(), get_git_state(), get_workspace_diff() (+23 more)

### Community 18 - "Project Context Detection"
Cohesion: 0.09
Nodes (30): _check_cargo_toml(), _check_docker(), _check_gemfile(), _check_go_mod(), _check_makefile(), _check_package_json(), _check_pyproject_toml(), _check_requirements_txt() (+22 more)

### Community 19 - "Agent Core"
Cohesion: 0.09
Nodes (8): _build_skills_block(), _build_skills_block_from_skills(), _collect_hook_messages(), _generate_agent_id(), _get_modified_args(), _parse_tool_call(), _remove_oldest_turn(), _should_block_hook()

### Community 20 - "Event System"
Cohesion: 0.06
Nodes (21): approval_request(), checkpoint_created(), content(), describe_event_type(), done(), error(), EventBus, Event system for Wisp SDK — structured events emitted by the agent core.  All I/ (+13 more)

### Community 21 - "Markdown Parser"
Cohesion: 0.09
Nodes (29): _clean_markdown(), CodeBlock, extract_code_blocks(), extract_front_matter(), extract_thinking(), _extract_thinking_sections(), format_code_block(), MarkdownDocument (+21 more)

### Community 22 - "Docker Sandbox"
Cohesion: 0.1
Nodes (11): DockerSandbox, get_sandbox(), NoopSandbox, Sandbox providers for isolating agent tool execution.  DockerSandbox runs comman, Runs commands directly on the host with optional resource limits.      Always av, Abstract interface for sandboxed command execution., Get or create the global sandbox singleton.      Tries Docker first, falls back, Reset the global sandbox singleton (for testing). (+3 more)

### Community 23 - "Skills System"
Cohesion: 0.12
Nodes (19): discover_skills(), find_skill(), _get_ontology_client(), _get_ontology_path(), has_ontology(), match_skill_via_ontology(), parse_skill(), Skills — Warp-compatible skill discovery and parsing.  Supports the same SKILL.m (+11 more)

### Community 24 - "Message Format"
Cohesion: 0.13
Nodes (21): build_image_part(), build_text_part(), extract_data_urls(), extract_images(), extract_text(), merge_content(), Message format utilities for multimodal (text + image) content.  Internal format, Validate a list of data URLs. Returns (valid_urls, errors). (+13 more)

### Community 25 - "Semantic Index"
Cohesion: 0.11
Nodes (13): _chunk_by_size(), CodeChunk, conn(), Semantic codebase index — embedding-based code search for agent context.  Chunks, Find all indexable files in the workspace., Split a file into chunks at function/class boundaries., Generate embeddings via Ollama embedding API., Index a single file: chunk, embed, store. Returns chunk count. (+5 more)

### Community 26 - "VS Code Server"
Cohesion: 0.16
Nodes (19): _code_cli(), _get_editor_state(), handle_call_tool(), handle_initialize(), handle_list_tools(), main(), _open_file(), VS Code MCP server — exposes VS Code editor capabilities as MCP tools.  Allows W (+11 more)

### Community 27 - "Error Diagnosis"
Cohesion: 0.15
Nodes (16): diagnose(), diagnose_tool_error(), Diagnosis, extract_error_message(), format_diagnosis_block(), identify_changed_files(), parse_traceback(), Error diagnosis — parse stack traces, classify errors, identify root causes.  An (+8 more)

### Community 28 - "Stream Parser"
Cohesion: 0.16
Nodes (10): EventStreamParser, parse_stream(), Event stream parser — handles NDJSON and SSE (text/event-stream) from LLM APIs., Feed raw HTTP response bytes and yield parsed JSON events.          Called repea, Process any remaining buffered bytes after the stream ends.          Call this w, Convenience: parse an HTTP stream response (iter_content) into JSON events., Parse a streaming HTTP response body as NDJSON or SSE.      Auto-detects format, Detect stream format from a line (called once on first non-empty line). (+2 more)

### Community 29 - "Configuration"
Cohesion: 0.15
Nodes (15): get_config_path(), get_schema(), get_setting(), load_config(), Configuration for Wisp — reads settings from environment, CLI args, and config f, Return a copy of the settings schema., Validate config values against the schema.      Returns a list of error messages, Get a human-readable type name, handling unions. (+7 more)

### Community 30 - "Arena Benchmarking"
Cohesion: 0.18
Nodes (4): ArenaEntry, ArenaRunner, get_arena(), _git_diff()

### Community 31 - "Terminal Colors"
Cohesion: 0.17
Nodes (8): is_enabled(), Minimal ANSI color support for Wisp terminal output.  Zero dependencies. Respect, Lazy ANSI style wrapper., Apply style even when colors are disabled (for testing)., Remove ANSI escape codes from text., Return True if colors are currently enabled., strip_ansi(), _Style

### Community 32 - "Subagent Runner"
Cohesion: 0.31
Nodes (7): code_reviewer(), _compact_args(), doc_writer(), _extract_files_changed(), security_auditor(), SubagentSpec, test_writer()

### Community 33 - "Wisp SDK"
Cohesion: 0.24
Nodes (1): Wisp

### Community 34 - "Background Agent"
Cohesion: 0.36
Nodes (2): BackgroundRunner, get_runner()

### Community 35 - "Code Completion"
Cohesion: 0.33
Nodes (6): build_completion_prompt(), CompletionResult, _extract_context(), generate_completion(), Split file content into prefix (before cursor) and suffix (after cursor).      R, Build a fill-in-the-middle prompt for code completion.

### Community 36 - "Suggestion Watcher"
Cohesion: 0.29
Nodes (4): FileSuggestion, SuggestionWatcher — polls workspace for recently changed files and surfaces LSP, Return list of file paths that have changed since last scan., Scan for changes and return files with diagnostic counts.

### Community 37 - "Community 37"
Cohesion: 1.0
Nodes (1): Convenience factory for an allow decision.

### Community 38 - "Community 38"
Cohesion: 1.0
Nodes (1): Convenience factory for a block decision.

### Community 39 - "Community 39"
Cohesion: 1.0
Nodes (1): Convenience factory for a modify decision.

### Community 40 - "Community 40"
Cohesion: 1.0
Nodes (1): Convenience factory for a warn decision.

### Community 41 - "Community 41"
Cohesion: 1.0
Nodes (1): True if this result blocks the operation.

### Community 42 - "Community 42"
Cohesion: 1.0
Nodes (1): True if this result modifies arguments.

### Community 43 - "Community 43"
Cohesion: 1.0
Nodes (1): True if this result is a warning.

### Community 44 - "Community 44"
Cohesion: 1.0
Nodes (1): Deserialize from a dict (for loading config files).

### Community 45 - "Community 45"
Cohesion: 1.0
Nodes (1): Number of registered hooks (including disabled).

### Community 46 - "Community 46"
Cohesion: 1.0
Nodes (1): Deserialize from a plain dict.

### Community 47 - "Community 47"
Cohesion: 1.0
Nodes (1): A checkpoint is valid if it has backing data to restore from.

### Community 48 - "Community 48"
Cohesion: 1.0
Nodes (1): Run a git subprocess with a timeout. Returns (returncode, stdout, stderr).

### Community 49 - "Community 49"
Cohesion: 1.0
Nodes (1): Return True if *path* is a readable, well-formed tar.gz.

### Community 50 - "Community 50"
Cohesion: 1.0
Nodes (1): Walk *workspace* and return relative paths of tracked files.

### Community 51 - "Community 51"
Cohesion: 1.0
Nodes (1): Return True if this sandbox is ready to use.

### Community 52 - "Community 52"
Cohesion: 1.0
Nodes (1): Run a shell command and return (exit_code, stdout, stderr).

### Community 53 - "Community 53"
Cohesion: 1.0
Nodes (1): Read a file from the sandbox filesystem.

### Community 54 - "Community 54"
Cohesion: 1.0
Nodes (1): Write a file to the sandbox filesystem.

### Community 55 - "Community 55"
Cohesion: 1.0
Nodes (1): Human-readable sandbox name (e.g. 'docker', 'host').

### Community 56 - "Community 56"
Cohesion: 1.0
Nodes (1): Compute checkpoint hash for validation.

### Community 57 - "Community 57"
Cohesion: 1.0
Nodes (1): Get language-specific symbol boundary patterns.

### Community 58 - "Community 58"
Cohesion: 1.0
Nodes (1): Split line range into size-bounded chunks with overlap.

### Community 59 - "Community 59"
Cohesion: 1.0
Nodes (1): Shortcut for content/thinking events.

### Community 60 - "Community 60"
Cohesion: 1.0
Nodes (1): Shortcut for tool_call / tool_result events.

### Community 61 - "Community 61"
Cohesion: 1.0
Nodes (1): True if this event signals the end of a turn.

### Community 62 - "Community 62"
Cohesion: 1.0
Nodes (1): Deserialize from a dict (round-trips with to_dict).

### Community 63 - "Community 63"
Cohesion: 1.0
Nodes (1): Load a PluginManifest from a plugin.json file.          Args:             path:

### Community 64 - "Community 64"
Cohesion: 1.0
Nodes (1): Verify the provider is reachable and the configured model is usable.

### Community 65 - "Community 65"
Cohesion: 1.0
Nodes (1): Return available models in provider-specific shape.

### Community 66 - "Community 66"
Cohesion: 1.0
Nodes (1): Return the effective context length for the configured model.

### Community 67 - "Community 67"
Cohesion: 1.0
Nodes (1): Run a non-streaming generation.

### Community 68 - "Community 68"
Cohesion: 1.0
Nodes (1): Run a streaming generation and yield stream events.

## Knowledge Gaps
- **467 isolated node(s):** `Event stream parser — handles NDJSON and SSE (text/event-stream) from LLM APIs.`, `Raised when event stream parsing fails.`, `Parse a streaming HTTP response body as NDJSON or SSE.      Auto-detects format`, `Detect stream format from a line (called once on first non-empty line).`, `NDJSON mode: each line is a self-contained JSON object.` (+462 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `Community 37`** (1 nodes): `Convenience factory for an allow decision.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 38`** (1 nodes): `Convenience factory for a block decision.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 39`** (1 nodes): `Convenience factory for a modify decision.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 40`** (1 nodes): `Convenience factory for a warn decision.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 41`** (1 nodes): `True if this result blocks the operation.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 42`** (1 nodes): `True if this result modifies arguments.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 43`** (1 nodes): `True if this result is a warning.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 44`** (1 nodes): `Deserialize from a dict (for loading config files).`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 45`** (1 nodes): `Number of registered hooks (including disabled).`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 46`** (1 nodes): `Deserialize from a plain dict.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 47`** (1 nodes): `A checkpoint is valid if it has backing data to restore from.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 48`** (1 nodes): `Run a git subprocess with a timeout. Returns (returncode, stdout, stderr).`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 49`** (1 nodes): `Return True if *path* is a readable, well-formed tar.gz.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 50`** (1 nodes): `Walk *workspace* and return relative paths of tracked files.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 51`** (1 nodes): `Return True if this sandbox is ready to use.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 52`** (1 nodes): `Run a shell command and return (exit_code, stdout, stderr).`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 53`** (1 nodes): `Read a file from the sandbox filesystem.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 54`** (1 nodes): `Write a file to the sandbox filesystem.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 55`** (1 nodes): `Human-readable sandbox name (e.g. 'docker', 'host').`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 56`** (1 nodes): `Compute checkpoint hash for validation.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 57`** (1 nodes): `Get language-specific symbol boundary patterns.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 58`** (1 nodes): `Split line range into size-bounded chunks with overlap.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 59`** (1 nodes): `Shortcut for content/thinking events.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 60`** (1 nodes): `Shortcut for tool_call / tool_result events.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 61`** (1 nodes): `True if this event signals the end of a turn.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 62`** (1 nodes): `Deserialize from a dict (round-trips with to_dict).`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 63`** (1 nodes): `Load a PluginManifest from a plugin.json file.          Args:             path:`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 64`** (1 nodes): `Verify the provider is reachable and the configured model is usable.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 65`** (1 nodes): `Return available models in provider-specific shape.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 66`** (1 nodes): `Return the effective context length for the configured model.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 67`** (1 nodes): `Run a non-streaming generation.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 68`** (1 nodes): `Run a streaming generation and yield stream events.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `WispAgentCore` connect `Agent Decision Rationale` to `Subagent Runner`, `Wisp SDK`, `Agent Behavior Rationale`, `Agent Factory`, `Background Agent`, `Agent Memory System`, `App Server`, `Agent Core`, `Arena Benchmarking`?**
  _High betweenness centrality (0.189) - this node is a cross-community bridge._
- **Why does `WispConfig` connect `Agent Decision Rationale` to `Subagent Runner`, `Wisp SDK`, `Agent Behavior Rationale`, `Code Completion`, `ACP Adapter Protocol`, `Agent Factory`, `Background Agent`, `Agent Memory System`, `Plugin Discovery`, `App Server`, `Configuration`, `Arena Benchmarking`?**
  _High betweenness centrality (0.171) - this node is a cross-community bridge._
- **Why does `Persistence helpers for the terminal app runtime.` connect `Agent Factory` to `Agent Decision Rationale`, `Wisp SDK`, `Agent Memory System`, `Plugin Discovery`, `LSP Client`, `App Server`?**
  _High betweenness centrality (0.113) - this node is a cross-community bridge._
- **Are the 263 inferred relationships involving `WispConfig` (e.g. with `PromptRequest` and `BashRequest`) actually correct?**
  _`WispConfig` has 263 INFERRED edges - model-reasoned connections that need verification._
- **Are the 211 inferred relationships involving `WispAgentCore` (e.g. with `PromptRequest` and `BashRequest`) actually correct?**
  _`WispAgentCore` has 211 INFERRED edges - model-reasoned connections that need verification._
- **Are the 162 inferred relationships involving `SessionManager` (e.g. with `PromptRequest` and `BashRequest`) actually correct?**
  _`SessionManager` has 162 INFERRED edges - model-reasoned connections that need verification._
- **Are the 152 inferred relationships involving `Session` (e.g. with `PromptRequest` and `BashRequest`) actually correct?**
  _`Session` has 152 INFERRED edges - model-reasoned connections that need verification._