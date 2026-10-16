"""Offline tests for the accuracy gate (ecadlib/eval/gate.py) and the golden output snapshots (ecadlib/eval/golden.py).
Run:  python3 -m tests.test_evalgate

The replay pack is SYNTHETIC (a perfect reader's answers built from the standard table and the set's reference pads),
so these tests prove the gate and the snapshots catch regressions in the pipeline, not how accurate a model is."""
import copy, gzip, json, os, tempfile
from pathlib import Path
from unittest import mock

from ecadlib import footprint_gen as F
from ecadlib.eval import cli as EC, gate as G, golden as GO, runner as R

def _sample(base):
    """A case per family from the baseline's passing list (deterministic)."""
    seen, out = set(), []
    for i in base["passing"]:
        fam = i.split("-", 1)[0]
        if fam not in seen:
            seen.add(fam)
            out.append(i)
    return out


def _base():
    return json.loads(G.baseline_path().read_text(encoding="utf-8"))


# ---------------------------------------------------------------- gate
def test_replay_pack_is_committed_compact_and_reproducible():
    p = G.replay_path()
    assert p.exists() and p.stat().st_size < 250_000, p.stat().st_size          # compact: one gzip JSON, not 331 files
    pack = G.load_replay(p)
    cases = R.load_set("kicad-v1")["cases"]
    assert set(pack["answers"]) == {c["mpn"] for c in cases} and "SYNTHETIC" in pack["meta"]["about"]
    with tempfile.TemporaryDirectory() as d:
        again = G.make_replay("kicad-v1", Path(d) / "x.json.gz")
        assert again.read_bytes() == p.read_bytes(), "replay pack is stale: python -m ecadlib eval gate --make-replay"


def test_synthetic_answers_pass_the_readers_schema():
    """Every answer is what strict structured outputs would return; parse_message validates the schema."""
    from ecadlib import datasheet as D
    pack = G.load_replay(G.replay_path())
    with tempfile.TemporaryDirectory() as d:
        G.expand({"meta": pack["meta"], "answers": dict(list(pack["answers"].items())[:40])}, d)
        for f in sorted(Path(d).iterdir()):
            rec = json.loads(f.read_text())
            ext = D.parse_message(rec["message"])
            assert ext["package"]["name"] and rec["message"]["usage"] == {"input_tokens": 0, "output_tokens": 0}


def test_baseline_is_committed_and_consistent():
    b = _base()
    assert b["overall"]["cases"] == 331 and sum(f["cases"] for f in b["families"].values()) == 331
    assert len(b["passing"]) == b["overall"]["pass"] == sum(f["pass"] for f in b["families"].values())
    assert b["overall"]["rate"] >= 0.95, b["overall"]      # 325/331 when committed; a drop means the pipeline broke


def _rep(rows):
    return {"meta": {"set": "kicad-v1", "set_version": 1, "sources": G.SOURCES, "wall_s": 1.0, "network_requests": 0},
            "summary": R.summarize(rows), "cases": rows}


def _row(i, fam, ok, errors=()):
    return {"id": i, "family": fam, "mpn": i, "pass": ok, "source": "datasheet" if ok or errors else "none", "errors": list(errors),
            "fields": {}, "tokens_in": 0, "tokens_out": 0, "usd": 0.0, "tried": ["datasheet: failed (x)"]}


def test_check_flags_a_family_drop_and_a_broken_case_and_reports_new_passes():
    rows = [_row(f"qfn-P{i}", "qfn", True) for i in range(100)] + [_row("sot-A", "sot", True), _row("sot-B", "sot", False)]
    base = G.baseline(_rep(rows))
    assert G.check(_rep(rows), base)["ok"]
    # one of 100 qfn cases breaks: 1.0 % drop is within tol 0.01, but a passing case broke -> fail
    r2 = copy.deepcopy(rows)
    r2[5] = _row("qfn-P5", "qfn", False, ["pitch: ours 0.65 mm, KiCad 0.5 mm"])
    res = G.check(_rep(r2), base)
    assert not res["ok"] and res["problems"] == ["qfn-P5: passed in the baseline, now fails (datasheet): pitch: ours 0.65 mm, KiCad 0.5 mm"], res
    # the family rate itself below tolerance
    base2 = copy.deepcopy(base)
    base2["passing"] = []                              # only the rate check left
    r3 = copy.deepcopy(rows)
    for k in range(5):
        r3[k] = _row(f"qfn-P{k}", "qfn", False, ["pin1: mirrored"])
    res = G.check(_rep(r3), base2)
    assert not res["ok"] and res["problems"] == ["family qfn: pass rate 95.0 % < baseline 100.0 % - 1.0 %"], res
    assert G.check(_rep(r3), base2, tol=0.06)["ok"]
    # a new pass is reported, not a failure; a missing family is a failure
    r4 = copy.deepcopy(rows)
    r4[-1] = _row("sot-B", "sot", True)
    res = G.check(_rep(r4), base)
    assert res["ok"] and res["fixed"] == ["sot-B"] and "--update" in res["text"]
    res = G.check(_rep(rows[:100]), base)
    assert not res["ok"] and any(p.startswith("family sot: missing") for p in res["problems"])


def test_gate_runs_offline_and_catches_a_wrong_answer():
    """A case per family through the real gate path (replay pack, no network): all pass as in the baseline. Then one
    answer is made wrong (0.635 mm pitch for a 1.27 mm SOIC) and the gate fails on exactly that case."""
    base = _base()
    ids = _sample(base)
    assert len(ids) == len(base["families"])
    rep = G.run(ids=ids)
    res = G.check(rep, base, subset=True)
    assert res["ok"], res["text"]
    assert all(c["pass"] for c in rep["cases"]) and rep["meta"]["mode"] == "replay"
    with tempfile.TemporaryDirectory() as d:
        pack = G.load_replay(G.replay_path())
        soic = next(c for c in rep["cases"] if c["family"] == "soic")
        pkg = pack["answers"][soic["mpn"]]["package"]
        assert pkg["pitch"]["nom"] == 1.27
        pkg["pitch"]["nom"] = 0.635
        bad = Path(d) / "bad.json.gz"
        bad.write_bytes(gzip.compress(json.dumps(pack).encode()))
        rep2 = G.run(ids=ids, replay=bad)
        res = G.check(rep2, base, subset=True)
        assert not res["ok"] and len(res["problems"]) == 1 and res["problems"][0].startswith(f"{soic['id']}: passed in the baseline, now fails")
        assert "FAIL (1)" in res["text"]


def test_gate_refuses_every_network_request_and_ignores_keys():
    hits = []
    with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-should-not-be-used"}), \
            mock.patch("requests.post", side_effect=lambda *a, **k: hits.append(a) or (_ for _ in ()).throw(AssertionError("network"))):
        rep = G.run(ids=["chip-CL03A104KP3NNNC", _sample(_base())[0]])
    assert not hits and rep["meta"]["mode"] == "replay" and rep["meta"]["network_requests"] >= 0


def test_gate_cli_update_then_check():
    with tempfile.TemporaryDirectory() as d:
        b = Path(d) / "b.json"
        ids = _sample(_base())[:2]
        real = G.run
        with mock.patch.object(G, "run", lambda *a, **k: real(ids=ids)):
            assert EC.main(["gate", "--baseline", str(b), "--update"]) == 0
            assert json.loads(b.read_text())["overall"]["cases"] == 2
            assert EC.main(["gate", "--baseline", str(b)]) == 0
            data = json.loads(b.read_text())
            data["passing"].append("qfn-NOT-IN-RUN")
            b.write_text(json.dumps(data))
            assert EC.main(["gate", "--baseline", str(b)]) == 1


# ---------------------------------------------------------------- golden snapshots
def test_golden_snapshots_match():
    """The committed fingerprints of the testpack, demo and package_specs outputs. A failure prints the diff; if the
    change is deliberate: python -m ecadlib eval golden --update, review git diff ecadlib/eval/golden, commit."""
    res = GO.check()
    bad = {g: t for g, t in res.items() if t}
    assert not bad, "\n".join(f"golden {g} changed:\n{t}" for g, t in bad.items())


def test_golden_ignores_uuids_and_altium_dates_and_ids():
    a = '(footprint "X" (version 20240108) (generator "pcbnew") (generator_version "8.0") (layer "F.Cu") (uuid "1111")\n' \
        '  (pad "1" smd rect (at 0.10000001 -0) (size 1 2) (layers "F.Cu") (uuid "aaaa")))'
    b = a.replace("1111", "2222").replace("aaaa", "bbbb").replace('"8.0"', '"9.0"').replace("0.10000001", "0.1")
    assert GO.kicad_mod(a) == GO.kicad_mod(b)
    assert GO.kicad_mod(a)[-1] == "(pad 1 smd rect (at 0.1 0) (size 1 2) (layers F.Cu))"
    assert GO.kicad_mod(a) != GO.kicad_mod(a.replace("(size 1 2)", "(size 1 2.1)"))
    # Altium: written at two different dates (SOURCE_DATE_EPOCH, as a run on another day) -> same fingerprint
    from ecadlib import altium_lib as AL, footprint_bench as FB, kicad_import as K
    txt, _ = F.build(json.loads(FB.spec_path("SOIC-8_3.9x4.9mm_P1.27mm").read_text()))
    fps = []
    with tempfile.TemporaryDirectory() as d:
        for i, epoch in enumerate(("1000000000", "1900000000")):
            fp, t = K.convert_footprint(txt, "SOIC-8", "t", [])
            with mock.patch.dict(os.environ, {"SOURCE_DATE_EPOCH": epoch}):
                AL.write_pcblib(Path(d) / f"{i}.PcbLib", [fp])
            fps.append(GO.pcblib(Path(d) / f"{i}.PcbLib"))
        assert (Path(d) / "0.PcbLib").read_bytes() != (Path(d) / "1.PcbLib").read_bytes()    # the dates differ in the file
    assert fps[0] == fps[1] and any(l.startswith("pad 1 at=(") for l in fps[0]["SOIC-8"])
    assert not any("UNIQUEID" in l or "MODELID" in l for v in fps[0].values() for l in v)


def test_golden_diff_is_readable_and_update_round_trips():
    """A pad that moved and a removed part show up as such; --update writes what check then accepts."""
    with tempfile.TemporaryDirectory() as d:
        assert EC.main(["golden", "--group", "demo", "--dir", d, "--update"]) == 0
        f = Path(d) / "demo.txt"
        assert EC.main(["golden", "--group", "demo", "--dir", d]) == 0
        old = GO.loads(f.read_text())
        key = next(k for k in old if k.endswith("SOT-23.kicad_mod"))
        i = next(j for j, l in enumerate(old[key]) if l.startswith("(pad 1 "))
        old[key][i] = old[key][i].replace("(at ", "(at 9", 1)
        gone = next(k for k in old if ".SchLib:" in k)
        f.write_text(GO.dumps(old) + f"### {gone}-extra\nx\n")
        res = GO.check(["demo"], d)["demo"]
        assert f"~ changed: {key}" in res and f"- removed: {gone}-extra" in res, res
        assert "    -(pad 1 smd roundrect (at 9" in res and "    +(pad 1 smd roundrect (at " in res, res
        assert EC.main(["golden", "--group", "demo", "--dir", d]) == 1
        assert EC.main(["golden", "--group", "demo", "--dir", d, "--update"]) == 0
        assert GO.check(["demo"], d)["demo"] == ""


def test_golden_catches_an_exporter_change():
    """A change in the generator (pad size of every gullwing land 0.01 mm wider) changes the specs snapshot."""
    real = F.build

    def wider(spec, density=None):
        txt, rep = real(spec, density)
        return (txt.replace("(size 1.475 0.6)", "(size 1.485 0.6)") if spec.get("name") == "SOT-23" else txt), rep
    with mock.patch.object(F, "build", wider):
        new = GO.build("specs")
    old = GO.loads(GO.path("specs").read_text(encoding="utf-8"))
    txt = GO.diff(old, new)
    assert "~ changed: spec/SOT-23.kicad_mod" in txt and "size 1.485 0.6" in txt, txt
    assert "~ changed: spec/SOT-23.PcbLib:SOT-23" in txt and "size=(1.485,0.6)" in txt, txt


if __name__ == "__main__":
    for k, f in list(globals().items()):
        if k.startswith("test_") and callable(f):
            f(); print("ok", k)
