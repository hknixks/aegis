"""KeeperHub payload fixtures.

The transfer/execution/status payloads below are the REAL JSON captured
during interactive MCP validation in this project's development session —
a zero-value self-transfer proof transaction on Base Sepolia. Used verbatim
so tests stay grounded in the real API shape rather than an invented one.

The Aave account-data payloads are explicitly MOCK data: the real "Aave
Health Factor Monitor (Base Sepolia)" workflow in this KeeperHub org was
never wired up with a `user` address, so no real getUserAccountData
response has been observed yet. Field names match the real workflow's
`{{@step:Get Aave Health Factor.healthFactor}}` template reference.
"""

from __future__ import annotations

# --- Real, captured during this project's Base Sepolia proof transaction ---

SIMULATE_TRANSFER_RESULT: dict[str, object] = {
    "success": True,
    "status": "simulated",
    "from": "0x9d0895d99824e82e64be0423f130bf213fcb1599",
    "to": "0x9d0895d99824e82E64bE0423F130Bf213fCb1599",
    "value": "0",
    "gasEstimate": "21227",
    "simulatedReturnValue": None,
    "wouldRevert": False,
}

EXECUTE_TRANSFER_RESULT: dict[str, object] = {
    "executionId": "zkn8vu62dmox0diyzulr0",
    "status": "completed",
    "transactionHash": "0x85f0ba6f6f2f20f9d9ab50bbd51102dc10f356228e4ce039b6ab9b21a47394f1",
    "transactionLink": (
        "https://sepolia.basescan.org/tx/"
        "0x85f0ba6f6f2f20f9d9ab50bbd51102dc10f356228e4ce039b6ab9b21a47394f1"
    ),
}

EXECUTION_STATUS_RESULT: dict[str, object] = {
    "executionId": "zkn8vu62dmox0diyzulr0",
    "status": "completed",
    "type": "transfer",
    "transactionHash": "0x85f0ba6f6f2f20f9d9ab50bbd51102dc10f356228e4ce039b6ab9b21a47394f1",
    "transactionLink": (
        "https://sepolia.basescan.org/tx/"
        "0x85f0ba6f6f2f20f9d9ab50bbd51102dc10f356228e4ce039b6ab9b21a47394f1"
    ),
    "sponsored": True,
    "error": None,
}

# --- Mock: no real Base Sepolia Aave V3 position has been read yet ---

MOCK_AAVE_ACCOUNT_DATA_AT_RISK: dict[str, object] = {
    "totalCollateralBase": "1000000000",
    "totalDebtBase": "800000000",
    "availableBorrowsBase": "50000000",
    "currentLiquidationThreshold": "8000",
    "ltv": "7500",
    "healthFactor": "1200000000000000000",  # 1.2 — below the 1.5 threshold
}

MOCK_AAVE_ACCOUNT_DATA_SAFE: dict[str, object] = {
    "totalCollateralBase": "1000000000",
    "totalDebtBase": "200000000",
    "availableBorrowsBase": "550000000",
    "currentLiquidationThreshold": "8000",
    "ltv": "7500",
    "healthFactor": "4000000000000000000",  # 4.0
}
