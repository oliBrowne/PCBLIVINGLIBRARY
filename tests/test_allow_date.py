"""Re-allowing a part with the failures it was already allowed for keeps the first allow date, so a re-run on a
new day leaves plan.json alone (test_testpack's second run failed when CI crossed midnight UTC)."""
import datetime

from ecadlib import release_check as RC
from tests.fixtures_lite import lite

FAIL = ("verify", "symbol pinout Q_NPN_BEC from the family table: check pins 1-3 against the datasheet")


class _Day(datetime.date):
    today_is = datetime.date(2026, 10, 26)

    @classmethod
    def today(cls):
        return cls.today_is


def _run(items, base, monkeypatch, day, fails=(FAIL,)):
    _Day.today_is = day
    monkeypatch.setattr(RC.datetime, "date", _Day)
    monkeypatch.setattr(RC, "check_item", lambda it, b, r: (list(fails), [], {}))
    return RC.apply(items, base, allow=("Q1",), change_holds=False)


@lite
def test_rerun_next_day_keeps_the_allow_date(tmp_path, monkeypatch):
    items = [{"name": "Q1"}]
    _run(items, tmp_path, monkeypatch, datetime.date(2026, 10, 26))
    assert items[0]["allow"]["date"] == "2026-10-26" and items[0]["status"] == "ready", items[0]
    _run(items, tmp_path, monkeypatch, datetime.date(2026, 10, 27))
    assert items[0]["allow"]["date"] == "2026-10-26", items[0]["allow"]


@lite
def test_new_failures_get_a_new_allow_date(tmp_path, monkeypatch):
    items = [{"name": "Q1"}]
    _run(items, tmp_path, monkeypatch, datetime.date(2026, 10, 26))
    other = ("height", "3D height 9 mm is not plausible for SOT-23")
    _run(items, tmp_path, monkeypatch, datetime.date(2026, 10, 27), fails=(FAIL, other))
    assert items[0]["allow"]["date"] == "2026-10-27" and len(items[0]["allow"]["failures"]) == 2, items[0]["allow"]


if __name__ == "__main__":
    test_rerun_next_day_keeps_the_allow_date()
    test_new_failures_get_a_new_allow_date()
    print("ok")
