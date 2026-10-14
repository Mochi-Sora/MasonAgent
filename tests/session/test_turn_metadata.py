"""Caller-supplied turn ids on inbound metadata."""

from __future__ import annotations

from mason.session.turn_metadata import TURN_ID_METADATA_KEY, supplied_turn_id


def test_supplied_turn_id_reads_the_metadata_key() -> None:
    assert supplied_turn_id({TURN_ID_METADATA_KEY: "turn-abc"}) == "turn-abc"


def test_supplied_turn_id_ignores_missing_or_invalid_values() -> None:
    assert supplied_turn_id(None) is None
    assert supplied_turn_id({}) is None
    assert supplied_turn_id({TURN_ID_METADATA_KEY: ""}) is None
    assert supplied_turn_id({TURN_ID_METADATA_KEY: "   "}) is None
    assert supplied_turn_id({TURN_ID_METADATA_KEY: 42}) is None
