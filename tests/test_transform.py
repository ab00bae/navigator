"""Stage 3 — cleaning and derivation."""

from __future__ import annotations

import pandas as pd
import pytest

from navigator.pipeline.transform import categorise, transform


def only(frame):
    return frame.iloc[0]


class TestTimestamps:
    def test_local_time_is_converted_to_utc(self, extracted):
        """August in New York is EDT, four hours behind UTC."""
        frame = transform(extracted([{"created_date": "2026-08-12T01:55:26.000"}]))

        assert only(frame)["created_at"] == pd.Timestamp("2026-08-12T05:55:26Z")

    def test_winter_uses_the_standard_time_offset(self, extracted):
        """January is EST, five hours behind — a fixed offset would be wrong here."""
        frame = transform(extracted([{"created_date": "2026-01-15T12:00:00.000"}]))

        assert only(frame)["created_at"] == pd.Timestamp("2026-01-15T17:00:00Z")

    def test_the_hour_that_does_not_exist_is_shifted_forward(self, extracted):
        """Clocks jump 02:00 to 03:00 on 8 March 2026, so 02:30 never happens.

        Such a timestamp collapses to the first instant that does exist, 03:00
        local — it is not carried forward keeping its minutes.
        """
        frame = transform(extracted([{"created_date": "2026-03-08T02:30:00.000"}]))

        assert only(frame)["created_at"] == pd.Timestamp("2026-03-08T07:00:00Z")

    def test_the_repeated_hour_is_read_as_daylight_time(self, extracted):
        """01:30 happens twice on 1 November 2026; the earlier one is taken."""
        frame = transform(extracted([{"created_date": "2026-11-01T01:30:00.000"}]))

        assert only(frame)["created_at"] == pd.Timestamp("2026-11-01T05:30:00Z")

    def test_open_ticket_has_no_closed_timestamp(self, extracted):
        frame = transform(extracted([{"closed_date": ""}]))

        assert pd.isna(only(frame)["closed_at"])


class TestDerivedFields:
    def test_resolution_hours_is_the_elapsed_time(self, extracted):
        frame = transform(
            extracted(
                [{
                    "created_date": "2026-08-12T01:00:00.000",
                    "closed_date": "2026-08-12T03:30:00.000",
                }]
            )
        )

        assert only(frame)["resolution_hours"] == 2.5

    def test_resolution_is_null_while_open(self, extracted):
        frame = transform(extracted([{"closed_date": ""}]))

        assert pd.isna(only(frame)["resolution_hours"])

    def test_same_instant_close_is_zero_not_null(self, extracted):
        stamp = "2026-08-12T01:00:00.000"
        frame = transform(extracted([{"created_date": stamp, "closed_date": stamp}]))

        assert only(frame)["resolution_hours"] == 0.0
        assert only(frame)["is_closed"] is True or only(frame)["is_closed"]

    def test_resolution_spans_a_daylight_saving_boundary_correctly(self, extracted):
        """23 wall-clock hours across the spring change is 22 real hours."""
        frame = transform(
            extracted(
                [{
                    "created_date": "2026-03-07T12:00:00.000",
                    "closed_date": "2026-03-08T11:00:00.000",
                }]
            )
        )

        assert only(frame)["resolution_hours"] == 22.0

    def test_is_closed_follows_the_closed_timestamp(self, extracted):
        frame = transform(
            extracted([{"closed_date": ""}, {"closed_date": "2026-08-12T02:00:00.000"}])
        )

        assert list(frame["is_closed"]) == [False, True]


class TestNormalisation:
    @pytest.mark.parametrize("placeholder", ["Unspecified", "N/A", "", "  ", "none"])
    def test_placeholders_become_null(self, extracted, placeholder):
        frame = transform(extracted([{"borough": placeholder}]))

        assert pd.isna(only(frame)["borough"])

    def test_borough_is_title_cased(self, extracted):
        frame = transform(extracted([{"borough": "STATEN ISLAND"}]))

        assert only(frame)["borough"] == "Staten Island"

    def test_five_digit_zip_is_kept(self, extracted):
        frame = transform(extracted([{"incident_zip": "11203"}]))

        assert only(frame)["incident_zip"] == "11203"

    @pytest.mark.parametrize("bad_zip", ["1120", "112034", "ABCDE", "11203-1234"])
    def test_malformed_zip_is_dropped_but_the_row_is_kept(self, extracted, bad_zip):
        frame = transform(extracted([{"incident_zip": bad_zip}]))

        assert len(frame) == 1
        assert pd.isna(only(frame)["incident_zip"])

    def test_coordinates_become_numbers(self, extracted):
        frame = transform(extracted([{"latitude": "40.6465", "longitude": "-73.9452"}]))

        assert only(frame)["latitude"] == pytest.approx(40.6465)
        assert only(frame)["longitude"] == pytest.approx(-73.9452)


class TestCategorisation:
    @pytest.mark.parametrize(
        ("complaint_type", "expected"),
        [
            ("Noise - Residential", "Noise"),
            ("Noise - Vehicle", "Noise"),
            ("HEAT/HOT WATER", "Heat & Hot Water"),
            ("Illegal Parking", "Parking & Vehicles"),
            ("For Hire Vehicle Complaint", "Parking & Vehicles"),
            ("Damaged Tree", "Trees & Parks"),
            ("Overgrown Tree/Branches", "Trees & Parks"),
            ("UNSANITARY CONDITION", "Sanitation"),
            ("Illegal Dumping", "Sanitation"),
            ("Street Condition", "Streets & Sidewalks"),
            ("Water System", "Water & Sewer"),
            ("Homeless Person Assistance", "Homeless Services"),
            ("Something Entirely New", "Other"),
        ],
    )
    def test_types_roll_up_to_the_expected_category(self, complaint_type, expected):
        result = categorise(pd.Series([complaint_type]))

        assert result.iloc[0] == expected

    def test_parking_wins_over_parks(self):
        """'Illegal Parking' contains 'park'; rule order must not mis-bin it."""
        result = categorise(pd.Series(["Illegal Parking"]))

        assert result.iloc[0] == "Parking & Vehicles"

    def test_noise_wins_over_vehicles(self):
        """'Noise - Vehicle' matches both rules; noise is the more useful bin."""
        result = categorise(pd.Series(["Noise - Vehicle"]))

        assert result.iloc[0] == "Noise"

    def test_matching_is_case_insensitive(self):
        result = categorise(pd.Series(["nOiSe - Residential"]))

        assert result.iloc[0] == "Noise"

    def test_every_row_gets_a_category(self):
        result = categorise(pd.Series(["Whatever", "", "Noise - Street"]))

        assert result.notna().all()


class TestPurity:
    def test_transform_is_deterministic(self, extracted):
        """Same input, same output — which is what makes re-running safe."""
        frame = extracted([{"unique_key": "1"}, {"unique_key": "2"}])

        first = transform(frame)
        second = transform(frame)

        pd.testing.assert_frame_equal(first, second)

    def test_transform_does_not_mutate_its_input(self, extracted):
        frame = extracted([{"borough": "BROOKLYN"}])
        before = frame.copy()

        transform(frame)

        pd.testing.assert_frame_equal(frame, before)
