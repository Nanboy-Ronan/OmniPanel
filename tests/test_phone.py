"""Tests for app/utils/phone.py — pure phone-matching helpers, no DB required."""
from __future__ import annotations

from app.utils.phone import (
    fuzzy_fingerprint,
    is_full_cn_mobile,
    is_jd_masked_phone,
    jd_mask_fingerprint,
    normalize_phone,
)


class TestNormalizePhone:
    def test_strips_non_digit_non_star_characters(self):
        assert normalize_phone("138-0000-1111") == "13800001111"

    def test_keeps_asterisks(self):
        assert normalize_phone("1******6198") == "1******6198"

    def test_none_for_empty_or_none(self):
        assert normalize_phone(None) is None
        assert normalize_phone("") is None
        assert normalize_phone("   ") is None


class TestIsFullCnMobile:
    def test_valid_11_digit_mobile(self):
        assert is_full_cn_mobile("13800001111") is True

    def test_rejects_masked_phone(self):
        assert is_full_cn_mobile("1******6198") is False

    def test_rejects_wrong_length(self):
        assert is_full_cn_mobile("1380000111") is False
        assert is_full_cn_mobile("138000011112") is False

    def test_rejects_landline_style_prefix(self):
        assert is_full_cn_mobile("10800001111") is False

    def test_none_input(self):
        assert is_full_cn_mobile(None) is False


class TestIsJdMaskedPhone:
    def test_matches_jd_mask_format(self):
        assert is_jd_masked_phone("1******6198") is True

    def test_rejects_full_phone(self):
        assert is_jd_masked_phone("13800001111") is False

    def test_rejects_wrong_star_count(self):
        assert is_jd_masked_phone("1***6198") is False

    def test_none_input(self):
        assert is_jd_masked_phone(None) is False


class TestFingerprints:
    def test_jd_mask_fingerprint_extracts_first_digit_and_last_4(self):
        assert jd_mask_fingerprint("1******6198") == "1-6198"

    def test_jd_mask_fingerprint_none_for_non_masked(self):
        assert jd_mask_fingerprint("13800001111") is None

    def test_fuzzy_fingerprint_extracts_same_shape_from_full_number(self):
        assert fuzzy_fingerprint("13800006198") == "1-6198"

    def test_fuzzy_fingerprint_none_for_masked(self):
        assert fuzzy_fingerprint("1******6198") is None

    def test_matching_fingerprints_are_comparable(self):
        """A JD masked phone and a full phone that could plausibly be the same
        person produce identical fingerprints — this is the join key used by
        the fuzzy-confidence clustering tier."""
        assert jd_mask_fingerprint("1******6198") == fuzzy_fingerprint("13800006198")
