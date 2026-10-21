"""User-facing response style settings and prompt guidance.

These values live on generation presets and travel with the runtime, so a
session's tone follows the preset it selected rather than a single global
personality.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

BaseStyle = Literal[
    "professional",
    "friendly",
    "candid",
    "seductive",
    "efficient",
    "cynical",
]
WarmthLevel = Literal["very_cold", "cold", "neutral", "warm", "very_warm"]
IntensityLevel = Literal["more", "balanced", "less"]

DEFAULT_BASE_STYLE: BaseStyle = "friendly"
DEFAULT_WARMTH: WarmthLevel = "neutral"
DEFAULT_INTENSITY: IntensityLevel = "balanced"

BASE_STYLE_GUIDANCE: dict[str, tuple[str, str]] = {
    "professional": (
        "Professional",
        "Polished and precise. Use complete sentences, standard grammar, and a "
        "measured tone. Avoid slang, filler, and overly casual phrasing.",
    ),
    "friendly": (
        "Friendly",
        "Warm and chatty. Use contractions and conversational phrasing, "
        "acknowledge the person, and keep the exchange easygoing.",
    ),
    "candid": (
        "Candid",
        "Direct and encouraging. State the main point plainly, then support it. "
        "Be honest about problems without being harsh.",
    ),
    "seductive": (
        "Seductive",
        "Flirtatious, playful, and suggestive. Use deliberate pacing, implication, "
        "and confident charm; keep it tasteful and never explicit.",
    ),
    "efficient": (
        "Efficient",
        "Concise and plain. Answer first, use short sentences and lists, and omit "
        "pleasantries and filler.",
    ),
    "cynical": (
        "Cynical",
        "Critical and sarcastic. Use dry wit and skeptical framing while staying "
        "accurate and useful; never insult the user.",
    ),
}

WARMTH_GUIDANCE: dict[str, tuple[str, str]] = {
    "very_cold": (
        "Very cold",
        "Extra robotic and cold. No pleasantries or emotional framing; "
        "transactional and detached.",
    ),
    "cold": (
        "Cold",
        "Cold and robotic. Minimal warmth, brief acknowledgements, and "
        "professional distance.",
    ),
    "neutral": (
        "Neutral",
        "Neutral. Matter-of-fact and courteous; match the user's level of warmth "
        "rather than adding your own.",
    ),
    "warm": (
        "Warm",
        "Warming and personable. Acknowledge feelings, use encouraging framing, "
        "and sound genuinely engaged.",
    ),
    "very_warm": (
        "Very warm",
        "Extra warming and personable. Be actively supportive and affectionate in "
        "phrasing, and check in on how the user is doing when it fits.",
    ),
}

ENTHUSIASM_GUIDANCE: dict[str, tuple[str, str]] = {
    "more": (
        "More",
        "More enthusiastic. Sound energetic and upbeat; use emphasis and "
        "occasional exclamation marks, but do not oversell.",
    ),
    "balanced": (
        "Balanced",
        "Balanced. Show interest without performing excitement.",
    ),
    "less": (
        "Less",
        "Less enthusiastic. Stay calm, understated, and matter-of-fact.",
    ),
}

EMOJI_GUIDANCE: dict[str, tuple[str, str]] = {
    "more": (
        "More",
        "Use emojis generously (roughly one per short paragraph) when they fit "
        "the tone.",
    ),
    "balanced": (
        "Balanced",
        "Use emojis sparingly, only when they add meaning or warmth.",
    ),
    "less": (
        "Less",
        "Avoid emojis unless the user uses them first.",
    ),
}


@dataclass(frozen=True, slots=True)
class StyleSettings:
    """Tone controls applied to user-facing replies."""

    base_style: BaseStyle = DEFAULT_BASE_STYLE
    warmth: WarmthLevel = DEFAULT_WARMTH
    enthusiasm: IntensityLevel = DEFAULT_INTENSITY
    emojis: IntensityLevel = DEFAULT_INTENSITY

    def prompt_vars(self) -> dict[str, str]:
        """Return labels and guidance for the system-prompt template."""
        base_label, base_guidance = BASE_STYLE_GUIDANCE[self.base_style]
        warmth_label, warmth_guidance = WARMTH_GUIDANCE[self.warmth]
        enthusiasm_label, enthusiasm_guidance = ENTHUSIASM_GUIDANCE[self.enthusiasm]
        emoji_label, emoji_guidance = EMOJI_GUIDANCE[self.emojis]
        return {
            "base_style_label": base_label,
            "base_style_guidance": base_guidance,
            "warmth_label": warmth_label,
            "warmth_guidance": warmth_guidance,
            "enthusiasm_label": enthusiasm_label,
            "enthusiasm_guidance": enthusiasm_guidance,
            "emoji_label": emoji_label,
            "emoji_guidance": emoji_guidance,
        }
