from aegis.config import Settings
from aegis.hermes.mcp_gateway import HermesMcpGateway
from aegis.hermes.prompt import AEGIS_SYSTEM_PROMPT
from aegis.hermes.runtime import HermesAgent
from aegis.intents import Decision, Intent


class FakeLlmClient:
    def __init__(self, intent: Intent) -> None:
        self._intent = intent
        self.calls: list[dict[str, object]] = []

    def decide(self, *, system_prompt: str, position_summary: dict, gateway) -> Intent:
        self.calls.append(
            {"system_prompt": system_prompt, "position_summary": position_summary, "gateway": gateway}
        )
        return self._intent


class FakeSession:
    def list_tools(self) -> list[str]:
        return ["execute_protocol_action"]

    def call_tool(self, name: str, arguments: dict) -> dict:
        return {}


def test_hermes_agent_returns_llm_intent_and_uses_aegis_system_prompt() -> None:
    settings = Settings(_env_file=None, keeperhub_api_key="kh_test123")  # type: ignore[call-arg]
    gateway = HermesMcpGateway(FakeSession(), settings)
    expected_intent = Intent(decision=Decision.DO_NOTHING, rationale="healthy position")
    llm = FakeLlmClient(expected_intent)

    agent = HermesAgent(llm, gateway)
    result = agent.decide({"healthFactor": "4000000000000000000"})

    assert result is expected_intent
    assert llm.calls[0]["system_prompt"] == AEGIS_SYSTEM_PROMPT
    assert llm.calls[0]["position_summary"] == {"healthFactor": "4000000000000000000"}
    assert llm.calls[0]["gateway"] is gateway
