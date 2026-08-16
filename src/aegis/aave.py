"""Aave V3 position reading and action-param construction.

This module owns all Aave-specific shapes so aegis.keeperhub stays
protocol-agnostic and a second protocol can be added later without
touching it, the KeeperHub adapter, or the policy engine.
"""

from __future__ import annotations

import json

from pydantic import BaseModel

from aegis.intents import Decision, Intent
from aegis.keeperhub.client import KeeperHubClient

AAVE_GET_USER_ACCOUNT_DATA = "aave-v3/get-user-account-data"
AAVE_REPAY = "aave-v3/repay"
AAVE_SUPPLY = "aave-v3/supply"

_DECISION_TO_ACTION = {
    Decision.REPAY_DEBT: AAVE_REPAY,
    Decision.ADD_COLLATERAL: AAVE_SUPPLY,
}

# Aave V3 Pool proxy address per chain ID, from the official address book
# (https://github.com/aave-dao/aave-address-book) — KeeperHub's REST API has
# no 'aave-v3/...' action endpoint (see aegis.keeperhub.client's module
# docstring), so every Aave call here goes through KeeperHub's generic
# /api/execute/contract-call against this contract directly.
_POOL_ADDRESS_BY_CHAIN_ID: dict[int, str] = {
    11155111: "0x6Ae43d3271ff6888e7Fc43Fd7321a503ff738951",  # Ethereum Sepolia
    84532: "0x8bAB6d1b75f19e9eD9fCe8b9BD338844fF79aE27",  # Base Sepolia
    421614: "0xBfC91D59fdAA134A4ED45f7B584cAf96D7792Eff",  # Arbitrum Sepolia
}

# Aave V3 IPool.repay's interestRateMode: 1=Stable (deprecated), 2=Variable.
# KeeperHub's own 'aave-v3/repay' action schema lists this as optional,
# defaulting to variable-rate debt — matched here since Aegis calls the
# Pool contract directly instead of that abstraction.
_VARIABLE_RATE_MODE = "2"
_DEFAULT_REFERRAL_CODE = "0"


def _pool_address(network: str) -> str:
    try:
        chain_id = int(network)
    except ValueError as exc:
        raise ValueError(f"invalid network/chain id {network!r}") from exc
    address = _POOL_ADDRESS_BY_CHAIN_ID.get(chain_id)
    if address is None:
        raise ValueError(f"no known Aave V3 Pool address for chain id {chain_id}")
    return address


class AaveUserAccountData(BaseModel):
    """Response shape of aave-v3/get-user-account-data (Aave V3
    Pool.getUserAccountData).

    Field names, and the presence of `healthFactor` specifically, are
    confirmed against a real KeeperHub workflow inspected in this project
    ("Aave Health Factor Monitor (Base Sepolia)"), which templates
    `{{@step:Get Aave Health Factor.healthFactor}}` from this exact action.
    """

    totalCollateralBase: str
    totalDebtBase: str
    availableBorrowsBase: str
    currentLiquidationThreshold: str
    ltv: str
    healthFactor: str

    model_config = {"extra": "ignore"}


class AavePositionReader:
    """Reads an Aave V3 account position through KeeperHub.

    Read-only — this class never simulates or executes anything. The
    underlying action (`aave-v3/get-user-account-data`) requires no wallet
    credentials, per KeeperHub's own action schema.
    """

    def __init__(self, client: KeeperHubClient) -> None:
        self._client = client

    def get_account_data(self, network: str, user: str) -> AaveUserAccountData:
        raw = self._client.call_protocol_action(
            AAVE_GET_USER_ACCOUNT_DATA,
            {
                "contractAddress": _pool_address(network),
                "chainId": network,
                "functionName": "getUserAccountData",
                "functionArgs": json.dumps([user]),
            },
        )
        # KeeperHub decodes a multi-value view return into a {name: value}
        # object under "result", keyed by the Solidity return param names —
        # confirmed via a live read against Base Sepolia.
        return AaveUserAccountData.model_validate(raw["result"])


def build_protocol_action_params(intent: Intent) -> dict[str, str]:
    """Map a REPAY_DEBT/ADD_COLLATERAL Intent to a KeeperHub
    /api/execute/contract-call body against the Aave V3 Pool contract.

    Raises ValueError for anything this module doesn't know how to
    translate — callers must not fall back to guessing a shape.
    """
    expected_action = _DECISION_TO_ACTION.get(intent.decision)
    if expected_action is None:
        raise ValueError(f"no Aave V3 action mapping for decision {intent.decision.value}")

    if intent.protocol_action != expected_action:
        raise ValueError(
            f"intent.protocol_action '{intent.protocol_action}' does not match expected "
            f"'{expected_action}' for decision {intent.decision.value}"
        )

    if not (intent.network and intent.asset and intent.amount and intent.on_behalf_of):
        # Intent's own validator already guarantees this for non-DO_NOTHING
        # decisions; this is a defensive re-check, not the primary guard.
        raise ValueError("intent is missing required fields for an Aave V3 action")

    # IPool.repay(address asset, uint256 amount, uint256 interestRateMode,
    # address onBehalfOf) / IPool.supply(address asset, uint256 amount,
    # address onBehalfOf, uint16 referralCode) — exact positional order
    # from Aave V3's IPool interface. Full signatures, not just the bare
    # name: this Pool contract also exposes a packed-calldata overload
    # (repay(bytes32) / supply(bytes32)) for gas-optimized callers, so
    # "repay"/"supply" alone is ambiguous — confirmed directly against
    # the real API (KeeperHub's ABI resolution rejects the bare name with
    # "ambiguous function description"). Never send just the bare name.
    if intent.decision is Decision.REPAY_DEBT:
        function_name = "repay(address,uint256,uint256,address)"
        function_args = [intent.asset, str(intent.amount), _VARIABLE_RATE_MODE, intent.on_behalf_of]
    else:
        function_name = "supply(address,uint256,address,uint16)"
        function_args = [intent.asset, str(intent.amount), intent.on_behalf_of, _DEFAULT_REFERRAL_CODE]

    return {
        "contractAddress": _pool_address(intent.network),
        "chainId": intent.network,
        "functionName": function_name,
        "functionArgs": json.dumps(function_args),
    }
