from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from aegis.aave import AaveUserAccountData
from aegis.audit import AuditLogger
from aegis.config import Settings
from aegis.execution import VerificationTimeoutError
from aegis.intents import Decision, Intent
from aegis.keeperhub.models import ExecutionStatus, ProtocolActionExecution, ProtocolActionSimulation
from aegis.policy import PolicyDecision, PolicyEngine
from aegis.recovery import (
    InvalidStateTransitionError,
    RecoveryFailureCategory,
    RunState,
    run_with_recovery,
    transition,
)
from tests.fixtures.keeperhub_payloads import (
    EXECUTE_TRANSFER_RESULT,
    EXECUTION_STATUS_RESULT,
    MOCK_AAVE_ACCOUNT_DATA_AT_RISK,
    MOCK_AAVE_ACCOUNT_DATA_SAFE,
)

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


def _position(data: dict) -> AaveUserAccountData:
    return AaveUserAccountData.model_validate(data)


def _sim(*, success: bool = True, would_revert: bool = False, gas_estimate: str | None = None):
    return ProtocolActionSimulation(success=success, wouldRevert=would_revert, gasEstimate=gas_estimate)


def _parts(settings: Settings, account_data: dict, *, simulate_side_effect, account_data_after: dict | None = None):
    after = account_data_after if account_data_after is not None else account_data
    position_reader = MagicMock()
    position_reader.get_account_data.side_effect = [_position(account_data), _position(after)]

    simulation_service = MagicMock()
    simulation_service.simulate.side_effect = simulate_side_effect

    execution_service = MagicMock()
    execution_service.execute.return_value = ProtocolActionExecution.model_validate(EXECUTE_TRANSFER_RESULT)

    verification_service = MagicMock()
    verification_service.verify.return_value = ExecutionStatus.model_validate(EXECUTION_STATUS_RESULT)

    return {
        "position_reader": position_reader,
        "policy_engine": PolicyEngine(settings),
        "simulation_service": simulation_service,
        "execution_service": execution_service,
        "verification_service": verification_service,
    }


def _always_passes(protocol_action: str, params: dict) -> ProtocolActionSimulation:
    return _sim()


def _run(settings: Settings, parts: dict, audit: AuditLogger):
    return run_with_recovery(
        settings=settings,
        audit=audit,
        network=NETWORK,
        user=USER,
        debt_asset=DEBT_ASSET,
        collateral_asset=COLLATERAL_ASSET,
        **parts,
    )


def _stages(audit: AuditLogger, run_id: str) -> list[str]:
    return [e.stage for e in audit.events_for(run_id)]


def _assert_subsequence(full: list[str], required: list[str]) -> None:
    """Assert `required` appears in `full`, in order, not necessarily
    contiguous — the exact assertion "12. Audit trail contains every
    recovery stage" needs, since extra interleaved detail events (e.g.
    POLICY_CHECK) are expected and fine."""
    it = iter(full)
    for stage in required:
        assert stage in it, f"expected {stage!r} to appear (in order) in {full}"


# --- 1/2/3: preferred fails, alternative selected, preferred not retried --


def test_preferred_action_simulates_and_reaches_ready_to_execute() -> None:
    settings = _settings()  # autonomous execution off by default
    audit = AuditLogger()
    parts = _parts(settings, MOCK_AAVE_ACCOUNT_DATA_AT_RISK, simulate_side_effect=_always_passes)

    result = _run(settings, parts, audit)

    assert result.final_state is RunState.READY_TO_EXECUTE
    assert result.selected is not None
    assert result.selected.decision is not Decision.DO_NOTHING
    assert result.recovery_attempts == []
    assert result.executed is False
    assert result.stop_reason == "autonomous execution disabled"
    stages = _stages(audit, result.run_id)
    assert stages == [
        "DETECTED", "ANALYZED", "EVALUATING", "CANDIDATE_SELECTED", "SIMULATED",
        "POLICY_CHECK", "READY_TO_EXECUTE", "STOPPED",
    ]


def test_preferred_repay_fails_simulation_recovers_to_add_collateral() -> None:
    """The exact scenario from the spec: REPAY_DEBT is preferred, its
    simulation fails (e.g. insufficient USDC balance), and Aegis selects
    ADD_COLLATERAL instead — never retrying REPAY_DEBT."""
    settings = _settings()

    def simulate(protocol_action: str, params: dict) -> ProtocolActionSimulation:
        if protocol_action == "aave-v3/repay":
            return _sim(success=False)
        return _sim(success=True)

    audit = AuditLogger()
    parts = _parts(settings, MOCK_AAVE_ACCOUNT_DATA_AT_RISK, simulate_side_effect=simulate)

    result = _run(settings, parts, audit)

    # 1 & 2: failure recorded, candidate marked unavailable
    assert len(result.recovery_attempts) == 1
    rejected = result.recovery_attempts[0]
    assert rejected.decision is Decision.REPAY_DEBT
    assert rejected.rejection_reason == "simulation failed or would revert"

    # 3-6: reassessed, evaluated + simulated + selected ADD_COLLATERAL
    assert result.final_state is RunState.READY_TO_EXECUTE
    assert result.selected is not None
    assert result.selected.decision is Decision.ADD_COLLATERAL
    assert result.selected.eligible

    stages = _stages(audit, result.run_id)
    assert stages == [
        "DETECTED", "ANALYZED", "EVALUATING",
        "CANDIDATE_SELECTED", "SIMULATED", "SIMULATION_FAILED", "RECOVERY_STARTED",
        "ALTERNATIVE_SELECTED", "SIMULATED", "POLICY_CHECK", "READY_TO_EXECUTE",
        "STOPPED",
    ]
    # 3. Preferred action is not retried: REPAY_DEBT was simulated exactly
    # once, never again.
    repay_calls = [c for c in parts["simulation_service"].simulate.call_args_list if c.args[0] == "aave-v3/repay"]
    assert len(repay_calls) == 1

    failed_events = [e for e in audit.events_for(result.run_id) if e.stage == "SIMULATION_FAILED"]
    assert len(failed_events) == 1
    assert failed_events[0].detail["candidate"] == "REPAY_DEBT"
    assert failed_events[0].detail["failure_category"] == RecoveryFailureCategory.SIMULATION_FAILURE.value
    assert failed_events[0].detail["failure_reason"] == "simulation failed or would revert"


def test_all_candidates_fail_simulation_reaches_no_safe_action() -> None:
    settings = _settings()

    def simulate(protocol_action: str, params: dict) -> ProtocolActionSimulation:
        return _sim(success=False)

    audit = AuditLogger()
    parts = _parts(settings, MOCK_AAVE_ACCOUNT_DATA_AT_RISK, simulate_side_effect=simulate)

    result = _run(settings, parts, audit)

    assert result.final_state is RunState.NO_SAFE_ACTION
    assert result.executed is False
    assert result.selected is not None
    assert result.selected.decision is Decision.DO_NOTHING
    assert {c.decision for c in result.recovery_attempts} == {Decision.REPAY_DEBT, Decision.ADD_COLLATERAL}
    stages = _stages(audit, result.run_id)
    assert stages.count("RECOVERY_STARTED") == 2
    assert stages[-1] == "NO_SAFE_ACTION"
    parts["execution_service"].execute.assert_not_called()


def test_connected_wallet_that_is_not_the_authorized_execution_wallet_never_executes() -> None:
    """EXECUTION AUTHORITY guarantee: reading a position for a connected
    USER WALLET must never imply Aegis can act on its behalf. Here the
    position read as `user` is genuinely at risk and would simulate fine,
    but `user` differs from settings.aegis_expected_wallet_address (the
    KeeperHub-authorized execution wallet) — PolicyEngine's own wallet-pin
    check (aegis.policy) must reject every candidate that would move
    funds, leaving only DO_NOTHING, so the run ends NO_SAFE_ACTION and the
    execution service is never called. No new logic under test here; this
    proves the existing wallet-pin check is what a connected wallet
    actually goes through end to end."""
    settings = _settings()  # aegis_expected_wallet_address=USER, i.e. "0xWallet"
    connected_wallet = "0xConnectedButNotAuthorized00000000000"

    audit = AuditLogger()
    parts = _parts(settings, MOCK_AAVE_ACCOUNT_DATA_AT_RISK, simulate_side_effect=_always_passes)

    result = run_with_recovery(
        settings=settings, audit=audit, network=NETWORK, user=connected_wallet,
        debt_asset=DEBT_ASSET, collateral_asset=COLLATERAL_ASSET, **parts,
    )

    assert result.final_state is RunState.NO_SAFE_ACTION
    assert result.executed is False
    assert result.selected is not None
    assert result.selected.decision is Decision.DO_NOTHING
    for candidate in result.candidates:
        if candidate.decision is Decision.DO_NOTHING:
            continue
        assert candidate.eligible is False
        assert candidate.rejection_reason is not None and "wallet" in candidate.rejection_reason.lower()
    parts["execution_service"].execute.assert_not_called()


def test_safe_position_resolves_via_do_nothing_without_simulating() -> None:
    settings = _settings()
    audit = AuditLogger()
    parts = _parts(settings, MOCK_AAVE_ACCOUNT_DATA_SAFE, simulate_side_effect=_always_passes)

    result = _run(settings, parts, audit)

    assert result.final_state is RunState.RESOLVED
    assert result.selected is not None
    assert result.selected.decision is Decision.DO_NOTHING
    assert result.recovery_attempts == []
    parts["simulation_service"].simulate.assert_not_called()
    stages = _stages(audit, result.run_id)
    assert stages == ["DETECTED", "ANALYZED", "EVALUATING", "RESOLVED"]


# --- 8: successful execution is verified before considering it resolved ---


def test_full_execution_resolves_and_reassesses_risk() -> None:
    settings = _settings(aegis_autonomous_execution_enabled=True)
    audit = AuditLogger()
    parts = _parts(
        settings, MOCK_AAVE_ACCOUNT_DATA_AT_RISK,
        simulate_side_effect=_always_passes, account_data_after=MOCK_AAVE_ACCOUNT_DATA_SAFE,
    )

    result = _run(settings, parts, audit)

    assert result.final_state is RunState.RESOLVED
    assert result.executed is True
    assert result.failure_category is None
    assert result.risk_after is not None
    assert result.risk_after.at_risk is False
    parts["execution_service"].execute.assert_called_once()
    parts["verification_service"].verify.assert_called_once()
    stages = _stages(audit, result.run_id)
    assert stages == [
        "DETECTED", "ANALYZED", "EVALUATING", "CANDIDATE_SELECTED", "SIMULATED",
        "POLICY_CHECK", "READY_TO_EXECUTE", "EXECUTING", "EXECUTED",
        "VERIFYING", "VERIFIED", "REASSESS_RISK", "RESOLVED",
    ]
    # verification happened, and only then was the outcome RESOLVED —
    # never assumed from the execute() response alone.
    verified_index = stages.index("VERIFIED")
    resolved_index = stages.index("RESOLVED")
    assert verified_index < resolved_index


# --- EXECUTION_UNCERTAIN: execute() raises with no execution id ------------


def test_execute_error_with_no_execution_id_is_uncertain_not_retried() -> None:
    settings = _settings(aegis_autonomous_execution_enabled=True)
    audit = AuditLogger()
    parts = _parts(settings, MOCK_AAVE_ACCOUNT_DATA_AT_RISK, simulate_side_effect=_always_passes)
    parts["execution_service"].execute.side_effect = RuntimeError("network error broadcasting tx")

    result = _run(settings, parts, audit)

    assert result.final_state is RunState.UNCERTAIN
    assert result.failure_category is RecoveryFailureCategory.EXECUTION_UNCERTAIN
    assert result.executed is False
    assert result.verification is None
    assert result.stop_reason is not None and "uncertain" in result.stop_reason
    # never attempted a second candidate after the broadcast attempt failed
    assert parts["simulation_service"].simulate.call_count == 1
    stages = _stages(audit, result.run_id)
    assert stages[-2:] == ["EXECUTE_ERROR", "UNCERTAIN"]


# --- 4/5: confirmed execution failure (via clean terminal status) ---------


def test_confirmed_execution_failure_via_keeperhub_status_is_failed_not_uncertain() -> None:
    settings = _settings(aegis_autonomous_execution_enabled=True)
    audit = AuditLogger()
    parts = _parts(settings, MOCK_AAVE_ACCOUNT_DATA_AT_RISK, simulate_side_effect=_always_passes)
    parts["verification_service"].verify.return_value = ExecutionStatus(
        executionId=EXECUTE_TRANSFER_RESULT["executionId"],
        status="failed",
        error="reverted on-chain",
    )

    result = _run(settings, parts, audit)

    assert result.final_state is RunState.FAILED
    assert result.failure_category is RecoveryFailureCategory.EXECUTION_FAILURE
    assert result.executed is True
    assert result.verification is not None
    assert result.verification.succeeded is False
    # confirmed THROUGH KeeperHub's own status endpoint — verify() only
    # returns once a terminal status is reached.
    parts["verification_service"].verify.assert_called_once()
    parts["execution_service"].execute.assert_called_once()
    stages = _stages(audit, result.run_id)
    assert stages[-2:] == ["VERIFIED", "FAILED"]


# --- 6/7: execution timeout triggers a status lookup, never a resend ------


def test_execution_timeout_triggers_a_status_lookup_not_a_resend() -> None:
    settings = _settings(aegis_autonomous_execution_enabled=True)
    audit = AuditLogger()
    parts = _parts(settings, MOCK_AAVE_ACCOUNT_DATA_AT_RISK, simulate_side_effect=_always_passes)
    parts["verification_service"].verify.side_effect = VerificationTimeoutError("still unconfirmed")
    parts["verification_service"].check_status_once.return_value = ExecutionStatus(
        executionId=EXECUTE_TRANSFER_RESULT["executionId"], status="unconfirmed",
    )

    result = _run(settings, parts, audit)

    # do NOT resend: execute() is called exactly once regardless of outcome
    parts["execution_service"].execute.assert_called_once()
    parts["verification_service"].check_status_once.assert_called_once_with(
        EXECUTE_TRANSFER_RESULT["executionId"]
    )
    assert result.final_state is RunState.UNCERTAIN
    assert result.failure_category is RecoveryFailureCategory.EXECUTION_TIMEOUT
    stages = _stages(audit, result.run_id)
    assert stages[-3:] == ["VERIFY_TIMEOUT", "STATUS_CHECKED", "UNCERTAIN"]


def test_execution_timeout_confirmed_failed_via_status_check_allows_replanning() -> None:
    settings = _settings(aegis_autonomous_execution_enabled=True)
    audit = AuditLogger()
    parts = _parts(settings, MOCK_AAVE_ACCOUNT_DATA_AT_RISK, simulate_side_effect=_always_passes)
    parts["verification_service"].verify.side_effect = VerificationTimeoutError("still unconfirmed")
    parts["verification_service"].check_status_once.return_value = ExecutionStatus(
        executionId=EXECUTE_TRANSFER_RESULT["executionId"], status="failed", error="reverted",
    )

    result = _run(settings, parts, audit)

    assert result.final_state is RunState.FAILED  # confirmed, not uncertain — safe to re-plan
    assert result.failure_category is RecoveryFailureCategory.EXECUTION_TIMEOUT
    parts["execution_service"].execute.assert_called_once()


# --- 7: uncertain execution stops the pipeline (no further candidates) ----


def test_uncertain_execution_never_falls_back_to_another_candidate() -> None:
    """Even though ADD_COLLATERAL would have been a perfectly fine
    fallback, once EXECUTING has been reached, an uncertain outcome must
    never trigger trying a different candidate — that could stack a
    second real transaction on an unresolved first one."""
    settings = _settings(aegis_autonomous_execution_enabled=True)
    audit = AuditLogger()
    parts = _parts(settings, MOCK_AAVE_ACCOUNT_DATA_AT_RISK, simulate_side_effect=_always_passes)
    parts["execution_service"].execute.side_effect = RuntimeError("connection reset")

    result = _run(settings, parts, audit)

    assert result.final_state is RunState.UNCERTAIN
    assert result.selected is not None and result.selected.decision is Decision.REPAY_DEBT
    # ADD_COLLATERAL was never attempted after the uncertain outcome
    assert parts["simulation_service"].simulate.call_count == 1
    assert parts["execution_service"].execute.call_count == 1


# --- policy safety controls preserved under recovery -----------------------


def test_mainnet_candidates_never_selected_and_never_simulated() -> None:
    settings = _settings()
    audit = AuditLogger()
    parts = _parts(settings, MOCK_AAVE_ACCOUNT_DATA_AT_RISK, simulate_side_effect=_always_passes)

    result = run_with_recovery(
        settings=settings,
        audit=audit,
        network="8453",  # Base mainnet
        user=USER,
        debt_asset=DEBT_ASSET,
        collateral_asset=COLLATERAL_ASSET,
        **parts,
    )

    assert result.final_state is RunState.NO_SAFE_ACTION
    assert result.selected.decision is Decision.DO_NOTHING
    for c in result.candidates:
        if c.decision is not Decision.DO_NOTHING:
            assert not c.eligible
            assert "mainnet" in c.rejection_reason
    parts["simulation_service"].simulate.assert_not_called()


def test_amount_over_spending_limit_never_reaches_simulation() -> None:
    settings = _settings(aegis_max_tx_amount=Decimal("1"))
    audit = AuditLogger()
    parts = _parts(settings, MOCK_AAVE_ACCOUNT_DATA_AT_RISK, simulate_side_effect=_always_passes)

    result = _run(settings, parts, audit)

    assert result.final_state is RunState.NO_SAFE_ACTION
    for c in result.candidates:
        if c.decision is not Decision.DO_NOTHING:
            assert not c.eligible
            assert "policy:" in c.rejection_reason
    parts["simulation_service"].simulate.assert_not_called()


# --- policy rejection after candidate selection ----------------------------


def test_policy_rejection_after_candidate_selection_triggers_recovery() -> None:
    """PolicyEngine.evaluate is deterministic and stateless, so normally
    the EVALUATING pre-check and the final per-candidate POLICY_CHECK
    always agree. To prove that final gate is real and enforced rather
    than dead code, this test uses a policy engine that approves every
    candidate through simulation, then revokes approval at each
    candidate's final check. Both real candidates must be rejected at
    that later gate, never executed, and DO_NOTHING must be the result."""
    settings = _settings()
    audit = AuditLogger()
    parts = _parts(settings, MOCK_AAVE_ACCOUNT_DATA_AT_RISK, simulate_side_effect=_always_passes)

    approved = PolicyDecision(approved=True, violated_rules=[])
    rejected = PolicyDecision(approved=False, violated_rules=["policy revoked after candidate selection"])
    policy_engine = MagicMock()
    policy_engine.evaluate.side_effect = [
        approved, approved, approved,  # EVALUATING: DO_NOTHING, REPAY_DEBT, ADD_COLLATERAL
        approved, rejected,  # REPAY_DEBT: pre-simulation-score check passes, final check fails
        approved, rejected,  # ADD_COLLATERAL: same
    ]
    parts["policy_engine"] = policy_engine

    result = _run(settings, parts, audit)

    assert result.final_state is RunState.NO_SAFE_ACTION
    assert result.selected.decision is Decision.DO_NOTHING
    assert result.executed is False
    rejected_at_final_check = [
        c for c in result.recovery_attempts if c.rejection_reason and c.rejection_reason.startswith("policy:")
    ]
    assert {c.decision for c in rejected_at_final_check} == {Decision.REPAY_DEBT, Decision.ADD_COLLATERAL}
    for c in rejected_at_final_check:
        assert "revoked after candidate selection" in c.rejection_reason
        assert c.simulation_status.value == "PASSED"  # rejected AFTER a passing simulation, not before
    parts["execution_service"].execute.assert_not_called()
    stages = _stages(audit, result.run_id)
    assert stages.count("POLICY_CHECK") == 2  # both real candidates reached the final gate
    assert stages.count("POLICY_REJECTED") == 2
    assert stages.count("CANDIDATE_SELECTED") + stages.count("ALTERNATIVE_SELECTED") == 2


# --- 12: audit trail contains every required recovery stage ----------------


def test_audit_trail_contains_the_full_recovery_narrative() -> None:
    settings = _settings(aegis_autonomous_execution_enabled=True)
    audit = AuditLogger()

    def simulate(protocol_action: str, params: dict) -> ProtocolActionSimulation:
        if protocol_action == "aave-v3/repay":
            return _sim(success=False)
        return _sim(success=True)

    parts = _parts(
        settings, MOCK_AAVE_ACCOUNT_DATA_AT_RISK,
        simulate_side_effect=simulate, account_data_after=MOCK_AAVE_ACCOUNT_DATA_SAFE,
    )

    result = _run(settings, parts, audit)

    assert result.final_state is RunState.RESOLVED
    stages = _stages(audit, result.run_id)
    _assert_subsequence(
        stages,
        [
            "DETECTED", "ANALYZED", "CANDIDATE_SELECTED", "SIMULATED", "SIMULATION_FAILED",
            "RECOVERY_STARTED", "ALTERNATIVE_SELECTED", "SIMULATED", "EXECUTED", "VERIFIED", "RESOLVED",
        ],
    )

    # every event carries the minimum required audit fields
    for event in audit.events_for(result.run_id):
        assert event.run_id == result.run_id
        assert event.timestamp is not None
        # detail dict always has these keys (value may legitimately be
        # None), except for the two pre-state-machine bookkeeping events.
        if event.stage not in ("DETECTED", "ANALYZED"):
            for key in (
                "state", "candidate", "failure_category", "failure_reason",
                "execution_id", "transaction_hash", "risk_before", "risk_after", "recovery_decision",
            ):
                assert key in event.detail, f"{event.stage} missing required field {key!r}"


# --- 13: invalid state transitions are rejected -----------------------------


def test_invalid_state_transition_is_rejected() -> None:
    audit = AuditLogger()
    # EVALUATING may go straight to RESOLVED/NO_SAFE_ACTION (nothing to
    # attempt) or SIMULATING (something to attempt) — never straight to
    # EXECUTING, skipping candidate selection and simulation entirely.
    with pytest.raises(InvalidStateTransitionError):
        transition(audit, "run-1", RunState.EVALUATING, RunState.EXECUTING, stage="EXECUTING")
    # a terminal state (RESOLVED) can never transition to anything else
    with pytest.raises(InvalidStateTransitionError):
        transition(audit, "run-1", RunState.RESOLVED, RunState.EXECUTING, stage="EXECUTING")
    # READY_TO_EXECUTE can only ever move forward to EXECUTING, never
    # back to SIMULATING (that would mean silently trying another
    # candidate after one was already selected)
    with pytest.raises(InvalidStateTransitionError):
        transition(audit, "run-1", RunState.READY_TO_EXECUTE, RunState.SIMULATING, stage="SIMULATING")
    # UNCERTAIN is a hard stop — it can never transition onward
    with pytest.raises(InvalidStateTransitionError):
        transition(audit, "run-1", RunState.UNCERTAIN, RunState.EVALUATING, stage="EVALUATING")


def test_valid_state_transitions_are_accepted() -> None:
    audit = AuditLogger()
    state = transition(audit, "run-2", None, RunState.EVALUATING, stage="EVALUATING")
    state = transition(audit, "run-2", state, RunState.SIMULATING, stage="CANDIDATE_SELECTED")
    state = transition(audit, "run-2", state, RunState.RECOVERING, stage="RECOVERY_STARTED")
    state = transition(audit, "run-2", state, RunState.SIMULATING, stage="ALTERNATIVE_SELECTED")
    state = transition(audit, "run-2", state, RunState.READY_TO_EXECUTE, stage="READY_TO_EXECUTE")
    state = transition(audit, "run-2", state, RunState.EXECUTING, stage="EXECUTING")
    state = transition(audit, "run-2", state, RunState.VERIFYING, stage="VERIFYING")
    state = transition(audit, "run-2", state, RunState.RESOLVED, stage="RESOLVED")
    assert state is RunState.RESOLVED
    assert len(audit.events_for("run-2")) == 8


# --- 14: Hermes, when consulted, competes on equal terms --------------------


class _FakeHermesAgent:
    def __init__(self, intent: Intent | None = None, error: Exception | None = None) -> None:
        self._intent = intent
        self._error = error

    def decide(self, position_summary: dict) -> Intent:
        if self._error is not None:
            raise self._error
        assert self._intent is not None
        return self._intent


def test_hermes_candidate_is_added_scored_like_any_other() -> None:
    hermes_intent = Intent(
        decision=Decision.ADD_COLLATERAL, protocol_action="aave-v3/supply", network=NETWORK,
        asset=COLLATERAL_ASSET, amount="12.5", on_behalf_of=USER, rationale="Hermes: add collateral",
    )
    hermes_agent = _FakeHermesAgent(intent=hermes_intent)
    settings = _settings(aegis_autonomous_execution_enabled=True)
    parts = _parts(
        settings, MOCK_AAVE_ACCOUNT_DATA_AT_RISK, simulate_side_effect=_always_passes,
        account_data_after=MOCK_AAVE_ACCOUNT_DATA_SAFE,
    )
    audit = AuditLogger()
    result = run_with_recovery(
        settings=settings, audit=audit, network=NETWORK, user=USER,
        debt_asset=DEBT_ASSET, collateral_asset=COLLATERAL_ASSET, hermes_agent=hermes_agent, **parts,
    )

    hermes_candidates = [c for c in result.candidates if c.source == "hermes"]
    assert len(hermes_candidates) == 1
    candidate = hermes_candidates[0]
    assert candidate.decision == Decision.ADD_COLLATERAL
    assert candidate.amount == "12.5"
    assert candidate.on_behalf_of == USER  # the run's own wallet, never trusted from the Intent
    # scored by the exact same pipeline as the deterministic candidates —
    # never left out of financial/execution scoring.
    assert candidate.financial_detail is not None
    assert candidate.execution_detail is not None
    assert any(e.stage == "HERMES_CONSULTED" for e in audit.events_for(result.run_id))


def test_hermes_do_nothing_adds_no_extra_candidate() -> None:
    hermes_agent = _FakeHermesAgent(intent=Intent(decision=Decision.DO_NOTHING, rationale="looks fine to me"))
    settings = _settings(aegis_autonomous_execution_enabled=True)
    parts = _parts(
        settings, MOCK_AAVE_ACCOUNT_DATA_AT_RISK, simulate_side_effect=_always_passes,
        account_data_after=MOCK_AAVE_ACCOUNT_DATA_SAFE,
    )
    audit = AuditLogger()
    result = run_with_recovery(
        settings=settings, audit=audit, network=NETWORK, user=USER,
        debt_asset=DEBT_ASSET, collateral_asset=COLLATERAL_ASSET, hermes_agent=hermes_agent, **parts,
    )
    assert not any(c.source == "hermes" for c in result.candidates)
    assert any(e.stage == "HERMES_CONSULTED" for e in audit.events_for(result.run_id))


def test_hermes_failure_never_breaks_the_run() -> None:
    hermes_agent = _FakeHermesAgent(error=RuntimeError("LLM API down"))
    settings = _settings(aegis_autonomous_execution_enabled=True)
    parts = _parts(
        settings, MOCK_AAVE_ACCOUNT_DATA_AT_RISK, simulate_side_effect=_always_passes,
        account_data_after=MOCK_AAVE_ACCOUNT_DATA_SAFE,
    )
    audit = AuditLogger()
    result = run_with_recovery(
        settings=settings, audit=audit, network=NETWORK, user=USER,
        debt_asset=DEBT_ASSET, collateral_asset=COLLATERAL_ASSET, hermes_agent=hermes_agent, **parts,
    )
    assert result.final_state is RunState.RESOLVED  # deterministic candidates still resolve the run
    assert not any(c.source == "hermes" for c in result.candidates)
    hermes_events = [e for e in audit.events_for(result.run_id) if e.stage == "HERMES_CONSULTED"]
    assert len(hermes_events) == 1
    assert "RuntimeError" in hermes_events[0].detail["recovery_decision"]


def test_no_hermes_agent_is_unchanged_behavior() -> None:
    settings = _settings(aegis_autonomous_execution_enabled=True)
    parts = _parts(
        settings, MOCK_AAVE_ACCOUNT_DATA_AT_RISK, simulate_side_effect=_always_passes,
        account_data_after=MOCK_AAVE_ACCOUNT_DATA_SAFE,
    )
    audit = AuditLogger()
    result = run_with_recovery(
        settings=settings, audit=audit, network=NETWORK, user=USER,
        debt_asset=DEBT_ASSET, collateral_asset=COLLATERAL_ASSET, **parts,
    )
    assert not any(c.source == "hermes" for c in result.candidates)
    assert not any(e.stage == "HERMES_CONSULTED" for e in audit.events_for(result.run_id))


def test_hermes_reckless_proposal_is_rejected_like_any_bad_candidate() -> None:
    """A Hermes proposal that fails simulation gets zero execution score
    and is never selected — no privilege, exactly like a deterministic
    candidate would be for the same failure."""
    hermes_intent = Intent(
        decision=Decision.REPAY_DEBT, protocol_action="aave-v3/repay", network=NETWORK,
        asset=DEBT_ASSET, amount="999999", on_behalf_of=USER, rationale="Hermes: repay a lot",
    )
    hermes_agent = _FakeHermesAgent(intent=hermes_intent)

    def _repay_fails(protocol_action: str, params: dict) -> ProtocolActionSimulation:
        if protocol_action == "aave-v3/repay":
            return _sim(success=False, would_revert=True)
        return _sim()

    settings = _settings(aegis_autonomous_execution_enabled=True)
    parts = _parts(
        settings, MOCK_AAVE_ACCOUNT_DATA_AT_RISK, simulate_side_effect=_repay_fails,
        account_data_after=MOCK_AAVE_ACCOUNT_DATA_SAFE,
    )
    audit = AuditLogger()
    result = run_with_recovery(
        settings=settings, audit=audit, network=NETWORK, user=USER,
        debt_asset=DEBT_ASSET, collateral_asset=COLLATERAL_ASSET, hermes_agent=hermes_agent, **parts,
    )
    assert result.selected is not None
    assert result.selected.source != "hermes"
    hermes_candidate = next(c for c in result.candidates if c.source == "hermes")
    assert not hermes_candidate.eligible
