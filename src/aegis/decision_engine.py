"""Execution-aware decision layer — Aegis's core differentiator.

Aegis must never pick the theoretically best financial action if that
action is unsafe or unlikely to execute. This module generates several
candidate actions for the current Aave V3 position, scores each one on two
independent axes — financial effectiveness and execution feasibility — and
selects the highest-scoring candidate that is actually safe to run.

Flow (this module covers GENERATE CANDIDATE ACTIONS through SELECT BEST
EXECUTABLE ACTION; the READ/ANALYZE RISK step before it and the POLICY
CHECK/EXECUTE/VERIFY/REASSESS RISK steps after it belong to
aegis.recovery.run_with_recovery, the single orchestrator that owns the
whole run — nothing about them is reimplemented here):

    READ -> ANALYZE RISK -> GENERATE CANDIDATE ACTIONS
         -> EVALUATE FINANCIAL OUTCOME -> EVALUATE EXECUTION FEASIBILITY
         -> SIMULATE CANDIDATES -> REMOVE FAILED/UNSAFE CANDIDATES
         -> SELECT BEST EXECUTABLE ACTION -> POLICY CHECK -> EXECUTE
         -> VERIFY -> REASSESS RISK

No LLM is anywhere in this call path. Every score is a plain, deterministic
function of observable data (position numbers, PolicyEngine's own
allowlists, and KeeperHub's own simulation response) — nothing here is
invented by a model, and nothing here can be talked out of a policy limit.

Units note: Aave V3's totalCollateralBase/totalDebtBase are both expressed
in the same protocol-defined base currency, so their ratio (health factor)
is scale-invariant regardless of what that base currency's decimals are.
This module reuses that same base-currency scale for candidate `amount`
and `capital_cost` values and for the `available_balance` parameter below,
rather than converting to a specific asset's native decimals — this project
has no price oracle or per-asset decimals lookup yet. That conversion is a
follow-up phase, not this one.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_UP, Decimal, InvalidOperation
from enum import Enum

from pydantic import BaseModel

from aegis.aave import AaveUserAccountData, build_protocol_action_params
from aegis.execution import SimulationService
from aegis.intents import Decision, Intent
from aegis.keeperhub.models import ProtocolActionSimulation
from aegis.policy import PolicyDecision, PolicyEngine
from aegis.risk import RiskAssessment

# --- Tunables (deterministic, no LLM involvement) ---------------------------

# How far above the risk threshold a candidate should aim to push the health
# factor, so a proposed action doesn't just barely clear the line.
_SAFETY_MARGIN = Decimal("0.1")

# Weight applied to capital committed (as a fraction of total collateral)
# when computing financial_score. Higher committed capital is a real cost
# even when it improves the health factor.
_CAPITAL_COST_WEIGHT = Decimal("0.5")

_EXECUTION_SCORE_MAX = Decimal("100")
_BALANCE_UTILIZATION_PENALTY = Decimal("10")
_GAS_ESTIMATE_DIVISOR = Decimal("1_000_000")

_AMOUNT_QUANTIZE = Decimal("0.000001")

# Aave V3's "Base" position accounting (totalCollateralBase/totalDebtBase) is
# USD-denominated with 8 decimals, per Aave's own oracle convention. A
# CandidateAction's `amount`/`capital_cost` are expressed in ordinary human
# decimal terms (i.e. divided by this scale) so they compare sensibly
# against policy limits and available-balance figures — unlike
# totalCollateralBase/totalDebtBase themselves, which this module leaves
# raw everywhere it only needs a health-factor ratio (scale-invariant, so
# no conversion is needed for that math).
_BASE_CURRENCY_SCALE = Decimal(10) ** 8


class SimulationStatus(str, Enum):
    NOT_APPLICABLE = "NOT_APPLICABLE"  # DO_NOTHING — no onchain call to simulate
    SKIPPED = "SKIPPED"  # eliminated before a simulation was ever attempted
    PASSED = "PASSED"
    FAILED = "FAILED"


class CandidateFinalStatus(str, Enum):
    """The outcome of SELECT BEST EXECUTABLE ACTION for this one candidate.
    Set once, after every candidate has been scored and one has been
    selected — never guessed ahead of time, never set by an LLM."""

    SELECTED = "SELECTED"  # this candidate is the one the engine chose
    NOT_SELECTED = "NOT_SELECTED"  # eligible, but a higher-scoring candidate won
    REJECTED = "REJECTED"  # ineligible: policy, balance, or simulation failure


class FinancialScore(BaseModel):
    """How much financial value a candidate creates, independent of
    whether it can actually be executed — "financially attractive" on its
    own, before Execution Confidence is applied.

    Deterministic formula, no LLM input, no historical data:

        value = expected_risk_reduction - CAPITAL_COST_WEIGHT * capital_cost_ratio

    - expected_risk_reduction: projected health factor after the action
      minus the current health factor — plain arithmetic on Aave V3's own
      totalCollateralBase/totalDebtBase/currentLiquidationThreshold (see
      _projected_health_factor).
    - capital_cost_ratio: capital_cost (the amount this action commits)
      divided by total collateral, so a small position and a large
      position are penalized equally per unit of position size, not by
      raw amount.
    - CAPITAL_COST_WEIGHT is a fixed module constant (0.5), never chosen
      by a model.

    DO_NOTHING always scores exactly 0 (no capital committed, no risk
    change) — the break-even point every real action's financial value is
    judged against.
    """

    value: Decimal
    expected_risk_reduction: Decimal
    capital_cost: Decimal
    capital_cost_ratio: Decimal


class ExecutionScore(BaseModel):
    """Execution Confidence: how likely a candidate is to actually execute
    cleanly, as a 0-100 percentage. This is what turns "financially
    attractive" into "financially attractive AND realistically
    executable."

    Deterministic, built only from signals observable right now —
    KeeperHub's own simulation response, PolicyEngine's own allowlist
    verdict, and a supplied available-balance figure. Never a historical
    success rate (this project tracks none), never a model-estimated
    probability, and never a value an LLM supplies —
    compute_execution_score is the only place this number is produced.

    value starts at 100 and is either:
      - forced to exactly 0 the instant any hard requirement is violated:
        policy_compliant is False (closed decision/chain/protocol-action
        allowlist, mainnet block, wallet pin, spending cap — all
        delegated to PolicyEngine, never re-implemented here),
        balance_sufficient is False (amount > available balance), or a
        completed simulation with simulation_passed False or
        would_revert True; or
      - reduced by two soft, proportional penalties when every hard
        requirement is satisfied: a gas-estimate deduction
        (gas / 1,000,000) and a balance-utilization deduction (fraction
        of available balance used x 10) — both real costs, but neither
        one a correctness failure by itself.

    DO_NOTHING is always exactly 100 — there is nothing to broadcast, so
    nothing can fail to execute.

    chain_supported / protocol_supported / action_supported are read from
    PolicyEngine's own violated_rules text (this module and aegis.policy
    are maintained together, so this coupling is intentional, not a guess
    at an external format). This codebase's allowlist joins protocol and
    action into one string (aegis_allowed_protocol_actions), so those two
    flags are always reported identically — a finer-grained PolicyEngine
    would be needed to tell them apart, and isn't in scope here.
    transaction_parameters_valid is read the same way (an "amount"-related
    violation, e.g. not a valid decimal or <= 0) — like the two flags
    above, it is never an independent check; PolicyEngine's own verdict
    (policy_compliant) is what actually gates eligibility. It exists so a
    rejection's specific cause is legible without re-parsing
    violated_rules by hand.

    execution_availability answers "is KeeperHub itself reachable/healthy
    right now" — this project has that capability
    (KeeperHubClient.health_check) but nothing currently wires its result
    into scoring, so this is UNKNOWN (None) unless a caller explicitly
    passes execution_available to compute_execution_score. Per this
    phase's own rule — never invent a value for a signal that isn't
    actually being measured — UNKNOWN here is neither a positive nor a
    negative signal; it is simply absent from the score. Only an
    explicit False (a caller who DID check and found KeeperHub
    unavailable) is a hard failure.

    A value of 0 always corresponds to a non-null rejection_reason on the
    candidate (see describe_execution_rejection) — this is the actual
    mechanism that gives a failed simulation zero eligibility for
    execution, not a side effect of it.
    """

    value: Decimal
    policy_compliant: bool
    chain_supported: bool
    protocol_supported: bool
    action_supported: bool
    transaction_parameters_valid: bool = True
    balance_sufficient: bool | None = None  # None: no available_balance was supplied
    simulation_passed: bool | None = None  # None: not simulated yet
    would_revert: bool | None = None  # None: not simulated yet
    gas_estimate: Decimal | None = None
    execution_availability: bool | None = None  # None: not checked — UNKNOWN, never assumed True


class CombinedScore(BaseModel):
    """The number Aegis actually selects candidates on: Execution
    Confidence applied to Financial Score.

        value = financial.value * (execution.value / 100)

    Multiplicative, not additive, by design — this is what makes
    "financially attractive" different from "financially attractive AND
    realistically executable" a single number, rather than two numbers a
    caller has to reconcile. A candidate with a strong financial_score but
    0 execution confidence (failed simulation, policy violation,
    insufficient balance) scores exactly 0 — identical to DO_NOTHING —
    rather than merely losing a fixed bonus an additive formula would
    apply. And between two candidates with similar financial value but
    different execution confidence, the whole financial value is
    discounted by that confidence, not just a capped slice of it — so a
    much safer action can, and should, outrank a slightly-better-financial
    one once both are judged on what they're actually likely to deliver.

    DO_NOTHING's financial_score is always 0, so its combined_score is
    always exactly 0 regardless of its (always-100) execution confidence
    — the correct decision-theoretic break-even: a real action is only
    worth preferring over doing nothing once its confidence-discounted
    expected value is positive.
    """

    value: Decimal
    financial: FinancialScore
    execution: ExecutionScore


class CandidateAction(BaseModel):
    """One deterministically generated, deterministically scored proposal.

    financial_score/execution_score/combined_score are the scalar values of
    financial_detail/execution_detail/combined_detail (FinancialScore/
    ExecutionScore/CombinedScore) — kept as plain Decimal fields for easy
    comparison and sorting, with the full, documented breakdown attached
    alongside for audit purposes. All of it is computed by plain
    deterministic functions in this module — never by an LLM. A candidate
    with a non-null rejection_reason is not eligible for selection,
    regardless of its scores.
    """

    decision: Decision
    protocol: str | None = None
    action: str | None = None  # e.g. "repay", "supply"; None for DO_NOTHING
    asset: str | None = None
    amount: str | None = None  # decimal string, base-currency-equivalent units
    network: str | None = None
    on_behalf_of: str | None = None

    financial_score: Decimal = Decimal("0")
    execution_score: Decimal = Decimal("0")
    expected_risk_reduction: Decimal = Decimal("0")
    capital_cost: Decimal = Decimal("0")

    financial_detail: FinancialScore | None = None
    execution_detail: ExecutionScore | None = None
    combined_detail: CombinedScore | None = None

    simulation_status: SimulationStatus = SimulationStatus.NOT_APPLICABLE
    simulation_result: ProtocolActionSimulation | None = None
    rejection_reason: str | None = None
    combined_score: Decimal | None = None
    final_status: CandidateFinalStatus | None = None

    rationale: str = ""

    @property
    def protocol_action(self) -> str | None:
        if self.protocol is None or self.action is None:
            return None
        return f"{self.protocol}/{self.action}"

    @property
    def eligible(self) -> bool:
        return self.rejection_reason is None


@dataclass
class EngineDecision:
    selected: CandidateAction
    candidates: list[CandidateAction]
    explanation: str
    explanation_detail: "ExecutionConfidenceExplanation"


def candidate_to_intent(candidate: CandidateAction) -> Intent:
    """The trust-boundary conversion: a CandidateAction becomes a plain
    Intent so it can go through the exact same PolicyEngine and
    build_protocol_action_params the Hermes-driven loop uses. No policy or
    param-shape logic is duplicated here."""
    if candidate.decision is Decision.DO_NOTHING:
        return Intent(
            decision=Decision.DO_NOTHING,
            rationale=candidate.rationale or "position is within safe risk parameters",
        )
    return Intent(
        decision=candidate.decision,
        protocol_action=candidate.protocol_action,
        network=candidate.network,
        asset=candidate.asset,
        amount=candidate.amount,
        on_behalf_of=candidate.on_behalf_of,
        rationale=candidate.rationale,
        source="execution_aware_decision_engine",
    )


def _round_amount_up(amount: Decimal) -> Decimal:
    return amount.quantize(_AMOUNT_QUANTIZE, rounding=ROUND_UP)


def _to_human_amount(raw: Decimal) -> Decimal:
    """Convert a raw Base-currency delta to the human-decimal units
    CandidateAction.amount/capital_cost are expressed in. Rounds up so a
    proposed action always requests at least enough to reach its target."""
    return _round_amount_up(raw / _BASE_CURRENCY_SCALE)


def _liquidation_threshold_fraction(position: AaveUserAccountData) -> Decimal:
    return Decimal(position.currentLiquidationThreshold) / Decimal(10_000)


def _projected_health_factor(
    position: AaveUserAccountData, decision: Decision, amount: Decimal
) -> Decimal:
    collateral = Decimal(position.totalCollateralBase)
    debt = Decimal(position.totalDebtBase)
    lt = _liquidation_threshold_fraction(position)
    if decision is Decision.REPAY_DEBT:
        debt = max(debt - amount, Decimal("0"))
    elif decision is Decision.ADD_COLLATERAL:
        collateral = collateral + amount
    if debt <= 0:
        return Decimal("999999")  # no debt left — Aave's own "safe" sentinel case
    return (collateral * lt) / debt


def _repay_amount(position: AaveUserAccountData, risk: RiskAssessment) -> Decimal | None:
    collateral = Decimal(position.totalCollateralBase)
    debt = Decimal(position.totalDebtBase)
    lt = _liquidation_threshold_fraction(position)
    if debt <= 0 or lt <= 0:
        return None
    target_hf = risk.threshold + _SAFETY_MARGIN
    new_debt = (collateral * lt) / target_hf
    amount = debt - new_debt
    if amount <= 0:
        return None
    return min(amount, debt)


def _add_collateral_amount(position: AaveUserAccountData, risk: RiskAssessment) -> Decimal | None:
    debt = Decimal(position.totalDebtBase)
    collateral = Decimal(position.totalCollateralBase)
    lt = _liquidation_threshold_fraction(position)
    if lt <= 0:
        return None
    target_hf = risk.threshold + _SAFETY_MARGIN
    new_collateral = (target_hf * debt) / lt
    amount = new_collateral - collateral
    if amount <= 0:
        return None
    return amount


def generate_candidate_actions(
    position: AaveUserAccountData,
    risk: RiskAssessment,
    *,
    network: str,
    user: str,
    debt_asset: str,
    collateral_asset: str,
) -> list[CandidateAction]:
    """Deterministically propose candidates for the current position.

    DO_NOTHING is always proposed, unconditionally. REPAY_DEBT and
    ADD_COLLATERAL are only proposed when the position is AT_RISK — a
    healthy position has nothing to fix, so there's nothing to generate.
    """
    candidates = [
        CandidateAction(
            decision=Decision.DO_NOTHING,
            rationale=(
                f"health factor {risk.health_factor} vs threshold {risk.threshold} "
                f"({risk.level.value})"
            ),
        )
    ]

    if not risk.at_risk:
        return candidates

    current_hf = risk.health_factor

    repay_amount_raw = _repay_amount(position, risk)
    if repay_amount_raw is not None:
        projected = _projected_health_factor(position, Decision.REPAY_DEBT, repay_amount_raw)
        repay_amount = _to_human_amount(repay_amount_raw)
        candidates.append(
            CandidateAction(
                decision=Decision.REPAY_DEBT,
                protocol="aave-v3",
                action="repay",
                asset=debt_asset,
                amount=str(repay_amount),
                network=network,
                on_behalf_of=user,
                expected_risk_reduction=projected - current_hf,
                capital_cost=repay_amount,
                rationale=(
                    f"repaying {repay_amount} of debt projects health factor "
                    f"{current_hf} -> {projected}"
                ),
            )
        )

    add_amount_raw = _add_collateral_amount(position, risk)
    if add_amount_raw is not None:
        projected = _projected_health_factor(position, Decision.ADD_COLLATERAL, add_amount_raw)
        add_amount = _to_human_amount(add_amount_raw)
        candidates.append(
            CandidateAction(
                decision=Decision.ADD_COLLATERAL,
                protocol="aave-v3",
                action="supply",
                asset=collateral_asset,
                amount=str(add_amount),
                network=network,
                on_behalf_of=user,
                expected_risk_reduction=projected - current_hf,
                capital_cost=add_amount,
                rationale=(
                    f"adding {add_amount} collateral projects health factor "
                    f"{current_hf} -> {projected}"
                ),
            )
        )

    return candidates


def compute_financial_score(candidate: CandidateAction, position: AaveUserAccountData) -> FinancialScore:
    """See FinancialScore's docstring for the exact formula. No LLM input
    anywhere in this computation."""
    if candidate.decision is Decision.DO_NOTHING:
        return FinancialScore(
            value=Decimal("0"),
            expected_risk_reduction=Decimal("0"),
            capital_cost=Decimal("0"),
            capital_cost_ratio=Decimal("0"),
        )
    collateral = Decimal(position.totalCollateralBase) / _BASE_CURRENCY_SCALE
    capital_ratio = candidate.capital_cost / collateral if collateral > 0 else Decimal("1")
    value = candidate.expected_risk_reduction - _CAPITAL_COST_WEIGHT * capital_ratio
    return FinancialScore(
        value=value,
        expected_risk_reduction=candidate.expected_risk_reduction,
        capital_cost=candidate.capital_cost,
        capital_cost_ratio=capital_ratio,
    )


def _policy_breakdown(policy_decision: PolicyDecision) -> tuple[bool, bool, bool]:
    """See ExecutionScore's docstring for why chain/protocol/action
    support and transaction-parameter validity are all derived from
    PolicyEngine's own message text, and why protocol_supported/
    action_supported end up identical."""
    violated = policy_decision.violated_rules
    chain_supported = not any("chain ID" in v or "mainnet" in v for v in violated)
    protocol_action_supported = not any("protocol_action" in v for v in violated)
    transaction_parameters_valid = not any("amount" in v for v in violated)
    return chain_supported, protocol_action_supported, transaction_parameters_valid


def compute_execution_score(
    candidate: CandidateAction,
    policy_decision: PolicyDecision,
    available_balance: Decimal | None,
    simulation: ProtocolActionSimulation | None,
    *,
    execution_available: bool | None = None,
) -> ExecutionScore:
    """See ExecutionScore's docstring for the exact formula. Called twice
    per real candidate by this module's callers: once with simulation=None
    as a cheap pre-simulation feasibility check (so a candidate already
    known to violate policy or exceed available balance is never
    simulated), and again with KeeperHub's real simulation response once
    one exists.

    execution_available is UNKNOWN (None) unless a caller explicitly
    checked KeeperHub's own reachability (e.g. KeeperHubClient.
    health_check) and passed the result in — nothing in this project
    currently wires that check into scoring, so no caller needs to (or
    should) pass anything here yet. Only an explicit False is a hard
    failure; None never invents a positive or negative signal."""
    if candidate.decision is Decision.DO_NOTHING:
        return ExecutionScore(
            value=_EXECUTION_SCORE_MAX,
            policy_compliant=True,
            chain_supported=True,
            protocol_supported=True,
            action_supported=True,
            transaction_parameters_valid=True,
        )

    chain_supported, protocol_action_supported, transaction_parameters_valid = _policy_breakdown(policy_decision)
    policy_compliant = policy_decision.approved

    balance_sufficient: bool | None = None
    if available_balance is not None and candidate.amount is not None:
        balance_sufficient = Decimal(candidate.amount) <= available_balance

    simulation_passed: bool | None = None
    would_revert: bool | None = None
    gas_estimate: Decimal | None = None
    if simulation is not None:
        simulation_passed = simulation.success
        would_revert = simulation.wouldRevert
        if simulation.gasEstimate is not None:
            try:
                gas_estimate = Decimal(simulation.gasEstimate)
            except InvalidOperation:
                gas_estimate = None

    hard_fail = (
        not policy_compliant
        or balance_sufficient is False
        or simulation_passed is False
        or would_revert is True
        or execution_available is False
    )
    if hard_fail:
        value = Decimal("0")
    else:
        value = _EXECUTION_SCORE_MAX
        if gas_estimate is not None:
            value -= gas_estimate / _GAS_ESTIMATE_DIVISOR
        if available_balance is not None and available_balance > 0 and candidate.amount is not None:
            utilization = Decimal(candidate.amount) / available_balance
            value -= utilization * _BALANCE_UTILIZATION_PENALTY
        value = max(Decimal("0"), value)

    return ExecutionScore(
        value=value,
        policy_compliant=policy_compliant,
        chain_supported=chain_supported,
        protocol_supported=protocol_action_supported,
        action_supported=protocol_action_supported,
        transaction_parameters_valid=transaction_parameters_valid,
        balance_sufficient=balance_sufficient,
        simulation_passed=simulation_passed,
        would_revert=would_revert,
        gas_estimate=gas_estimate,
        execution_availability=execution_available,
    )


def describe_execution_rejection(score: ExecutionScore, policy_decision: PolicyDecision) -> str | None:
    """Human-readable reason a candidate with ExecutionScore.value == 0 was
    rejected — None when it wasn't. A value of exactly 0 always means
    rejected, whichever of the hard-fail conditions produced it."""
    if score.value > 0:
        return None
    if not score.policy_compliant:
        return "policy: " + "; ".join(policy_decision.violated_rules)
    if score.balance_sufficient is False:
        return "insufficient balance for requested amount"
    if score.simulation_passed is False or score.would_revert:
        return "simulation failed or would revert"
    if score.execution_availability is False:
        return "KeeperHub execution is not currently available"
    return "execution not currently viable"


def determine_simulation_status(
    candidate: CandidateAction, simulation: ProtocolActionSimulation | None
) -> SimulationStatus:
    if candidate.decision is Decision.DO_NOTHING:
        return SimulationStatus.NOT_APPLICABLE
    if simulation is None:
        return SimulationStatus.SKIPPED
    if not simulation.success or simulation.wouldRevert:
        return SimulationStatus.FAILED
    return SimulationStatus.PASSED


def compute_combined_score(financial: FinancialScore, execution: ExecutionScore) -> CombinedScore:
    """See CombinedScore's docstring for the exact formula and rationale."""
    value = financial.value * (execution.value / _EXECUTION_SCORE_MAX)
    return CombinedScore(value=value, financial=financial, execution=execution)


def select_best_executable(candidates: list[CandidateAction]) -> CandidateAction:
    """REMOVE FAILED/UNSAFE CANDIDATES -> SELECT BEST EXECUTABLE ACTION.

    A pure function over already-scored candidates — deterministic, no
    network calls. DO_NOTHING is never eliminated (see
    generate_candidate_actions/compute_execution_score), so this list is
    structurally guaranteed to contain at least one eligible candidate.
    """
    executable = [c for c in candidates if c.eligible]
    if not executable:
        raise RuntimeError(
            "no executable candidate — DO_NOTHING must always be executable; this indicates "
            "a bug in candidate generation or feasibility scoring, not a real all-fail state"
        )
    return max(executable, key=lambda c: c.combined_score)


def apply_final_status(candidates: list[CandidateAction], selected: CandidateAction) -> None:
    """Stamp final_status on every candidate given SELECT's outcome. Shared
    by ExecutionAwareDecisionEngine (below) and aegis.recovery.
    run_with_recovery so this bookkeeping is written exactly once, not
    duplicated per engine."""
    for candidate in candidates:
        if candidate is selected:
            candidate.final_status = CandidateFinalStatus.SELECTED
        elif candidate.eligible:
            candidate.final_status = CandidateFinalStatus.NOT_SELECTED
        else:
            candidate.final_status = CandidateFinalStatus.REJECTED


class ExecutionConfidenceExplanation(BaseModel):
    """A structured, deterministic account of SELECT BEST EXECUTABLE
    ACTION's outcome — built entirely from CandidateAction/FinancialScore/
    ExecutionScore/CombinedScore data this module's pure functions already
    computed. Nothing in it is invented, and nothing in it is influenced
    by an LLM: build_explanation() is a plain function of already-scored
    candidates. An LLM may later turn this into prose for a user, but
    every fact it has to work with originates here."""

    selected_action: Decision
    financial_score: Decimal
    execution_score: Decimal
    combined_score: Decimal | None
    expected_risk_reduction: Decimal
    rejected_candidates: list[str]
    rejection_reasons: dict[str, str]
    execution_factors: dict[str, object]
    selection_reason: str


def _selection_reason(selected: CandidateAction, candidates: list[CandidateAction]) -> str:
    """Deterministic, plain-language account of why `selected` won —
    built only from scores already computed elsewhere in this module.
    When an eligible candidate had a higher financial_score but lost on
    execution confidence, names it explicitly (matching this project's
    own worked example: "Candidate A has a higher financial score, but
    Candidate B has substantially stronger execution confidence and
    provides sufficient risk reduction.")."""
    eligible_others = [c for c in candidates if c is not selected and c.eligible]
    if not eligible_others:
        return (
            f"Selected {selected.decision.value}: the only eligible candidate "
            f"(financial_score={selected.financial_score}, execution_score={selected.execution_score})."
        )
    best_financial_other = max(eligible_others, key=lambda c: c.financial_score)
    if best_financial_other.financial_score > selected.financial_score:
        return (
            f"Candidate {best_financial_other.decision.value} has a higher financial score "
            f"({best_financial_other.financial_score} vs {selected.financial_score}), but "
            f"{selected.decision.value} has substantially stronger execution confidence "
            f"({selected.execution_score} vs {best_financial_other.execution_score}) and provides "
            f"sufficient risk reduction (combined_score={selected.combined_score} vs "
            f"{best_financial_other.combined_score})."
        )
    return (
        f"Selected {selected.decision.value}: highest combined score "
        f"({selected.combined_score}) among eligible candidates."
    )


def build_explanation(
    selected: CandidateAction, candidates: list[CandidateAction]
) -> ExecutionConfidenceExplanation:
    """Deterministic structured explanation for SELECT BEST EXECUTABLE
    ACTION. See ExecutionConfidenceExplanation's docstring: every field
    here is read straight from already-computed scores, never invented,
    never LLM-influenced."""
    rejected = [c for c in candidates if c is not selected and not c.eligible]
    execution_factors: dict[str, object] = (
        selected.execution_detail.model_dump() if selected.execution_detail is not None else {}
    )
    return ExecutionConfidenceExplanation(
        selected_action=selected.decision,
        financial_score=selected.financial_score,
        execution_score=selected.execution_score,
        combined_score=selected.combined_score,
        expected_risk_reduction=selected.expected_risk_reduction,
        rejected_candidates=[c.decision.value for c in rejected],
        rejection_reasons={c.decision.value: c.rejection_reason or "" for c in rejected},
        execution_factors=execution_factors,
        selection_reason=_selection_reason(selected, candidates),
    )


def _build_explanation(selected: CandidateAction, candidates: list[CandidateAction]) -> str:
    parts = [
        f"Selected {selected.decision.value} (financial_score={selected.financial_score}, "
        f"execution_score={selected.execution_score}, combined_score={selected.combined_score})."
    ]
    for candidate in candidates:
        if candidate is selected:
            continue
        if candidate.rejection_reason is not None:
            parts.append(f"Rejected {candidate.decision.value}: {candidate.rejection_reason}.")
        else:
            parts.append(
                f"{candidate.decision.value} was executable (combined_score={candidate.combined_score}) "
                "but scored lower."
            )
    parts.append(_selection_reason(selected, candidates))
    return " ".join(parts)


class ExecutionAwareDecisionEngine:
    """Aegis's core differentiator: generates and scores multiple candidate
    actions instead of trusting a single proposal, and never selects a
    candidate that failed policy or simulation — regardless of how good its
    financial score looks.

    Deterministic end to end. Reuses PolicyEngine (no policy-rule
    duplication) and SimulationService (no simulate-before-execute
    duplication) — this class only adds candidate generation and scoring.
    """

    def __init__(self, *, policy_engine: PolicyEngine, simulation_service: SimulationService) -> None:
        self._policy_engine = policy_engine
        self._simulation_service = simulation_service

    def decide(
        self,
        *,
        position: AaveUserAccountData,
        risk: RiskAssessment,
        network: str,
        user: str,
        debt_asset: str,
        collateral_asset: str,
        available_balance: Decimal | None = None,
    ) -> EngineDecision:
        # GENERATE CANDIDATE ACTIONS
        candidates = generate_candidate_actions(
            position,
            risk,
            network=network,
            user=user,
            debt_asset=debt_asset,
            collateral_asset=collateral_asset,
        )

        # EVALUATE FINANCIAL OUTCOME
        for candidate in candidates:
            financial = compute_financial_score(candidate, position)
            candidate.financial_detail = financial
            candidate.financial_score = financial.value

        # EVALUATE EXECUTION FEASIBILITY (cheap pre-check, no simulate call
        # yet — a candidate already known to violate policy or exceed
        # available balance is never simulated)
        for candidate in candidates:
            policy_decision = self._policy_engine.evaluate(candidate_to_intent(candidate))
            execution = compute_execution_score(candidate, policy_decision, available_balance, None)
            candidate.execution_detail = execution
            candidate.execution_score = execution.value
            reason = describe_execution_rejection(execution, policy_decision)
            if reason is not None:
                candidate.rejection_reason = reason

        # SIMULATE CANDIDATES (only those still eligible after the
        # feasibility pre-check above)
        for candidate in candidates:
            if candidate.decision is Decision.DO_NOTHING:
                candidate.simulation_status = SimulationStatus.NOT_APPLICABLE
                continue
            if not candidate.eligible:
                candidate.simulation_status = SimulationStatus.SKIPPED
                continue
            params = build_protocol_action_params(candidate_to_intent(candidate))
            simulation = self._simulation_service.simulate(candidate.protocol_action, params)
            candidate.simulation_result = simulation
            candidate.simulation_status = determine_simulation_status(candidate, simulation)
            policy_decision = self._policy_engine.evaluate(candidate_to_intent(candidate))
            execution = compute_execution_score(candidate, policy_decision, available_balance, simulation)
            candidate.execution_detail = execution
            candidate.execution_score = execution.value
            reason = describe_execution_rejection(execution, policy_decision)
            if reason is not None:
                candidate.rejection_reason = reason

        # REMOVE FAILED/UNSAFE CANDIDATES is implicit: combined_score is
        # computed for every candidate (for a transparent audit trail), but
        # select_best_executable only ever considers `eligible` ones.
        for candidate in candidates:
            if candidate.financial_detail is None or candidate.execution_detail is None:
                raise RuntimeError(
                    "candidate is missing financial_detail/execution_detail — every candidate "
                    "must be scored on both axes before combined_score can be computed"
                )
            combined = compute_combined_score(candidate.financial_detail, candidate.execution_detail)
            candidate.combined_detail = combined
            candidate.combined_score = combined.value

        # SELECT BEST EXECUTABLE ACTION
        selected = select_best_executable(candidates)
        apply_final_status(candidates, selected)
        explanation = _build_explanation(selected, candidates)
        explanation_detail = build_explanation(selected, candidates)
        return EngineDecision(
            selected=selected, candidates=candidates,
            explanation=explanation, explanation_detail=explanation_detail,
        )
