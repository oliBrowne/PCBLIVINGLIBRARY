"""The library hub's Fusion and EasyEDA columns: readers for Autodesk Fusion Electronics / EAGLE libraries (.lbr) and
EasyEDA Pro libraries (.elibz), built on the read-back code of eagle-export and easyeda-export, so the hub reads
these files the same way those exporters check what they wrote. Registered with hub.register_tool when this ecadlib
has the exporters; ecadlib's own exports (fusion/<bom>/, easyeda/<bom>/ in the workspace) count as ecadlib's copies,
and a part ecadlib built gets a "Make the Fusion version" / "Make the EasyEDA version" button (Workspace.tool_export)."""
import importlib


def _mod(name):
    try:
        return importlib.import_module(f"{__package__}.{name}")
    except ImportError:
        return None


def _field(d, *names):
    low = {str(k).lower(): v for k, v in (d or {}).items()}
    return next((str(low[n]) for n in names if low.get(n)), "")


def read_lbr(path):
    """.lbr -> one part per deviceset: its package's pads (y up) and its pins, numbered by the pads they connect to."""
    from . import dedupe as DD
    EG = _mod("eagle_export")
    lib = EG.read_lbr(path)
    out = []
    for name, ds in lib["devicesets"].items():
        pk = lib["packages"].get(ds["package"]) if ds["package"] else None
        pads = None
        if pk is not None:
            pads = sorted(DD._pad_tuple(p["num"], p["x"], -p["y"], p["w"], p["h"], p.get("rot") or 0.0,
                                        p.get("drill") is not None) for p in pk["pads"])
        pins = [(num, p["name"]) for p in ds["pins"].values() for num in (p["pads"] or [])]
        attrs = {k: v for k, v in (ds["attrs"] or {}).items() if v}
        out.append({"name": name, "mpn": _field(attrs, "mpn", "manufacturer part number", "manufacturer_part_number"),
                    "symbol": name, "footprint": ds["package"] or "", "prefix": ds.get("prefix") or "",
                    "fields": {"Manufacturer": _field(attrs, "manufacturer", "mf"), "Description": _field(attrs, "description"),
                               "Value": _field(attrs, "value")},
                    "pads": pads, "pins": pins if ds["pins"] else None})
    return out


def read_elibz(path):
    """.elibz -> one part per device: its footprint read back as KiCad (dedupe's geometry) and its symbol's pins."""
    from . import dedupe as DD
    EE = _mod("easyeda_pro_export")
    lib = EE.read_elibz(path)
    ids = lib.get("ids") or {}
    out = []
    for title, attrs in lib["devices"].items():
        sym = ids.get(attrs.get("Symbol"), attrs.get("Symbol"))
        fp = ids.get(attrs.get("Footprint"), attrs.get("Footprint"))
        pads = pins = None
        if fp in lib["footprints"]:
            pads = DD.fp_geometry_kicad(EE.footprint_to_kicad(lib["footprints"][fp], fp))["pads"]
        if sym in lib["symbols"]:
            pins = [(num, p["name"][:-1] if p["name"].endswith("#") else p["name"])      # overbar: NAME# -> NAME
                    for num, p in EE.symbol_pins(lib["symbols"][sym]).items() if num]
        out.append({"name": title, "mpn": _field(attrs, "manufacturer part", "mpn", "manufacturer part number"),
                    "symbol": sym or "", "footprint": fp or "", "prefix": str(attrs.get("Designator") or "").rstrip("?"),
                    "fields": {"Manufacturer": _field(attrs, "manufacturer"), "Description": attrs.get("_description", ""),
                               "Value": _field(attrs, "value")},
                    "pads": pads, "pins": pins})
    return out


def _export_fix(tool):
    """Write the library of the part's BOM for this tool (Workspace.tool_export); the hub passes the BOM."""
    def fix(ws, plan_path, item, bom=None):
        return ws.tool_export(bom, tool)
    fix.wants_bom = True
    return fix


def hub_register(register_tool):
    if _mod("eagle_export"):
        register_tool("fusion", "Fusion", (".lbr",), read_lbr, workspace_dirs=("fusion",), fix=_export_fix("eagle"))
    if _mod("easyeda_pro_export"):
        register_tool("easyeda", "EasyEDA", (".elibz",), read_elibz, workspace_dirs=("easyeda",), fix=_export_fix("easyeda"))
