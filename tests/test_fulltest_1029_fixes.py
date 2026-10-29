"""Found in real Altium on 2026-10-29 (full end-to-end test), fixed on Oliver's word ("all those can be fixed"):
* Nexar answering "You have exceeded your part limit of 0" counted as "source unreachable", so a bad MPN said Held
  instead of Not found and a BOM check said "source refused/unreachable 37";
* a part held only for having no datasheet link (RC0603FR-0710KL): now a note, `[release] datasheet = "hold"` holds;
* the dialog's status line cut off mid-word ("...Altium is busy unt").
python3 -m tests.test_fulltest_1029_fixes"""
import csv, re, tempfile
from pathlib import Path

from ecadlib import altium_menu as AM, bom_triage as T, providers as P, rules as RU
from tests.fixtures_lite import lite

ROOT = Path(__file__).resolve().parent.parent


class _OverQuota(P.Provider):
    name = "nexar"

    def lookup(self, line):
        return P.SourceResult(self.name, mpn=line.get("mpn", ""), error="You have exceeded your part limit of 0")


class _Down(P.Provider):
    name = "digikey"

    def lookup(self, line):
        return P.SourceResult(self.name, mpn=line.get("mpn", ""), error="ProxyError: connection refused")


class _Miss(P.Provider):
    name = "mouser"

    def lookup(self, line):
        return P.SourceResult(self.name, found=False)                     # answered: no such part


LINE = {"designator": "U1", "mpn": "ZZZ-NOT-A-PART-123", "manufacturer": "", "value": "", "footprint": "", "lcsc": ""}


def test_a_quota_answer_is_not_an_unreachable_source():
    assert P.account_limit("You have exceeded your part limit of 0") and P.account_limit("Daily quota exceeded")
    assert not P.account_limit("ProxyError: connection refused") and not P.account_limit("HTTP 503") and not P.account_limit("")
    row = T.triage([dict(LINE)], T.KiCadIndex([]), [_OverQuota(None), _Miss(None)], None)[0]
    assert row["status"] == "needs_footprint_generation", row                    # a real miss, not "unverified"
    assert "nexar: not used, the account's limit is reached" in row["notes"], row["notes"]
    down = T.triage([dict(LINE)], T.KiCadIndex([]), [_Down(None), _Miss(None)], None)[0]
    assert down["status"] == "unverified (source unreachable)", down             # a network failure still is


@lite
def test_the_menu_says_not_found_when_nexar_is_over_quota(tmp_path):
    row = T.triage([dict(LINE)], T.KiCadIndex([]), [_OverQuota(None), _Miss(None)], None)[0]
    f = tmp_path / "triage.csv"
    with f.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, list(row))
        w.writeheader()
        w.writerow(row)
    assert AM.not_found(f, LINE["mpn"], {"status": "held", "name": LINE["mpn"]})


def test_no_datasheet_link_is_a_note_unless_asked_to_hold():
    from ecadlib import release_check as RC
    from tests.test_release_check import make
    assert RU.DEFAULTS["release"]["datasheet"] == "warn"
    with tempfile.TemporaryDirectory() as d:
        it = make(d)
        it["parameters"] = [p for p in it["parameters"] if p["name"] != "ComponentLink2URL"]
        f, w, _ = RC.check_item(it, d)
        assert f == [] and any(x.startswith("no datasheet link") for x in w), (f, w)
        hold = RU.load({"release": {"datasheet": "hold"}})
        assert [m for _, m in RC.check_item(it, d, hold)[0]] == ["no datasheet link"]
    try:
        RU.load({"release": {"datasheet": "sometimes"}})
        raise AssertionError("bad value accepted")
    except RU.RulesError as e:
        assert "release.datasheet" in str(e)


def test_the_status_line_never_runs_past_the_dialog():
    pas = (ROOT / "ecadlib" / "altium_scripts" / "ecadlib_dialog.pas").read_text(encoding="utf-8")
    fixed = re.findall(r"Status\.Caption := '([^']*)'", pas)
    assert fixed and all(len(s) <= 54 for s in fixed), [s for s in fixed if len(s) > 54]   # 380 px at ~7 px a letter
    assert "Status.Caption := OneLine(Said, 46) + Elapsed" in pas        # the job's own text is cut with '...'
    assert re.search(r"Function OneLine\(S : String; Room : Integer\) : String;", pas)


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            f()
            print("ok", n)
