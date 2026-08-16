"""Aegis command-line interface."""

from __future__ import annotations

import logging
import time

import typer

from aegis.audit import AuditLogger
from aegis.config import get_settings
from aegis.demo_orchestrator import (
    DemoMode,
    DemoOrchestrationError,
    RunHandle,
    start_run,
    wait_for_completion,
)
from aegis.keeperhub import KeeperHubClient
from aegis.logging_config import configure_logging
from aegis.pipeline import run_pipeline
from aegis.preflight import run_preflight

app = typer.Typer(add_completion=False, help="Aegis — autonomous DeFi risk management agent.")

logger = logging.getLogger(__name__)


@app.callback()
def main() -> None:
    """Aegis — autonomous DeFi risk management agent."""


@app.command()
def health() -> None:
    """Check that configuration is valid and KeeperHub is reachable and authenticated."""
    settings = get_settings()
    configure_logging(settings)

    typer.echo(f"KeeperHub base URL : {settings.keeperhub_base_url}")
    typer.echo(f"Allowed chain IDs  : {list(settings.aegis_allowed_chain_ids)}")

    with KeeperHubClient(settings) as client:
        result = client.health_check()

    typer.echo(f"Reachable          : {result.reachable}")
    typer.echo(f"Authenticated      : {result.authenticated}")
    if result.chain_count is not None:
        typer.echo(f"Chains visible     : {result.chain_count}")
    if result.user_id:
        typer.echo(f"Authenticated as   : {result.user_id}")
    if result.detail:
        typer.echo(f"Detail             : {result.detail}")

    if result.ok:
        typer.secho("OK: KeeperHub integration is healthy.", fg=typer.colors.GREEN)
        raise typer.Exit(code=0)

    typer.secho("FAIL: KeeperHub integration is not healthy.", fg=typer.colors.RED)
    raise typer.Exit(code=1)


def _run_pipeline_command(
    *, dry_run: bool, network: str, user: str | None, debt_asset: str | None, collateral_asset: str | None
) -> None:
    settings = get_settings()
    configure_logging(settings)

    user = user or settings.aegis_expected_wallet_address
    debt_asset = debt_asset or settings.aegis_debt_asset
    collateral_asset = collateral_asset or settings.aegis_collateral_asset
    if not user or not debt_asset or not collateral_asset:
        typer.secho(
            "FAIL: --user/--debt-asset/--collateral-asset must be given, or "
            "AEGIS_EXPECTED_WALLET_ADDRESS/AEGIS_DEBT_ASSET/AEGIS_COLLATERAL_ASSET configured.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)

    audit = AuditLogger()
    result = run_pipeline(
        settings=settings,
        network=network,
        user=user,
        debt_asset=debt_asset,
        collateral_asset=collateral_asset,
        available_balance=settings.aegis_available_balance,
        audit=audit,
        dry_run=dry_run,
    )

    typer.echo(f"Dry run     : {result.dry_run}")
    typer.echo(f"Rounds      : {len(result.rounds)}")
    for i, round_result in enumerate(result.rounds, start=1):
        decision = round_result.selected.decision.value if round_result.selected else "n/a"
        typer.echo(f"  Round {i}: state={round_result.final_state.value} selected={decision}")
    typer.echo(f"Final state : {result.final_state.value}")
    typer.echo(f"Resolved    : {result.resolved}")
    if result.stop_reason:
        typer.echo(f"Stop reason : {result.stop_reason}")

    if result.resolved or result.final_state.value in ("READY_TO_EXECUTE", "NO_SAFE_ACTION"):
        raise typer.Exit(code=0)
    raise typer.Exit(code=1)


@app.command()
def demo(
    network: str = typer.Option(..., help="Chain ID to evaluate, e.g. 84532 for Base Sepolia."),
    user: str | None = typer.Option(None, help="Wallet to evaluate. Defaults to AEGIS_EXPECTED_WALLET_ADDRESS."),
    debt_asset: str | None = typer.Option(None, help="Asset to propose repaying. Defaults to AEGIS_DEBT_ASSET."),
    collateral_asset: str | None = typer.Option(
        None, help="Asset to propose supplying. Defaults to AEGIS_COLLATERAL_ASSET."
    ),
) -> None:
    """Run the complete Aegis decision pipeline in dry-run mode.

    READ through SIMULATE runs for real against KeeperHub; EXECUTE is
    never reached, regardless of AEGIS_AUTONOMOUS_EXECUTION_ENABLED. Safe
    to run against a real KeeperHub org and a real Base Sepolia wallet —
    nothing is ever broadcast.
    """
    _run_pipeline_command(
        dry_run=True, network=network, user=user, debt_asset=debt_asset, collateral_asset=collateral_asset
    )


@app.command()
def run(
    network: str = typer.Option(..., help="Chain ID to evaluate, e.g. 84532 for Base Sepolia."),
    user: str | None = typer.Option(None, help="Wallet to evaluate. Defaults to AEGIS_EXPECTED_WALLET_ADDRESS."),
    debt_asset: str | None = typer.Option(None, help="Asset to propose repaying. Defaults to AEGIS_DEBT_ASSET."),
    collateral_asset: str | None = typer.Option(
        None, help="Asset to propose supplying. Defaults to AEGIS_COLLATERAL_ASSET."
    ),
) -> None:
    """Run the complete Aegis decision pipeline for real.

    Real execution still only happens if AEGIS_AUTONOMOUS_EXECUTION_ENABLED=true
    is set — otherwise this behaves identically to `aegis demo` and stops
    after SIMULATE. Use `aegis demo` if you want that guarantee regardless
    of configuration.
    """
    _run_pipeline_command(
        dry_run=False, network=network, user=user, debt_asset=debt_asset, collateral_asset=collateral_asset
    )


def _watch_and_print(handle: RunHandle) -> None:
    """Prints each newly-recorded audit stage as it happens — real backend
    state, never a fake progress animation (the same requirement Phase
    19A's dashboard event stream satisfies, applied here to the terminal)
    — then blocks until the run finishes. A no-op wait for FIXTURE mode,
    which never leaves start_run() running (no real I/O to wait on)."""
    seen = 0
    while True:
        events = handle.audit.all_events()
        for event in events[seen:]:
            typer.echo(f"  [{event.stage}]")
        seen = len(events)
        if not handle.running:
            break
        time.sleep(0.2)
    wait_for_completion(handle)


def _print_run_summary(handle: RunHandle, *, strict: bool) -> None:
    """`strict=True` (live-demo): only RESOLVED counts as success — this
    command claims nothing on your behalf. `strict=False`
    (fixture-demo/live-dry-run): RESOLVED, READY_TO_EXECUTE (dry-run's
    expected stopping point), or NO_SAFE_ACTION also count, matching the
    old `demo`/`run` commands' exit-code contract."""
    typer.echo(f"\nAegis demo run ID : {handle.run_id}")
    typer.echo(f"Mode              : {handle.mode.value}")
    if handle.error:
        typer.secho(f"Error             : {handle.error}", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    result = handle.result
    if result is None:
        typer.secho("No result recorded.", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    typer.echo(f"Rounds            : {len(result.rounds)}")
    for i, round_result in enumerate(result.rounds, start=1):
        decision = round_result.selected.decision.value if round_result.selected else "n/a"
        typer.echo(f"  Round {i}: state={round_result.final_state.value} selected={decision}")
        if round_result.verification is not None:
            typer.echo(f"    KeeperHub execution ID : {round_result.verification.executionId}")
            typer.echo(f"    Execution status       : {round_result.verification.status}")
            typer.echo(f"    Transaction hash       : {round_result.verification.transactionHash}")
            if round_result.verification.transactionHash:
                typer.echo(
                    "    Explorer URL           : "
                    f"https://sepolia.basescan.org/tx/{round_result.verification.transactionHash}"
                )
    typer.echo(f"Final state       : {result.final_state.value}")
    typer.echo(f"Resolved          : {result.resolved}")
    if result.stop_reason:
        typer.echo(f"Stop reason       : {result.stop_reason}")

    if strict:
        if result.resolved:
            typer.secho("\nOK: live transaction executed and independently verified.", fg=typer.colors.GREEN)
            raise typer.Exit(code=0)
        typer.secho(
            "\nDid not reach RESOLVED. Inspect the audit trail above before treating this as a "
            "success — this command never claims success on your behalf.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)

    if result.resolved or result.final_state.value in ("READY_TO_EXECUTE", "NO_SAFE_ACTION"):
        raise typer.Exit(code=0)
    raise typer.Exit(code=1)


@app.command(name="fixture-demo")
def fixture_demo() -> None:
    """MODE 1 — FIXTURE DEMO: the deterministic presentation story, no
    network access at all. REPAY_DEBT fails simulation, Aegis re-plans,
    ADD_COLLATERAL passes, executes, verifies, resolves — every score,
    selection, and audit event comes from the real decision engine /
    PolicyEngine / recovery state machine; only the KeeperHub responses
    feeding them are canned (see aegis.demo_orchestrator.build_fixture_components).
    Safe to run with no .env at all.
    """
    handle = start_run(DemoMode.FIXTURE)
    _watch_and_print(handle)
    _print_run_summary(handle, strict=False)


@app.command(name="live-dry-run")
def live_dry_run() -> None:
    """MODE 2 — LIVE DRY RUN: real KeeperHub reads, real risk/candidate
    scoring, real simulation, real PolicyEngine — but dry_run=True is
    structural here (aegis.pipeline.run_pipeline never reaches EXECUTE
    under it), regardless of AEGIS_AUTONOMOUS_EXECUTION_ENABLED. Requires
    AEGIS_EXPECTED_WALLET_ADDRESS / AEGIS_DEBT_ASSET / AEGIS_COLLATERAL_ASSET
    configured. Identical in effect to `aegis demo`; this name matches the
    Phase 19A mode vocabulary.
    """
    settings = get_settings()
    configure_logging(settings)
    try:
        handle = start_run(DemoMode.LIVE_DRY_RUN, settings=settings)
    except DemoOrchestrationError as exc:
        typer.secho(f"STOP: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    _watch_and_print(handle)
    _print_run_summary(handle, strict=False)


@app.command(name="live-demo")
def live_demo(
    confirm: bool = typer.Option(
        False, "--confirm", help="Explicitly authorize a real, non-simulated Base Sepolia transaction."
    ),
) -> None:
    """MODE 3 — LIVE EXECUTION: run ONE controlled live transaction on Base
    Sepolia through the real Aegis production path (PolicyEngine ->
    simulation -> KeeperHub execution -> verification), preceded by a
    mandatory preflight check.

    Two independent, explicit opt-ins are required before anything can be
    broadcast — this command adds neither shortcut nor override for either:

    1. AEGIS_AUTONOMOUS_EXECUTION_ENABLED=true must already be set in the
       environment (the same master switch `aegis run` depends on).
    2. --confirm must be passed on this command line.

    If preflight fails, or either opt-in is missing, this STOPS before
    aegis.demo_orchestrator.start_run is ever called — no partial attempt,
    no silent fallback to dry-run. Base Sepolia (chain ID 84532) only;
    mainnet stays structurally unreachable regardless of any flag here
    (see aegis.config.KNOWN_MAINNET_CHAIN_IDS and aegis.policy.PolicyEngine).

    Every event this run records also goes to Settings.aegis_audit_log_path
    (a shared JSON-lines file), so the dashboard (GET /api/runs/{run_id})
    can show this run's progress and result even though it started in this
    separate CLI process — there is still no HTTP endpoint that can
    *start* a LIVE_EXECUTION run; see aegis.api's module docstring.
    """
    settings = get_settings()
    configure_logging(settings)

    typer.echo("Running preflight checks...")
    preflight = run_preflight(settings)
    for check in preflight.checks:
        mark = "PASS" if check.passed else "FAIL"
        color = typer.colors.GREEN if check.passed else typer.colors.RED
        typer.secho(f"  [{mark}] {check.name}", fg=color)
        typer.echo(f"         {check.detail}")

    if not preflight.ok:
        typer.secho(
            f"\nSTOP: {len(preflight.failures)} preflight check(s) failed. No transaction was attempted.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)

    typer.secho("\nAll preflight checks passed.", fg=typer.colors.GREEN)

    if not settings.aegis_autonomous_execution_enabled:
        typer.secho(
            "\nSTOP: AEGIS_AUTONOMOUS_EXECUTION_ENABLED is not true. Set it in your environment "
            "to explicitly enable real execution, then re-run this command.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=1)

    if not confirm:
        typer.secho(
            "\nPreflight passed and autonomous execution is enabled, but --confirm was not "
            "given. Nothing was attempted. Re-run with --confirm to actually broadcast a "
            "real Base Sepolia transaction through KeeperHub.",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(code=0)

    try:
        # Every gate above already passed — this is defense-in-depth, the
        # same "re-check what upstream already validated" pattern
        # PolicyEngine itself follows. Should be unreachable; if it ever
        # fires (e.g. something changed between the checks above and now),
        # stopping is still correct.
        handle = start_run(DemoMode.LIVE_EXECUTION, settings=settings, confirm=True, preflight_result=preflight)
    except DemoOrchestrationError as exc:
        typer.secho(f"\nSTOP: {exc}", fg=typer.colors.RED)
        raise typer.Exit(code=1)

    typer.secho("\nProceeding with live execution on Base Sepolia (chain ID 84532)...", fg=typer.colors.YELLOW)
    typer.echo(f"Aegis run ID : {handle.run_id}  (watch live: GET /api/runs/{handle.run_id})")
    _watch_and_print(handle)
    _print_run_summary(handle, strict=True)


if __name__ == "__main__":
    app()
