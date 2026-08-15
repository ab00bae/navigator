"""Stage 2 — the quality rules.

The real dataset only ever trips one of these rules, so every other rule is
proven here against a purpose-built row. Without this the pipeline would claim
checks it has never actually exercised.
"""

from __future__ import annotations

import pytest

from navigator.pipeline.validate import validate


def rules_for(result, source_row: int) -> set[str]:
    return {r.rule for r in result.rejections if r.source_row == source_row}


class TestAcceptance:
    def test_a_clean_row_survives(self, extracted):
        result = validate(extracted([{"unique_key": "1"}]))

        assert len(result.valid) == 1
        assert result.rejections == []

    def test_an_open_ticket_is_not_a_rejection(self, extracted):
        """A null closed_date means still open, which is normal, not broken."""
        result = validate(extracted([{"closed_date": ""}]))

        assert len(result.valid) == 1

    @pytest.mark.parametrize("field", ["incident_zip", "latitude", "longitude",
                                       "descriptor", "borough"])
    def test_missing_detail_is_kept_not_rejected(self, extracted, field):
        """Absent detail is normalised later; it does not make a row unusable."""
        result = validate(extracted([{field: ""}]))

        assert len(result.valid) == 1


class TestIdentityRules:
    def test_blank_unique_key_is_rejected(self, extracted):
        result = validate(extracted([{"unique_key": ""}]))

        assert len(result.valid) == 0
        assert rules_for(result, 2) == {"missing_unique_key"}

    def test_whitespace_only_key_counts_as_blank(self, extracted):
        result = validate(extracted([{"unique_key": "   "}]))

        assert rules_for(result, 2) == {"missing_unique_key"}

    def test_duplicate_key_rejects_the_later_row_only(self, extracted):
        frame = extracted([{"unique_key": "7"}, {"unique_key": "7"}])

        result = validate(frame)

        assert len(result.valid) == 1
        assert result.valid["unique_key"].iloc[0] == "7"
        assert rules_for(result, 3) == {"duplicate_unique_key"}

    def test_duplicate_message_names_the_key(self, extracted):
        result = validate(extracted([{"unique_key": "7"}, {"unique_key": "7"}]))

        assert "7" in result.rejections[0].message


class TestDateRules:
    def test_missing_created_date_is_rejected(self, extracted):
        result = validate(extracted([{"created_date": ""}]))

        assert rules_for(result, 2) == {"missing_created_date"}

    def test_unparseable_created_date_is_rejected(self, extracted):
        result = validate(extracted([{"created_date": "last Tuesday"}]))

        assert rules_for(result, 2) == {"unparseable_created_date"}

    def test_unparseable_closed_date_is_rejected(self, extracted):
        result = validate(extracted([{"closed_date": "not a date"}]))

        assert rules_for(result, 2) == {"unparseable_closed_date"}

    def test_closed_before_created_is_rejected(self, extracted):
        result = validate(
            extracted(
                [{
                    "created_date": "2026-08-12T10:00:00.000",
                    "closed_date": "2026-08-12T09:00:00.000",
                }]
            )
        )

        assert rules_for(result, 2) == {"closed_before_created"}

    def test_closed_exactly_at_created_is_allowed(self, extracted):
        """Auto-closed tickets legitimately resolve in the same instant."""
        stamp = "2026-08-12T10:00:00.000"
        result = validate(extracted([{"created_date": stamp, "closed_date": stamp}]))

        assert len(result.valid) == 1

    def test_message_shows_both_timestamps(self, extracted):
        result = validate(
            extracted(
                [{
                    "created_date": "2026-08-12T10:00:00.000",
                    "closed_date": "2026-08-12T09:00:00.000",
                }]
            )
        )

        message = result.rejections[0].message
        assert "09:00:00" in message and "10:00:00" in message


class TestFieldRules:
    def test_missing_complaint_type_is_rejected(self, extracted):
        result = validate(extracted([{"complaint_type": ""}]))

        assert rules_for(result, 2) == {"missing_complaint_type"}

    @pytest.mark.parametrize(
        ("latitude", "longitude"),
        [
            ("51.5074", "-0.1278"),   # London
            ("40.6465", "-120.0"),    # plausible latitude, wrong longitude
            ("0", "0"),               # null island
        ],
    )
    def test_coordinates_outside_new_york_are_rejected(
        self, extracted, latitude, longitude
    ):
        result = validate(extracted([{"latitude": latitude, "longitude": longitude}]))

        assert rules_for(result, 2) == {"coordinates_out_of_range"}

    def test_coordinates_inside_new_york_are_kept(self, extracted):
        result = validate(extracted([{"latitude": "40.7128", "longitude": "-74.0060"}]))

        assert len(result.valid) == 1


class TestReporting:
    def test_a_row_breaking_two_rules_reports_both(self, extracted):
        result = validate(
            extracted([{"unique_key": "", "complaint_type": ""}])
        )

        assert rules_for(result, 2) == {"missing_unique_key", "missing_complaint_type"}

    def test_row_count_counts_rows_not_violations(self, extracted):
        """Two broken rules on one row is still only one lost row."""
        result = validate(extracted([{"unique_key": "", "complaint_type": ""}]))

        assert len(result.rejections) == 2
        assert result.rejected_row_count == 1

    def test_rejection_carries_the_source_line(self, extracted):
        frame = extracted([{"unique_key": "1"}, {"unique_key": ""}])

        result = validate(frame)

        assert result.rejections[0].source_row == 3

    def test_good_rows_survive_alongside_bad_ones(self, extracted):
        frame = extracted(
            [{"unique_key": "1"}, {"unique_key": ""}, {"unique_key": "3"}]
        )

        result = validate(frame)

        assert list(result.valid["unique_key"]) == ["1", "3"]
