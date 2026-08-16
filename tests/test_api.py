"""Tests for the read-only dashboard API (aegis.api).

No real network access — every run started here is FIXTURE mode (in-process
fakes, like this project's other tests) or LIVE_DRY_RUN with KeeperHub
settings deliberately unreachable/unconfigured, so it returns a structured
error rather than attempting a real connection.
"""

import time

from fastapi.testclient import TestClient

from aegis.api import app
from aegis.demo_orchestrator import build_fixture_components, fixture_settings

client = TestClient(app)


def _start(mode: str) -> str:
    response = client.post("/api/runs", json={"mode": mode})
    assert response.status_code == 200
    return response.json()["run_id"]


def _poll_until_done(run_id: str, *, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        data = client.get(f"/api/runs/{run_id}").json()
        if not data["running"]:
            return data
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not finish within {timeout}s")


def test_health() -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_fixture_run_resolves_with_the_full_narrative() -> None:
    run_id = _start("fixture")
    data = _poll_until_done(run_id)

    assert data["mode"] == "fixture"
    assert data["run_id"] == run_id
    assert data["running"] is False
    assert data["status"] == "Resolved"
    assert data["incident_state"] == "Resolved"
    assert data["network"] == "84532"
    assert data["chain_id"] == 84532
    assert data["protocol"] == "Aave V3"

    decisions = {c["action"] for c in data["candidates"]}
    assert decisions == {"DO_NOTHING", "REPAY_DEBT", "ADD_COLLATERAL"}

    repay = next(c for c in data["candidates"] if c["action"] == "REPAY_DEBT")
    assert repay["eligible"] is False
    assert repay["simulation_status"] == "FAILED"
    assert repay["rejection_reason"] == "simulation failed or would revert"

    add_collateral = next(c for c in data["candidates"] if c["action"] == "ADD_COLLATERAL")
    assert add_collateral["eligible"] is True
    assert add_collateral["final_status"] == "SELECTED"

    assert data["explanation"]["selected_action"] == "ADD_COLLATERAL"
    assert "REPAY_DEBT" in data["explanation"]["rejected_candidates"]

    assert data["execution"]["execution_status"] == "completed"
    assert data["execution"]["executed_through"] == "KeeperHub"
    assert data["execution"]["transaction_hash"].startswith("0xdemo")
    assert data["execution"]["explorer_url"] == (
        f"https://sepolia.basescan.org/tx/{data['execution']['transaction_hash']}"
    )

    assert data["verification"]["before_risk"] == "AT_RISK"
    assert data["verification"]["after_risk"] == "SAFE"
    assert data["verification"]["risk_reduced"] is True
    assert data["verification"]["incident_resolved"] is True

    assert len(data["recovery_steps"]) == 2
    assert data["recovery_steps"][0]["action"] == "REPAY_DEBT"
    assert data["recovery_steps"][0]["outcome"] == "rejected"
    assert data["recovery_steps"][1]["action"] == "ADD_COLLATERAL"
    assert data["recovery_steps"][1]["outcome"] == "selected"

    assert len(data["audit_timeline"]) > 0
    stages = [e["stage"] for e in data["audit_timeline"]]
    assert "DETECTED" in stages
    assert "SIMULATION_FAILED" in stages
    assert "RESOLVED" in stages
    # every audit event in a single-round run shares the run's own run_id
    assert {e["run_id"] for e in data["audit_timeline"]} == {run_id}


def test_fixture_components_never_touch_a_real_client() -> None:
    """The FIXTURE story's KeeperHub client is a bare MagicMock — asserting
    this is what actually makes it structurally incapable of a real write,
    not merely configured not to attempt one."""
    components = build_fixture_components(fixture_settings())
    assert type(components.keeperhub_client).__name__ == "MagicMock"


def test_fixture_audit_events_carry_no_secret() -> None:
    run_id = _start("fixture")
    data = _poll_until_done(run_id)
    serialized = str(data)
    assert "kh_demo" not in serialized
    assert "Authorization" not in serialized
    assert "Bearer" not in serialized


def test_start_run_rejects_live_execution_mode() -> None:
    """LIVE_EXECUTION is structurally excluded from the HTTP surface —
    StartRunRequest.mode is a Literal that doesn't include it, so this
    fails schema validation before aegis.demo_orchestrator is ever
    reached."""
    response = client.post("/api/runs", json={"mode": "live_execution"})
    assert response.status_code == 422


def test_live_dry_run_reports_structured_error_when_not_configured(monkeypatch) -> None:
    from aegis.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("KEEPERHUB_API_KEY", "kh_test123")
    # Explicit empty overrides, not delenv: a real local .env file (which
    # Settings loads via env_file=".env") can otherwise leak real values
    # back in once these are merely deleted from os.environ, since
    # pydantic-settings only prefers os.environ over the dotenv file when
    # the variable is actually *present* in os.environ.
    monkeypatch.setenv("AEGIS_EXPECTED_WALLET_ADDRESS", "")
    monkeypatch.setenv("AEGIS_DEBT_ASSET", "")
    monkeypatch.setenv("AEGIS_COLLATERAL_ASSET", "")

    response = client.post("/api/runs", json={"mode": "live_dry_run"})
    get_settings.cache_clear()

    assert response.status_code == 400
    assert "not configured" in response.json()["detail"]


def test_live_dry_run_never_exposes_the_api_key_even_on_error(monkeypatch) -> None:
    from aegis.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("KEEPERHUB_API_KEY", "kh_supersecretvalue123")
    monkeypatch.setenv("AEGIS_EXPECTED_WALLET_ADDRESS", "0xWallet")
    monkeypatch.setenv("AEGIS_DEBT_ASSET", "0xUSDC")
    monkeypatch.setenv("AEGIS_COLLATERAL_ASSET", "0xWETH")
    # points KeeperHub at an address nothing is listening on, forcing a
    # connection error rather than a real network call
    monkeypatch.setenv("KEEPERHUB_BASE_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("KEEPERHUB_TIMEOUT_SECONDS", "1")

    run_id = _start("live_dry_run")
    data = _poll_until_done(run_id, timeout=15.0)
    get_settings.cache_clear()

    assert data["error"] is not None
    assert "kh_supersecretvalue123" not in str(data)
    assert "Authorization" not in str(data)
    # never a real broadcast attempt, never an execution result fabricated
    assert data["execution"]["execution_status"] == "not_attempted"
    assert data["execution"]["transaction_hash"] is None


def test_get_run_unknown_id_is_404() -> None:
    response = client.get("/api/runs/does-not-exist")
    assert response.status_code == 404
