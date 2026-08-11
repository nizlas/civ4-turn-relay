"""Structured diagnostic events with redaction (FR-012 foundation)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from civ4_turn_relay.domain import REDACTED, redact_known_secrets, redact_structure

_FORBIDDEN_VALUE_KEYS = frozenset({"password", "private_key", "sftp_password"})


def _shorten_hash(value: object) -> object:
    if not isinstance(value, str) or len(value) < 12:
        return value
    return f"{value[:12]}…"


def _sanitize_fields(fields: Mapping[str, object]) -> dict[str, object]:
    redacted = redact_structure(dict(fields))
    if not isinstance(redacted, dict):
        return {}
    sanitized: dict[str, object] = {}
    for key, value in redacted.items():
        lowered = key.lower()
        if lowered in _FORBIDDEN_VALUE_KEYS:
            sanitized[key] = REDACTED
            continue
        if lowered == "sha256" or "sha" in lowered:
            sanitized[key] = _shorten_hash(value)
        else:
            sanitized[key] = value
    return sanitized


@dataclass(frozen=True, slots=True)
class DiagnosticEvent:
    """One redacted diagnostic record for logs or export."""

    name: str
    fields: dict[str, object]
    message: str

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise TypeError("name must be a non-empty string")
        if not isinstance(self.message, str):
            raise TypeError("message must be a string")
        if not isinstance(self.fields, dict):
            raise TypeError("fields must be a dict")


def emit_diagnostic(
    name: str,
    *,
    fields: Mapping[str, object] | None = None,
    message: str = "",
    secret_values: Iterable[str] = (),
) -> DiagnosticEvent:
    """Build a diagnostic event with structure and text redaction applied."""
    raw_fields = {} if fields is None else dict(fields)
    sanitized = _sanitize_fields(raw_fields)
    redacted_message = redact_known_secrets(message, secret_values)
    return DiagnosticEvent(name=name, fields=sanitized, message=redacted_message)
