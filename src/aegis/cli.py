"""Aegis command-line interface."""

from __future__ import annotations

import logging

import typer

from aegis.config import get_settings
from aegis.keeperhub import KeeperHubClient
from aegis.logging_config import configure_logging

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


if __name__ == "__main__":
    app()
