"""Preflight checks required before any live (real, non-simulated) Aegis run.

Used by `aegis live-demo` (see aegis.cli) to gate the one controlled live
Base Sepolia transaction the Phase 20 hackathon demo performs. This module
never executes, simulates, or signs anything itself — it only asks
KeeperHub (REST and MCP) and this project's own Settings whether the
production path is actually ready, and reports each answer individually so
a failure is diagnosable rather than a single opaque "not ready".

If any check fails, the caller must STOP. This module does not enforce
that itself (it has no side effects to enforce against) — aegis.cli.live_demo
is the one place that turns PreflightResult.ok into an actual go/no-go.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from aegis.config import KNOWN_MAINNET_CHAIN_IDS, Settings
from aegis.hermes.mcp_gateway import McpSession, create_default_session
from aegis.keeperhub import KeeperHubClient

# The only chain this project is allowed to touch for a live run. Checked
# independently of (in addition to, never instead of) Settings' own
# mainnet-blocking validators.
BASE_SEPOLIA_CHAIN_ID = 84532


@dataclass
class PreflightCheck:
    name: str
    passed: bool
    detail: str


@dataclass
class PreflightResult:
    checks: list[PreflightCheck] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.checks) > 0 and all(c.passed for c in self.checks)

    @property
    def failures(self) -> list[PreflightCheck]:
        return [c for c in self.checks if not c.passed]


def run_preflight(
    settings: Settings,
    *,
    keeperhub_client: KeeperHubClient | None = None,
    mcp_session: McpSession | None = None,
) -> PreflightResult:
    """Runs every check required before a live run. Read-only: this never
    calls simulate/execute/transfer on either the REST or MCP client."""
    checks: list[PreflightCheck] = []

    wallet = settings.aegis_expected_wallet_address
    checks.append(
        PreflightCheck(
            "wallet address configured",
            wallet is not None,
            wallet or "AEGIS_EXPECTED_WALLET_ADDRESS is not set",
        )
    )

    allowed = set(settings.aegis_allowed_chain_ids)
    mainnet_hit = allowed & KNOWN_MAINNET_CHAIN_IDS
    chain_ok = BASE_SEPOLIA_CHAIN_ID in allowed and not mainnet_hit
    checks.append(
        PreflightCheck(
            "chain ID 84532 (Base Sepolia) allowed, no mainnet in allowlist",
            chain_ok,
            f"AEGIS_ALLOWED_CHAIN_IDS={sorted(allowed)}"
            + (f"; BLOCKED: mainnet chain IDs present: {sorted(mainnet_hit)}" if mainnet_hit else ""),
        )
    )

    client = keeperhub_client
    owns_client = client is None
    if client is None:
        client = KeeperHubClient(settings)
    try:
        try:
            health = client.health_check()
        except Exception as exc:  # noqa: BLE001 - reported as a failed check, not raised
            detail = f"{type(exc).__name__}: could not complete KeeperHub health check"
            checks.append(PreflightCheck("KeeperHub API authentication active", False, detail))
            checks.append(PreflightCheck("Base Sepolia visible on KeeperHub account", False, "skipped — health check failed"))
            for name in (
                "KeeperHub simulation tool available",
                "KeeperHub execution tool available",
                "KeeperHub transaction status tool available",
            ):
                checks.append(PreflightCheck(name, False, "skipped — health check failed"))
        else:
            checks.append(
                PreflightCheck(
                    "KeeperHub API authentication active",
                    health.ok,
                    health.detail or f"reachable={health.reachable} authenticated={health.authenticated}",
                )
            )
            try:
                chains = client.list_chains()
                chain_ids = {c.chainId for c in chains}
                checks.append(
                    PreflightCheck(
                        "Base Sepolia visible on KeeperHub account",
                        BASE_SEPOLIA_CHAIN_ID in chain_ids,
                        f"KeeperHub reports chain IDs: {sorted(chain_ids)}",
                    )
                )
            except Exception as exc:  # noqa: BLE001 - reported as a failed check, not raised
                checks.append(
                    PreflightCheck(
                        "Base Sepolia visible on KeeperHub account",
                        False,
                        f"{type(exc).__name__}: could not list chains",
                    )
                )

            # KeeperHub's REST API scopes simulate/execute/status calls to
            # the same organization API key as health_check — there is no
            # separate per-endpoint permission to probe without actually
            # calling call_protocol_action/execute_protocol_action, which
            # this module must never do. Availability is therefore reported
            # as "the authenticated session that would carry these calls is
            # healthy", not as an independent per-tool probe.
            for name in (
                "KeeperHub simulation tool available",
                "KeeperHub execution tool available",
                "KeeperHub transaction status tool available",
            ):
                checks.append(
                    PreflightCheck(
                        name,
                        health.ok,
                        "inferred from the authenticated REST session — KeeperHub scopes "
                        "simulate/execute/status to the same organization API key",
                    )
                )
    finally:
        if owns_client:
            client.close()

    session = mcp_session
    try:
        if session is None:
            session = create_default_session(settings)
        diagnostics = session.diagnostics()  # type: ignore[union-attr]
    except AttributeError:
        checks.append(PreflightCheck("KeeperHub MCP connected", False, "session has no diagnostics() method"))
        checks.append(PreflightCheck("KeeperHub MCP authenticated", False, "skipped — could not connect"))
    except Exception as exc:  # noqa: BLE001 - reported as a failed check, not raised
        checks.append(PreflightCheck("KeeperHub MCP connected", False, f"{type(exc).__name__}: could not connect"))
        checks.append(PreflightCheck("KeeperHub MCP authenticated", False, "skipped — could not connect"))
    else:
        checks.append(PreflightCheck("KeeperHub MCP connected", diagnostics.reachable, diagnostics.detail or diagnostics.endpoint))
        checks.append(
            PreflightCheck(
                "KeeperHub MCP authenticated",
                diagnostics.authenticated,
                diagnostics.detail or f"{diagnostics.tool_count} tools visible via MCP",
            )
        )

    return PreflightResult(checks=checks)
