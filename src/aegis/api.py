"""Read-only HTTP API for the Aegis hackathon dashboard.

This is a visualization backend, not a second execution path — every run
this module can ever start goes through the one authoritative
aegis.demo_orchestrator.start_run, exactly like aegis.cli does. Two modes
are reachable from HTTP:

  - FIXTURE — MagicMock KeeperHub responses, never touches a network
    (see aegis.demo_orchestrator.build_fixture_components).
  - LIVE_DRY_RUN — real KeeperHub REST calls, but dry_run=True is
    structural (aegis.pipeline.run_pipeline never reaches EXECUTE under
    it), regardless of Settings.aegis_autonomous_execution_enabled.

LIVE_EXECUTION is deliberately NOT reachable from this module. There is no
endpoint here that accepts "please execute a real transaction" as input —
that mode is CLI-only (`aegis live-demo --confirm`, see aegis.cli), gated
by aegis.preflight and an explicit --confirm flag entered at a terminal by
whoever is authorizing it. This is a deliberate scope boundary, not an
oversight: CORS here is restricted to Settings.aegis_allowed_frontend_origins
(local dev only by default) precisely because a POST-to-execute endpoint
would let any page in any allowed origin trigger a real Base Sepolia
transaction through an authenticated session — even LIVE_DRY_RUN alone
makes real KeeperHub REST calls against the configured org API key, so
this allowlist must be set to the real deployed frontend origin(s) before
any non-local deployment; never '*'. `GET /api/runs/{run_id}` can still show a
CLI-started LIVE_EXECUTION run's progress and result — it reads the same
shared audit_log_path aegis.demo_orchestrator.start_run writes to,
in-process or not — but nothing here can ever start one.

No KeeperHub credential, private key, or Authorization header is ever
placed in a response body — only aegis.audit.AuditEvent detail dicts are
serialized, and those are already secret-free by construction (see
aegis.recovery/aegis.hermes.mcp_gateway's own "never log secrets"
invariants, tested elsewhere in this project).
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator

from aegis.audit import AuditEvent, load_events_for_run
from aegis.config import get_settings
from aegis.decision_engine import CandidateAction, build_explanation
from aegis.demo_orchestrator import DemoMode, DemoOrchestrationError, RunHandle, get_run, start_run
from aegis.recovery import RecoveryRunResult, RunState
from aegis.risk import RiskAssessment, is_no_debt_health_factor


def _cors_allowed_origins() -> list[str]:
    """Read straight from the environment, not via get_settings() —
    CORS middleware is configured once at module import time, before any
    request, and must not require the full Settings() object (which
    requires a real KEEPERHUB_API_KEY) just to serve /api/health or a
    FIXTURE-mode run. Local dev only (http://localhost:3000) by default;
    set AEGIS_ALLOWED_FRONTEND_ORIGINS (comma-separated) to the real
    deployed frontend origin(s) before any non-local deployment — never
    '*', since even LIVE_DRY_RUN alone makes real KeeperHub REST calls
    against the configured org API key."""
    raw = os.environ.get("AEGIS_ALLOWED_FRONTEND_ORIGINS")
    if not raw:
        return ["http://localhost:3000"]
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


app = FastAPI(title="Aegis Dashboard API", version="2.0.0")

# GET+POST only: every POST this API accepts (/api/runs) can only ever
# start a FIXTURE or LIVE_DRY_RUN run — see this module's docstring for
# why LIVE_EXECUTION is excluded structurally, not just by convention.
app.add_middleware(
    CORSMiddleware, allow_origins=_cors_allowed_origins(), allow_methods=["GET", "POST"], allow_headers=["*"],
)


# --- presentational risk tiers -----------------------------------------
#
# aegis.risk.RiskLevel is, and stays, a binary SAFE/AT_RISK classification
# — that is the authoritative signal every real decision in this project
# is made from, and this module never touches it. The dashboard wants a
# 5-tier *display* gradient (NO_POSITION/SAFE/AT_RISK/HIGH/CRITICAL); this
# is a purely presentational derivation from health_factor/threshold,
# computed here only, never fed back into any decision. NO_POSITION means
# no read has happened yet — distinct from SAFE, which means a real read
# came back healthy (including a genuine zero-collateral/zero-debt read).

def _display_risk_tier(risk: RiskAssessment | None) -> str:
    if risk is None:
        return "NO_POSITION"
    if risk.no_debt or risk.health_factor >= risk.threshold:
        return "SAFE"
    if risk.health_factor >= risk.threshold * Decimal("0.8"):
        return "AT_RISK"
    if risk.health_factor >= risk.threshold * Decimal("0.6"):
        return "HIGH"
    return "CRITICAL"


# --- system / incident / run state: what the four rows in the header mean
#
# Every one of these is computed here, server-side, from data the backend
# already has (audit stage, RunState, RiskAssessment.at_risk/no_debt) —
# the frontend only ever displays these strings, it never re-derives them
# from raw numbers. See aegis.recovery.RunState for the underlying
# state machine these are a coarser, presentation-facing view of.

# SYSTEM STATUS: what Aegis is doing right now, independent of outcome.
_INTERVENING_STAGES = frozenset({"EXECUTING", "EXECUTED"})
_VERIFYING_STAGES = frozenset({"VERIFYING", "VERIFIED", "STATUS_CHECKED", "REASSESS_RISK", "ONCHAIN_STATE_INSPECTED"})


def _system_status(stage: str | None, running: bool) -> str:
    if not running:
        # A finished run returns to "watching" — the same idle state as
        # before it started. The exact outcome is what incident_state/
        # run_state are for, not this row.
        return "MONITORING"
    if stage in _INTERVENING_STAGES:
        return "INTERVENING"
    if stage in _VERIFYING_STAGES:
        return "VERIFYING"
    return "ANALYZING"


# INCIDENT STATE: is there (or was there) a real risk incident, and what
# happened to it. This is the fix for the "SAFE position shown as
# RESOLVED" bug — RunState.RESOLVED is also what a round that never had
# anything to fix (DO_NOTHING, position already safe) ends in, and those
# are not the same thing to a user.
def _incident_state(
    running: bool, risk_before: RiskAssessment | None, final_state: RunState | None,
) -> str:
    if risk_before is None:
        return "NO_ACTIVE_INCIDENT"
    if not risk_before.at_risk:
        # The position was safe (or debt-free) the moment it was read —
        # DO_NOTHING being selected afterward is correct, not a resolved
        # incident, because there was never an incident.
        return "NO_ACTIVE_INCIDENT"
    if running:
        return "ACTIVE"
    if final_state is None:
        return "UNCERTAIN"
    if final_state is RunState.RESOLVED:
        return "RESOLVED"
    if final_state is RunState.FAILED:
        return "FAILED"
    if final_state is RunState.UNCERTAIN:
        return "UNCERTAIN"
    if final_state is RunState.NO_SAFE_ACTION:
        return "FAILED"
    # READY_TO_EXECUTE (dry-run, or autonomous execution disabled) and any
    # in-progress recovery state: there is a real, identified incident,
    # just not (yet, or ever, in dry-run) acted on.
    return "ACTIVE"


# RUN STATE: what kind of run this was and whether it actually broadcast
# anything — never conflates a dry run with a real one, and a run that
# never got to execute (SAFE position, or gated) is STOPPED, not FAILED.
def _run_state(
    running: bool, error: str | None, mode: DemoMode, executed_for_real: bool, final_state: RunState | None,
) -> str:
    if running:
        return "RUNNING"
    if error is not None:
        return "FAILED"
    if mode in (DemoMode.FIXTURE, DemoMode.LIVE_DRY_RUN):
        # Neither mode can ever broadcast a real transaction — dry_run is
        # structural for LIVE_DRY_RUN, and FIXTURE's execution_service is
        # a bare MagicMock never wired to a real client either way.
        return "DRY_RUN_COMPLETE"
    if executed_for_real:
        return "EXECUTION_COMPLETE"
    if final_state in (RunState.FAILED, RunState.UNCERTAIN):
        return "FAILED"
    return "STOPPED"


# --- response schema -----------------------------------------------------


class DashboardCandidate(BaseModel):
    action: str
    asset: str | None
    amount: str | None
    financial_score: str
    execution_score: str
    combined_score: str | None
    eligible: bool
    final_status: str | None
    simulation_status: str
    rejection_reason: str | None


class DashboardExplanation(BaseModel):
    selected_action: str
    financial_score: str
    execution_score: str
    combined_score: str | None
    expected_risk_reduction: str
    rejected_candidates: list[str]
    rejection_reasons: dict[str, str]
    selection_reason: str


class DashboardExecution(BaseModel):
    simulation_status: str  # NOT_APPLICABLE | SKIPPED | PASSED | FAILED
    would_revert: bool | None
    gas_estimate: str | None
    policy_approved: bool | None
    execution_status: str  # not_attempted | pending | completed | failed | uncertain
    execution_id: str | None
    transaction_hash: str | None
    explorer_url: str | None
    executed_through: Literal["KeeperHub"] = "KeeperHub"


class DashboardVerification(BaseModel):
    before_health_factor: str | None
    before_no_debt: bool
    before_risk: str | None
    after_health_factor: str | None
    after_no_debt: bool | None
    after_risk: str | None
    risk_reduced: bool | None
    incident_resolved: bool


class DashboardAuditEvent(BaseModel):
    run_id: str
    timestamp: str
    stage: str
    detail: dict


class DashboardRecoveryStep(BaseModel):
    action: str
    amount: str | None
    simulation_status: str
    outcome: str  # "rejected" | "selected"
    reason: str | None


class DashboardState(BaseModel):
    mode: Literal["fixture", "live_dry_run", "live_execution"]
    run_id: str | None
    running: bool
    stage: str | None
    generated_at: str
    status: str
    system_status: str
    incident_state: str
    run_state: str
    network: str
    chain_id: int
    wallet: str | None
    # Where `wallet` came from — "connected" (a browser-connected USER
    # WALLET), "dev_default" (Settings.aegis_expected_wallet_address, used
    # because no wallet was connected), or "fixture" (FIXTURE mode's fixed
    # demo wallet, unrelated to any real wallet). The frontend uses this
    # to label whether it's showing the visitor's own position or dev/
    # demo data — never inferred from the address string itself.
    wallet_source: Literal["connected", "dev_default", "fixture"]
    protocol: str
    health_factor: str | None
    no_debt: bool
    risk_level: str | None
    risk_tier: str | None
    position: dict
    candidates: list[DashboardCandidate]
    explanation: DashboardExplanation | None
    execution: DashboardExecution
    verification: DashboardVerification
    audit_timeline: list[DashboardAuditEvent]
    recovery_steps: list[DashboardRecoveryStep]
    rounds: int
    error: str | None = None


_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


class UserWallet(BaseModel):
    """The USER WALLET: whichever address a browser wallet extension
    reported via eth_requestAccounts (see frontend/lib/wallet.ts) — public
    address only, read-only, and only ever used here to pick which
    position to monitor. This is never an execution identity: it carries
    no signature, no session, no delegation of any kind, and does not by
    itself authorize anything. Whether Aegis can ever act on this
    wallet's behalf is decided entirely by PolicyEngine's own wallet-pin
    check (Settings.aegis_expected_wallet_address) against the KeeperHub-
    authorized execution wallet — a separate, server-side concern this
    model has no bearing on.
    """

    address: str
    chain: str
    connected: bool

    @field_validator("address")
    @classmethod
    def _validate_address_shape(cls, value: str) -> str:
        if not _ADDRESS_RE.match(value):
            raise ValueError("address must be a 0x-prefixed 40-hex-character EVM address")
        return value


class StartRunRequest(BaseModel):
    mode: Literal["fixture", "live_dry_run"]
    # Optional: the connected USER WALLET to monitor. Omitted (or
    # connected=False) falls back to the server's dev-default wallet —
    # see aegis.demo_orchestrator.start_run's wallet_source resolution.
    # FIXTURE mode ignores this entirely (see start_run's docstring).
    wallet: UserWallet | None = None


class StartRunResponse(BaseModel):
    run_id: str
    mode: Literal["fixture", "live_dry_run"]


# --- adapter: existing Aegis dataclasses -> DashboardState ---------------


def _candidate_to_dashboard(c: CandidateAction) -> DashboardCandidate:
    return DashboardCandidate(
        action=c.decision.value, asset=c.asset, amount=c.amount,
        financial_score=str(c.financial_score), execution_score=str(c.execution_score),
        combined_score=str(c.combined_score) if c.combined_score is not None else None,
        eligible=c.eligible, final_status=c.final_status.value if c.final_status else None,
        simulation_status=c.simulation_status.value, rejection_reason=c.rejection_reason,
    )


def _empty_execution_panel() -> DashboardExecution:
    return DashboardExecution(
        simulation_status="NOT_APPLICABLE", would_revert=None, gas_estimate=None,
        policy_approved=None, execution_status="not_attempted",
        execution_id=None, transaction_hash=None, explorer_url=None,
    )


def _execution_panel(round_result: RecoveryRunResult | None) -> DashboardExecution:
    if round_result is None or round_result.selected is None:
        return _empty_execution_panel()
    selected = round_result.selected
    sim = selected.simulation_result
    if round_result.final_state is RunState.RESOLVED and round_result.verification is not None:
        execution_status = "completed" if round_result.verification.succeeded else "failed"
    elif round_result.final_state is RunState.FAILED:
        execution_status = "failed"
    elif round_result.final_state is RunState.UNCERTAIN:
        execution_status = "uncertain"
    elif round_result.executed:
        execution_status = "pending"
    else:
        execution_status = "not_attempted"
    tx_hash = round_result.verification.transactionHash if round_result.verification else None
    return DashboardExecution(
        simulation_status=selected.simulation_status.value,
        would_revert=sim.wouldRevert if sim else None,
        gas_estimate=sim.gasEstimate if sim else None,
        policy_approved=round_result.policy_decision.approved if round_result.policy_decision else None,
        execution_status=execution_status,
        execution_id=round_result.verification.executionId if round_result.verification else None,
        transaction_hash=tx_hash,
        explorer_url=(f"https://sepolia.basescan.org/tx/{tx_hash}" if tx_hash else None),
    )


def _empty_verification_panel() -> DashboardVerification:
    return DashboardVerification(
        before_health_factor=None, before_no_debt=False, before_risk=None,
        after_health_factor=None, after_no_debt=None, after_risk=None,
        risk_reduced=None, incident_resolved=False,
    )


def _verification_panel(round_result: RecoveryRunResult | None, resolved: bool) -> DashboardVerification:
    if round_result is None or round_result.risk_before is None:
        return _empty_verification_panel()
    before = round_result.risk_before
    after = round_result.risk_after
    risk_reduced = None
    if after is not None:
        risk_reduced = after.health_factor > before.health_factor
    return DashboardVerification(
        # The raw sentinel value never leaves the backend as a "health
        # factor" string — when no_debt is true the number is meaningless,
        # so the field is None and the frontend renders "No debt" from the
        # boolean instead (see RiskAssessment.no_debt in aegis.risk).
        before_health_factor=None if before.no_debt else str(before.health_factor),
        before_no_debt=before.no_debt, before_risk=before.level.value,
        after_health_factor=None if (after and after.no_debt) else (str(after.health_factor) if after else None),
        after_no_debt=after.no_debt if after else None,
        after_risk=after.level.value if after else None,
        risk_reduced=risk_reduced, incident_resolved=resolved,
    )


def _recovery_steps(round_result: RecoveryRunResult | None) -> list[DashboardRecoveryStep]:
    if round_result is None:
        return []
    steps = [
        DashboardRecoveryStep(
            action=c.decision.value, amount=c.amount, simulation_status=c.simulation_status.value,
            outcome="rejected", reason=c.rejection_reason,
        )
        for c in round_result.recovery_attempts
    ]
    if round_result.selected is not None and round_result.selected.decision.value != "DO_NOTHING":
        steps.append(
            DashboardRecoveryStep(
                action=round_result.selected.decision.value, amount=round_result.selected.amount,
                simulation_status=round_result.selected.simulation_status.value,
                outcome="selected", reason=None,
            )
        )
    return steps


def _audit_timeline(events: list[AuditEvent]) -> list[DashboardAuditEvent]:
    ordered = sorted(events, key=lambda e: e.timestamp)
    return [
        DashboardAuditEvent(run_id=e.run_id, timestamp=e.timestamp.isoformat(), stage=e.stage, detail=e.detail)
        for e in ordered
    ]


def _dashboard_state_from_handle(handle: RunHandle) -> DashboardState:
    """The rich path: an in-process RunHandle with a fully-typed
    PipelineResult (or none yet, if still running) to build every panel
    from — exactly what the pre-Phase-19A /api/demo and /api/live endpoints
    did, now shared by every mode instead of duplicated per-endpoint."""
    result = handle.result
    events = handle.audit.all_events()
    last_round = result.rounds[-1] if result and result.rounds else None
    candidates = [_candidate_to_dashboard(c) for c in last_round.candidates] if last_round else []
    explanation = None
    if last_round is not None and last_round.selected is not None:
        detail = build_explanation(last_round.selected, last_round.candidates)
        explanation = DashboardExplanation(
            selected_action=detail.selected_action.value, financial_score=str(detail.financial_score),
            execution_score=str(detail.execution_score),
            combined_score=str(detail.combined_score) if detail.combined_score is not None else None,
            expected_risk_reduction=str(detail.expected_risk_reduction),
            rejected_candidates=detail.rejected_candidates, rejection_reasons=detail.rejection_reasons,
            selection_reason=detail.selection_reason,
        )

    # health_factor prefers the post-round read, falling back to the
    # pre-round read for rounds that never executed anything (e.g. SAFE,
    # DO_NOTHING). Either way, the raw uint256-max sentinel never becomes
    # a displayed number — no_debt=True means the field is None and the
    # frontend shows "No debt" from the boolean instead.
    risk_obj = (last_round.risk_after or last_round.risk_before) if last_round else None
    no_debt = bool(risk_obj.no_debt) if risk_obj is not None else False
    health_factor = None if no_debt else (str(risk_obj.health_factor) if risk_obj is not None else None)
    risk_level = risk_obj.level.value if risk_obj is not None else None
    risk_tier = _display_risk_tier(risk_obj)

    final_state = result.final_state if result is not None else None
    system_status = _system_status(handle.latest_stage, handle.running)
    incident_state = _incident_state(
        handle.running, last_round.risk_before if last_round else None, final_state,
    )
    run_state = _run_state(handle.running, handle.error, handle.mode, executed_for_real=False, final_state=final_state)

    if handle.error is not None:
        status = "Error"
    elif handle.running:
        status = "Running"
    elif result is None:
        status = "Error"
    elif incident_state == "NO_ACTIVE_INCIDENT":
        status = "Monitoring"
    elif result.resolved:
        status = "Resolved"
    elif result.final_state is RunState.UNCERTAIN:
        status = "Uncertain, Stopped"
    elif result.final_state is RunState.NO_SAFE_ACTION:
        status = "No Safe Action"
    elif result.final_state is RunState.READY_TO_EXECUTE:
        status = "Monitoring"
    else:
        status = "Recovering"

    detected_events = [e for e in events if e.stage == "DETECTED"]
    last_update = detected_events[-1].timestamp.isoformat() if detected_events else None
    # Collateral/debt: prefer the post-round read, but fall back to the
    # pre-round read (position_before) for rounds that never executed
    # anything — that read still happened and came back with real values
    # (including a real, known zero), so it must not be reported as
    # "unknown" just because nothing was executed afterward.
    position_read = (
        (last_round.position_after or last_round.position_before) if last_round else None
    )
    position: dict[str, str | None | bool] = {
        "collateral": position_read.totalCollateralBase if position_read else None,
        "debt": position_read.totalDebtBase if position_read else None,
        "health_factor": health_factor,
        "no_debt": no_debt,
        "risk_level": risk_level,
        "risk_threshold": str(risk_obj.threshold) if risk_obj else None,
        "timestamp": last_update,
        "last_update": last_update,
    }
    return DashboardState(
        mode=handle.mode.value, run_id=handle.run_id, running=handle.running, stage=handle.latest_stage,
        generated_at=datetime.now(timezone.utc).isoformat(), status=status,
        system_status=system_status, incident_state=incident_state, run_state=run_state,
        network=handle.network, chain_id=int(handle.network), wallet=handle.wallet, wallet_source=handle.wallet_source,
        protocol="Aave V3", health_factor=health_factor, no_debt=no_debt, risk_level=risk_level, risk_tier=risk_tier,
        position=position,
        candidates=candidates, explanation=explanation,
        execution=_execution_panel(last_round), verification=_verification_panel(last_round, bool(result and result.resolved)),
        audit_timeline=_audit_timeline(events), recovery_steps=_recovery_steps(last_round),
        rounds=len(result.rounds) if result else 0, error=handle.error,
    )


def _best_effort_field(events: list[AuditEvent], stage: str, key: str) -> str | None:
    for event in reversed(events):
        if event.stage == stage and event.detail.get(key) is not None:
            return event.detail[key]
    return None


def _dashboard_state_from_file_events(run_id: str, events: list[AuditEvent]) -> DashboardState:
    """The cross-process fallback: run_id isn't in this process's
    in-memory registry (e.g. `aegis live-demo --confirm` ran in a separate
    CLI process). Rebuilds only what the shared audit_log_path's events
    actually contain — never fabricates candidates/scores this process
    never computed itself. Section 9's required LIVE_EXECUTION fields
    (execution id, tx hash, explorer URL, before/after health
    factor/risk) are all present in EXECUTED/VERIFIED/REASSESS_RISK event
    detail dicts, so those are populated; per-candidate scoring detail is
    not (that lived only in the other process's PipelineResult objects).
    Callers must ensure `events` is non-empty — aegis.api's
    get_run_endpoint returns 404 before ever calling this otherwise."""
    stage = events[-1].stage
    running = stage not in ("RUN_RESOLVED", "RUN_STOPPED")
    detected = next((e for e in events if e.stage == "DETECTED"), None)
    network = str(detected.detail.get("network")) if detected else ""
    wallet = detected.detail.get("user") if detected else None

    execution_id = _best_effort_field(events, "EXECUTED", "execution_id")
    tx_hash = _best_effort_field(events, "VERIFIED", "transaction_hash")
    before_hf = _best_effort_field(events, "ANALYZED", "health_factor")
    before_risk = _best_effort_field(events, "ANALYZED", "level")
    after_hf = _best_effort_field(events, "REASSESS_RISK", "risk_after")
    resolved_event = next((e for e in events if e.stage == "RUN_RESOLVED"), None)
    resolved = resolved_event is not None

    execution_status = "not_attempted"
    if any(e.stage == "EXECUTED" for e in events):
        execution_status = "pending"
    if any(e.stage == "VERIFIED" for e in events):
        execution_status = "completed"
    if any(e.stage == "FAILED" for e in events):
        execution_status = "failed"
    if any(e.stage == "UNCERTAIN" for e in events):
        execution_status = "uncertain"

    # ANALYZED/REASSESS_RISK record the already-human-scaled health factor
    # (see recovery.run_with_recovery), so the sentinel check compares
    # against the human-scaled constant rather than the raw wad one used
    # by aegis.risk.RiskAssessment.no_debt for a freshly computed reading.
    before_no_debt = before_hf is not None and is_no_debt_health_factor(Decimal(before_hf))
    after_no_debt = None if after_hf is None else is_no_debt_health_factor(Decimal(after_hf))
    no_debt = after_no_debt if after_no_debt is not None else before_no_debt
    health_factor = None if no_debt else (after_hf or before_hf)

    at_risk_before = before_risk == "AT_RISK"
    if before_risk is None or not at_risk_before:
        incident_state = "NO_ACTIVE_INCIDENT"
    elif running:
        incident_state = "ACTIVE"
    elif resolved:
        incident_state = "RESOLVED"
    elif execution_status == "failed":
        incident_state = "FAILED"
    elif execution_status == "uncertain":
        incident_state = "UNCERTAIN"
    else:
        incident_state = "ACTIVE"

    if resolved:
        final_state: RunState | None = RunState.RESOLVED
    elif execution_status == "failed":
        final_state = RunState.FAILED
    elif execution_status == "uncertain":
        final_state = RunState.UNCERTAIN
    else:
        final_state = None
    executed_for_real = execution_id is not None or tx_hash is not None
    system_status = _system_status(stage, running)
    run_state = _run_state(running, None, DemoMode.LIVE_EXECUTION, executed_for_real, final_state)

    if running:
        status = "Running"
    elif incident_state == "NO_ACTIVE_INCIDENT":
        status = "Monitoring"
    elif resolved:
        status = "Resolved"
    else:
        status = "Stopped"

    detected_collateral = detected.detail.get("totalCollateralBase") if detected else None
    detected_debt = detected.detail.get("totalDebtBase") if detected else None

    return DashboardState(
        mode="live_execution", run_id=run_id, running=running, stage=stage,
        generated_at=datetime.now(timezone.utc).isoformat(), status=status,
        system_status=system_status, incident_state=incident_state, run_state=run_state,
        network=network, chain_id=int(network) if network.isdigit() else 0, wallet=wallet,
        # Always CLI-started (aegis live-demo --confirm never accepts a
        # connected wallet — see aegis.cli) — dev_default is correct here.
        wallet_source="dev_default", protocol="Aave V3",
        health_factor=health_factor, no_debt=no_debt, risk_level=None, risk_tier=None,
        position={
            "collateral": detected_collateral, "debt": detected_debt,
            "health_factor": health_factor, "no_debt": no_debt,
            "last_update": events[-1].timestamp.isoformat(),
        },
        candidates=[], explanation=None,
        execution=DashboardExecution(
            simulation_status="NOT_APPLICABLE", would_revert=None, gas_estimate=None, policy_approved=None,
            execution_status=execution_status, execution_id=execution_id, transaction_hash=tx_hash,
            explorer_url=(f"https://sepolia.basescan.org/tx/{tx_hash}" if tx_hash else None),
        ),
        verification=DashboardVerification(
            before_health_factor=None if before_no_debt else before_hf, before_no_debt=before_no_debt,
            before_risk=before_risk,
            after_health_factor=None if after_no_debt else after_hf, after_no_debt=after_no_debt,
            after_risk=None,
            risk_reduced=None, incident_resolved=resolved,
        ),
        audit_timeline=_audit_timeline(events), recovery_steps=[],
        rounds=len({e.run_id for e in events}), error=None,
    )


# --- endpoints -------------------------------------------------------


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/runs", response_model=StartRunResponse)
def start_run_endpoint(request: StartRunRequest) -> StartRunResponse:
    """Starts a FIXTURE or LIVE_DRY_RUN run and returns its run_id
    immediately — the run continues on a background thread (FIXTURE
    finishes synchronously, before this even returns). Poll
    GET /api/runs/{run_id} for progress. LIVE_EXECUTION is intentionally
    not a valid value of `mode` here — see this module's docstring."""
    mode = DemoMode(request.mode)
    settings = get_settings() if mode is DemoMode.LIVE_DRY_RUN else None
    wallet_address = request.wallet.address if request.wallet and request.wallet.connected else None
    try:
        handle = start_run(mode, settings=settings, wallet_address=wallet_address)
    except DemoOrchestrationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return StartRunResponse(run_id=handle.run_id, mode=request.mode)


@app.get("/api/runs/{run_id}", response_model=DashboardState)
def get_run_endpoint(run_id: str) -> DashboardState:
    """Poll this for a run's current state — safe to call repeatedly while
    `running` is true. Works for a run this process started
    (`POST /api/runs`) AND for one a separate CLI process started
    (`aegis live-demo --confirm`), by falling back to the shared
    audit_log_path when run_id isn't in this process's memory."""
    handle = get_run(run_id)
    if handle is not None:
        return _dashboard_state_from_handle(handle)
    settings = get_settings()
    events = load_events_for_run(settings.aegis_audit_log_path, run_id)
    if not events:
        raise HTTPException(status_code=404, detail=f"no run found with id {run_id}")
    return _dashboard_state_from_file_events(run_id, events)
