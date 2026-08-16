"""Tests for _LiveMcpSession against a REAL local MCP server.

Unlike the rest of tests/hermes/ (which use a plain fake implementing the
McpSession Protocol directly, no actual MCP wire protocol involved), this
file runs a real mcp.server.mcpserver.MCPServer over real Streamable HTTP
(via uvicorn on a loopback port) and drives it with the real
_LiveMcpSession/mcp.ClientSession client stack. This is what actually
proves the transport implementation is correct — that it authenticates,
lists real tools, calls a real tool, and gets a real (not fabricated)
response back over the wire — without depending on network access to the
real KeeperHub endpoint (that's tests/test_live_keeperhub_mcp.py, gated
behind an explicit opt-in env var).

Requires the `hermes` extra (`pip install -e ".[hermes]"`) for the `mcp`
and `uvicorn` packages; skipped entirely if they aren't installed.
"""

from __future__ import annotations

import socket
import threading
import time

import pytest

from aegis.config import Settings
from aegis.keeperhub.exceptions import KeeperHubMcpConnectionError, KeeperHubMcpTimeoutError

mcp_server = pytest.importorskip("mcp.server.mcpserver.server")
uvicorn = pytest.importorskip("uvicorn")

from aegis.hermes.mcp_gateway import (  # noqa: E402
    ALLOWED_TOOL_NAMES,
    HermesMcpGateway,
    ToolNotAllowedError,
    _LiveMcpSession,
)

FAKE_ACCOUNT_DATA = {
    "totalCollateralBase": "1000000000",
    "totalDebtBase": "800000000",
    "availableBorrowsBase": "50000000",
    "currentLiquidationThreshold": "8000",
    "ltv": "7500",
    "healthFactor": "1200000000000000000",
}
FAKE_SERVER_NAME = "fake-keeperhub"
FAKE_SERVER_VERSION = "0.0.1-test"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _build_fake_server() -> "mcp_server.MCPServer":
    server = mcp_server.MCPServer(name=FAKE_SERVER_NAME, version=FAKE_SERVER_VERSION)

    @server.tool(name="execute_protocol_action")
    def execute_protocol_action(actionType: str, params: dict) -> dict:
        if actionType == "aave-v3/get-user-account-data":
            return FAKE_ACCOUNT_DATA
        return {"error": f"fake server has no fixture for actionType {actionType!r}"}

    @server.tool(name="search_protocol_actions")
    def search_protocol_actions(protocol: str = "", query: str = "") -> dict:
        return {"actions": ["aave-v3/repay", "aave-v3/supply"]}

    # Deliberately NOT in ALLOWED_TOOL_NAMES — proves the gateway rejects
    # a real write-shaped tool before ever dispatching it, even against a
    # real server that would happily answer.
    @server.tool(name="transfer_funds")
    def transfer_funds(to: str, amount: str) -> dict:
        return {"status": "SHOULD NEVER BE CALLED THROUGH THE GATEWAY"}

    @server.tool(name="slow_tool")
    def slow_tool() -> dict:
        time.sleep(3)
        return {"ok": True}

    return server


@pytest.fixture(scope="module")
def fake_mcp_server_url():
    server = _build_fake_server()
    app = server.streamable_http_app(streamable_http_path="/mcp")
    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    uv_server = uvicorn.Server(config)
    thread = threading.Thread(target=uv_server.run, daemon=True, name="fake-keeperhub-mcp-server")
    thread.start()

    deadline = time.time() + 5
    while not uv_server.started and time.time() < deadline:
        time.sleep(0.05)
    if not uv_server.started:
        raise RuntimeError("fake MCP server did not start within 5s")

    yield f"http://127.0.0.1:{port}/mcp"

    uv_server.should_exit = True
    thread.join(timeout=5)


def _settings(mcp_url: str, **overrides: object) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        keeperhub_api_key="kh_test123",
        keeperhub_mcp_url=mcp_url,
        **overrides,  # type: ignore[arg-type]
    )


# --- direct _LiveMcpSession tests: real transport, real wire protocol -----


def test_live_mcp_session_lists_real_tools_from_local_server(fake_mcp_server_url: str) -> None:
    session = _LiveMcpSession(_settings(fake_mcp_server_url))

    tools = session.list_tools()

    assert set(tools) == {"execute_protocol_action", "search_protocol_actions", "transfer_funds", "slow_tool"}


def test_live_mcp_session_calls_a_real_tool_and_returns_the_real_response(fake_mcp_server_url: str) -> None:
    session = _LiveMcpSession(_settings(fake_mcp_server_url))

    result = session.call_tool(
        "execute_protocol_action",
        {"actionType": "aave-v3/get-user-account-data", "params": {"network": "84532", "user": "0xWallet"}},
    )

    assert result["isError"] is False
    assert result["content"] == FAKE_ACCOUNT_DATA


def test_live_mcp_session_diagnostics_reports_the_real_server_identity(fake_mcp_server_url: str) -> None:
    session = _LiveMcpSession(_settings(fake_mcp_server_url))

    diagnostics = session.diagnostics()

    assert diagnostics.ok is True
    assert diagnostics.reachable is True
    assert diagnostics.authenticated is True
    assert diagnostics.server_name == FAKE_SERVER_NAME
    assert diagnostics.server_version == FAKE_SERVER_VERSION
    assert diagnostics.protocol_version is not None
    assert diagnostics.tool_count == 4


def test_live_mcp_session_calls_are_sequential_and_repeatable(fake_mcp_server_url: str) -> None:
    session = _LiveMcpSession(_settings(fake_mcp_server_url))

    first = session.list_tools()
    second = session.list_tools()

    assert first == second


# --- error handling against real network failure modes --------------------


def test_live_mcp_session_raises_connection_error_for_unreachable_endpoint() -> None:
    unreachable_port = _free_port()  # nothing is listening here
    session = _LiveMcpSession(
        _settings(f"http://127.0.0.1:{unreachable_port}/mcp", keeperhub_timeout_seconds=2)
    )

    with pytest.raises(KeeperHubMcpConnectionError):
        session.list_tools()


def test_live_mcp_session_raises_timeout_error_for_a_slow_tool(fake_mcp_server_url: str) -> None:
    session = _LiveMcpSession(_settings(fake_mcp_server_url, keeperhub_timeout_seconds=0.5))

    start = time.monotonic()
    with pytest.raises(KeeperHubMcpTimeoutError):
        session.call_tool("slow_tool", {})
    # proves it actually timed out rather than waiting for the 3s sleep
    assert time.monotonic() - start < 2.5


# --- HermesMcpGateway restrictions still hold over the real transport -----


def test_gateway_over_live_session_still_enforces_tool_allowlist(fake_mcp_server_url: str) -> None:
    settings = _settings(fake_mcp_server_url)
    gateway = HermesMcpGateway(_LiveMcpSession(settings), settings)

    visible = gateway.list_tools()
    assert set(visible) <= ALLOWED_TOOL_NAMES
    assert "transfer_funds" not in visible  # real tool on the real server, still hidden

    with pytest.raises(ToolNotAllowedError):
        gateway.call_tool("transfer_funds", {"to": "0x1", "amount": "1"})


def test_gateway_over_live_session_still_enforces_action_type_allowlist(fake_mcp_server_url: str) -> None:
    settings = _settings(fake_mcp_server_url)
    gateway = HermesMcpGateway(_LiveMcpSession(settings), settings)

    with pytest.raises(ToolNotAllowedError):
        gateway.call_tool("execute_protocol_action", {"actionType": "aave-v3/repay", "params": {}})


def test_gateway_over_live_session_still_enforces_mainnet_block(fake_mcp_server_url: str) -> None:
    settings = _settings(fake_mcp_server_url)
    gateway = HermesMcpGateway(_LiveMcpSession(settings), settings)

    with pytest.raises(ToolNotAllowedError):
        gateway.call_tool(
            "execute_protocol_action",
            {"actionType": "aave-v3/get-user-account-data", "params": {"network": "8453", "user": "0xWallet"}},
        )


def test_gateway_over_live_session_allows_a_real_read_action_end_to_end(fake_mcp_server_url: str) -> None:
    settings = _settings(fake_mcp_server_url)
    gateway = HermesMcpGateway(_LiveMcpSession(settings), settings)

    result = gateway.call_tool(
        "execute_protocol_action",
        {"actionType": "aave-v3/get-user-account-data", "params": {"network": "84532", "user": "0xWallet"}},
    )

    assert result["content"] == FAKE_ACCOUNT_DATA
