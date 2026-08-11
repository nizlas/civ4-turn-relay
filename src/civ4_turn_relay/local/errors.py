"""Typed local-store failures (missing / corrupt / unsupported / I/O)."""

from __future__ import annotations


class LocalStoreError(Exception):
    """Base class for local persistence failures."""

    def __init__(self, message: str, *, path: str | None = None) -> None:
        self.message = message
        self.path = path
        super().__init__(f"{path}: {message}" if path else message)


class LocalStoreMissingError(LocalStoreError):
    """Raised when an expected durable document is absent."""


class LocalStoreCorruptError(LocalStoreError):
    """Raised when a document exists but is not a valid schema-v1 object."""


class LocalStoreUnsupportedSchemaError(LocalStoreError):
    """Raised when a document declares an unsupported schema version."""


class LocalStoreIOError(LocalStoreError):
    """Raised when filesystem I/O fails before a successful atomic commit."""
