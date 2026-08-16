"""Execution recovery and re-planning: Aegis must not simply retry a failed
transaction.

If the preferred candidate can't be executed, Aegis reassesses the
remaining candidates and selects the next safest viable one — the same
GENERATE/SCORE/FEASIBILITY pipeline aegis.decision_engine already runs,
just applied one candidate at a time so a rejection can trigger picking the
next-best candidate instead of giving up. Nothing here is reimplemented:
this module is a thin state machine wrapped around
aegis.decision_engine's pure scoring functions and aegis.execution's
services.

The critical distinction this module encodes is *when* recovery is safe,
and different failures are NOT all treated the same (RecoveryFailureCategory
below):

- SIMULATION_FAILURE / POLICY_REJECTION: caught before anything was ever
  broadcast — zero on-chain risk, so reassessing and trying the next
  candidate within the SAME round is always safe. This is RECOVERING.
- EXECUTION_FAILURE: execute() succeeded (broadcast) and KeeperHub's own
  status endpoint later confirms, via a clean terminal result, that it
  failed on-chain. Known, not guessed — safe to start a brand NEW round
  (fresh READ, fresh candidates) on the next planning cycle.
- EXECUTION_TIMEOUT: verify()'s polling budget ran out before reaching a
  terminal status. This is checked ONE more time
  (VerificationService.check_status_once) before deciding anything — if
  that check now shows a clean terminal result, it's handled exactly like
  EXECUTION_FAILURE (confirmed failed) or a normal success; if it's still
  non-terminal, the outcome is genuinely unknown and this becomes
  EXECUTION_UNCERTAIN.
- EXECUTION_UNCERTAIN / an unresolved EXECUTION_TIMEOUT / an execute()
  call that raised without ever producing an execution id: Aegis cannot
  be sure what state the chain is actually in. These NEVER trigger
  another candidate or another round automatically — they end in
  UNCERTAIN, a hard stop. Recovering by trying something else here could
  stack a second real transaction on top of an unresolved first one,
  exactly what "never blindly resend an uncertain transaction" forbids.
- VERIFICATION_FAILURE: verify() itself broke unexpectedly (not a timeout,
  not a clean terminal result — some other error while checking). Rather
  than guess, this re-reads the real position and compares it to the risk
  captured before execution: if it changed, something confirmably
  happened on-chain and a new round is safe; if it's identical, the
  outcome is still unknown and this stops in UNCERTAIN too.
- RISK_NOT_RESOLVED: execution succeeded and verified cleanly, but the
  freshly re-read position is still AT_RISK. Not a failure of anything —
  just not resolved yet. Safe, and expected, to start a new round.

State machine (see RunState/_VALID_TRANSITIONS — every transition is
validated; an undefined one raises InvalidStateTransitionError rather than
silently happening):

    EVALUATING -> SIMULATING -> RECOVERING -> SIMULATING -> ...
                            \\-> READY_TO_EXECUTE -> EXECUTING -> VERIFYING -> RESOLVED
                                                            \\           \\-> FAILED
                                                             \\-> UNCERTAIN
    (all real candidates exhausted, or nothing to try) -> NO_SAFE_ACTION / RESOLVED

FAILED and RESOLVED-but-still-at-risk are both re-plannable by the caller
(aegis.pipeline.run_pipeline starts a new round — a fresh READ, fresh
candidates, from the new ground truth — never a retry of the same
transaction), bounded by a hard round limit. UNCERTAIN and NO_SAFE_ACTION
are not: they require operator attention.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum

from aegis.aave import AavePositionReader, AaveUserAccountData, build_protocol_action_params
from aegis.audit import AuditLogger
from aegis.config import Settings
from aegis.decision_engine import (
    CandidateAction,
    apply_final_status,
    build_explanation,
    build_hermes_candidate,
    candidate_to_intent,
    compute_combined_score,
    compute_execution_score,
    compute_financial_score,
    describe_execution_rejection,
    determine_simulation_status,
    generate_candidate_actions,
)
from aegis.execution import ExecutionService, SimulationService, VerificationService, VerificationTimeoutError
from aegis.hermes.runtime import HermesAgent
from aegis.intents import Decision
from aegis.keeperhub.models import ExecutionStatus, ProtocolActionSimulation
from aegis.policy import PolicyDecision, PolicyEngine
from aegis.risk import RiskAssessment, assess_health_factor


class RunState(str, Enum):
    EVALUATING = "EVALUATING"
    SIMULATING = "SIMULATING"
    READY_TO_EXECUTE = "READY_TO_EXECUTE"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    RECOVERING = "RECOVERING"
    RESOLVED = "RESOLVED"
    FAILED = "FAILED"
    NO_SAFE_ACTION = "NO_SAFE_ACTION"
    UNCERTAIN = "UNCERTAIN"


class RecoveryFailureCategory(str, Enum):
    """These must NOT all be treated as the same error — each has its own
    recovery response, documented in this module's docstring."""

    SIMULATION_FAILURE = "SIMULATION_FAILURE"
    POLICY_REJECTION = "POLICY_REJECTION"
    EXECUTION_FAILURE = "EXECUTION_FAILURE"
    EXECUTION_TIMEOUT = "EXECUTION_TIMEOUT"
    EXECUTION_UNCERTAIN = "EXECUTION_UNCERTAIN"
    VERIFICATION_FAILURE = "VERIFICATION_FAILURE"
    RISK_NOT_RESOLVED = "RISK_NOT_RESOLVED"


class InvalidStateTransitionError(Exception):
    """Raised when a recovery run attempts a transition not in
    _VALID_TRANSITIONS — a structural bug, not a modeled business outcome.
    Recovery must never silently pass through an undefined state change."""


class RunAlreadyCompletedError(Exception):
    """Raised when run_with_recovery is called again with a run_id that
    already reached a CONCLUSIVE terminal state (RESOLVED/FAILED/
    NO_SAFE_ACTION). A completed run_id must never be reused for a new
    decision cycle — a genuinely new cycle gets a fresh run_id (the
    default when none is supplied). UNCERTAIN is deliberately NOT one of
    these: it means "we don't know yet", not "we're done" — retrying that
    run_id must re-check status (see _find_unresolved_prior_execution),
    not raise, because a real accidental redelivery is exactly the case
    that must "check existing execution status first" rather than give up."""


# Conclusive answers — re-invoking a run_id that reached one of these is a
# caller bug, not a retry to honor.
_HARD_TERMINAL_STAGES = frozenset({s.value for s in (RunState.RESOLVED, RunState.FAILED, RunState.NO_SAFE_ACTION)})


# The complete, explicit transition graph. A state with no entry (or an
# empty set) is terminal for this run — the caller (aegis.pipeline) decides
# whether/how to start a brand new run from there, never this module
# continuing on its own past a terminal state.
_VALID_TRANSITIONS: dict[RunState | None, frozenset[RunState]] = {
    None: frozenset({RunState.EVALUATING}),
    RunState.EVALUATING: frozenset({RunState.SIMULATING, RunState.NO_SAFE_ACTION, RunState.RESOLVED}),
    RunState.SIMULATING: frozenset({RunState.RECOVERING, RunState.READY_TO_EXECUTE}),
    RunState.RECOVERING: frozenset({RunState.SIMULATING, RunState.NO_SAFE_ACTION, RunState.RESOLVED}),
    RunState.READY_TO_EXECUTE: frozenset({RunState.EXECUTING}),
    RunState.EXECUTING: frozenset({RunState.VERIFYING, RunState.UNCERTAIN}),
    RunState.VERIFYING: frozenset({RunState.RESOLVED, RunState.FAILED, RunState.UNCERTAIN}),
    RunState.FAILED: frozenset(),
    RunState.RESOLVED: frozenset(),
    RunState.NO_SAFE_ACTION: frozenset(),
    RunState.UNCERTAIN: frozenset(),
}


def _log(
    audit: AuditLogger,
    run_id: str,
    stage: str,
    *,
    state: RunState | None = None,
    candidate: CandidateAction | None = None,
    failure_category: RecoveryFailureCategory | None = None,
    failure_reason: str | None = None,
    execution_id: str | None = None,
    transaction_hash: str | None = None,
    risk_before: RiskAssessment | None = None,
    risk_after: RiskAssessment | None = None,
    recovery_decision: str | None = None,
    **extra: object,
) -> None:
    """Every recovery-relevant audit event — transitions and narrative
    events alike — goes through here (transition() below calls it too) so
    the minimum required field set (run_id/current state/candidate/
    failure category/failure reason/execution id/transaction hash/
    previous and updated risk/recovery decision/timestamp) is always
    present — None when not applicable, never simply missing. run_id is
    AuditLogger's own first positional argument and timestamp is stamped
    automatically by AuditEvent, so neither needs to be passed explicitly
    here.

    financial_score/execution_score/combined_score/hard_eligibility/
    simulation_status/execution_factors are derived automatically from
    `candidate` (when one is given) rather than requiring every call site
    to remember to pass them — Execution Confidence scoring data is part
    of every recovery event that has a candidate, not an opt-in extra."""
    audit.record(
        run_id,
        stage,
        state=state.value if state is not None else None,
        candidate=candidate.decision.value if candidate is not None else None,
        financial_score=str(candidate.financial_score) if candidate is not None else None,
        execution_score=str(candidate.execution_score) if candidate is not None else None,
        combined_score=(
            str(candidate.combined_score) if candidate is not None and candidate.combined_score is not None else None
        ),
        hard_eligibility=candidate.eligible if candidate is not None else None,
        simulation_status=candidate.simulation_status.value if candidate is not None else None,
        execution_factors=(
            candidate.execution_detail.model_dump(mode="json")
            if candidate is not None and candidate.execution_detail is not None
            else None
        ),
        failure_category=failure_category.value if failure_category is not None else None,
        failure_reason=failure_reason,
        execution_id=execution_id,
        transaction_hash=transaction_hash,
        risk_before=str(risk_before.health_factor) if risk_before is not None else None,
        risk_after=str(risk_after.health_factor) if risk_after is not None else None,
        recovery_decision=recovery_decision,
        **extra,
    )


def transition(
    audit: AuditLogger,
    run_id: str,
    current: RunState | None,
    new: RunState,
    *,
    stage: str,
    candidate: CandidateAction | None = None,
    failure_category: RecoveryFailureCategory | None = None,
    failure_reason: str | None = None,
    execution_id: str | None = None,
    transaction_hash: str | None = None,
    risk_before: RiskAssessment | None = None,
    risk_after: RiskAssessment | None = None,
    recovery_decision: str | None = None,
    **extra: object,
) -> RunState:
    """The only way this module changes RunState. Validates the change
    against _VALID_TRANSITIONS and raises InvalidStateTransitionError for
    anything not explicitly modeled, then records it via _log (so a
    transition-carrying event has exactly the same required-field schema
    as any other recovery event), plus from_state/to_state for the
    transition itself."""
    allowed = _VALID_TRANSITIONS.get(current, frozenset())
    if new not in allowed:
        raise InvalidStateTransitionError(
            f"illegal recovery state transition "
            f"{current.value if current else 'START'} -> {new.value} "
            f"(allowed from {current.value if current else 'START'}: "
            f"{sorted(a.value for a in allowed) or 'none — terminal state'})"
        )
    _log(
        audit, run_id, stage, state=new, candidate=candidate,
        failure_category=failure_category, failure_reason=failure_reason,
        execution_id=execution_id, transaction_hash=transaction_hash,
        risk_before=risk_before, risk_after=risk_after, recovery_decision=recovery_decision,
        from_state=current.value if current else None, to_state=new.value, **extra,
    )
    return new


@dataclass
class RecoveryRunResult:
    run_id: str
    final_state: RunState
    candidates: list[CandidateAction]
    recovery_attempts: list[CandidateAction] = field(default_factory=list)
    selected: CandidateAction | None = None
    policy_decision: PolicyDecision | None = None
    simulation: ProtocolActionSimulation | None = None
    verification: ExecutionStatus | None = None
    executed: bool = False
    failure_category: RecoveryFailureCategory | None = None
    stop_reason: str | None = None
    risk_before: RiskAssessment | None = None
    # The position as read at DETECTED, before anything else happened
    # this round — populated even when nothing gets executed (SAFE/
    # DO_NOTHING, or a round that never reaches execution), unlike
    # position_after below. aegis.api uses this so collateral/debt can
    # show a real, known "0" instead of "unknown" whenever a read
    # actually happened, regardless of whether anything executed.
    position_before: AaveUserAccountData | None = None
    position_after: AaveUserAccountData | None = None
    risk_after: RiskAssessment | None = None


def _find_unresolved_prior_execution(audit: AuditLogger, run_id: str) -> str | None:
    """Scan run_id's own audit history (newest first) for an EXECUTED
    event with no terminal event after it — i.e. a prior invocation of
    this function, under the same caller-supplied run_id, that reached
    EXECUTING but never reported back a resolved/failed/uncertain
    outcome (e.g. the caller's process died before that could happen).
    Returns the prior execution id if so, else None (including when the
    run_id is simply new)."""
    for event in reversed(audit.events_for(run_id)):
        if event.stage in _HARD_TERMINAL_STAGES:
            return None
        if event.stage == "EXECUTED":
            return event.detail.get("execution_id")
    return None


def _reject_if_already_terminal(audit: AuditLogger, run_id: str) -> None:
    if any(event.stage in _HARD_TERMINAL_STAGES for event in audit.events_for(run_id)):
        raise RunAlreadyCompletedError(
            f"run_id {run_id} already reached a terminal state; run_with_recovery must not be "
            "invoked again with the same run_id once resolved — a new decision cycle gets a new one"
        )


def _resume_from_prior_execution(
    audit: AuditLogger, run_id: str, execution_id: str, verification_service: VerificationService,
) -> RecoveryRunResult:
    """IDEMPOTENCY: this run_id already reached EXECUTING in a previous
    call — e.g. a caller retried after a crash between EXECUTE and a
    terminal outcome, deliberately reusing the same run_id. Never execute
    again: check what actually happened first, the same never-resend
    contract EXECUTION_TIMEOUT enforces mid-run (see
    VerificationService.check_status_once). Deliberately bypasses the
    normal transition()-validated state walk — this is an out-of-band
    guard entered before EVALUATING would otherwise begin, not a step
    within it."""
    _log(
        audit, run_id, "IDEMPOTENCY_GUARD", execution_id=execution_id,
        recovery_decision="This run already started executing before. Checking its status "
        "instead of running it again.",
    )
    checked = verification_service.check_status_once(execution_id)
    _log(
        audit, run_id, "STATUS_CHECKED", execution_id=execution_id,
        transaction_hash=checked.transactionHash, status=checked.status,
        recovery_decision="idempotency-guard status check",
    )

    if checked.terminal and checked.succeeded:
        _log(
            audit, run_id, "RESOLVED", execution_id=execution_id, transaction_hash=checked.transactionHash,
            recovery_decision="This already succeeded before. Not running it again.",
        )
        return RecoveryRunResult(
            run_id=run_id, final_state=RunState.RESOLVED, candidates=[], executed=True,
            verification=checked, stop_reason=None,
        )
    if checked.terminal and not checked.succeeded:
        _log(
            audit, run_id, "FAILED", execution_id=execution_id,
            failure_category=RecoveryFailureCategory.EXECUTION_FAILURE,
            recovery_decision="This failed before. It is safe to try a new plan.",
        )
        return RecoveryRunResult(
            run_id=run_id, final_state=RunState.FAILED, candidates=[], executed=True,
            verification=checked, failure_category=RecoveryFailureCategory.EXECUTION_FAILURE,
            stop_reason="prior execution (from an earlier invocation of this run_id) confirmed failed",
        )
    _log(
        audit, run_id, "UNCERTAIN", execution_id=execution_id,
        failure_category=RecoveryFailureCategory.EXECUTION_UNCERTAIN,
        recovery_decision="Still not finished. Stopping here and will not run it again.",
    )
    return RecoveryRunResult(
        run_id=run_id, final_state=RunState.UNCERTAIN, candidates=[], executed=True,
        failure_category=RecoveryFailureCategory.EXECUTION_UNCERTAIN,
        stop_reason="prior execution (from an earlier invocation of this run_id) status still unresolved",
    )


def _consult_hermes(
    hermes_agent: HermesAgent,
    position: AaveUserAccountData,
    risk: RiskAssessment,
    *,
    network: str,
    user: str,
    debt_asset: str,
    collateral_asset: str,
    audit: AuditLogger,
    run_id: str,
) -> CandidateAction | None:
    """Hermes is an optional enhancement to candidate generation, never a
    dependency of it — a flaky LLM call, an unreachable KeeperHub MCP
    session, or a malformed response must never break, delay past its own
    call, or block the deterministic path that runs regardless. Any
    failure here is caught, recorded, and treated as "Hermes had nothing
    to add this round" — generate_candidate_actions' own deterministic
    candidates are unaffected either way."""
    position_summary: dict[str, object] = {
        "network": network,
        "user": user,
        "protocol": "aave-v3",
        "healthFactor": position.healthFactor,
        "totalCollateralBase": position.totalCollateralBase,
        "totalDebtBase": position.totalDebtBase,
        "availableBorrowsBase": position.availableBorrowsBase,
        "currentLiquidationThreshold": position.currentLiquidationThreshold,
        "ltv": position.ltv,
        "riskLevel": risk.level.value,
        "healthFactorThreshold": str(risk.threshold),
        "debtAsset": debt_asset,
        "collateralAsset": collateral_asset,
    }
    try:
        intent = hermes_agent.decide(position_summary)
    except Exception as exc:  # noqa: BLE001 - see docstring; covers HermesDidNotDecideError too
        _log(
            audit, run_id, "HERMES_CONSULTED",
            recovery_decision=f"Hermes unavailable or did not decide: {type(exc).__name__}: {exc}",
        )
        return None

    candidate = build_hermes_candidate(
        intent, position, risk, network=network, user=user,
        debt_asset=debt_asset, collateral_asset=collateral_asset,
    )
    _log(
        audit, run_id, "HERMES_CONSULTED", candidate=candidate,
        recovery_decision=(
            f"Hermes proposed {intent.decision.value}"
            + (f" (added as a candidate, amount={candidate.amount})" if candidate is not None else " (no candidate added)")
        ),
    )
    return candidate


def run_with_recovery(
    *,
    settings: Settings,
    position_reader: AavePositionReader,
    policy_engine: PolicyEngine,
    simulation_service: SimulationService,
    execution_service: ExecutionService,
    verification_service: VerificationService,
    audit: AuditLogger,
    network: str,
    user: str,
    debt_asset: str,
    collateral_asset: str,
    available_balance: Decimal | None = None,
    run_id: str | None = None,
    hermes_agent: HermesAgent | None = None,
) -> RecoveryRunResult:
    run_id = run_id or str(uuid.uuid4())

    # IDEMPOTENCY GUARD — only relevant when a caller supplies its own
    # run_id (e.g. to make a retriable trigger safe); a freshly generated
    # run_id can never have prior history. See _resume_from_prior_execution.
    _reject_if_already_terminal(audit, run_id)
    prior_execution_id = _find_unresolved_prior_execution(audit, run_id)
    if prior_execution_id is not None:
        return _resume_from_prior_execution(audit, run_id, prior_execution_id, verification_service)

    # DETECTED — a fresh read. Recovery must never reuse stale Aave
    # position data; every run (including every re-planning round) starts
    # here, from the real current state. The event carries everything a
    # position snapshot needs (collateral/debt/health factor/protocol/
    # chain/timestamp) — protocol and chain are recorded explicitly here;
    # timestamp is AuditEvent's own automatic field.
    position = position_reader.get_account_data(network=network, user=user)
    _log(
        audit, run_id, "DETECTED", network=network, user=user, protocol="aave-v3", chain=network,
        healthFactor=position.healthFactor, totalCollateralBase=position.totalCollateralBase,
        totalDebtBase=position.totalDebtBase,
    )

    # ANALYZED
    risk = assess_health_factor(position.healthFactor, settings.aegis_health_factor_threshold)
    _log(
        audit, run_id, "ANALYZED",
        health_factor=str(risk.health_factor), threshold=str(risk.threshold), level=risk.level.value,
        risk_before=risk,
    )

    state: RunState | None = transition(audit, run_id, None, RunState.EVALUATING, stage="EVALUATING")

    # Generate every candidate, score financial + feasibility. Every
    # candidate — the preferred one and every fallback — goes through this
    # exact same pipeline; nothing about recovery gets a shortcut.
    candidates = generate_candidate_actions(
        position, risk, network=network, user=user, debt_asset=debt_asset, collateral_asset=collateral_asset,
    )
    if hermes_agent is not None:
        hermes_candidate = _consult_hermes(
            hermes_agent, position, risk, network=network, user=user,
            debt_asset=debt_asset, collateral_asset=collateral_asset, audit=audit, run_id=run_id,
        )
        if hermes_candidate is not None:
            candidates.append(hermes_candidate)
    recovery_attempts: list[CandidateAction] = []
    for candidate in candidates:
        financial = compute_financial_score(candidate, position)
        candidate.financial_detail = financial
        candidate.financial_score = financial.value

        policy_decision = policy_engine.evaluate(candidate_to_intent(candidate))
        execution = compute_execution_score(candidate, policy_decision, available_balance, None)
        candidate.execution_detail = execution
        candidate.execution_score = execution.value
        reason = describe_execution_rejection(execution, policy_decision)
        if reason is not None:
            candidate.rejection_reason = reason
            _log(
                audit, run_id, "REJECTED", state=state, candidate=candidate,
                failure_reason=reason, risk_before=risk,
                recovery_decision="pre-filtered before ever being attempted",
            )
            if candidate.decision is not Decision.DO_NOTHING:
                recovery_attempts.append(candidate)

        combined = compute_combined_score(financial, execution)
        candidate.combined_detail = combined
        candidate.combined_score = combined.value

    # Preferred order: highest financial score first, among candidates
    # that survived the feasibility pre-check. DO_NOTHING is never
    # attempted here — it's the guaranteed fallback if every real
    # candidate fails.
    attempt_queue = sorted(
        (c for c in candidates if c.decision is not Decision.DO_NOTHING and c.eligible),
        key=lambda c: c.financial_score,
        reverse=True,
    )
    do_nothing = next(c for c in candidates if c.decision is Decision.DO_NOTHING)

    if not attempt_queue:
        # Nothing was even worth attempting (position safe, or every real
        # candidate was pre-filtered before a single simulation).
        apply_final_status(candidates, do_nothing)
        final_state = RunState.NO_SAFE_ACTION if (risk.at_risk and recovery_attempts) else RunState.RESOLVED
        state = transition(
            audit, run_id, state, final_state, stage=final_state.value, candidate=do_nothing,
            selection_reason=build_explanation(do_nothing, candidates).selection_reason,
        )
        stop_reason = (
            "no safe action available — all candidates failed simulation or policy"
            if final_state is RunState.NO_SAFE_ACTION
            else "decision is DO_NOTHING"
        )
        return RecoveryRunResult(
            run_id=run_id, final_state=final_state, candidates=candidates,
            recovery_attempts=recovery_attempts, selected=do_nothing, executed=False,
            stop_reason=stop_reason, risk_before=risk, position_before=position,
        )

    selected: CandidateAction | None = None
    final_policy_decision: PolicyDecision | None = None
    first_attempt = True

    for candidate in attempt_queue:
        # SIMULATING — never a retry of the same candidate; each iteration
        # is a different candidate drawn from the queue built above.
        state = transition(
            audit, run_id, state, RunState.SIMULATING,
            stage="CANDIDATE_SELECTED" if first_attempt else "ALTERNATIVE_SELECTED",
            candidate=candidate,
        )
        first_attempt = False

        params = build_protocol_action_params(candidate_to_intent(candidate))
        simulation = simulation_service.simulate(candidate.protocol_action, params)
        candidate.simulation_result = simulation
        candidate.simulation_status = determine_simulation_status(candidate, simulation)
        _log(
            audit, run_id, "SIMULATED", state=state, candidate=candidate,
            success=simulation.success, wouldRevert=simulation.wouldRevert,
        )

        policy_decision = policy_engine.evaluate(candidate_to_intent(candidate))
        execution = compute_execution_score(candidate, policy_decision, available_balance, simulation)
        candidate.execution_detail = execution
        candidate.execution_score = execution.value
        reason = describe_execution_rejection(execution, policy_decision)
        if reason is not None:
            candidate.rejection_reason = reason

        if candidate.financial_detail is None:
            raise RuntimeError("candidate is missing financial_detail set during EVALUATING")
        combined = compute_combined_score(candidate.financial_detail, execution)
        candidate.combined_detail = combined
        candidate.combined_score = combined.value

        if not candidate.eligible:
            # 1. record the failure  2. mark the candidate unavailable
            category = RecoveryFailureCategory.SIMULATION_FAILURE
            _log(
                audit, run_id, "SIMULATION_FAILED", state=state, candidate=candidate,
                failure_category=category, failure_reason=candidate.rejection_reason, risk_before=risk,
                recovery_decision="remove candidate for this cycle; recalculate remaining options",
            )
            recovery_attempts.append(candidate)
            # 3. reassess remaining actions — never an identical retry of
            # the candidate that just failed.
            remaining = [c.decision.value for c in attempt_queue if c is not candidate and c not in recovery_attempts]
            state = transition(
                audit, run_id, state, RunState.RECOVERING, stage="RECOVERY_STARTED",
                candidate=candidate, failure_category=category, remaining_candidates=remaining,
            )
            continue

        # Simulation passed. Final POLICY CHECK — the same authoritative,
        # defense-in-depth gate every candidate must clear, preferred or
        # fallback alike. Never bypassed.
        intent = candidate_to_intent(candidate)
        policy_decision = policy_engine.evaluate(intent)
        _log(
            audit, run_id, "POLICY_CHECK", state=state, candidate=candidate,
            recovery_decision="approved" if policy_decision.approved else "rejected",
            violated_rules=policy_decision.violated_rules,
        )
        if not policy_decision.approved:
            candidate.rejection_reason = "policy: " + "; ".join(policy_decision.violated_rules)
            category = RecoveryFailureCategory.POLICY_REJECTION
            _log(
                audit, run_id, "POLICY_REJECTED", state=state, candidate=candidate,
                failure_category=category, failure_reason=candidate.rejection_reason, risk_before=risk,
                recovery_decision="remove candidate for this cycle; recalculate remaining options",
            )
            recovery_attempts.append(candidate)
            remaining = [c.decision.value for c in attempt_queue if c is not candidate and c not in recovery_attempts]
            state = transition(
                audit, run_id, state, RunState.RECOVERING, stage="RECOVERY_STARTED",
                candidate=candidate, failure_category=category, remaining_candidates=remaining,
            )
            continue

        # 6. select it if valid
        selected = candidate
        final_policy_decision = policy_decision
        apply_final_status(candidates, selected)
        state = transition(
            audit, run_id, state, RunState.READY_TO_EXECUTE, stage="READY_TO_EXECUTE",
            candidate=candidate, recovery_decision=f"selected (combined_score={candidate.combined_score})",
            selection_reason=build_explanation(selected, candidates).selection_reason,
        )
        break

    if selected is None:
        # Every real candidate was rejected or failed simulation/policy —
        # DO_NOTHING is the only remaining safe option.
        apply_final_status(candidates, do_nothing)
        final_state = RunState.NO_SAFE_ACTION if (risk.at_risk and recovery_attempts) else RunState.RESOLVED
        state = transition(
            audit, run_id, state, final_state, stage=final_state.value, candidate=do_nothing, risk_before=risk,
            selection_reason=build_explanation(do_nothing, candidates).selection_reason,
        )
        stop_reason = (
            "no safe action available — all candidates failed simulation or policy"
            if final_state is RunState.NO_SAFE_ACTION
            else "decision is DO_NOTHING"
        )
        return RecoveryRunResult(
            run_id=run_id, final_state=final_state, candidates=candidates,
            recovery_attempts=recovery_attempts, selected=do_nothing, policy_decision=None,
            simulation=None, verification=None, executed=False,
            stop_reason=stop_reason, risk_before=risk, position_before=position,
        )

    if not settings.aegis_autonomous_execution_enabled:
        _log(
            audit, run_id, "STOPPED", state=state, candidate=selected,
            recovery_decision="autonomous execution disabled",
        )
        return RecoveryRunResult(
            run_id=run_id, final_state=RunState.READY_TO_EXECUTE, candidates=candidates,
            recovery_attempts=recovery_attempts, selected=selected, policy_decision=final_policy_decision,
            simulation=selected.simulation_result, verification=None, executed=False,
            stop_reason="autonomous execution disabled", risk_before=risk, position_before=position,
        )

    intent = candidate_to_intent(selected)
    simulation = selected.simulation_result
    if simulation is None:
        raise RuntimeError(
            "selected candidate has no passing simulation result — this indicates a bug in "
            "the recovery loop, which must never reach READY_TO_EXECUTE without one"
        )

    # 7-8. continue through execution, but only after policy approved.
    # EXECUTING — the point of no easy return: once this is called, an
    # uncertain outcome is never "try something else."
    state = transition(audit, run_id, state, RunState.EXECUTING, stage="EXECUTING", candidate=selected)
    try:
        execution = execution_service.execute(
            selected.protocol_action, build_protocol_action_params(intent), simulation
        )
    except Exception as exc:  # noqa: BLE001 - immediately re-classified as EXECUTION_UNCERTAIN below
        # No execution id was ever obtained, so there is nothing to check
        # status on — this is the textbook "uncertain transaction" case:
        # never guess, never retry, stop.
        category = RecoveryFailureCategory.EXECUTION_UNCERTAIN
        _log(
            audit, run_id, "EXECUTE_ERROR", state=state, candidate=selected,
            failure_category=category, failure_reason=str(exc),
            recovery_decision="No execution ID was returned, so status cannot be checked. Stopping. Will not send it again.",
        )
        state = transition(
            audit, run_id, state, RunState.UNCERTAIN, stage="UNCERTAIN", candidate=selected,
            failure_category=category, failure_reason=str(exc), risk_before=risk,
        )
        return RecoveryRunResult(
            run_id=run_id, final_state=RunState.UNCERTAIN, candidates=candidates,
            recovery_attempts=recovery_attempts, selected=selected, policy_decision=final_policy_decision,
            simulation=simulation, verification=None, executed=False, failure_category=category,
            stop_reason=f"execution uncertain: {exc}", risk_before=risk, position_before=position,
        )

    _log(
        audit, run_id, "EXECUTED", state=state, candidate=selected,
        execution_id=execution.executionId, status=execution.status,
    )

    # VERIFYING — poll for the real, on-chain-reconciled outcome.
    state = transition(
        audit, run_id, state, RunState.VERIFYING, stage="VERIFYING",
        candidate=selected, execution_id=execution.executionId,
    )
    try:
        verification = verification_service.verify(execution.executionId)
    except VerificationTimeoutError as exc:
        # EXECUTION_TIMEOUT — an unresolved timeout is not proof of
        # failure. Check KeeperHub's status exactly once more (never
        # resend) before deciding anything.
        category = RecoveryFailureCategory.EXECUTION_TIMEOUT
        _log(
            audit, run_id, "VERIFY_TIMEOUT", state=state, candidate=selected,
            failure_category=category, failure_reason=str(exc), execution_id=execution.executionId,
        )
        checked = verification_service.check_status_once(execution.executionId)
        _log(
            audit, run_id, "STATUS_CHECKED", state=state, candidate=selected,
            failure_category=category, execution_id=execution.executionId,
            transaction_hash=checked.transactionHash, status=checked.status,
            recovery_decision="post-timeout status check via KeeperHub",
        )

        if checked.terminal and checked.succeeded:
            # Confirmed after all — the extra check caught a race between
            # our poll budget and the chain. Proceed as a normal success.
            verification = checked
            _log(
                audit, run_id, "VERIFIED", state=state, candidate=selected,
                execution_id=execution.executionId, transaction_hash=checked.transactionHash,
                recovery_decision="confirmed succeeded on post-timeout check",
            )
        elif checked.terminal and not checked.succeeded:
            # Confirmed failed via KeeperHub's own status — known, not
            # guessed. Safe for another planning cycle.
            state = transition(
                audit, run_id, state, RunState.FAILED, stage="FAILED", candidate=selected,
                failure_category=category, execution_id=execution.executionId,
                transaction_hash=checked.transactionHash, risk_before=risk,
                recovery_decision="Confirmed failed after a timeout. It is safe to try a new plan.",
            )
            return RecoveryRunResult(
                run_id=run_id, final_state=RunState.FAILED, candidates=candidates,
                recovery_attempts=recovery_attempts, selected=selected, policy_decision=final_policy_decision,
                simulation=simulation, verification=checked, executed=True, failure_category=category,
                stop_reason=f"execution timed out and was confirmed failed: {exc}",
                risk_before=risk, position_before=position,
            )
        else:
            # Still pending/unconfirmed/unknown — this is the honest
            # "uncertain" outcome. Never resend, never assume either way.
            state = transition(
                audit, run_id, state, RunState.UNCERTAIN, stage="UNCERTAIN", candidate=selected,
                failure_category=category, execution_id=execution.executionId, risk_before=risk,
                recovery_decision="Still not finished after a timeout. Stopping. Will not send it again.",
            )
            return RecoveryRunResult(
                run_id=run_id, final_state=RunState.UNCERTAIN, candidates=candidates,
                recovery_attempts=recovery_attempts, selected=selected, policy_decision=final_policy_decision,
                simulation=simulation, verification=None, executed=True, failure_category=category,
                stop_reason=f"execution status still unresolved after timeout: {exc}",
                risk_before=risk, position_before=position,
            )
    except Exception as exc:  # noqa: BLE001 - VERIFICATION_FAILURE: verify() itself broke
        # Not a timeout, not a clean terminal result — verify() errored.
        # Never assume; inspect the real on-chain position and decide
        # from what actually changed.
        category = RecoveryFailureCategory.VERIFICATION_FAILURE
        _log(
            audit, run_id, "VERIFICATION_ERROR", state=state, candidate=selected,
            failure_category=category, failure_reason=str(exc), execution_id=execution.executionId,
        )
        position_after = position_reader.get_account_data(network=network, user=user)
        risk_after = assess_health_factor(position_after.healthFactor, settings.aegis_health_factor_threshold)
        position_changed = risk_after.health_factor != risk.health_factor
        _log(
            audit, run_id, "ONCHAIN_STATE_INSPECTED", state=state, candidate=selected,
            failure_category=category, risk_before=risk, risk_after=risk_after,
            recovery_decision=(
                "position changed — safe for another planning cycle"
                if position_changed else "position unchanged — remains uncertain"
            ),
        )
        if position_changed:
            state = transition(
                audit, run_id, state, RunState.FAILED, stage="FAILED", candidate=selected,
                failure_category=category, risk_before=risk, risk_after=risk_after,
            )
            return RecoveryRunResult(
                run_id=run_id, final_state=RunState.FAILED, candidates=candidates,
                recovery_attempts=recovery_attempts, selected=selected, policy_decision=final_policy_decision,
                simulation=simulation, verification=None, executed=True, failure_category=category,
                stop_reason=f"verification failed unexpectedly, but the position changed: {exc}",
                risk_before=risk, position_before=position, position_after=position_after, risk_after=risk_after,
            )
        state = transition(
            audit, run_id, state, RunState.UNCERTAIN, stage="UNCERTAIN", candidate=selected,
            failure_category=category, risk_before=risk, risk_after=risk_after,
        )
        return RecoveryRunResult(
            run_id=run_id, final_state=RunState.UNCERTAIN, candidates=candidates,
            recovery_attempts=recovery_attempts, selected=selected, policy_decision=final_policy_decision,
            simulation=simulation, verification=None, executed=True, failure_category=category,
            stop_reason=f"verification failed unexpectedly and the position is unchanged: {exc}",
            risk_before=risk, position_before=position, position_after=position_after, risk_after=risk_after,
        )
    else:
        _log(
            audit, run_id, "VERIFIED", state=state, candidate=selected,
            execution_id=execution.executionId, transaction_hash=verification.transactionHash,
            status=verification.status, succeeded=verification.succeeded,
        )

    if not verification.succeeded:
        # EXECUTION_FAILURE — confirmed via KeeperHub's own status
        # endpoint (verify() only returns on a terminal result). Known,
        # not guessed. Safe for another planning cycle.
        category = RecoveryFailureCategory.EXECUTION_FAILURE
        state = transition(
            audit, run_id, state, RunState.FAILED, stage="FAILED", candidate=selected,
            failure_category=category,
            failure_reason="execution reached a terminal status but did not succeed",
            execution_id=execution.executionId, transaction_hash=verification.transactionHash,
            risk_before=risk, recovery_decision="KeeperHub confirms this failed. It is safe to try a new plan.",
        )
        return RecoveryRunResult(
            run_id=run_id, final_state=RunState.FAILED, candidates=candidates,
            recovery_attempts=recovery_attempts, selected=selected, policy_decision=final_policy_decision,
            simulation=simulation, verification=verification, executed=True, failure_category=category,
            stop_reason="execution failed on-chain (confirmed via KeeperHub status)",
            risk_before=risk, position_before=position,
        )

    # 9-10. Verify the resulting position and determine whether the
    # incident is resolved. Ground truth, freshly read, never fabricated.
    position_after = position_reader.get_account_data(network=network, user=user)
    risk_after = assess_health_factor(position_after.healthFactor, settings.aegis_health_factor_threshold)
    resolved_category = RecoveryFailureCategory.RISK_NOT_RESOLVED if risk_after.at_risk else None
    _log(
        audit, run_id, "REASSESS_RISK", state=state, candidate=selected,
        risk_before=risk, risk_after=risk_after, failure_category=resolved_category,
        recovery_decision="resolved" if resolved_category is None else "Risk is still not fixed. May need another try.",
    )

    state = transition(
        audit, run_id, state, RunState.RESOLVED, stage="RESOLVED", candidate=selected,
        failure_category=resolved_category, risk_before=risk, risk_after=risk_after,
    )
    return RecoveryRunResult(
        run_id=run_id, final_state=RunState.RESOLVED, candidates=candidates,
        recovery_attempts=recovery_attempts, selected=selected, policy_decision=final_policy_decision,
        simulation=simulation, verification=verification, executed=True, failure_category=resolved_category,
        stop_reason=None, risk_before=risk, position_before=position, position_after=position_after,
        risk_after=risk_after,
    )
