"""Storage exception taxonomy for the adapter port.

These errors report primitive storage outcomes so protocol code can distinguish
not-found, conflicts, wrong kinds, capability gaps, and transport failures.
They may carry validated relative paths, but never credentials or host details.
"""

from __future__ import annotations


class StorageError(Exception):
    """Base class for all storage-port failures."""

    def __init__(self, message: str, *, path: str | None = None) -> None:
        self.message = message
        self.path = path
        super().__init__(f"{path}: {message}" if path else message)


class StorageInvalidPathError(StorageError):
    """Raised when a path fails containment / shape validation before mutation."""


class StorageNotFoundError(StorageError):
    """Raised when a required file or directory does not exist."""


class StorageAlreadyExistsError(StorageError):
    """Raised when an exclusive create/publish finds an existing destination."""


class StorageWrongKindError(StorageError):
    """Raised when a path is a file but a directory was required, or vice versa."""


class StorageNotEmptyError(StorageError):
    """Raised when removing a directory that still has children."""


class StorageCapabilityError(StorageError):
    """Raised when a required adapter capability is unavailable.

    Raised before any mutation. Callers MUST NOT fall back to a non-atomic
    substitute.
    """


class StorageTransportError(StorageError):
    """Raised for injected fake failures or equivalent transport interruptions."""
