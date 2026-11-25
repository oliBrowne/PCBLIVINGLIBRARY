"""Findings of the 2026-11-25 full code review, datasheet checks and shared store. Plain functions, no pytest needed:
python3 -m tests.run_all test_review1125_checks"""
from ecadlib import pin_drawing as PD
from tests.fixtures_lite import lite


GATE = {"1": "VCC", "2": "IN", "3": "SD", "4": "COM", "5": "LO", "6": "VS", "7": "HO", "8": "VB"}
DUAL = {"1": "OUT", "2": "IN-", "3": "IN+", "4": "VEE", "5": "GND", "6": "REF", "7": "VCC", "8": "EN"}


def test_one_rail_name_on_two_pins_is_no_class_match():
    # review 2026-11-25 (r3 F1): a symbol with VCC on pins 1 and 6 where the part has VCC and VS took the drawing's VS
    # as 'the only positive rail'; the same for GND on 4 and 5 where the part has VEE on 4
    sym = [(k, "VCC" if k == "6" else v) for k, v in GATE.items()]
    assert ("6", "VCC", "VS") in PD.compare("other", sym, GATE)
    sym = [(k, "GND" if k == "4" else v) for k, v in DUAL.items()]
    assert ("4", "GND", "VEE") in PD.compare("other", sym, DUAL)
    assert PD.compare("other", list(GATE.items()), GATE) == []


def test_a_rail_spelt_differently_still_matches_by_class():
    sym = [("1", "VIN"), ("2", "GND"), ("3", "EN"), ("4", "NC"), ("5", "VOUT")]
    drawn = {"1": "IN", "2": "GND", "3": "EN", "4": "NC", "5": "OUT"}
    assert PD.compare("other", sym, drawn) == [], PD.compare("other", sym, drawn)


def test_a_blank_drawing_label_does_not_confirm_a_symbol_nc():
    # review 2026-11-25 (r3 F6): a symbol's NC on the part's EN pin agreed with an unlabelled terminal
    sym = [("1", "VIN"), ("2", "GND"), ("3", "NC"), ("4", "OUT")]
    assert PD.compare("other", sym, {"1": "VIN", "2": "GND", "3": "", "4": "OUT"})
    assert PD.compare("other", sym, {"1": "VIN", "2": "GND", "3": "NC", "4": "OUT"}) == []


def test_a_lift_is_taken_back_when_its_record_is_no_longer_confirmed():
    # review 2026-11-25 (r5 M1): a hold lifted by a confirmed record stayed lifted after the maintainers narrowed who
    # may confirm and the record dropped to checked; a part built from it was held
    import json
    import tempfile
    from pathlib import Path
    from ecadlib import datasheet as D, librarian as L, store_client as SC, store_record as SR, store_repo as SRP
    quiet = lambda *a, **k: None
    resolve, D._resolve = D._resolve, (lambda host: [])
    try:
        d = Path(tempfile.mkdtemp(prefix="r1125-store-"))
        (d / "bom.csv").write_text("Designator,Qty,Value,Footprint,Manufacturer,Mfr. Part #,Description\n"
                                   "R1,1,10k,0603,Yageo,RC0603FR-0710KL,RES 10k 1% 0603\n", encoding="utf-8")
        items = {i["name"]: i for i in L.run(d / "bom.csv", out=d / "out", sources=["standard"], offline=True,
                                             cache=d / "cache", rules={}, log=quiet)}
        it = items["RES-0603-10k"]
        rec = SR.merge(SR.from_item(it, d / "out", by="alice", at="2026-11-23T10:00:00Z"),
                       SR.from_item(it, d / "out", by="mallory", at="2026-11-24T10:00:00Z"))
        root = d / "store"
        SRP.init(root, maintainers=("maint",), log=quiet)
        (d / "rec.json").write_text(json.dumps(rec))
        SRP.add(root, [d / "rec.json"], maintainer=True, log=quiet)
        h = SC.Store(str(root)).lookup("Yageo", "RC0603FR-0710KL")
        assert h.level == "confirmed"
        lifted = {"name": "B", "source": {"symbol": "datasheet:https://x.example/a.pdf",
                                          "footprint": "datasheet:https://x.example/a.pdf"},
                  "store_check": {"result": "agreed", "key": h.key, "key_id": h.key_id, "rev": h.rev,
                                  "lifted": ["datasheet reader output (unconfirmed): check pads"]}}
        assert not SC.problems(lifted, SC.Store(str(root)))
        meta = json.loads((root / "store.json").read_text())
        meta["trusted"] = ["alice"]                                   # mallory no longer counts: only checked
        (root / "store.json").write_text(json.dumps(meta))
        SRP.index(root, log=quiet)
        got = [w for _, w in SC.problems(lifted, SC.Store(str(root)))]
        assert got and "only a confirmed record lifts" in got[0], got
    finally:
        D._resolve = resolve


def test_a_service_url_is_read_as_the_http_client_reads_it():
    # review 2026-11-25 (r4 F4): NEXAR_TOKEN_URL 'https://evil.example\@identity.nexar.com/...' passed the same-host
    # check (urlsplit reads identity.nexar.com) while the HTTP client connects to evil.example
    from ecadlib import keyhosts as K
    dflt = "https://identity.nexar.com/connect/token"
    said = []
    assert K.service_url("X", dflt, {"X": "https://evil.example\\@identity.nexar.com/x"}, same_host=True,
                         warn=said.append) == dflt and said
    assert K.service_url("X", dflt, {"X": "https://identity.nexar.com/v2"}, same_host=True) == "https://identity.nexar.com/v2"
    assert K.service_url("X", dflt, {"X": "http://127.0.0.1:8000/t"}) == "http://127.0.0.1:8000/t"


@lite
def test_confirm_records_the_name_it_showed(tmp_path, monkeypatch):
    # review 2026-11-25 (r4 F3): the prompt named the person from the workspace's settings, the record was made under
    # the name a git config in the library folder gave
    import json
    import subprocess
    from ecadlib import confirm as CF
    monkeypatch.setenv("ECADLIB_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("ECADLIB_USER", "")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitglobal"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    (tmp_path / "gitglobal").write_text("[user]\n\tname = Ann Lee\n\temail = ann@example.com\n")
    ws = tmp_path / "ws"
    lib = ws / "librarian" / "bom"
    lib.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(lib)], check=True)
    subprocess.run(["git", "-C", str(lib), "config", "user.name", "Mallory"], check=True)
    subprocess.run(["git", "-C", str(lib), "config", "user.email", "mallory@evil.example"], check=True)
    plan = lib / "plan.json"
    plan.write_text(json.dumps([{"name": "R1", "status": "awaiting_approval", "symbol": {}, "footprint": {}}]))
    r = None
    with CF.over_mcp("approve_part"):
        try:
            CF.ask_decision(ws, plan, "bom.csv", "R1", "approve")
        except CF.NeedsPerson as e:
            r = e.request
    asked = []
    CF.main([r["id"], "--workspace", str(ws)], inp=lambda p: asked.append(p) or "yes")
    assert "Ann Lee" in asked[0], asked
    who = [a.get("who") for a in json.loads(plan.read_text())[0].get("approvals") or []]
    assert who and all("Ann Lee" in str(w) for w in who), who


def test_the_second_read_compares_each_function_s_active_low_mark():
    # review 2026-11-25 (r3 F2): compare_pins read no mark in a multi-function name ('~{RESET}/PB5' agreed with
    # 'RESET/PB5'); pin_drawing already compared them function by function
    from ecadlib import crosscheck as CC
    base = [("1", "PB0"), ("2", "PB1"), ("3", "PB2"), ("4", "GND")]
    for a, b in (("~{RESET}/PB5", "RESET/PB5"), ("PB5/~{RESET}", "PB5/RESET"), ("RESET/PB5", "~{RESET}/PB5")):
        assert any(d["field"] == "pin_names" for d in CC.compare_pins(base + [("5", a)], base + [("5", b)])), (a, b)
    assert CC.compare_pins(base + [("5", "~{RESET}/PB5")], base + [("5", "PB5/~{RESET}")]) == []
    assert CC.compare_pins(base + [("5", "SDA/PB5")], base + [("5", "PB5/SDA")]) == []
