from unittest.mock import MagicMock

import pytest

from aegis.aave import AavePositionReader, build_protocol_action_params
from aegis.intents import Decision, Intent
from tests.fixtures.keeperhub_payloads import (
    MOCK_AAVE_ACCOUNT_DATA_AT_RISK,
    MOCK_AAVE_ACCOUNT_DATA_SAFE,
)


def test_get_account_data_parses_response() -> None:
    client = MagicMock()
    client.call_protocol_action.return_value = {"result": MOCK_AAVE_ACCOUNT_DATA_SAFE}

    reader = AavePositionReader(client)
    data = reader.get_account_data(network="84532", user="0xWallet")

    client.call_protocol_action.assert_called_once_with(
        "aave-v3/get-user-account-data",
        {
            "contractAddress": "0x8bAB6d1b75f19e9eD9fCe8b9BD338844fF79aE27",
            "chainId": "84532",
            "functionName": "getUserAccountData",
            "functionArgs": '["0xWallet"]',
        },
    )
    assert data.healthFactor == MOCK_AAVE_ACCOUNT_DATA_SAFE["healthFactor"]


def test_get_account_data_at_risk_fixture_shape() -> None:
    client = MagicMock()
    client.call_protocol_action.return_value = {"result": MOCK_AAVE_ACCOUNT_DATA_AT_RISK}

    reader = AavePositionReader(client)
    data = reader.get_account_data(network="84532", user="0xWallet")

    assert data.totalDebtBase == "800000000"


def test_build_protocol_action_params_repay() -> None:
    intent = Intent(
        decision=Decision.REPAY_DEBT,
        protocol_action="aave-v3/repay",
        network="84532",
        asset="0xUSDC",
        amount="10",
        on_behalf_of="0xWallet",
        rationale="at risk",
    )
    params = build_protocol_action_params(intent)
    assert params == {
        "contractAddress": "0x8bAB6d1b75f19e9eD9fCe8b9BD338844fF79aE27",
        "chainId": "84532",
        "functionName": "repay(address,uint256,uint256,address)",
        "functionArgs": '["0xUSDC", "10", "2", "0xWallet"]',
    }


def test_build_protocol_action_params_supply() -> None:
    intent = Intent(
        decision=Decision.ADD_COLLATERAL,
        protocol_action="aave-v3/supply",
        network="84532",
        asset="0xWETH",
        amount="5",
        on_behalf_of="0xWallet",
        rationale="add collateral",
    )
    params = build_protocol_action_params(intent)
    assert params == {
        "contractAddress": "0x8bAB6d1b75f19e9eD9fCe8b9BD338844fF79aE27",
        "chainId": "84532",
        "functionName": "supply(address,uint256,address,uint16)",
        "functionArgs": '["0xWETH", "5", "0xWallet", "0"]',
    }


def test_build_protocol_action_params_rejects_do_nothing() -> None:
    intent = Intent(decision=Decision.DO_NOTHING, rationale="nothing to do")
    with pytest.raises(ValueError, match="no Aave V3 action mapping"):
        build_protocol_action_params(intent)
