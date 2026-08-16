"""Tests for the dashboard-state semantics in aegis.api: the uint256-max
no-debt sentinel must never be displayed, known-zero collateral/debt must
be distinguished from unknown, and SYSTEM/POSITION/INCIDENT/RUN state must
be reported separately instead of collapsed into a single "Resolved".

These build RunHandle/RecoveryRunResult/AuditEvent objects directly rather
than driving a full pipeline run, since the FIXTURE story's own narrative
is a single fixed at-risk-then-resolved scenario (see test_api.py) and
cannot exercise the no-debt/no-incident path this module is really about.
"""

from datetime import datetime, timezone
from decimal import Decimal

from aegis.aave import AaveUserAccountData
from aegis.api import (
    DashboardState,
    _dashboard_state_from_file_events,
    _dashboard_state_from_handle,
    _display_risk_tier,
    _empty_verification_panel,
    _incident_state,
    _run_state,
    _system_status,
    _verification_panel,
)
from aegis.audit import AuditEvent, AuditLogger
from aegis.demo_orchestrator import DemoMode, RunHandle
from aegis.pipeline import PipelineResult
from aegis.recovery import RecoveryRunResult, RunState
from aegis.risk import NO_DEBT_HEALTH_FACTOR, RiskAssessment, RiskLevel

NETWORK = "84532"
WALLET = "0xWallet"
THRESHOLD = Decimal("1.5")

# Aave's uint256-max sentinel, human-scaled — exactly what a naive display
# of health_factor would render as a giant number if no_debt weren't
# checked first.
NO_DEBT_RAW_WAD = str(2**256 - 1)


def _no_debt_risk() -> RiskAssessment:
    return RiskAssessment(
        health_factor=NO_DEBT_HEALTH_FACTOR, threshold=THRESHOLD, level=RiskLevel.SAFE, no_debt=True,
    )


def _at_risk_risk(health_factor: str = "1.09") -> RiskAssessment:
    return RiskAssessment(
        health_factor=Decimal(health_factor), threshold=THRESHOLD, level=RiskLevel.AT_RISK, no_debt=False,
    )


def _safe_risk(health_factor: str = "1.74") -> RiskAssessment:
    return RiskAssessment(
        health_factor=Decimal(health_factor), threshold=THRESHOLD, level=RiskLevel.SAFE, no_debt=False,
    )


def _zero_position() -> AaveUserAccountData:
    return AaveUserAccountData(
        totalCollateralBase="0", totalDebtBase="0", availableBorrowsBase="0",
        currentLiquidationThreshold="8000", ltv="7500", healthFactor=NO_DEBT_RAW_WAD,
    )


def _handle(
    *, mode: DemoMode = DemoMode.FIXTURE, running: bool = False, error: str | None = None,
    result: PipelineResult | None, audit: AuditLogger | None = None, run_id: str = "run-1",
    wallet_source: str = "dev_default",
) -> RunHandle:
    return RunHandle(
        run_id=run_id, mode=mode, audit=audit or AuditLogger(), network=NETWORK, wallet=WALLET,
        wallet_source=wallet_source,  # type: ignore[arg-type]
        started_at=datetime.now(timezone.utc),
        completed_at=None if running else datetime.now(timezone.utc),
        result=result, error=error,
    )


# --- _display_risk_tier ----------------------------------------------------


def test_risk_tier_no_position_when_no_read_happened() -> None:
    assert _display_risk_tier(None) == "NO_POSITION"


def test_risk_tier_safe_for_no_debt_sentinel() -> None:
    """The giant sentinel-derived health_factor must never push the tier
    into a numeric comparison — no_debt short-circuits straight to SAFE."""
    assert _display_risk_tier(_no_debt_risk()) == "SAFE"


def test_risk_tier_safe_above_threshold() -> None:
    assert _display_risk_tier(_safe_risk("1.74")) == "SAFE"


def test_risk_tier_at_risk_between_80_and_100_percent_of_threshold() -> None:
    assert _display_risk_tier(_at_risk_risk("1.3")) == "AT_RISK"


def test_risk_tier_high_between_60_and_80_percent_of_threshold() -> None:
    assert _display_risk_tier(_at_risk_risk("1.0")) == "HIGH"


def test_risk_tier_critical_below_60_percent_of_threshold() -> None:
    assert _display_risk_tier(_at_risk_risk("0.5")) == "CRITICAL"


# --- _system_status / _incident_state / _run_state --------------------------


def test_system_status_monitoring_when_not_running() -> None:
    assert _system_status("EXECUTED", running=False) == "MONITORING"


def test_system_status_intervening_while_executing() -> None:
    assert _system_status("EXECUTING", running=True) == "INTERVENING"


def test_system_status_verifying_during_reassessment() -> None:
    assert _system_status("REASSESS_RISK", running=True) == "VERIFYING"


def test_system_status_analyzing_otherwise() -> None:
    assert _system_status("DETECTED", running=True) == "ANALYZING"


def test_incident_state_no_active_incident_when_never_read() -> None:
    assert _incident_state(running=False, risk_before=None, final_state=None) == "NO_ACTIVE_INCIDENT"


def test_incident_state_no_active_incident_when_position_was_safe() -> None:
    """The exact bug from problem #3: a SAFE (or no-debt) read must never
    be reported as a resolved incident, because there was no incident."""
    assert _incident_state(running=False, risk_before=_no_debt_risk(), final_state=RunState.RESOLVED) == (
        "NO_ACTIVE_INCIDENT"
    )
    assert _incident_state(running=False, risk_before=_safe_risk(), final_state=RunState.RESOLVED) == (
        "NO_ACTIVE_INCIDENT"
    )


def test_incident_state_active_while_at_risk_and_running() -> None:
    assert _incident_state(running=True, risk_before=_at_risk_risk(), final_state=None) == "ACTIVE"


def test_incident_state_resolved_after_a_genuine_at_risk_recovery() -> None:
    assert _incident_state(running=False, risk_before=_at_risk_risk(), final_state=RunState.RESOLVED) == "RESOLVED"


def test_incident_state_failed_when_recovery_failed() -> None:
    assert _incident_state(running=False, risk_before=_at_risk_risk(), final_state=RunState.FAILED) == "FAILED"


def test_incident_state_uncertain_when_verification_inconclusive() -> None:
    assert _incident_state(running=False, risk_before=_at_risk_risk(), final_state=RunState.UNCERTAIN) == "UNCERTAIN"


def test_run_state_running_while_in_progress() -> None:
    assert _run_state(True, None, DemoMode.FIXTURE, executed_for_real=False, final_state=None) == "RUNNING"


def test_run_state_dry_run_complete_for_fixture_and_live_dry_run() -> None:
    assert _run_state(False, None, DemoMode.FIXTURE, executed_for_real=False, final_state=RunState.RESOLVED) == (
        "DRY_RUN_COMPLETE"
    )
    assert _run_state(False, None, DemoMode.LIVE_DRY_RUN, executed_for_real=False, final_state=RunState.RESOLVED) == (
        "DRY_RUN_COMPLETE"
    )


def test_run_state_execution_complete_only_for_a_real_broadcast() -> None:
    assert _run_state(
        False, None, DemoMode.LIVE_EXECUTION, executed_for_real=True, final_state=RunState.RESOLVED,
    ) == "EXECUTION_COMPLETE"


def test_run_state_failed_on_error() -> None:
    assert _run_state(False, "boom", DemoMode.LIVE_EXECUTION, executed_for_real=False, final_state=None) == "FAILED"


def test_run_state_stopped_when_never_executed_and_no_error() -> None:
    assert _run_state(
        False, None, DemoMode.LIVE_EXECUTION, executed_for_real=False, final_state=RunState.RESOLVED,
    ) == "STOPPED"


# --- _verification_panel: no_debt must never leak the raw sentinel ---------


def test_empty_verification_panel_carries_the_new_required_fields() -> None:
    panel = _empty_verification_panel()
    assert panel.before_no_debt is False
    assert panel.after_no_debt is None


def test_verification_panel_hides_health_factor_when_no_debt() -> None:
    round_result = RecoveryRunResult(
        run_id="r", final_state=RunState.RESOLVED, candidates=[],
        risk_before=_no_debt_risk(), risk_after=_no_debt_risk(),
    )
    panel = _verification_panel(round_result, resolved=True)
    assert panel.before_health_factor is None
    assert panel.before_no_debt is True
    assert panel.after_health_factor is None
    assert panel.after_no_debt is True


def test_verification_panel_shows_health_factor_for_a_genuine_reading() -> None:
    round_result = RecoveryRunResult(
        run_id="r", final_state=RunState.RESOLVED, candidates=[],
        risk_before=_at_risk_risk("1.09"), risk_after=_safe_risk("1.74"),
    )
    panel = _verification_panel(round_result, resolved=True)
    assert panel.before_health_factor == "1.09"
    assert panel.before_no_debt is False
    assert panel.after_health_factor == "1.74"
    assert panel.after_no_debt is False


# --- _dashboard_state_from_handle: the full regression scenario ------------


def test_no_debt_no_collateral_no_incident_regression() -> None:
    """Exact scenario from the bug report: healthFactor = uint256.max,
    totalDebtBase = 0, totalCollateralBase = 0. Must show "no debt" (never
    the giant number), "$0"-representable known-zero collateral/debt, SAFE
    risk, no active incident, MONITORING system status, and a completed
    dry run — never RESOLVED."""
    audit = AuditLogger()
    audit.record(
        "run-1", "DETECTED", network=NETWORK, user=WALLET, protocol="aave-v3", chain=NETWORK,
        healthFactor=NO_DEBT_RAW_WAD, totalCollateralBase="0", totalDebtBase="0",
    )
    round_result = RecoveryRunResult(
        run_id="run-1", final_state=RunState.RESOLVED, candidates=[],
        risk_before=_no_debt_risk(), position_before=_zero_position(),
    )
    result = PipelineResult(
        resolved=True, dry_run=True, final_state=RunState.RESOLVED, stop_reason="already safe",
        run_id="run-1", rounds=[round_result],
    )
    handle = _handle(result=result, audit=audit)

    state = _dashboard_state_from_handle(handle)

    assert state.no_debt is True
    assert state.health_factor is None
    assert state.risk_tier == "SAFE"
    assert state.position["collateral"] == "0"
    assert state.position["debt"] == "0"
    assert state.position["no_debt"] is True
    assert state.incident_state == "NO_ACTIVE_INCIDENT"
    assert state.system_status == "MONITORING"
    assert state.run_state == "DRY_RUN_COMPLETE"

    # No enormous health-factor number appears in any *display* field —
    # the raw wad value legitimately still appears inside the audit
    # timeline's DETECTED event detail (a truthful record of what was
    # actually read on-chain, not a rendered "health factor" figure).
    assert state.health_factor is None
    assert state.verification.before_health_factor is None
    assert str(NO_DEBT_HEALTH_FACTOR) not in state.model_dump_json(exclude={"audit_timeline"})


def test_wallet_source_is_reported_straight_from_the_handle() -> None:
    """The frontend must never guess whether it's showing the visitor's
    own connected wallet or dev/demo data — this comes straight from
    whatever aegis.demo_orchestrator.start_run actually resolved."""
    round_result = RecoveryRunResult(
        run_id="run-1", final_state=RunState.RESOLVED, candidates=[],
        risk_before=_no_debt_risk(), position_before=_zero_position(),
    )
    result = PipelineResult(
        resolved=True, dry_run=True, final_state=RunState.RESOLVED, stop_reason="already safe",
        run_id="run-1", rounds=[round_result],
    )
    for source in ("connected", "dev_default", "fixture"):
        state = _dashboard_state_from_handle(_handle(result=result, wallet_source=source))
        assert state.wallet_source == source


def test_known_zero_collateral_and_debt_survive_when_nothing_executed() -> None:
    """A round that reads a real zero-collateral/zero-debt position and
    then selects DO_NOTHING (nothing executed) must still report "0", not
    "unknown" — position_after is never populated in that case, so the
    fallback to position_before is what makes this work."""
    round_result = RecoveryRunResult(
        run_id="run-1", final_state=RunState.RESOLVED, candidates=[],
        risk_before=_no_debt_risk(), position_before=_zero_position(), position_after=None,
    )
    result = PipelineResult(
        resolved=True, dry_run=True, final_state=RunState.RESOLVED, stop_reason="already safe",
        run_id="run-1", rounds=[round_result],
    )
    handle = _handle(result=result)

    state = _dashboard_state_from_handle(handle)

    assert state.position["collateral"] == "0"
    assert state.position["debt"] == "0"


def test_unknown_collateral_and_debt_when_no_read_ever_happened() -> None:
    """Distinct from the known-zero case above: when there is no round at
    all (e.g. the run errored before DETECTED), collateral/debt must stay
    None ("unknown"), never coerced to "0"."""
    handle = _handle(result=None, error="boom")

    state = _dashboard_state_from_handle(handle)

    assert state.position["collateral"] is None
    assert state.position["debt"] is None
    assert state.no_debt is False


def test_active_incident_reported_while_running() -> None:
    round_result = RecoveryRunResult(
        run_id="run-1", final_state=RunState.EVALUATING, candidates=[], risk_before=_at_risk_risk("1.09"),
    )
    result = PipelineResult(
        resolved=False, dry_run=True, final_state=RunState.EVALUATING, stop_reason=None,
        run_id="run-1", rounds=[round_result],
    )
    handle = _handle(result=result, running=True)

    state = _dashboard_state_from_handle(handle)

    assert state.incident_state == "ACTIVE"
    assert state.system_status == "ANALYZING"
    assert state.run_state == "RUNNING"


def test_resolved_incident_after_a_genuine_at_risk_recovery() -> None:
    round_result = RecoveryRunResult(
        run_id="run-1", final_state=RunState.RESOLVED, candidates=[],
        risk_before=_at_risk_risk("1.09"), risk_after=_safe_risk("1.74"),
        position_before=AaveUserAccountData(
            totalCollateralBase="1000000000", totalDebtBase="733944954", availableBorrowsBase="0",
            currentLiquidationThreshold="8000", ltv="7500", healthFactor="1090000000000000000",
        ),
        position_after=AaveUserAccountData(
            totalCollateralBase="1600000000", totalDebtBase="733944954", availableBorrowsBase="0",
            currentLiquidationThreshold="8000", ltv="7500", healthFactor="1740000000000000000",
        ),
    )
    result = PipelineResult(
        resolved=True, dry_run=True, final_state=RunState.RESOLVED, stop_reason="resolved",
        run_id="run-1", rounds=[round_result],
    )
    handle = _handle(result=result)

    state = _dashboard_state_from_handle(handle)

    assert state.incident_state == "RESOLVED"
    assert state.health_factor == "1.74"
    assert state.no_debt is False
    assert state.position["collateral"] == "1600000000"


# --- _dashboard_state_from_file_events (cross-process fallback) ------------


def _event(stage: str, **detail: object) -> AuditEvent:
    return AuditEvent(run_id="run-2", stage=stage, timestamp=datetime.now(timezone.utc), detail=detail)


def test_file_events_no_active_incident_when_safe() -> None:
    events = [
        _event("DETECTED", network=NETWORK, user=WALLET, totalCollateralBase="0", totalDebtBase="0"),
        _event(
            "ANALYZED", health_factor=str(NO_DEBT_HEALTH_FACTOR), threshold=str(THRESHOLD), level="SAFE",
        ),
        _event("RUN_RESOLVED", status="RESOLVED", reason="already safe"),
    ]
    state = _dashboard_state_from_file_events("run-2", events)

    assert state.no_debt is True
    assert state.health_factor is None
    assert state.incident_state == "NO_ACTIVE_INCIDENT"
    assert state.status == "Monitoring"
    assert state.position["collateral"] == "0"
    assert state.position["debt"] == "0"


def test_file_events_active_incident_while_running() -> None:
    events = [
        _event("DETECTED", network=NETWORK, user=WALLET, totalCollateralBase="1000000000", totalDebtBase="733944954"),
        _event("ANALYZED", health_factor="1.09", threshold=str(THRESHOLD), level="AT_RISK"),
        _event("EXECUTING"),
    ]
    state = _dashboard_state_from_file_events("run-2", events)

    assert state.running is True
    assert state.incident_state == "ACTIVE"
    assert state.run_state == "RUNNING"


def test_file_events_live_execution_complete_after_a_real_broadcast() -> None:
    events = [
        _event("DETECTED", network=NETWORK, user=WALLET, totalCollateralBase="1000000000", totalDebtBase="733944954"),
        _event("ANALYZED", health_factor="1.09", threshold=str(THRESHOLD), level="AT_RISK"),
        _event("EXECUTED", execution_id="exec-123"),
        _event("VERIFIED", transaction_hash="0xdeadbeef"),
        _event("REASSESS_RISK", risk_after="1.74"),
        _event("RUN_RESOLVED", status="RESOLVED", reason="resolved"),
    ]
    state = _dashboard_state_from_file_events("run-2", events)

    assert state.incident_state == "RESOLVED"
    assert state.run_state == "EXECUTION_COMPLETE"
    assert state.execution.transaction_hash == "0xdeadbeef"
    assert state.health_factor == "1.74"


def test_file_events_dashboard_state_is_a_valid_dashboard_state() -> None:
    """Guards against silent schema drift between this fallback path and
    the richer in-process path — both must build the same DashboardState."""
    events = [_event("DETECTED", network=NETWORK, user=WALLET), _event("RUN_STOPPED", status="FAILED")]
    state = _dashboard_state_from_file_events("run-2", events)
    assert isinstance(state, DashboardState)
