# rap/app/utils/phone.py
"""Phone-number normalization for cross-platform customer matching.

Pure functions, no I/O — safe to unit-test without a DB. This is an
analytics-time concern (matching customers across platforms), distinct from
`app/db/etl/normalize.py`'s ETL-time concern (cleaning/storing raw values).
"""
from __future__ import annotations

import re

_CN_MOBILE_RE = re.compile(r"^1[3-9]\d{9}$")
_JD_MASKED_RE = re.compile(r"^1\*{5,7}(\d{4})$")


def normalize_phone(raw: str | None) -> str | None:
    """Strip everything but digits and '*' (JD's mask character).

    Returns None for empty/unusable input. Does NOT validate shape — callers
    decide whether the result is a full or masked number.
    """
    if not raw:
        return None
    cleaned = re.sub(r"[^\d*]", "", str(raw))
    return cleaned or None


def is_full_cn_mobile(phone: str | None) -> bool:
    """True if ``phone`` is a complete, unmasked 11-digit CN mobile number."""
    return bool(phone) and bool(_CN_MOBILE_RE.match(phone))


def is_jd_masked_phone(phone: str | None) -> bool:
    """True if ``phone`` matches JD's masking pattern: ``1`` + asterisks + 4 digits."""
    return bool(phone) and bool(_JD_MASKED_RE.match(phone))


def jd_mask_fingerprint(phone: str | None) -> str | None:
    """Extract a (first digit, last 4 digits) fingerprint from a JD-masked phone.

    Returns a string like ``"1-6198"`` usable as a fuzzy join key against
    ``fuzzy_fingerprint()`` of a full number, or None if ``phone`` doesn't
    match the expected masked shape.
    """
    if not is_jd_masked_phone(phone):
        return None
    return f"{phone[0]}-{phone[-4:]}"


def fuzzy_fingerprint(phone: str | None) -> str | None:
    """Extract the same (first digit, last 4 digits) fingerprint from a FULL
    11-digit phone, so it can be compared against ``jd_mask_fingerprint()``.
    """
    if not is_full_cn_mobile(phone):
        return None
    return f"{phone[0]}-{phone[-4:]}"
