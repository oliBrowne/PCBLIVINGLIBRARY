"""Shared actions behind every front end (web UI, MCP server, `ecadlib` CLI).

A Workspace is one folder on the user's machine:

    <root>/uploads/                       BOMs dropped into the UI
    <root>/footprints/ecadlib.pretty/     footprints the user approved (indexed like any other library)
    <root>/altium_models/PcbLib|SchLib/   Altium binaries: approved footprints, converted library footprints,
                                          generic R/C symbols (what an Altium 365 release uploads)
    <root>/reports/                       triage .md/.csv
    <root>/altium/                        DbLib export
    <root>/altium365/                     release plan + push reports
    <root>/http_cache/                    provider responses

Each action returns plain JSON-able dicts so the UI and the MCP server can share it."""
import json, os, re
from pathlib import Path

import pandas as pd

from . import bom_triage as T, footprint_gen as F, providers as P, sexpr
from .httpcache import CachedHTTP
from .kicad_index import KiCadIndex

STATUS_ORDER = ["needs_footprint_generation", "unverified (source unreachable)", "easyeda_footprint_only",
                "available_easyeda", "in_library (generated footprint)", "in_library"]


class Workspace:
    def __init__(self, root="ecadlib_workspace", libs=(), providers=None, offline=False):
        self.root = Path(root).resolve()
        for d in ("uploads", "reports", "altium", "altium365", "footprints/ecadlib.pretty",
                  "altium_models/PcbLib", "altium_models/SchLib"):
            (self.root / d).mkdir(parents=True, exist_ok=True)
        self.gen_dir = self.root / "footprints" / "ecadlib.pretty"
        self.models = self.root / "altium_models"
        self.libs = [str(Path(l).resolve()) for l in libs]
        self.provider_names = providers or [c.name for c in P.ALL]
        self.offline = offline

    # ------------------------------------------------------------------ paths
    def safe_name(self, name):
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(str(name)).name).strip("._") or "bom.csv"
        return name[:120]

    def inside(self, path):
        """Resolve a workspace-relative path, refusing anything outside the workspace."""
        p = (self.root / path).resolve()
        if p != self.root and self.root not in p.parents:
            raise ValueError("path outside workspace")
        return p

    def rel(self, p):
        return Path(p).resolve().relative_to(self.root).as_posix()

    def _bom_path(self, bom):
        return Path(bom) if Path(bom).is_absolute() else self.inside(bom)

    def _index(self):
        return KiCadIndex(self.libs + [str(self.root / "footprints")])

    def _read_bom(self, bom_path):
        if bom_path.suffix.lower() not in (".xlsx", ".xlsm", ".xls") and b"\0" in bom_path.read_bytes()[:4096]:
            raise ValueError(f"{bom_path.name} looks like a binary file, not a CSV; export the BOM as CSV or XLSX")
        try:
            lines, mapping = T.read_bom(bom_path)
        except Exception as e:                        # pandas/csv/zip errors -> one message a person can act on
            raise ValueError(f"couldn't read {bom_path.name} as a BOM table ({type(e).__name__}: {e}). "
                             "Save it as CSV or XLSX with a header row.") from e
        if not lines or not {"designator", "mpn", "value"} & set(mapping):
            raise ValueError(f"no BOM lines found in {bom_path.name}: it needs a header row with columns such as "
                             "Designator, Quantity and Manufacturer Part Number, followed by at least one part")
        return lines, mapping

    def _triage_frame(self, bom_path):
        tri = self.root / "reports" / f"triage_{bom_path.stem}.csv"
        return pd.read_csv(tri, dtype=str).fillna("") if tri.exists() else None

    # ------------------------------------------------------------------ actions
    def info(self):
        http = CachedHTTP(self.root / "http_cache", offline=True)
        index = self._index()
        return {"workspace": str(self.root), "libs": self.libs, "offline": self.offline,
                "symbols": len(index.symbols), "footprints": len(index.footprints),
                "providers": [{"name": c.name, "auth": c.auth, "enabled": c.name in self.provider_names,
                               "configured": c(http).configured()} for c in P.ALL],
                "altium365": {"token": bool(os.environ.get("NEXAR_TOKEN"))},
                "altium_models": {k: sorted(p.stem for p in (self.models / k).glob(f"*.{k}")) for k in ("PcbLib", "SchLib")},
                "boms": sorted(self.rel(p) for p in (self.root / "uploads").iterdir() if p.is_file())}

    def save_upload(self, filename, data: bytes):
        if not data:
            raise ValueError(f"{filename} is empty")
        p = self.root / "uploads" / self.safe_name(filename)
        p.write_bytes(data)
        return self.rel(p)

    def triage(self, bom, progress=None):
        """Triage a BOM (workspace-relative or absolute path). Writes reports/triage_<stem>.{md,csv}.
        progress(done, total, label) is called before each line, for UIs that show a progress bar."""
        bom_path = self._bom_path(bom)
        http = CachedHTTP(self.root / "http_cache", min_interval=1.0, offline=self.offline)
        provs = [c(http) for c in P.ALL if c.name in self.provider_names]
        lines, mapping = self._read_bom(bom_path)
        index = self._index()
        rows = []
        for i, ln in enumerate(lines, 1):            # one line at a time so long lookups can report progress
            if progress:
                progress(i - 1, len(lines), ln.get("mpn") or ln.get("value") or ln.get("designator", ""))
            r = T.triage([ln], index, provs, http)[0]
            r["line"] = i
            rows.append(r)
        if progress:
            progress(len(lines), len(lines), "writing report")
        stem = self.root / "reports" / f"triage_{bom_path.stem}"
        counts = T.write_reports(rows, mapping, provs, http, stem, bom_path.name)
        return {"bom": self.rel(bom_path) if self.root in bom_path.resolve().parents else str(bom_path),
                "mapping": mapping, "rows": rows,
                "counts": {k: int(v) for k, v in counts.items()},
                "reports": [self.rel(stem.with_suffix(".md")), self.rel(stem.with_suffix(".csv"))]}

    def preview_footprint(self, spec, density=None):
        """Run the IPC-7351B generator without saving. Returns numbers, pads and an SVG drawing."""
        if isinstance(spec, str):
            spec = json.loads(spec)
        if not isinstance(spec, dict) or not spec.get("name"):
            raise ValueError("the package spec needs at least a \"name\"")
        try:
            F.validate(spec)
            text, rep = F.build(spec, density)
        except KeyError as e:                         # no jsonschema installed: the generator finds the gap itself
            raise ValueError(f"the package spec is missing {e.args[0]!r}") from e
        except Exception as e:
            if type(e).__name__ != "ValidationError":
                raise
            where = "/".join(map(str, e.absolute_path))
            raise ValueError(f"the package spec isn't valid{' at ' + where if where else ''}: {e.message}") from e
        sexpr.parse(text)
        name = self.safe_name(spec["name"])
        return {"name": spec["name"], "report": rep, "kicad_mod": text, "svg": footprint_svg(rep),
                "exists": (self.gen_dir / f"{name}.kicad_mod").exists()}

    def approve_footprint(self, spec, density=None, overwrite=False):
        """Save the generated footprint into the workspace library (KiCad .kicad_mod + Altium .PcbLib),
        where triage and the Altium 365 plan will find it. Refuses to replace an existing one unless overwrite."""
        from . import altium_lib
        spec_obj = json.loads(spec) if isinstance(spec, str) else spec
        res = self.preview_footprint(spec_obj, density)
        name = self.safe_name(res["name"])
        p = self.gen_dir / f"{name}.kicad_mod"
        if p.exists() and not overwrite:
            raise FileExistsError(f"{name} is already in the workspace library; approve again with overwrite to replace it")
        p.write_text(res["kicad_mod"])
        (self.gen_dir / f"{name}.json").write_text(json.dumps(spec_obj, indent=2))  # keep the source dimensions
        files = [self.rel(p)]
        warnings = [f"{c['rule']}: {c['message']}" for c in res["report"].get("checks", []) if c["level"] == "error"]
        try:
            fp, _ = altium_lib.footprint_from_spec(spec_obj, density)
            files.append(self.rel(altium_lib.write_pcblib(self.models / "PcbLib" / f"{name}.PcbLib", [fp])))
        except Exception as e:
            warnings.append(f"Altium .PcbLib not written: {type(e).__name__}: {e}")
        return {"name": name, "path": files[0], "files": files, "warnings": warnings, "report": res["report"]}

    def build_altium_models(self, bom):
        """Write the Altium files an Altium 365 release needs, for every BOM line that has a footprint:
        footprints already in the KiCad libraries are converted to .PcbLib, and generic Resistor/Capacitor
        symbols are written as .SchLib. IC symbols are not generated here."""
        from . import altium_lib
        bom_path = self._bom_path(bom)
        tri = self._triage_frame(bom_path)
        if tri is None:
            self.triage(bom)
            tri = self._triage_frame(bom_path)
        index, done, skipped = self._index(), [], []
        for key in dict.fromkeys(k.split(" ")[0] for k in tri["local_footprint"] if k):
            ref = key.split(":")[-1]
            out = self.models / "PcbLib" / f"{ref}.PcbLib"
            if out.exists():
                done.append(ref)
                continue
            hit = index.footprints.get(key) or index.footprint(key)[1]
            if not hit:
                skipped.append(f"{ref}: not found in indexed libraries")
                continue
            try:
                altium_lib.write_pcblib(out, [altium_lib.footprint_from_kicad(Path(hit["path"]).read_text())])
                done.append(ref)
            except Exception as e:
                skipped.append(f"{ref}: {e}")
        for name, make in (("Resistor", altium_lib.resistor_symbol), ("Capacitor", altium_lib.capacitor_symbol)):
            out = self.models / "SchLib" / f"{name}.SchLib"
            if not out.exists():
                altium_lib.write_schlib(out, [make()])
        return {"footprints": done, "skipped": skipped,
                "symbols": sorted(p.stem for p in (self.models / "SchLib").glob("*.SchLib"))}

    def export_dblib(self, bom):
        """Write the Altium DbLib (SQLite + XLSX + .DbLib) for a BOM, using the latest triage."""
        from . import altium_export
        bom_path = self._bom_path(bom)
        self._read_bom(bom_path)                      # same friendly error as triage for unreadable files
        tri = self.root / "reports" / f"triage_{bom_path.stem}.csv"
        out = self.root / "altium"
        argv = [str(bom_path), "-o", str(out)] + (["--triage", str(tri)] if tri.exists() else [])
        altium_export.main(argv)
        return {"files": [self.rel(p) for p in sorted(out.iterdir()) if p.is_file()]}

    def altium365_plan(self, bom):
        """One workspace component per part, marked ready when its .SchLib and .PcbLib are in altium_models/."""
        from . import altium365 as A
        bom_path = self._bom_path(bom)
        lines, _ = self._read_bom(bom_path)
        items = A.plan(lines, self._triage_frame(bom_path), self.models)
        out = self.root / "altium365" / f"plan_{bom_path.stem}.json"
        out.write_text(json.dumps(items, indent=2))
        ready = sum(i["status"] == "ready" for i in items)
        return {"items": items, "ready": ready, "total": len(items), "plan": self.rel(out)}

    def altium365_workspaces(self):
        from . import altium365 as A
        return {"workspaces": A.Nexar(_nexar_token()).workspaces()}

    def altium365_push(self, bom, workspace_url, dry_run=True):
        """Release ready parts and update parameters of existing ones. dry_run (the default) sends nothing
        and returns the requests that would be made."""
        from . import altium365 as A
        if not workspace_url and not dry_run:
            raise ValueError("choose an Altium 365 workspace first")
        plan = self.altium365_plan(bom)
        client = None if dry_run else A.Nexar(_nexar_token())
        results = A.push(plan["items"], client, workspace_url or "https://<workspace>", dry_run=dry_run)
        out = self.root / "altium365" / f"push_{'dry_run_' if dry_run else ''}{Path(plan['plan']).stem[5:]}.json"
        out.write_text(json.dumps(results, indent=2))
        return {"dry_run": dry_run, "results": results, "report": self.rel(out)}

    def example_specs(self):
        d = Path(__file__).resolve().parent.parent / "examples" / "packages"
        return {p.stem: json.loads(p.read_text()) for p in sorted(d.glob("*.json"))} if d.exists() else {}


def _nexar_token():
    tok = os.environ.get("NEXAR_TOKEN")
    if not tok:
        raise ValueError("set NEXAR_TOKEN before starting ecadlib (a Nexar portal token with the "
                         "design.domain, user.access and openid scopes)")
    return tok


def footprint_svg(rep, scale=40):
    """Top view: copper pads (pin 1 highlighted), exposed pad and courtyard. KiCad Y points down, as SVG's does."""
    cx, cy = rep["courtyard"][0] / 2, rep["courtyard"][1] / 2
    m = 0.6
    vb = f"{-cx - m:.3f} {-cy - m:.3f} {2 * (cx + m):.3f} {2 * (cy + m):.3f}"
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb}" width="{2 * (cx + m) * scale:.0f}" '
           f'height="{2 * (cy + m) * scale:.0f}" class="fp">',
           f'<rect class="crtyd" x="{-cx}" y="{-cy}" width="{2 * cx}" height="{2 * cy}"/>']
    if "EP" in rep:
        ew, eh = rep["EP"][0], rep["EP"][1]
        out.append(f'<rect class="pad ep" x="{-ew / 2}" y="{-eh / 2}" width="{ew}" height="{eh}"/>')
    for p in rep["pads"]:
        r = min(0.25, 0.25 * min(p["w"], p["h"]))
        cls = "pad p1" if p["num"] == "1" else "pad"
        out.append(f'<rect class="{cls}" x="{p["x"] - p["w"] / 2:.4f}" y="{p["y"] - p["h"] / 2:.4f}" '
                   f'width="{p["w"]}" height="{p["h"]}" rx="{r:.3f}"><title>pad {p["num"]}: '
                   f'{p["w"]:.3f} x {p["h"]:.3f} mm @ ({p["x"]:.3f}, {p["y"]:.3f})</title></rect>')
    out.append("</svg>")
    return "".join(out)
