from datetime import timezone

import main


class TestDowFieldToNames:
    def test_star_means_every_day(self):
        assert main._dow_field_to_names("*") == []

    def test_empty_string_means_no_days(self):
        assert main._dow_field_to_names("") == []

    def test_single_day(self):
        assert main._dow_field_to_names("1") == ["mon"]

    def test_multiple_days_comma_separated(self):
        assert main._dow_field_to_names("1,3,5") == ["mon", "wed", "fri"]

    def test_whitespace_around_tokens_is_stripped(self):
        assert main._dow_field_to_names("1, 3") == ["mon", "wed"]

    def test_zero_is_sunday(self):
        assert main._dow_field_to_names("0") == ["sun"]

    def test_seven_wraps_to_sunday(self):
        assert main._dow_field_to_names("7") == ["sun"]

    def test_non_digit_token_is_skipped(self):
        assert main._dow_field_to_names("mon") == []

    def test_mixed_digit_and_non_digit_keeps_only_digits(self):
        assert main._dow_field_to_names("1,foo,3") == ["mon", "wed"]


class TestToIsoDatetimeWithTz:
    def test_bare_date_gets_midnight_and_offset(self):
        result = main._to_iso_datetime_with_tz("2025-12-05", "America/Los_Angeles")
        assert result.startswith("2025-12-05T00:00:00")
        assert result.endswith("-08:00")

    def test_date_with_time_component_uses_only_the_date_part(self):
        result = main._to_iso_datetime_with_tz("2025-12-05T13:45:00Z", "UTC")
        assert result == "2025-12-05T00:00:00.000+00:00"

    def test_invalid_tz_name_falls_back_to_utc(self):
        result = main._to_iso_datetime_with_tz("2025-12-05", "Not/A_Real_Zone")
        assert result == "2025-12-05T00:00:00.000+00:00"

    def test_empty_tz_name_falls_back_to_utc(self):
        result = main._to_iso_datetime_with_tz("2025-12-05", "")
        assert result == "2025-12-05T00:00:00.000+00:00"

    def test_none_tz_name_falls_back_to_utc(self):
        result = main._to_iso_datetime_with_tz("2025-12-05", None)
        assert result == "2025-12-05T00:00:00.000+00:00"

    def test_result_has_millisecond_precision(self):
        result = main._to_iso_datetime_with_tz("2025-01-01", "UTC")
        # "2025-01-01T00:00:00.000+00:00"
        assert result.split(".")[1].startswith("000")


class TestBuildSchedule:
    def test_wrong_field_count_returns_empty_dict(self, subscription_factory):
        sub = subscription_factory(schedule="0 0 * * 1")  # only 5 fields
        assert main._build_schedule(sub) == {}

    def test_missing_schedule_returns_empty_dict(self, subscription_factory):
        sub = subscription_factory(schedule="")
        assert main._build_schedule(sub) == {}

    def test_weekly_schedule(self, subscription_factory):
        sub = subscription_factory(schedule="0 30 8 * * 1,3", tzName="UTC", startAt=None)
        schedule = main._build_schedule(sub)
        assert schedule["cron"] == "30 8 * * mon,wed"
        assert schedule["timezone"] == "UTC"
        assert schedule["time"]["frequency"] == "week"
        assert schedule["time"]["onTime"] == 8
        assert schedule["time"]["onTime_min"] == 30
        assert schedule["time"]["days"] == {
            "sun": False, "mon": True, "tue": False, "wed": True,
            "thu": False, "fri": False, "sat": False,
        }
        assert "startDate" not in schedule["time"]

    def test_monthly_schedule_increments_month_for_cron(self, subscription_factory):
        # month field is 0-indexed in the legacy cron (0 = January)
        sub = subscription_factory(schedule="0 15 9 5 0 *", startAt=None)
        schedule = main._build_schedule(sub)
        assert schedule["time"]["frequency"] == "month"
        assert "days" not in schedule["time"]
        assert schedule["cron"] == "15 9 5 1 *"

    def test_monthly_schedule_with_non_digit_month_keeps_month_as_is(self, subscription_factory):
        sub = subscription_factory(schedule="0 15 9 5 * *", startAt=None)
        schedule = main._build_schedule(sub)
        assert schedule["cron"] == "15 9 5 * *"

    def test_daily_schedule(self, subscription_factory):
        sub = subscription_factory(schedule="0 0 6 * * *", startAt=None)
        schedule = main._build_schedule(sub)
        assert schedule["time"]["frequency"] == "day"
        assert "days" not in schedule["time"]
        assert schedule["cron"] == "0 6 * * *"

    def test_non_digit_minute_and_hour_default_to_zero(self, subscription_factory):
        sub = subscription_factory(schedule="0 x y * * *", startAt=None)
        schedule = main._build_schedule(sub)
        assert schedule["time"]["onTime"] == 0
        assert schedule["time"]["onTime_min"] == 0

    def test_start_at_is_converted_and_included(self, subscription_factory):
        sub = subscription_factory(schedule="0 0 6 * * *", startAt="2025-12-05", tzName="UTC")
        schedule = main._build_schedule(sub)
        assert schedule["time"]["startDate"] == "2025-12-05T00:00:00.000+00:00"

    def test_no_start_at_omits_start_date(self, subscription_factory):
        sub = subscription_factory(schedule="0 0 6 * * *", startAt=None)
        schedule = main._build_schedule(sub)
        assert "startDate" not in schedule["time"]

    def test_missing_tz_name_defaults_to_empty_string_in_timezone_field(self, subscription_factory):
        sub = subscription_factory(schedule="0 0 6 * * *", startAt=None)
        del sub["tzName"]
        schedule = main._build_schedule(sub)
        assert schedule["timezone"] == ""

    def test_every_is_one_for_daily_frequency(self, subscription_factory):
        sub = subscription_factory(schedule="0 0 6 * * *", startAt=None)
        schedule = main._build_schedule(sub)
        assert schedule["time"]["every"] == 1

    def test_every_is_absent_for_weekly_frequency(self, subscription_factory):
        sub = subscription_factory(schedule="0 30 8 * * 1,3", startAt=None)
        schedule = main._build_schedule(sub)
        assert "every" not in schedule["time"]

    def test_every_is_absent_for_monthly_frequency(self, subscription_factory):
        sub = subscription_factory(schedule="0 15 9 5 0 *", startAt=None)
        schedule = main._build_schedule(sub)
        assert "every" not in schedule["time"]
