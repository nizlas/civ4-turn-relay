"""Pure helpers over the ordered human player list (INV-11)."""

from __future__ import annotations

from civ4_turn_relay.domain import DomainValidationError, Manifest, validate_player_id


def next_human_player_id(
    manifest: Manifest, *, after_player_id: str | None = None
) -> str:
    """Return the next human in relay order, wrapping from last to first.

    AI civilizations never appear in ``manifest.players``. Defaults to the
    player after ``manifest.current_player_id``.
    """
    if not manifest.players:
        raise DomainValidationError(
            "must list at least one human player", field_path="players"
        )
    pivot = manifest.current_player_id if after_player_id is None else after_player_id
    validate_player_id(pivot, field_path="after_player_id")
    ids = [player.id for player in manifest.players]
    try:
        index = ids.index(pivot)
    except ValueError:
        raise DomainValidationError(
            "must be the ID of a listed player",
            field_path="after_player_id",
        ) from None
    return ids[(index + 1) % len(ids)]
