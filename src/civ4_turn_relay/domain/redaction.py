"""Secret-redaction helpers for logs and diagnostics (FR-012 foundation).

All helpers are pure: they never mutate the caller's input and perform no
I/O.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

REDACTED = "[REDACTED]"

_SENSITIVE_TOKENS = (
    "password",
    "passwd",
    "passphrase",
    "secret",
    "token",
    "private_key",
    "privatekey",
    "api_key",
    "apikey",
    "credential",
)


def is_sensitive_field_name(name: str) -> bool:
    """Return whether a field name looks like it holds a secret."""
    normalized = name.lower().replace("-", "_").replace(" ", "_")
    return any(token in normalized for token in _SENSITIVE_TOKENS)


def redact_structure(value: object) -> object:
    """Return a copy of nested mappings/sequences with secrets redacted.

    Values (including whole subtrees) stored under sensitive field names are
    replaced with :data:`REDACTED`. The input is never mutated.
    """
    if isinstance(value, Mapping):
        return {
            str(key): (
                REDACTED
                if is_sensitive_field_name(str(key))
                else redact_structure(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, tuple):
        return tuple(redact_structure(item) for item in value)
    if isinstance(value, list):
        return [redact_structure(item) for item in value]
    return value


def redact_known_secrets(text: str, secret_values: Iterable[str]) -> str:
    """Replace every occurrence of each known secret value in ``text``.

    Longer secrets are replaced first so overlapping values cannot leave
    fragments behind. Empty values are ignored.
    """
    secrets = sorted({value for value in secret_values if value}, key=len)
    for secret in reversed(secrets):
        text = text.replace(secret, REDACTED)
    return text
