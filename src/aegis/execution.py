"""Simulation, execution, and verification services.

These three services implement the contract this project requires for any
onchain write: every write is simulated first, every real execute is
idempotent, and every success is verified against KeeperHub's own
on-chain-reconciled status. They are protocol-agnostic — they operate on a
protocol_action string and a params dict, not on Aave specifically. Use
aegis.aave.build_protocol_action_params to go from an Intent to those.
"""

from __future__ import annotations

import time
import uuid
from typing import Callable

from aegis.keeperhub.client import KeeperHubClient
from aegis.keeperhub.models import ExecutionStatus, ProtocolActionExecution, ProtocolActionSimulation


class SimulationBlockedError(Exception):
    """Raised when execute() is called without a passing prior simulation."""


class VerificationTimeoutError(Exception):
    """Raised when an execution doesn't reach a terminal status within budget."""


class SimulationService:
    def __init__(self, client: KeeperHubClient) -> None:
        self._client = client

    def simulate(self, protocol_action: str, params: dict[str, object]) -> ProtocolActionSimulation:
        return self._client.simulate_protocol_action(protocol_action, params)


class ExecutionService:
    """Executes a protocol action, but only given a simulation that passed.

    This is a structural enforcement of "every write action must be
    simulated before execution" — there is no code path in this service
    that reaches KeeperHub's write call without one.
    """

    def __init__(
        self,
        client: KeeperHubClient,
        idempotency_key_factory: Callable[[], str] | None = None,
    ) -> None:
        self._client = client
        self._make_key = idempotency_key_factory or (lambda: f"aegis-{uuid.uuid4()}")

    def execute(
        self,
        protocol_action: str,
        params: dict[str, object],
        simulation: ProtocolActionSimulation,
    ) -> ProtocolActionExecution:
        if not simulation.success or simulation.wouldRevert:
            raise SimulationBlockedError(
                f"refusing to execute {protocol_action}: simulation success={simulation.success} "
                f"wouldRevert={simulation.wouldRevert}"
            )
        idempotency_key = self._make_key()
        return self._client.execute_protocol_action(
            protocol_action, params, idempotency_key=idempotency_key
        )


class VerificationService:
    """Polls KeeperHub for the on-chain-reconciled outcome of an execution.

    `unconfirmed` is explicitly not terminal — the transaction is on chain
    but not yet confirmed, so this keeps polling rather than treating it as
    a failure or re-sending the execute call.
    """

    def __init__(
        self,
        client: KeeperHubClient,
        *,
        max_attempts: int = 10,
        poll_interval_seconds: float = 2.0,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client
        self._max_attempts = max_attempts
        self._poll_interval_seconds = poll_interval_seconds
        self._sleep = sleep_fn

    def verify(self, execution_id: str) -> ExecutionStatus:
        for attempt in range(self._max_attempts):
            status = self._client.get_protocol_action_status(execution_id)
            if status.terminal:
                return status
            if attempt < self._max_attempts - 1:
                self._sleep(self._poll_interval_seconds)
        raise VerificationTimeoutError(
            f"execution {execution_id} did not reach a terminal status after "
            f"{self._max_attempts} polls"
        )

    def check_status_once(self, execution_id: str) -> ExecutionStatus:
        """A single, non-polling status fetch — whatever KeeperHub reports
        right now, terminal or not. Used by recovery flows that need to
        inspect the current status after a timeout or an unexpected error
        without resuming the normal polling loop (which would just wait
        again rather than letting the caller decide now). Never resends
        or re-executes anything; it only reads."""
        return self._client.get_protocol_action_status(execution_id)
