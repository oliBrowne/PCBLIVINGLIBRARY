"""Reproducible rebuilds: library.lock.json records what every part was built from, `ecadlib rebuild --check` proves it.

    ecadlib rebuild LIB_DIR --check [--json] [--keep DIR]     rebuild into a temporary folder, offline, and compare
    ecadlib rebuild LIB_DIR                                    rebuild in place, offline, from the lock's settings

The librarian writes <out>/library.lock.json at the end of every run (no timestamps: a re-run that changes nothing
leaves it byte-identical):

    {"schema_version": 1, "ecadlib": "0.3.0",
     "bom": {"file": "../bom.csv", "sha256": ...},
     "settings": {"sources": [...], "libs": [...], "cache": "../cache", "providers": null, "gen_step": true,
                  "rules_file": "", "rules_sha256": ...},
     "parts": {"<name>": {"key": <the BOM-line fingerprint>,
               "built_with": {"ecadlib": "0.3.0", "rules_sha256": ...},
               "inputs": [{"method": "GET", "url": ..., "sha256": <sha256 of the response text>}, ...] or null,
               "local": [{"file": <--lib file>, "sha256": ...}],
               "datasheet": {"url", "pdf_sha256", "model", "provider", "schema"} (parts read from a datasheet),
               "symbol": {"source", "file", "sha256", "norm_sha256"},
               "footprint": {"source", "file", "sha256", "norm_sha256", "kicad_mod": {"file", "sha256"}}}}}

`inputs` are the HTTP responses the part's sources fetched (the KiCad library file, the EasyEDA component, ...), recorded
as they arrive (Recorder); null for a part built before ecadlib recorded them. `norm_sha256` hashes the file with what is
not content blanked: the .PcbLib library header's DATE/TIME (the only wall-clock value ecadlib writes; GUIDs and
unique ids are derived from the content).

`rebuild --check` copies the folder to a temporary one, deletes every model file the librarian made (files a person
chose with `ecadlib resolve` stay), and runs the librarian again from the lock's BOM, libraries, sources and rules with
`--offline` (the HTTP cache only; a datasheet part is rebuilt only from the cached model answer, a model is never
called). It then reports, per part and file, identical / identical after normalising / DRIFT, plus inputs that
changed (the BOM, the rules, a cached response, a --lib file) and parts built by another ecadlib version. Exit 0: no
drift; 1: drift; 2: cannot rebuild (no lock, BOM missing)."""
import argparse, contextlib, hashlib, json, os, re, shutil, sys, tempfile
from pathlib import Path

from . import __version__

LOCK = "library.lock.json"
COPY_MARK = ".ecadlib-rebuild-copy"                  # in a --check --keep copy: a later --check --keep may replace it
SKIP_URL = re.compile(r"/repository/tree\?")        # the KiCad name index crawl: shared, cached in its own file


def sha(data):
    return hashlib.sha256(data if isinstance(data, bytes) else str(data).encode("utf-8")).hexdigest()


def file_sha(path):
    p = Path(path)
    return sha(p.read_bytes()) if p.is_file() else None


def norm_bytes(path):
    """A file's bytes with wall-clock values blanked (.PcbLib: the library header's DATE / TIME)."""
    p = Path(path)
    data = p.read_bytes()
    if p.suffix.lower() != ".pcblib":
        return data
    try:
        import olefile
        with olefile.OleFileIO(str(p)) as o:
            parts = []
            for s in sorted("/".join(e) for e in o.listdir(streams=True, storages=False)):
                b = o.openstream(s).read()
                if s == "Library/Data":
                    b = re.sub(rb"\|DATE=[^|]*", b"|DATE=", re.sub(rb"\|TIME=[^|]*", b"|TIME=", b, count=1), count=1)
                parts.append(s.encode() + b"\0" + sha(b).encode())
            return b"\n".join(parts)
    except Exception:                                   # not OLE: blank the raw bytes (same length kept)
        return re.sub(rb"\|(DATE|TIME)=[0-9/:]*", rb"|\1=", data)


def norm_sha(path):
    return sha(norm_bytes(path)) if Path(path).is_file() else None


def rules_hash(rules):
    from . import rules as RU
    return sha(json.dumps(RU.effective(rules), sort_keys=True, default=str))


# ------------------------------------------------------------------ recording what each part fetched
class Recorder:
    """Wraps an HTTP client's get/post (CachedHTTP or a test fake) and notes, per part, every 2xx response it got:
    method, URL (secrets redacted) and sha256 of the text. close() puts the client back as it was."""
    def __init__(self, http):
        self.http, self.cur, self.got, self._orig = http, None, {}, {}
        for m in ("get", "post"):
            f = getattr(http, m, None)
            if not callable(f):
                continue
            try:
                own = m in getattr(http, "__dict__", {})
                setattr(http, m, self._wrap(m.upper(), f))
                self._orig[m] = (f, own)
            except (AttributeError, TypeError):
                pass

    def _wrap(self, method, f):
        def call(url, *a, **kw):
            r = f(url, *a, **kw)
            try:
                self._note(method, url, a, kw, r)
            except Exception:                           # recording must never break a fetch
                pass
            return r
        return call

    def _note(self, method, url, a, kw, r):
        if self.cur is None or SKIP_URL.search(str(url)):
            return
        status = getattr(r, "status", getattr(r, "status_code", None))
        text = getattr(r, "text", None)
        if not isinstance(status, int) or not 200 <= status < 300 or not isinstance(text, str):
            return
        from .httpcache import redact
        e = {"method": method, "url": redact(str(url)), "sha256": sha(text)}
        body = a[0] if a else kw.get("body")
        if method == "POST" and body is not None:
            e["body_sha256"] = sha(body if isinstance(body, (bytes, str)) else json.dumps(body, sort_keys=True))
        lst = self.got.setdefault(self.cur, [])
        if e not in lst:
            lst.append(e)

    @contextlib.contextmanager
    def part(self, name):
        prev, self.cur = self.cur, name
        try:
            yield self
        finally:
            self.cur = prev

    def take(self, name):
        return sorted(self.got.pop(name, []), key=lambda e: (e["url"], e["method"]))

    def close(self):
        for m, (f, own) in self._orig.items():
            try:
                if own:
                    setattr(self.http, m, f)
                else:
                    delattr(self.http, m)
            except AttributeError:
                pass
        self._orig = {}


def stamp_state(st, inputs, rules):
    """What a freshly built part's librarian_state record keeps for the lock."""
    st["inputs"] = inputs
    st["built_with"] = {"ecadlib": __version__, "rules_sha256": rules_hash(rules)}
    return st


# ------------------------------------------------------------------ the lock file
def _rel(path, base):
    try:
        return os.path.relpath(Path(path).resolve(), Path(base).resolve()).replace(os.sep, "/")
    except ValueError:                                  # another drive (Windows)
        return str(Path(path).resolve())


def _abs(p, base):
    p = Path(p)
    return p if p.is_absolute() else (Path(base) / p).resolve()


def _local_inputs(source, libs):
    """--lib files a local:<Lib>:<Name> source came from."""
    m = re.match(r"local:([^:]+):(.+)$", str(source or ""))
    if not m:
        return []
    lib, name = m.groups()
    out = []
    for root in libs:
        for f in sorted(Path(root).rglob(f"{lib}.kicad_sym")) + sorted(Path(root).rglob(f"{lib}.pretty/{name}.kicad_mod")):
            out.append(f)
    return out[:2]


def _datasheet(out, url):
    from . import datasheet as D
    for f in sorted((Path(out) / "datasheet").glob("*.extraction.json")):
        try:
            s = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        pdf = s.get("pdf") or {}
        if url in (s.get("datasheet"), pdf.get("url")):
            ai = s.get("ai") or {}
            return {"url": url, "pdf_sha256": pdf.get("sha256"), "model": s.get("model") or ai.get("model"),
                    "provider": ai.get("provider") or "anthropic", "schema": D.schema_version(),
                    "extraction": _rel(f, out)}
    return {"url": url, "pdf_sha256": None, "model": None, "provider": None, "schema": D.schema_version(), "extraction": None}


def _half(out, rec):
    if not rec or not rec.get("file"):
        return None
    f = Path(out) / rec["file"]
    h = {"source": rec.get("source"), "file": rec["file"], "sha256": file_sha(f), "norm_sha256": norm_sha(f)}
    if rec.get("kicad_mod"):
        h["kicad_mod"] = {"file": rec["kicad_mod"], "sha256": file_sha(Path(out) / rec["kicad_mod"])}
    if rec.get("chosen"):
        h["chosen"] = True
    return h


def build_lock(out, items, state, bom, libs, sources, cache, providers, gen_step, rules):
    out = Path(out)
    parts = {}
    for it in sorted(items, key=lambda i: i["name"]):
        st = state.get(it["name"])
        if st is None:                                  # an Altium 365 item reused as it is: nothing was built
            continue
        p = {"key": st.get("key"), "built_with": st.get("built_with"),
             "inputs": st.get("inputs"), "symbol": _half(out, st.get("symbol")), "footprint": _half(out, st.get("footprint")),
             # released when built? (change control: only then do other bytes on disk say a lost ledger knew the part)
             "status": it.get("status")}
        local = []
        for k in ("symbol", "footprint"):
            for f in _local_inputs((st.get(k) or {}).get("source"), libs):
                e = {"file": _rel(f, out), "sha256": file_sha(f)}
                if e not in local:
                    local.append(e)
        if local:
            p["local"] = local
        urls = sorted({str((st.get(k) or {}).get("source"))[len("datasheet:"):] for k in ("symbol", "footprint")
                       if str((st.get(k) or {}).get("source") or "").startswith("datasheet:")})
        if urls:
            p["datasheet"] = _datasheet(out, urls[0])
        parts[it["name"]] = p
    rf = (rules or {}).get("_file") or ""
    return {"schema_version": 1, "ecadlib": __version__,
            "bom": {"file": _rel(bom, out), "sha256": file_sha(bom)},
            "settings": {"sources": list(sources), "libs": [_rel(l, out) for l in libs], "cache": _rel(cache, out),
                         "providers": providers if providers is None else list(providers) if not isinstance(providers, str)
                         else providers, "gen_step": bool(gen_step), "rules_file": _rel(rf, out) if rf else "",
                         "rules_sha256": rules_hash(rules)},
            "parts": parts}


def write_lock(out, *a, **kw):
    from .fsutil import write_text_atomic
    lock = build_lock(out, *a, **kw)
    text = json.dumps(lock, indent=1, sort_keys=False) + "\n"
    f = Path(out) / LOCK
    if not f.is_file() or f.read_text(encoding="utf-8") != text:
        write_text_atomic(f, text)
    return lock


def read_lock(lib_dir):
    from . import migrate as MG
    f = Path(lib_dir) / LOCK
    data = json.loads(f.read_text(encoding="utf-8"))
    MG.check_key("lock", data, str(f))
    return data


# ------------------------------------------------------------------ rebuild
class _OfflineReader:
    """Datasheet answers from the cache only: a rebuild never calls a model."""
    def __init__(self, reader):
        self._r = reader

    def __getattr__(self, k):
        return getattr(self._r, k)

    def read_now(self, job, *a, **kw):
        from .datasheet import DatasheetError
        raise DatasheetError("not in the model-answer cache (rebuild is offline and never calls a model)")

    def submit(self, job):
        return self.read_now(job)

    @property
    def deferred(self):
        return False


def _model_files(out, state):
    """Files the librarian made (to delete before a clean rebuild); chosen files are kept."""
    keep = {str((Path(out) / (st.get(k) or {}).get(f)).resolve()) for st in state.values() for k in ("symbol", "footprint")
            for f in ("file", "kicad_mod") if (st.get(k) or {}).get("chosen") and (st.get(k) or {}).get(f)}
    for f in sorted((Path(out) / "models").rglob("*")):
        if f.is_file() and str(f.resolve()) not in keep:
            yield f


def rebuild(lib_dir, out=None, log=print, clean=False):
    """Run the librarian again from the lock, offline, into `out` (default: lib_dir itself). clean: first delete the
    model files the librarian made (chosen ones stay), so every file is really rebuilt. -> (new lock, inputs drift)."""
    from . import librarian as L, rules as RU
    from .httpcache import CachedHTTP
    lib_dir = Path(lib_dir).resolve()
    lock = read_lock(lib_dir)
    s = lock["settings"]
    out = Path(out).resolve() if out else lib_dir
    bom = _abs(lock["bom"]["file"], lib_dir)
    if not bom.is_file():
        raise FileNotFoundError(f"the lock's BOM {bom} is missing: cannot rebuild")
    rules = RU.load(str(_abs(s["rules_file"], lib_dir))) if s.get("rules_file") else RU.load({})
    cache = _abs(s["cache"], lib_dir)
    libs = [str(_abs(l, lib_dir)) for l in s.get("libs") or []]
    http = CachedHTTP(cache, offline=True)
    drift = check_inputs(lock, lib_dir, rules, http)
    reader = None
    if any(p.get("datasheet") for p in lock["parts"].values()):
        from . import datasheet as D
        try:
            reader = _OfflineReader(D.Reader(out, cache, "ecadlib-rebuild-offline", rules, http, log=lambda *a: None))
        except Exception as e:                          # e.g. another provider whose key variable is not set here
            log(f"  rebuild: datasheet answers unavailable ({e})")
    if clean:
        state_f = out / "librarian_state.json"
        state = json.loads(state_f.read_text(encoding="utf-8")) if state_f.is_file() else {}
        for f in _model_files(out, state):
            f.unlink()
    L.run(str(bom), libs, str(out), sources=s["sources"], refresh=[], offline=True, cache=str(cache), http=http,
          providers=s.get("providers"), gen_step=s.get("gen_step", True), rules=rules, reader=reader,
          api_key="ecadlib-rebuild-offline" if reader else None, user="ecadlib rebuild", log=log)
    return read_lock(out), drift


def check_inputs(lock, lib_dir, rules, http):
    drift = []
    bom = _abs(lock["bom"]["file"], lib_dir)
    if file_sha(bom) != lock["bom"]["sha256"]:
        drift.append({"part": "", "what": "bom", "detail": f"{lock['bom']['file']} changed since the lock was written"})
    if rules_hash(rules) != lock["settings"]["rules_sha256"]:
        drift.append({"part": "", "what": "rules", "detail": "the library rules changed since the lock was written"})
    if lock.get("ecadlib") != __version__:
        drift.append({"part": "", "what": "ecadlib", "detail": f"lock written by ecadlib {lock.get('ecadlib')}, "
                                                              f"rebuilding with {__version__}", "info": True})
    for name, p in sorted(lock["parts"].items()):
        bw = (p.get("built_with") or {}).get("ecadlib")
        if bw and bw != __version__:
            drift.append({"part": name, "what": "ecadlib", "detail": f"built by ecadlib {bw}", "info": True})
        if p.get("inputs") is None and (p.get("symbol") or p.get("footprint")):
            drift.append({"part": name, "what": "inputs", "detail": "inputs not recorded (built before ecadlib "
                                                                   "recorded them)", "info": True})
        for e in p.get("inputs") or []:
            if e["method"] != "GET":
                continue
            r = http.get(e["url"])
            if not r.ok:
                drift.append({"part": name, "what": "input", "detail": f"{e['url']}: not in the HTTP cache"})
            elif sha(r.text) != e["sha256"]:
                drift.append({"part": name, "what": "input", "detail": f"{e['url']}: cached response changed"})
        for e in p.get("local") or []:
            if file_sha(_abs(e["file"], lib_dir)) != e["sha256"]:
                drift.append({"part": name, "what": "input", "detail": f"{e['file']}: --lib file changed or missing"})
    return drift


def compare(old, new):
    """Per part and file: identical | normalised (same after blanking dates) | DRIFT. -> rows."""
    rows = []
    for name in sorted(set(old["parts"]) | set(new["parts"])):
        a, b = old["parts"].get(name), new["parts"].get(name)
        if a is None or b is None:
            rows.append({"part": name, "file": "", "result": "DRIFT", "detail": "part only in the " + ("rebuild" if a is None else "lock")})
            continue
        for k in ("symbol", "footprint"):
            x, y = a.get(k), b.get(k)
            if not x and not y:
                continue
            if not x or not y:
                rows.append({"part": name, "file": (x or y)["file"], "result": "DRIFT",
                             "detail": f"{k} {'missing in the rebuild' if x else 'only in the rebuild'}"})
                continue
            pairs = [(x["file"], y["file"], x["sha256"], y["sha256"], x.get("norm_sha256"), y.get("norm_sha256"), k)]
            if x.get("kicad_mod") or y.get("kicad_mod"):
                xm, ym = x.get("kicad_mod") or {}, y.get("kicad_mod") or {}
                pairs.append((xm.get("file"), ym.get("file"), xm.get("sha256"), ym.get("sha256"), None, None, "kicad_mod"))
            for fa, fb, ha, hb, na, nb, what in pairs:
                if fa != fb:
                    rows.append({"part": name, "file": fa or fb, "result": "DRIFT", "detail": f"{what}: file {fa} -> {fb}"})
                elif ha and ha == hb:
                    rows.append({"part": name, "file": fa, "result": "identical", "detail": ""})
                elif na and na == nb:
                    rows.append({"part": name, "file": fa, "result": "normalised", "detail": "same content; only the library date differs"})
                else:
                    rows.append({"part": name, "file": fa, "result": "DRIFT", "detail": f"{what}: content differs"})
            if x.get("source") != y.get("source"):
                rows.append({"part": name, "file": x["file"], "result": "DRIFT", "detail": f"{k} source {x.get('source')} -> {y.get('source')}"})
    return rows


class KeepError(ValueError):
    """--check --keep DIR would put the rebuilt copy on, inside or around the library or one of the lock's inputs, or
    replace a folder an earlier --check --keep did not make (the CLI exits 2, no traceback)."""
    plain_error = True
    hint = "keep the rebuilt copy in a new folder outside the library and its inputs, e.g. --keep /tmp/rb"


def _lock_paths(lock, lib_dir):
    """Every file or folder the lock reads: the BOM, the HTTP cache, the --lib libraries and files, the rules file."""
    s = lock.get("settings") or {}
    paths = [lock["bom"]["file"], s.get("cache"), *(s.get("libs") or []), s.get("rules_file")]
    paths += [e.get("file") for p in (lock.get("parts") or {}).values() for e in p.get("local") or []]
    return [Path(_abs(p, lib_dir)).resolve() for p in paths if p]


def check(lib_dir, keep=None, log=print):
    """Rebuild into a temporary copy and compare with the lock. -> report dict (ok, rows, inputs)."""
    lib_dir = Path(lib_dir).resolve()
    old = read_lock(lib_dir)
    tmp = Path(keep).resolve() if keep else Path(tempfile.mkdtemp(prefix="ecadlib-rebuild-"))
    dst = tmp / lib_dir.name
    if dst.is_relative_to(lib_dir) or lib_dir.is_relative_to(dst):    # the copy is replaced first: never the library
        raise KeepError(f"--keep {keep}: the rebuilt copy would go to {dst}, which is the library {lib_dir} "
                        "or overlaps it; --check changes nothing in the library")
    for p in _lock_paths(old, lib_dir):                # nor what the library is built from
        if p.is_relative_to(dst) or dst.is_relative_to(p):
            raise KeepError(f"--keep {keep}: the rebuilt copy would go to {dst}, which holds or is inside {p}, "
                            "an input the library is built from; --check changes nothing")
    if dst.exists() and not (dst / COPY_MARK).is_file():   # only a copy an earlier --check --keep made is replaced
        raise KeepError(f"--keep {keep}: {dst} exists and is not a copy `rebuild --check` made; "
                        "remove it or choose another --keep")
    try:
        if dst.exists():
            shutil.rmtree(dst)
        # without the change-control ledger: the rebuild must reproduce the files, not be held back to them
        shutil.copytree(lib_dir, dst, ignore=shutil.ignore_patterns(".ecadlib_backup", "*.tmp", ".ecadlib",
                                                                    "library.ledger.jsonl", "library.usage.json"))
        (dst / COPY_MARK).write_text(f"a copy of {lib_dir} made by `ecadlib rebuild --check`; "
                                     "the next --check --keep here replaces it\n", encoding="utf-8")
        from . import migrate as MG            # the copy keeps no ledger: its stamp must not say it does
        st = dst / MG.STAMP
        if st.is_file():
            d = json.loads(st.read_text(encoding="utf-8"))
            if isinstance(d, dict) and "ledger" in (d.get("kinds") or {}):
                d["kinds"] = {k: v for k, v in d["kinds"].items() if k != "ledger"}
                st.write_text(json.dumps(d, indent=1) + "\n", encoding="utf-8")
        # the copy points at the same BOM, libraries and cache: write the lock's relative paths as absolute
        lk = json.loads((dst / LOCK).read_text(encoding="utf-8"))
        lk["bom"]["file"] = str(_abs(lk["bom"]["file"], lib_dir))
        s = lk["settings"]
        s["libs"] = [str(_abs(l, lib_dir)) for l in s.get("libs") or []]
        s["cache"] = str(_abs(s["cache"], lib_dir))
        if s.get("rules_file"):
            s["rules_file"] = str(_abs(s["rules_file"], lib_dir))
        for p in lk["parts"].values():
            for e in p.get("local") or []:
                e["file"] = str(_abs(e["file"], lib_dir))
        (dst / LOCK).write_text(json.dumps(lk), encoding="utf-8")
        new, inputs = rebuild(dst, dst, log=log, clean=True)
        rows = compare(old, new)
    finally:
        if not keep:
            shutil.rmtree(tmp, ignore_errors=True)
    bad = [r for r in rows if r["result"] == "DRIFT"] + [d for d in inputs if not d.get("info")]
    return {"lock": str(lib_dir / LOCK), "ok": not bad, "rows": rows, "inputs": inputs,
            "counts": {k: sum(r["result"] == k for r in rows) for k in ("identical", "normalised", "DRIFT")}}


def render(rep, styled=False, width=None, verbosity=0):
    """The --check result as text: what drifted (in plain words), then the outcome."""
    import io
    from . import messages as M, term as TM
    buf = io.StringIO()
    t = TM.Term(buf, styled=styled, width=width, verbosity=verbosity)
    lock = rep["lock"]
    try:
        rel = os.path.relpath(lock)
        lock = rel if not rel.startswith("..") else lock
    except ValueError:
        pass
    t.write(f"rebuild --check {lock}")
    rows = [[t.status("info" if d.get("info") else "held", "note" if d.get("info") else "input changed"),
             d["part"] or "(library)", M.clean(d["detail"])] for d in rep["inputs"]]
    rows += [[t.status("held" if r["result"] == "DRIFT" else "skipped", r["result"].lower()), r["part"],
              f"{r['file']} {M.clean(r['detail'])}".strip()] for r in rep["rows"] if r["result"] == "DRIFT" or (r["result"] != "identical" and t.verbose)]
    t.table(rows)
    c = rep["counts"]
    t.summary(("OK: the library rebuilds from its lock" if rep["ok"] else "DRIFT found: the library does not rebuild the same")
              + f" ({c['identical']} file(s) byte-identical, {c['normalised']} identical after normalising dates, {c['DRIFT']} drifted)",
              next=[] if rep["ok"] else [("ecadlib rebuild LIB --check --keep /tmp/rb", "keep the rebuilt copy to compare")])
    return buf.getvalue().strip("\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description="rebuild a librarian output folder from its library.lock.json, offline")
    ap.add_argument("lib", help="the librarian output folder (holds library.lock.json)")
    ap.add_argument("--check", action="store_true", help="rebuild into a temporary copy and report any drift; change nothing")
    ap.add_argument("--keep", metavar="DIR", help="--check: rebuild into DIR and keep it (to look at a drift)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    lib = Path(a.lib)
    if not (lib / LOCK).is_file():
        print(f"ecadlib rebuild: {lib / LOCK} not found (run `ecadlib librarian` once to write it)", file=sys.stderr)
        return 2
    quiet = lambda *x: None
    try:
        if a.check:
            from . import term as TM
            with TM.working("rebuilding offline into a temporary copy"):
                rep = check(lib, a.keep, log=quiet if a.json else quiet)
            print(json.dumps(rep, indent=1) if a.json else render(rep, TM.wants_style(sys.stdout), TM.term_width(sys.stdout),
                                                                  TM.VERBOSITY))
            return 0 if rep["ok"] else 1
        new, drift = rebuild(lib, log=quiet if a.json else print)
    except (FileNotFoundError, KeepError) as e:
        print(f"ecadlib rebuild: {e}" + (f"\n  hint: {e.hint}" if isinstance(e, KeepError) else ""), file=sys.stderr)
        return 2
    rep = {"lock": str(lib / LOCK), "parts": len(new["parts"]), "inputs": drift}
    print(json.dumps(rep, indent=1) if a.json else f"rebuilt {len(new['parts'])} part(s) from {lib / LOCK}"
          + "".join(f"\n  {'note ' if d.get('info') else 'INPUT'} {d['part'] or '(library)'}: {d['detail']}" for d in drift))
    return 0


if __name__ == "__main__":
    sys.exit(main())
