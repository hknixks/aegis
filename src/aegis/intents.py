"""Structured agent intent model.

This is the sole interface between Hermes (the LLM decision layer, not
implemented in this phase) and the rest of Aegis. An Intent is plain data —
it carries no callable, no KeeperHub client handle, and no way to reach
onchain execution on its own. Everything after an Intent is produced must
pass through the deterministic policy engine (aegis.policy) before it can
be simulated or executed.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, model_validator


class Decision(str, Enum):
    DO_NOTHING = "DO_NOTHING"
    REPAY_DEBT = "REPAY_DEBT"
    ADD_COLLATERAL = "ADD_COLLATERAL"


class Intent(BaseModel):
    """A structured, closed-shape action proposal.

    ``extra="forbid"`` is deliberate: this model is the trust boundary
    between the LLM and everything that can touch KeeperHub, so it must
    reject any field an LLM tries to smuggle in that isn't explicitly
    modeled here (e.g. raw calldata, a different action type).
    """

    model_config = {"extra": "forbid"}

    decision: Decision
    protocol_action: str | None = Field(
        default=None,
        description="e.g. 'aave-v3/repay'. Required unless decision is DO_NOTHING.",
    )
    network: str | None = Field(default=None, description="Chain ID as a string, e.g. '84532'.")
    asset: str | None = Field(default=None, description="Asset address or symbol.")
    amount: str | None = Field(default=None, description="Decimal string, human units.")
    on_behalf_of: str | None = Field(
        default=None, description="Wallet address the action applies to."
    )
    rationale: str = Field(description="Why this decision was made, for the audit trail.")
    based_on_health_factor: str | None = Field(
        default=None,
        description="The health factor (human units) this decision was based on, if any.",
    )
    source: str = Field(default="hermes")

    @model_validator(mode="after")
    def _check_action_fields(self) -> "Intent":
        if self.decision is Decision.DO_NOTHING:
            if any([self.protocol_action, self.network, self.asset, self.amount, self.on_behalf_of]):
                raise ValueError(
                    "DO_NOTHING must not carry protocol_action/network/asset/amount/on_behalf_of"
                )
            return self

        required = (
            ("protocol_action", self.protocol_action),
            ("network", self.network),
            ("asset", self.asset),
            ("amount", self.amount),
            ("on_behalf_of", self.on_behalf_of),
        )
        missing = [name for name, value in required if not value]
        if missing:
            raise ValueError(f"{self.decision.value} requires fields: {', '.join(missing)}")
        return self
