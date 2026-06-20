"""Tests for the WeChat auto-sync scheduler helpers."""
from datetime import datetime
from unittest.mock import patch

import pytest

from app.scheduler import seconds_until_next_run


class TestSecondsUntilNextRun:
    def _now_at(self, hour: int, minute: int = 0, second: int = 0, tz: str = "Asia/Shanghai"):
        """Return a fixed datetime in *tz* at the given time-of-day."""
        from zoneinfo import ZoneInfo

        return datetime(2026, 5, 28, hour, minute, second, tzinfo=ZoneInfo(tz))

    def _call(self, target_hour: int, current_hour: int, current_minute: int = 0,
              tz: str = "Asia/Shanghai") -> float:
        fake_now = self._now_at(current_hour, current_minute, tz=tz)
        with patch("app.scheduler.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            return seconds_until_next_run(target_hour, tz)

    def test_target_later_today(self):
        # It is 01:00 and target is 03:00 → 2 h = 7200 s
        delay = self._call(target_hour=3, current_hour=1)
        assert abs(delay - 7200) < 2

    def test_target_is_now_schedules_tomorrow(self):
        # It is exactly 03:00 and target is 03:00 → should schedule 24 h later
        delay = self._call(target_hour=3, current_hour=3, current_minute=0)
        assert abs(delay - 86400) < 2

    def test_target_already_passed_today(self):
        # It is 10:00 and target is 03:00 → 17 h until 03:00 tomorrow
        delay = self._call(target_hour=3, current_hour=10)
        assert abs(delay - 17 * 3600) < 2

    def test_midnight_target(self):
        # It is 23:00 and target is 00:00 → 1 h
        delay = self._call(target_hour=0, current_hour=23)
        assert abs(delay - 3600) < 2

    def test_unknown_timezone_falls_back_to_utc(self):
        # Should not raise; result is some positive number
        delay = seconds_until_next_run(hour=3, tz_name="Not/AReal/Timezone")
        assert delay > 0

    def test_returns_positive_seconds(self):
        for hour in [0, 3, 12, 23]:
            delay = seconds_until_next_run(hour=hour, tz_name="Asia/Shanghai")
            assert 0 < delay <= 86400
