"""Load global configuration from an explicit .env path and/or mapping.

Never searches arbitrary working directories. Environment values supplied in
``environ`` override values from the optional dotenv file. Secrets are never
logged here.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from civ4_turn_relay.domain import (
    ENV_PREFIX,
    DomainValidationError,
    GlobalConfig,
    global_config_from_env_mapping,
)


def _parse_dotenv_file(path: Path) -> dict[str, str]:
    """Parse a minimal dotenv file via python-dotenv (explicit path only)."""
    from dotenv import dotenv_values

    raw = dotenv_values(dotenv_path=path, interpolate=False)
    parsed: dict[str, str] = {}
    for key, value in raw.items():
        if key is None or value is None:
            continue
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        parsed[key] = value
    return parsed


def load_global_config(
    *,
    dotenv_path: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> GlobalConfig:
    """Load :class:`GlobalConfig` from an optional dotenv file and mapping.

    ``environ`` defaults to a copy of ``os.environ`` when omitted. Values in
    ``environ`` override the dotenv file. Only ``CIV4_RELAY_*`` keys matter
    for parsing; unknown prefixed keys are rejected by the domain parser.
    """
    merged: dict[str, str] = {}
    if dotenv_path is not None:
        path = Path(dotenv_path)
        if not path.is_file():
            raise DomainValidationError(
                "dotenv path does not exist or is not a file",
                field_path="dotenv_path",
            )
        merged.update(_parse_dotenv_file(path))
    env_map = dict(os.environ) if environ is None else dict(environ)
    for key, value in env_map.items():
        if key.startswith(ENV_PREFIX):
            merged[key] = value
    return global_config_from_env_mapping(merged)
