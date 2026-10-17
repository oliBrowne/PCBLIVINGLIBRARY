"""Shared actions behind every front end (web UI, MCP server, `ecadlib` CLI).

A Workspace is one folder on the user's machine:

    <root>/uploads/<stamp>-<rand>/<name>  BOMs dropped into the UI, one folder per upload (never overwritten)
    <root>/footprints/ecadlib.pretty/     footprints the user approved (indexed like any other library)
    <root>/altium_models/PcbLib|SchLib/   Altium binaries: approved footprints, converted library footprints,
                                          generic R/C symbols (what an Altium 365 release uploads)
    <root>/reports/                       triage .md/.csv, plus .json (the full result, for triage_history)
    <root>/altium/                        DbLib export
    <root>/altium365/                     release plan + push reports
    <root>/librarian/<bom>/               librarian output: models, plan.json, review and release-check reports
    <root>/http_cache/                    provider responses

Each action returns plain JSON-able dicts so the UI and the MCP server can share it."""
import contextlib, datetime, hashlib, html, json, math, os, re, secrets
from pathlib import Path

import pandas as pd

from . import bom_triage as T, footprint_gen as F, providers as P, rules as RU, sexpr
from .fsutil import write_text_atomic
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
        from . import migrate as MG                      # files from a newer ecadlib: one clear error, nothing misread
        MG.guard_tree(self.root)
        self.gen_dir = self.root / "footprints" / "ecadlib.pretty"
        self.models = self.root / "altium_models"
        self.libs = [str(Path(l).resolve()) for l in libs]
        self.provider_names = providers or [c.name for c in P.ALL]
        self._providers_given = bool(providers)          # else [providers] order in the rules decides (ecadlib.plugins)
        self.offline = offline

    # ------------------------------------------------------------------ paths
    def safe_name(self, name, strict=False):
        """A plain file name. Uploads keep only their base name; footprint names (strict) with a path
        separator are refused, so "SOIC-8/TI_D" can't quietly become "TI_D" and collide with another part."""
        name = str(name)
        if strict and ("/" in name or "\\" in name):
            plain = re.sub(r"[/\\]+", "_", name)
            raise ValueError(f"footprint name {name!r} contains a path separator; use a plain name such as {plain!r}")
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", re.split(r"[/\\]", name)[-1]).strip("._") or "bom.csv"
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
        try:
            found = bom_path.is_file()
        except OSError:                                    # name too long, ...
            found = False
        if not found:
            raise ValueError(f"BOM not found: {bom_path.name[:80]} is not a file in the workspace")
        head = bom_path.read_bytes()[:4096]                # UTF-16 with a byte-order mark: Excel "Unicode Text", fine
        if (bom_path.suffix.lower() not in (".xlsx", ".xlsm", ".xls", ".schdoc", ".prjpcb", ".bomdoc")
                and b"\0" in head and head[:2] not in (b"\xff\xfe", b"\xfe\xff") and not T.utf16_without_bom(head)):
            raise ValueError(f"{bom_path.name} looks like a binary file, not a CSV; export the BOM as CSV or XLSX")
        try:
            lines, mapping = T.read_bom(bom_path, RU.bom_columns(self.rules()))
        except Exception as e:                        # pandas/csv/zip errors -> one message a person can act on
            if type(e) is ValueError:                 # the reader's own words: no exception name, no server path
                raise ValueError(f"couldn't read {bom_path.name} as a BOM: "
                                 + str(e).replace(str(bom_path), bom_path.name).replace(f"{bom_path.name}: ", "", 1)) from e
            raise ValueError(f"couldn't read {bom_path.name} as a BOM table ({type(e).__name__}: {e}). "
                             "Save it as CSV or XLSX with a header row.") from e
        if not lines or not {"designator", "mpn", "value"} & set(mapping):
            raise ValueError(f"no BOM lines found in {bom_path.name}: it needs a header row with columns such as "
                             "Designator, Quantity and Manufacturer Part Number, followed by at least one part")
        return lines, mapping

    def _key(self, bom_path):
        """Report name for a BOM: its stem for BOMs in uploads/ (plus the upload's folder name for
        uploads/<stamp>-<rand>/<name>), plus a short hash of the full path for any other location, so
        /projA/BOM.csv and /projB/BOM.csv (or two uploads called BOM.csv) don't share (and overwrite) one triage."""
        bom_path = Path(bom_path).resolve()
        if bom_path.parent == self.root / "uploads":
            return bom_path.stem
        if bom_path.parent.parent == self.root / "uploads":
            return f"{bom_path.stem}_{bom_path.parent.name}"
        return f"{bom_path.stem}_{hashlib.sha1(str(bom_path).encode()).hexdigest()[:8]}"

    def _triage_csv(self, bom_path):
        return self.root / "reports" / f"triage_{self._key(bom_path)}.csv"

    def _triage_frame(self, bom_path):
        tri = self._triage_csv(bom_path)
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
                "rules": (lambda f: self._label(f) if f else None)(self.rules_in_use()),
                "altium_models": {k: sorted(p.stem for p in (self.models / k).glob(f"*.{k}")) for k in ("PcbLib", "SchLib")},
                "boms": self._uploaded_boms()}

    def _uploaded_boms(self):
        """uploads/<name> (older workspaces) and uploads/<stamp>-<rand>/<name>, newest upload folder first.
        Links are skipped, so nothing outside the workspace is listed."""
        up = self.root / "uploads"
        flat = sorted(p for p in up.iterdir() if p.is_file() and not p.is_symlink())
        nested = [f for d in sorted((d for d in up.iterdir() if d.is_dir() and not d.is_symlink()), reverse=True)
                  for f in sorted(d.iterdir()) if f.is_file() and not f.is_symlink()]
        return [self.rel(p) for p in nested + flat if self._is_inside(p)]

    def _is_inside(self, p):
        try:
            self.inside(p)
            return True
        except ValueError:
            return False

    def save_upload(self, filename, data: bytes):
        """Store an uploaded BOM as uploads/<UTC stamp>-<random>/<plain file name> and return that path. Every
        upload gets its own new folder, so two people uploading BOM.csv never overwrite each other (nor each
        other's triage reports, which are keyed by the folder too). The file keeps its (sanitised) name."""
        if not data:
            raise ValueError(f"{filename} is empty")
        name = self.safe_name(filename)
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
        for _ in range(20):
            d = self.root / "uploads" / f"{stamp}-{secrets.token_hex(3)}"
            try:
                d.mkdir()                             # atomic: a folder another upload made is never reused
                break
            except FileExistsError:
                continue
        else:
            raise FileExistsError("couldn't make a new upload folder; try again")
        p = d / name
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        return self.rel(p)

    def triage(self, bom, progress=None, lock=None, user=None):
        """Triage a BOM (workspace-relative or absolute path). Writes reports/triage_<stem>.{md,csv,json}; the
        .json is the whole result plus when and by whom (user), which triage_history lists and opens again.
        progress(done, total, label) is called before each line, for UIs that show a progress bar.
        lock, if given, is held only while the reports are written: the lookups are network-bound and
        must not block other workspace actions."""
        bom_path = self._bom_path(bom)
        http = CachedHTTP(self.root / "http_cache", min_interval=1.0, offline=self.offline)
        provs = [c(http) for c in self._provider_classes()]
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
        stem = self.root / "reports" / f"triage_{self._key(bom_path)}"
        with lock or contextlib.nullcontext():
            counts = T.write_reports(rows, mapping, provs, http, stem, bom_path.name)
            out = {"bom": self.rel(bom_path) if self.root in bom_path.resolve().parents else str(bom_path),
                   "mapping": mapping, "rows": rows,
                   "counts": {k: int(v) for k, v in counts.items()},
                   "reports": [self.rel(stem.with_suffix(".md")), self.rel(stem.with_suffix(".csv"))]}
            meta = {"id": self._key(bom_path), "name": bom_path.name, "finished": _now(), "user": user or None}
            tmp = stem.with_name(stem.name + f".{os.getpid()}.{secrets.token_hex(3)}.tmp")
            tmp.write_text(json.dumps({**meta, **out}, default=str), encoding="utf-8")
            os.replace(tmp, stem.with_suffix(".json"))
        return {**out, **meta}

    # ------------------------------------------------------------------ past triages (read-only)
    def _triage_files(self):
        """{id: (json or None, csv or None)} of every triage report in reports/ (id = the BOM's report key)."""
        out = {}
        for f in (self.root / "reports").glob("triage_*"):
            if f.suffix in (".json", ".csv") and f.is_file() and not f.is_symlink() and self._is_inside(f):
                j, c = out.get(f.stem[len("triage_"):], (None, None))
                out[f.stem[len("triage_"):]] = (f, c) if f.suffix == ".json" else (j, f)
        return out

    def triage_history(self, limit=200):
        """Finished triages in this workspace, newest first: id, BOM, file name, when, who ran it (team mode),
        status counts and report links. Older triages that only left a .csv are listed too (no BOM / user)."""
        items = []
        for tid, (j, c) in self._triage_files().items():
            try:
                if j:
                    d = json.loads(j.read_text(encoding="utf-8"))
                    it = {k: d.get(k) for k in ("bom", "name", "finished", "user", "counts")}
                    it["lines"] = len(d.get("rows") or [])
                    it["reports"] = d.get("reports") or []
                elif c:
                    rows = pd.read_csv(c, dtype=str).fillna("")
                    st = rows["status"].value_counts() if "status" in rows else {}
                    it = {"bom": None, "name": tid, "user": None, "lines": len(rows),
                          "counts": {k: int(v) for k, v in dict(st).items()},
                          "finished": _now(c.stat().st_mtime),
                          "reports": [self.rel(x) for x in (c.with_suffix(".md"), c) if x.exists()]}
                else:
                    continue
            except (ValueError, OSError, KeyError, pd.errors.ParserError, pd.errors.EmptyDataError):
                continue                                  # a damaged report must not hide the others
            it["id"], mtime = tid, max(x.stat().st_mtime_ns for x in (j, c) if x)
            it["bom_exists"] = bool(it["bom"]) and self._is_inside(it["bom"]) and self.inside(it["bom"]).is_file()
            items.append((it.get("finished") or "", mtime, it))
        items.sort(key=lambda x: x[:2], reverse=True)                # same second: the file written last first
        return {"triages": [x[2] for x in items[:limit]], "total": len(items)}

    def triage_result(self, tid):
        """One finished triage by its id (from triage_history), in the shape triage() returns. Read-only: the id
        is only matched against the reports that exist, never turned into a path."""
        f = self._triage_files().get(str(tid or ""))
        if not f:
            raise KeyError(f"no triage {tid!r} in this workspace")
        j, c = f
        if j:
            d = json.loads(j.read_text(encoding="utf-8"))
            d["id"] = tid
            return d
        rows = pd.read_csv(c, dtype=str).fillna("").to_dict("records")
        for r in rows:
            with contextlib.suppress(ValueError, KeyError):
                r["line"] = int(r["line"])
        st = pd.Series([r.get("status", "") for r in rows]).value_counts() if rows else {}
        return {"id": tid, "bom": None, "name": tid, "user": None, "finished": _now(c.stat().st_mtime), "mapping": {},
                "rows": rows, "counts": {k: int(v) for k, v in dict(st).items()},
                "reports": [self.rel(x) for x in (c.with_suffix(".md"), c) if x.exists()]}

    def preview_footprint(self, spec, density=None):
        """Run the IPC-7351B generator without saving. Returns numbers, pads and an SVG drawing."""
        if isinstance(spec, str):
            spec = json.loads(spec)
        if not isinstance(spec, dict) or not spec.get("name"):
            raise ValueError("the package spec needs at least a \"name\"")
        try:
            F.validate(spec)
            text, rep = F.build(spec, RU.density_for(spec, self.rules(), density))
        except KeyError as e:                         # no jsonschema installed: the generator finds the gap itself
            raise ValueError(f"the package spec is missing {e.args[0]!r}") from e
        except Exception as e:
            if type(e).__name__ != "ValidationError":
                raise
            where = "/".join(map(str, e.absolute_path))
            raise ValueError(f"the package spec isn't valid{' at ' + where if where else ''}: {e.message}") from e
        sexpr.parse(text)
        name = self.safe_name(spec["name"], strict=True)
        return {"name": spec["name"], "report": rep, "kicad_mod": text, "svg": footprint_svg(rep, kicad_mod=text),
                "exists": (self.gen_dir / f"{name}.kicad_mod").exists()}

    def preview_symbol(self, pins, name="PART", split=None, stack=None, save=False, overwrite=False):
        """IC symbol with pins grouped by function (ecadlib.symbol_layout) under the workspace rules' [symbols].
        pins: [{num, name, type?, group?}] or a CSV/JSON path. Returns groups, parts, problems and an SVG; save=True
        also writes <workspace>/altium_models/SchLib/<name>.SchLib (refused if it exists unless overwrite)."""
        from . import altium_lib, symbol_layout as SL
        if isinstance(pins, str):
            p = self._bom_path(pins)
            name_, pins, _ = SL.load_pins(p)
            name = name if name != "PART" else name_
        sec = dict(self.rules()["symbols"])
        sec.update({k: v for k, v in (("split_pins", split), ("stack_power", stack)) if v is not None})
        sym = SL.build(name, pins, rules={"symbols": sec})
        groups = {}
        for q in SL.arrange_pins(pins, {"symbols": sec}):
            groups.setdefault(f"{chr(64 + q['unit'])}:{q['side']}:{q['group']}", []).append(q["num"])
        out = {"name": name, "pins": len(pins), "parts": sym.get("parts", 1), "groups": groups,
               "problems": SL.check(sym, sec["grid"]), "svg": SL.svg(sym)}
        if save:
            f = self.models / "SchLib" / f"{altium_lib.file_stem(self.safe_name(name, strict=True))}.SchLib"
            if f.exists() and not overwrite:
                raise FileExistsError(f"{f.name} is already in the workspace; save again with overwrite to replace it")
            f.parent.mkdir(parents=True, exist_ok=True)
            out["file"] = self.rel(altium_lib.write_schlib(f, [sym]))
        return out

    def approve_footprint(self, spec, density=None, overwrite=False):
        """Save the generated footprint into the workspace library (KiCad .kicad_mod + Altium .PcbLib),
        where triage and the Altium 365 plan will find it. Refuses to replace an existing one unless overwrite."""
        from . import altium_lib
        spec_obj = json.loads(spec) if isinstance(spec, str) else spec
        res = self.preview_footprint(spec_obj, density)
        name = self.safe_name(res["name"], strict=True)
        p = self.gen_dir / f"{name}.kicad_mod"
        if p.exists() and not overwrite:
            raise FileExistsError(f"{name} is already in the workspace library; approve again with overwrite to replace it")
        p.write_text(res["kicad_mod"], encoding="utf-8")
        (self.gen_dir / f"{name}.json").write_text(json.dumps(spec_obj, indent=2), encoding="utf-8")  # keep the source dimensions
        files = [self.rel(p)]
        warnings = [f"{c['rule']}: {c['message']}" for c in res["report"].get("checks", []) if c["level"] == "error"]
        try:
            rules = self.rules()
            fp, _ = altium_lib.footprint_from_spec(spec_obj, RU.density_for(spec_obj, rules, density))
            files.append(self.rel(altium_lib.write_pcblib(self.models / "PcbLib" / f"{name}.PcbLib", [fp], RU.layers(rules))))
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
        index, done, skipped, layers = self._index(), [], [], RU.layers(self.rules())
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
                altium_lib.write_pcblib(out, [altium_lib.footprint_from_kicad(Path(hit["path"]).read_text(encoding="utf-8"))], layers)
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
        tri = self._triage_csv(bom_path)
        out = self.root / "altium"
        argv = [str(bom_path), "-o", str(out)] + (["--triage", str(tri)] if tri.exists() else [])
        rf = self.rules_in_use()
        argv += ["--rules", str(rf)] if rf else []
        if altium_export.main(argv):
            raise ValueError(f"couldn't write the DbLib for {bom_path.name}: no part lines in it")
        return {"files": [self.rel(p) for p in sorted(out.iterdir()) if p.is_file()]}

    def altium365_plan(self, bom):
        """One workspace component per part. When the librarian has run for this BOM its plan is used, with the
        release check's ready / held verdicts; otherwise a part is ready when its .SchLib and .PcbLib are in
        altium_models/."""
        from . import altium365 as A
        bom_path = self._bom_path(bom)
        lines, _ = self._read_bom(bom_path)
        lib_plan = self._librarian_dir(bom_path) / "plan.json"
        if lib_plan.exists():
            items = json.loads(lib_plan.read_text(encoding="utf-8"))
            for it in items:                          # librarian paths are relative to its output folder
                for k in ("symbol", "footprint"):
                    f = it[k].get("file")
                    if f and not Path(f).is_absolute():
                        it[k]["file"] = str(lib_plan.parent / f)
            src, out = "librarian", lib_plan
        else:
            items = A.plan(lines, self._triage_frame(bom_path), self.models, rules=self.rules())
            out = self.root / "altium365" / f"plan_{self._key(bom_path)}.json"
            out.write_text(json.dumps(items, indent=2), encoding="utf-8")
            src = "triage"
        ready = sum(i["status"] == "ready" for i in items)
        return {"items": items, "ready": ready, "total": len(items), "plan": self.rel(out), "source": src}

    # ------------------------------------------------------------------ librarian
    def _librarian_dir(self, bom_path):
        return self.root / "librarian" / self._key(bom_path)

    def librarian_run(self, bom, sources=None, allow=(), refresh=None, progress=None):
        """BOM -> library in one go (ecadlib.librarian): triage, fill every missing symbol and footprint from
        the KiCad libraries, EasyEDA or the datasheet, write Altium models and the plan, run the release check.
        refresh: None resumes (finished parts are skipped), [] redoes everything, [names] redoes those."""
        from . import librarian as L
        bom_path = self._bom_path(bom)
        lines, _ = self._read_bom(bom_path)               # same friendly error as triage for unreadable files
        out = self._librarian_dir(bom_path)
        log, n = [], [0]

        def note(msg):
            log.append(str(msg).strip())
            n[0] += 1
            if progress:
                progress(min(n[0], len(lines)), len(lines), str(msg).strip()[:80])
        if progress:
            progress(0, len(lines), "looking up parts")
        provs = self._librarian_providers()
        items = L.run(str(bom_path), self.libs + [str(self.root / "footprints")], out,
                      list(sources) if sources else None, refresh, False, list(allow), self.offline,
                      self.root / "http_cache", 0.5, provs, log=note, rules=self.rules())
        return self._librarian_summary(bom_path, items, log)

    def release_check(self, bom, allow=(), unallow=(), user=None):
        """Re-run the release check on the librarian's plan, optionally releasing named parts despite their
        failures (allow) or taking that back (unallow). user, when given, is recorded as the person who gave the
        allow (approvals' no_self_approval uses it). Never touches the network."""
        with self._plan_lock():
            return self._release_check(bom, allow, unallow, user)

    def _release_check(self, bom, allow=(), unallow=(), user=None):
        from . import release_check as RC
        bom_path = self._bom_path(bom)
        out = self._librarian_dir(bom_path)
        plan_f = out / "plan.json"
        if not plan_f.exists():
            raise ValueError("no librarian plan for this BOM yet: run the librarian (Fill missing parts) first")
        items = json.loads(plan_f.read_text(encoding="utf-8"))
        unknown = set(allow) | set(unallow)
        unknown -= {i["name"] for i in items}
        if unknown:
            raise ValueError(f"not parts of this plan: {', '.join(sorted(unknown))}")
        rows = RC.apply(items, out, list(allow), list(unallow), who="ecadlib ui/mcp", rules=self.rules())
        user = user.strip() if isinstance(user, str) else ""
        if allow and user:
            from .approvals import post_check, stamp_allow
            stamp_allow(items, list(allow), user)
            post_check(items, out, rows)
        write_text_atomic(plan_f, json.dumps(items, indent=2))
        RC.write_report(rows, out / "release_check", plan_f)
        from . import librarian as L
        L.write_review(L.review_rows(items), out, str(bom_path))
        return self._librarian_summary(bom_path, items, [])

    @contextlib.contextmanager
    def _plan_lock(self):
        """<workspace>/agent/plan.lock around a plan rewrite (not re-entrant: locked methods call the unlocked _bodies): while the library agent's
        librarian rewrites a plan, a change written now would be overwritten by its copy, so it is refused instead."""
        from .agent_queue import plan_lock
        with plan_lock(self.root, wait=False) as free:
            if not free:
                raise ValueError("the library agent is rebuilding the library right now; try again in a minute")
            yield

    def _librarian_summary(self, bom_path, items, log):
        from . import librarian as L
        out = self._librarian_dir(bom_path)
        rows = L.review_rows(items)
        for r, it in zip(rows, items):
            r["allowed"] = bool(it.get("allow"))
            a = it.get("approval")
            r["approval"] = {k: a.get(k) for k in ("required", "met", "approved_by", "missing", "stale")} if a else None
            if it.get("duplicate_of"):
                r["duplicate_of"] = it["duplicate_of"]
        files = [out / f for f in ("review.md", "release_check.md", "plan.json", "dedupe.md")]
        return {"bom": self.rel(bom_path) if self._is_inside(bom_path) else str(bom_path),
                "parts": len(items), "ready": sum(i["status"] == "ready" for i in items),
                "held": sum(i["status"] == "held" for i in items),
                "awaiting_approval": sum(i["status"] == "awaiting_approval" for i in items),
                "duplicates": sum(i["status"] == "duplicate" for i in items),
                "needs_person": sum(bool(r["needs_human"]) for r in rows), "review": rows,
                "reports": [self.rel(f) for f in files if f.exists()], "log": log[-60:]}

    # ------------------------------------------------------------------ held parts (ecadlib.resolve)
    def _plan(self, bom):
        """(bom_path, plan.json path, items) of the librarian plan for a BOM; the plan is always inside the workspace."""
        bom_path = self._bom_path(bom)
        plan_f = self.inside(self.rel(self._librarian_dir(bom_path) / "plan.json"))
        if not plan_f.exists():
            raise ValueError("no librarian plan for this BOM yet: run the librarian (Fill missing parts) first")
        return bom_path, plan_f, json.loads(plan_f.read_text(encoding="utf-8"))

    @staticmethod
    def _part(items, part):
        if not isinstance(part, str) or not part.strip():
            raise ValueError("no part given")
        it = next((i for i in items if i["name"] == part), None)
        if it is None:
            raise ValueError(f"{part!r} is not a part of this plan")
        return it

    def _resolve_sources(self):
        from . import resolve as R
        return R.Sources(self.root / "http_cache", self.libs + [str(self.root / "footprints")], self.offline)

    @staticmethod
    def _kind(kind):
        from . import resolve as R
        if kind not in R.KINDS:
            raise ValueError(f"kind must be one of {', '.join(R.KINDS)}")
        return kind

    def _bom_label(self, bom_path):
        return self.rel(bom_path) if self._is_inside(bom_path) else str(bom_path)

    def held_parts(self, bom):
        """Held plan items: name, folder, missing halves, hold reasons and where each half came from. No network."""
        from . import resolve as R
        bom_path, plan_f, _ = self._plan(bom)
        return {"bom": self._bom_label(bom_path), "plan": self.rel(plan_f), "held": R.held(plan_f)}

    def part_candidates(self, bom, part, kind=None, limit=10, progress=None):
        """Ranked symbol / footprint candidates for one plan item (default: its missing halves, else both), with the
        item's current state. May fetch KiCad library files (through the HTTP cache; offline uses the cache only)."""
        from . import resolve as R
        _, plan_f, items = self._plan(bom)
        it = self._part(items, part)
        kinds = [self._kind(kind)] if kind else (R.missing(it) or list(R.KINDS))
        try:
            limit = max(1, min(50, int(limit or 10)))
        except (TypeError, ValueError):
            raise ValueError("limit must be a number") from None
        src, out = self._resolve_sources(), {}
        for n, k in enumerate(kinds):
            if progress:
                progress(n, len(kinds), f"{k} candidates for {part}")
            out[k] = R.candidates(it, k, plan_f.parent, src, limit)
        if progress:
            progress(len(kinds), len(kinds), "done")
        return {"part": part, "item": R.show(plan_f, part), "candidates": out}

    _CID = re.compile(r"(kicad|local):[^:/\\\0]+:[^:/\\\0]+|spec:[^:/\\\0]+")

    def choose_part(self, bom, part, kind, candidate_id, pin_map=None):
        """Use a candidate (kicad:Lib:Name, local:Lib:Name or spec:Name) for one half of a plan item, convert it into
        the librarian's models folder, then re-run the release check and rewrite the review / release-check reports.
        pin_map {symbol pin: pad} remaps pin numbers. Returns the librarian summary plus `chosen`."""
        with self._plan_lock():
            return self._choose_part(bom, part, kind, candidate_id, pin_map)

    def _choose_part(self, bom, part, kind, candidate_id, pin_map=None):
        from . import resolve as R
        _, plan_f, items = self._plan(bom)
        self._part(items, part)
        self._kind(kind)
        if not isinstance(candidate_id, str) or not self._CID.fullmatch(candidate_id) or \
                any(s.strip() in ("", ".", "..") for s in candidate_id.split(":")):
            raise ValueError(f"candidate id {candidate_id!r} must look like kicad:Lib:Name, local:Lib:Name or spec:Name")
        if pin_map is not None and not (isinstance(pin_map, dict) and all(
                isinstance(a, str) and isinstance(b, str) and a and b and len(a) < 40 and len(b) < 40 for a, b in pin_map.items())):
            raise ValueError("pin_map must map symbol pin numbers to pad numbers, e.g. {\"S1\": \"SH\"}")
        try:
            chosen = R.choose(plan_f, part, kind, candidate_id, src=self._resolve_sources(), pin_map=pin_map or None)
        except FileNotFoundError as e:
            raise ValueError(str(e)) from e
        out = self._release_check(bom)    # the whole plan again (lock already held): reports, statuses, approvals
        out["chosen"] = chosen
        return out

    # ------------------------------------------------------------------ sign-off (ecadlib.approvals)
    def approval_status(self, bom):
        """The sign-off policy next to the plan (none = approvals are recorded, not enforced) and one row per part."""
        from . import approvals as AP
        bom_path, plan_f, items = self._plan(bom)
        base = plan_f.parent
        policy = AP.load_policy(base)
        pf, audit = AP.policy_file(base), base / AP.AUDIT
        return {"bom": self._bom_label(bom_path), "policy": policy, "policy_file": self.rel(pf) if pf else None,
                "rows": AP.status_rows(items, policy, base),
                "awaiting_approval": sum(i.get("status") == "awaiting_approval" for i in items),
                "audit": self.rel(audit) if audit.exists() else None}

    def approve_part(self, bom, part, decision, user, role=None, comment="", via="ecadlib ui/mcp"):
        """Record an approve / reject (see _approve_part). Holds <workspace>/agent/plan.lock while it rewrites the plan:
        while the library agent's librarian rewrites a plan the decision is refused ("decide again in a minute")
        instead of being overwritten when the librarian saves its copy."""
        from .agent_queue import plan_lock
        with plan_lock(self.root, wait=False) as free:
            if not free:
                raise ValueError("the library agent is rebuilding the library right now; decide again in a minute")
            return self._approve_part(bom, part, decision, user, role, comment, via)

    def _approve_part(self, bom, part, decision, user, role=None, comment="", via="ecadlib ui/mcp"):
        """Record an approve / reject for one part as `user`. user is required: locally there is no login, so the
        caller says who it is (never taken from this machine's git identity); the team server passes the signed-in
        user and via="ecadlib ui (team, signed in)", which the audit entry records. Every decision, refused ones included, is
        appended to approvals_audit.jsonl; the part then flips between ready and awaiting_approval."""
        from . import approvals as AP, librarian as L
        if decision not in ("approve", "reject"):
            raise ValueError("decision must be approve or reject")
        user = user.strip() if isinstance(user, str) else ""
        if not user:
            raise ValueError("say who you are: user is required (e.g. \"Ann Lee <ann@example.com>\")")
        if len(user) > 200 or any(c in user for c in "\r\n\0"):
            raise ValueError("user must be one line of at most 200 characters")
        role = (role.strip() if isinstance(role, str) else "") or None
        comment = comment if isinstance(comment, str) else ""
        bom_path, plan_f, items = self._plan(bom)
        it, base = self._part(items, part), plan_f.parent
        policy = AP.load_policy(base)
        entry = {"ts": AP.now(), "plan": str(plan_f), "part": part, "who": user, "role": role or "", "decision": decision,
                 "comment": comment, "via": via}
        try:
            rec = AP.decide(it, decision, user, role, comment, base, policy)
        except AP.Refused as e:
            entry["result"] = f"refused: {e}"
            AP.audit(base, entry)
            raise ValueError(str(e)) from e
        status = AP.refresh_status(it, policy, base)
        entry.update(role=rec["role"], hash=rec["hash"], result="recorded", status=status)
        AP.audit(base, entry)
        write_text_atomic(plan_f, json.dumps(items, indent=2))
        L.write_review(L.review_rows(items), base, str(bom_path))
        out = self._librarian_summary(bom_path, items, [])
        out["decision"] = {"part": part, "decision": decision, "who": user, "role": rec["role"], "status": status,
                           "enforced": bool(policy)}
        return out

    # ------------------------------------------------------------------ library agent review queue (ecadlib.agent_queue)
    def agent_queue_list(self, status=None, batch=None):
        """Queue items (summaries, newest first), counts per state and batches; batch=ID adds that batch."""
        from . import agent_queue as AQ
        return AQ.ws_list(self.root, status, batch)

    def agent_queue_get(self, item_id):
        """One queue item in full, its showable pictures (paths inside agent/), allowed next states and its batch."""
        from . import agent_queue as AQ
        return AQ.ws_get(self.root, item_id)

    def agent_queue_decide(self, item_id, action, user, role=None, note="", via="ecadlib ui/mcp", aliases=(), expect=None):
        """approve / reject / rebuild / retry a queue item as `user`; approve and reject also go through approvals.py.
        expect: the build_token agent_queue_get gave; the decision is refused if the item was rebuilt since."""
        from . import agent_queue as AQ
        return AQ.decide(self, item_id, action, user, role, note, via, aliases, expect=expect)

    def agent_picture(self, path):
        """Path of a picture under <workspace>/agent/ (anything else: ValueError)."""
        from . import agent_queue as AQ
        return AQ.picture_path(self.root, path)

    # ------------------------------------------------------------------ duplicates (ecadlib.dedupe)
    def find_duplicates(self, bom, merge=False):
        """Duplicate parts (same MPN), footprints (same geometry) and symbols (same pinout) across the librarian plan
        and the workspace's altium_models/ and footprints/. merge=True points duplicates at their keeper in the plan
        (library files are never changed; plan.json.pre-dedupe keeps the old plan) and re-runs the release check."""
        with self._plan_lock() if merge else contextlib.nullcontext():
            return self._find_duplicates(bom, merge)

    def _find_duplicates(self, bom, merge=False):
        from . import dedupe as D
        _, plan_f, _ = self._plan(bom)
        libs = [p for p in (self.models, self.root / "footprints") if p.exists()]
        r = D.run(plan_f, libs, do_merge=bool(merge), who="ecadlib ui/mcp")
        keep = ("name", "origin", "mpn", "used_by", "status", "where", "files", "file")
        short = lambda f: self.rel(f) if self._is_inside(f) else str(f)
        groups = []
        for g in r["groups"]:
            members = []
            for m in g["members"]:
                m = {k: m[k] for k in keep if m.get(k) not in (None, "", [])}
                if "files" in m:
                    m["files"] = [short(f) for f in m["files"]]
                    m.pop("file", None)
                elif "file" in m:
                    m["file"] = short(m["file"])
                members.append(m)
            groups.append({"kind": g["kind"], "key": g["key"], "keeper": g["keeper"], "members": members})
        out = {"groups": groups, "mapping": r["mapping"], "errors": r["errors"], "merged": bool(merge),
               "reports": [self.rel(r["report"]), self.rel(r["map"])]}
        if merge:
            out["summary"] = self._release_check(bom)          # the plan lock is already held
        return out

    # ------------------------------------------------------------------ upkeep (ecadlib.upkeep)
    def upkeep_run(self, bom, checks=None, apply=False, workspace=None):
        """The weekly upkeep checks for a BOM (its librarian plan when there is one): lifecycle, KiCad upstream, price
        and stock, Altium 365 drift. Reports only what changed since the last upkeep run of this BOM; checks without
        credentials are skipped. apply=True reconverts KiCad parts whose sources changed. Never touches GitHub."""
        from . import upkeep as UK
        bom_path = self._bom_path(bom)
        base = self.root / "upkeep" / self._key(bom_path)
        plan_f = self._librarian_dir(bom_path) / "plan.json"
        src = plan_f if plan_f.exists() else bom_path
        manifest = next((m for m in (plan_f.parent / "models" / "manifest.json", self.models / "manifest.json")
                         if m.is_file()), None)
        rules = self.rules()
        rep = UK.run(str(src), checks or None, base / "state", base / "upkeep", manifest and str(manifest),
                     str(bom_path) if src != bom_path else None, workspace or None, bool(apply), rules,
                     UK.Ctx(cache=self.root / "http_cache", offline=self.offline))
        rel = lambda p: self.rel(p) if self._is_inside(p) else str(p)
        return {"bom": self._bom_label(bom_path), "input": rel(src), "new_changes": rep["new_changes"],
                "exit_code": rep["exit_code"],
                "checks": [{k: s[k] for k in ("check", "status", "reason", "changes", "resolved", "notes")} for s in rep["checks"]],
                "applied": [rel(p) for p in rep["applied_paths"]],
                "reports": [self.rel(base / "upkeep.md"), self.rel(base / "upkeep.json")]}
    # ------------------------------------------------------------------ library agent (ecadlib.agent)
    def agent_status(self):
        """What the library agent did last (status.json), its queue counts per state and its batches/PRs."""
        from . import agent as AG
        return AG.status(self.root)

    def agent_run(self, dry_run=True):
        """One agent pass now: scan watched sources, build and prove new parts, batch PR, push (if [agent] auto_push).
        dry_run (the default) reports what would happen and changes nothing."""
        from . import agent as AG
        rep = AG.run_once(self.root, self.rules(), dry_run=bool(dry_run), libs=self.libs)
        return {k: v for k, v in rep.items() if k != "push"} | {"push": rep.get("push")}

    # ------------------------------------------------------------------ Altium projects (ecadlib.altium_project)
    def project_bom(self, project, variant=None):
        """BOM straight from an Altium .PrjPcb / .SchDoc (with variants and ActiveBOM choices); writes
        reports/project_<name>.csv, which triage/librarian also accept, as does the project file itself."""
        from . import altium_project as AP
        p = self._bom_path(project)
        prj = AP.load_project(p)
        groups = AP.bom_groups(prj, variant)
        variants = [v["name"] for v in prj["variants"]]
        out = AP.write_bom_csv(groups, self.root / "reports" / f"project_{self._key(p)}{'_' + self.safe_name(variant) if variant else ''}.csv", variants)
        keep = ("designator", "qty", "mpn", "manufacturer", "value", "footprint", "description", "libref", "design_item_id",
                "dnp", "alternates", "variant_notes", "variants", "lifecycle", "item_guid", "revision_guid")
        return {"project": prj["name"], "variant": variant, "variants": variants, "sheets": [Path(s).name for s in prj["sheets"]],
                "components": len(prj["components"]), "lines": [{k: g[k] for k in keep} for g in groups],
                "warnings": prj["warnings"], "csv": self.rel(out)}

    def where_used(self, projects, part=None, plan=None):
        """Which projects use which part. projects: folders or project files. The library compared against is the
        given librarian plan (or every librarian plan in the workspace) plus altium_models/."""
        from . import altium_project as AP
        dirs = [self._bom_path(d) for d in ([projects] if isinstance(projects, str) else projects)]
        plans = [self._bom_path(plan)] if plan else sorted((self.root / "librarian").glob("*/plan.json"))
        if plan and not plans[0].is_file():
            raise ValueError(f"plan not found: {plan} is not a plan.json file")
        libs = [str(self.models)] if self.models.exists() else []
        res = AP.where_used(dirs, part, plans[0] if plans else None, libs)
        files = AP.write_where_used(res, self.root / "reports" / "where_used")
        rows = AP.where_used_rows(res)
        return {"projects": res["projects"], "flags": res["flags"], "rows": rows, "warnings": res["warnings"],
                "library": [self.rel(p) if self._is_inside(p) else str(p) for p in plans[:1]] + [self.rel(l) for l in libs],
                "reports": [self.rel(f) for f in files.values()]}
    def sim_models(self, bom, attach=False, models=None):
        """Find and check SPICE / IBIS models for the librarian plan's parts (built-in passives, the workspace's
        sim_models/ folder plus `models` folders inside the workspace, Nexar documents, vendor URL patterns).
        attach=True writes SIM links into the .SchLib files and Sim.* fields for kicad-export, and re-runs the
        release check (a model whose pin map is unverified holds its part as `verify`)."""
        from . import sim_models as SM
        _, plan_f, _ = self._plan(bom)
        dirs = [self.inside(m) for m in ([models] if isinstance(models, str) else models or [])]
        own = self.root / "sim_models"
        if own.is_dir():
            dirs.insert(0, own)
        rep = SM.run(plan_f, [d for d in dirs if d.is_dir()], attach=bool(attach),
                     http=CachedHTTP(self.root / "http_cache", offline=self.offline, timeout=60), rules=self.rules(), log=lambda *a: None)
        return {"attach": rep["attach"], "parts": rep["parts"], "hosts": rep["hosts"], "altium365": rep["altium365"],
                "errors": rep.get("errors", []), "reports": [self.rel(plan_f.parent / "sim_models.md"), self.rel(plan_f.parent / "sim_models.json")]}

    def _datasheet_reader(self, out, batch=True, wait=False, estimate=False, yes=False):
        from . import datasheet as D
        return D.Reader(out, self.root / "http_cache", rules=self.rules(), batch=batch, wait=wait, estimate=estimate, yes=yes,
                        http=CachedHTTP(self.root / "http_cache", offline=self.offline, timeout=60), log=lambda *a: None)

    def datasheet_batch(self, bom, sync=False, wait=False, parts=None, yes=False):
        """Read the datasheet of every part of the librarian plan that still has no symbol or footprint, as one
        Message Batch (sync=True: one Messages call per part), build them, mark them `verify` and re-run the release
        check. wait=False submits and returns; call again to collect (the batch id is kept, nothing is paid twice).
        With no plan yet, the librarian runs first with the batch as its datasheet step."""
        from . import datasheet as D, librarian as L
        bom_path = self._bom_path(bom)
        out = self._librarian_dir(bom_path)
        rd = self._datasheet_reader(out, batch=not sync, wait=wait, yes=yes)
        if not rd.key:
            raise ValueError(f"{D.settings(self.rules())['api_key_env']} is not set: the datasheet reader needs a Claude API key "
                             "(datasheet_estimate works without one)")
        log = []
        rd.say = lambda m: log.append(str(m).strip())
        if (out / "plan.json").exists():
            items = D.fill_plan(out / "plan.json", rd, self.rules(), log=log.append, parts=set(parts or []) or None)
        else:
            self._read_bom(bom_path)
            provs = self._librarian_providers()
            items = L.run(str(bom_path), self.libs + [str(self.root / "footprints")], out, None, None, False, [], self.offline,
                          self.root / "http_cache", 0.5, provs, http=rd.http, log=log.append, rules=self.rules(), reader=rd)
        state = rd._state()
        r = self._librarian_summary(bom_path, items, log)
        r["batches"] = [{k: b.get(k) for k in ("id", "status", "counts", "collected", "errors")} for b in state["batches"]][-5:]
        r["pending"] = sum(1 for b in state["batches"] if not b.get("collected"))
        return r

    def datasheet_estimate(self, bom):
        """Tokens and cost of reading the datasheets the librarian plan still needs, before anything is sent (exact
        input counts with an API key, rough without). Downloads the datasheets (cached) to count their pages."""
        from . import datasheet as D
        bom_path = self._bom_path(bom)
        out = self._librarian_dir(bom_path)
        plan_f = out / "plan.json"
        if not plan_f.exists():
            raise ValueError("no librarian plan for this BOM yet: run the librarian (Fill missing parts) first")
        est = D.estimate_plan(plan_f, self._datasheet_reader(out, estimate=True))
        return dict(est, key_set=est["exact_input"])

    def cross_checks(self, bom):
        """Which datasheet parts of the librarian plan were cross-checked against a second source (ecadlib.crosscheck)
        and how: agreed, disagreed (held, both values in the review), unchecked or skipped. Sends nothing."""
        import json
        from . import crosscheck as CC
        out = self._librarian_dir(self._bom_path(bom))
        plan_f = out / "plan.json"
        if not plan_f.exists():
            raise ValueError("no librarian plan for this BOM yet: run the librarian (Fill missing parts) first")
        parts = [{"part": it["name"], "status": it.get("status"), "cross_check": it["cross_check"]["status"],
                  "summary": it["cross_check"].get("summary", "")}
                 for it in json.loads(plan_f.read_text(encoding="utf-8")) if it.get("cross_check")]
        counts = {s: sum(p["cross_check"] == s for p in parts) for s in ("agreed", "disagreed", "unchecked", "skipped")}
        return {"parts": parts, "counts": counts, "modes": list(CC.MODES)}

    def altium365_workspaces(self):
        from . import altium365 as A
        return {"workspaces": A.Nexar(_nexar_token()).workspaces()}

    def altium365_push(self, bom, workspace_url, dry_run=True):
        """Release ready parts and update parameters of existing ones; parts the release check held are left
        alone. dry_run (the default) sends nothing and returns the requests that would be made."""
        from . import altium365 as A
        if not workspace_url and not dry_run:
            raise ValueError("choose an Altium 365 workspace first")
        plan = self.altium365_plan(bom)
        client = None if dry_run else A.Nexar(_nexar_token())
        policy = None
        if plan["source"] == "librarian":             # the sign-off policy as it is now, not as at the last check
            from .approvals import load_policy
            policy = load_policy(self.inside(plan["plan"]).parent)
        results = A.push(plan["items"], client, workspace_url or "https://<workspace>", dry_run=dry_run, policy=policy,
                         rules=self.rules())
        if any(r.get("a365_updated") for r in results):
            from .a365_sync import save_meta          # new revisions of pulled parts, so the next push is not a conflict
            save_meta(self.inside(plan["plan"]), plan["items"])
        out = self.root / "altium365" / f"push_{'dry_run_' if dry_run else ''}{self._key(self._bom_path(bom))}.json"
        out.write_text(json.dumps(results, indent=2), encoding="utf-8")
        return {"dry_run": dry_run, "results": results, "report": self.rel(out)}

    # ------------------------------------------------------------------ two-way sync (ecadlib.a365_sync)
    def _a365_plan(self, bom=None):
        """The plan a pull writes into: the librarian plan of a BOM (so the librarian reuses workspace parts), or
        altium365/workspace_plan.json for the whole workspace library."""
        if bom:
            f = self._librarian_dir(self._bom_path(bom)) / "plan.json"
            f.parent.mkdir(parents=True, exist_ok=True)
            return self.inside(self.rel(f))
        return self.root / "altium365" / "workspace_plan.json"

    def _a365_run(self, mode, workspace_url, bom=None, folders=None, client=None):
        from . import a365_sync as S, altium365 as A
        if not workspace_url:
            raise ValueError("choose an Altium 365 workspace first")
        r = S.run(mode, self._a365_plan(bom), client or A.Nexar(_nexar_token()), workspace_url,
                  folders=folders or None, rules=self.rules(), log=lambda *_: None)
        return {"mode": mode, "summary": r["summary"], "conflicts": r["conflicts"], "unresolved": r["unresolved"],
                "rows": [x for x in r["rows"] if x["status"] != "in_sync"], "components": len(r["components"]),
                "plan": self.rel(r["plan"]), "state": self.rel(r["state"]) if mode != "status" else None,
                "report": self.rel(r["report"])}

    def a365_pull(self, workspace_url, bom=None, folders=None, client=None):
        """Import the workspace's components into a plan (status `existing`), refresh ones pulled before, download
        their .SchLib/.PcbLib. Changes local files only; conflicts are reported, not settled."""
        return self._a365_run("pull", workspace_url, bom, folders, client)

    def a365_status(self, workspace_url, bom=None, client=None):
        """Read-only comparison of the plan with the workspace (new, changed on either side, conflicts, deleted)."""
        return self._a365_run("status", workspace_url, bom, None, client)

    # ------------------------------------------------------------------ library agent: watch (ecadlib.agent_watch)
    def agent_watch_scan(self, sources=None, dry_run=False, client=None):
        """New / changed / removed parts in the watched projects and BOMs ([agent.watch] sources, or `sources`), compared
        with the last scan (agent/watch_state.json; dry_run leaves it alone). Relative refs are workspace-relative."""
        from . import agent_watch as AW
        rep = AW.scan_report(self, sources=sources, client=client, dry_run=dry_run)
        rep["state"] = self.rel(rep["state"])
        return rep

    # ------------------------------------------------------------------ library rules (ecadlib.rules / rules_infer)
    def rules_file(self):
        """<workspace>/ecadlib.toml (or ecadlib_rules.json when only that exists)."""
        return next((self.root / n for n in RU.FILES if (self.root / n).is_file()), self.root / RU.FILES[0])

    def rules_in_use(self):
        """The rules file the workspace follows: its own, else the one every ecadlib command run here finds (the
        workspace's parents, then the current folder and its parents: `ecadlib init` writes ./ecadlib.toml). The
        tools the workspace runs by path (DbLib export, approvals, upkeep) search the same way, so all agree. None: no file."""
        f = self.rules_file()
        return f if f.is_file() else RU.find(self.root)

    def _label(self, p):
        return self.rel(p) if self._is_inside(p) else str(p)

    def rules(self):
        """The rules in use (rules_in_use) merged over the defaults (no file: ecadlib's own behaviour)."""
        f = self.rules_in_use()
        return RU.load(f) if f else RU.load({})

    def rules_info(self):
        f = self.rules_in_use()
        return {"file": self._label(f or self.rules_file()), "exists": bool(f), "text": f.read_text(encoding="utf-8") if f else "",
                "rules": RU.effective(self.rules()), "defaults": RU.DEFAULTS}

    def _rules_sources(self):
        """What a scan looks at when the person names nothing: the --lib folders, uploaded BOMs and Altium 365 state."""
        out = list(self.libs) + [str(self.root / "uploads")]
        out += [str(p) for p in sorted(self.root.glob("**/a365_state.json")) if self._is_inside(p)]
        return out

    def propose_rules(self, paths=None, a365=None, choices=None, progress=None):
        """Scan existing libraries (Altium .SchLib/.PcbLib/.DbLib, parts tables, KiCad libraries, BOMs, Altium 365
        component lists) and propose rules with evidence and a confidence per setting. Nothing is written; the
        proposal's `toml` goes to save_rules. paths default to the workspace's --lib folders and uploaded BOMs.
        choices {setting key: value} answer the proposal's questions. A diff is included when a file exists."""
        from . import rules_infer as I
        paths, a365 = ([x] if isinstance(x, (str, Path)) else x for x in (paths, a365))   # one path, not its letters
        paths = [str(p).strip() for p in (paths or []) if str(p).strip()] or self._rules_sources()
        if progress:
            progress(0, 1, "scanning " + ", ".join(Path(p).name for p in paths)[:80])
        out = I.propose(paths, [str(x) for x in (a365 or [])], choices, existing=self.rules_file())
        out["paths"], out["file"] = paths, self.rel(self.rules_file())
        if progress:
            progress(1, 1, "done")
        return out

    def save_rules(self, text, overwrite=False):
        """Validate and write <workspace>/ecadlib.toml. An existing different file is only replaced with overwrite=True
        (kept as ecadlib.toml.bak); otherwise the diff comes back with needs_confirm, for the person to look at."""
        from . import rules_infer as I
        if not isinstance(text, str) or not text.strip():
            raise ValueError("no rules text given")
        res = I.save(text, self.rules_file(), bool(overwrite))
        res["path"] = self.rel(self.rules_file())
        if res.get("backup"):
            res["backup"] = self.rel(res["backup"])
        return res

    def file_formats(self, migrate=False):
        """Versioned files in this workspace (ecadlib/migrate.py): kind, version, ok / old / new. migrate=True brings
        old ones to this version (backups in .ecadlib_backup/ next to them); files from a newer ecadlib are refused."""
        from . import migrate as MG
        rows = MG.migrate_tree(self.root, dry_run=not migrate, log=lambda *a: None)
        for r in rows:
            r["path"] = self.rel(Path(r["path"]))
        return {"rows": rows, "old": sum(r["state"] == "old" for r in rows),
                "newer": sum(r["state"] in ("new", "error") for r in rows), "migrated": bool(migrate)}

    def rebuild_check(self, bom):
        """Rebuild the BOM's library from its library.lock.json offline in a temporary copy (ecadlib/rebuild.py) and
        report per part and file: identical, identical after normalising dates, or drift; and inputs that changed."""
        from . import rebuild as RB
        d = self._librarian_dir(self._bom_path(bom))
        if not (d / RB.LOCK).is_file():
            raise ValueError("no library.lock.json for this BOM yet: run the librarian (Fill missing parts) first")
        rep = RB.check(d, log=lambda *a: None)
        rep["lock"] = self.rel(Path(rep["lock"]))
        return rep

    def visual_check(self, target, datasheet=None, pages=None, symbol=None, render_only=False, client=None):
        """Claude visual proof (ecadlib.visual_check): render our footprint / symbol (TOP VIEW, pin 1, pitch, grid)
        next to the datasheet pages and ask Claude whether they match. target: a part name in this workspace
        (approved footprint, librarian plan) or a workspace path to a .kicad_mod / package spec .json. Writes the
        pictures and proof.json under agent/proofs/<part>/. No API key -> verdict "skipped" (pictures still written)."""
        from . import visual_check as VC
        item = {"datasheet": datasheet, "pages": pages, "symbol": None}
        try:
            p = self.inside(target)
        except ValueError:
            p = None
        if p is not None and p.is_file():
            item.update(footprint=str(p), part=p.stem)
        else:
            found = VC._find_part(str(target), self.root)
            if not found:
                raise ValueError(f"{target}: not a footprint file or a part in this workspace")
            item.update({k: v for k, v in found.items() if not item.get(k)}, part=str(target))
        if symbol:
            item["symbol"] = str(self.inside(symbol))
        if datasheet and not str(datasheet).startswith("http"):
            item["datasheet"] = str(self.inside(datasheet))
        out = self.root / "agent" / "proofs" / self.safe_name(item["part"])
        pr = VC.prove(item, self.root, self.rules(), client=client, out_dir=out, render_only=render_only)
        pr["proof_file"] = self.rel(VC.write_proof(pr, out / "proof.json"))
        pr["images"] = {k: [self.rel(f) for f in v] for k, v in pr["images"].items()}
        return pr

    def export_parts(self, bom, fmt="csv", include_held=False, name="ecadlib"):
        """The library's parts table (stable columns, ecadlib.parts_export) for a BOM's plan, as reports/parts_<bom>.<fmt>."""
        from . import parts_export as PE
        if fmt not in ("csv", "json", "xlsx"):
            raise ValueError(f"format must be csv, json or xlsx, not {str(fmt)[:20]!r}")
        bom_path, plan_f, _ = self._plan(bom)
        rows = PE.rows(plan_f, name, include_held)
        out = PE.write(rows, self.root / "reports" / f"parts_{self._key(bom_path)}.{fmt}", fmt)
        return {"file": self.rel(out), "parts": len(rows), "columns": PE.COLUMNS}

    def kicad_dblib(self, bom, name="ecadlib", driver=None, include_held=False):
        """KiCad files (kicad-export) plus a KiCad database library (.kicad_dbl + SQLite) for a BOM's plan, in kicad/<bom>/."""
        from . import kicad_dblib as KD
        from .kicad_export import kname
        bom_path, plan_f, _ = self._plan(bom)
        out = self.root / "kicad" / self._key(bom_path)
        rep = KD.build(plan_f, out, kname(str(name))[:60], include_held, self.rules_in_use(), driver or KD.DEFAULT_DRIVER,
                       export=True, http=CachedHTTP(self.root / "http_cache", offline=self.offline), log=lambda *a: None)
        return {"folder": self.rel(out), "files": [self.rel(p) for p in sorted(out.iterdir()) if p.is_file()],
                "tables": rep["tables"], "skipped": rep["skipped"], "warnings": rep["warnings"],
                "kicad_export": rep.get("kicad_export")}

    def tool_export(self, bom, tool, name="ecadlib", include_held=False):
        """The BOM's library for another tool, written natively and read back pad by pad: tool "eagle" (Autodesk Fusion
        Electronics / EAGLE .lbr, in fusion/<bom>/) or "easyeda" (EasyEDA Pro .elibz, in easyeda/<bom>/)."""
        from .kicad_export import kname
        mods = {"eagle": ("eagle_export", "fusion"), "easyeda": ("easyeda_pro_export", "easyeda")}
        if tool not in mods:
            raise ValueError(f"tool must be eagle or easyeda, not {str(tool)[:20]!r}")
        import importlib
        mod = importlib.import_module(f"ecadlib.{mods[tool][0]}")
        bom_path, plan_f, _ = self._plan(bom)
        out = self.root / mods[tool][1] / self._key(bom_path)
        rep = mod.export(plan_f, out, kname(str(name))[:60], include_held, http=CachedHTTP(self.root / "http_cache", offline=self.offline),
                         log=lambda *a: None, rules=self.rules_in_use())
        return {"folder": self.rel(out), "file": self.rel(out / rep["file"]), "counts": rep["counts"],
                "ok": not rep["errors"], "errors": rep["errors"][:30], "approximations": rep["warnings"][:30],
                "models": len(rep["models"]), "kicad_reread": rep.get("kicad_reread"),
                "report": self.rel(out / ("eagle_export.json" if tool == "eagle" else "easyeda_export.json"))}

    def parity(self, bom, kicad=False, name="ecadlib"):
        """KiCad vs Altium parity of a BOM's library (ecadlib.parity): the plan's .kicad_mod against its .PcbLib / .SchLib,
        or with kicad=True the KiCad export in kicad/<bom>/ (run kicad_dblib first) against the Altium files, its
        .kicad_dbl rows included. Report in reports/parity_<bom>.md / .json. Never touches the network."""
        from . import parity as PY
        from .kicad_export import kname
        bom_path, plan_f, _ = self._plan(bom)
        kdir = None
        if kicad:
            kdir = self.root / "kicad" / self._key(bom_path)
            if not (kdir / f"{kname(str(name))[:60]}.kicad_sym").is_file():
                raise ValueError(f"no KiCad export in {self.rel(kdir)} yet: run kicad_dblib (or kicad-export) first")
        rep = PY.check_plan(plan_f, kdir, kname(str(name))[:60] if kdir else None, rules=self.rules_in_use())
        stem = PY.write_report(rep, self.root / "reports" / f"parity_{self._key(bom_path)}")
        bad = [p for p in rep["parts"] if p["result"] == "mismatch"]
        return {"mode": rep["mode"], "ok": rep["ok"], "counts": rep["counts"], "report": self.rel(stem.with_suffix(".md")),
                "mismatches": {p["part"]: [d["msg"] for d in p["diffs"] if d["level"] == "error"][:20] for p in bad},
                "database": [d["msg"] for d in rep["database"]][:50]}

    def tool_packages(self, bom, name="ecadlib", kicad=8, include_held=False):
        """Packages that plug into the tools themselves, for a BOM's plan, in packages/<bom>/: a KiCad Plugin and
        Content Manager zip (kicad_pcm) and the Altium hand-off folder (altium_pkg: .LibPkg, install script, DbLib)."""
        from . import altium_pkg as AP, kicad_pcm as KP
        from .kicad_export import kname
        bom_path, plan_f, _ = self._plan(bom)
        lib = kname(str(name))[:60]
        out = self.root / "packages" / self._key(bom_path)
        pcm = KP.build(plan_f, out / "kicad", lib, kicad=int(kicad), include_held=include_held,
                       http=CachedHTTP(self.root / "http_cache", offline=self.offline), log=lambda *a: None)
        alt = AP.build(plan_f, out / "altium", lib, rules=self.rules_in_use(), log=lambda *a: None)
        return {"folder": self.rel(out),
                "kicad": {"zip": self.rel(Path(pcm["zip"])), "version": pcm["version"], "nickname": pcm["nickname"],
                          "symbols": pcm["symbols"], "footprints": pcm["footprints"], "problems": pcm["problems"]},
                "altium": {"folder": self.rel(out / "altium"), "libpkg": alt["libpkg"], "script": alt["script"],
                           "dblibs": alt["dblibs"], "documents": len(alt["documents"]), "problems": alt["problems"]}}

    def altium_menu(self, win_dir=None, name="ecadlib", command="", port=8765):
        """The "ecadlib" menu for Altium (ecadlib.altium_menu) in altium_menu/: a DelphiScript project whose entries
        add a part from its MPN, check the focused project's BOM and open the review queue, all in this workspace
        (settings use relative paths: workspace=.., library=..\\altium_library). win_dir: the Windows folder the
        altium_menu folder is in on the Altium PC, for the ready-made menu file (.DXPPrf)."""
        from . import altium_menu as AM
        out = self.root / "altium_menu"
        rep = AM.build(out, "..", "..\\altium_library", name, command, win_dir or None, int(port))
        return {"folder": self.rel(out), "files": [self.rel(p) for p in sorted(out.iterdir()) if p.is_file()],
                "written": rep["written"], "kept": rep["kept"], "menu_file": rep["menu_file"], "problems": rep["problems"],
                "procedures": rep["procedures"]}

    def example_specs(self):
        d = Path(__file__).resolve().parent / "package_specs"      # shipped as package data
        return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(d.glob("*.json"))} if d.exists() else {}

    # ------------------------------------------------------------------ setup check and summary (ecadlib.doctor / ws_status)
    def status(self):
        """One-screen summary (read-only): parts per librarian plan, held, waiting for sign-off, last triage, last
        upkeep, the library agent's state and queue counts. Paths are relative to the workspace."""
        from . import ws_status
        return ws_status.status(self.root)

    def doctor(self):
        """The `ecadlib doctor` checks for this workspace and its --lib folders: each {check, level OK/WARN/FAIL,
        detail, hint}. API keys are reported by name only."""
        from . import doctor
        rf = self.rules_in_use()
        return doctor.run(self.root, self.libs, str(rf) if rf else None)

    # ------------------------------------------------------------------ plugins (ecadlib.plugins), notifications
    def _provider_classes(self):
        """Triage sources: --providers when given, else [providers] order, else every built-in (today's default)."""
        from . import plugins as PL
        return PL.select(self.provider_names if self._providers_given else None, self.rules(),
                         default=[c.name for c in P.ALL])

    def _librarian_providers(self):
        """The librarian's sources: [providers] order when set (and no --providers), else the free built-in two."""
        from . import plugins as PL
        if not self._providers_given and PL.order_set(self.rules()):
            return None
        return ",".join(p for p in ("jlcpcb_search", "easyeda") if p in self.provider_names)

    def plugins(self):
        """Installed provider / exporter plugins, the [providers] order in use and the [notify] targets (no secrets)."""
        from . import notify as N, plugins as PL
        d = PL.describe()
        r = self.rules()
        d["order"] = [c.name for c in self._provider_classes()]
        d["notify"] = [{"name": t["name"], "type": t["type"], "events": sorted(t["events"]), "url_env": t["url_env"],
                        "url_set": bool(os.environ.get(t["url_env"]))} for t in N.settings(r)["targets"]]
        return d

    def plugin_export(self, name, bom):
        """Run exporter plugin `name` on the librarian plan of a BOM; files go to <ws>/exports/<name>/<bom stem>/."""
        from . import plugins as PL
        bom_path = self._bom_path(bom)
        plan = self._librarian_dir(bom_path) / "plan.json"
        if not plan.exists():
            raise ValueError("run the librarian on this BOM first (no plan.json)")
        out = self.root / "exports" / self.safe_name(name) / self.safe_name(bom_path.stem)
        files = PL.run_exporter(name, plan, out, self.rules())
        return {"exporter": name, "out": self.rel(out),
                "files": [self.rel(f) if self.root in Path(f).resolve().parents else str(f) for f in files]}


def _now(ts=None):
    t = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc) if ts else datetime.datetime.now(datetime.timezone.utc)
    return t.replace(microsecond=0).isoformat()


def _nexar_token():
    tok = os.environ.get("NEXAR_TOKEN")
    if not tok:
        raise ValueError("set NEXAR_TOKEN before starting ecadlib (a Nexar portal token with the "
                         "design.domain, user.access and openid scopes)")
    return tok


def _svg_outline(g):
    """fp_line / fp_rect / fp_poly -> list of (x, y) points (KiCad frame) and whether it is closed."""
    if g[0] == "fp_poly":
        return [(float(c[1]), float(c[2])) for c in sexpr.find(sexpr.first(g, "pts") or [], "xy")], True
    (x1, y1), (x2, y2) = [(float(c[1]), float(c[2])) for c in (sexpr.first(g, "start"), sexpr.first(g, "end"))]
    return ([(x1, y1), (x2, y2)], False) if g[0] == "fp_line" else ([(x1, y1), (x2, y1), (x2, y2), (x1, y2)], True)


def footprint_geometry(kicad_mod):
    """What the preview draws, from the .kicad_mod itself (KiCad frame, y down): courtyard and fab outlines, copper
    pads (rotation, rounded corners, polygon pads) and drills. Each item carries its points for the bounds."""
    t = sexpr.parse(kicad_mod)
    out = {"crtyd": [], "fab": [], "pads": [], "holes": []}
    for kind in ("fp_line", "fp_rect", "fp_poly"):
        for g in sexpr.find(t, kind):
            layer = (sexpr.first(g, "layer") or [None, None])[1]
            if layer in ("F.CrtYd", "F.Fab"):
                out["crtyd" if layer == "F.CrtYd" else "fab"].append(_svg_outline(g))
    for p in sexpr.find(t, "pad"):
        at, size = sexpr.first(p, "at"), sexpr.first(p, "size")
        x, y, rot = float(at[1]), float(at[2]), float(at[3]) if len(at) > 3 else 0.0
        w, h = float(size[1]), float(size[2])
        layers = (sexpr.first(p, "layers") or ["layers"])[1:]
        a = math.radians(-rot)                                         # KiCad: + is counter-clockwise on screen
        tf = lambda u, v: (x + u * math.cos(a) - v * math.sin(a), y + u * math.sin(a) + v * math.cos(a))
        d = sexpr.first(p, "drill")
        if d:
            vals = [float(v) for v in d[1:] if isinstance(v, str) and v != "oval"]
            dw, dh = vals[0], vals[1] if len(vals) > 1 else vals[0]
            out["holes"].append({"x": x, "y": y, "w": dw, "h": dh, "rot": rot,
                                 "pts": [tf(sx * dw / 2, sy * dh / 2) for sx in (-1, 1) for sy in (-1, 1)]})
        if p[2] == "np_thru_hole" or not any(l.endswith(".Cu") for l in layers):
            continue                                                   # holes only; paste windows aren't copper
        shape = p[3]
        pad = {"num": str(p[1]), "x": x, "y": y, "w": w, "h": h, "rot": rot}
        if shape == "custom":
            polys = [[tf(float(c[1]), float(c[2])) for c in sexpr.find(sexpr.first(g, "pts") or [], "xy")]
                     for g in (sexpr.first(p, "primitives") or [])[1:] if g[0] == "gr_poly"]
            pad["polys"] = [q for q in polys if len(q) > 2]
        r = {"circle": 0.5, "oval": 0.5}.get(shape, 0.0)
        rr = sexpr.first(p, "roundrect_rratio")
        if shape == "roundrect" and rr:
            r = float(rr[1])
        pad["r"] = r * min(w, h)
        pad["pts"] = [tf(sx * w / 2, sy * h / 2) for sx in (-1, 1) for sy in (-1, 1)] + [q for poly in pad.get("polys", []) for q in poly]
        out["pads"].append(pad)
    return out


def footprint_svg(rep, scale=40, kicad_mod=None):
    """Top view: copper pads (pin 1 highlighted), drills, exposed pad, fab body and courtyard. KiCad Y points down,
    as SVG's does. The view box fits what is drawn (through-hole connectors put pin 1, not the centre, at 0,0).
    kicad_mod: draw from the footprint itself (rotation, polygon pads, holes); else from the report's numbers."""
    f = lambda v: f"{float(v):.4f}".rstrip("0").rstrip(".")
    body, pts = [], []
    if kicad_mod:
        geo = footprint_geometry(kicad_mod)
        for cls in ("fab", "crtyd"):
            for line, closed in geo[cls]:
                pts += line
                d = " ".join(f"{f(x)},{f(y)}" for x, y in line)
                body.append(f'<{"polygon" if closed else "polyline"} class="{cls}" points="{d}"/>')
        pads = geo["pads"]
    else:
        bb = rep.get("courtyard_bbox")
        if not bb:
            cx, cy = float(rep["courtyard"][0]) / 2, float(rep["courtyard"][1]) / 2
            bb = (-cx, -cy, cx, cy)
        bb = [float(v) for v in bb]
        pts += [(bb[0], bb[1]), (bb[2], bb[3])]
        body.append(f'<rect class="crtyd" x="{f(bb[0])}" y="{f(bb[1])}" width="{f(bb[2] - bb[0])}" height="{f(bb[3] - bb[1])}"/>')
        if "EP" in rep:
            ew, eh = float(rep["EP"][0]), float(rep["EP"][1])
            body.append(f'<rect class="pad ep" x="{f(-ew / 2)}" y="{f(-eh / 2)}" width="{f(ew)}" height="{f(eh)}"/>')
        pads = []
        for p in rep["pads"]:
            x, y, w, h = (float(p[k]) for k in ("x", "y", "w", "h"))
            pads.append({"num": str(p["num"]), "x": x, "y": y, "w": w, "h": h, "rot": 0.0, "r": min(0.25, 0.25 * min(w, h)),
                         "pts": [(x - w / 2, y - h / 2), (x + w / 2, y + h / 2)]})
        geo = {"holes": []}
    p1 = next((p["num"] for p in pads if p["num"] in ("1", "A1")), None)
    for p in pads:
        pts += p["pts"]
        cls = "pad p1" if p["num"] == p1 else "pad"
        num = html.escape(p["num"])                                    # the UI inserts this SVG as HTML
        tip = f'<title>pad {num}: {p["w"]:.3f} x {p["h"]:.3f} mm @ ({p["x"]:.3f}, {p["y"]:.3f})</title>'
        if p.get("polys"):
            body.append(f'<g class="{cls}">' + "".join(
                '<polygon points="' + " ".join(f"{f(x)},{f(y)}" for x, y in q) + '"/>' for q in p["polys"]) + tip + "</g>")
            continue
        rot = f' transform="rotate({f(-p["rot"])} {f(p["x"])} {f(p["y"])})"' if p["rot"] else ""
        body.append(f'<rect class="{cls}" x="{f(p["x"] - p["w"] / 2)}" y="{f(p["y"] - p["h"] / 2)}" width="{f(p["w"])}" '
                    f'height="{f(p["h"])}" rx="{f(p["r"])}"{rot}>{tip}</rect>')
    for q in geo["holes"]:
        pts += q["pts"]
        rot = f' transform="rotate({f(-q["rot"])} {f(q["x"])} {f(q["y"])})"' if q["rot"] else ""
        body.append(f'<rect class="hole" x="{f(q["x"] - q["w"] / 2)}" y="{f(q["y"] - q["h"] / 2)}" width="{f(q["w"])}" '
                    f'height="{f(q["h"])}" rx="{f(min(q["w"], q["h"]) / 2)}"{rot}/>')
    m = 0.6
    x0, y0 = min(x for x, _ in pts) - m, min(y for _, y in pts) - m
    vw, vh = max(x for x, _ in pts) + m - x0, max(y for _, y in pts) + m - y0
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{f(x0)} {f(y0)} {f(vw)} {f(vh)}" width="{vw * scale:.0f}" '
            f'height="{vh * scale:.0f}" class="fp">' + "".join(body) + "</svg>")
