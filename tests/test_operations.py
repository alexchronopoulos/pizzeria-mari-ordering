from __future__ import annotations

import json
import logging

from app.operations import StructuredLogFormatter, log_event


def test_identifier_only_event_logging_is_compact_json() -> None:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="checkout_created",
        args=(),
        exc_info=None,
    )
    record.event_name = "checkout_created"
    record.event_fields = {"square_order_id": "ORDER-1"}

    payload = json.loads(StructuredLogFormatter().format(record))

    assert payload["event"] == "checkout_created"
    assert payload["square_order_id"] == "ORDER-1"


def test_log_event_adds_structured_fields(caplog) -> None:
    logger = logging.getLogger("operations-test")
    with caplog.at_level(logging.INFO, logger=logger.name):
        log_event(logger, "checkout_completed", square_order_id="ORDER-2")

    record = caplog.records[-1]
    assert record.event_name == "checkout_completed"
    assert record.event_fields == {"square_order_id": "ORDER-2"}
