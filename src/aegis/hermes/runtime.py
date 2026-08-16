"""Hermes: Aegis's LLM-driven decision layer.

Hermes performs READ -> ANALYZE -> DECIDE and returns exactly one
aegis.intents.Intent. It has no callable that can simulate or execute
anything — its only tool access is HermesMcpGateway, which is
read/discovery-only. Everything from POLICY CHECK onward is deterministic
Python code in aegis.policy / aegis.execution, orchestrated by
aegis.recovery.run_with_recovery, with no LLM in that call path.
"""

from __future__ import annotations

import json
from typing import Protocol

from aegis.config import Settings
from aegis.hermes.mcp_gateway import ALLOWED_READ_ACTION_TYPES, HermesMcpGateway, ToolNotAllowedError
from aegis.hermes.prompt import AEGIS_SYSTEM_PROMPT
from aegis.intents import Intent


class LlmClient(Protocol):
    """The minimal LLM surface HermesAgent depends on.

    AnthropicLlmClient below is the real implementation; tests inject a
    fake that returns a canned Intent, so HermesAgent's own tests never
    need a live API key or network access.
    """

    def decide(
        self, *, system_prompt: str, position_summary: dict[str, object], gateway: HermesMcpGateway
    ) -> Intent: ...


class HermesAgent:
    """Thin wrapper tying an LlmClient to Hermes's system prompt and
    restricted gateway. Holds no other capability."""

    def __init__(self, llm_client: LlmClient, gateway: HermesMcpGateway) -> None:
        self._llm = llm_client
        self._gateway = gateway

    def decide(self, position_summary: dict[str, object]) -> Intent:
        return self._llm.decide(
            system_prompt=AEGIS_SYSTEM_PROMPT,
            position_summary=position_summary,
            gateway=self._gateway,
        )


class HermesDidNotDecideError(Exception):
    """Raised when the model exhausts its turn budget without producing a final Intent."""


class AnthropicLlmClient:
    """Real LLM client: Anthropic's Messages API with tool use, restricted
    to HermesMcpGateway's allowlisted tools.

    Lazy-imports `anthropic` so it is only required for the real runtime
    path (`pip install -e ".[hermes]"`), not for running this project's
    test suite.
    """

    def __init__(self, settings: Settings, max_turns: int = 6) -> None:
        try:
            import anthropic
        except ImportError as exc:
            raise RuntimeError(
                "The 'anthropic' package is required for AnthropicLlmClient. "
                "Install it with: pip install -e '.[hermes]'"
            ) from exc
        if not settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not configured")

        self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self._model = settings.aegis_hermes_model
        self._max_turns = max_turns

    def decide(
        self, *, system_prompt: str, position_summary: dict[str, object], gateway: HermesMcpGateway
    ) -> Intent:
        tools = self._tool_definitions()
        messages: list[dict[str, object]] = [
            {
                "role": "user",
                "content": (
                    "Inspect the DeFi position below (refresh it with your tools if you need "
                    "to), assess its risk, and decide. Respond with ONLY a single final JSON "
                    "object matching the Intent schema once you are done reasoning and calling "
                    "tools — no other text alongside it.\n\nPosition summary:\n"
                    + json.dumps(position_summary)
                ),
            }
        ]

        for _ in range(self._max_turns):
            response = self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                system=system_prompt,
                tools=tools,
                messages=messages,
            )
            messages.append({"role": "assistant", "content": response.content})

            tool_uses = [block for block in response.content if block.type == "tool_use"]
            if not tool_uses:
                text = "".join(block.text for block in response.content if block.type == "text")
                return Intent.model_validate_json(text)

            # Sequential only — never dispatch these concurrently, per this
            # project's rule against parallel KeeperHub tool execution.
            tool_results = []
            for call in tool_uses:
                try:
                    result: object = gateway.call_tool(call.name, call.input)
                except ToolNotAllowedError as exc:
                    result = {"error": str(exc)}
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": json.dumps(result),
                    }
                )
            messages.append({"role": "user", "content": tool_results})

        raise HermesDidNotDecideError(
            f"Hermes did not produce a final Intent within {self._max_turns} turns"
        )

    def _tool_definitions(self) -> list[dict[str, object]]:
        # Hand-written schemas matching the real KeeperHub MCP tools
        # inspected earlier in this project, narrowed to what
        # HermesMcpGateway actually allows through.
        return [
            {
                "name": "execute_protocol_action",
                "description": "Read an Aave V3 account value. Read-only actions only.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "actionType": {
                            "type": "string",
                            "enum": sorted(ALLOWED_READ_ACTION_TYPES),
                        },
                        "params": {"type": "object"},
                    },
                    "required": ["actionType", "params"],
                },
            },
            {
                "name": "search_protocol_actions",
                "description": "Discover available Aave V3 actions and their required params.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "protocol": {"type": "string"},
                        "query": {"type": "string"},
                    },
                },
            },
            {
                "name": "list_action_schemas",
                "description": "List supported chains and action schemas.",
                "input_schema": {"type": "object", "properties": {}},
            },
            {
                "name": "get_spending_limits",
                "description": "Read the org's daily direct-execution spending caps and usage.",
                "input_schema": {"type": "object", "properties": {}},
            },
        ]
