import httpx
import pytest
import respx

from aegis.config import Settings
from aegis.keeperhub import KeeperHubClient
from aegis.keeperhub.exceptions import KeeperHubAuthError, KeeperHubConnectionError


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
