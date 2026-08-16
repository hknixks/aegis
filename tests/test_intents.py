import pytest
from pydantic import ValidationError

from aegis.intents import Decision, Intent


def test_do_nothing_minimal() -> None:
    intent = Intent(decision=Decision.DO_NOTHING, rationale="health factor is healthy")
    assert intent.protocol_action is None


def test_do_nothing_rejects_action_fields() -> None:
    with pytest.raises(ValidationError):
        Intent(
            decision=Decision.DO_NOTHING,
            protocol_action="aave-v3/repay",
            rationale="should not be allowed",
        )


def test_repay_debt_requires_full_shape() -> None:
    with pytest.raises(ValidationError, match="requires fields"):
        Intent(decision=Decision.REPAY_DEBT, rationale="missing fields")


def test_repay_debt_valid() -> None:
    intent = Intent(
        decision=Decision.REPAY_DEBT,
        protocol_action="aave-v3/repay",
        network="84532",
        asset="0xUSDC",
        amount="10",
        on_behalf_of="0xWallet",
        rationale="health factor below threshold",
    )
    assert intent.decision is Decision.REPAY_DEBT


def test_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        Intent.model_validate(
            {
                "decision": "DO_NOTHING",
                "rationale": "ok",
                "raw_calldata": "0xdeadbeef",
            }
        )
