from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from flask import Flask


class StructuredLogFormatter(logging.Formatter):
    """Emit one compact JSON object per application log line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "event": getattr(record, "event_name", record.getMessage()),
        }
        fields = getattr(record, "event_fields", {})
        if isinstance(fields, dict):
            payload.update(fields)
        return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def configure_structured_logging(app: Flask) -> None:
    if app.config.get("TESTING"):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(StructuredLogFormatter())
    app.logger.handlers.clear()
    app.logger.addHandler(handler)
    app.logger.setLevel(logging.INFO)
    app.logger.propagate = False


def log_event(
    logger: logging.Logger,
    event: str,
    *,
    level: int = logging.INFO,
    **fields: object,
) -> None:
    """Log identifiers and states only; callers must never pass buyer data."""

    logger.log(
        level,
        event,
        extra={"event_name": event, "event_fields": fields},
    )
