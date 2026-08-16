import httpx
import pytest
import respx

from aegis.config import Settings
from aegis.keeperhub import KeeperHubClient
from aegis.keeperhub.exceptions import KeeperHubAuthError, KeeperHubConnectionError, KeeperHubError
from tests.fixtures.keeperhub_payloads import (
    EXECUTE_TRANSFER_RESULT,
    EXECUTION_STATUS_RESULT,
    SIMULATE_TRANSFER_RESULT,
)


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, keeperhub_api_key="kh_test123")  # type: ignore[call-arg]


@respx.mock
def test_health_check_ok(settings: Settings) -> None:
    respx.get("https://app.keeperhub.com/api/chains").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": "y2kycikh0jhz1wjchzz12",
                    "chainId": 11155111,
                    "name": "Ethereum Sepolia",
                    "isTestnet": True,
                    "isEnabled": True,
                }
            ],
        )
    )
    respx.get("https://app.keeperhub.com/api/user").mock(
        return_value=httpx.Response(200, json={"id": "user_123", "email": "a@b.com"})
    )

    with KeeperHubClient(settings) as client:
        result = client.health_check()

    assert result.ok is True
    assert result.reachable is True
    assert result.authenticated is True
    assert result.chain_count == 1
    assert result.user_id == "user_123"


@respx.mock
def test_health_check_bad_api_key(settings: Settings) -> None:
    respx.get("https://app.keeperhub.com/api/chains").mock(
        return_value=httpx.Response(200, json=[])
    )
    respx.get("https://app.keeperhub.com/api/user").mock(return_value=httpx.Response(401))

    with KeeperHubClient(settings) as client:
        result = client.health_check()

    assert result.reachable is True
    assert result.authenticated is False
    assert result.ok is False


@respx.mock
def test_health_check_unreachable(settings: Settings) -> None:
    respx.get("https://app.keeperhub.com/api/chains").mock(
        side_effect=httpx.ConnectError("connection refused")
    )

    with KeeperHubClient(settings) as client:
        result = client.health_check()

    assert result.reachable is False
    assert result.authenticated is False
    assert result.ok is False


@respx.mock
def test_get_current_user_raises_auth_error(settings: Settings) -> None:
    respx.get("https://app.keeperhub.com/api/user").mock(return_value=httpx.Response(403))

    with KeeperHubClient(settings) as client:
        with pytest.raises(KeeperHubAuthError):
            client.get_current_user()


@respx.mock
def test_list_chains_raises_connection_error(settings: Settings) -> None:
    respx.get("https://app.keeperhub.com/api/chains").mock(
        side_effect=httpx.ConnectTimeout("timed out")
    )

    with KeeperHubClient(settings) as client:
        with pytest.raises(KeeperHubConnectionError):
            client.list_chains()


@respx.mock
def test_simulate_protocol_action(settings: Settings) -> None:
    route = respx.post("https://app.keeperhub.com/api/execute/contract-call").mock(
        return_value=httpx.Response(200, json=SIMULATE_TRANSFER_RESULT)
    )

    with KeeperHubClient(settings) as client:
        result = client.simulate_protocol_action(
            "aave-v3/repay",
            {"contractAddress": "0xPool", "chainId": "84532", "functionName": "repay", "functionArgs": "[]"},
        )

    assert result.success is True
    assert result.wouldRevert is False
    sent_body = route.calls.last.request.content
    assert b'"simulate":true' in sent_body


@respx.mock
def test_simulate_protocol_action_returns_a_failed_result_instead_of_raising_on_http_400(
    settings: Settings,
) -> None:
    """Confirmed directly against the real API: a simulate=true contract-
    call whose simulation would revert (or whose function name is
    ambiguous between overloads) comes back as HTTP 400 with a
    legitimate ProtocolActionSimulation-shaped body — this must reach
    SimulationService as a normal failed result, never an exception, or
    every real candidate whose simulation fails crashes run_with_recovery
    instead of being gracefully rejected."""
    respx.post("https://app.keeperhub.com/api/execute/contract-call").mock(
        return_value=httpx.Response(
            400,
            json={
                "success": False,
                "status": "simulated",
                "failureKind": "revert",
                "wouldRevert": True,
                "revertReason": "Simulation reverted: execution reverted (unknown custom error)",
                "error": "Simulation reverted: execution reverted (unknown custom error)",
            },
        )
    )

    with KeeperHubClient(settings) as client:
        result = client.simulate_protocol_action(
            "aave-v3/supply",
            {
                "contractAddress": "0xPool", "chainId": "84532",
                "functionName": "supply(address,uint256,address,uint16)", "functionArgs": "[]",
            },
        )

    assert result.success is False
    assert result.wouldRevert is True
    assert "reverted" in result.revertReason


@respx.mock
def test_a_genuine_400_without_a_simulation_body_still_raises(settings: Settings) -> None:
    """The lenient-400 handling is narrowly scoped to bodies that actually
    look like a ProtocolActionSimulation (have a wouldRevert key) — a
    plain malformed-request 400 must still raise, exactly as before."""
    respx.post("https://app.keeperhub.com/api/execute/contract-call").mock(
        return_value=httpx.Response(400, json={"error": "invalid request body"})
    )

    with KeeperHubClient(settings) as client:
        with pytest.raises(KeeperHubError):
            client.simulate_protocol_action(
                "aave-v3/supply",
                {"contractAddress": "0xPool", "chainId": "84532", "functionName": "supply", "functionArgs": "[]"},
            )


@respx.mock
def test_execute_protocol_action_still_raises_on_http_400(settings: Settings) -> None:
    """The lenient handling only ever applies to simulate=true calls — an
    unexpected 400 during a real (non-simulated) execute must still raise,
    which aegis.recovery treats as EXECUTION_UNCERTAIN (a hard stop), not
    a graceful failure."""
    respx.post("https://app.keeperhub.com/api/execute/contract-call").mock(
        return_value=httpx.Response(
            400, json={"success": False, "wouldRevert": True, "revertReason": "reverted"}
        )
    )

    with KeeperHubClient(settings) as client:
        with pytest.raises(KeeperHubError):
            client.execute_protocol_action(
                "aave-v3/supply",
                {"contractAddress": "0xPool", "chainId": "84532", "functionName": "supply", "functionArgs": "[]"},
                idempotency_key="fixed-key",
            )


@respx.mock
def test_execute_protocol_action_sends_idempotency_header(settings: Settings) -> None:
    route = respx.post("https://app.keeperhub.com/api/execute/contract-call").mock(
        return_value=httpx.Response(200, json=EXECUTE_TRANSFER_RESULT)
    )

    with KeeperHubClient(settings) as client:
        result = client.execute_protocol_action(
            "aave-v3/repay",
            {"contractAddress": "0xPool", "chainId": "84532", "functionName": "repay", "functionArgs": "[]"},
            idempotency_key="fixed-key",
        )

    assert result.executionId == EXECUTE_TRANSFER_RESULT["executionId"]
    assert result.transactionHash == EXECUTE_TRANSFER_RESULT["transactionHash"]
    assert route.calls.last.request.headers["idempotency-key"] == "fixed-key"


@respx.mock
def test_get_protocol_action_status(settings: Settings) -> None:
    execution_id = "zkn8vu62dmox0diyzulr0"
    respx.get(f"https://app.keeperhub.com/api/execute/{execution_id}/status").mock(
        return_value=httpx.Response(200, json=EXECUTION_STATUS_RESULT)
    )

    with KeeperHubClient(settings) as client:
        status = client.get_protocol_action_status(execution_id)

    assert status.status == "completed"
    assert status.terminal is True
    assert status.succeeded is True


@respx.mock
def test_call_protocol_action_raises_connection_error(settings: Settings) -> None:
    respx.post("https://app.keeperhub.com/api/execute/contract-call").mock(
        side_effect=httpx.ConnectError("connection refused")
    )

    with KeeperHubClient(settings) as client:
        with pytest.raises(KeeperHubConnectionError):
            client.simulate_protocol_action(
                "aave-v3/repay",
                {"contractAddress": "0xPool", "chainId": "84532", "functionName": "repay", "functionArgs": "[]"},
            )
