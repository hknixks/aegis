from decimal import Decimal

import pytest

from aegis.config import Settings
from aegis.intents import Decision, Intent
from aegis.policy import PolicyEngine


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        keeperhub_api_key="kh_test123",
        aegis_expected_wallet_address="0xWallet",
    )


def test_do_nothing_always_approved(settings: Settings) -> None:
    intent = Intent(decision=Decision.DO_NOTHING, rationale="ok")
    decision = PolicyEngine(settings).evaluate(intent)
    assert decision.approved is True


def test_valid_repay_approved(settings: Settings) -> None:
    intent = Intent(
        decision=Decision.REPAY_DEBT,
        protocol_action="aave-v3/repay",
        network="84532",
        asset="0xUSDC",
        amount="10",
        on_behalf_of="0xWallet",
        rationale="at risk",
    )
    decision = PolicyEngine(settings).evaluate(intent)
    assert decision.approved is True
    assert decision.violated_rules == []


def test_unlisted_protocol_action_rejected(settings: Settings) -> None:
    intent = Intent(
        decision=Decision.REPAY_DEBT,
        protocol_action="uniswap/swap",
        network="84532",
        asset="0xUSDC",
        amount="10",
        on_behalf_of="0xWallet",
        rationale="not allowed",
    )
    decision = PolicyEngine(settings).evaluate(intent)
    assert decision.approved is False
    assert any("not in the allowed action set" in rule for rule in decision.violated_rules)


def test_mainnet_chain_rejected_even_if_smuggled_into_allowlist(settings: Settings) -> None:
    # model_copy bypasses field validators on purpose: this proves the
    # policy engine's own mainnet check is a real independent gate, not
    # something that only works because Settings' constructor validator
    # happens to run first.
    settings = settings.model_copy(update={"aegis_allowed_chain_ids": (1, 84532)})
    intent = Intent(
        decision=Decision.REPAY_DEBT,
        protocol_action="aave-v3/repay",
        network="1",
        asset="0xUSDC",
        amount="10",
        on_behalf_of="0xWallet",
        rationale="should still be blocked",
    )
    decision = PolicyEngine(settings).evaluate(intent)
    assert decision.approved is False
    assert any("mainnet" in rule for rule in decision.violated_rules)


def test_wallet_mismatch_rejected(settings: Settings) -> None:
    intent = Intent(
        decision=Decision.ADD_COLLATERAL,
        protocol_action="aave-v3/supply",
        network="84532",
        asset="0xUSDC",
        amount="10",
        on_behalf_of="0xSomeoneElse",
        rationale="wrong wallet",
    )
    decision = PolicyEngine(settings).evaluate(intent)
    assert decision.approved is False
    assert any("does not match the configured wallet" in rule for rule in decision.violated_rules)


def test_amount_over_cap_rejected(settings: Settings) -> None:
    settings = settings.model_copy(update={"aegis_max_tx_amount": Decimal("5")})
    intent = Intent(
        decision=Decision.REPAY_DEBT,
        protocol_action="aave-v3/repay",
        network="84532",
        asset="0xUSDC",
        amount="10",
        on_behalf_of="0xWallet",
        rationale="too big",
    )
    decision = PolicyEngine(settings).evaluate(intent)
    assert decision.approved is False
    assert any("exceeds the configured max" in rule for rule in decision.violated_rules)


def test_unlisted_decision_rejected(settings: Settings) -> None:
    settings = settings.model_copy(update={"aegis_allowed_decisions": ("DO_NOTHING",)})
    intent = Intent(decision=Decision.DO_NOTHING, rationale="ok")
    decision = PolicyEngine(settings).evaluate(intent)
    assert decision.approved is True

    intent2 = Intent(
        decision=Decision.REPAY_DEBT,
        protocol_action="aave-v3/repay",
        network="84532",
        asset="0xUSDC",
        amount="10",
        on_behalf_of="0xWallet",
        rationale="not allowed by this config",
    )
    decision2 = PolicyEngine(settings).evaluate(intent2)
    assert decision2.approved is False
    assert any("not in the allowed decision set" in rule for rule in decision2.violated_rules)
