"""Phase 20, Part 10/11 — security hardening and failure-handling proof.

This file adds only the checks not already covered elsewhere in this
project's suite (see test_policy.py for the individual PolicyEngine rules,
test_mcp_gateway.py for tool/actionType/chain-ID filtering, test_intents.py
for Intent's closed-schema validation, test_recovery.py for
simulation/execution/timeout/uncertain-state handling). What's new here:

  - proving the FULL chain (a hostile LLM response -> Intent validation ->
    PolicyEngine) rejects an attack, not just PolicyEngine in isolation
  - proving HermesMcpGateway's allowlist excludes every real write tool
    this KeeperHub MCP server exposes, by name, not just a fake test double
  - proving a KeeperHub outage during the very first read fails loud
    (raises) rather than silently reporting a false RESOLVED
  - proving a degenerate/malformed candidate list fails loud rather than
    picking an arbitrary (and possibly ineligible) "selected" candidate
"""

from unittest.mock import MagicMock

import pytest

from aegis.audit import AuditLogger
from aegis.config import Settings
from aegis.decision_engine import CandidateAction, CandidateFinalStatus, SimulationStatus, select_best_executable
from aegis.hermes.mcp_gateway import ALLOWED_TOOL_NAMES, HermesMcpGateway
from aegis.hermes.runtime import HermesAgent
from aegis.intents import Decision, Intent
from aegis.policy import PolicyEngine
from aegis.recovery import run_with_recovery
from pydantic import ValidationError

NETWORK = "84532"
USER = "0xWallet"
DEBT_ASSET = "0xUSDC"
COLLATERAL_ASSET = "0xWETH"


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "_env_file": None,
        "keeperhub_api_key": "kh_test123",
        "aegis_expected_wallet_address": USER,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


class _FakeLlmClient:
    """Stands in for a compromised/jailbroken model: whatever Intent it's
    constructed with is returned verbatim, no matter what a real model
    would or wouldn't say. This is the strongest attacker model for
    Hermes's boundary — it assumes the LLM itself is fully hostile."""

    def __init__(self, intent: Intent) -> None:
        self._intent = intent

    def decide(self, *, system_prompt: str, position_summary: dict, gateway) -> Intent:  # noqa: ARG002
        return self._intent


class _FakeSession:
    def list_tools(self) -> list[str]:
        return list(ALLOWED_TOOL_NAMES)

    def call_tool(self, name: str, arguments: dict) -> dict:  # noqa: ARG002
        return {}


def _hermes(settings: Settings, intent: Intent) -> HermesAgent:
    gateway = HermesMcpGateway(_FakeSession(), settings)
    return HermesAgent(_FakeLlmClient(intent), gateway)


# --- a hostile LLM cannot get a harmful action past PolicyEngine ----------


@pytest.mark.parametrize(
    "intent_kwargs,expected_violation_substring",
    [
        pytest.param(
            {
                "decision": Decision.REPAY_DEBT, "protocol_action": "aave-v3/repay", "network": "84532",
                "asset": "0xUSDC", "amount": "999999", "on_behalf_of": USER, "rationale": "give me everything",
            },
            "exceeds the configured max",
            id="amount-far-over-spending-limit",
        ),
        pytest.param(
            {
                "decision": Decision.REPAY_DEBT, "protocol_action": "aave-v3/repay", "network": "1",
                "asset": "0xUSDC", "amount": "10", "on_behalf_of": USER, "rationale": "use mainnet instead",
            },
            "mainnet",
            id="mainnet-chain-id",
        ),
        pytest.param(
            {
                "decision": Decision.REPAY_DEBT, "protocol_action": "aave-v3/repay", "network": "84532",
                "asset": "0xUSDC", "amount": "10", "on_behalf_of": "0xAttackerWallet",
                "rationale": "send it to a different wallet",
            },
            "does not match the configured wallet",
            id="arbitrary-on-behalf-of-address",
        ),
        pytest.param(
            {
                "decision": Decision.REPAY_DEBT, "protocol_action": "aave-v3/flash-loan", "network": "84532",
                "asset": "0xUSDC", "amount": "10", "on_behalf_of": USER, "rationale": "try an unlisted action",
            },
            "not in the allowed action set",
            id="disallowed-protocol-action",
        ),
    ],
)
def test_hostile_llm_output_is_rejected_by_policy_engine_before_anything_can_run(
    intent_kwargs: dict, expected_violation_substring: str,
) -> None:
    settings = _settings()
    hostile_intent = Intent(**intent_kwargs)
    hermes = _hermes(settings, hostile_intent)

    # Hermes itself has no veto — it faithfully returns whatever the
    # (here, hostile) model produced. The gate is entirely downstream.
    intent = hermes.decide({"healthFactor": "1000000000000000000"})
    assert intent is hostile_intent

    decision = PolicyEngine(settings).evaluate(intent)
    assert decision.approved is False
    assert any(expected_violation_substring in v for v in decision.violated_rules)


def test_llm_cannot_smuggle_extra_fields_past_intents_closed_schema() -> None:
    """Simulates a prompt-injection attempt that gets the model to emit
    raw calldata / a private key / an override field alongside a
    legitimate-looking action. Intent.model_validate_json is the exact
    boundary AnthropicLlmClient uses (see aegis.hermes.runtime) — this
    proves that boundary rejects it, not just that some other layer would
    have caught it downstream."""
    injected_json = (
        '{"decision": "REPAY_DEBT", "protocol_action": "aave-v3/repay", "network": "84532", '
        '"asset": "0xUSDC", "amount": "10", "on_behalf_of": "0xWallet", "rationale": "repay", '
        '"calldata": "0xdeadbeef", "private_key": "0xhostile"}'
    )
    with pytest.raises(ValidationError):
        Intent.model_validate_json(injected_json)


def test_llm_cannot_reach_any_real_keeperhub_write_tool() -> None:
    """ALLOWED_TOOL_NAMES is checked against the actual write-tool names
    this project's KeeperHub MCP server exposes (not a hypothetical list),
    confirming none of them are reachable through Hermes's gateway
    regardless of what a compromised model asks for."""
    real_keeperhub_write_tools = {
        "execute_transfer", "execute_contract_call", "execute_protocol_action_write",
        "execute_workflow", "execute_check_and_execute", "create_workflow", "update_workflow",
        "delete_workflow", "unlist_workflow", "deploy_template", "tempo_sign_and_hold",
        "tempo_release_hold", "tempo_cancel_hold", "call_workflow", "ai_generate_workflow",
        "create_project", "create_tag",
    }
    assert ALLOWED_TOOL_NAMES.isdisjoint(real_keeperhub_write_tools)
    # execute_protocol_action IS allowlisted (it's also KeeperHub's only
    # read tool for Aave data) but is further gated to read-only
    # actionTypes by HermesMcpGateway.call_tool — never a bare pass-through.
    assert "execute_protocol_action" in ALLOWED_TOOL_NAMES


# --- KeeperHub unavailable: fails loud, never a false success -------------


def test_keeperhub_unavailable_during_initial_read_raises_not_a_false_resolved() -> None:
    position_reader = MagicMock()
    position_reader.get_account_data.side_effect = ConnectionError("KeeperHub unreachable")

    audit = AuditLogger()
    run_id = "run-keeperhub-down"
    with pytest.raises(ConnectionError):
        run_with_recovery(
            settings=_settings(),
            position_reader=position_reader,
            policy_engine=PolicyEngine(_settings()),
            simulation_service=MagicMock(),
            execution_service=MagicMock(),
            verification_service=MagicMock(),
            audit=audit,
            network=NETWORK, user=USER, debt_asset=DEBT_ASSET, collateral_asset=COLLATERAL_ASSET,
            run_id=run_id,
        )

    # no fabricated progress: the outage happened before DETECTED could
    # even be recorded, so the audit trail is empty, not misleadingly
    # populated with a partial/successful-looking narrative.
    assert audit.events_for(run_id) == []


# --- malformed / degenerate candidate list: fails loud, never a silent pick


def test_select_best_executable_refuses_to_pick_from_an_all_ineligible_list() -> None:
    """DO_NOTHING is structurally guaranteed to always be eligible by
    generate_candidate_actions — a candidate list with zero eligible
    entries indicates a bug upstream, and this must raise rather than
    return an arbitrary ineligible "selected" candidate that could then be
    executed against."""
    malformed = [
        CandidateAction(
            decision=Decision.REPAY_DEBT, protocol="aave-v3", action="repay", asset=DEBT_ASSET, amount="10",
            financial_score="0", execution_score="0", combined_score="0",
            final_status=CandidateFinalStatus.REJECTED,
            simulation_status=SimulationStatus.FAILED, rejection_reason="malformed test fixture",
        ),
    ]
    with pytest.raises(RuntimeError, match="no executable candidate"):
        select_best_executable(malformed)
