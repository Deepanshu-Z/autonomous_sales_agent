"""Small catalog-backed self-check for batch timing resolution."""

from src.core.helpers.schedule_engine import resolve_catalog_batch_semantics


def main() -> None:
    cases = [
        ({"start": "04:00", "end": "05:00", "period": "Morning"}, "UNAVAILABLE", None),
        ({"start": "16:00", "end": "17:00", "period": "Evening"}, "SELECTED", "4:00 PM to 5:00 PM"),
        ({"start": "07:00", "end": "08:00", "period": "Morning"}, "SELECTED", "7:00 AM to 8:00 AM"),
        ({"start": "19:00", "end": "20:00", "period": "Evening"}, "SELECTED", "7:00 PM to 8:00 PM"),
        ({"start": "07:00", "end": None, "period": None}, "AMBIGUOUS", None),
        ({"start": "07:30", "end": None, "period": "Evening"}, "UNAVAILABLE", None),
        ({"start": None, "end": None, "period": "Morning"}, "PERIOD_ONLY", None),
        ({"start": None, "end": None, "period": None}, "UNRESOLVED", None),
        ({"start": "16:00", "end": "17:00", "period": "Evening"}, "SELECTED", "4:00 PM to 5:00 PM"),
    ]
    for semantic, expected_status, expected_timing in cases:
        result = resolve_catalog_batch_semantics(
            "yoga",
            start_24h=semantic["start"],
            end_24h=semantic["end"],
            period=semantic["period"],
        )
        assert result["status"] == expected_status, (semantic, result)
        if expected_timing:
            assert result["timing"] == expected_timing, (semantic, result)
    print("timing resolver self-check: OK")


if __name__ == "__main__":
    main()
