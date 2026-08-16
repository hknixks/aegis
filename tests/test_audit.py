import json
from pathlib import Path

from aegis.audit import AuditLogger


def test_record_appends_in_memory_event() -> None:
    logger = AuditLogger()
    event = logger.record("run-1", "READ", network="84532")

    assert event.run_id == "run-1"
    assert event.stage == "READ"
    assert event.detail == {"network": "84532"}
    assert logger.events_for("run-1") == [event]


def test_record_writes_jsonl_file(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    logger = AuditLogger(path=path)

    logger.record("run-1", "READ")
    logger.record("run-1", "POLICY_CHECK", approved=True)

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["stage"] == "READ"
    second = json.loads(lines[1])
    assert second["detail"] == {"approved": True}


def test_events_for_filters_by_run_id() -> None:
    logger = AuditLogger()
    logger.record("run-1", "READ")
    logger.record("run-2", "READ")

    assert len(logger.events_for("run-1")) == 1
