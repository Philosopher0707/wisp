# AG2 (formerly AutoGen) — Overview

> **Research date:** 2026-05-12  
> **Latest AG2 version:** v0.12.3 (released 2026-05-06)

---

## What Is AG2?

**AG2** (formerly **AutoGen**) is an open-source programming framework for building AI agents and orchestrating cooperation among multiple agents to solve tasks. It aims to streamline the development and research of agentic AI by providing primitives for multi-agent conversation, tool use, and workflow automation.

AG2 is positioning itself as an **"AgentOS"** — an operating system for agents — rather than just a library. It provides infrastructure for orchestration, observability, scale, and enterprise governance.

- **GitHub:** https://github.com/ag2ai/ag2
- **Website:** https://www.ag2.ai/
- **Documentation:** https://docs.ag2.ai/
- **PyPI:** `ag2` (also published under aliases `autogen` and `pyautogen`)

---

## Who Built It?

- **Original creators:**
  - **Qingyun Wu** — Penn State University
  - **Chi Wang** — Microsoft Research
  - A coalition of academic and industry collaborators

- **Current maintainers:**
  - The project is maintained by a dynamic group of volunteers from several organizations.
  - Project administrators: **Chi Wang** and **Qingyun Wu** (contact: support@ag2.ai).
  - The core team forked from the original Microsoft AutoGen repository in late 2024 and rebranded as **AG2** under the `ag2ai` GitHub organization.

---

## Key Features

### Core Framework
- **ConversableAgent** — The fundamental building block; agents send, receive, and react to messages.
- **Multi-agent orchestration** — Built-in conversation patterns: group chats, swarms, nested chats, sequential chats, and handoffs.
- **LLM interoperability** — Supports OpenAI, Azure OpenAI, Anthropic, Google Gemini, Groq, Ollama, Cohere, Mistral, DeepSeek, and more.
- **Tool use & code execution** — Agents can call external APIs, execute Python code (with Docker/Daytona sandbox support in beta), and use built-in tools.
- **Human-in-the-loop** — Native support for pausing workflows to request human approval or input.
- **RAG (Retrieval-Augmented Generation)** — Integrations with ChromaDB, Qdrant, MongoDB, Couchbase, PGVector, Neo4j, and FalkorDB.

### Enterprise & Production Features
- **A2A (Agent-to-Agent) Protocol** — Native support for the Agent2Agent protocol (v1.0 as of v0.12.3), enabling cross-framework agent communication.
- **MCP (Model Context Protocol)** — Support for connecting to MCP servers for tool and context sharing.
- **Framework interoperability** — Connect agents from AG2, Google ADK, OpenAI, LangChain, and PydanticAI into unified teams.
- **Observability & tracing** — Event-driven logging, streaming runs, and telemetry for debugging agent workflows.
- **Stateful agents (Agent Harness — Beta)** — Persistent memory across sessions, context window management, knowledge stores (Memory, Disk, SQLite, Redis), and compaction/aggregation policies.
- **Remote agents** — Deploy agents as microservices behind standard APIs with full remote tool-calling and chat history management.
- **AG2 Studio** — Visual drag-and-drop workflow builder for designing agent topologies without boilerplate code.
- **Universal Assistant** — A pre-trained meta-layer that routes user requests to the correct specialized agent or workflow.

### Beta Framework (Path to v1.0)
- AG2 is actively developing a new beta framework (`autogen.beta`) built around an async, event-driven actor model.
- At **v1.0**, the beta framework will become the official AG2, while the original codebase moves to maintenance mode on a separate branch.

---

## Latest Version

| Detail | Info |
|--------|------|
| **Current stable** | **v0.12.3** (released May 6, 2026) |
| **Python requirement** | >= 3.10 |
| **License** | Apache-2.0 |
| **Install** | `pip install ag2[openai]` |

### Recent v0.12.x Highlights
- **A2A v1.0 compatibility** — Aligns with the latest Agent2Agent protocol specification.
- **Sandboxed Code Execution (Beta)** — `SandboxCodeTool` with Daytona and Docker backends.
- **Agent Harness (Beta)** — Stateful agents with persistence, assembly policies, compaction, and aggregation.
- **Search tools refresh** — Async SDK clients for Perplexity, Exa, and other search integrations.
- **Deprecation notices** — Preparing legacy APIs for removal in v0.14 as the project transitions to v1.0.

---

## AG2 vs. AutoGen v1 (Original AutoGen / v0.2)

| Dimension | AutoGen (v0.2) — Microsoft | AG2 (Community Fork) |
|-----------|---------------------------|----------------------|
| **Origin** | Microsoft Research project | Forked by original creators (Chi Wang, Qingyun Wu) |
| **Governance** | Microsoft-led | Community-driven, open governance |
| **Architecture** | Synchronous, chat-constrained loops | Evolving to async, event-driven actor model (beta) |
| **Backward compatibility** | v0.2 maintained but no new features | Full backward compatibility with AutoGen v0.2 code |
| **Scope** | Research library | Production-grade "AgentOS" with Studio, remote agents, observability |
| **State management** | Ephemeral (state lost on crash) | Persistent, resumable workflows with long-term memory |
| **Deployment** | Self-managed wrappers required | Native remote agent support (A2A, APIs, microservices) |
| **Visual tooling** | Limited | AG2 Studio for visual orchestration |
| **Interoperability** | Microsoft-centric | Framework-agnostic (LangChain, Google ADK, OpenAI, etc.) |
| **PyPI packages** | `autogen-agentchat`, `autogen-core` (v0.4) | `ag2`, `autogen`, `pyautogen` |

### The Microsoft Split
In late 2024, the original AutoGen creators departed from Microsoft and established AG2 as an independent, community-driven project. Microsoft continued AutoGen under its own stewardship with a **complete rewrite (v0.4)** featuring:
- TypeScript support
- Async, event-driven actor model
- Distributed agent deployment
- Deeper Semantic Kernel integration

However, **AutoGen v0.4 is not backward compatible** with v0.2. Microsoft has since indicated plans to converge AutoGen's runtime into **Semantic Kernel**, leaving the original v0.2 architecture behind.

**AG2** preserves the familiar v0.2 patterns while adding enterprise-grade features, making it the recommended path for teams that want to migrate existing AutoGen code without a full rewrite.

---

## Release Roadmap (Path to v1.0)

| Version | Milestone |
|---------|-----------|
| **v0.12** | Deprecation notices for legacy APIs; beta development continues |
| **v0.13** | Transition period; community feedback incorporated; beta API refinements |
| **v0.14** | Deprecated features removed; beta moves to Release Candidate (RC) |
| **v1.0** | Beta becomes stable; original AG2 moves to `ag2-original` branch for maintenance |

---

## Quick Start

```bash
# Install AG2 with OpenAI support
pip install "ag2[openai]"
```

```python
from autogen import AssistantAgent, UserProxyAgent, LLMConfig

llm_config = LLMConfig.from_json(path="OAI_CONFIG_LIST")
assistant = AssistantAgent("assistant", llm_config=llm_config)
user_proxy = UserProxyAgent("user_proxy", code_execution_config={"work_dir": "coding", "use_docker": False})

user_proxy.run(assistant, message="Summarize the main differences between Python lists and tuples.").process()
```

---

## Summary

AG2 is the **community-driven continuation of AutoGen**, led by its original creators. It offers a stable, backward-compatible migration path from AutoGen v0.2 while evolving into a production-ready AgentOS with enterprise features like observability, persistent state, visual orchestration, and cross-framework interoperability. Teams currently on AutoGen v0.2 can transition to AG2 without rewriting their codebase, whereas Microsoft's AutoGen v0.4/Semantic Kernel path requires significant architectural changes.

---

*Sources: ag2.ai, github.com/ag2ai/ag2, docs.ag2.ai, PyPI, gettingstarted.ai, cohorte.co, dev.to*
