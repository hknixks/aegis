"""Append-only structured audit trail for the Aegis decision/execution loop.

Distinct from operational logging (aegis.logging_config): this is meant to
be replayed and cross-referenced against KeeperHub's own executionId /
transactionHash, independent of whatever log level is configured.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class AuditEvent(BaseModel):
    run_id: str
    stage: str
    timestamp: datetime
    detail: dict[str, Any] = {}


class AuditLogger:
    """Records one AuditEvent per pipeline stage.

    Always kept in memory for the lifetime of the logger; optionally
    appended to a JSON-lines file for persistence across process runs.
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path else None
        self._events: list[AuditEvent] = []

    def record(self, run_id: str, stage: str, **detail: Any) -> AuditEvent:
        event = AuditEvent(
            run_id=run_id, stage=stage, timestamp=datetime.now(timezone.utc), detail=detail
        )
        self._events.append(event)
        logger.info("audit run=%s stage=%s detail=%s", run_id, stage, detail)
        if self._path is not None:
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(event.model_dump_json() + "\n")
        return event

    def events_for(self, run_id: str) -> list[AuditEvent]:
        return [event for event in self._events if event.run_id == run_id]

    def all_events(self) -> list[AuditEvent]:
        """Every event this logger has recorded, across every run_id, in
        the order recorded. Used by callers (e.g. aegis.api's dashboard)
        that need the full trail of a multi-round run or of a run that
        failed before its final run_id could be reported back."""
        return list(self._events)


def load_events_for_run(path: Path | str, run_id: str) -> list[AuditEvent]:
    """Read events for one run_id from a JSON-lines audit file written by a
    DIFFERENT process's AuditLogger (see aegis.demo_orchestrator) — e.g. the
    dashboard API polling a run that `aegis live-demo --confirm` started in
    its own process. Missing file means no events yet, not an error."""
    file_path = Path(path)
    if not file_path.exists():
        return []
    events: list[AuditEvent] = []
    with file_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            event = AuditEvent.model_validate_json(line)
            if event.run_id == run_id:
                events.append(event)
    return events
