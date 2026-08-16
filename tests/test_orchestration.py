"""Integration tests for the complete Aegis orchestration loop
(aegis.pipeline.run_pipeline), covering Phase 17's specific new
capabilities (hard recovery limits, idempotency, RUN_* audit vocabulary,
secret-scrubbing) plus pipeline-level versions of the scenarios that need
the full orchestrator rather than just aegis.recovery in isolation.

Most of the 20 required scenarios are already covered, in more depth, by
existing tests written for earlier phases — duplicating them here would
just be redundant coverage of the same code path:

  1.  LOW risk -> DO_NOTHING -> RESOLVED
        -> covered here (test_low_risk_resolves_via_do_nothing) AND at
           the recovery level by test_recovery.py::
           test_safe_position_resolves_via_do_nothing_without_simulating
  2.  HIGH risk -> candidates -> simulation -> selected action
        -> test_pipeline.py::test_pipeline_resolves_in_one_round_end_to_end
  3.  Best financial candidate fails simulation -> alternative selected
        -> covered here (test_best_candidate_fails_simulation_alternative_selected)
           AND at the recovery level by test_recovery.py::
           test_preferred_repay_fails_simulation_recovers_to_add_collateral
  4.  Selected candidate fails policy -> recovery
        -> test_recovery.py::test_policy_rejection_after_candidate_selection_triggers_recovery
  5.  Execution succeeds -> verification succeeds -> RESOLVED
        -> test_pipeline.py::test_pipeline_resolves_in_one_round_end_to_end
  6.  Execution succeeds -> risk remains high -> RECOVERY
        -> test_pipeline.py::test_pipeline_replans_when_first_round_resolves_but_still_at_risk
  7.  Execution fails -> status checked -> recovery
        -> test_pipeline.py::test_pipeline_replans_after_confirmed_execution_failure_using_fresh_position
  8.  Execution times out -> status checked -> no duplicate execution
        -> test_recovery.py::test_execution_timeout_triggers_a_status_lookup_not_a_resend
  9.  Execution remains uncertain -> UNCERTAIN
        -> test_pipeline.py::test_pipeline_stops_on_uncertain_execution_without_replanning
  10. All candidates fail -> NO_SAFE_ACTION
        -> test_recovery.py::test_all_candidates_fail_simulation_reaches_no_safe_action
  11. Recovery limit reached -> STOPPED
        -> covered here (test_max_rounds_exhausted_stops_with_no_safe_action,
           test_max_write_attempts_limit_stops_the_run,
           test_max_total_execution_amount_limit_stops_the_run,
           test_max_runtime_limit_stops_before_starting_a_round)
  12. Mainnet candidate rejected
        -> test_recovery.py::test_mainnet_candidates_never_selected_and_never_simulated
  13. Unsupported protocol rejected
        -> test_decision_engine.py::test_unsupported_protocol_rejected_same_mechanism_as_unsupported_action
  14. Spending limit rejected
        -> test_recovery.py::test_amount_over_spending_limit_never_reaches_simulation
  15. Dry-run never executes
        -> test_pipeline.py::test_dry_run_never_executes_even_when_autonomous_execution_is_enabled
  16. Live execution only occurs when explicitly enabled
        -> covered here (test_live_execution_disabled_by_default_stops_before_execute)
  17. Every stage appears in the audit trail
        -> covered here (test_run_level_audit_vocabulary_present) AND at
           the recovery level by test_recovery.py::
           test_audit_trail_contains_the_full_recovery_narrative
  18. A new recovery cycle reads fresh position data
        -> test_pipeline.py::test_pipeline_replans_after_confirmed_execution_failure_using_fresh_position
  19. Identical inputs produce deterministic scores
        -> test_execution_confidence.py::
           test_repeated_engine_evaluation_of_identical_inputs_produces_identical_scores
  20. No secret appears in logs
        -> covered here (test_no_secret_appears_in_the_audit_trail)

Plus this phase's genuinely new capability: idempotent re-invocation
(test_accidental_double_invocation_does_not_duplicate_execution).
"""

import json

import httpx
import pytest
import respx

from aegis.config import Settings
from aegis.pipeline import PipelineComponents, run_pipeline
from aegis.recovery import RunAlreadyCompletedError, RunState, run_with_recovery
from aegis.audit import AuditLogger
from aegis.aave import AavePositionReader, AaveUserAccountData
from aegis.execution import ExecutionService, SimulationService, VerificationService
from aegis.policy import PolicyEngine
from unittest.mock import MagicMock
from aegis.keeperhub.models import ExecutionStatus, ProtocolActionExecution, ProtocolActionSimulation
from tests.fixtures.keeperhub_payloads import (
    EXECUTE_TRANSFER_RESULT,
    EXECUTION_STATUS_RESULT,
    MOCK_AAVE_ACCOUNT_DATA_AT_RISK,
    MOCK_AAVE_ACCOUNT_DATA_SAFE,
    SIMULATE_TRANSFER_RESULT,
)

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


def _position(data: dict) -> AaveUserAccountData:
    return AaveUserAccountData.model_validate(data)


def _fake_components(settings: Settings, *, simulate_side_effect, verify_status: dict) -> PipelineComponents:
    position_reader = MagicMock(spec=AavePositionReader)
    position_reader.get_account_data.return_value = _position(MOCK_AAVE_ACCOUNT_DATA_AT_RISK)
    simulation_service = MagicMock(spec=SimulationService)
    simulation_service.simulate.side_effect = simulate_side_effect
    execution_service = MagicMock(spec=ExecutionService)
    execution_service.execute.return_value = ProtocolActionExecution.model_validate(EXECUTE_TRANSFER_RESULT)
    verification_service = MagicMock(spec=VerificationService)
    verification_service.verify.return_value = ExecutionStatus.model_validate(verify_status)
    return PipelineComponents(
        keeperhub_client=MagicMock(), position_reader=position_reader, policy_engine=PolicyEngine(settings),
        simulation_service=simulation_service, execution_service=execution_service,
        verification_service=verification_service, owns_client=False,
    )


def _always_passes(protocol_action: str, params: dict) -> ProtocolActionSimulation:
    return ProtocolActionSimulation(success=True, wouldRevert=False)


# --- 1: LOW risk -> DO_NOTHING -> RESOLVED (full orchestrator) -------------


@respx.mock
def test_low_risk_resolves_via_do_nothing() -> None:
    settings = _settings(aegis_autonomous_execution_enabled=True)
    _mock_protocol_action_route([MOCK_AAVE_ACCOUNT_DATA_SAFE])

    result = run_pipeline(
        settings=settings, network=NETWORK, user=USER,
        debt_asset=DEBT_ASSET, collateral_asset=COLLATERAL_ASSET, dry_run=False,
    )

    assert result.resolved is True
    assert len(result.rounds) == 1
    assert result.rounds[0].selected.decision.value == "DO_NOTHING"
    assert result.rounds[0].executed is False  # DO_NOTHING never simulates or executes


# --- 3: best financial candidate fails simulation -> alternative selected --


@respx.mock
def test_best_candidate_fails_simulation_alternative_selected() -> None:
    settings = _settings(aegis_autonomous_execution_enabled=True)
    reads = [MOCK_AAVE_ACCOUNT_DATA_AT_RISK, MOCK_AAVE_ACCOUNT_DATA_SAFE]
    read_calls = {"count": 0}

    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["functionName"] == "getUserAccountData":
            index = read_calls["count"]
            read_calls["count"] += 1
            return httpx.Response(200, json={"result": reads[index]})
        if body.get("simulate") is True:
            if body["functionName"] == "repay":
                return httpx.Response(200, json={**SIMULATE_TRANSFER_RESULT, "success": False, "wouldRevert": True})
            return httpx.Response(200, json=SIMULATE_TRANSFER_RESULT)
        return httpx.Response(200, json=EXECUTE_TRANSFER_RESULT)

    respx.post(f"{BASE_URL}/api/execute/contract-call").mock(side_effect=handle)
    _mock_status_route()

    result = run_pipeline(
        settings=settings, network=NETWORK, user=USER,
        debt_asset=DEBT_ASSET, collateral_asset=COLLATERAL_ASSET, dry_run=False,
    )

    assert result.resolved is True
    round1 = result.rounds[0]
    assert round1.selected.decision.value == "ADD_COLLATERAL"  # REPAY_DEBT failed simulation
    rejected = next(c for c in round1.candidates if c.decision.value == "REPAY_DEBT")
    assert not rejected.eligible


# --- 11: recovery limits reached -> STOPPED ---------------------------------


@respx.mock
def test_max_rounds_exhausted_stops_with_no_safe_action() -> None:
    settings = _settings(aegis_autonomous_execution_enabled=True)
    # every round: executes, verifies successfully, but position stays AT_RISK
    reads = [MOCK_AAVE_ACCOUNT_DATA_AT_RISK] * 6  # READ + REASSESS per round, 3 rounds
    _mock_protocol_action_route(reads)
    _mock_status_route()

    result = run_pipeline(
        settings=settings, network=NETWORK, user=USER,
        debt_asset=DEBT_ASSET, collateral_asset=COLLATERAL_ASSET, dry_run=False, max_rounds=3,
    )

    assert result.resolved is False
    assert result.final_state is RunState.NO_SAFE_ACTION
    assert "max recovery rounds exhausted" in result.stop_reason
    assert len(result.rounds) == 3


def test_max_write_attempts_limit_stops_the_run() -> None:
    settings = _settings(aegis_autonomous_execution_enabled=True)
    components = _fake_components(
        settings, simulate_side_effect=_always_passes,
        verify_status={"executionId": "e1", "status": "failed", "error": "reverted"},
    )

    result = run_pipeline(
        settings=settings, network=NETWORK, user=USER, debt_asset=DEBT_ASSET, collateral_asset=COLLATERAL_ASSET,
        components=components, dry_run=False, max_rounds=10, max_write_attempts=2,
    )

    assert result.final_state is RunState.NO_SAFE_ACTION
    assert "write attempts" in result.stop_reason
    assert len(result.rounds) == 2
    assert components.execution_service.execute.call_count == 2


def test_max_total_execution_amount_limit_stops_the_run() -> None:
    from decimal import Decimal

    settings = _settings(aegis_autonomous_execution_enabled=True)
    components = _fake_components(
        settings, simulate_side_effect=_always_passes,
        verify_status={"executionId": "e1", "status": "failed", "error": "reverted"},
    )

    result = run_pipeline(
        settings=settings, network=NETWORK, user=USER, debt_asset=DEBT_ASSET, collateral_asset=COLLATERAL_ASSET,
        components=components, dry_run=False, max_rounds=10, max_total_execution_amount=Decimal("3"),
    )

    assert result.final_state is RunState.NO_SAFE_ACTION
    assert "execution amount" in result.stop_reason


def test_max_runtime_limit_stops_before_starting_a_round() -> None:
    settings = _settings(aegis_autonomous_execution_enabled=True)
    components = _fake_components(
        settings, simulate_side_effect=_always_passes,
        verify_status=EXECUTION_STATUS_RESULT,
    )

    result = run_pipeline(
        settings=settings, network=NETWORK, user=USER, debt_asset=DEBT_ASSET, collateral_asset=COLLATERAL_ASSET,
        components=components, dry_run=False, max_rounds=100, max_runtime_seconds=0.0,
    )

    assert result.final_state is RunState.NO_SAFE_ACTION
    assert "runtime" in result.stop_reason
    assert len(result.rounds) == 0
    components.execution_service.execute.assert_not_called()


# --- 16: live execution only occurs when explicitly enabled -----------------


def test_live_execution_disabled_by_default_stops_before_execute() -> None:
    settings = _settings()  # aegis_autonomous_execution_enabled defaults to False
    components = _fake_components(
        settings, simulate_side_effect=_always_passes, verify_status=EXECUTION_STATUS_RESULT,
    )

    result = run_pipeline(
        settings=settings, network=NETWORK, user=USER, debt_asset=DEBT_ASSET, collateral_asset=COLLATERAL_ASSET,
        components=components, dry_run=False,
    )

    assert result.final_state is RunState.READY_TO_EXECUTE
    assert result.resolved is False
    components.execution_service.execute.assert_not_called()


# --- 17: RUN_* audit vocabulary is present ----------------------------------


def test_run_level_audit_vocabulary_present() -> None:
    settings = _settings(aegis_autonomous_execution_enabled=True)
    audit = AuditLogger()
    components = _fake_components(
        settings, simulate_side_effect=_always_passes, verify_status=EXECUTION_STATUS_RESULT,
    )
    # AT_RISK for the initial read, SAFE for REASSESS_RISK — so this round
    # actually resolves instead of endlessly re-planning to max_rounds.
    components.position_reader.get_account_data.side_effect = [
        _position(MOCK_AAVE_ACCOUNT_DATA_AT_RISK), _position(MOCK_AAVE_ACCOUNT_DATA_SAFE),
    ]

    result = run_pipeline(
        settings=settings, network=NETWORK, user=USER, debt_asset=DEBT_ASSET, collateral_asset=COLLATERAL_ASSET,
        components=components, dry_run=False, audit=audit,
    )

    assert result.resolved is True
    all_stages = {e.stage for e in audit._events}  # every event this run recorded, across ids
    assert "RUN_STARTED" in all_stages
    assert "RUN_RESOLVED" in all_stages
    # every event carries run_id/stage/timestamp/detail structurally
    for event in audit._events:
        assert event.run_id
        assert event.stage
        assert event.timestamp is not None
        assert isinstance(event.detail, dict)


# --- 20: no secret ever appears in the audit trail --------------------------


def test_no_secret_appears_in_the_audit_trail() -> None:
    settings = _settings(aegis_autonomous_execution_enabled=True)
    audit = AuditLogger()
    components = _fake_components(
        settings, simulate_side_effect=_always_passes, verify_status=EXECUTION_STATUS_RESULT,
    )

    run_pipeline(
        settings=settings, network=NETWORK, user=USER, debt_asset=DEBT_ASSET, collateral_asset=COLLATERAL_ASSET,
        components=components, dry_run=False, audit=audit,
    )

    serialized = "\n".join(event.model_dump_json() for event in audit._events)
    assert settings.keeperhub_api_key not in serialized
    assert "Authorization" not in serialized
    assert "Bearer" not in serialized


# --- idempotency: accidental double invocation never duplicates execution --


def test_accidental_double_invocation_does_not_duplicate_execution() -> None:
    settings = _settings(aegis_autonomous_execution_enabled=True)
    audit = AuditLogger()
    position_reader = MagicMock(spec=AavePositionReader)
    position_reader.get_account_data.return_value = _position(MOCK_AAVE_ACCOUNT_DATA_AT_RISK)
    simulation_service = MagicMock(spec=SimulationService)
    simulation_service.simulate.side_effect = _always_passes
    execution_service = MagicMock(spec=ExecutionService)
    execution_service.execute.return_value = ProtocolActionExecution.model_validate(EXECUTE_TRANSFER_RESULT)
    verification_service = MagicMock(spec=VerificationService)
    from aegis.execution import VerificationTimeoutError
    verification_service.verify.side_effect = VerificationTimeoutError("still pending")
    verification_service.check_status_once.return_value = ExecutionStatus(
        executionId=EXECUTE_TRANSFER_RESULT["executionId"], status="unconfirmed",
    )

    common_kwargs = dict(
        settings=settings, position_reader=position_reader, policy_engine=PolicyEngine(settings),
        simulation_service=simulation_service, execution_service=execution_service,
        verification_service=verification_service, audit=audit, network=NETWORK, user=USER,
        debt_asset=DEBT_ASSET, collateral_asset=COLLATERAL_ASSET,
    )
    fixed_run_id = "accidental-double-invocation"

    first = run_with_recovery(run_id=fixed_run_id, **common_kwargs)
    assert first.final_state is RunState.UNCERTAIN
    assert execution_service.execute.call_count == 1

    # caller (accidentally) invokes again with the SAME run_id
    verification_service.check_status_once.return_value = ExecutionStatus(
        executionId=EXECUTE_TRANSFER_RESULT["executionId"], status="completed",
        transactionHash=EXECUTE_TRANSFER_RESULT["transactionHash"],
    )
    second = run_with_recovery(run_id=fixed_run_id, **common_kwargs)

    assert second.final_state is RunState.RESOLVED
    assert execution_service.execute.call_count == 1  # still just once — never duplicated
    # check_status_once is called twice in total: once by the FIRST call's
    # own timeout handling, once more by the SECOND call's idempotency
    # guard — never a re-execute, always a re-check.
    assert verification_service.check_status_once.call_count == 2
    for call in verification_service.check_status_once.call_args_list:
        assert call.args == (EXECUTE_TRANSFER_RESULT["executionId"],)

    # a THIRD invocation of the now-terminal run_id must be refused outright
    with pytest.raises(RunAlreadyCompletedError):
        run_with_recovery(run_id=fixed_run_id, **common_kwargs)
