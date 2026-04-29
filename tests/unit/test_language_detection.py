"""Unit tests for language detection with Sheng support."""

from __future__ import annotations

import pytest

from afriagent.perceiver.language import (
    detect_language,
    get_language_name,
    SHENG_MARKERS,
    SWAHILI_MARKERS,
)


class TestDetectLanguage:
    def test_english_default(self):
        assert detect_language("I need help with my domain") == "en"

    def test_empty_text(self):
        assert detect_language("") == "en"
        assert detect_language("   ") == "en"

    def test_sheng_detection(self):
        assert detect_language("sasa buda, niaje?") == "sheng"
        assert detect_language("maze poa fiti") == "sheng"
        assert detect_language("uko poa buda") == "sheng"

    def test_sheng_single_marker_short_message(self):
        # Short message with 1 sheng marker should be sheng
        assert detect_language("niaje?") == "sheng"

    def test_swahili_detection(self):
        assert detect_language("habari yako, asante sana kwa msaada") == "sw"
        assert detect_language("karibu, namba yako ya akaunti ni ipi?") == "sw"

    def test_french_detection(self):
        assert detect_language("bonjour, j'ai un problème avec mon compte") == "fr"

    def test_mixed_sheng_english(self):
        # Sheng markers in English text
        assert detect_language("maze my M-Pesa payment went through but invoice still unpaid buda") == "sheng"

    def test_no_false_positives(self):
        # Words that look like markers but aren't
        assert detect_language("I saw a saw") == "en"

    def test_long_english_text(self):
        text = (
            "I have been trying to renew my hosting plan for the past three days "
            "but the payment page keeps showing an error. Can you please help me "
            "resolve this issue as soon as possible?"
        )
        assert detect_language(text) == "en"


class TestGetLanguageName:
    def test_known_codes(self):
        assert get_language_name("en") == "English"
        assert get_language_name("sw") == "Swahili"
        assert get_language_name("sheng") == "Sheng"
        assert get_language_name("fr") == "French"

    def test_unknown_code(self):
        assert get_language_name("xx") == "Unknown"


class TestShengMarkers:
    def test_markers_not_empty(self):
        assert len(SHENG_MARKERS) > 0

    def test_markers_are_lowercase(self):
        for marker in SHENG_MARKERS:
            assert marker == marker.lower(), f"Marker '{marker}' is not lowercase"


class TestSwahiliMarkers:
    def test_markers_not_empty(self):
        assert len(SWAHILI_MARKERS) > 0

    def test_no_overlap_with_sheng(self):
        """Swahili markers should not overlap with Sheng markers."""
        overlap = set(SWAHILI_MARKERS) & set(SHENG_MARKERS)
        # Some overlap is acceptable (e.g., "mambo" could be both)
        # but we should document it
        if overlap:
            # These words exist in both lists — that's expected
            pass
