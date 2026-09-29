"""Tests for the memory configuration models."""

from __future__ import annotations

from nanobot.config.schema import MemoryConfig, MemoryConsolidationConfig


def test_consolidation_defaults() -> None:
    cfg = MemoryConsolidationConfig()

    assert cfg.enabled is True
    assert cfg.interval_h == 2
    schedule = cfg.build_schedule("UTC")
    assert schedule.kind == "every"
    assert schedule.every_ms == 2 * 3_600_000
    assert cfg.describe_schedule() == "every 2h"


def test_consolidation_legacy_cron_override() -> None:
    cfg = MemoryConsolidationConfig.model_validate({"cron": "0 */4 * * *"})

    schedule = cfg.build_schedule("Europe/Berlin")
    assert schedule.kind == "cron"
    assert schedule.expr == "0 */4 * * *"
    assert cfg.describe_schedule() == "cron 0 */4 * * * (legacy)"


def test_consolidation_model_aliases() -> None:
    assert MemoryConsolidationConfig.model_validate({"modelOverride": "big"}).model_override == "big"
    assert MemoryConsolidationConfig.model_validate({"model": "small"}).model_override == "small"
    assert (
        MemoryConsolidationConfig.model_validate({"model_override": "tiny"}).model_override
        == "tiny"
    )


def test_memory_defaults() -> None:
    cfg = MemoryConfig()

    assert cfg.enabled is True
    assert cfg.state_max_chars == 2048
    assert cfg.consolidation.enabled is True
