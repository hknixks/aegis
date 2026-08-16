import pytest

from aegis.config import Settings
from aegis.hermes.mcp_gateway import HermesMcpGateway, ToolNotAllowedError


class FakeSession:
    """Pretends to be a real MCP session exposing MORE tools than Hermes
    should ever be allowed to reach — the gateway must filter these down,
    not trust that the server only offers safe tools."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def list_tools(self) -> list[str]:
        return [
            "execute_protocol_action",
            "search_protocol_actions",
            "list_action_schemas",
            "get_spending_limits",
            "execute_transfer",
            "execute_contract_call",
            "delete_workflow",
            "tempo_release_hold",
        ]

    def call_tool(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        self.calls.append((name, arguments))
        return {"ok": True, "tool": name}


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, keeperhub_api_key="kh_test123")  # type: ignore[call-arg]


@pytest.fixture
def session() -> FakeSession:
    return FakeSession()


def test_list_tools_filters_to_allowlist(session: FakeSession, settings: Settings) -> None:
    gateway = HermesMcpGateway(session, settings)
    tools = gateway.list_tools()

    assert tools == [
        "execute_protocol_action",
        "get_spending_limits",
        "list_action_schemas",
        "search_protocol_actions",
    ]
    assert "execute_transfer" not in tools
    assert "delete_workflow" not in tools
    assert "tempo_release_hold" not in tools


def test_call_tool_rejects_disallowed_tool_name(session: FakeSession, settings: Settings) -> None:
    gateway = HermesMcpGateway(session, settings)
    with pytest.raises(ToolNotAllowedError, match="not in Hermes's allowlist"):
        gateway.call_tool("execute_transfer", {"chain_id": "84532"})
    assert session.calls == []


def test_call_tool_rejects_write_action_type(session: FakeSession, settings: Settings) -> None:
    gateway = HermesMcpGateway(session, settings)
    with pytest.raises(ToolNotAllowedError, match="not a read action"):
        gateway.call_tool(
            "execute_protocol_action",
            {"actionType": "aave-v3/repay", "params": {"network": "84532"}},
        )
    assert session.calls == []


def test_call_tool_allows_read_action_type(session: FakeSession, settings: Settings) -> None:
    gateway = HermesMcpGateway(session, settings)
    result = gateway.call_tool(
        "execute_protocol_action",
        {
            "actionType": "aave-v3/get-user-account-data",
            "params": {"network": "84532", "user": "0xWallet"},
        },
    )
    assert result == {"ok": True, "tool": "execute_protocol_action"}
    assert session.calls == [
        (
            "execute_protocol_action",
            {
                "actionType": "aave-v3/get-user-account-data",
                "params": {"network": "84532", "user": "0xWallet"},
            },
        )
    ]


def test_call_tool_rejects_mainnet_chain_id(session: FakeSession, settings: Settings) -> None:
    gateway = HermesMcpGateway(session, settings)
    with pytest.raises(ToolNotAllowedError, match="mainnet"):
        gateway.call_tool(
            "execute_protocol_action",
            {
                "actionType": "aave-v3/get-user-account-data",
                "params": {"network": "1", "user": "0xWallet"},
            },
        )
    assert session.calls == []


def test_call_tool_allows_base_sepolia(session: FakeSession, settings: Settings) -> None:
    gateway = HermesMcpGateway(session, settings)
    gateway.call_tool(
        "execute_protocol_action",
        {
            "actionType": "aave-v3/get-user-account-data",
            "params": {"network": "84532", "user": "0xWallet"},
        },
    )
    assert len(session.calls) == 1
