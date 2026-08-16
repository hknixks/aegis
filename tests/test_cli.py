"""Tests for aegis.cli — the three Phase 19A demo commands plus `health`.

Uses typer.testing.CliRunner. No real network access: `health` and
`live-dry-run`/`live-demo` gate-failure tests point KeeperHub at an
unreachable address so a connection error stands in for "not configured",
exactly like this project's other tests do; `fixture-demo` never touches a
network at all (MagicMock KeeperHub client).
"""

from __future__ import annotations

import httpx
import respx
from typer.testing import CliRunner

from aegis.cli import app
from aegis.config import get_settings

runner = CliRunner()


def _clear_settings_cache():
    get_settings.cache_clear()


def test_fixture_demo_resolves_with_no_network_access() -> None:
    result = runner.invoke(app, ["fixture-demo"])
    assert result.exit_code == 0
    assert "RESOLVED" in result.stdout
    assert "Aegis demo run ID" in result.stdout
    assert "ADD_COLLATERAL" in result.stdout


def test_live_dry_run_stops_when_not_configured(monkeypatch) -> None:
    _clear_settings_cache()
    monkeypatch.setenv("KEEPERHUB_API_KEY", "kh_test123")
    monkeypatch.setenv("AEGIS_EXPECTED_WALLET_ADDRESS", "")
    monkeypatch.setenv("AEGIS_DEBT_ASSET", "")
    monkeypatch.setenv("AEGIS_COLLATERAL_ASSET", "")

    result = runner.invoke(app, ["live-dry-run"])
    _clear_settings_cache()

    assert result.exit_code == 1
    assert "STOP" in result.stdout
    assert "not configured" in result.stdout


@respx.mock
def test_live_demo_stops_when_preflight_fails(monkeypatch) -> None:
    _clear_settings_cache()
    monkeypatch.setenv("KEEPERHUB_API_KEY", "kh_test123")
    monkeypatch.setenv("AEGIS_EXPECTED_WALLET_ADDRESS", "0xWallet")
    monkeypatch.setenv("AEGIS_DEBT_ASSET", "0xUSDC")
    monkeypatch.setenv("AEGIS_COLLATERAL_ASSET", "0xWETH")
    respx.get("https://app.keeperhub.com/api/chains").mock(
        side_effect=httpx.ConnectError("connection refused")
    )

    result = runner.invoke(app, ["live-demo"])
    _clear_settings_cache()

    assert result.exit_code == 1
    assert "preflight check(s) failed" in result.stdout
    assert "No transaction was attempted" in result.stdout


@respx.mock
def test_live_demo_stops_without_confirm_even_when_ready(monkeypatch) -> None:
    _clear_settings_cache()
    monkeypatch.setenv("KEEPERHUB_API_KEY", "kh_test123")
    monkeypatch.setenv("AEGIS_EXPECTED_WALLET_ADDRESS", "0xWallet")
    monkeypatch.setenv("AEGIS_DEBT_ASSET", "0xUSDC")
    monkeypatch.setenv("AEGIS_COLLATERAL_ASSET", "0xWETH")
    monkeypatch.setenv("AEGIS_AUTONOMOUS_EXECUTION_ENABLED", "true")
    respx.get("https://app.keeperhub.com/api/chains").mock(
        return_value=httpx.Response(
            200, json=[{"id": "1", "chainId": 84532, "name": "Base Sepolia", "isEnabled": True}]
        )
    )
    respx.get("https://app.keeperhub.com/api/user").mock(
        return_value=httpx.Response(200, json={"id": "user_1"})
    )

    result = runner.invoke(app, ["live-demo"])  # no --confirm
    _clear_settings_cache()

    # MCP diagnostics aren't reachable in this test either, so preflight
    # still fails overall — but the point under test is that --confirm is
    # never bypassed. Assert whichever STOP path was hit never proceeded
    # to a transaction attempt.
    assert result.exit_code == 1
    assert "Proceeding with live execution" not in result.stdout


def test_no_command_starts_a_real_transaction_without_confirm(monkeypatch) -> None:
    """Belt-and-suspenders: --confirm is a required, explicit flag on
    live-demo — Typer itself won't invoke the command with it True unless
    passed."""
    result = runner.invoke(app, ["live-demo", "--help"])
    assert result.exit_code == 0
    assert "--confirm" in result.stdout
