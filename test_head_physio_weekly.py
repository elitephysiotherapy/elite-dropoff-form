"""Hub period maths + block parsing for head_physio_weekly (no network)."""
from datetime import date

import head_physio_weekly as h


def test_month_split_matches_hub():
    # Hub (2026-10-13): Sept wk5 = 28–30 Sep, Oct wk1 = 1–4 Oct, wk2 = 5–11, wk3 = 12–18
    sep = h.hub_periods_in_month(2026, 9)
    assert sep[-1] == ("September - Week 5", date(2026, 9, 28), date(2026, 9, 30))
    oct_ = h.hub_periods_in_month(2026, 10)
    assert oct_[0] == ("October - Week 1", date(2026, 10, 1), date(2026, 10, 4))
    assert oct_[1] == ("October - Week 2", date(2026, 10, 5), date(2026, 10, 11))
    assert oct_[2] == ("October - Week 3", date(2026, 10, 12), date(2026, 10, 18))
    assert h.hub_date_range(date(2026, 10, 12), date(2026, 10, 18)) == "12th - 18th October 2026"
    assert h.hub_date_range(date(2026, 10, 1), date(2026, 10, 4)) == "1st - 4th October 2026"


def test_finished_periods_excludes_running_week():
    got = [p[0] for p in h.finished_periods(date(2026, 9, 29), date(2026, 10, 13))]
    assert got == ["September - Week 5", "October - Week 1", "October - Week 2"]
    # Monday: the week that ended yesterday is finished
    got = [p[0] for p in h.finished_periods(date(2026, 10, 12), date(2026, 10, 19))]
    assert got == ["October - Week 3"]


def test_hub_formulas():
    # Values from the live Hub test entry: 100/10/8/4/3/2/1000/800
    r = h._row("Marty", {1: 100, 2: 10, 3: 8, 5: 4, 7: 3, 8: 2, 10: 1000, 11: 800})
    assert r[4] == "80%" and r[6] == "50%" and r[9] == "5%" and r[12] == "80%"


def test_parse_and_sort_blocks_newest_first():
    vals = [["Head Physio Weekly"], ["x"], [], [],
            [],
            ["October - Week 1", "1st - 4th October 2026", "Hub: entered"], h.HEADERS, ["Marty", 1],
            [],
            ["October - Week 2", "5th - 11th October 2026", h.HUB_NOT_ENTERED], h.HEADERS, ["Marty", 2],
            []]
    blocks = h.parse_blocks(vals)
    assert len(blocks) == 2
    grid = h.build_grid(blocks, __import__("datetime").datetime(2026, 10, 13))
    titles = [r[0] for r in grid if r and h.TITLE_RE.match(r[0])]
    assert titles == ["October - Week 2", "October - Week 1"]


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print(f"\n{len(tests)}/{len(tests)} passed")
