"""Hermes: Aegis's LLM-driven decision layer.

Hermes performs READ -> ANALYZE -> DECIDE only, and its sole output is a
plain aegis.intents.Intent. It never holds a capability that can simulate
or execute anything — its only tool access is HermesMcpGateway, which is
read/discovery-only and independently allowlists tool names, Aave V3
actionType values, and chain IDs. Everything from POLICY CHECK onward
(aegis.policy, aegis.execution) is deterministic and has no LLM in its
call path.
"""

from aegis.hermes.mcp_gateway import (
    ALLOWED_READ_ACTION_TYPES,
    ALLOWED_TOOL_NAMES,
    HermesMcpGateway,
    McpDiagnostics,
    McpSession,
    ToolNotAllowedError,
    create_default_session,
)
from aegis.hermes.prompt import AEGIS_SYSTEM_PROMPT
from aegis.hermes.runtime import AnthropicLlmClient, HermesAgent, HermesDidNotDecideError, LlmClient

__all__ = [
    "AEGIS_SYSTEM_PROMPT",
    "ALLOWED_READ_ACTION_TYPES",
    "ALLOWED_TOOL_NAMES",
    "AnthropicLlmClient",
    "HermesAgent",
    "HermesDidNotDecideError",
    "HermesMcpGateway",
    "LlmClient",
    "McpDiagnostics",
    "McpSession",
    "ToolNotAllowedError",
    "create_default_session",
]
