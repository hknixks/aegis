from decimal import Decimal
from unittest.mock import MagicMock

from aegis.aave import AaveUserAccountData
from aegis.config import Settings
from aegis.decision_engine import (
    CandidateAction,
    CandidateFinalStatus,
    ExecutionAwareDecisionEngine,
    SimulationStatus,
    candidate_to_intent,
    compute_execution_score,
    compute_financial_score,
    describe_execution_rejection,
    determine_simulation_status,
    generate_candidate_actions,
    select_best_executable,
)
from aegis.intents import Decision
from aegis.keeperhub.models import ProtocolActionSimulation
from aegis.policy import PolicyDecision, PolicyEngine
from aegis.risk import assess_health_factor
from tests.fixtures.keeperhub_payloads import (
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


def _risk(settings: Settings, position: AaveUserAccountData):
    return assess_health_factor(position.healthFactor, settings.aegis_health_factor_threshold)


def _sim(*, success: bool = True, would_revert: bool = False, gas_estimate: str | None = None):
    return ProtocolActionSimulation(success=success, wouldRevert=would_revert, gasEstimate=gas_estimate)


def _engine(settings: Settings, simulate_side_effect):
    simulation_service = MagicMock()
    simulation_service.simulate.side_effect = simulate_side_effect
    engine = ExecutionAwareDecisionEngine(
        policy_engine=PolicyEngine(settings), simulation_service=simulation_service
    )
    return engine, simulation_service


def _always_passes(protocol_action: str, params: dict) -> ProtocolActionSimulation:
    return _sim()


# --- candidate generation sanity ---------------------------------------


def test_generate_candidate_actions_at_risk_includes_repay_and_add() -> None:
    settings = _settings()
    position = _position(MOCK_AAVE_ACCOUNT_DATA_AT_RISK)
    risk = _risk(settings, position)

    candidates = generate_candidate_actions(
        position, risk, network=NETWORK, user=USER, debt_asset=DEBT_ASSET, collateral_asset=COLLATERAL_ASSET
    )

    decisions = {c.decision for c in candidates}
    assert decisions == {Decision.DO_NOTHING, Decision.REPAY_DEBT, Decision.ADD_COLLATERAL}


def test_generate_candidate_actions_safe_only_do_nothing() -> None:
    settings = _settings()
    position = _position(MOCK_AAVE_ACCOUNT_DATA_SAFE)
    risk = _risk(settings, position)

    candidates = generate_candidate_actions(
        position, risk, network=NETWORK, user=USER, debt_asset=DEBT_ASSET, collateral_asset=COLLATERAL_ASSET
    )

    assert [c.decision for c in candidates] == [Decision.DO_NOTHING]


# --- required scenario: financially best action is executable ----------


def test_financially_best_action_is_executable() -> None:
    settings = _settings()
    position = _position(MOCK_AAVE_ACCOUNT_DATA_AT_RISK)
    risk = _risk(settings, position)

    generated = generate_candidate_actions(
        position, risk, network=NETWORK, user=USER, debt_asset=DEBT_ASSET, collateral_asset=COLLATERAL_ASSET
    )
    real_candidates = [c for c in generated if c.decision is not Decision.DO_NOTHING]
    for c in real_candidates:
        c.financial_score = compute_financial_score(c, position).value
    best = max(real_candidates, key=lambda c: c.financial_score)

    engine, simulation_service = _engine(settings, _always_passes)
    result = engine.decide(
        position=position,
        risk=risk,
        network=NETWORK,
        user=USER,
        debt_asset=DEBT_ASSET,
        collateral_asset=COLLATERAL_ASSET,
    )

    assert result.selected.decision == best.decision
    assert result.selected.eligible
    assert result.selected.simulation_status is SimulationStatus.PASSED
    assert result.selected.combined_score == max(c.combined_score for c in result.candidates if c.eligible)


# --- required scenario: best action fails simulation, another selected --


def test_financially_best_action_fails_simulation_falls_back() -> None:
    settings = _settings()
    position = _position(MOCK_AAVE_ACCOUNT_DATA_AT_RISK)
    risk = _risk(settings, position)

    # REPAY_DEBT is the financially stronger candidate for this fixture
    # (same expected_risk_reduction as ADD_COLLATERAL, but a smaller
    # capital_cost ratio) — force it to fail simulation.
    def simulate(protocol_action: str, params: dict) -> ProtocolActionSimulation:
        if protocol_action == "aave-v3/repay":
            return _sim(success=True, would_revert=True)
        return _sim()

    engine, simulation_service = _engine(settings, simulate)
    result = engine.decide(
        position=position,
        risk=risk,
        network=NETWORK,
        user=USER,
        debt_asset=DEBT_ASSET,
        collateral_asset=COLLATERAL_ASSET,
    )

    repay = next(c for c in result.candidates if c.decision is Decision.REPAY_DEBT)
    assert repay.simulation_status is SimulationStatus.FAILED
    assert repay.rejection_reason == "simulation failed or would revert"
    assert not repay.eligible

    assert result.selected.decision == Decision.ADD_COLLATERAL
    assert result.selected.eligible
    assert result.selected.simulation_status is SimulationStatus.PASSED


# --- required scenario: all actions fail -> DO_NOTHING ------------------


def test_all_actions_fail_selects_do_nothing() -> None:
    settings = _settings()
    position = _position(MOCK_AAVE_ACCOUNT_DATA_AT_RISK)
    risk = _risk(settings, position)

    def simulate(protocol_action: str, params: dict) -> ProtocolActionSimulation:
        return _sim(success=False)

    engine, _ = _engine(settings, simulate)
    result = engine.decide(
        position=position,
        risk=risk,
        network=NETWORK,
        user=USER,
        debt_asset=DEBT_ASSET,
        collateral_asset=COLLATERAL_ASSET,
    )

    assert result.selected.decision == Decision.DO_NOTHING
    for c in result.candidates:
        if c.decision is not Decision.DO_NOTHING:
            assert not c.eligible
            assert c.simulation_status is SimulationStatus.FAILED


# --- required scenario: action exceeds spending limit --------------------


def test_action_exceeds_spending_limit_rejected_before_simulation() -> None:
    settings = _settings(aegis_max_tx_amount=Decimal("1"))
    position = _position(MOCK_AAVE_ACCOUNT_DATA_AT_RISK)
    risk = _risk(settings, position)

    engine, simulation_service = _engine(settings, _always_passes)
    result = engine.decide(
        position=position,
        risk=risk,
        network=NETWORK,
        user=USER,
        debt_asset=DEBT_ASSET,
        collateral_asset=COLLATERAL_ASSET,
    )

    for c in result.candidates:
        if c.decision is not Decision.DO_NOTHING:
            assert not c.eligible
            assert c.rejection_reason is not None and "policy:" in c.rejection_reason
            assert c.simulation_status is SimulationStatus.SKIPPED
    simulation_service.simulate.assert_not_called()
    assert result.selected.decision == Decision.DO_NOTHING


# --- required scenario: unsupported protocol/action ----------------------


def test_unsupported_action_eliminated_other_candidate_selected() -> None:
    settings = _settings(
        aegis_allowed_protocol_actions=(
            "aave-v3/get-user-account-data",
            "aave-v3/get-user-reserve-data",
            "aave-v3/supply",  # aave-v3/repay deliberately excluded
        )
    )
    position = _position(MOCK_AAVE_ACCOUNT_DATA_AT_RISK)
    risk = _risk(settings, position)

    engine, simulation_service = _engine(settings, _always_passes)
    result = engine.decide(
        position=position,
        risk=risk,
        network=NETWORK,
        user=USER,
        debt_asset=DEBT_ASSET,
        collateral_asset=COLLATERAL_ASSET,
    )

    repay = next(c for c in result.candidates if c.decision is Decision.REPAY_DEBT)
    assert not repay.eligible
    assert "not in the allowed action set" in repay.rejection_reason
    assert repay.simulation_status is SimulationStatus.SKIPPED
    assert repay.final_status is CandidateFinalStatus.REJECTED

    assert result.selected.decision == Decision.ADD_COLLATERAL
    assert result.selected.eligible
    assert result.selected.final_status is CandidateFinalStatus.SELECTED
    supply_amount = next(c for c in result.candidates if c.decision is Decision.ADD_COLLATERAL).amount
    simulation_service.simulate.assert_called_once_with(
        "aave-v3/supply",
        {
            "contractAddress": "0x8bAB6d1b75f19e9eD9fCe8b9BD338844fF79aE27",
            "chainId": NETWORK,
            "functionName": "supply",
            "functionArgs": f'["{COLLATERAL_ASSET}", "{supply_amount}", "{USER}", "0"]',
        },
    )


def test_unsupported_protocol_rejected_same_mechanism_as_unsupported_action() -> None:
    """Distinct from test_unsupported_action_eliminated_other_candidate_selected:
    this project's policy allowlist (aegis_allowed_protocol_actions) is a
    single closed set of 'protocol/action' strings — there is no separate
    per-protocol allowlist. A candidate targeting an unsupported PROTOCOL
    (not just an unsupported action within aave-v3) is rejected through
    the exact same PolicyEngine check, never a separate/duplicated code
    path — confirmed here directly against compute_execution_score."""
    settings = _settings()  # default allowlist: aave-v3 only, no morpho
    policy_engine = PolicyEngine(settings)

    candidate = CandidateAction(
        decision=Decision.REPAY_DEBT,
        protocol="morpho",
        action="repay",
        asset=DEBT_ASSET,
        amount="10",
        network=NETWORK,
        on_behalf_of=USER,
    )
    policy_decision = policy_engine.evaluate(candidate_to_intent(candidate))

    execution = compute_execution_score(candidate, policy_decision, available_balance=None, simulation=None)
    reason = describe_execution_rejection(execution, policy_decision)

    assert policy_decision.approved is False
    assert "not in the allowed action set" in "; ".join(policy_decision.violated_rules)
    assert execution.value == Decimal("0")
    assert execution.protocol_supported is False
    assert execution.action_supported is False  # not independently distinguishable — see ExecutionScore docstring
    assert reason is not None and "policy:" in reason


# --- required scenario: mainnet action ------------------------------------


def test_mainnet_network_rejected_selects_do_nothing() -> None:
    settings = _settings()
    position = _position(MOCK_AAVE_ACCOUNT_DATA_AT_RISK)
    risk = _risk(settings, position)

    engine, simulation_service = _engine(settings, _always_passes)
    result = engine.decide(
        position=position,
        risk=risk,
        network="8453",  # Base mainnet
        user=USER,
        debt_asset=DEBT_ASSET,
        collateral_asset=COLLATERAL_ASSET,
    )

    for c in result.candidates:
        if c.decision is not Decision.DO_NOTHING:
            assert not c.eligible
            assert "mainnet" in c.rejection_reason
    simulation_service.simulate.assert_not_called()
    assert result.selected.decision == Decision.DO_NOTHING


# --- required scenario: insufficient balance ------------------------------


def test_insufficient_balance_eliminates_only_the_unaffordable_candidate() -> None:
    settings = _settings()
    position = _position(MOCK_AAVE_ACCOUNT_DATA_AT_RISK)
    risk = _risk(settings, position)

    generated = generate_candidate_actions(
        position, risk, network=NETWORK, user=USER, debt_asset=DEBT_ASSET, collateral_asset=COLLATERAL_ASSET
    )
    repay_amount = Decimal(next(c.amount for c in generated if c.decision is Decision.REPAY_DEBT))
    add_amount = Decimal(next(c.amount for c in generated if c.decision is Decision.ADD_COLLATERAL))
    assert repay_amount < add_amount  # sanity: fixture produces two different amounts
    available_balance = (repay_amount + add_amount) / 2

    engine, simulation_service = _engine(settings, _always_passes)
    result = engine.decide(
        position=position,
        risk=risk,
        network=NETWORK,
        user=USER,
        debt_asset=DEBT_ASSET,
        collateral_asset=COLLATERAL_ASSET,
        available_balance=available_balance,
    )

    repay = next(c for c in result.candidates if c.decision is Decision.REPAY_DEBT)
    add = next(c for c in result.candidates if c.decision is Decision.ADD_COLLATERAL)
    assert repay.eligible
    assert not add.eligible
    assert add.rejection_reason == "insufficient balance for requested amount"
    assert add.simulation_status is SimulationStatus.SKIPPED
    assert result.selected.decision == Decision.REPAY_DEBT


# --- required scenario: failed simulation ---------------------------------


def test_compute_execution_score_failed_simulation_is_zero_and_ineligible() -> None:
    candidate = CandidateAction(
        decision=Decision.REPAY_DEBT,
        protocol="aave-v3",
        action="repay",
        asset=DEBT_ASSET,
        amount="10",
        network=NETWORK,
        on_behalf_of=USER,
    )
    policy_decision = PolicyDecision(approved=True, violated_rules=[])

    execution = compute_execution_score(
        candidate, policy_decision, available_balance=None,
        simulation=_sim(success=True, would_revert=True),
    )
    reason = describe_execution_rejection(execution, policy_decision)
    candidate.execution_score = execution.value
    candidate.rejection_reason = reason
    candidate.simulation_status = determine_simulation_status(
        candidate, _sim(success=True, would_revert=True)
    )

    assert candidate.simulation_status is SimulationStatus.FAILED
    assert reason == "simulation failed or would revert"
    assert execution.value == Decimal("0")
    assert execution.would_revert is True
    assert not candidate.eligible


# --- required scenario: successful simulation -----------------------------


def test_compute_execution_score_success_applies_gas_penalty() -> None:
    candidate = CandidateAction(
        decision=Decision.REPAY_DEBT,
        protocol="aave-v3",
        action="repay",
        asset=DEBT_ASSET,
        amount="10",
        network=NETWORK,
        on_behalf_of=USER,
    )
    policy_decision = PolicyDecision(approved=True, violated_rules=[])
    simulation = _sim(success=True, would_revert=False, gas_estimate="500000")

    execution = compute_execution_score(
        candidate, policy_decision, available_balance=None, simulation=simulation
    )
    reason = describe_execution_rejection(execution, policy_decision)
    simulation_status = determine_simulation_status(candidate, simulation)

    assert simulation_status is SimulationStatus.PASSED
    assert reason is None
    assert execution.simulation_passed is True
    assert execution.would_revert is False
    assert execution.gas_estimate == Decimal("500000")
    assert execution.value == Decimal("100") - Decimal("500000") / Decimal("1000000")


# --- required scenario: execution score affects final selection ----------


def test_execution_score_can_override_a_higher_financial_score() -> None:
    strong_financial_weak_execution = CandidateAction(
        decision=Decision.REPAY_DEBT,
        financial_score=Decimal("0.5"),
        execution_score=Decimal("10"),
    )
    weak_financial_strong_execution = CandidateAction(
        decision=Decision.ADD_COLLATERAL,
        financial_score=Decimal("0.4"),
        execution_score=Decimal("100"),
    )
    for c in (strong_financial_weak_execution, weak_financial_strong_execution):
        c.combined_score = c.financial_score + c.execution_score / Decimal("100")

    selected = select_best_executable([strong_financial_weak_execution, weak_financial_strong_execution])

    assert selected is weak_financial_strong_execution
    assert weak_financial_strong_execution.financial_score < strong_financial_weak_execution.financial_score
    assert weak_financial_strong_execution.combined_score > strong_financial_weak_execution.combined_score


