"""Shared actions behind every front end (web UI, MCP server, CLI).

A Workspace is one folder on the user's machine:

    <root>/uploads/                       BOMs dropped into the UI
    <root>/footprints/ecadlib.pretty/     footprints the user approved (indexed like any other library)
    <root>/reports/                       triage .md/.csv
    <root>/altium/                        DbLib export
    <root>/http_cache/                    provider responses

Each action returns plain JSON-able dicts so the UI and the MCP server can share it."""
import json, re
from pathlib import Path

from . import bom_triage as T, footprint_gen as F, providers as P, sexpr
from .httpcache import CachedHTTP
from .kicad_index import KiCadIndex

STATUS_ORDER = ["needs_footprint_generation", "unverified (source unreachable)", "easyeda_footprint_only",
                "available_easyeda", "in_library (generated footprint)", "in_library"]


class Workspace:
    def __init__(self, root="ecadlib_workspace", libs=(), providers=None, offline=False):
        self.root = Path(root).resolve()
        for d in ("uploads", "reports", "altium", "footprints/ecadlib.pretty"):
            (self.root / d).mkdir(parents=True, exist_ok=True)
        self.gen_dir = self.root / "footprints" / "ecadlib.pretty"
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
        return str(Path(p).resolve().relative_to(self.root))

    # ------------------------------------------------------------------ actions
    def info(self):
        http = CachedHTTP(self.root / "http_cache", offline=True)
        index = KiCadIndex(self.libs + [str(self.root / "footprints")])
        return {"workspace": str(self.root), "libs": self.libs, "offline": self.offline,
                "symbols": len(index.symbols), "footprints": len(index.footprints),
                "providers": [{"name": c.name, "auth": c.auth, "enabled": c.name in self.provider_names,
                               "configured": c(http).configured()} for c in P.ALL],
                "boms": sorted(self.rel(p) for p in (self.root / "uploads").iterdir() if p.is_file())}

    def save_upload(self, filename, data: bytes):
        p = self.root / "uploads" / self.safe_name(filename)
        p.write_bytes(data)
        return self.rel(p)

    def triage(self, bom):
        """Triage a BOM (workspace-relative or absolute path). Writes reports/<stem>.{md,csv}."""
        bom_path = self.inside(bom) if not Path(bom).is_absolute() else Path(bom)
        http = CachedHTTP(self.root / "http_cache", min_interval=1.0, offline=self.offline)
        provs = [c(http) for c in P.ALL if c.name in self.provider_names]
        lines, mapping = T.read_bom(bom_path)
        if not lines:
            raise ValueError("no BOM lines found; is there a header row with Designator / MPN columns?")
        index = KiCadIndex(self.libs + [str(self.root / "footprints")])
        rows = T.triage(lines, index, provs, http)
        stem = self.root / "reports" / f"triage_{bom_path.stem}"
        counts = T.write_reports(rows, mapping, provs, http, stem, bom_path.name)
        return {"bom": str(bom), "mapping": mapping, "rows": rows,
                "counts": {k: int(v) for k, v in counts.items()},
                "reports": [self.rel(stem.with_suffix(".md")), self.rel(stem.with_suffix(".csv"))]}

    def preview_footprint(self, spec, density=None):
        """Run the IPC-7351B generator without saving. Returns numbers, pads and an SVG drawing."""
        if isinstance(spec, str):
            spec = json.loads(spec)
        F.validate(spec)
        text, rep = F.build(spec, density)
        sexpr.parse(text)
        return {"name": spec["name"], "report": rep, "kicad_mod": text, "svg": footprint_svg(rep)}

    def approve_footprint(self, spec, density=None):
        """Save the generated footprint into the workspace library, where triage will find it."""
        res = self.preview_footprint(spec, density)
        name = self.safe_name(res["name"])
        p = self.gen_dir / f"{name}.kicad_mod"
        p.write_text(res["kicad_mod"])
        spec_obj = json.loads(spec) if isinstance(spec, str) else spec
        (self.gen_dir / f"{name}.json").write_text(json.dumps(spec_obj, indent=2))  # keep the source dimensions
        return {"name": name, "path": self.rel(p), "report": res["report"]}

    def export_dblib(self, bom):
        """Write the Altium DbLib (SQLite + XLSX + .DbLib) for a BOM, using the latest triage."""
        from . import altium_export
        bom_path = self.inside(bom) if not Path(bom).is_absolute() else Path(bom)
        tri = self.root / "reports" / f"triage_{bom_path.stem}.csv"
        out = self.root / "altium"
        argv = [str(bom_path), "-o", str(out)] + (["--triage", str(tri)] if tri.exists() else [])
        altium_export.main(argv)
        return {"files": [self.rel(p) for p in sorted(out.iterdir()) if p.is_file()]}

    def example_specs(self):
        d = Path(__file__).resolve().parent.parent / "examples" / "packages"
        return {p.stem: json.loads(p.read_text()) for p in sorted(d.glob("*.json"))} if d.exists() else {}


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
