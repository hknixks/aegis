"""Deterministic policy engine.

A pure function of (Intent, Settings) -> PolicyDecision. No network calls,
no randomness, no LLM. This is the hard gate between a structured Intent
(possibly LLM-produced) and anything that can simulate or execute onchain.
A violation is always a hard reject — the engine never rewrites or clamps
an intent, so the audit trail always shows exactly what was proposed versus
what was allowed.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from pydantic import BaseModel

from aegis.config import KNOWN_MAINNET_CHAIN_IDS, Settings
from aegis.intents import Decision, Intent

POLICY_VERSION = "v1"


class PolicyDecision(BaseModel):
    approved: bool
    violated_rules: list[str] = []
    policy_version: str = POLICY_VERSION


class PolicyEngine:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def evaluate(self, intent: Intent) -> PolicyDecision:
        violations: list[str] = []

        if intent.decision.value not in self._settings.aegis_allowed_decisions:
            violations.append(
                f"decision '{intent.decision.value}' is not in the allowed decision set"
            )

        if intent.decision is Decision.DO_NOTHING:
            return PolicyDecision(approved=not violations, violated_rules=violations)

        # Everything below is re-checked here even though Intent's own
        # validator already enforces the action shape structurally — the
        # policy engine must not assume upstream validation ran.
        if intent.protocol_action not in self._settings.aegis_allowed_protocol_actions:
            violations.append(
                f"protocol_action '{intent.protocol_action}' is not in the allowed action set"
            )

        network_id: int | None = None
        if intent.network is None:
            violations.append("network is required for a state-changing decision")
        else:
            try:
                network_id = int(intent.network)
            except ValueError:
                violations.append(f"network '{intent.network}' is not a valid chain ID")

        if network_id is not None:
            if network_id in KNOWN_MAINNET_CHAIN_IDS:
                violations.append(
                    f"network {network_id} is a known mainnet chain ID; mainnet is disabled"
                )
            elif network_id not in self._settings.aegis_allowed_chain_ids:
                violations.append(f"network {network_id} is not in the allowed chain ID set")

        expected_wallet = self._settings.aegis_expected_wallet_address
        if expected_wallet is not None and intent.on_behalf_of is not None:
            if intent.on_behalf_of.lower() != expected_wallet.lower():
                violations.append(
                    f"on_behalf_of '{intent.on_behalf_of}' does not match the configured wallet"
                )

        if intent.amount is not None:
            try:
                amount = Decimal(intent.amount)
            except InvalidOperation:
                violations.append(f"amount '{intent.amount}' is not a valid decimal")
            else:
                if amount <= 0:
                    violations.append("amount must be greater than zero")
                elif amount > self._settings.aegis_max_tx_amount:
                    violations.append(
                        f"amount {amount} exceeds the configured max "
                        f"({self._settings.aegis_max_tx_amount})"
                    )

        return PolicyDecision(approved=not violations, violated_rules=violations)
