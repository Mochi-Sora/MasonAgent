"""Tests for the response style settings."""

from __future__ import annotations

from typing import get_args

from mason.config.schema import GenerationPresetConfig
from mason.style import BASE_STYLE_GUIDANCE, BaseStyle, StyleSettings


def test_every_base_style_has_guidance() -> None:
    assert set(get_args(BaseStyle)) == set(BASE_STYLE_GUIDANCE)


def test_charming_style_renders_its_guidance() -> None:
    variables = StyleSettings(base_style="charming").prompt_vars()

    assert variables["base_style_label"] == "Charming"
    assert "socially graceful" in variables["base_style_guidance"]


def test_generation_preset_accepts_charming() -> None:
    assert GenerationPresetConfig(base_style="charming").base_style == "charming"
