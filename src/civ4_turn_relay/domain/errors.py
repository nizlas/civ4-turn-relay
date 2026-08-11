"""Domain validation errors."""

from __future__ import annotations


class DomainValidationError(ValueError):
    """Raised when domain-level validation rejects a value.

    Messages describe the violated rule and carry a field path for context.
    They intentionally never embed the offending value, so secrets can never
    leak through validation errors.
    """

    def __init__(self, message: str, *, field_path: str | None = None) -> None:
        self.message = message
        self.field_path = field_path
        super().__init__(f"{field_path}: {message}" if field_path else message)

    def with_prefix(self, prefix: str) -> DomainValidationError:
        """Return a copy of this error with ``prefix`` prepended to the path."""
        if not prefix:
            return DomainValidationError(self.message, field_path=self.field_path)
        if self.field_path is None:
            path = prefix
        elif self.field_path.startswith("["):
            path = f"{prefix}{self.field_path}"
        else:
            path = f"{prefix}.{self.field_path}"
        return DomainValidationError(self.message, field_path=path)
