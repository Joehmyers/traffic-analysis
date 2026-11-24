import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src" / "traffic_busyness"))
from extract_daily import summarise_days  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "webtris"


def day(name):
    rows = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))["Rows"]
    return summarise_days(rows)


def test_normal_day_has_96_slots_and_bands_close_to_total():
    (date, d), = day("normal-day-2025-03-04").items()
    assert date == "2025-03-04"
    assert d["slots_present"] == 96 and d["rows_returned"] == 96
    assert d["total_volume"] > 0
    assert d["vol_0_520"] + d["vol_521_660"] + d["vol_661_1160"] + d["vol_1160_plus"] <= d["total_volume"] + 100


def test_clocks_forward_day_has_92_slots():
    (_, d), = day("clocks-forward-2025-03-30").items()
    assert d["rows_returned"] == 92 and d["slots_present"] == 89  # 3 slots blank at 05:15 to 05:44


def test_clocks_back_day_drops_the_blank_twin_hour():
    (_, d), = day("clocks-back-2025-10-26").items()
    assert d["rows_returned"] == 96
    assert d["slots_present"] == 92  # the 4 blank 01:xx slots are not counted


def test_missing_slots_day_counts_only_filled_slots():
    (_, d), = day("missing-slots-2025-07-27").items()
    assert d["rows_returned"] == 96 and d["slots_present"] == 87
    assert d["total_volume"] > 0

