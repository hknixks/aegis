"""Tests for aegis.demo_orchestrator — the one authoritative entrypoint
every CLI command and API endpoint uses to start a demo/live run.

Covers the Phase 19A mode-isolation guarantees: FIXTURE can never reach a
real KeeperHub client, LIVE_DRY_RUN always forces dry_run=True, and
LIVE_EXECUTION refuses to start without every one of its gates satisfied.
"""

from __future__ import annotations

import pytest

from aegis.config import Settings
from aegis.demo_orchestrator import (
    DemoMode,
    LiveConfigMissingError,
    LiveExecutionNotAuthorizedError,
    build_fixture_components,
    fixture_settings,
    get_run,
    start_run,
)
from aegis.preflight import PreflightCheck, PreflightResult


def _live_settings(**overrides: object) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        keeperhub_api_key="kh_test123",
        aegis_expected_wallet_address="0xWallet",
        aegis_debt_asset="0xUSDC",
        aegis_collateral_asset="0xWETH",
        **overrides,  # type: ignore[arg-type]
    )


def _passing_preflight() -> PreflightResult:
    return PreflightResult(checks=[PreflightCheck("dummy check", True, "ok")])


def _failing_preflight() -> PreflightResult:
    return PreflightResult(checks=[PreflightCheck("dummy check", False, "not ready")])


# --- 1: FIXTURE never reaches a real KeeperHub client --------------------


def test_fixture_components_are_never_a_real_keeperhub_client() -> None:
    components = build_fixture_components(fixture_settings())
    assert type(components.keeperhub_client).__name__ == "MagicMock"


def test_fixture_run_never_calls_a_real_client_end_to_end() -> None:
    handle = start_run(DemoMode.FIXTURE)
    assert handle.result is not None
    assert handle.result.resolved is True
    # FIXTURE runs synchronously — no thread, no pending real I/O.
    assert handle.running is False


# --- 2: LIVE_DRY_RUN never broadcasts -------------------------------------


def test_live_dry_run_is_rejected_without_config() -> None:
    with pytest.raises(LiveConfigMissingError):
        start_run(DemoMode.LIVE_DRY_RUN, settings=Settings(_env_file=None, keeperhub_api_key="kh_test123"))  # type: ignore[call-arg]


def test_live_dry_run_forces_base_sepolia_regardless_of_chain_order() -> None:
    """Regression: LIVE_DRY_RUN must always target Base Sepolia (84532),
    never just "whatever chain ID happens to be first" in
    AEGIS_ALLOWED_CHAIN_IDS — this project's live demo is Base Sepolia
    only. Uses an unreachable KeeperHub base URL so this never makes a
    real network call; only the chain selection is under test."""
    settings = _live_settings(
        keeperhub_base_url="http://127.0.0.1:1",  # type: ignore[arg-type]
        keeperhub_timeout_seconds=1,
        aegis_allowed_chain_ids=(11155111, 84532),  # Ethereum Sepolia listed FIRST
    )
    handle = start_run(DemoMode.LIVE_DRY_RUN, settings=settings)
    assert handle.network == "84532"


def test_live_dry_run_rejects_a_chain_allowlist_without_base_sepolia() -> None:
    settings = _live_settings(aegis_allowed_chain_ids=(11155111,))
    with pytest.raises(LiveConfigMissingError):
        start_run(DemoMode.LIVE_DRY_RUN, settings=settings)


# --- 3/6: LIVE_EXECUTION requires confirm + autonomous flag + preflight --


def test_live_execution_requires_confirm() -> None:
    settings = _live_settings(aegis_autonomous_execution_enabled=True)
    with pytest.raises(LiveExecutionNotAuthorizedError) as exc_info:
        start_run(DemoMode.LIVE_EXECUTION, settings=settings, confirm=False, preflight_result=_passing_preflight())
    assert any("confirm" in reason for reason in exc_info.value.reasons)


def test_live_execution_requires_autonomous_execution_enabled() -> None:
    settings = _live_settings(aegis_autonomous_execution_enabled=False)
    with pytest.raises(LiveExecutionNotAuthorizedError) as exc_info:
        start_run(DemoMode.LIVE_EXECUTION, settings=settings, confirm=True, preflight_result=_passing_preflight())
    assert any("AEGIS_AUTONOMOUS_EXECUTION_ENABLED" in reason for reason in exc_info.value.reasons)


def test_live_execution_requires_passing_preflight() -> None:
    settings = _live_settings(aegis_autonomous_execution_enabled=True)
    with pytest.raises(LiveExecutionNotAuthorizedError) as exc_info:
        start_run(DemoMode.LIVE_EXECUTION, settings=settings, confirm=True, preflight_result=_failing_preflight())
    assert any("preflight failed" in reason for reason in exc_info.value.reasons)


def test_live_execution_reports_every_unmet_gate_at_once() -> None:
    settings = _live_settings(aegis_autonomous_execution_enabled=False)
    with pytest.raises(LiveExecutionNotAuthorizedError) as exc_info:
        start_run(DemoMode.LIVE_EXECUTION, settings=settings, confirm=False, preflight_result=_failing_preflight())
    assert len(exc_info.value.reasons) == 3


def test_live_execution_requires_live_config() -> None:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, keeperhub_api_key="kh_test123", aegis_autonomous_execution_enabled=True,
    )
    with pytest.raises(LiveConfigMissingError):
        start_run(DemoMode.LIVE_EXECUTION, settings=settings, confirm=True, preflight_result=_passing_preflight())


# --- 4: mainnet is impossible ---------------------------------------------


def test_mainnet_chain_id_cannot_even_be_configured() -> None:
    """Belt-and-suspenders, exercised here at the boundary this module
    depends on: Settings itself refuses a mainnet chain ID in
    AEGIS_ALLOWED_CHAIN_IDS, so no DemoMode could ever be started against
    one regardless of what this module does."""
    with pytest.raises(Exception, match="mainnet"):
        Settings(  # type: ignore[call-arg]
            _env_file=None, keeperhub_api_key="kh_test123", aegis_allowed_chain_ids=(1,),
        )


# --- 13/14: fixture data cannot reach the live execution layer -----------


def test_fixture_and_live_runs_never_share_components() -> None:
    """FIXTURE and LIVE_* runs are built from entirely separate component
    factories (build_fixture_components vs build_pipeline_components) —
    there is no shared mutable state a FIXTURE run could leak into a LIVE
    run's components, and no parameter that reuses one mode's components
    for another."""
    fixture_handle = start_run(DemoMode.FIXTURE)
    assert fixture_handle.wallet is not None and fixture_handle.wallet.lower().startswith("0xdem0")

    settings = _live_settings(
        keeperhub_base_url="http://127.0.0.1:1",  # type: ignore[arg-type]
        keeperhub_timeout_seconds=1,
    )
    live_handle = start_run(DemoMode.LIVE_DRY_RUN, settings=settings)
    assert live_handle.wallet == "0xWallet"
    assert live_handle.run_id != fixture_handle.run_id


def test_registry_lookup_returns_the_right_handle() -> None:
    handle = start_run(DemoMode.FIXTURE)
    assert get_run(handle.run_id) is handle
    assert get_run("not-a-real-run-id") is None
