"""Offline tests for ecadlib.house_style (`ecadlib style`): learn a house style from a library, adapt plan parts to it
by code, and with a model (a fake one here: no network, no key) for every tier.
Run:  python3 -m tests.test_house_style"""
import contextlib, copy, io, json, tempfile
from pathlib import Path

from ecadlib import altium_lib as AL, house_style as H, rules as RU
from ecadlib.app import Workspace
from tests import rules_fixtures as RF

KICAD_SYM = """(kicad_symbol_lib (version 20231120) (generator test)
  (symbol "TPS3839" (property "Reference" "U") (property "Value" "TPS3839") (property "Footprint" "SOT-23")
    (property "Description" "VOLTAGE SUPERVISOR 3.3V SOT-23") (property "MPN" "TPS3839K33DBZR")
    (symbol "TPS3839_1_1"
      (pin output line (at 0 0 0) (length 2.54) (name "~{RESET}" (effects)) (number "1" (effects)))
      (pin power_in line (at 0 2 0) (length 2.54) (name "VDD" (effects)) (number "3" (effects)))))
  (symbol "SN74LVC1G125" (property "Reference" "U") (property "Value" "SN74LVC1G125") (property "Footprint" "SOT-23-5")
    (property "Description" "BUFFER 1-BIT SOT-23-5") (property "MPN" "SN74LVC1G125DBVR")
    (symbol "SN74LVC1G125_1_1"
      (pin input line (at 0 0 0) (length 2.54) (name "~{OE}" (effects)) (number "1" (effects)))
      (pin input line (at 0 2 0) (length 2.54) (name "A" (effects)) (number "2" (effects)))))
  (symbol "W25Q32" (property "Reference" "U") (property "Value" "W25Q32") (property "Footprint" "SOIC-8")
    (property "Description" "FLASH 32MBIT SPI SOIC-8") (property "MPN" "W25Q32JVSSIQ")
    (symbol "W25Q32_1_1"
      (pin input line (at 0 0 0) (length 2.54) (name "~{CS}" (effects)) (number "1" (effects)))
      (pin input line (at 0 2 0) (length 2.54) (name "~{WP}" (effects)) (number "3" (effects)))
      (pin input line (at 0 4 0) (length 2.54) (name "~{HOLD}" (effects)) (number "7" (effects)))))
)
"""


def quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()) as o, contextlib.redirect_stderr(io.StringIO()):
        rc = fn(*a, **k)
    return rc, o.getvalue()


def altium_house(d):
    """Resistors 'RES 10K ±1% 0603' with Tolerance '±1%'; caps with 'Voltage' '16 V'; an IC with barred pins."""
    d = Path(d)
    syms = []
    for size, val, tol in RF.RES:
        s = RF.sym(f"RES_{size}_{val}", "R", {"MPN": RF.MPN_R[val], "Tolerance": "±" + tol, "Power": "0.1 W"}, f"R{size}")
        s["description"] = f"RES {val} ±{tol} {size}"
        syms.append(s)
    for size, val, v, diel in RF.CAPS:
        s = RF.sym(f"CAP_{size}_{val}", "C", {"MPN": RF.MPN_C[val], "Voltage": v.replace("V", " V"), "Dielectric": diel},
                   f"C{size}")
        s["description"] = f"CAP {val} {v} {diel} {size}"
        syms.append(s)
    ic = AL.ic_symbol("TPS3839", [{"num": "1", "name": "~{RESET}", "side": "right"}, {"num": "2", "name": "GND", "side": "left"},
                                 {"num": "3", "name": "VDD", "side": "left"}, {"num": "4", "name": "~{MR}", "side": "left"},
                                 {"num": "5", "name": "~{WDI}", "side": "left"}], designator="U?")
    syms.append(ic)
    (d / "SchLib").mkdir(parents=True, exist_ok=True)
    AL.write_schlib(d / "SchLib" / "House.SchLib", syms)
    return d


def plan_items():
    return [{"name": "RES-0603-10k", "folder": "Components/Resistors", "description": "Resistor 10k 1% 0603", "comment": "=Value",
             "parameters": [{"name": "Value", "value": "10k"}, {"name": "Tolerance", "value": "1%"},
                            {"name": "Manufacturer", "value": "Yageo"},
                            {"name": "Manufacturer Part Number", "value": "RC0603FR-0710KL"}],
             "footprint": {"ref": "R_0603_1608Metric"}, "symbol": {"ref": "Resistor"}},
            {"name": "CAP-0402-100nF", "folder": "Components/Capacitors", "description": "Capacitor 100nF 16V X7R 0402",
             "comment": "=Value", "parameters": [{"name": "Value", "value": "100nF"}, {"name": "Voltage", "value": "16V"},
                                                 {"name": "Manufacturer Part Number", "value": "CL05B104KO5NNNC"}],
             "footprint": {"ref": "C_0402_1005Metric"}, "symbol": {"ref": "Capacitor"}},
            {"name": "LM358DR", "folder": "Components/ICs", "description": "Dual op amp, 3-32V, SOIC-8", "comment": "LM358DR",
             "parameters": [{"name": "Manufacturer", "value": "Texas Instruments"},
                            {"name": "Manufacturer Part Number", "value": "LM358DR"}],
             "footprint": {"ref": "SOIC-8_3.9x4.9mm_P1.27mm"}, "symbol": {"ref": "LM358DR"}}]


def test_pin_notation_round_trips():
    for name, base, st in (("R\\E\\S\\E\\T\\", "RESET", "overbar"), ("~{RESET}", "RESET", "overbar"), ("RESET#", "RESET", "hash"),
                           ("/CS", "CS", "slash"), ("nRESET", "RESET", "n_prefix"), ("RST_N", "RST", "suffix_n"),
                           ("R̅S̅T̅", "RST", "overbar")):
        assert H.pin_low(name) == (base, st), name
        for style in H.PIN_STYLES:
            out = H.convert_pin(name, style)
            assert H.pin_low(out) == (base, style), (name, style, out)
    for plain in ("VCC", "GND", "EN", "CS/SHDN", "SDA", "nc", "PA0"):
        assert H.pin_low(plain) == (plain, None) and H.convert_pin(plain, "hash") == plain


def test_learn_from_an_altium_library_and_adapt_a_plan():
    with tempfile.TemporaryDirectory() as td:
        lib = altium_house(Path(td) / "lib")
        prof = H.learn([lib], direction=["IC descriptions are upper case"])
        assert prof["components"] == len(RF.RES) + len(RF.CAPS) + 1
        assert prof["pins"]["active_low"] == "overbar", prof["pins"]
        assert prof["descriptions"]["Resistors"]["template"] == "{cat3} {value} {tolerance} {package}", prof["descriptions"]
        assert prof["param_formats"]["Tolerance"]["plus_minus"] is True
        assert prof["param_formats"]["Voltage"]["space"] is True
        assert prof["direction"][0]["text"] == "IC descriptions are upper case"
        assert prof["examples"]["Resistors"] and all("MPN" in e["parameters"] for e in prof["examples"]["Resistors"])
        prof["_file"] = str(Path(td) / "house_style.json")
        items = plan_items()
        found = {f["field"]: f for f in H.check_item(items[0], prof)}
        assert found["description"]["want"] == "RES 10K ±1% 0603", found
        assert found["parameters.Tolerance"]["want"] == "±1%"
        done = H.adapt_items(items, prof)
        assert items[0]["description"] == "RES 10K ±1% 0603"
        assert {p["name"]: p["value"] for p in items[1]["parameters"]}["Voltage"] == "16 V"
        assert items[1]["description"] == "CAP 100N 16V X7R 0402", items[1]["description"]
        assert items[0]["house_style"]["changes"][0]["was"] == "Resistor 10k 1% 0603"
        assert "LM358DR" not in done                                   # nothing exact to do for a free-text IC
        again = copy.deepcopy(items)
        assert H.adapt_items(again, prof) == {} and again == items     # idempotent
        assert {p["value"] for p in items[0]["parameters"]} >= {"Yageo", "RC0603FR-0710KL"}   # identity facts kept
        sym, ren = H.adapt_symbol({"pins": [{"num": "1", "name": "RESET#"}, {"num": "2", "name": "VDD"}]}, prof)
        assert [p["name"] for p in sym["pins"]] == ["~{RESET}", "VDD"] and ren == ["RESET# -> ~{RESET}"]


def test_kicad_pins_and_descriptions_are_learned():
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "house.kicad_sym"
        f.write_text(KICAD_SYM)
        prof = H.learn([f])
        assert prof["pins"]["active_low"] == "overbar" and prof["pins"]["active_low_pins"] == 5, prof["pins"]
        assert prof["descriptions"]["ICs"]["case"] == "upper"


def test_rules_style_section():
    RU.validate({"style": {"apply": "off", "provider": "ollama", "model": "qwen2.5:7b", "profile": "team/style.json"}})
    for bad in ({"apply": "always"}, {"profile": "style.toml"}, {"model": "bad model!"}, {"base_url": "ftp://x"}):
        try:
            RU.validate({"style": bad})
        except RU.RulesError:
            continue
        raise AssertionError(f"accepted {bad}")
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "ecadlib.toml").write_text('[style]\napply = "off"\n')
        H.save({"schema_version": 1, "direction": []}, Path(td) / "house_style.json")
        rules = RU.load(start=td)
        assert H.profile_path(rules) == Path(td) / "house_style.json"
        assert H.load(rules) is not None and H.active(rules) is None


class FakeModel:
    """Answers like a model would, records what it was sent."""
    def __init__(self, answers):
        self.answers, self.bodies = list(answers), []

    def __call__(self, body):
        self.bodies.append(body)
        a = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]     # the last answer repeats
        text = a if isinstance(a, str) else json.dumps(a)
        return {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn",
                "usage": {"input_tokens": 100, "output_tokens": 50}}


def _rules(model):
    if model.startswith("claude"):
        return RU.load({"style": {"model": model}})
    return RU.load({"style": {"provider": "ollama", "model": model}, "ai": {"detect": False}})


GUIDE = {"guide": ["Write IC descriptions in upper case: FUNCTION RATING PACKAGE"],
         "settings": [{"key": "naming.case", "value": "upper"}, {"key": "naming.value_style", "value": "fancy"},
                      {"key": "layers.courtyard", "value": "Mechanical1"}],
         "pin_style": "hash", "description_templates": [{"category": "Resistors", "template": "R {value} {nonsense}"}],
         "conflicts": ["Today 40% of IC descriptions are mixed case."]}


def test_guide_scales_with_the_model_and_checks_its_settings():
    with tempfile.TemporaryDirectory() as td:
        prof = H.learn([altium_house(Path(td) / "lib")], direction=["IC descriptions are upper case", "pins as RESET#"])
        big = FakeModel([GUIDE])
        g = H.write_guide(prof, _rules("claude-opus-5-5"), send=big)
        b = big.bodies[0]
        assert b["output_config"]["format"]["type"] == "json_schema"           # frontier: the schema as response format
        assert "Example of the shape" not in b["messages"][0]["content"][0]["text"]
        assert g["settings"] == [{"key": "naming.case", "value": "upper"}]       # unknown value / key not allowed: dropped
        assert len(g["dropped"]) == 3 and any("nonsense" in d for d in g["dropped"])
        assert g["pin_style"] == "hash" and H.pin_style(prof) == "hash" and g["tier"] == "frontier"
        assert H.setting(prof, "naming.case") == "upper"
        small = FakeModel(["Sure! ```json\n" + json.dumps(GUIDE) + ",\n```", ])
        prof2 = copy.deepcopy(prof)
        g2 = H.write_guide(prof2, _rules("house-tuned-1"), send=small)      # unknown model: small, no JSON schema
        t = small.bodies[0]["messages"][0]["content"][0]["text"]
        assert "output_config" not in small.bodies[0] and "Answer with ONE JSON object" in t   # schema in the prompt
        assert "Example of the shape" in t and g2["tier"] == "small" and g2["settings"] == g["settings"]   # fence repaired
        q = FakeModel([GUIDE])
        H.write_guide(copy.deepcopy(prof), _rules("qwen2.5:7b"), send=q)    # a small model with a JSON-schema format
        assert "output_config" in q.bodies[0] and "Example of the shape" in q.bodies[0]["messages"][0]["content"][0]["text"]
        n_small = t.count("\n- Resistors |")
        n_big = b["messages"][0]["content"][0]["text"].count("\n- Resistors |")
        assert n_small == H.TIER["small"]["examples"] and n_big > n_small
        retry = FakeModel(["not json at all", GUIDE])                            # one retry with the exact error
        H.write_guide(copy.deepcopy(prof), _rules("house-tuned-1"), send=retry)
        assert len(retry.bodies) == 2 and "does not fit" in retry.bodies[1]["messages"][-1]["content"][0]["text"]
        try:
            H.write_guide(copy.deepcopy(prof), _rules("house-tuned-1"), send=FakeModel(["no", "still no"]))
        except H.StyleError:
            pass
        else:
            raise AssertionError("a bad answer was accepted")


def test_model_rewording_is_checked_for_new_facts():
    with tempfile.TemporaryDirectory() as td:
        prof = H.learn([altium_house(Path(td) / "lib")], direction=["IC descriptions are upper case"])
        prof["_file"] = str(Path(td) / "house_style.json")
        items = plan_items()
        ans = {"parts": [{"name": "LM358DR", "description": "DUAL OP AMP 3-32V SOIC-8", "comment": "LM358DR",
                          "parameters": [{"name": "Manufacturer", "value": "TI"}, {"name": "Gain", "value": "100"}]},
                         {"name": "RES-0603-10k", "description": "RES 10K ±1% 0603 100mW", "comment": "10K",
                          "parameters": [{"name": "Tolerance", "value": "±1%"}]},
                         {"name": "CAP-0402-100nF", "description": "CAP 100N 16V X7R 0402", "comment": "=Value",
                          "parameters": []}]}
        fake = FakeModel([ans])
        done, rejected = H.adapt_with_model(items, prof, _rules("claude-opus-5-5"), send=fake)
        shown = fake.bodies[0]["messages"][0]["content"][0]["text"]
        assert "RC0603FR-0710KL" not in shown and "Texas Instruments" not in shown    # identity facts never shown
        lm = next(i for i in items if i["name"] == "LM358DR")
        assert lm["description"] == "DUAL OP AMP 3-32V SOIC-8"
        assert {p["name"]: p["value"] for p in lm["parameters"]}["Manufacturer"] == "Texas Instruments"   # locked
        assert not any(p["name"] == "Gain" for p in lm["parameters"])                                     # no new params
        res = next(i for i in items if i["name"] == "RES-0603-10k")
        assert res["description"] == "Resistor 10k 1% 0603" and res["comment"] == "=Value"   # 100mW: a new fact; =Value kept
        assert any("adds 100" in r for r in rejected), rejected
        assert lm["house_style"]["model"] == "claude-opus-5-5"
        small = FakeModel([{"parts": [{"name": i["name"], "description": i["description"], "comment": i["comment"],
                                       "parameters": []}]} for i in plan_items()])
        H.adapt_with_model(plan_items(), prof, _rules("qwen2.5:7b"), send=small)
        assert len(small.bodies) == 3                                               # small: one part per request


def test_cli_learn_direct_check_apply_and_dataset():
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        lib = altium_house(d / "lib")
        (d / "ecadlib.toml").write_text("")
        pf, plan = d / "house_style.json", d / "plan.json"
        plan.write_text(json.dumps(plan_items()))
        rc, out = quiet(H.main, ["--rules", str(d / "ecadlib.toml"), "learn", "--from", str(lib)])
        assert rc == 0 and pf.is_file() and "description Resistors" in out, out
        rc, out = quiet(H.main, ["--rules", str(d / "ecadlib.toml"), "direct", "IC descriptions are upper case"])
        assert rc == 0 and "1. IC descriptions are upper case" in out
        rc, out = quiet(H.main, ["--rules", str(d / "ecadlib.toml"), "check", str(plan)])
        assert rc == 0 and "of 3 parts match" in out and "RES 10K" in out, out
        rc, out = quiet(H.main, ["--rules", str(d / "ecadlib.toml"), "apply", str(plan), "--dry-run"])
        assert rc == 0 and json.loads(plan.read_text()) == plan_items()
        rc, out = quiet(H.main, ["--rules", str(d / "ecadlib.toml"), "apply", str(plan)])
        assert rc == 0 and json.loads(plan.read_text())[0]["description"] == "RES 10K ±1% 0603"
        assert json.loads((d / "plan.json.style.bak").read_text()) == plan_items()
        ans = {"parts": [{"name": "LM358DR", "description": "DUAL OP AMP 3-32V SOIC-8", "comment": "LM358DR", "parameters": []}]}
        rc, out = quiet(H.main, ["--rules", str(d / "ecadlib.toml"), "apply", str(plan), "--ai", "--part", "LM358DR",
                                 "--model", "claude-opus-5-5"], send=FakeModel([ans]))
        assert rc == 0 and json.loads(plan.read_text())[2]["description"] == "DUAL OP AMP 3-32V SOIC-8", out
        rc, out = quiet(H.main, ["--rules", str(d / "ecadlib.toml"), "export-dataset", "-o", str(d / "t.jsonl"),
                                 "--eval-share", "0.3"])
        assert rc == 0, out
        rows = [json.loads(x) for x in (d / "t.jsonl").read_text().splitlines()] + \
               [json.loads(x) for x in (d / "t.eval.jsonl").read_text().splitlines()]
        assert len(rows) == len(RF.RES) + len(RF.CAPS) + 1
        r0 = rows[0]["messages"]
        assert [m["role"] for m in r0] == ["system", "user", "assistant"]
        assert "name" in json.loads(r0[2]["content"]) and "category" in json.loads(r0[1]["content"])
        again = H.write_dataset(H.dataset_rows(H.load(path=pf)), d / "u.jsonl", 0.3)   # the split is stable
        assert (d / "u.jsonl").read_text() == (d / "t.jsonl").read_text() and again["n"] + again["n_eval"] == len(rows)
        rc, out = quiet(H.main, ["--rules", str(d / "ecadlib.toml"), "learn", "--from", str(lib), "--write-rules",
                                 "--force"])
        assert rc == 0 and "naming" in (d / "ecadlib.toml").read_text()


def test_workspace_and_mcp():
    from ecadlib import mcp_server as M
    with tempfile.TemporaryDirectory() as td:
        lib = altium_house(Path(td) / "lib")
        ws = Workspace(Path(td) / "ws", libs=[str(lib)])
        assert ws.house_style() == {"exists": False, "file": "house_style.json"}
        r = ws.learn_house_style(direction="IC descriptions are upper case")
        assert r["exists"] and r["pins"]["active_low"] == "overbar" and r["direction"][0]["text"].startswith("IC")
        tools = {t["name"] for t in M.TOOLS}
        assert {"learn_house_style", "house_style", "check_house_style", "apply_house_style"} <= tools
        got = M.call_tool(ws, "house_style", {"direction": "pins as RESET#"})
        assert "pins as RESET#" in json.dumps(got)
        got = M.call_tool(ws, "house_style", {})
        assert "IC descriptions are upper case" in json.dumps(got)


def test_ui_learns_the_style():
    from tests.test_ui import serve, wait_job
    with tempfile.TemporaryDirectory() as td:
        lib = altium_house(Path(td) / "lib")
        ws = Workspace(Path(td) / "ws", libs=[str(lib)])
        srv, http = serve(ws)
        try:
            st, page = http("GET", "/")
            assert st == 200 and b'id="stylego"' in page and b"/api/style/learn" in page
            assert http("GET", "/api/style")[1]["exists"] is False
            st, j = http("POST", "/api/style/learn", {"paths": [], "direction": ["IC descriptions are upper case"]})
            assert st == 200, j
            res = wait_job(http, j["job"])
            assert res["state"] == "done", res
            st, r = http("GET", "/api/style")
            assert r["exists"] and "IC descriptions are upper case" in r["summary"]
            st, r = http("POST", "/api/style/direction", {"clear": True})
            assert st == 200 and r["direction"] == []
            assert http("POST", "/api/style/learn", {"paths": "nope"})[0] == 400
        finally:
            srv.shutdown()


def test_librarian_follows_the_house_style():
    """A plan made with a house_style.json next to the rules comes out in the house style; [style] apply = off: not."""
    from tests.test_cli_smoke import BOM, KICAD_SYM as KSYM
    from ecadlib import librarian as L
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        lib = altium_house(d / "house")
        (d / "kicad").mkdir()
        (d / "kicad" / "demo.kicad_sym").write_text(KSYM)
        (d / "bom.csv").write_text(BOM)
        for apply in ("auto", "off"):
            out = d / f"out_{apply}"
            out.mkdir()
            (out / "ecadlib.toml").write_text(f'[style]\napply = "{apply}"\n')
            prof = H.learn([lib])
            H.save(prof, out / "house_style.json")
            items = L.run(str(d / "bom.csv"), [str(d / "kicad")], str(out), ["standard"], offline=True,
                          cache=str(d / "cache"), log=lambda *a, **k: None)
            styled = [i for i in items if i.get("house_style")]
            if apply == "auto":
                assert styled, [i["description"] for i in items]
            else:
                assert not styled


if __name__ == "__main__":
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
            print("ok", k)
