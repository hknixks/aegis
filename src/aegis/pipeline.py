"""The complete, wired-together Aegis pipeline.

This is the composition root: the one place that assembles a real
KeeperHubClient and every service built on top of it
(AavePositionReader, SimulationService, ExecutionService,
VerificationService) and drives them through aegis.recovery's state
machine. Every other module in this project is tested against fakes;
this module is what actually runs for real (or in dry-run/demo mode)
against KeeperHub.

    1. Read Aave position                    ) aegis.aave.AavePositionReader
    2. Calculate risk                        ) aegis.risk.assess_health_factor
    3. Generate candidate interventions       )
    4. Score financial effectiveness          ) aegis.recovery.run_with_recovery,
    5. Evaluate execution feasibility         ) which reuses aegis.decision_engine's
    6. Simulate candidate actions             ) pure scoring functions and
    7. Eliminate failed candidates            ) aegis.execution's services —
    8. Select best executable action          ) nothing here is reimplemented.
    9. Run deterministic policy validation    )
   10. Execute through KeeperHub              )
   11. Poll execution status                  )
   12. Obtain transaction hash                )
   13. Verify the resulting position          ) run_with_recovery's REASSESS_RISK
   14. Recalculate risk                       ) step re-reads ground truth.
   15. Determine whether the incident is resolved   ) this module: compares
   16. If not resolved, enter recovery/re-planning  ) risk_after to risk.at_risk
                                                       and starts another round.
   17. Write the complete audit trail         ) every step above already writes
                                                 to the same aegis.audit.AuditLogger.

KeeperHub remains the only onchain execution layer: this module never
constructs, signs, or broadcasts a transaction, and never talks to any
provider other than KeeperHub. There is no direct blockchain signing
anywhere in this project.

Steps 15-16 (resolution + re-planning) are new here: a single
run_with_recovery call is one round — it can execute one action and
verify it. run_pipeline starts another round (a fresh READ, fresh risk
calculation, fresh candidate generation, from the new ground truth — never
a retry of the same transaction) whenever the round ended in one of two
re-plannable states: RESOLVED but still AT_RISK (it helped but wasn't
enough), or FAILED (a CONFIRMED execution/verification failure — known,
not guessed, so it's safe to try again with fresh information). Both are
bounded by max_rounds. UNCERTAIN and NO_SAFE_ACTION never trigger another
round automatically; UNCERTAIN in particular means Aegis does not know
what happened on-chain, so it stops for operator attention rather than
guessing by trying something else — exactly "never blindly resend an
uncertain transaction."

Dry-run/demo mode: dry_run=True forces execution off for this call
regardless of the operator's configured
settings.aegis_autonomous_execution_enabled — the full decision process
still runs through SIMULATE, but EXECUTE is never reached. This is the
explicit, always-safe way to run the complete pipeline against a real
KeeperHub org and a real Base Sepolia wallet without any risk of a real
transaction. dry_run=False does not itself turn execution on — it only
stops overriding the setting, so autonomous execution still defaults to
off unless the operator explicitly enabled it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal

from aegis.aave import AavePositionReader
from aegis.audit import AuditLogger
from aegis.config import Settings
from aegis.execution import ExecutionService, SimulationService, VerificationService
from aegis.hermes.runtime import HermesAgent
from aegis.keeperhub.client import KeeperHubClient
from aegis.policy import PolicyEngine
from aegis.recovery import RecoveryRunResult, RunState, run_with_recovery

DEFAULT_MAX_ROUNDS = 3


@dataclass
class PipelineComponents:
    """Everything run_pipeline needs, wired from a real (or test-supplied)
    KeeperHubClient. Construct via build_pipeline_components — this
    dataclass just holds the assembled pieces.

    hermes_agent is optional and None by default: when absent, candidate
    generation is exactly what it always was (deterministic only) — see
    aegis.recovery._consult_hermes and aegis.decision_engine.
    build_hermes_candidate for what changes when it's supplied."""

    keeperhub_client: KeeperHubClient
    position_reader: AavePositionReader
    policy_engine: PolicyEngine
    simulation_service: SimulationService
    execution_service: ExecutionService
    verification_service: VerificationService
    owns_client: bool = True
    hermes_agent: HermesAgent | None = None


def build_pipeline_components(
    settings: Settings, *, client: KeeperHubClient | None = None, hermes_agent: HermesAgent | None = None
) -> PipelineComponents:
    """The composition root. KeeperHub is the only onchain execution
    layer this ever talks to — every service below is a thin wrapper over
    the same single KeeperHubClient, and none of them can construct, sign,
    or broadcast a transaction on their own (see aegis.keeperhub.client's
    and aegis.execution's own docstrings).

    hermes_agent is never constructed here — it needs the optional
    `hermes` extra (anthropic + a live MCP session), which this module
    must not require just to run the deterministic pipeline. A caller
    that wants Hermes consulted (e.g. aegis.demo_orchestrator) builds one
    itself and passes it in; None (the default) is the unchanged,
    deterministic-only behavior."""
    owns_client = client is None
    keeperhub_client = client or KeeperHubClient(settings)
    return PipelineComponents(
        keeperhub_client=keeperhub_client,
        position_reader=AavePositionReader(keeperhub_client),
        policy_engine=PolicyEngine(settings),
        simulation_service=SimulationService(keeperhub_client),
        execution_service=ExecutionService(keeperhub_client),
        verification_service=VerificationService(keeperhub_client),
        owns_client=owns_client,
        hermes_agent=hermes_agent,
    )


@dataclass
class PipelineResult:
    resolved: bool
    dry_run: bool
    final_state: RunState
    stop_reason: str | None
    run_id: str | None = None
    rounds: list[RecoveryRunResult] = field(default_factory=list)


def run_pipeline(
    *,
    settings: Settings,
    network: str,
    user: str,
    debt_asset: str,
    collateral_asset: str,
    available_balance: Decimal | None = None,
    audit: AuditLogger | None = None,
    components: PipelineComponents | None = None,
    dry_run: bool = False,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    max_write_attempts: int | None = None,
    max_total_execution_amount: Decimal | None = None,
    max_runtime_seconds: float | None = None,
    run_id: str | None = None,
) -> PipelineResult:
    """The full 17-step Aegis pipeline. See this module's docstring for
    the step-by-step mapping and the resolution/re-planning/dry-run
    semantics.

    `components` lets a caller (tests, mainly) supply an already-built
    PipelineComponents — e.g. one wrapping a KeeperHubClient whose HTTP
    layer is mocked — instead of this function constructing a real one
    from `settings`.

    Hard recovery limits, all optional (None = unbounded, matching prior
    behavior): max_rounds bounds planning cycles (already existed);
    max_write_attempts bounds how many times execute() may actually be
    called across every round; max_total_execution_amount bounds the sum
    of every executed candidate's amount; max_runtime_seconds bounds this
    call's total wall-clock time, checked before starting each round (a
    round already in flight is never interrupted mid-way — that would
    itself be an uncertain-transaction risk). Whichever limit is hit
    first ends the run in NO_SAFE_ACTION with an explicit reason, exactly
    as an ordinary "nothing safe left to try" outcome would.

    run_id, if supplied, is used for the FIRST round only — see
    aegis.recovery's idempotency guard (RunAlreadyCompletedError /
    run_id-based resume) for what happens if this exact run_id was
    already used in a prior call. Re-planning rounds after the first
    always get a fresh, auto-generated run_id, since each is a genuinely
    new decision, not a re-invocation of the same one.
    """
    

    audit = audit or AuditLogger()
    owns_components = components is None
    components = components or build_pipeline_components(settings)
    start_time = time.monotonic()

    # Dry-run/demo mode: run the complete decision process through
    # SIMULATE, but never EXECUTE — regardless of what the operator's real
    # settings say. This is the only place this override happens; nothing
    # downstream needs to know it's in a demo run.
    effective_settings = settings
    if dry_run and settings.aegis_autonomous_execution_enabled:
        effective_settings = settings.model_copy(update={"aegis_autonomous_execution_enabled": False})

    run_marker = run_id or "pipeline-" + network + "-" + user
    audit.record(
        run_marker, "RUN_STARTED", status="started", network=network, user=user,
        dry_run=dry_run, max_rounds=max_rounds,
    )

    def _stop(final_state: RunState, stop_reason: str, rounds: list[RecoveryRunResult]) -> PipelineResult:
        last_run_id = rounds[-1].run_id if rounds else run_marker
        run_stage = "RUN_RESOLVED" if final_state is RunState.RESOLVED else "RUN_STOPPED"
        audit.record(last_run_id, run_stage, status=final_state.value, reason=stop_reason, dry_run=dry_run)
        return PipelineResult(
            resolved=final_state is RunState.RESOLVED, dry_run=dry_run, final_state=final_state,
            stop_reason=stop_reason, run_id=last_run_id, rounds=rounds,
        )

    try:
        rounds: list[RecoveryRunResult] = []
        total_write_attempts = 0
        total_execution_amount = Decimal("0")

        for round_number in range(1, max_rounds + 1):
            if max_runtime_seconds is not None and time.monotonic() - start_time > max_runtime_seconds:
                return _stop(
                    RunState.NO_SAFE_ACTION,
                    f"max runtime exceeded ({max_runtime_seconds}s) before starting round {round_number}",
                    rounds,
                )

            result = run_with_recovery(
                settings=effective_settings,
                position_reader=components.position_reader,
                policy_engine=components.policy_engine,
                simulation_service=components.simulation_service,
                execution_service=components.execution_service,
                verification_service=components.verification_service,
                audit=audit,
                network=network,
                user=user,
                debt_asset=debt_asset,
                collateral_asset=collateral_asset,
                available_balance=available_balance,
                hermes_agent=components.hermes_agent,
                run_id=run_id if round_number == 1 else None,
            )
            rounds.append(result)

            # Hard limits: write attempts and cumulative execution amount.
            # Tracked after every round; only actually enforced at the two
            # re-plan points below — a round that just resolved cleanly
            # doesn't need to be blocked by a limit meant to bound FURTHER
            # write attempts, and interrupting an in-flight round would
            # itself create an uncertain transaction, exactly what this
            # project forbids.
            if result.executed:
                total_write_attempts += 1
                if result.selected is not None and result.selected.amount is not None:
                    total_execution_amount += Decimal(result.selected.amount)

            def _limit_reason() -> str | None:
                if max_write_attempts is not None and total_write_attempts >= max_write_attempts:
                    return f"max write attempts reached ({max_write_attempts})"
                if max_total_execution_amount is not None and total_execution_amount > max_total_execution_amount:
                    return (
                        f"max total execution amount exceeded ({total_execution_amount} > "
                        f"{max_total_execution_amount})"
                    )
                return None

            if result.final_state is RunState.RESOLVED:
                # Step 15: determine whether the incident is resolved. A
                # RESOLVED round with no risk_after means nothing needed
                # fixing (DO_NOTHING was correct) — resolved. A RESOLVED
                # round that executed something is only resolved if the
                # freshly re-read, freshly recalculated risk confirms it.
                # Recorded onto the SAME run_id run_with_recovery already
                # used, so this round's audit trail stays one coherent
                # story rather than splitting across separate ids.
                still_at_risk = result.risk_after is not None and result.risk_after.at_risk
                if not still_at_risk:
                    audit.record(result.run_id, "PIPELINE_RESOLVED", round=round_number, dry_run=dry_run)
                    return _stop(RunState.RESOLVED, "risk objective achieved", rounds)
                limit_reason = _limit_reason()
                if limit_reason is not None:
                    return _stop(RunState.NO_SAFE_ACTION, limit_reason, rounds)
                # Step 16: not resolved — enter recovery/re-planning. This
                # is a brand new round (fresh READ, fresh candidates) on
                # the next loop iteration, not a retry of the transaction
                # that just succeeded.
                audit.record(
                    result.run_id, "PIPELINE_REPLANNING", round=round_number,
                    reason="executed successfully but position is still at risk",
                    failure_category="RISK_NOT_RESOLVED",
                )
                continue

            if result.final_state is RunState.FAILED:
                # A CONFIRMED failure (execution or verification failure,
                # established via KeeperHub's own status endpoint, or via
                # a re-read position after an unexpected verification
                # error) is known, not guessed — safe to re-plan from: a
                # brand new round with fresh candidates and fresh ground
                # truth, never a retry of the transaction that failed.
                # Bounded by max_rounds like every other re-plan.
                limit_reason = _limit_reason()
                if limit_reason is not None:
                    return _stop(RunState.NO_SAFE_ACTION, limit_reason, rounds)
                audit.record(
                    result.run_id, "PIPELINE_REPLANNING", round=round_number,
                    reason="Confirmed execution or verification failure. Trying a new plan from fresh data.",
                    failure_category=result.failure_category.value if result.failure_category else None,
                )
                continue

            # UNCERTAIN, NO_SAFE_ACTION, or READY_TO_EXECUTE (dry-run or
            # autonomous execution disabled) all end the pipeline here.
            # None of these ever triggers another round automatically.
            # UNCERTAIN in particular must never auto-continue — Aegis
            # does not know what happened on-chain, so it never guesses
            # by trying something else; it stops for operator attention,
            # exactly "never blindly resend an uncertain transaction."
            audit.record(
                result.run_id, "PIPELINE_STOPPED", round=round_number,
                final_state=result.final_state.value, dry_run=dry_run,
                failure_category=result.failure_category.value if result.failure_category else None,
            )
            return _stop(result.final_state, result.stop_reason or result.final_state.value, rounds)

        return _stop(RunState.NO_SAFE_ACTION, "max recovery rounds exhausted", rounds)
    finally:
        if owns_components and components.owns_client:
            components.keeperhub_client.close()
