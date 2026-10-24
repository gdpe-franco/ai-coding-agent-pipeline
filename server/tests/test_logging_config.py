from __future__ import annotations

import json
import logging

from app.infrastructure.logging_config import JsonFormatter


def test_json_formatter_includes_allowlisted_context_only() -> None:
    record = logging.LogRecord(
        "app.worker", logging.INFO, __file__, 1, "run_execution_finished", (), None
    )
    record.event = "run_execution_finished"
    record.run_id = "run-123"
    record.duration_ms = 1200
    record.model_usage = {"usage": {"total_tokens": 42}}
    record.secret_key = "must-not-be-logged"

    entry = json.loads(JsonFormatter().format(record))

    assert entry["event"] == "run_execution_finished"
    assert entry["run_id"] == "run-123"
    assert entry["duration_ms"] == 1200
    assert entry["model_usage"]["usage"]["total_tokens"] == 42
    assert "secret_key" not in entry
