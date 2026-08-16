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
oversight: this API's CORS policy is wide open (`allow_origins=["*"]`,
appropriate for a local hackathon dashboard, not for a real-money-adjacent
write path), so a POST-to-execute endpoint here would let any page in any
browser tab trigger a real Base Sepolia transaction through an
authenticated session. `GET /api/runs/{run_id}` can still show a
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

from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from aegis.audit import AuditEvent, load_events_for_run
from aegis.config import get_settings
from aegis.decision_engine import CandidateAction, build_explanation
from aegis.demo_orchestrator import DemoMode, DemoOrchestrationError, RunHandle, get_run, start_run
from aegis.recovery import RecoveryRunResult, RunState

app = FastAPI(title="Aegis Dashboard API", version="2.0.0")

# Local-dev CORS only — the dashboard is a same-org visualization tool,
# not a public API. Tighten before any real deployment. GET+POST only:
# every POST this API accepts (/api/runs) can only ever start a FIXTURE or
# LIVE_DRY_RUN run — see this module's docstring for why LIVE_EXECUTION is
# excluded structurally, not just by convention.
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST"], allow_headers=["*"],
)


# --- presentational risk tiers -----------------------------------------
#
# aegis.risk.RiskLevel is, and stays, a binary SAFE/AT_RISK classification
# — that is the authoritative signal every real decision in this project
# is made from, and this module never touches it. The dashboard wants a
# 4-tier *display* gradient (SAFE/AT_RISK/HIGH/CRITICAL); this is a purely
# presentational derivation from health_factor/threshold, computed here
# only, never fed back into any decision.

def _display_risk_tier(health_factor: Decimal, threshold: Decimal) -> str:
    if health_factor >= threshold:
        return "SAFE"
    if health_factor >= threshold * Decimal("0.8"):
        return "AT_RISK"
    if health_factor >= threshold * Decimal("0.6"):
        return "HIGH"
    return "CRITICAL"


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
    before_risk: str | None
    after_health_factor: str | None
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
    incident_state: str
    network: str
    chain_id: int
    wallet: str | None
    protocol: str
    health_factor: str | None
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


class StartRunRequest(BaseModel):
    mode: Literal["fixture", "live_dry_run"]


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
        before_health_factor=None, before_risk=None, after_health_factor=None,
        after_risk=None, risk_reduced=None, incident_resolved=False,
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
        before_health_factor=str(before.health_factor), before_risk=before.level.value,
        after_health_factor=str(after.health_factor) if after else None,
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

    health_factor = str(last_round.risk_after.health_factor) if last_round and last_round.risk_after else (
        str(last_round.risk_before.health_factor) if last_round and last_round.risk_before else None
    )
    risk_level = (last_round.risk_after or last_round.risk_before).level.value if last_round and (
        last_round.risk_after or last_round.risk_before
    ) else None
    risk_tier = None
    risk_obj = last_round.risk_after or last_round.risk_before if last_round else None
    if risk_obj is not None:
        risk_tier = _display_risk_tier(risk_obj.health_factor, risk_obj.threshold)

    if handle.error is not None:
        status, incident_state = "Error", "Error"
    elif handle.running:
        status, incident_state = "Running", "Active"
    elif result is None:
        status, incident_state = "Error", "Error"
    elif result.resolved:
        status, incident_state = "Resolved", "Resolved"
    elif result.final_state is RunState.UNCERTAIN:
        status, incident_state = "Uncertain — Stopped", "Uncertain"
    elif result.final_state is RunState.NO_SAFE_ACTION:
        status, incident_state = "No Safe Action", "Blocked"
    elif result.final_state is RunState.READY_TO_EXECUTE:
        status, incident_state = "Monitoring", "Active"
    else:
        status, incident_state = "Recovering", "Re-Planning"

    detected_events = [e for e in events if e.stage == "DETECTED"]
    last_update = detected_events[-1].timestamp.isoformat() if detected_events else None
    position: dict[str, str | None] = {
        "collateral": last_round.position_after.totalCollateralBase if last_round and last_round.position_after else None,
        "debt": last_round.position_after.totalDebtBase if last_round and last_round.position_after else None,
        "health_factor": health_factor,
        "risk_level": risk_level,
        "risk_threshold": str(risk_obj.threshold) if risk_obj else None,
        "timestamp": last_update,
        "last_update": last_update,
    }
    return DashboardState(
        mode=handle.mode.value, run_id=handle.run_id, running=handle.running, stage=handle.latest_stage,
        generated_at=datetime.now(timezone.utc).isoformat(), status=status,
        incident_state=incident_state, network=handle.network, chain_id=int(handle.network), wallet=handle.wallet,
        protocol="Aave V3", health_factor=health_factor, risk_level=risk_level, risk_tier=risk_tier,
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

    status = "Resolved" if resolved else ("Running" if running else "Stopped")

    return DashboardState(
        mode="live_execution", run_id=run_id, running=running, stage=stage,
        generated_at=datetime.now(timezone.utc).isoformat(), status=status,
        incident_state="Resolved" if resolved else ("Active" if running else "Stopped"),
        network=network, chain_id=int(network) if network.isdigit() else 0, wallet=wallet, protocol="Aave V3",
        health_factor=after_hf or before_hf, risk_level=None, risk_tier=None,
        position={"health_factor": after_hf or before_hf, "last_update": events[-1].timestamp.isoformat()},
        candidates=[], explanation=None,
        execution=DashboardExecution(
            simulation_status="NOT_APPLICABLE", would_revert=None, gas_estimate=None, policy_approved=None,
            execution_status=execution_status, execution_id=execution_id, transaction_hash=tx_hash,
            explorer_url=(f"https://sepolia.basescan.org/tx/{tx_hash}" if tx_hash else None),
        ),
        verification=DashboardVerification(
            before_health_factor=before_hf, before_risk=before_risk,
            after_health_factor=after_hf, after_risk=None,
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
    try:
        handle = start_run(mode, settings=settings)
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
