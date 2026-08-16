"""Live integration test: the same run_pipeline as tests/test_pipeline.py,
but against the REAL KeeperHub API instead of a respx-mocked HTTP layer.

Deselected by default. Requires:
  - AEGIS_LIVE_INTEGRATION_TESTS=1 (the explicit opt-in this file's
    skipif checks — nothing here runs on a normal `pytest` invocation)
  - a real KEEPERHUB_API_KEY in the environment/.env
  - AEGIS_EXPECTED_WALLET_ADDRESS, AEGIS_DEBT_ASSET, AEGIS_COLLATERAL_ASSET
    configured for a real Base Sepolia testnet wallet

Safety, independent of the opt-in gate above:
  - dry_run=True is hardcoded below and is never a parameter this test
    can override — this test can never broadcast a transaction, no matter
    how it's invoked.
  - Settings' own validators reject any mainnet chain ID
    (aegis.config.KNOWN_MAINNET_CHAIN_IDS) before this test body ever
    runs, and PolicyEngine rejects one a second time per candidate — so
    even a misconfigured AEGIS_ALLOWED_CHAIN_IDS can't put this on
    mainnet.
  - No real funds are ever at risk: dry-run stops before EXECUTE, and the
    only state-changing KeeperHub calls made here are `simulate=true`
    calls, which KeeperHub itself never broadcasts.
"""

from __future__ import annotations

import os

import pytest

from aegis.config import KNOWN_MAINNET_CHAIN_IDS, Settings, get_settings
from aegis.pipeline import run_pipeline
from aegis.recovery import RunState

_LIVE_TESTS_ENABLED = os.environ.get("AEGIS_LIVE_INTEGRATION_TESTS") == "1"


def _load_live_settings() -> Settings | None:
    try:
        settings = get_settings()
    except Exception:
        return None
    if not settings.keeperhub_api_key or settings.keeperhub_api_key.startswith("kh_your_"):
        return None
    if not (settings.aegis_expected_wallet_address and settings.aegis_debt_asset and settings.aegis_collateral_asset):
        return None
    return settings


pytestmark = pytest.mark.live_integration


@pytest.mark.skipif(
    not _LIVE_TESTS_ENABLED,
    reason="set AEGIS_LIVE_INTEGRATION_TESTS=1 to run against the real KeeperHub API",
)
def test_dry_run_pipeline_against_real_keeperhub_base_sepolia() -> None:
    settings = _load_live_settings()
    if settings is None:
        pytest.skip(
            "AEGIS_LIVE_INTEGRATION_TESTS=1 but KEEPERHUB_API_KEY / "
            "AEGIS_EXPECTED_WALLET_ADDRESS / AEGIS_DEBT_ASSET / AEGIS_COLLATERAL_ASSET "
            "are not fully configured — see this file's module docstring."
        )

    network = str(next(iter(settings.aegis_allowed_chain_ids)))
    assert int(network) not in KNOWN_MAINNET_CHAIN_IDS  # belt-and-suspenders, see module docstring

    result = run_pipeline(
        settings=settings,
        network=network,
        user=settings.aegis_expected_wallet_address,  # type: ignore[arg-type]
        debt_asset=settings.aegis_debt_asset,  # type: ignore[arg-type]
        collateral_asset=settings.aegis_collateral_asset,  # type: ignore[arg-type]
        available_balance=settings.aegis_available_balance,
        dry_run=True,  # hardcoded — see module docstring; never broadcasts
    )

    assert result.dry_run is True
    assert len(result.rounds) == 1
    # dry-run can only ever land on one of these two states — it is
    # structurally unable to reach EXECUTING.
    assert result.rounds[0].final_state in (RunState.READY_TO_EXECUTE, RunState.RESOLVED, RunState.NO_SAFE_ACTION)
    assert result.rounds[0].executed is False
