"""Structured JSON-line logging (structlog) — the observability backbone.

Requirement: every log line is a single JSON object emitted to stdout, so that
the assessment's observability contract is met from day one. Later phases will
`bind()` domain fields onto loggers — the standard keyvals this project
commits to carrying are: `input_id`, `decision`, `reason`, `action`, `result`,
`error`, `timing_ms`. The processor chain below is written to pass such
arbitrary keyvals through the JSON renderer untouched.
"""

import hashlib
import logging
import sys
from typing import Any

import structlog

_PII_FIELDS = frozenset({
    "email", "phone", "primary_email", "primary_phone",
    "display_name", "name",
})


def _pii_redactor(logger: Any, method_name: str, event_dict: dict) -> dict:
    """Hash any PII field values before they reach the renderer.

    Raw PII remains in the DB for identity resolution, but MUST NOT appear
    in structured log output or evidence exports (FR-10).
    Fields listed in _PII_FIELDS are replaced with their SHA-256 hash
    (first 16 hex chars, prefixed 'sha256:') so logs remain correlatable
    without exposing raw values.
    """
    for field in _PII_FIELDS:
        raw = event_dict.get(field)
        if isinstance(raw, str) and raw:
            hashed = hashlib.sha256(raw.encode()).hexdigest()[:16]
            event_dict[field] = f"sha256:{hashed}"
    return event_dict


def configure_logging(level: str = "INFO") -> None:
    """Configure stdlib logging + structlog for JSON-line output to stdout."""

    level_norm = level.upper()
    logging.basicConfig(stream=sys.stdout, level=level_norm, format="%(message)s")

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            _pii_redactor,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level_norm, logging.INFO)
        ),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a structlog logger bound with the module name."""
    return structlog.get_logger(name or __name__)