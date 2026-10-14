"""Shared actions behind every front end (web UI, MCP server, `ecadlib` CLI).

A Workspace is one folder on the user's machine:

    <root>/uploads/                       BOMs dropped into the UI
    <root>/footprints/ecadlib.pretty/     footprints the user approved (indexed like any other library)
    <root>/altium_models/PcbLib|SchLib/   Altium binaries: approved footprints, converted library footprints,
                                          generic R/C symbols (what an Altium 365 release uploads)
    <root>/reports/                       triage .md/.csv
    <root>/altium/                        DbLib export
    <root>/altium365/                     release plan + push reports
    <root>/librarian/<bom>/               librarian output: models, plan.json, review and release-check reports
    <root>/http_cache/                    provider responses

Each action returns plain JSON-able dicts so the UI and the MCP server can share it."""
import contextlib, hashlib, html, json, os, re
from pathlib import Path

import pandas as pd

from . import bom_triage as T, footprint_gen as F, providers as P, rules as RU, sexpr
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
        if bom_path.suffix.lower() not in (".xlsx", ".xlsm", ".xls", ".schdoc", ".prjpcb", ".bomdoc") and b"\0" in bom_path.read_bytes()[:4096]:
            raise ValueError(f"{bom_path.name} looks like a binary file, not a CSV; export the BOM as CSV or XLSX")
        try:
            lines, mapping = T.read_bom(bom_path, RU.bom_columns(self.rules()))
        except Exception as e:                        # pandas/csv/zip errors -> one message a person can act on
            raise ValueError(f"couldn't read {bom_path.name} as a BOM table ({type(e).__name__}: {e}). "
                             "Save it as CSV or XLSX with a header row.") from e
        if not lines or not {"designator", "mpn", "value"} & set(mapping):
            raise ValueError(f"no BOM lines found in {bom_path.name}: it needs a header row with columns such as "
                             "Designator, Quantity and Manufacturer Part Number, followed by at least one part")
        return lines, mapping

    def _key(self, bom_path):
        """Report name for a BOM: its stem for BOMs in uploads/, plus a short hash of the full path for any
        other location, so /projA/BOM.csv and /projB/BOM.csv don't share (and overwrite) one triage."""
        bom_path = Path(bom_path).resolve()
        if bom_path.parent == self.root / "uploads":
            return bom_path.stem
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
                "rules": self.rel(self.rules_file()) if self.rules_file().is_file() else None,
                "altium_models": {k: sorted(p.stem for p in (self.models / k).glob(f"*.{k}")) for k in ("PcbLib", "SchLib")},
                "boms": sorted(self.rel(p) for p in (self.root / "uploads").iterdir()
                               if p.is_file() and not p.is_symlink() and self._is_inside(p))}

    def _is_inside(self, p):
        try:
            self.inside(p)
            return True
        except ValueError:
            return False

    def save_upload(self, filename, data: bytes):
        if not data:
            raise ValueError(f"{filename} is empty")
        p = self.root / "uploads" / self.safe_name(filename)
        if p.is_symlink():                            # never write through a link to somewhere else
            p.unlink()
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o644)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        return self.rel(p)

    def triage(self, bom, progress=None, lock=None):
        """Triage a BOM (workspace-relative or absolute path). Writes reports/triage_<stem>.{md,csv}.
        progress(done, total, label) is called before each line, for UIs that show a progress bar.
        lock, if given, is held only while the reports are written: the lookups are network-bound and
        must not block other workspace actions."""
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
        stem = self.root / "reports" / f"triage_{self._key(bom_path)}"
        with lock or contextlib.nullcontext():
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
        return {"name": spec["name"], "report": rep, "kicad_mod": text, "svg": footprint_svg(rep),
                "exists": (self.gen_dir / f"{name}.kicad_mod").exists()}

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
        p.write_text(res["kicad_mod"])
        (self.gen_dir / f"{name}.json").write_text(json.dumps(spec_obj, indent=2))  # keep the source dimensions
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
                altium_lib.write_pcblib(out, [altium_lib.footprint_from_kicad(Path(hit["path"]).read_text())], layers)
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
        argv += ["--rules", str(self.rules_file())] if self.rules_file().is_file() else []
        altium_export.main(argv)
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
            items = json.loads(lib_plan.read_text())
            for it in items:                          # librarian paths are relative to its output folder
                for k in ("symbol", "footprint"):
                    f = it[k].get("file")
                    if f and not Path(f).is_absolute():
                        it[k]["file"] = str(lib_plan.parent / f)
            src, out = "librarian", lib_plan
        else:
            items = A.plan(lines, self._triage_frame(bom_path), self.models, rules=self.rules())
            out = self.root / "altium365" / f"plan_{self._key(bom_path)}.json"
            out.write_text(json.dumps(items, indent=2))
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
        provs = ",".join(p for p in ("jlcpcb_search", "easyeda") if p in self.provider_names)
        items = L.run(str(bom_path), self.libs + [str(self.root / "footprints")], out,
                      list(sources) if sources else None, refresh, False, list(allow), self.offline,
                      self.root / "http_cache", 0.5, provs, log=note, rules=self.rules())
        return self._librarian_summary(bom_path, items, log)

    def release_check(self, bom, allow=(), unallow=(), user=None):
        """Re-run the release check on the librarian's plan, optionally releasing named parts despite their
        failures (allow) or taking that back (unallow). user, when given, is recorded as the person who gave the
        allow (approvals' no_self_approval uses it). Never touches the network."""
        from . import release_check as RC
        bom_path = self._bom_path(bom)
        out = self._librarian_dir(bom_path)
        plan_f = out / "plan.json"
        if not plan_f.exists():
            raise ValueError("no librarian plan for this BOM yet: run the librarian (Fill missing parts) first")
        items = json.loads(plan_f.read_text())
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
        plan_f.write_text(json.dumps(items, indent=2))
        RC.write_report(rows, out / "release_check", plan_f)
        from . import librarian as L
        L.write_review(L.review_rows(items), out, str(bom_path))
        return self._librarian_summary(bom_path, items, [])

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
        return bom_path, plan_f, json.loads(plan_f.read_text())

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
        out = self.release_check(bom)                 # the whole plan again: reports, statuses, approval snapshot
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

    def approve_part(self, bom, part, decision, user, role=None, comment=""):
        """Record an approve / reject for one part as `user`. user is required: there is no login, so the caller
        says who it is (never taken from this machine's git identity). Every decision, refused ones included, is
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
                 "comment": comment, "via": "ecadlib ui/mcp"}
        try:
            rec = AP.decide(it, decision, user, role, comment, base, policy)
        except AP.Refused as e:
            entry["result"] = f"refused: {e}"
            AP.audit(base, entry)
            raise ValueError(str(e)) from e
        status = AP.refresh_status(it, policy, base)
        entry.update(role=rec["role"], hash=rec["hash"], result="recorded", status=status)
        AP.audit(base, entry)
        plan_f.write_text(json.dumps(items, indent=2))
        L.write_review(L.review_rows(items), base, str(bom_path))
        out = self._librarian_summary(bom_path, items, [])
        out["decision"] = {"part": part, "decision": decision, "who": user, "role": rec["role"], "status": status,
                           "enforced": bool(policy)}
        return out

    # ------------------------------------------------------------------ duplicates (ecadlib.dedupe)
    def find_duplicates(self, bom, merge=False):
        """Duplicate parts (same MPN), footprints (same geometry) and symbols (same pinout) across the librarian plan
        and the workspace's altium_models/ and footprints/. merge=True points duplicates at their keeper in the plan
        (library files are never changed; plan.json.pre-dedupe keeps the old plan) and re-runs the release check."""
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
            out["summary"] = self.release_check(bom)
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
        rules = UK.load_rules(None, start=str(self.root))
        rep = UK.run(str(src), checks or None, base / "state", base / "upkeep", manifest and str(manifest),
                     str(bom_path) if src != bom_path else None, workspace or None, bool(apply), rules,
                     UK.Ctx(cache=self.root / "http_cache", offline=self.offline))
        rel = lambda p: self.rel(p) if self._is_inside(p) else str(p)
        return {"bom": self._bom_label(bom_path), "input": rel(src), "new_changes": rep["new_changes"],
                "exit_code": rep["exit_code"],
                "checks": [{k: s[k] for k in ("check", "status", "reason", "changes", "resolved", "notes")} for s in rep["checks"]],
                "applied": [rel(p) for p in rep["applied_paths"]],
                "reports": [self.rel(base / "upkeep.md"), self.rel(base / "upkeep.json")]}
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
        dirs = [self.inside(m) for m in (models or [])]
        own = self.root / "sim_models"
        if own.is_dir():
            dirs.insert(0, own)
        rep = SM.run(plan_f, [d for d in dirs if d.is_dir()], attach=bool(attach),
                     http=CachedHTTP(self.root / "http_cache", offline=self.offline, timeout=60), log=lambda *a: None)
        return {"attach": rep["attach"], "parts": rep["parts"], "hosts": rep["hosts"], "altium365": rep["altium365"],
                "errors": rep.get("errors", []), "reports": [self.rel(plan_f.parent / "sim_models.md"), self.rel(plan_f.parent / "sim_models.json")]}

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
        out.write_text(json.dumps(results, indent=2))
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
                  folders=folders or None, log=lambda *_: None)
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

    # ------------------------------------------------------------------ library rules (ecadlib.rules / rules_infer)
    def rules_file(self):
        """<workspace>/ecadlib.toml (or ecadlib_rules.json when only that exists)."""
        return next((self.root / n for n in RU.FILES if (self.root / n).is_file()), self.root / RU.FILES[0])

    def rules(self):
        """The workspace's rules merged over the defaults (no file: ecadlib's own behaviour)."""
        f = self.rules_file()
        return RU.load(f) if f.is_file() else RU.load({})

    def rules_info(self):
        f = self.rules_file()
        return {"file": self.rel(f), "exists": f.is_file(), "text": f.read_text(encoding="utf-8") if f.is_file() else "",
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

    def example_specs(self):
        d = Path(__file__).resolve().parent / "package_specs"      # shipped as package data
        return {p.stem: json.loads(p.read_text()) for p in sorted(d.glob("*.json"))} if d.exists() else {}


def _nexar_token():
    tok = os.environ.get("NEXAR_TOKEN")
    if not tok:
        raise ValueError("set NEXAR_TOKEN before starting ecadlib (a Nexar portal token with the "
                         "design.domain, user.access and openid scopes)")
    return tok


def footprint_svg(rep, scale=40):
    """Top view: copper pads (pin 1 highlighted), exposed pad and courtyard. KiCad Y points down, as SVG's does."""
    cx, cy = float(rep["courtyard"][0]) / 2, float(rep["courtyard"][1]) / 2
    m = 0.6
    vb = f"{-cx - m:.3f} {-cy - m:.3f} {2 * (cx + m):.3f} {2 * (cy + m):.3f}"
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb}" width="{2 * (cx + m) * scale:.0f}" '
           f'height="{2 * (cy + m) * scale:.0f}" class="fp">',
           f'<rect class="crtyd" x="{-cx}" y="{-cy}" width="{2 * cx}" height="{2 * cy}"/>']
    if "EP" in rep:
        ew, eh = float(rep["EP"][0]), float(rep["EP"][1])
        out.append(f'<rect class="pad ep" x="{-ew / 2}" y="{-eh / 2}" width="{ew}" height="{eh}"/>')
    for p in rep["pads"]:
        r = min(0.25, 0.25 * min(p["w"], p["h"]))
        cls = "pad p1" if p["num"] == "1" else "pad"
        x, y, w, h = (float(p[k]) for k in ("x", "y", "w", "h"))      # numbers only, and text escaped: the
        num = html.escape(str(p["num"]))                              # UI inserts this SVG as HTML
        out.append(f'<rect class="{cls}" x="{x - w / 2:.4f}" y="{y - h / 2:.4f}" '
                   f'width="{w:.4f}" height="{h:.4f}" rx="{r:.3f}"><title>pad {num}: '
                   f'{w:.3f} x {h:.3f} mm @ ({x:.3f}, {y:.3f})</title></rect>')
    out.append("</svg>")
    return "".join(out)
