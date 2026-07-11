"""Tests for the KPI dashboard's period-comparison date math.

Pure-function tests only (no DB, no Streamlit) — these guard against the two
bugs the KPI page used to have: comparing a partial week/month against a
*full* prior period (always looks like a crash on day 1), and anchoring on
`date.today()` instead of the latest uploaded order date (the "today" row
was always empty since orders arrive in batches, not in real time).
"""
from __future__ import annotations

from datetime import date

from app.ui.pages.kpi_overview import (
    _week_range,
    _month_range,
    _prior_same_length_week,
    _prior_same_length_month,
)


def test_week_range_starts_monday():
    # 2026-07-10 is a Friday
    start, end = _week_range(date(2026, 7, 10))
    assert start == date(2026, 7, 6)  # Monday
    assert end == date(2026, 7, 10)


def test_prior_same_length_week_matches_day_count_not_full_week():
    """A 2-day 'week to date' must compare against a 2-day prior window, not
    the full 7-day prior week."""
    this_start, this_end = date(2026, 7, 6), date(2026, 7, 7)  # Mon-Tue, 2 days
    prior_start, prior_end = _prior_same_length_week(this_start, this_end)
    assert prior_start == date(2026, 6, 29)  # prior Monday
    assert prior_end == date(2026, 6, 30)  # prior Tuesday — 2 days, not 7
    assert (prior_end - prior_start).days == (this_end - this_start).days


def test_prior_same_length_week_full_week():
    this_start, this_end = date(2026, 7, 6), date(2026, 7, 12)  # full week
    prior_start, prior_end = _prior_same_length_week(this_start, this_end)
    assert prior_start == date(2026, 6, 29)
    assert prior_end == date(2026, 7, 5)


def test_month_range_starts_first_of_month():
    start, end = _month_range(date(2026, 7, 10))
    assert start == date(2026, 7, 1)
    assert end == date(2026, 7, 10)


def test_prior_same_length_month_matches_day_count():
    """A 10-day 'month to date' must compare against the first 10 days of the
    prior month, not the full prior month."""
    this_start, this_end = date(2026, 7, 1), date(2026, 7, 10)  # 10 days
    prior_start, prior_end = _prior_same_length_month(this_start, this_end)
    assert prior_start == date(2026, 6, 1)
    assert prior_end == date(2026, 6, 10)  # 10 days, not all of June


def test_prior_same_length_month_caps_to_shorter_prior_month():
    """Mar 1-31 (31 days) vs Feb (28 days in a non-leap year) must cap at
    Feb's actual length, not run into March."""
    this_start, this_end = date(2026, 3, 1), date(2026, 3, 31)
    prior_start, prior_end = _prior_same_length_month(this_start, this_end)
    assert prior_start == date(2026, 2, 1)
    assert prior_end == date(2026, 2, 28)  # 2026 is not a leap year


def test_prior_same_length_month_single_day():
    """Anchor falls on the 1st of the month — 'month to date' is 1 day."""
    this_start, this_end = date(2026, 7, 1), date(2026, 7, 1)
    prior_start, prior_end = _prior_same_length_month(this_start, this_end)
    assert prior_start == date(2026, 6, 1)
    assert prior_end == date(2026, 6, 1)
