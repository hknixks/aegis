import httpx
import pytest
import respx

from aegis.config import Settings
from aegis.keeperhub import KeeperHubClient
from aegis.keeperhub.exceptions import KeeperHubAuthError, KeeperHubConnectionError
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
