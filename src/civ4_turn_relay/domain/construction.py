"""Helpers for validating and canonicalizing direct model construction.

Frozen dataclasses only freeze attribute assignment. Nested collections and
nested object types must still be checked (and collections canonicalized into
immutable tuples) so every constructible instance is valid and deeply
immutable with respect to caller-owned lists.
"""

from __future__ import annotations

from civ4_turn_relay.domain.errors import DomainValidationError


def require_optional_string(value: object, *, field_path: str) -> str | None:
    """Require ``value`` to be ``str | None`` without embedding the value."""
    if value is not None and not isinstance(value, str):
        raise DomainValidationError("expected a string or null", field_path=field_path)
    return value


def require_instance[T](value: object, expected: type[T], *, field_path: str) -> T:
    """Require ``value`` to be an instance of ``expected``."""
    if not isinstance(value, expected):
        raise DomainValidationError(
            f"expected a {expected.__name__} instance",
            field_path=field_path,
        )
    return value


def require_optional_instance[T](
    value: object, expected: type[T], *, field_path: str
) -> T | None:
    """Require ``value`` to be ``expected | None``."""
    if value is None:
        return None
    return require_instance(value, expected, field_path=field_path)


def canonicalize_tuple[T](
    value: object,
    item_type: type[T],
    *,
    field_path: str,
    item_label: str,
) -> tuple[T, ...]:
    """Copy a sequence into an immutable tuple of ``item_type`` instances.

    Accepts ``list`` or ``tuple`` inputs so callers may pass either, but never
    retains the caller-owned collection. Wrong container or item types raise
    :class:`DomainValidationError` with a useful field path.
    """
    if not isinstance(value, list | tuple):
        raise DomainValidationError(
            f"expected a sequence of {item_label}",
            field_path=field_path,
        )
    items: list[T] = []
    for index, item in enumerate(value):
        if not isinstance(item, item_type):
            raise DomainValidationError(
                f"expected a {item_label}",
                field_path=f"{field_path}[{index}]",
            )
        items.append(item)
    return tuple(items)
