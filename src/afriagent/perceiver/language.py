"""Language detection with Sheng support.

Uses langdetect as the primary detector with a Sheng marker pre-check,
since langdetect misclassifies Sheng (Kenyan slang) as Swahili.
"""

from __future__ import annotations

import re
from typing import Any

from afriagent.config.logging import get_logger

log = get_logger(__name__)

# ── Sheng markers ─────────────────────────────────────────────────
# These are common Sheng (Kenyan urban slang) words that should NOT
# be classified as standard Swahili.

SHENG_MARKERS = [
    "sasa", "poa", "maze", "fiti", "niaje", "uko", "buda", "dame",
    "ndege", "mzinga", "ngwai", "kihoto", "mbogi", "sonko", "fala",
    "kichwa", "ngoma", "tuma", "teja", "guoko", "kasri", "mresh",
    "niaje", "aje", "vipi", "namna", "bz", "sare", "ndeio",
]

# Swahili markers that are NOT Sheng
SWAHILI_MARKERS = [
    "habari", "nzuri", "sawa", "asante", "tafadhali", "karibu",
    "namba", "akaunti", "malipo", "tatizo", "huduma", "pesa",
    "sana", "ndugu", "rafiki", "jambo", "mambo", "shikamoo",
    "hujambo", "unaendeleaje", "pole", "haraka",
]

# French markers
FRENCH_MARKERS = [
    "bonjour", "merci", "s'il vous plaît", "compte", "paiement",
    "problème", "service", "aide", "salut", "oui", "non",
    "monsieur", "madame", "comment", "pourquoi", "quand",
]

# Hausa markers
HAUSA_MARKERS = [
    "sannu", "na gode", "don Allah", "lafiya", "yaya",
    "ina", "wannan", "aboki", "gaisuwa",
]

# Yoruba markers
YORUBA_MARKERS = [
    "bawo", "e ku", "o se", "jowo", "omo", "alejo",
    "eto", "ise", "owo",
]


def detect_language(text: str) -> str:
    """Detect language with Sheng awareness.

    Priority:
    1. Check for Sheng markers (langdetect misclassifies Sheng as sw)
    2. Check for explicit Swahili markers
    3. Fall back to langdetect
    4. Default to "en"

    Returns:
        Language code: "en", "sw", "sheng", "fr", "ha", "yo", or "other"
    """
    if not text or not text.strip():
        return "en"

    lower = text.lower()
    words = set(re.findall(r'\b\w+\b', lower))

    # 1. Check Sheng markers first (highest priority for Kenya)
    sheng_score = sum(1 for m in SHENG_MARKERS if m in words or m in lower)
    if sheng_score >= 2:
        return "sheng"
    if sheng_score == 1 and len(words) <= 5:
        # Short message with a Sheng marker — likely Sheng
        return "sheng"

    # 2. Check Swahili markers
    sw_score = sum(1 for m in SWAHILI_MARKERS if m in words or m in lower)
    if sw_score >= 2:
        return "sw"

    # 3. Check French markers
    fr_score = sum(1 for m in FRENCH_MARKERS if m in lower)
    if fr_score >= 2:
        return "fr"

    # 4. Check Hausa/Yoruba
    ha_score = sum(1 for m in HAUSA_MARKERS if m in lower)
    if ha_score >= 1:
        return "ha"

    yo_score = sum(1 for m in YORUBA_MARKERS if m in lower)
    if yo_score >= 1:
        return "yo"

    # 5. Fall back to langdetect
    try:
        from langdetect import detect as _detect
        detected = _detect(text)
        # Map langdetect codes to our codes
        lang_map = {
            "sw": "sw",
            "en": "en",
            "fr": "fr",
            "pt": "pt",
            "ha": "ha",
            "yo": "yo",
            "ig": "ig",
            "am": "am",
            "zu": "zu",
            "xh": "xh",
        }
        result = lang_map.get(detected, "other")
        # If langdetect says "sw" but we have Sheng markers, override
        if result == "sw" and sheng_score > 0:
            return "sheng"
        return result
    except ImportError:
        log.debug("langdetect not installed, using marker-based detection only")
    except Exception as e:
        log.debug("langdetect failed", error=str(e))

    # 6. Default
    return "en"


def get_language_name(code: str) -> str:
    """Get human-readable language name from code."""
    names = {
        "en": "English",
        "sw": "Swahili",
        "sheng": "Sheng",
        "fr": "French",
        "ha": "Hausa",
        "yo": "Yoruba",
        "ig": "Igbo",
        "am": "Amharic",
        "zu": "Zulu",
        "xh": "Xhosa",
        "pt": "Portuguese",
        "other": "Other",
    }
    return names.get(code, "Unknown")
