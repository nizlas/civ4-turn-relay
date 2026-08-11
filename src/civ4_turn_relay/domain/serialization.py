"""Deterministic JSON serialization and strict JSON mapping readers.

Canonical form (protocol §3.3): UTF-8 without BOM, lexicographically sorted
keys, two-space indentation with stable separators, LF newlines, and exactly
one trailing newline.

The strict readers implement schema-v1 parsing: missing, unexpected, or
mistyped fields raise :class:`DomainValidationError`. JSON booleans are
rejected wherever an integer is required (``bool`` subclasses ``int`` in
Python and must not slip through).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping

from civ4_turn_relay.domain.errors import DomainValidationError

_UTF8_BOM = b"\xef\xbb\xbf"


def to_canonical_json_bytes(value: object) -> bytes:
    """Serialize ``value`` to deterministic canonical UTF-8 JSON bytes."""
    text = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        separators=(",", ": "),
        allow_nan=False,
    )
    return text.encode("utf-8") + b"\n"


def parse_json_object_bytes(data: bytes) -> Mapping[str, object]:
    """Parse UTF-8 JSON bytes whose top-level value must be an object."""
    if data.startswith(_UTF8_BOM):
        raise DomainValidationError("JSON document must not contain a UTF-8 BOM")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise DomainValidationError("document is not valid UTF-8") from None
    try:
        parsed: object = json.loads(text)
    except json.JSONDecodeError:
        raise DomainValidationError("document is not valid JSON") from None
    if not isinstance(parsed, dict):
        raise DomainValidationError("top-level JSON value must be an object")
    return parsed


def join_path(parent: str, key: str) -> str:
    """Join a parent field path and a key into a dotted field path."""
    return f"{parent}.{key}" if parent else key


def check_exact_keys(
    mapping: Mapping[str, object],
    required: Iterable[str],
    *,
    optional: Iterable[str] = (),
    path: str = "",
) -> None:
    """Require exactly ``required`` keys (plus optionally ``optional``)."""
    present = {str(key) for key in mapping}
    required_set = set(required)
    missing = required_set - present
    if missing:
        raise DomainValidationError(
            "required field is missing",
            field_path=join_path(path, sorted(missing)[0]),
        )
    unexpected = present - required_set - set(optional)
    if unexpected:
        raise DomainValidationError(
            "unexpected field",
            field_path=join_path(path, sorted(unexpected)[0]),
        )


def _get(mapping: Mapping[str, object], key: str, path: str) -> object:
    if key not in mapping:
        raise DomainValidationError(
            "required field is missing", field_path=join_path(path, key)
        )
    return mapping[key]


def get_string(mapping: Mapping[str, object], key: str, *, path: str = "") -> str:
    value = _get(mapping, key, path)
    if not isinstance(value, str):
        raise DomainValidationError(
            "expected a string", field_path=join_path(path, key)
        )
    return value


def get_optional_string(
    mapping: Mapping[str, object], key: str, *, path: str = ""
) -> str | None:
    value = _get(mapping, key, path)
    if value is not None and not isinstance(value, str):
        raise DomainValidationError(
            "expected a string or null", field_path=join_path(path, key)
        )
    return value


def get_integer(mapping: Mapping[str, object], key: str, *, path: str = "") -> int:
    value = _get(mapping, key, path)
    if isinstance(value, bool) or not isinstance(value, int):
        raise DomainValidationError(
            "expected an integer (JSON booleans are not integers)",
            field_path=join_path(path, key),
        )
    return value


def get_boolean(
    mapping: Mapping[str, object],
    key: str,
    *,
    path: str = "",
    default: bool | None = None,
) -> bool:
    if key not in mapping:
        if default is not None:
            return default
        raise DomainValidationError(
            "required field is missing", field_path=join_path(path, key)
        )
    value = mapping[key]
    if not isinstance(value, bool):
        raise DomainValidationError(
            "expected a boolean", field_path=join_path(path, key)
        )
    return value


def get_array(
    mapping: Mapping[str, object], key: str, *, path: str = ""
) -> tuple[object, ...]:
    value = _get(mapping, key, path)
    if not isinstance(value, list | tuple):
        raise DomainValidationError(
            "expected an array", field_path=join_path(path, key)
        )
    return tuple(value)


def get_object(
    mapping: Mapping[str, object], key: str, *, path: str = ""
) -> Mapping[str, object]:
    value = _get(mapping, key, path)
    if not isinstance(value, Mapping):
        raise DomainValidationError(
            "expected an object", field_path=join_path(path, key)
        )
    return value


def get_optional_object(
    mapping: Mapping[str, object], key: str, *, path: str = ""
) -> Mapping[str, object] | None:
    value = _get(mapping, key, path)
    if value is not None and not isinstance(value, Mapping):
        raise DomainValidationError(
            "expected an object or null", field_path=join_path(path, key)
        )
    return value
