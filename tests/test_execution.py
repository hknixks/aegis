from unittest.mock import MagicMock

import pytest

from aegis.execution import (
    ExecutionService,
    SimulationBlockedError,
    SimulationService,
    VerificationService,
    VerificationTimeoutError,
)
from aegis.keeperhub.models import ExecutionStatus, ProtocolActionExecution, ProtocolActionSimulation
from tests.fixtures.keeperhub_payloads import (
    EXECUTE_TRANSFER_RESULT,
    EXECUTION_STATUS_RESULT,
    SIMULATE_TRANSFER_RESULT,
)


def test_simulation_service_returns_typed_result() -> None:
    client = MagicMock()
    client.simulate_protocol_action.return_value = ProtocolActionSimulation.model_validate(
        SIMULATE_TRANSFER_RESULT
    )

    result = SimulationService(client).simulate("aave-v3/repay", {"network": "84532"})

    assert result.success is True
    assert result.wouldRevert is False


def test_execution_service_refuses_without_passing_simulation() -> None:
    client = MagicMock()
    bad_simulation = ProtocolActionSimulation(success=False, wouldRevert=True)

    with pytest.raises(SimulationBlockedError):
        ExecutionService(client).execute("aave-v3/repay", {}, bad_simulation)

    client.execute_protocol_action.assert_not_called()


def test_execution_service_refuses_when_would_revert_even_if_success_true() -> None:
    client = MagicMock()
    would_revert_simulation = ProtocolActionSimulation(success=True, wouldRevert=True)

    with pytest.raises(SimulationBlockedError):
        ExecutionService(client).execute("aave-v3/repay", {}, would_revert_simulation)

    client.execute_protocol_action.assert_not_called()


def test_execution_service_executes_after_passing_simulation() -> None:
    client = MagicMock()
    client.execute_protocol_action.return_value = ProtocolActionExecution.model_validate(
        EXECUTE_TRANSFER_RESULT
    )
    good_simulation = ProtocolActionSimulation.model_validate(SIMULATE_TRANSFER_RESULT)

    result = ExecutionService(client, idempotency_key_factory=lambda: "fixed-key").execute(
        "aave-v3/repay", {"network": "84532"}, good_simulation
    )

    client.execute_protocol_action.assert_called_once_with(
        "aave-v3/repay", {"network": "84532"}, idempotency_key="fixed-key"
    )
    assert result.transactionHash == EXECUTE_TRANSFER_RESULT["transactionHash"]


def test_verification_service_returns_on_first_terminal_status() -> None:
    client = MagicMock()
    client.get_protocol_action_status.return_value = ExecutionStatus.model_validate(
        EXECUTION_STATUS_RESULT
    )

    result = VerificationService(client, sleep_fn=lambda _: None).verify("zkn8vu62dmox0diyzulr0")

    assert result.status == "completed"
    assert result.succeeded is True
    client.get_protocol_action_status.assert_called_once()


def test_verification_service_keeps_polling_on_unconfirmed() -> None:
    client = MagicMock()
    unconfirmed = ExecutionStatus.model_validate({**EXECUTION_STATUS_RESULT, "status": "unconfirmed"})
    completed = ExecutionStatus.model_validate(EXECUTION_STATUS_RESULT)
    client.get_protocol_action_status.side_effect = [unconfirmed, unconfirmed, completed]

    sleeps: list[float] = []
    result = VerificationService(
        client, max_attempts=5, poll_interval_seconds=0.01, sleep_fn=sleeps.append
    ).verify("zkn8vu62dmox0diyzulr0")

    assert result.status == "completed"
    assert client.get_protocol_action_status.call_count == 3
    assert sleeps == [0.01, 0.01]


def test_verification_service_times_out() -> None:
    client = MagicMock()
    unconfirmed = ExecutionStatus.model_validate({**EXECUTION_STATUS_RESULT, "status": "unconfirmed"})
    client.get_protocol_action_status.return_value = unconfirmed

    with pytest.raises(VerificationTimeoutError):
        VerificationService(client, max_attempts=2, sleep_fn=lambda _: None).verify("x")
