"""End-to-end integration tests for aegis.pipeline.run_pipeline.

Unlike the rest of this project's tests (which mock at the service layer
— SimulationService, ExecutionService, etc.), these mock only at the
network boundary: a real KeeperHubClient, real AavePositionReader/
SimulationService/ExecutionService/VerificationService/PolicyEngine, all
wired together by aegis.pipeline.build_pipeline_components, talking to a
respx-mocked HTTP layer. This is the strongest test this project can run
of "the whole thing is actually wired together correctly" without hitting
a real network.
"""

import json

import httpx
import respx

from aegis.config import Settings
from aegis.keeperhub.client import KeeperHubClient
from aegis.pipeline import build_pipeline_components, run_pipeline
from aegis.recovery import RecoveryFailureCategory, RunState
from tests.fixtures.keeperhub_payloads import (
    EXECUTE_TRANSFER_RESULT,
    EXECUTION_STATUS_RESULT,
    MOCK_AAVE_ACCOUNT_DATA_AT_RISK,
    MOCK_AAVE_ACCOUNT_DATA_SAFE,
    SIMULATE_TRANSFER_RESULT,
)

# A second, distinct AT_RISK position — used to prove a re-plan round uses
# a freshly re-read position, not a cached/stale value from the round
# that just confirmed-failed.
MOCK_AAVE_ACCOUNT_DATA_AT_RISK_ROUND_2 = {
    "totalCollateralBase": "1000000000",
    "totalDebtBase": "820000000",
    "availableBorrowsBase": "30000000",
    "currentLiquidationThreshold": "8000",
    "ltv": "7500",
    "healthFactor": "1170000000000000000",  # ~1.17 — still AT_RISK, but a different value
}

BASE_URL = "https://app.keeperhub.com"
NETWORK = "84532"
USER = "0xWallet"
DEBT_ASSET = "0xUSDC"
COLLATERAL_ASSET = "0xWETH"


def _settings(**overrides: object) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        keeperhub_api_key="kh_test123",
        aegis_expected_wallet_address=USER,
        **overrides,  # type: ignore[arg-type]
    )


def _mock_protocol_action_route(read_sequence: list[dict]):
    """Registers one respx route standing in for KeeperHub's single
    /api/execute/contract-call endpoint, which this project's real
    client uses for reads, simulations, AND real executions — routed here
    by inspecting the request body exactly like the real endpoint would
    dispatch internally."""
    read_calls = {"count": 0}

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["functionName"] == "getUserAccountData":
            index = read_calls["count"]
            read_calls["count"] += 1
            return httpx.Response(200, json={"result": read_sequence[index]})
        if body.get("simulate") is True:
            return httpx.Response(200, json=SIMULATE_TRANSFER_RESULT)
        return httpx.Response(200, json=EXECUTE_TRANSFER_RESULT)

    return respx.post(f"{BASE_URL}/api/execute/contract-call").mock(side_effect=handle)


def _mock_status_route():
    return respx.get(f"{BASE_URL}/api/execute/{EXECUTE_TRANSFER_RESULT['executionId']}/status").mock(
        return_value=httpx.Response(200, json=EXECUTION_STATUS_RESULT)
    )


@respx.mock
def test_pipeline_resolves_in_one_round_end_to_end() -> None:
    settings = _settings(aegis_autonomous_execution_enabled=True)
    _mock_protocol_action_route([MOCK_AAVE_ACCOUNT_DATA_AT_RISK, MOCK_AAVE_ACCOUNT_DATA_SAFE])
    _mock_status_route()

    result = run_pipeline(
        settings=settings, network=NETWORK, user=USER,
        debt_asset=DEBT_ASSET, collateral_asset=COLLATERAL_ASSET, dry_run=False,
    )

    assert result.resolved is True
    assert result.dry_run is False
    assert len(result.rounds) == 1
    round_result = result.rounds[0]
    assert round_result.final_state is RunState.RESOLVED
    assert round_result.executed is True
    assert round_result.verification is not None
    # step 12: obtain transaction hash
    assert round_result.verification.transactionHash == EXECUTE_TRANSFER_RESULT["transactionHash"]
    # step 13/14: verify the resulting position and recalculate risk
    assert round_result.risk_after is not None
    assert round_result.risk_after.at_risk is False


@respx.mock
def test_dry_run_never_executes_even_when_autonomous_execution_is_enabled() -> None:
    settings = _settings(aegis_autonomous_execution_enabled=True)
    _mock_protocol_action_route([MOCK_AAVE_ACCOUNT_DATA_AT_RISK])
    _mock_status_route()

    result = run_pipeline(
        settings=settings, network=NETWORK, user=USER,
        debt_asset=DEBT_ASSET, collateral_asset=COLLATERAL_ASSET, dry_run=True,
    )

    assert result.dry_run is True
    assert result.resolved is False
    assert result.final_state is RunState.READY_TO_EXECUTE
    assert len(result.rounds) == 1
    assert result.rounds[0].executed is False

    protocol_action_calls = respx.routes[0].calls
    for call in protocol_action_calls:
        assert "idempotency-key" not in {k.lower() for k in call.request.headers.keys()}


@respx.mock
def test_pipeline_replans_when_first_round_resolves_but_still_at_risk() -> None:
    settings = _settings(aegis_autonomous_execution_enabled=True)
    _mock_protocol_action_route(
        [
            MOCK_AAVE_ACCOUNT_DATA_AT_RISK,  # round 1 READ
            MOCK_AAVE_ACCOUNT_DATA_AT_RISK,  # round 1 REASSESS_RISK — still at risk, not resolved
            MOCK_AAVE_ACCOUNT_DATA_AT_RISK,  # round 2 READ (re-planning)
            MOCK_AAVE_ACCOUNT_DATA_SAFE,  # round 2 REASSESS_RISK — resolved
        ]
    )
    _mock_status_route()

    result = run_pipeline(
        settings=settings, network=NETWORK, user=USER,
        debt_asset=DEBT_ASSET, collateral_asset=COLLATERAL_ASSET, dry_run=False,
    )

    assert result.resolved is True
    assert len(result.rounds) == 2
    assert result.rounds[0].final_state is RunState.RESOLVED
    assert result.rounds[0].risk_after is not None
    assert result.rounds[0].risk_after.at_risk is True  # round 1 wasn't enough
    assert result.rounds[1].final_state is RunState.RESOLVED
    assert result.rounds[1].risk_after is not None
    assert result.rounds[1].risk_after.at_risk is False  # round 2 finished the job
    # each round is an independent decision, not a resend of the same tx
    assert result.rounds[0].run_id != result.rounds[1].run_id


@respx.mock
def test_pipeline_replans_after_confirmed_execution_failure_using_fresh_position() -> None:
    """A round that ends CONFIRMED-failed (KeeperHub's own status endpoint
    says status="failed") is safe to re-plan from — unlike UNCERTAIN. The
    next round must re-read the position (never reuse stale data) before
    generating new candidates."""
    settings = _settings(aegis_autonomous_execution_enabled=True)
    _mock_protocol_action_route(
        [
            MOCK_AAVE_ACCOUNT_DATA_AT_RISK,  # round 1 READ
            MOCK_AAVE_ACCOUNT_DATA_AT_RISK_ROUND_2,  # round 2 READ — freshly re-read, different data
            MOCK_AAVE_ACCOUNT_DATA_SAFE,  # round 2 REASSESS_RISK — resolved
        ]
    )
    status_route = respx.get(f"{BASE_URL}/api/execute/{EXECUTE_TRANSFER_RESULT['executionId']}/status")
    status_route.mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "executionId": EXECUTE_TRANSFER_RESULT["executionId"],
                    "status": "failed",
                    "error": "reverted on-chain",
                },
            ),
            httpx.Response(200, json=EXECUTION_STATUS_RESULT),  # round 2: "completed"
        ]
    )

    result = run_pipeline(
        settings=settings, network=NETWORK, user=USER,
        debt_asset=DEBT_ASSET, collateral_asset=COLLATERAL_ASSET, dry_run=False,
    )

    assert result.resolved is True
    assert len(result.rounds) == 2

    round1 = result.rounds[0]
    assert round1.final_state is RunState.FAILED
    assert round1.failure_category is RecoveryFailureCategory.EXECUTION_FAILURE
    assert round1.verification is not None
    assert round1.verification.succeeded is False

    round2 = result.rounds[1]
    assert round2.final_state is RunState.RESOLVED
    assert round2.risk_after is not None
    assert round2.risk_after.at_risk is False
    # round 2's decision was built from the ROUND_2 fixture's health
    # factor, not round 1's — proof the re-plan re-read rather than
    # reusing stale data.
    assert round2.risk_before is not None
    assert str(round2.risk_before.health_factor) == "1.17"

    assert round1.run_id != round2.run_id


@respx.mock
def test_pipeline_stops_on_uncertain_execution_without_replanning() -> None:
    """An UNCERTAIN outcome (execute() failed at the HTTP layer, no
    execution id ever obtained) must never trigger another round — Aegis
    does not know what happened on-chain, so it stops rather than
    guessing by trying again."""
    settings = _settings(aegis_autonomous_execution_enabled=True)
    read_calls = {"count": 0}
    reads = [MOCK_AAVE_ACCOUNT_DATA_AT_RISK]

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["functionName"] == "getUserAccountData":
            index = read_calls["count"]
            read_calls["count"] += 1
            return httpx.Response(200, json={"result": reads[index]})
        if body.get("simulate") is True:
            return httpx.Response(200, json=SIMULATE_TRANSFER_RESULT)
        # the real execute attempt fails at the HTTP layer — no
        # execution id is ever obtained.
        return httpx.Response(500, text="internal server error")

    respx.post(f"{BASE_URL}/api/execute/contract-call").mock(side_effect=handle)

    result = run_pipeline(
        settings=settings, network=NETWORK, user=USER,
        debt_asset=DEBT_ASSET, collateral_asset=COLLATERAL_ASSET, dry_run=False,
    )

    assert result.resolved is False
    assert result.final_state is RunState.UNCERTAIN
    assert len(result.rounds) == 1  # never auto-continued into a second round
    assert result.rounds[0].failure_category is RecoveryFailureCategory.EXECUTION_UNCERTAIN
    assert read_calls["count"] == 1  # position was never re-read — no re-plan attempted


def test_build_pipeline_components_reuses_a_supplied_client_and_does_not_close_it() -> None:
    settings = _settings()
    client = KeeperHubClient(settings)

    parts = build_pipeline_components(settings, client=client)

    assert parts.owns_client is False
    assert parts.keeperhub_client is client
    client.close()
