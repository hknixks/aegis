"""ONE authoritative entrypoint every demo/live surface calls through.

Phase 19A: before this module, `aegis.cli` and `aegis.api` each built their
own KeeperHub components, their own fixture data, and their own dry-run/
autonomous-execution wiring for what was conceptually the same three modes.
This module is the single place that turns a `DemoMode` into the right
`PipelineComponents` + `dry_run` + run_id + audit trail, so `aegis.cli` and
`aegis.api` become thin callers of `start_run` — never a second
orchestration path, never mode-specific execution logic of their own.

Three modes, matching the hackathon demo's three stages:

- FIXTURE: MagicMock KeeperHub responses, a deterministic canned incident
  (REPAY_DEBT fails simulation -> re-plan -> ADD_COLLATERAL succeeds).
  `build_fixture_components` never constructs a real KeeperHubClient — the
  MagicMock services it returns are never wired to any network client at
  all, so there is no code path from FIXTURE mode to a real write, not
  merely a flag saying so (see `test_fixture_components_never_touch_a_real_client`).
- LIVE_DRY_RUN: real KeeperHub REST calls (real position read, real
  simulation, real PolicyEngine) but `dry_run=True` is passed
  unconditionally — aegis.pipeline.run_pipeline's own dry-run override
  forces EXECUTE to never be reached regardless of
  Settings.aegis_autonomous_execution_enabled.
- LIVE_EXECUTION: the one real, non-simulated Base Sepolia transaction.
  `_authorize_live_execution` requires, all at once: explicit
  `confirm=True`, `Settings.aegis_autonomous_execution_enabled`, and a
  passing `aegis.preflight.run_preflight` — the exact gate
  `aegis live-demo --confirm` already enforced before this module existed;
  this only gives every caller one shared path to it, never a looser one.
  There is deliberately no HTTP endpoint that can reach this mode — see
  aegis.api's module docstring for why.

Every run gets one run_id, generated here once and used as
run_pipeline's first-round run_id. Re-planning rounds after the first
always get their own fresh run_id (aegis.pipeline.run_pipeline's existing
contract: each round is a genuinely new decision) — `handle.audit` holds
every round's events regardless, since each RunHandle owns a dedicated
AuditLogger instance nothing else writes to.

LIVE_DRY_RUN and LIVE_EXECUTION runs execute on a background thread so a
caller (aegis.api) can poll progress via `get_run`/`handle.audit` instead
of blocking on the whole pipeline; `aegis.cli` calls `wait_for_completion`
to block anyway, since a CLI command is expected to print a final result.
FIXTURE runs are synchronous (MagicMock — no real I/O to wait on).

Every AuditLogger here is also given `settings.aegis_audit_log_path`, a
JSON-lines file on disk — so a run started by one process (e.g. the CLI's
one-shot `live-demo --confirm`) can still be polled by another (the
dashboard API server) via `aegis.audit.load_events_for_run`.
"""

from __future__ import annotations

import threading
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from unittest.mock import MagicMock

from aegis.aave import AaveUserAccountData
from aegis.audit import AuditLogger
from aegis.config import Settings, get_settings
from aegis.hermes.runtime import HermesAgent
from aegis.keeperhub.models import ExecutionStatus, ProtocolActionExecution, ProtocolActionSimulation
from aegis.pipeline import PipelineComponents, PipelineResult, build_pipeline_components, run_pipeline
from aegis.policy import PolicyEngine
from aegis.preflight import BASE_SEPOLIA_CHAIN_ID, PreflightResult, run_preflight

logger = logging.getLogger(__name__)


class DemoMode(str, Enum):
    FIXTURE = "fixture"
    LIVE_DRY_RUN = "live_dry_run"
    LIVE_EXECUTION = "live_execution"


class DemoOrchestrationError(Exception):
    """Base class for start_run() precondition failures — never raised for
    something that went wrong inside the pipeline itself (that lands on
    RunHandle.error/result instead); always a gate that stops a run before
    it was ever attempted."""


class LiveConfigMissingError(DemoOrchestrationError):
    """LIVE_DRY_RUN or LIVE_EXECUTION requested without
    AEGIS_EXPECTED_WALLET_ADDRESS / AEGIS_DEBT_ASSET / AEGIS_COLLATERAL_ASSET
    configured."""


class LiveExecutionNotAuthorizedError(DemoOrchestrationError):
    """LIVE_EXECUTION requested without every required gate satisfied.
    `reasons` lists every unmet gate at once, not just the first, so a
    caller (CLI stderr, API error response) can report all of them."""

    def __init__(self, reasons: list[str]) -> None:
        self.reasons = reasons
        super().__init__("; ".join(reasons))


@dataclass
class RunHandle:
    run_id: str
    mode: DemoMode
    audit: AuditLogger
    network: str
    wallet: str | None
    started_at: datetime
    completed_at: datetime | None = None
    result: PipelineResult | None = None
    error: str | None = None
    _thread: threading.Thread | None = field(default=None, repr=False, compare=False)

    @property
    def running(self) -> bool:
        return self.completed_at is None

    @property
    def latest_stage(self) -> str | None:
        events = self.audit.all_events()
        return events[-1].stage if events else None


_REGISTRY: dict[str, RunHandle] = {}
_REGISTRY_LOCK = threading.Lock()


def get_run(run_id: str) -> RunHandle | None:
    """In-memory lookup only — a run started by a different process (e.g.
    the CLI) will not be found here. Callers that need to work either way
    should fall back to aegis.audit.load_events_for_run against the shared
    audit_log_path."""
    with _REGISTRY_LOCK:
        return _REGISTRY.get(run_id)


def wait_for_completion(handle: RunHandle, timeout: float | None = None) -> RunHandle:
    """Blocks until `handle`'s background thread finishes (a no-op for
    FIXTURE mode, which never spawns one). Used by aegis.cli, which — unlike
    aegis.api — wants a final result before it prints anything and exits."""
    if handle._thread is not None:
        handle._thread.join(timeout)
    return handle


# --- FIXTURE mode: deterministic, real Aegis logic, canned KeeperHub data -

FIXTURE_WALLET = "0xDEM0000000000000000000000000000000000000"
FIXTURE_NETWORK = "84532"
FIXTURE_DEBT_ASSET = "0xUSDC-demo"
FIXTURE_COLLATERAL_ASSET = "0xWETH-demo"

_FIXTURE_AT_RISK_POSITION = {
    "totalCollateralBase": "1000000000",
    "totalDebtBase": "733944954",
    "availableBorrowsBase": "16055046",
    "currentLiquidationThreshold": "8000",
    "ltv": "7500",
    "healthFactor": "1090000000000000000",  # 1.09 — matches the demo narrative
}
_FIXTURE_SAFE_POSITION = {
    "totalCollateralBase": "1600000000",
    "totalDebtBase": "733944954",
    "availableBorrowsBase": "466055046",
    "currentLiquidationThreshold": "8000",
    "ltv": "7500",
    "healthFactor": "1740000000000000000",  # comfortably resolved
}
_FIXTURE_EXECUTION = {
    "executionId": "demo-exec-0000000000",
    "status": "completed",
    "transactionHash": "0xdemo00000000000000000000000000000000000000000000000000000000",
    "transactionLink": "https://sepolia.basescan.org/tx/0xdemo00000000000000000000000000000000000000000000000000000000",
}
_FIXTURE_STATUS = {**_FIXTURE_EXECUTION, "sponsored": True, "error": None}


def fixture_settings() -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        keeperhub_api_key="kh_demo0000000000000000000",
        aegis_expected_wallet_address=FIXTURE_WALLET,
        # Safe: this Settings instance is only ever paired with
        # build_fixture_components' MagicMock services (see below) — it is
        # never passed to build_pipeline_components, so it can never
        # construct a real KeeperHubClient.
        aegis_autonomous_execution_enabled=True,
    )


def build_fixture_components(settings: Settings) -> PipelineComponents:
    """The ONE deterministic failure/recovery story every FIXTURE run
    tells: REPAY_DEBT fails simulation, Aegis re-plans, ADD_COLLATERAL
    passes, executes, verifies, resolves. `keeperhub_client` is a bare
    MagicMock — never wired to a KeeperHubClient/httpx at all, so FIXTURE
    mode is structurally incapable of a real write, not merely configured
    not to attempt one."""
    position_reader = MagicMock()
    position_reader.get_account_data.side_effect = [
        AaveUserAccountData.model_validate(_FIXTURE_AT_RISK_POSITION),
        AaveUserAccountData.model_validate(_FIXTURE_SAFE_POSITION),
    ]

    def simulate(protocol_action: str, params: dict) -> ProtocolActionSimulation:
        if protocol_action == "aave-v3/repay":
            return ProtocolActionSimulation(
                success=False, wouldRevert=True, gasEstimate=None,
                revertReason="health factor would remain below the safety threshold",
            )
        return ProtocolActionSimulation(success=True, wouldRevert=False, gasEstimate="180000")

    simulation_service = MagicMock()
    simulation_service.simulate.side_effect = simulate

    execution_service = MagicMock()
    execution_service.execute.return_value = ProtocolActionExecution.model_validate(_FIXTURE_EXECUTION)

    verification_service = MagicMock()
    verification_service.verify.return_value = ExecutionStatus.model_validate(_FIXTURE_STATUS)

    return PipelineComponents(
        keeperhub_client=MagicMock(), position_reader=position_reader, policy_engine=PolicyEngine(settings),
        simulation_service=simulation_service, execution_service=execution_service,
        verification_service=verification_service, owns_client=False,
    )


# --- shared gates -----------------------------------------------------


def _require_live_config(settings: Settings) -> None:
    if not (settings.aegis_expected_wallet_address and settings.aegis_debt_asset and settings.aegis_collateral_asset):
        raise LiveConfigMissingError(
            "AEGIS_EXPECTED_WALLET_ADDRESS / AEGIS_DEBT_ASSET / AEGIS_COLLATERAL_ASSET not configured"
        )


def _authorize_live_execution(
    settings: Settings, *, confirm: bool, preflight_result: PreflightResult | None = None
) -> None:
    """Every gate `aegis live-demo --confirm` already enforced, checked
    together so a caller learns every unmet requirement at once instead of
    fixing them one at a time. Never loosened for API callers — there is no
    parameter here that skips a check."""
    reasons: list[str] = []
    if not confirm:
        reasons.append("explicit confirmation (confirm=true) was not given")
    if not settings.aegis_autonomous_execution_enabled:
        reasons.append("AEGIS_AUTONOMOUS_EXECUTION_ENABLED is not true")
    preflight = preflight_result if preflight_result is not None else run_preflight(settings)
    if not preflight.ok:
        failed = ", ".join(c.name for c in preflight.failures)
        reasons.append(f"preflight failed: {failed}")
    if reasons:
        raise LiveExecutionNotAuthorizedError(reasons)


def _maybe_build_hermes_agent(settings: Settings) -> HermesAgent | None:
    """Constructs a real Hermes agent for LIVE_DRY_RUN/LIVE_EXECUTION when
    both settings.aegis_hermes_enabled and anthropic_api_key are set — off
    (None) by default, which is the unchanged, deterministic-only
    behavior. Construction failure (package not installed, KeeperHub MCP
    unreachable, bad API key) is caught and logged, never raised: Hermes
    is an optional enhancement to candidate generation, not a
    precondition for a live run — see aegis.recovery._consult_hermes for
    the same guarantee at call time."""
    if not (settings.aegis_hermes_enabled and settings.anthropic_api_key):
        return None
    try:
        from aegis.hermes.mcp_gateway import HermesMcpGateway, create_default_session
        from aegis.hermes.runtime import AnthropicLlmClient

        session = create_default_session(settings)
        gateway = HermesMcpGateway(session, settings)
        llm_client = AnthropicLlmClient(settings)
        return HermesAgent(llm_client, gateway)
    except Exception as exc:  # noqa: BLE001 - never blocks a live run; see docstring
        logger.warning("Hermes agent could not be constructed, continuing without it: %s", exc)
        return None


# --- the one authoritative entrypoint ----------------------------------


def start_run(
    mode: DemoMode,
    *,
    settings: Settings | None = None,
    confirm: bool = False,
    preflight_result: PreflightResult | None = None,
) -> RunHandle:
    """The single path every CLI command and API endpoint uses to start an
    Aegis run. Raises DemoOrchestrationError (never starts a thread, never
    registers a handle) if the requested mode's preconditions aren't met —
    a run_id is only ever handed back once a run has actually begun."""
    if mode is DemoMode.FIXTURE:
        run_settings = fixture_settings()
        components = build_fixture_components(run_settings)
        dry_run = False  # safe: components' services are MagicMocks, never a real client — see build_fixture_components
        network, user = FIXTURE_NETWORK, FIXTURE_WALLET
        debt_asset, collateral_asset = FIXTURE_DEBT_ASSET, FIXTURE_COLLATERAL_ASSET
    elif mode is DemoMode.LIVE_DRY_RUN:
        run_settings = settings or get_settings()
        _require_live_config(run_settings)
        if BASE_SEPOLIA_CHAIN_ID not in run_settings.aegis_allowed_chain_ids:
            raise LiveConfigMissingError(
                f"chain ID {BASE_SEPOLIA_CHAIN_ID} (Base Sepolia) is not in AEGIS_ALLOWED_CHAIN_IDS="
                f"{sorted(run_settings.aegis_allowed_chain_ids)} — this project's demo is Base Sepolia only"
            )
        components = build_pipeline_components(run_settings, hermes_agent=_maybe_build_hermes_agent(run_settings))
        dry_run = True  # structural override — aegis.pipeline.run_pipeline never reaches EXECUTE under this
        network = str(BASE_SEPOLIA_CHAIN_ID)
        user = run_settings.aegis_expected_wallet_address
        debt_asset, collateral_asset = run_settings.aegis_debt_asset, run_settings.aegis_collateral_asset
    elif mode is DemoMode.LIVE_EXECUTION:
        run_settings = settings or get_settings()
        _require_live_config(run_settings)
        _authorize_live_execution(run_settings, confirm=confirm, preflight_result=preflight_result)
        components = build_pipeline_components(run_settings, hermes_agent=_maybe_build_hermes_agent(run_settings))
        dry_run = False
        network = str(BASE_SEPOLIA_CHAIN_ID)
        user = run_settings.aegis_expected_wallet_address
        debt_asset, collateral_asset = run_settings.aegis_debt_asset, run_settings.aegis_collateral_asset
    else:
        raise ValueError(f"unknown demo mode {mode!r}")

    run_id = str(uuid.uuid4())
    audit = AuditLogger(path=run_settings.aegis_audit_log_path)
    handle = RunHandle(
        run_id=run_id, mode=mode, audit=audit, network=network, wallet=user,
        started_at=datetime.now(timezone.utc),
    )

    def _execute() -> None:
        try:
            handle.result = run_pipeline(
                settings=run_settings, network=network, user=user or "", debt_asset=debt_asset,
                collateral_asset=collateral_asset, available_balance=run_settings.aegis_available_balance,
                audit=audit, components=components, dry_run=dry_run, run_id=run_id,
            )
        except Exception as exc:  # noqa: BLE001 - surfaced on the handle, never crashes the thread silently
            handle.error = f"{type(exc).__name__}: run did not complete"
        finally:
            handle.completed_at = datetime.now(timezone.utc)

    with _REGISTRY_LOCK:
        _REGISTRY[run_id] = handle

    if mode is DemoMode.FIXTURE:
        # No real I/O (MagicMock) — run inline so callers never need to
        # poll a FIXTURE run to see its result.
        _execute()
    else:
        thread = threading.Thread(target=_execute, daemon=True, name=f"aegis-run-{run_id}")
        handle._thread = thread
        thread.start()

    return handle
