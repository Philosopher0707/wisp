"""Agent factory — creates WispAgent instances configured for a specific role."""

from __future__ import annotations

import logging
from typing import Optional

from wisp.agent import WispAgent
from wisp.config import WispConfig

from .roles import AgentRole, RoleConfig, ROLE_CONFIGS

logger = logging.getLogger(__name__)


class AgentFactory:
    """Creates role-configured agents for the swarm.

    Each agent is a fresh WispAgent instance with:
    - A role-specific system prompt
    - A constrained toolset
    - Shared Ollama HTTP session (for connection pooling)
    """

    def __init__(self, base_config: WispConfig, parent_agent: Optional[WispAgent] = None):
        self.base_config = base_config
        self.parent_agent = parent_agent

    def create(self, role: str, agent_id: str, model: Optional[str] = None) -> WispAgent:
        """Create a new agent for the given role.

        Args:
            role: One of the AgentRole constants.
            agent_id: Unique ID for this agent instance.
            model: Override the Ollama model (None = inherit from base_config).

        Returns:
            A configured WispAgent ready to join the swarm.
        """
        config = ROLE_CONFIGS.get(role, ROLE_CONFIGS[AgentRole.CODER])

        # Build child config from base
        child_cfg = WispConfig()
        child_cfg.model = model or config.model or self.base_config.model
        child_cfg.ollama_url = self.base_config.ollama_url
        child_cfg.auto_approve = self.base_config.auto_approve
        child_cfg.show_thinking = self.base_config.show_thinking
        child_cfg.workspace = self.base_config.workspace

        agent = WispAgent(config=child_cfg, agent_id=agent_id, role=role)

        # Reuse parent's HTTP session for connection pooling
        if self.parent_agent is not None:
            agent.client._session = self.parent_agent.client._session

        # Inject role-specific system prompt
        agent._role_system_extra = config.system_prompt

        # Constrain tools by filtering schemas
        if config.allowed_tools != ["all"]:
            agent._allowed_tools = set(config.allowed_tools)

        logger.info("Created %s agent: %s (model=%s)", role, agent_id, child_cfg.model)
        return agent

    def get_role_config(self, role: str) -> RoleConfig:
        """Return the configuration for a role."""
        return ROLE_CONFIGS.get(role, ROLE_CONFIGS[AgentRole.CODER])
