"""Typed response models for the KeeperHub endpoints Aegis currently calls."""

from __future__ import annotations

from pydantic import BaseModel


class KeeperHubUser(BaseModel):
    """Response shape of GET /api/user."""

    id: str
    name: str | None = None
    email: str | None = None
    walletAddress: str | None = None

    model_config = {"extra": "ignore"}


class KeeperHubChain(BaseModel):
    """A single entry from GET /api/chains.

    Note: `id` is KeeperHub's internal record ID (opaque string), distinct
    from `chainId`, the actual numeric chain ID (e.g. 1, 11155111).
    """

    id: str
    chainId: int
    name: str | None = None
    isTestnet: bool | None = None
    isEnabled: bool | None = None

    model_config = {"extra": "ignore"}


class ProtocolActionSimulation(BaseModel):
    """Response shape of a simulate=true protocol-action call."""

    success: bool
    wouldRevert: bool
    gasEstimate: str | None = None
    sponsored: bool | None = None
    simulatedReturnValue: object | None = None
    revertReason: str | None = None

    model_config = {"extra": "ignore"}


class ProtocolActionExecution(BaseModel):
    """Response shape of a real (non-simulated) protocol-action call."""

    executionId: str
    status: str
    transactionHash: str | None = None
    transactionLink: str | None = None

    model_config = {"extra": "ignore"}


class ExecutionStatus(BaseModel):
    """Response shape of the execution-status poll endpoint.

    `unconfirmed` means the transaction is on chain but not yet confirmed —
    it is not terminal and must not be treated as failure or retried.
    """

    executionId: str
    status: str
    transactionHash: str | None = None
    transactionLink: str | None = None
    sponsored: bool | None = None
    error: str | None = None

    model_config = {"extra": "ignore"}

    @property
    def terminal(self) -> bool:
        return self.status in ("completed", "failed")

    @property
    def succeeded(self) -> bool:
        return self.status == "completed" and self.error is None


class HealthCheckResult(BaseModel):
    """Outcome of KeeperHubClient.health_check()."""

    reachable: bool
    authenticated: bool
    base_url: str
    chain_count: int | None = None
    user_id: str | None = None
    detail: str | None = None

    @property
    def ok(self) -> bool:
        return self.reachable and self.authenticated
