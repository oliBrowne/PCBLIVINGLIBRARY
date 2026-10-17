"""Synthetic libraries in several house styles, written with ecadlib's own Altium/KiCad writers, for the rules
inference tests (tests/test_rules_infer.py). Every name, parameter and layer choice here is made up; each style
returns (paths to scan, the settings a person would expect `ecadlib init` to find)."""
import json
from pathlib import Path

from ecadlib import altium_lib as AL, footprint_gen as F, rules as RU

SPECS = Path(F.__file__).with_name("package_specs")
RES = [("0603", "10K", "1%"), ("0402", "4K7", "5%"), ("0603", "100R", "1%"), ("0805", "1K", "1%"), ("0402", "47K", "1%"),
       ("0603", "2K2", "5%"), ("1206", "0R", "5%"), ("0603", "22R", "1%")]
CAPS = [("0402", "100N", "16V", "X7R"), ("0603", "1U", "25V", "X5R"), ("0805", "10U", "10V", "X5R"), ("0402", "10P", "50V", "C0G"),
        ("0603", "4U7", "16V", "X7R"), ("0402", "1N", "50V", "X7R")]
ICS = [("TPS62133RGTR", "Texas Instruments", "QFN-16-1EP_3x3mm_P0.5mm_EP1.75x1.75mm"), ("CH340C", "WCH", "SOIC-16_3.9x9.9mm_P1.27mm"),
       ("AMS1117-3.3", "AMS", "SOT-223-3_TabPin2"), ("LM358DR", "Texas Instruments", "SOIC-8_3.9x4.9mm_P1.27mm"),
       ("STM32F103C8T6", "ST", "LQFP-48_7x7mm_P0.5mm")]
MPN_R = {"10K": "RC0603FR-0710KL", "4K7": "RC0402JR-074K7L", "100R": "RC0603FR-07100RL", "1K": "RC0805FR-071KL",
         "47K": "RC0402FR-0747KL", "2K2": "RC0603JR-072K2L", "0R": "RC1206JR-070RL", "22R": "RC0603FR-0722RL"}
MPN_C = {"100N": "CL05B104KO5NNNC", "1U": "CL10A105KA8NNNC", "10U": "CL21A106KPFNNNE", "10P": "CL05C100JB5NNNC",
         "4U7": "CL10A475KO8NNNC", "1N": "CL05B102KB5NNNC"}


def sym(name, ref, params, fp, pins=2):
    if ref in ("R", "C"):
        s = (AL.resistor_symbol if ref == "R" else AL.capacitor_symbol)(name, footprint=fp)
    else:
        s = AL.ic_symbol(name, [{"num": str(i), "name": f"P{i}", "side": "left" if i % 2 else "right"} for i in range(1, pins + 1)],
                         designator=f"{ref}?", footprint=fp)
        s["designator"] = f"{ref}?"
    s["params"] = list(params.items())
    return s


def footprint(spec_name, name, layers=None, density="B", body=True):
    spec = json.loads((SPECS / f"{spec_name}.json").read_text())
    fp, _ = AL.footprint_from_spec(spec, density)
    fp = dict(fp, name=name)
    if body:
        xs = [p["x"] for p in fp["pads"]]
        ys = [p["y"] for p in fp["pads"]]
        x0, x1, y0, y1 = min(xs) * 0.6, max(xs) * 0.6, min(ys) * 0.6 - 0.3, max(ys) * 0.6 + 0.3
        fp["bodies"] = [{"pts": [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], "height": 0.5}]
    return fp, layers


def write_pcb(d, fps):
    (d / "PcbLib").mkdir(parents=True, exist_ok=True)
    for fp, layers in fps:
        AL.write_pcblib(d / "PcbLib" / f"{AL.file_stem(fp['name'])}.PcbLib", [fp], layers)


def chip_spec(kind, size):
    met = {"0402": "1005", "0603": "1608", "0805": "2012", "1206": "3216"}[size]
    for k in (kind, "R"):
        if (SPECS / f"{k}_{size}_{met}Metric.json").exists():
            return f"{k}_{size}_{met}Metric"


def style_altium_classic(d):
    """RES_0603_10K_1% / CAP_0402_100N_16V_X7R, ICs by MPN, house params MPN / Mfr, IPC footprint names (density N),
    courtyard on Mechanical 1, assembly on Mechanical 2, 3D bodies on Mechanical 3."""
    d = Path(d)
    lay = {"courtyard": RU.layer_id("Mechanical1"), "assembly": RU.layer_id("Mechanical2"), "body3d": RU.layer_id("Mechanical3")}
    syms, fps = [], []
    for size, val, tol in RES:
        ipc = RU.ipc7351_name(f"R_{size}_{ {'0402': '1005', '0603': '1608', '0805': '2012', '1206': '3216'}[size]}Metric")
        syms.append(sym(f"RES_{size}_{val}_{tol}", "R", {"MPN": MPN_R[val], "Mfr": "Yageo", "Datasheet": "https://x/y.pdf",
                                                        "RoHS": "Yes", "Tolerance": tol}, ipc))
        fps.append(footprint(chip_spec("R", size), ipc, lay))
    for size, val, v, diel in CAPS:
        ipc = RU.ipc7351_name(f"C_{size}_{ {'0402': '1005', '0603': '1608', '0805': '2012'}[size]}Metric")
        syms.append(sym(f"CAP_{size}_{val}_{v}_{diel}", "C", {"MPN": MPN_C[val], "Mfr": "Samsung", "Datasheet": "https://x/c.pdf",
                                                                  "RoHS": "Yes", "Voltage": v}, ipc))
        fps.append(footprint(chip_spec("C", size), ipc, lay))
    for mpn, mfr, kfp in ICS:
        ipc = RU.ipc7351_name(kfp) or kfp
        syms.append(sym(mpn, "U", {"MPN": mpn, "Mfr": mfr, "Datasheet": "https://x/ic.pdf", "RoHS": "Yes"}, ipc, 8))
        if (SPECS / f"{kfp}.json").exists():
            fps.append(footprint(kfp, ipc, lay))
    (d / "SchLib").mkdir(parents=True, exist_ok=True)
    AL.write_schlib(d / "SchLib" / "Passives.SchLib", syms[:len(RES) + len(CAPS)])
    AL.write_schlib(d / "SchLib" / "ICs.SchLib", syms[len(RES) + len(CAPS):])
    write_pcb(d, fps)
    expect = {"naming.by_category.Resistors": "{cat3}_{package}_{value}_{tolerance}",
              "naming.by_category.Capacitors": "{cat3}_{package}_{value}_{voltage}_{dielectric}",
              "naming.component": "{mpn}", "naming.value_style": "rkm", "naming.footprint": "ipc7351",
              "parameters.names": {"Manufacturer Part Number": "MPN", "Manufacturer": "Mfr", "ComponentLink2URL": "Datasheet"},
              "parameters.defaults": {"RoHS": "Yes"}, "layers.courtyard": "Mechanical1", "layers.assembly": "Mechanical2",
              "layers.body3d": "Mechanical3", "footprint.density": "N"}
    return [d], expect


def style_glued_dblib(d):
    """C0402_100N_16V_X7R / R0603_10K_1%, a DbLib over an .xlsx parts table (sheets = categories), house params
    'Mfr Part Number' / 'Manufacturer' / 'Supplier' / 'Supplier PN', footprint names R0603 / C0402 (density M pads)."""
    import pandas as pd
    d = Path(d)
    d.mkdir(parents=True, exist_ok=True)
    rows = {"Res": [], "Caps": [], "Semis": []}
    fps = []
    for size, val, tol in RES:
        rows["Res"].append({"Part Number": f"R{size}_{val}_{tol}", "Library Ref": "Resistor", "Footprint Ref": f"R{size}",
                            "Description": f"Resistor {val} {tol} {size}", "Manufacturer": "Yageo", "Mfr Part Number": MPN_R[val],
                            "Supplier": "Digi-Key", "Supplier PN": f"311-{val}-ND", "Value": val})
    for size, val, v, diel in CAPS:
        rows["Caps"].append({"Part Number": f"C{size}_{val}_{v}_{diel}", "Library Ref": "Capacitor", "Footprint Ref": f"C{size}",
                             "Description": f"Capacitor {val} {v} {diel}", "Manufacturer": "Samsung", "Mfr Part Number": MPN_C[val],
                             "Supplier": "Digi-Key", "Supplier PN": f"1276-{val}-ND", "Value": val})
    for mpn, mfr, kfp in ICS:
        rows["Semis"].append({"Part Number": mpn, "Library Ref": mpn, "Footprint Ref": kfp, "Description": f"{mpn} IC",
                              "Manufacturer": mfr, "Mfr Part Number": mpn, "Supplier": "Digi-Key", "Supplier PN": f"{mpn}-ND", "Value": ""})
    with pd.ExcelWriter(d / "parts.xlsx") as w:
        for k, v in rows.items():
            pd.DataFrame(v).to_excel(w, sheet_name=k, index=False)
    cols = list(rows["Res"][0])
    from ecadlib.altium_export import dblib_text, excel_connection
    text = dblib_text(excel_connection(r"C:\lib\parts.xlsx"), {k: cols for k in rows}, excel=True,
                      db_type="Microsoft Excel", db_path="parts.xlsx", relative=True)
    (d / "house.DbLib").write_text(text, newline="")
    for size in ("0603", "0805", "1206"):
        fps.append(footprint(chip_spec("R", size), f"R{size}", None, "A"))
    for size in ("0603", "0805"):
        fps.append(footprint(chip_spec("C", size), f"C{size}", None, "A"))
    write_pcb(d, fps)
    expect = {"naming.by_category.Resistors": "{ref}{package}_{value}_{tolerance}",
              "naming.by_category.Capacitors": "{ref}{package}_{value}_{voltage}_{dielectric}",
              "naming.component": "{mpn}", "naming.value_style": "rkm", "naming.footprint": "{ref}{package}",
              "parameters.names": {"Manufacturer Part Number": "Mfr Part Number", "Supplier 1": "Supplier",
                                   "Supplier Part Number 1": "Supplier PN"},
              "folder:Resistors": "Components/Res", "folder:Capacitors": "Components/Caps", "folder:ICs": "Components/Semis",
              "footprint.density": "M"}
    return [d], expect


def style_mpn_a365(d):
    """Every part named by its MPN (passives too), an Altium 365 component list with folders under
    'Library/Passives/...' and 'Library/ICs', default ecadlib parameter names, KiCad footprint names."""
    d = Path(d)
    d.mkdir(parents=True, exist_ok=True)
    comps = []
    for size, val, tol in RES:
        comps.append({"name": MPN_R[val], "folder": "Library/Passives/Resistors", "symbol": "Resistor",
                      "footprint": f"R_{size}_{ {'0402': '1005', '0603': '1608', '0805': '2012', '1206': '3216'}[size]}Metric",
                      "parameters": {"Manufacturer": "Yageo", "Manufacturer Part Number": MPN_R[val], "Value": val,
                                     "ComponentLink2URL": "https://x/r.pdf", "Lifecycle": "Active"}})
    for size, val, v, diel in CAPS:
        comps.append({"name": MPN_C[val], "folder": "Library/Passives/Capacitors", "symbol": "Capacitor",
                      "footprint": f"C_{size}_{ {'0402': '1005', '0603': '1608', '0805': '2012'}[size]}Metric",
                      "parameters": {"Manufacturer": "Samsung", "Manufacturer Part Number": MPN_C[val], "Value": val,
                                     "ComponentLink2URL": "https://x/c.pdf", "Lifecycle": "Active"}})
    for mpn, mfr, kfp in ICS:
        comps.append({"name": mpn, "folder": "Library/ICs", "symbol": mpn, "footprint": kfp,
                      "parameters": {"Manufacturer": mfr, "Manufacturer Part Number": mpn, "ComponentLink2URL": "https://x/i.pdf",
                                     "Lifecycle": "Active"}})
    f = d / "a365_components.json"
    f.write_text(json.dumps(comps, indent=1))
    expect = {"naming.component": "{mpn}", "naming.passive": "{mpn}", "naming.footprint": "kicad",
              "folders.root": "Library", "folder:Resistors": "Library/Passives/Resistors",
              "folder:Capacitors": "Library/Passives/Capacitors", "folder:ICs": "Library/ICs",
              "parameters.defaults": {"Lifecycle": "Active"}}
    return [f], expect


def style_kicad(d):
    """A KiCad library: symbols named by MPN with MPN / Manufacturer / LCSC properties, footprints in a .pretty
    built at density M (IPC 'most'), plus a BOM whose headers ecadlib has to guess."""
    d = Path(d)
    (d / "house.pretty").mkdir(parents=True, exist_ok=True)
    out = ['(kicad_symbol_lib (version 20231120) (generator "test")']
    for mpn, mfr, kfp in ICS:
        out.append(f'  (symbol "{mpn}" (in_bom yes) (on_board yes)\n'
                   f'    (property "Reference" "U" (at 0 0 0)) (property "Value" "{mpn}" (at 0 0 0))\n'
                   f'    (property "Footprint" "house:{kfp}" (at 0 0 0)) (property "Datasheet" "https://x/{mpn}.pdf" (at 0 0 0))\n'
                   f'    (property "MPN" "{mpn}" (at 0 0 0)) (property "Manufacturer" "{mfr}" (at 0 0 0)) (property "LCSC" "C{len(out)}" (at 0 0 0)))')
    out.append(")")
    (d / "house.kicad_sym").write_text("\n".join(out))
    for spec in ("R_0603_1608Metric", "R_0805_2012Metric", "R_1206_3216Metric", "C_0805_2012Metric", "SOIC-8_3.9x4.9mm_P1.27mm"):
        sp = json.loads((SPECS / f"{spec}.json").read_text())
        text, _ = F.build(sp, "A")
        (d / "house.pretty" / f"{spec}.kicad_mod").write_text(text)
    (d / "bom.csv").write_text("Ref Des,Qty,Comment,PCB Footprint,Maker,Manufacturer P/N (primary),Order code\n"
                               "R1,1,10k,R_0603_1608Metric,Yageo,RC0603FR-0710KL,C25804\n"
                               "U1,1,LM358DR,SOIC-8_3.9x4.9mm_P1.27mm,TI,LM358DR,C7950\n")
    expect = {"naming.component": "{mpn}", "naming.footprint": "kicad", "footprint.density": "M",
              "parameters.names": {"Manufacturer Part Number": "MPN"}, "bom.columns": {"mpn": "Manufacturer P/N (primary)"}}
    return [d], expect


def sparse_designator_library(d, n=48, designator_on_all=False):
    """Like Altium's own example libraries (found in AD26): n footprints, the courtyard outline and the 3D bodies on the
    same Mechanical 5, and only ONE footprint (or every one, designator_on_all) with a .Designator (on Mechanical 5)
    and a .Comment (on Mechanical 2); the rest carry no strings. Silkscreen lines are on the Top Overlay."""
    d = Path(d)
    lay = {"courtyard": RU.layer_id("Mechanical5"), "body3d": RU.layer_id("Mechanical5"),
           "assembly": RU.layer_id("Mechanical2"), "designator": RU.layer_id("Mechanical5")}
    sizes = [chip_spec(k, s) for k in ("R", "C") for s in ("0402", "0603", "0805", "1206")]
    fps = []
    for i in range(n):
        fp, _ = footprint(sizes[i % len(sizes)], f"EXAMPLE_{i:02d}")
        if i and not designator_on_all:
            fp["texts"] = []
        fps.append(fp)
    assert any(t[0] == ".Designator" for t in fps[0].get("texts", [])), "the writer's footprints carry a .Designator"
    (d / "PcbLib").mkdir(parents=True, exist_ok=True)
    AL.write_pcblib(d / "PcbLib" / "Examples.PcbLib", fps, lay)
    return d


STYLES = {"altium_classic": style_altium_classic, "glued_dblib": style_glued_dblib, "mpn_a365": style_mpn_a365, "kicad": style_kicad}


def score(prop, expect):
    """(hits, total, misses) of a proposal against a style's expectations; 'folder:<Category>' checks the folder the
    inferred rules give that category (a map entry or <root>/<Category>)."""
    got = {s["key"]: s["value"] for s in prop["settings"]}
    rules = RU.load(prop["rules"])
    hits, misses = 0, []
    for k, v in expect.items():
        have = RU.folder_for(k.split(":", 1)[1], rules) if k.startswith("folder:") else got.get(k)
        if have == v:
            hits += 1
        else:
            misses.append(f"{k}: expected {v!r}, got {have!r}")
    return hits, len(expect), misses
