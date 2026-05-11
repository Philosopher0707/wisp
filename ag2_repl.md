# AG2 Web Research Findings

*Date researched: Current session*
*Sources: ag2.ai, GitHub (ag2ai/ag2), PyPI, docs.ag2.ai*

---

## Overview

**AG2** (formerly **AutoGen**) is an open-source programming framework for building AI agents and facilitating cooperation among multiple agents to solve tasks. It is positioned as an "Open-Source AgentOS" — an operating system for agentic AI.

- **Former name**: AutoGen
- **License**: Apache 2.0
- **Python requirement**: >= 3.10
- **PyPI package**: `ag2` (alias `autogen`)
- **Latest PyPI version**: 0.12.3 (as of research date)
- **Project leads**: Chi Wang & Qingyun Wu (support@ag2.ai)
- **Community**: Maintained by a dynamic group of volunteers from several organizations

---

## Core Value Proposition

AG2 aims to streamline the development and research of agentic AI by offering:

- **Multi-agent intelligence at scale**: Build, orchestrate, and evolve systems of AI agents as an AI workforce.
- **Universal Framework Interoperability**: Connect agents from AG2, Google ADK, OpenAI, LangChain into unified teams.
- **Cross-Platform Coordination**: Assemble dynamic teams of specialized personas.
- **Unified State Management**: Maintain a "shared brain" across task lifecycles.
- **Standardized Protocols**: Support for A2A (Agent-to-Agent) and MCPs (Model Context Protocols) with enterprise security.

---

## Key Features

### 1. Agent Concepts
- **ConversableAgent**: The fundamental building block — handles message exchange and response generation. Base class for all agents.
- **AssistantAgent**: AI assistant agents that use LLMs to generate responses.
- **UserProxyAgent**: Acts on behalf of a human user, can execute code and solicit human input.

### 2. Conversation Patterns
- **Group Chat**: Multiple agents conversing in a shared context.
- **Swarm**: Dynamic agent swarms for distributed task solving.
- **Nested Chats**: Hierarchical conversation structures.
- **Sequential Chats**: Ordered multi-agent workflows.
- **Custom Orchestration**: Register custom reply methods for bespoke workflows.

### 3. Human-in-the-Loop
- Seamless integration of human feedback and oversight within autonomous agent workflows.
- Human input can be added at any point in the conversation.

### 4. Tools & Capabilities
- **Tool Use**: Programs that can be registered, invoked, and executed by agents.
- **Code Execution**: Agents can write and execute code (with Docker support optional).
- **RAG (Retrieval-Augmented Generation)**: Built-in support for retrieval-based augmentation.
- **Structured Outputs**: Support for structured response formats.

### 5. LLM Support
- OpenAI (GPT models)
- Anthropic (Claude)
- Google (Gemini)
- DeepSeek
- Groq
- Cohere
- Mistral
- Ollama (local models)
- Together AI
- Cerebras
- And more via extensible configuration

---

## Installation

```bash
# Windows/Linux
pip install ag2[openai]

# Mac
pip install 'ag2[openai]'
```

Minimal dependencies are installed by default. Extensive extras are available for specific integrations (e.g., `[anthropic]`, `[gemini]`, `[docker]`, `[rag]`, `[neo4j]`, `[redis]`, etc.).

---

## Quick Start Example

```python
from autogen import AssistantAgent, UserProxyAgent, LLMConfig

llm_config = LLMConfig.from_json(path="OAI_CONFIG_LIST")

assistant = AssistantAgent("assistant", llm_config=llm_config)
user_proxy = UserProxyAgent(
    "user_proxy",
    code_execution_config={"work_dir": "coding", "use_docker": False}
)

user_proxy.run(
    assistant,
    message="Summarize the main differences between Python lists and tuples."
).process()
```

---

## Architecture & Enterprise Vision

AG2 is evolving beyond a simple framework into a full **AgentOS** with three pillars:

1. **Orchestrator**: Universal runtime for multi-agent teams. Removes barriers between "islands of intelligence."
2. **Studio**: Enterprise-ready development and deployment environment.
3. **Applications**: Pre-built solutions for common enterprise use cases.

### Enterprise Claims
- Up to **70% faster workflows**
- Up to **5x productivity gain**
- **Full decision auditability**

---

## Roadmap to v1.0

> **Important**: AG2 is on the path to v1.0. The current framework will be tidied up through deprecations over the next few minor versions and moved to maintenance mode. The **beta framework (`autogen.beta`)** will become the official version of AG2 at v1.0.

---

## Ecosystem & Integrations

AG2 supports a vast ecosystem of integrations via optional dependencies:

- **Cloud/LLM Providers**: OpenAI, Anthropic, Google, Azure, AWS Bedrock, Cohere, Mistral, Groq, Together, Cerebras
- **Databases**: Neo4j, MongoDB, PostgreSQL (pgvector), Couchbase, Qdrant, CosmosDB, Redis, FalkorDB
- **Search**: DuckDuckGo, Google Search, Tavily, Perplexity, Exa
- **Communication**: Slack, Discord, Telegram, Twilio
- **Execution**: Docker, Daytona, Jupyter
- **Framework Interop**: LangChain, CrewAI, Pydantic AI
- **Protocols**: A2A, MCP
- **Other**: Browser-use, Crawl4AI, Wikipedia, YepCode

---

## Resources

- **Homepage**: https://ag2.ai
- **Documentation**: https://docs.ag2.ai
- **GitHub**: https://github.com/ag2ai/ag2
- **PyPI**: https://pypi.org/project/ag2/
- **Discord**: Community server available
- **Examples**: Dedicated application repository and Jupyter notebook collection
- **Learning**: Courses available on DeepLearning.AI

---

## Summary

AG2 is a mature, production-oriented multi-agent framework evolved from Microsoft's AutoGen project. It distinguishes itself through:

1. **Breadth of integration** — massive ecosystem of LLM and tool providers
2. **Conversation flexibility** — multiple built-in patterns for agent orchestration
3. **Enterprise readiness** — auditability, security, and state management
4. **Open governance** — community-maintained, fully open-source
5. **Future-proofing** — clear v1.0 roadmap with a next-gen beta framework

It competes in the same space as LangGraph, CrewAI, and OpenAI's Swarm, but differentiates through its "AgentOS" vision of universal interoperability and its strong academic/research lineage from AutoGen.
