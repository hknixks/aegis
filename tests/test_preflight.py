"""Tests for aegis.preflight — the go/no-go gate for `aegis live-demo`.

Every check is exercised individually so a failure is provably
diagnosable, not just "ok is False". None of these tests call simulate,
execute, or any mutating KeeperHub endpoint — preflight itself never does,
so there is nothing mutating to mock here.
"""

from unittest.mock import MagicMock

from aegis.config import Settings
from aegis.keeperhub.models import HealthCheckResult, KeeperHubChain
from aegis.preflight import BASE_SEPOLIA_CHAIN_ID, run_preflight

WALLET = "0xWallet"


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "_env_file": None,
        "keeperhub_api_key": "kh_test123",
        "aegis_expected_wallet_address": WALLET,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _healthy_client(chain_ids: list[int] | None = None) -> MagicMock:
    client = MagicMock()
    client.health_check.return_value = HealthCheckResult(
        reachable=True, authenticated=True, base_url="https://app.keeperhub.com",
        chain_count=3, user_id="user-1",
    )
    client.list_chains.return_value = [
        KeeperHubChain(id=str(i), chainId=cid) for i, cid in enumerate(chain_ids or [84532, 11155111])
    ]
    return client


def _healthy_mcp_session() -> MagicMock:
    session = MagicMock()
    session.diagnostics.return_value = MagicMock(
        reachable=True, authenticated=True, endpoint="https://app.keeperhub.com/mcp",
        tool_count=4, detail=None,
    )
    return session


def test_all_checks_pass_when_everything_is_healthy() -> None:
    result = run_preflight(_settings(), keeperhub_client=_healthy_client(), mcp_session=_healthy_mcp_session())
    assert result.ok is True
    assert result.failures == []
    # every advertised check actually ran and is individually visible
    names = {c.name for c in result.checks}
    assert "wallet address configured" in names
    assert "chain ID 84532 (Base Sepolia) allowed, no mainnet in allowlist" in names
    assert "KeeperHub API authentication active" in names
    assert "Base Sepolia visible on KeeperHub account" in names
    assert "KeeperHub simulation tool available" in names
    assert "KeeperHub execution tool available" in names
    assert "KeeperHub transaction status tool available" in names
    assert "KeeperHub MCP connected" in names
    assert "KeeperHub MCP authenticated" in names


def test_fails_when_wallet_address_not_configured() -> None:
    settings = _settings(aegis_expected_wallet_address=None)
    result = run_preflight(settings, keeperhub_client=_healthy_client(), mcp_session=_healthy_mcp_session())
    assert result.ok is False
    failed_names = {c.name for c in result.failures}
    assert "wallet address configured" in failed_names


def test_fails_when_base_sepolia_not_in_allowed_chain_ids() -> None:
    settings = _settings(aegis_allowed_chain_ids=(11155111,))
    result = run_preflight(settings, keeperhub_client=_healthy_client(), mcp_session=_healthy_mcp_session())
    assert result.ok is False
    failed_names = {c.name for c in result.failures}
    assert "chain ID 84532 (Base Sepolia) allowed, no mainnet in allowlist" in failed_names


def test_stops_on_keeperhub_rest_authentication_failure() -> None:
    client = MagicMock()
    client.health_check.return_value = HealthCheckResult(
        reachable=True, authenticated=False, base_url="https://app.keeperhub.com",
        detail="invalid API key",
    )
    result = run_preflight(_settings(), keeperhub_client=client, mcp_session=_healthy_mcp_session())
    assert result.ok is False
    failed_names = {c.name for c in result.failures}
    assert "KeeperHub API authentication active" in failed_names
    # dependent checks correctly report unavailable rather than silently passing
    assert "KeeperHub execution tool available" in failed_names
    assert "KeeperHub simulation tool available" in failed_names
    assert "KeeperHub transaction status tool available" in failed_names


def test_stops_when_keeperhub_health_check_raises() -> None:
    client = MagicMock()
    client.health_check.side_effect = ConnectionError("network unreachable")
    result = run_preflight(_settings(), keeperhub_client=client, mcp_session=_healthy_mcp_session())
    assert result.ok is False
    detail = next(c.detail for c in result.checks if c.name == "KeeperHub API authentication active")
    assert "ConnectionError" in detail
    # never leaks the raw exception message beyond its type name
    assert "network unreachable" not in detail


def test_fails_when_base_sepolia_not_visible_on_keeperhub_account() -> None:
    client = _healthy_client(chain_ids=[11155111, 421614])  # no 84532
    result = run_preflight(_settings(), keeperhub_client=client, mcp_session=_healthy_mcp_session())
    assert result.ok is False
    failed_names = {c.name for c in result.failures}
    assert "Base Sepolia visible on KeeperHub account" in failed_names


def test_fails_when_mcp_is_unreachable() -> None:
    session = MagicMock()
    session.diagnostics.side_effect = ConnectionError("connection refused")
    result = run_preflight(_settings(), keeperhub_client=_healthy_client(), mcp_session=session)
    assert result.ok is False
    failed_names = {c.name for c in result.failures}
    assert "KeeperHub MCP connected" in failed_names
    assert "KeeperHub MCP authenticated" in failed_names


def test_fails_when_mcp_reachable_but_not_authenticated() -> None:
    session = MagicMock()
    session.diagnostics.return_value = MagicMock(
        reachable=True, authenticated=False, endpoint="https://app.keeperhub.com/mcp",
        tool_count=None, detail="401 Unauthorized",
    )
    result = run_preflight(_settings(), keeperhub_client=_healthy_client(), mcp_session=session)
    assert result.ok is False
    failed_names = {c.name for c in result.failures}
    assert "KeeperHub MCP authenticated" in failed_names
    # reachability itself is a distinct, still-passing check
    passed_names = {c.name for c in result.checks if c.passed}
    assert "KeeperHub MCP connected" in passed_names
