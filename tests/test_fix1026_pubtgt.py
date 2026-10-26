"""Fixes 2026-10-26 (pubtgt): publish targets shared between plan folders (a DbLib folder, an Altium 365 workspace).
Change control is per plan folder, so a target two plans publish to must never let one plan change what the other
released. Plain functions: python -m tests.run_all test_fix1026_pubtgt. Altium 365 / Nexar is always a fake here."""
import json
from pathlib import Path

from tests.fixtures_lite import lite

ROOT = Path(__file__).parent.parent


# ---------------------------------------------------------------- R-FL-1: one DbLib folder, two plan folders
def _plan_folder(d, items, land=None):
    """A plan folder d with Resistor.SchLib and R_0603_1608Metric.PcbLib (land: changes to the 0603 spec, as a newer
    upstream footprint would have), the items released anyway; -> plan.json."""
    from ecadlib import altium_lib as AL
    from tests.test_dblib_sync import _plan
    (d / "models" / "SchLib").mkdir(parents=True)
    (d / "models" / "PcbLib").mkdir(parents=True)
    AL.write_schlib(d / "models/SchLib/Resistor.SchLib", [AL.resistor_symbol()])
    spec = json.loads((ROOT / "ecadlib/package_specs/R_0603_1608Metric.json").read_text())
    spec.update(land or {})
    AL.write_pcblib(d / "models/PcbLib/R_0603_1608Metric.PcbLib", [AL.footprint_from_spec(spec)[0]])
    return _plan(d, items)


def _res(name, mpn, maker="Yageo", value=None):
    from tests.test_dblib_sync import _item
    return _item(name, "Resistors", "Resistor", "R_0603_1608Metric", Value=value or name.rsplit("-", 1)[-1],
                 Manufacturer=maker, Manufacturer_Part_Number=mpn)


def _rows(lib):
    from ecadlib import altium_dblib_sync as S
    return {r["Part Number"]: r for _, (_, rs) in S.read_tables(lib / "ecadlib.xlsx").items() for r in rs}


def _acts(out):
    return {r["name"]: r["action"] for r in out["results"] if r.get("kind") is None}


@lite
def test_dblib_second_plan_does_not_replace_a_footprint_another_plans_rows_use(tmp_path):
    """Board A and board B (two plan folders) sync into one DbLib folder. B's build of R_0603_1608Metric has bigger
    pads: replacing the file gave A's released RES-0603-10k B's land with no hold (A's ledger still said r1 went
    there). The file another plan's rows use is not replaced: B's part is an error naming the conflict."""
    from ecadlib import altium_dblib_sync as S
    lib = tmp_path / "dblib"
    a = _plan_folder(tmp_path / "A", [_res("RES-0603-10k", "RC0603FR-0710KL")])
    b = _plan_folder(tmp_path / "B", [_res("RES-0603-1k", "RC0603FR-071KL")], land={"terminal_length": {"min": 0.3, "max": 0.6}})
    assert _acts(S.sync(a, lib)) == {"RES-0603-10k": "added"}
    before = (lib / "PcbLib/R_0603_1608Metric.PcbLib").read_bytes()
    out = S.sync(b, lib)
    res = next(r for r in out["results"] if r["name"] == "RES-0603-1k")
    assert res["action"] == "error" and "RES-0603-10k" in res["detail"] and "another plan" in res["detail"], res
    assert (lib / "PcbLib/R_0603_1608Metric.PcbLib").read_bytes() == before
    assert set(_rows(lib)) == {"RES-0603-10k"}
    # B with the same land as A's (same bytes): shared, both rows there
    b2 = _plan_folder(tmp_path / "B2", [_res("RES-0603-1k", "RC0603FR-071KL")])
    out = S.sync(b2, lib)
    assert _acts(out)["RES-0603-1k"] == "added", out["results"]
    assert set(_rows(lib)) == {"RES-0603-10k", "RES-0603-1k"}
    # now A's land changes (approved in A): B2's released RES-0603-1k uses the file, so A cannot replace it either
    a2 = _plan_folder(tmp_path / "A2", [_res("RES-0603-10k", "RC0603FR-0710KL")], land={"terminal_length": {"min": 0.3, "max": 0.6}})
    import shutil
    shutil.rmtree(tmp_path / "A"); shutil.move(tmp_path / "A2", tmp_path / "A")
    res = next(r for r in S.sync(a, lib)["results"] if r["name"] == "RES-0603-10k")
    assert res["action"] == "error" and "RES-0603-1k" in res["detail"], res
    assert (lib / "PcbLib/R_0603_1608Metric.PcbLib").read_bytes() == before


@lite
def test_dblib_one_plan_still_updates_its_own_shared_footprint(tmp_path):
    """No false refusal: within one plan folder, an approved land change rewrites the footprint every row of that plan
    uses, also a row of a part the plan has dropped since (kept, not pruned)."""
    from ecadlib import altium_dblib_sync as S
    lib = tmp_path / "dblib"
    a = _plan_folder(tmp_path / "A", [_res("RES-0603-10k", "RC0603FR-0710KL"), _res("RES-0603-1k", "RC0603FR-071KL")])
    S.sync(a, lib)
    import shutil
    shutil.rmtree(tmp_path / "A")
    a = _plan_folder(tmp_path / "A", [_res("RES-0603-10k", "RC0603FR-0710KL")], land={"terminal_length": {"min": 0.3, "max": 0.6}})
    before = (lib / "PcbLib/R_0603_1608Metric.PcbLib").read_bytes()
    out = S.sync(a, lib)
    assert _acts(out)["RES-0603-10k"] == "updated", out["results"]
    assert (lib / "PcbLib/R_0603_1608Metric.PcbLib").read_bytes() != before


@lite
def test_dblib_same_part_name_with_another_mpn_from_another_plan_is_held(tmp_path):
    """A third BOM gives the generated name RES-0603-1k to a Vishay part: its sync rewrote the Yageo row of the plan
    that released it (Manufacturer, Manufacturer Part Number) in place. An identity change of another plan's row is
    not applied; the same plan's own approved change still is."""
    from ecadlib import altium_dblib_sync as S
    lib = tmp_path / "dblib"
    b = _plan_folder(tmp_path / "B", [_res("RES-0603-1k", "RC0603FR-071KL")])
    S.sync(b, lib)
    c = _plan_folder(tmp_path / "C", [_res("RES-0603-1k", "CRCW06031K00FKEA", maker="Vishay")])
    out = S.sync(c, lib)
    res = next(r for r in out["results"] if r["name"] == "RES-0603-1k")
    assert res["action"] == "held" and "identity change" in res["detail"], res
    row = _rows(lib)["RES-0603-1k"]
    assert (row["Manufacturer"], row["Manufacturer Part Number"]) == ("Yageo", "RC0603FR-071KL"), row
    # B's own correction (its change control signed it off): applied
    import shutil
    shutil.rmtree(tmp_path / "B")
    b = _plan_folder(tmp_path / "B", [_res("RES-0603-1k", "RC0603FR-07L1KL")])
    assert _acts(S.sync(b, lib))["RES-0603-1k"] == "updated"
    assert _rows(lib)["RES-0603-1k"]["Manufacturer Part Number"] == "RC0603FR-07L1KL"


@lite
def test_dblib_prune_keeps_rows_another_plan_wrote(tmp_path):
    """--prune from plan B deleted plan A's rows (they are "not in B's plan"): only rows this plan wrote are pruned."""
    from ecadlib import altium_dblib_sync as S
    lib = tmp_path / "dblib"
    a = _plan_folder(tmp_path / "A", [_res("RES-0603-10k", "RC0603FR-0710KL")])
    b = _plan_folder(tmp_path / "B", [_res("RES-0603-1k", "RC0603FR-071KL")])
    S.sync(a, lib)
    S.sync(b, lib)
    out = S.sync(b, lib, prune=True)
    assert set(_rows(lib)) == {"RES-0603-10k", "RES-0603-1k"}, out["results"]
    r = next(r for r in out["results"] if r["name"] == "RES-0603-10k")
    assert r["action"] == "not_in_plan" and "another plan" in r["detail"], r


# ---------------------------------------------------------------- R-FL-2: an Altium 365 item this library never released
def _ws_fake():
    """FakeA365 (it keeps the model items released, with their bytes: model_data is Nexar's dataDownloadUrl) that
    records each read-back."""
    from tests.test_change_control import FakeA365

    class WS(FakeA365):
        downloads = []

        def model_data(self, ws, kind, name, folder):
            self.downloads.append((kind, name))
            return super().model_data(ws, kind, name, folder)
    return WS()


def _other_r0603(path):
    """An R_0603_1608Metric.PcbLib with another land (bigger terminals), as another plan folder or a person released."""
    from ecadlib import altium_lib as AL
    spec = json.loads((ROOT / "ecadlib/package_specs/R_0603_1608Metric.json").read_text())
    spec["terminal_length"] = {"min": 0.3, "max": 0.6}
    AL.write_pcblib(path, [AL.footprint_from_spec(spec)[0]])
    return Path(path).read_bytes()


@lite
def test_a365_unknown_workspace_footprint_with_another_drawing_is_not_linked(tmp_path):
    """The workspace already has an R_0603_1608Metric footprint item this library never released (another plan folder
    pushed it, or someone made it): the push linked it by name, released RES-0603-10k on a land its release check
    never saw, and recorded `publish r1`. Now the item is read back and compared: another drawing holds the part."""
    from ecadlib import altium365 as A, ledger as LG, rules as RU
    from tests.test_change_control import demo
    from tests.test_hunt1026_altpub import _push
    plan, lib = demo()
    nx = _ws_fake()
    nx.models["footprints"]["R_0603_1608Metric"] = _other_r0603(tmp_path / "R_0603_1608Metric.PcbLib")
    ws = "https://test.365.altium.com"
    res = _push(A, plan, lib, nx, ws, RU.load(None, start=lib))
    for n in ("RES-0603-10k", "RES-0603-4.7k"):
        assert res[n]["action"] == "skip" and "R_0603_1608Metric" in res[n]["detail"] and "another drawing" in res[n]["detail"], res[n]
        assert n not in nx.comps and LG.Ledger(lib).published(n, A.ws_target(ws)) is None
    assert ("footprints", "R_0603_1608Metric") in nx.downloads
    assert res["AO3401A"]["action"] == "release"          # the other parts go up as before


@lite
def test_a365_unknown_workspace_footprint_with_the_same_drawing_is_linked(tmp_path):
    """No false hold: the item holds this part's drawing (the same file another plan folder released) -> linked."""
    from ecadlib import altium365 as A, rules as RU
    from tests.test_change_control import demo
    from tests.test_hunt1026_altpub import _push
    plan, lib = demo()
    nx = _ws_fake()
    nx.models["footprints"]["R_0603_1608Metric"] = (lib / "models/PcbLib/R_0603_1608Metric.PcbLib").read_bytes()
    res = _push(A, plan, lib, nx, "https://test.365.altium.com", RU.load(None, start=lib))
    assert res["RES-0603-10k"]["action"] == "release", res["RES-0603-10k"]
    rel = nx.comps["RES-0603-10k"]["revisions"][0]
    assert rel["footprints"] == [{"id": "footprints:R_0603_1608Metric"}] and not rel["footprintFiles"], rel


@lite
def test_a365_unknown_workspace_item_that_does_not_read_back_holds(tmp_path):
    """A download that fails (or a file that does not read) can't show the drawing is this part's: held, not linked."""
    from ecadlib import altium365 as A, rules as RU
    from tests.test_change_control import demo
    from tests.test_hunt1026_altpub import _push
    plan, lib = demo()
    nx = _ws_fake()
    nx.models["footprints"]["R_0603_1608Metric"] = b"not a PcbLib"
    res = _push(A, plan, lib, nx, "https://test.365.altium.com", RU.load(None, start=lib))
    assert res["RES-0603-10k"]["action"] == "skip" and "R_0603_1608Metric" in res["RES-0603-10k"]["detail"], res["RES-0603-10k"]
    assert "RES-0603-10k" not in nx.comps


def test_a365_model_data_reads_the_item_through_its_download_url():
    """Nexar.model_data: the newest revision of the item in its folder, by its dataDownloadUrl (the query validates
    against Nexar's schema); the file reads back raw or zipped to the same drawing digest."""
    import io, tempfile, zipfile
    from ecadlib import altium365 as A
    try:
        from graphql import build_schema, parse, validate
        schema = build_schema((ROOT / "tests/nexar/schema.graphql").read_text(), assume_valid_sdl=True)
        for q in A.Q_MODEL_DATA.values():
            assert not validate(schema, parse(q)), q
    except ImportError:
        pass
    with tempfile.TemporaryDirectory() as t:
        data = _other_r0603(Path(t) / "x.PcbLib")

    class N(A.Nexar):
        def __init__(self):
            self.token, self.got = "t", []

        def gql(self, query, variables=None):
            assert "dataDownloadUrl" in query
            return {"desLibrary": {"footprints": {"nodes": [
                {"id": "a", "name": "R_0603_1608Metric-1", "releasedAt": "2026-01-01", "folder": {"path": "C/Models/Footprints"}, "dataDownloadUrl": "https://dl/1"},
                {"id": "b", "name": "R_0603_1608Metric-2", "releasedAt": "2026-02-01", "folder": {"path": "C/Models/Footprints"}, "dataDownloadUrl": "https://dl/2"}]}}}

        def download(self, url):
            self.got.append(url)
            return data
    n = N()
    assert n.model_data("ws", "footprints", "R_0603_1608Metric", "C/Models/Footprints") == data and n.got == ["https://dl/2"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Released/R_0603_1608Metric.PcbLib", data)
    raw = A.model_file_digest(data, "footprints", "R_0603_1608Metric")
    assert raw and A.model_file_digest(buf.getvalue(), "footprints", "R_0603_1608Metric") == raw
    try:
        A.model_file_digest(b"junk", "footprints", "R_0603_1608Metric")
    except ValueError:
        pass
    else:
        raise AssertionError("junk read as a footprint")


# ---------------------------------------------------------------- R-FX11-1: a component another plan folder released
def _two_plans(change_b=False):
    """Plan folders A and B (two BOMs), both with RES-0603-10k; change_b: B's Resistor symbol is another drawing
    (approved in B: its head revision). -> (planA, libA, planB, libB)"""
    from ecadlib import change_cli as CC, change_gate as CG
    from tests.test_change_control import ANN, demo, gate, item, items_of, write_symbol
    pa, la = demo()
    pb, lb = demo()
    if change_b:
        items = items_of(pb)
        g = gate(lb)
        write_symbol(lb, item(items, "RES-0603-10k"), [("1", "A", "passive"), ("2", "B", "passive")])
        g.settle(items)
        CG.write_plan(pb, items)
        CC.approve(pb, ["RES-0603-10k", "RES-0603-4.7k"], who=ANN)
    return pa, la, pb, lb


@lite
def test_a365_component_another_plan_released_with_another_drawing_is_held():
    """Two BOMs both have RES-0603-10k; B's drawing differs. B's first push found the component, saw no publish line
    in B's ledger and released a new revision of A's released component with B's drawing, no hold. Now the
    component's models are read back: another drawing than any revision of B's holds the part, nothing is sent."""
    from ecadlib import altium365 as A, ledger as LG, rules as RU
    from tests.test_hunt1026_altpub import Workspace, _push
    pa, la, pb, lb = _two_plans(change_b=True)
    nx, ws = Workspace(), "https://t.365.altium.com"
    only = lambda p: [i for i in json.loads(Path(p).read_text()) if i["name"] == "RES-0603-10k"]
    assert _push(A, pa, la, nx, ws, RU.load(None, start=la), items=only(pa))["RES-0603-10k"]["action"] == "release"
    nx.calls.clear()
    res = _push(A, pb, lb, nx, ws, RU.load(None, start=lb), items=only(pb))["RES-0603-10k"]
    assert res["action"] == "skip" and "another drawing" in res["detail"], res
    assert len(nx.comps["RES-0603-10k"]["revisions"]) == 1
    assert not [c for c in nx.calls if c[0] in ("release", "upload", "update_params")], nx.calls
    assert LG.Ledger(lb).published("RES-0603-10k", A.ws_target(ws)) is None


@lite
def test_a365_component_another_plan_released_with_the_same_drawing_is_linked_not_revised():
    """Identical drawings: B's push made a needless new revision and duplicate -r1 model items (a third plan then
    failed every run on the duplicate). Now it is this part already: no new revision, B records the publish, and
    a third plan does the same."""
    from ecadlib import altium365 as A, ledger as LG, rules as RU
    from tests.test_change_control import demo
    from tests.test_hunt1026_altpub import Workspace, _push
    pa, la, pb, lb = _two_plans()
    pc, lc = demo()
    nx, ws = Workspace(), "https://t.365.altium.com"
    only = lambda p: [i for i in json.loads(Path(p).read_text()) if i["name"] == "RES-0603-10k"]
    assert _push(A, pa, la, nx, ws, RU.load(None, start=la), items=only(pa))["RES-0603-10k"]["action"] == "release"
    for p, l in ((pb, lb), (pc, lc), (pc, lc)):
        nx.calls.clear()
        res = _push(A, p, l, nx, ws, RU.load(None, start=l), items=only(p))["RES-0603-10k"]
        assert res["action"] in ("up_to_date", "update_parameters"), res
        assert not [c for c in nx.calls if c[0] in ("release", "upload")], nx.calls
        assert LG.Ledger(l).published("RES-0603-10k", A.ws_target(ws)) is not None
    assert len(nx.comps["RES-0603-10k"]["revisions"]) == 1
    assert not [k for k in nx.wmodels if k[1].endswith("-r1")], nx.wmodels
