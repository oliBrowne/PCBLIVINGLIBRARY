"""One command from BOM to library: triage the BOM, fill every missing symbol and footprint, write the Altium
models and the altium365 plan, run the release check, and stop with a review list.

    python3 -m ecadlib.librarian examples/sample_bom.csv --lib examples/kicad_lib --lib out/footprints -o out/librarian \
        [--sources kicad,easyeda,standard,datasheet] [--allow PART] [--box] [--refresh [PART ...]] [--refresh-index] [--offline]

Per plan item (one per unique part), parts the triage found in a --lib library are converted from there; for
anything still missing, the sources are tried in --sources order, each filling only what is still missing:
  kicad      the official KiCad libraries on gitlab.com. A name index of kicad-symbols (KiCad 9 <Lib>.kicad_symdir/
             <Name>.kicad_sym, so derived symbols/aliases are names too) and kicad-footprints is built once from the
             GitLab tree API and cached (<cache>/kicad_library_index.json, 30 days; a build stopped by a timeout or
             HTTP 429 resumes from the cached pages after a pause of 15 min / 1 h, in which runs skip gitlab.com). A cache
             with no index starts from the copy shipped with ecadlib (assets/kicad_library_index.json.gz), not a build.
             Symbol by MPN: exact name, a name with KiCad's `x` wildcards (STM32F103C8Tx), or the MPN minus an
             ordering suffix (TPS62162DSG for TPS62162DSGR); generic connectors by value (Conn_01x04). Footprint: the BOM footprint name, the symbol's
             Footprint property, a footprint named after the MPN, or the shortest name starting with the BOM footprint.
             R/C/L/LED use Device:R/C/C_Polarized/L/LED and <Family>_SMD:<X>_<imperial>_<metric>Metric by package.
  easyeda    LCSC number (BOM, or found by MPN in triage) -> EasyEDA component JSON -> easyeda_convert -> KiCad text;
             the footprint's own STEP model is downloaded and placed on it (easyeda_3d), which gives the body height
  standard   no network, no API: chip passives and standard packages (SOT-23, SOIC-8, QFN-32 5x5 ...) built from the
             checked package table, symbols where the pinout is standard (ecadlib/stdparts.py); always after kicad
  datasheet  datasheet URL (BOM column, KiCad symbol, EasyEDA/LCSC link, LCSC pattern) + ANTHROPIC_API_KEY ->
             ecadlib.datasheet; the result is marked `verify` so release_check holds it until a person has looked (or
             --allow). The same goes for an EasyEDA conversion, a pinout from a family table and a KiCad symbol or
             footprint picked by a near name (ordering suffix, MPN in the name, shortest prefix). --datasheet-batch sends every part that reaches this source as one Message Batch (half price)
             after the other sources ran, then builds them; results are cached, so re-runs are free
Footprints without a KiCad STEP model get a package-shaped STEP from step_gen embedded (--box: extruded box).
Everything is converted through kicad_import into <out>/models/{SchLib,PcbLib} (plus the .kicad_mod in
<out>/models/KiCad for footprint_check). Outputs: <out>/plan.json (each item records `source`, `librarian.tried`
and the release-check result), <out>/review.{md,csv}, <out>/release_check.{md,csv}, <out>/triage.{md,csv}.
Resumable: <out>/librarian_state.json remembers finished parts (saved as the run goes, so a stopped or killed run
keeps them too); a re-run skips them unless --refresh (all, or the named parts) or their BOM line changed. --allow
PART is recorded in plan.json and survives re-runs, and so does a symbol or footprint chosen with `ecadlib resolve` (held parts: see ecadlib/resolve.py)."""
import argparse, csv, json, os, re, sys, time
from pathlib import Path

import pandas as pd

from . import __version__
from . import (altium365 as A365, altium_export as AX, altium_lib as AL, bom_triage as T, datasheet as D, easyeda_3d as E3, easyeda_convert as EC,
               footprint_gen as F, fp_share as FS, kicad_import as K, providers as P, release_check as RC, rules as RU, sexpr)
from .fsutil import write_text_atomic
from .httpcache import CachedHTTP
from . import crosscheck as CC, messages as M, sheetsafe as SS
from . import fp_tidy as FT, symbol_layout as SL
from .kicad_index import KiCadIndex, METRIC_TO_IMPERIAL, norm_mpn, package_of, two_readings

TREE = "https://gitlab.com/api/v4/projects/kicad%2Flibraries%2F{repo}/repository/tree?ref=master&recursive=true&per_page=100&page={page}"
SOURCES = ("kicad", "easyeda", "standard", "datasheet")
IMP2MET = {v: k for k, v in METRIC_TO_IMPERIAL.items()}
PASSIVE = {"R": ("Device:R", "Resistor_SMD:R_{}_{}Metric"), "C": ("Device:C", "Capacitor_SMD:C_{}_{}Metric"),
           "CP": ("Device:C_Polarized", None), "L": ("Device:L", "Inductor_SMD:L_{}_{}Metric"), "LED": ("Device:LED", "LED_SMD:LED_{}_{}Metric")}
SPECS = Path(F.__file__).with_name("package_specs")


def stacked_pins(sym):
    """'3, 7 (VDD)' for each group of pins of one part that end on the same spot (KiCad stacks VDD / GND pins that way)."""
    parts = sym.get("prim_parts") or [1] * len(sym["prims"])
    at = {}
    for pr, u in zip(sym["prims"], parts):
        if pr[0] == "pin":
            dx, dy = {"left": (-1, 0), "right": (1, 0), "top": (0, 1), "bottom": (0, -1)}[pr[2]]
            at.setdefault((u, pr[3] + dx * pr[5], pr[4] + dy * pr[5]), []).append(pr[1])
    return [", ".join(dict.fromkeys(str(p["num"]) for p in ps)) + f" ({ps[0].get('name', '')})" for ps in at.values()
            if len({p["num"] for p in ps}) > 1]


# ---------------------------------------------------------------- official KiCad library name index

def drop_joined_tab(res, table_spec, st):
    """A read's tab numbered on its own but on one lead's net (stdparts.tab_joins), built from a table land that numbers
    the tab with that lead (C3D08060G: tab 3 CATHODE, TO-263-2_TabPin1 numbers it 1): the symbol's pin for the tab's
    own number has no pad left, so it goes, and the review says so. -> res (a copy when changed)."""
    from . import stdparts as SP
    pins = (res.get("symbol") or {}).get("pins") or []
    lead = SP.tab_joins(res["spec"], [(q["num"], q["name"]) for q in pins])
    tab = str((res["spec"].get("tab") or {}).get("number"))
    pads = {str(q.get("number")) for q in (table_spec.get("pad_table") or {}).get("pads") or []}
    if not lead or tab in pads or lead not in pads:
        return res
    st["review"].append(f"tab: the datasheet numbers the tab {tab}, on pin {lead}'s net; the land numbers it {lead}, so "
                        f"the symbol has no pin {tab} (check the pin table)")
    return dict(res, symbol=dict(res["symbol"], pins=[q for q in pins if str(q["num"]) != tab]))

class KiCadLibrary:
    """Name index of the official KiCad libraries (see the module docstring). Building it reads ~400 GitLab tree pages;
    each page is kept in the HTTP cache, so a build that stops (timeout, HTTP 429) resumes where it stopped. After a
    failed build, <index>.retry.json holds the next attempt back for RETRY_AFTER s (RETRY_AFTER_429 after a 429) so
    every run in between goes straight to the other sources instead of waiting on gitlab.com again."""
    PAGE_TIMEOUT = 20                          # s per tree page (the client's own timeout is 60 s)
    RETRY_AFTER, RETRY_AFTER_429 = 15 * 60, 60 * 60
    # A copy of the index made when this ecadlib was released (scripts/build_kicad_index.py). With no index of its own,
    # a run starts from it instead of reading ~400 tree pages first (about 4 minutes, and gitlab.com may answer 429):
    # it becomes the cached index, refreshed after max_age like any other. A failed build falls back on it too.
    # ECADLIB_KICAD_SNAPSHOT=0 turns it off (always build from gitlab.com; the test suite's fake gitlab needs that).
    SNAPSHOT = Path(__file__).parent / "assets" / "kicad_library_index.json.gz"

    def _snapshot(self):
        return None if os.environ.get("ECADLIB_KICAD_SNAPSHOT", "").strip().lower() in ("0", "off", "no") else self.SNAPSHOT

    def __init__(self, http, cache_file, refresh=False, max_age=30 * 86400, now=time.time):
        self.http, self.error, self.symbols, self.footprints = http, None, {}, {}
        f = Path(cache_file)
        retry = f.with_name(f.name + ".retry.json")
        wait = self._backoff(retry, now()) if not refresh else None
        if f.exists() and not refresh and (getattr(http, "offline", False) or now() - f.stat().st_mtime < max_age) \
                and self._load(f):
            pass
        elif not f.exists() and not refresh and self._seed(f):
            pass
        elif wait:
            self.error = wait
            if not (f.exists() and self._load(f)):          # a stale index beats none, the shipped one beats nothing
                self._load_snapshot()
        else:
            try:
                self.symbols = self._crawl("kicad-symbols", ".kicad_symdir", ".kicad_sym")
                self.footprints = self._crawl("kicad-footprints", ".pretty", ".kicad_mod")
                f.parent.mkdir(parents=True, exist_ok=True)
                from .fsutil import write_text_atomic   # a crash mid-write (or a second run reading it) never sees half
                write_text_atomic(f, json.dumps({"built": time.strftime("%Y-%m-%d"), "symbols": self.symbols,
                                                 "footprints": self.footprints}))
                retry.unlink(missing_ok=True)
            except IOError as e:
                self.error = str(e)
                self.symbols, self.footprints = {}, {}
                if not getattr(http, "offline", False):
                    try:
                        retry.parent.mkdir(parents=True, exist_ok=True)
                        hold = self.RETRY_AFTER_429 if "HTTP 429" in self.error else self.RETRY_AFTER
                        retry.write_text(json.dumps({"after": now() + hold, "error": self.error}), encoding="utf-8")
                    except OSError:
                        pass
                if not (f.exists() and self._load(f)):      # a stale index beats none
                    self._load_snapshot()
        self._build_norm()

    def _build_norm(self):
        self._norm, self._wild, self._fpn = {}, [], []
        for name, libs in self.symbols.items():
            self._norm.setdefault(norm_mpn(name), []).extend(f"{l}:{name}" for l in libs)
            if re.search(r"(?<![0-9])x|x(?![0-9])", name) and re.search(r"[A-Z0-9]x", name) and not any(l.startswith("Connector") for l in libs):
                # a '.' between digits (or x wildcards) is kept by norm_mpn on the MPN side: 'LM1117-x.x' -> optional,
                # and two x slots take a decimal point between them: 1.5KExxA is 1.5KE6.8A, SMAJxxA SMAJ5.0A (R-FX10-3)
                flat = re.sub(r"(?<=[0-9x])\.(?=[0-9x])", "\x00", name)
                flat = re.sub(r"[^A-Za-z0-9\x00]", "", flat)
                rx = "".join(r"\.?[A-Z0-9]" if c == "x" and i and flat[i - 1] == "x" else
                             "[A-Z0-9]" if c == "x" else r"\.?" if c == "\x00" else re.escape(c.upper())
                             for i, c in enumerate(flat))
                # an x may also stand for nothing: Microchip's MCP6L01x-xOT is MCP6L01-E/OT and MCP6L01T-E/OT (the
                # x a blank or T), so MCP6L01R-E/OT fits MCP6L01Rx-xOT with that x blank. Then more than one name can
                # fit; find_symbol takes the one with the most literal characters (R-SY-1). Only where an ordering
                # letter goes: next to a '-' or '/' of the MPN (marked '|', see _marked) or at its end. A blank in
                # the middle of the name makes another part: VTL5C2 (single Vactrol) is not VTL5Cx2, the dual (R-FX20-1)
                blank = r"(?:[A-Z0-9]|(?=\|)|(?<=\|))"
                bx = r"\|?".join(r"\.?" + blank if c == "x" and i and flat[i - 1] == "x" else
                                 blank if c == "x" else r"\.?" if c == "\x00" else re.escape(c.upper())
                                 for i, c in enumerate(flat)) + r"\|?"
                lit = sum(c not in "x\x00" for c in flat)
                self._wild.append((re.compile(rx), [f"{l}:{name}" for l in libs], lit, re.compile(bx)))
        self._fpn = [(norm_mpn(n), f"{libs[0]}:{n}") for n, libs in self.footprints.items()]

    def _load_snapshot(self):
        """The index shipped with ecadlib -> True (False when this install has none or it does not read)."""
        import gzip
        try:
            snap = self._snapshot()
            if not snap:
                return False
            d = json.loads(gzip.decompress(Path(snap).read_bytes()).decode("utf-8"))
            sy, fp = d["symbols"], d["footprints"]
        except (OSError, ValueError, KeyError, TypeError, EOFError):
            return False
        if not isinstance(sy, dict) or not isinstance(fp, dict):
            return False
        self.symbols, self.footprints, self.built = sy, fp, d.get("built", "")
        return True

    def _seed(self, f):
        """No index in this cache yet: start from the shipped one and keep it as this cache's index (so it is
        refreshed from gitlab.com after max_age, not on the first run). -> True when it was used."""
        if not self._load_snapshot():
            return False
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            from .fsutil import write_text_atomic
            write_text_atomic(f, json.dumps({"built": self.built, "symbols": self.symbols, "footprints": self.footprints}))
        except OSError:
            pass                               # a read-only cache still uses it for this run
        return True

    def _load(self, f):
        """The cached index -> True; one that does not read (cut short by a crash) -> False, so it is built again
        instead of stopping every run until it is 30 days old."""
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            sy, fp = d["symbols"], d["footprints"]
        except (OSError, ValueError, KeyError, TypeError):
            return False
        if not isinstance(sy, dict) or not isinstance(fp, dict):
            return False
        self.symbols, self.footprints = sy, fp
        return True

    @staticmethod
    def _backoff(retry, t):
        """Why the index is not fetched now (a recent failed build), or None."""
        try:
            d = json.loads(retry.read_text(encoding="utf-8"))
            after = float(d["after"])
        except (OSError, ValueError, KeyError, TypeError):
            return None
        if t >= after:
            return None
        return (f"{d.get('error') or 'the last build failed'}; not retried until "
                f"{time.strftime('%H:%M', time.localtime(after))} (--refresh-index retries now)")

    def _get(self, url):
        try:
            return self.http.get(url, timeout=self.PAGE_TIMEOUT)
        except TypeError:                      # a client without a per-request timeout
            return self.http.get(url)

    def _crawl(self, repo, dsuf, fsuf):
        out = {}
        for page in range(1, 2000):
            r = self._get(TREE.format(repo=repo, page=page))
            if not r.ok:
                raise IOError(f"{repo} tree page {page}: {r.error or f'HTTP {r.status}'}")
            items = r.json()
            for it in items:
                lib, _, fn = it.get("path", "").partition("/")
                if it.get("type") == "blob" and lib.endswith(dsuf) and fn.endswith(fsuf) and "/" not in fn:
                    out.setdefault(fn[:-len(fsuf)], []).append(lib[:-len(dsuf)])
            if len(items) < 100:
                return out
        raise IOError(f"{repo}: tree listing did not end")

    @staticmethod
    def _pick(refs):
        refs = sorted(dict.fromkeys(refs))
        return refs[0], (f" (also in {', '.join(r.split(':')[0] for r in refs[1:])})" if len(refs) > 1 else "")

    def find_symbol(self, mpn):
        """MPN -> ('Lib:Name', how) or (None, None)."""
        nm = norm_mpn(mpn)
        if len(nm) < 3:
            return None, None
        if nm in self._norm:
            ref, more = self._pick(self._norm[nm])
            return ref, "exact name" + more
        fits = self._wild_fits(nm, mpn)
        if fits:
            best = max(lit for lit, _ in fits)
            ref, more = self._pick([r for lit, refs in fits if lit == best for r in refs])
            return ref, "name with KiCad x wildcards" + more
        raw = [i for i, c in enumerate(str(mpn)) if c.isalnum() or     # positions of the normalised characters (a
               (c == "." and str(mpn)[i - 1:i].isdigit() and str(mpn)[i + 1:i + 2].isdigit())]   # kept decimal point)
        for k in range(len(nm) - 1, max(5, int(len(nm) * 0.6)) - 1, -1):
            sep = raw[k] > raw[k - 1] + 1                             # a '-', '/', '(' ... right before the suffix
            if nm[:k] in self._norm and (len(nm) - k <= 2 or (sep and len(nm) - k <= 6)):
                ref, more = self._pick(self._norm[nm[:k]])
                return ref, f"name + ordering suffix '{str(mpn)[raw[k]:]}' (check the variant)" + more
        return None, None

    @staticmethod
    def _marked(mpn, nm):
        """nm with '|' where the MPN had a '-' or '/' between two of its characters, and at its end: the places an
        x of a KiCad name may stand for nothing (an ordering letter left out). Without a raw MPN (or when the
        characters do not line up) only the end is marked."""
        s = str(mpn or "")
        raw = [i for i, c in enumerate(s) if c.isalnum() or (c == "." and s[i - 1:i].isdigit() and s[i + 1:i + 2].isdigit())]
        if mpn is None or len(raw) != len(nm):
            return nm + "|"
        return "".join(("|" if k and set(s[raw[k - 1] + 1:raw[k]]) & set("-/") else "") + c
                       for k, c in enumerate(nm)) + "|"

    def _wild_fits(self, nm, mpn=None):
        sp = self._marked(mpn, nm)
        return [(w[2] if len(w) > 2 else 0, w[1]) for w in self._wild
                if w[0].fullmatch(nm) or (len(w) > 3 and w[3].fullmatch(sp))]

    def wild_alternates(self, mpn, ref):
        """The other KiCad x-wildcard names (one ref each) the MPN fits besides ref's: an x may be a blank, so
        MCP6L01R-E/OT fits MCP6L01Rx-xOT and MCP6L01x-xOT (x = R), whose supplies are the other way round. The
        librarian compares their pinouts with ref's and holds the part when one differs (R-SY-1)."""
        name = str(ref).split(":")[-1]
        out = {}
        for _, refs in self._wild_fits(norm_mpn(mpn), mpn):
            n = refs[0].split(":")[-1]
            if n != name:
                out.setdefault(n, sorted(refs)[0])
        return sorted(out.values())

    def generic_connector(self, value):
        for n in (value, f"{value}_Odd_Even"):
            libs = [l for l in self.symbols.get(n, []) if l == "Connector_Generic"]
            if libs and re.fullmatch(r"Conn_\d\dx\d\d.*", n):
                return f"{libs[0]}:{n}", f"generic connector symbol by value {value}"
        return None, None

    def footprint(self, ref):
        if not ref:
            return None
        lib, _, name = ref.rpartition(":")
        libs = self.footprints.get(name, [])
        return f"{lib}:{name}" if lib in libs else f"{libs[0]}:{name}" if libs and not lib else None

    def footprint_by_mpn(self, mpn):
        nm = norm_mpn(re.sub(r"\(.*", "", str(mpn)))
        hits = sorted((len(n), ref) for n, ref in self._fpn if len(nm) >= 6 and nm in n)
        return hits[0][1] if hits else None

    def footprint_by_prefix(self, name):
        hits = sorted((len(n), f"{libs[0]}:{n}") for n, libs in self.footprints.items() if name and n.startswith(name + "_"))
        return hits[0][1] if hits else None


# ---------------------------------------------------------------- helpers
SHARED_KINDS = ("R", "C", "CP", "L")       # parts whose Library Ref is the generic symbol (altium_export.GENERIC_SYMBOL)
PART_PARAMS = {"mpn", "lcsc", "manufacturer", "manufacturer part number", "datasheet", "footprint"}


PASSIVE_FOLDERS = ("Resistors", "Capacitors", "Inductors", "Crystals")
ACTIVE_PIN = re.compile(r"(?i)^(VCC|VDD|V\+|OUT|OUTPUT|CLK|EN|OE|E/D|ST|STBY|TRI)\b")   # an oscillator, not a crystal


def shared_symbol(sym):
    """A symbol every R / C / L of the library shares: no footprint model (a 0402 link would win over each DbLib row's
    [Footprint Ref], so a 0805 part compiled with the 0402 footprint) and none of one part's parameters."""
    return dict(sym, footprint=None, params=[(k, v) for k, v in sym.get("params") or [] if str(k).lower() not in PART_PARAMS])


def _where(e):
    """' at datasheet.py:742 in to_spec' for a bug (TypeError, KeyError ...) so a report names the line; nothing for
    the errors a source raises on purpose (no datasheet, HTTP, held)."""
    if not isinstance(e, (TypeError, KeyError, AttributeError, IndexError, ZeroDivisionError, UnboundLocalError)):
        return ""
    import traceback
    fr = [f for f in traceback.extract_tb(e.__traceback__) if "ecadlib" in f.filename.replace("\\", "/")]
    return f" at {Path(fr[-1].filename).name}:{fr[-1].lineno} in {fr[-1].name}" if fr else ""


def is_passive_part(item, ln=None):
    """R / C / L / ferrite bead / crystal: every pin is Passive, whatever the source's symbol says (an EasyEDA pin
    typed 1 = Input made Altium warn 'Nets containing floating input pins' on every resistor)."""
    folder = str(item.get("folder") or "").rstrip("/").rsplit("/", 1)[-1]
    ref = re.match(r"[A-Z]+", str((ln or {}).get("designator") or "").upper())
    return folder in PASSIVE_FOLDERS or bool(ref and ref.group(0) in ("R", "C", "L", "FB", "Y"))


def quiet_two_pin(sym):
    """A two-pin passive (R / C / L / ferrite bead / crystal) shows no pin names or numbers, whatever the source drew
    (Oliver's rule, as the standard symbols): a crystal's "1"/"2" or "OSC1"/"OSC2" ran over each other in its narrow body."""
    pins = [pr[1] for pr in sym.get("prims") or [] if pr[0] == "pin" and not pr[1].get("hidden")]
    if len(pins) != 2 or len({p["num"] for p in pins}) != 2:
        return sym
    prims = [(pr[0], dict(pr[1], show_name=False, show_num=False), *pr[2:]) if pr[0] == "pin" and not pr[1].get("hidden")
             else pr for pr in sym["prims"]]
    return dict(sym, prims=prims)


def category_designator(sym, folder):
    """The part's category letter for a symbol its source calls U? (EasyEDA draws crystals, transistors ... as U?):
    a crystal filed under Crystals is Y?. A source's own non-U letter (X? for a crystal) is kept."""
    pre = re.match(r"[A-Z]*", str(sym.get("designator") or "").upper()).group(0)
    ref = RU.CAT_REF.get(str(folder or "").rstrip("/").rsplit("/", 1)[-1])
    return dict(sym, designator=ref + "?") if ref and ref != "U" and pre in ("", "U", "IC") else sym


def passive_pins(sym):
    """sym with every pin Passive; unchanged when a pin looks like an oscillator's supply / output / enable."""
    pins = [pr[1] for pr in sym.get("prims") or [] if pr[0] == "pin"]
    if not pins or any(ACTIVE_PIN.match(str(p.get("name") or "")) for p in pins):
        return sym
    prims = [(pr[0], dict(pr[1], type="passive"), *pr[2:]) if pr[0] == "pin" else pr for pr in sym["prims"]]
    return dict(sym, prims=prims)


def _hold_lost_copper(st, name, notes):
    """kicad_import dropped copper it has no Altium layer for (B.Cu graphics, inner copper): the .PcbLib lacks copper
    the KiCad footprint has, so the part is held for a person (a net tie's bottom bridge, an antenna's ground)."""
    held = K.lost_copper_hold(name, notes)
    if held:
        st.setdefault("verify", []).append(held)


def passive_kind(item):
    from . import stdparts as SP
    cat, fp = item["folder"].split("/")[-1], item["footprint"].get("kicad") or item["footprint"]["ref"] or ""
    if cat == "Resistors":
        return "R"
    if cat == "Capacitors":
        return "CP" if fp.startswith("CP_") else "C"
    if cat == "Inductors":
        return "L"
    if cat == "Diodes" and (fp.startswith("LED_") or SP.says_led(item.get("description"))):
        return "LED"
    return None


def kicad_params(mpn, shared, kicad=True):
    """The parameters a symbol converted from a KiCad library carries: the MPN (not on one every R / C / L shares) and,
    from KiCad's own libraries, the CC-BY-SA credit, so an .IntLib compiled from the .SchLib keeps it."""
    return ([] if shared or not mpn else [("MPN", mpn)]) + ([("Library Credit", K.KICAD_CREDIT)] if kicad else [])


def _par(item):
    return {p["name"]: p["value"] for p in item.get("parameters", [])}


def _set_par(item, k, v):
    item["parameters"] = [p for p in item["parameters"] if p["name"] != k] + [{"name": k, "value": v}]
    item["cleared"] = [c for c in item.get("cleared", []) if c != k]


def _line_for(item, lines, rows):
    """The BOM line and triage row of a plan item: same MPN, else (no MPN) same value. Of several, the line whose
    footprint is the item's wins (10k 0603 and 10k 0402 are two items with one value: a DNP 0402 line must never be
    built from a fitted 0603 line's footprint), then a fitted line over a DNP one (R5 DNP and R1 share the MPN)."""
    par = _par(item)
    mpn, val = par.get("Manufacturer Part Number", ""), par.get("Value", "")
    fp = item.get("footprint") or {}
    want = {AL.lib_name(str(x).split(":")[-1]) for x in (fp.get("kicad"), fp.get("bom_ref"), fp.get("ref")) if x}
    best, score = ({}, {}), None
    for ln, row in zip(lines, rows):
        lm = "" if T.placeholder_mpn(ln.get("mpn")) else ln.get("mpn")    # 'N/A': planned without an MPN (altium_export)
        if (mpn and lm == mpn) or (not mpn and not lm and ln.get("value", "") == val):
            fps = {AL.lib_name(str(x).split(" ")[0].split(":")[-1]) for x in (ln.get("footprint"), row.get("local_footprint")) if x}
            s = (bool(fps & want), not ln.get("dnp") and not str(row.get("status") or "").startswith("dnp"))
            if score is None or s > score:
                best, score = (ln, row), s
    return best


def _own_land(names, plain):
    """The first footprint field that names a land of its own rather than the chip size `plain` (R_0603_1608Metric):
    a KiCad footprint name (R_Array_Convex_4x0603, L_Bourns_SRR1210A, a hand-solder variant), an array / shunt / RGB
    name, or a maker's land named KiCad's way (L_Wuerth_WE-PD-Typ-LS_1210). -> that name, or None."""
    from . import stdparts as SP
    for t in names:
        n = SP._strip_lib(t).strip()
        if not n or n == plain:
            continue
        if n in SP.kicad_names() or SP.own_land_name(n):    # a maker land with no chip code read too (R-FX10-1)
            return n
    return None


def _own_notes(rec):
    """A file record's notes without the "shared with" note a sharing part's copy of it carries."""
    return [n for n in rec.get("notes") or [] if not str(n).startswith("shared with ")]


def cost_hint(msg):
    """The datasheet reader's cost-cap refusal names `ecadlib datasheet` options (--estimate, --yes): in the librarian's
    tried lines, the commands that exist for a library run instead."""
    if "max_cost_usd" not in msg:
        return msg
    return re.sub(r";( nothing sent\.)? see --estimate.*$", "; nothing sent: see `ecadlib datasheet estimate "
                  "<plan.json>` for the cost, then raise [datasheet] max_cost_usd in ecadlib.toml", msg, flags=re.I | re.S)


class Run:
    def __init__(self, out, http, index, libs, sources, api_key=None, kicad_cache=None, refresh_index=False, gen_step=True,
                 rules=None, reader=None):
        self.out, self.http, self.index, self.libs, self.sources = Path(out), http, index, libs, sources
        self.rules = rules or RU.DEFAULTS
        self.models = self.out / "models"
        for d in ("PcbLib", "SchLib", "KiCad"):
            (self.models / d).mkdir(parents=True, exist_ok=True)
        self.api_key, self.written, self.gen_step = api_key, {}, gen_step
        self.syms, self.hashes = {}, {}               # SchLib path -> symbol dict; kicad_mod -> geometry hash
        self._kicad, self._kicad_args = None, (kicad_cache, refresh_index)
        self.reader, self.waiting = reader, []          # datasheet reader; parts waiting for its batch

    def kicad(self):
        if self._kicad is None:
            f = Path(self._kicad_args[0] or "")
            held = self._kicad_args[0] and KiCadLibrary._backoff(f.with_name(f.name + ".retry.json"), time.time())
            if self._kicad_args[0] and not self.http.offline and (self._kicad_args[1] or not f.exists()) and \
                    (self._kicad_args[1] or not held):
                getattr(self, "log", print)("  building the KiCad library name index from gitlab.com (first run only, "
                                            "can take a few minutes; cached for 30 days; resumes if it stops) ...")
            self._kicad = KiCadLibrary(self.http, *self._kicad_args)
        return self._kicad

    def rel(self, p):
        return os.path.relpath(p, self.out).replace(os.sep, "/")

    # ---- writers: everything goes through kicad_import's converters
    def put_fp(self, st, text, name, source, http=None, height=None, height_src="", spec=None, model=None):
        """One file per footprint, shared only by parts whose pads are the same (ecadlib/fp_share.py).
        model: a downloaded 3D model already placed on the footprint (easyeda_model), the part's body and height."""
        bom_name = name
        own_h = model["info"].get("height") if model else height    # the part's own body: shared only with one as high
        name, pcb, same, h = FS.pick(self.written, self.models, name, text, source, st["name"], self.out, self.hashes,
                                     own_h)
        renamed = {"renamed_from": bom_name} if name != bom_name else {}
        if same is not None:                    # (its own file from an earlier run, kept for a skipped part: not shared)
            st["footprint"] = {**{k: v for k, v in same.items() if k not in ("renamed_from", "chosen")},
                               "notes": _own_notes(same) if same.get("by") == st["name"] else
                               [f"shared with {same['by']}"], "name": name, "hash": h, "hash_v": FS.HASH_V, **renamed}
            _hold_lost_copper(st, name, same.get("notes") or [])
            self._relink(st, bom_name, name)
            return
        pcb, stem = Path(pcb), AL.file_stem(name)
        body = renamed and own_h is not None and any(       # same pads, but another 3D body (R-CV-2)
            FS.base_of(r).casefold() == bom_name.casefold() and FS.same_fp(r, h, source, self.out, self.hashes, text)
            for r in self.written.values())
        notes = [f"named {name}: footprint {bom_name} is another part's, with "
                 + (f"another 3D body (this part's is {own_h} mm high)" if body else "other pads")] if renamed else []
        fp, t = K.convert_footprint(text, name, source.split(":", 1)[-1], notes,
                                    K.KICAD_LICENSE if source.startswith("kicad:") else "")
        _hold_lost_copper(st, name, notes)
        if model:                               # the source's own 3D model: its shape and height beat any table
            E3.add_body(fp, model["text"], model["info"], model["ref"], notes, embed=self.gen_step)
            warn = E3.checks(model["info"], model["ref"], fp["pads"])
            notes += warn
            st["review"] += warn
        else:
            try:
                K.add_body(fp, t, http, height, False, notes, gen_step=self.gen_step, spec=spec,      # a part-specific
                           height_src=(height_src or "caller") if height else None,                      # height beats the table
                           kind=st.get("kind"), names=st.get("names", ()))
            except ValueError as e:
                notes.append(f"no 3D body: {e}")
        kmod = self.models / "KiCad" / f"{stem}.kicad_mod"
        text = re.sub(r'^(\s*\((?:footprint|module)\s+)("(?:[^"\\]|\\.)*"|[^\s()]+)',     # KiCad links a footprint by its file name,
                      lambda m: m.group(1) + '"' + stem.replace('"', '\\"') + '"', text, count=1)   # Altium by this name: one name
        text = re.sub(r'(\((?:fp_text\s+value|property\s+"Value")\s+)("(?:[^"\\]|\\.)*"|[^\s()]+)',   # Value = the name (KLC F9.1)
                      lambda m: m.group(1) + '"' + stem.replace('"', '\\"') + '"', text, count=1)
        kmod.write_text(text, encoding="utf-8", newline="\n")
        AL.write_pcblib(pcb, [fp], RU.layers(self.rules))
        st["footprint"] = {"source": source, "file": self.rel(pcb), "kicad_mod": self.rel(kmod), "notes": notes, "by": st["name"],
                           "name": name, "hash": h, "hash_v": FS.HASH_V, "height": fp.get("height_mm") or None, **renamed,
                           **({"mount_pads": explicit_mount_pads(spec)} if explicit_mount_pads(spec) else {}),
                           **({"model": {"source": f"easyeda:{model['ref']['uuid']}", "url": model["ref"]["url"],
                                         "title": model["ref"]["title"], "credit": EC.CREDIT}} if model else {})}
        self.written[str(pcb)] = st["footprint"]
        self._relink(st, bom_name, name)

    def _relink(self, st, bom_name, name):
        """A footprint renamed after this part's symbol was written: point the symbol's footprint link at it."""
        rec = st.get("symbol") or {}
        path = str(self.out / rec["file"]) if rec.get("file") else ""
        sym = self.syms.get(path)
        if name == bom_name or rec.get("by") != st["name"] or rec.get("chosen") or not sym or sym.get("footprint") != bom_name:
            return
        self.syms[path] = sym = dict(sym, footprint=name)
        AL.write_schlib(path, [sym])
        rec.setdefault("notes", []).append(f"footprint link {name} (the BOM's {bom_name} is another footprint)")

    def put_sym(self, st, sym, source, notes=(), kicad_types=None):
        """kicad_types: {pin number: KiCad type} the .SchLib cannot hold (kicad_only_types), kept with the plan's
        symbol so the KiCad export puts them back."""
        path = self.models / "SchLib" / f"{AL.file_stem(sym['name'])}.SchLib"
        if st.get("kind") in SHARED_KINDS:      # one Resistor / Capacitor / Inductor symbol for every value and size,
            sym = shared_symbol(sym)            # whichever source drew it: no footprint, no one part's MPN
        sym = category_designator(sym, st.get("folder"))
        if st.get("passive") or re.match(r"(FB|[RCLYX])\??$", str(sym.get("designator") or "").upper()):
            sym = quiet_two_pin(sym)
        if st.get("passive"):
            sym = passive_pins(sym)
        fr = st.get("footprint") or {}
        if fr.get("renamed_from") and sym.get("footprint") == fr["renamed_from"]:     # footprint renamed (fp_share)
            sym = dict(sym, footprint=fr["name"])
        # Windows / macOS file systems ignore case: CH340C.SchLib and ch340c.SchLib are one file, so one symbol
        rec = next((r for p, r in self.written.items() if p.casefold() == str(path).casefold()), None)
        if rec is not None:                     # its own file from an earlier run, kept for a skipped part: not shared
            st["symbol"] = dict(rec, notes=_own_notes(rec) if rec.get("by") == st["name"] else [f"shared with {rec['by']}"])
            return
        AL.write_schlib(path, [sym])
        self.syms[str(path)] = sym
        st["symbol"] = {"source": source, "file": self.rel(path), "notes": list(notes), "by": st["name"]}
        if kicad_types:
            st["symbol"]["kicad_types"] = dict(kicad_types)
        self.written[str(path)] = st["symbol"]

    def put_kicad_sym(self, st, chain, name, fp_name, source, extra=(), layout=False):
        """layout: re-lay plain IC symbols out by the house pin rule (EasyEDA symbols). KiCad library symbols are curated
        and kept as drawn, unless their pin text would overlap in Altium (symbol_layout.text_problems)."""
        notes = []
        sym = K.convert_symbol(chain, name, fp_name, list(extra), None, notes)
        npins = len({pr[1]["num"] for pr in sym["prims"] if pr[0] == "pin"})
        clash = SL.text_problems(sym)
        stacked = stacked_pins(sym) if SL.settings(self.rules)["stack_power"] == "none" else []
        if (layout or clash) and SL.wants_layout(npins, self.rules) and SL.is_box_ic(sym) or stacked and SL.is_box_ic(sym):
            sym = SL.relayout(sym, self.rules)
            notes.append(f"{npins} pins grouped by function (ecadlib.symbol_layout)" + (f", {sym['parts']} parts" if sym.get("parts", 1) > 1 else "")
                         + (f"; the drawn symbol's pin text overlapped ({clash[0]})" if clash else "")
                         + (f"; the drawn symbol stacked pins {', '.join(stacked[:3])} on one end ([symbols] stack_power = \"none\")" if stacked else ""))
        elif clash:
            notes.append(f"pin text overlaps ({'; '.join(clash[:3])}); not a plain IC box, so kept as drawn: check it")
        elif stacked:
            notes.append(f"pins {', '.join(stacked[:3])} share one pin end; not a plain IC box, so kept as drawn: check it")
        self.put_sym(st, sym, source, notes,
                     kicad_types=kicad_chain_types(chain) if source.startswith(("kicad:", "local:")) else None)

    # ---- per part
    def resolve(self, item, ln, row, keep=None):
        """keep: halves a person chose with `ecadlib resolve` ({kind: state record}); they are not re-fetched."""
        par = _par(item)
        mpn = par.get("Manufacturer Part Number", "")
        st = {"name": item["name"], "symbol": None, "footprint": None, "tried": [], "review": [], "verify": [],
              "found": {}, "hint_fp": "",
              "maker": par.get("Manufacturer", "") if RC._filled(par.get("Manufacturer")) else ""}   # not "N/A"
        for k, rec in (keep or {}).items():
            st[k] = rec
            st["tried"].append(f"resolve: {k} {rec['source']} chosen by user (kept)")
        if ln.get("altium_mpn_source") == "Alternate" and ln.get("footprint"):   # alternate_footprint: the land
            st["alternate"] = True                     # is the base part's until the alternate's own read says so
        both = two_readings(ln.get("value"))
        if both:                                             # '4,700pF': 4.7 pF or 4.7 nF, so a person says which
            st["verify"].append(f"value {ln['value'].strip()} reads two ways: {both[0]} or {both[1]}; write it without the comma")
        dnp = (row.get("status") or "").startswith("dnp")   # not assembled, but still on the schematic and the PCB:
        if dnp:                                              # built from the free sources, no paid datasheet read
            st["tried"].append("DNP line: built for the schematic/PCB (not assembled), from the free sources only")
        kind, fp_name, sym_name = passive_kind(item), item["footprint"]["ref"], item["symbol"]["ref"]
        st["passive"], st["folder"] = is_passive_part(item, ln), item.get("folder")
        st["kind"], st["names"] = kind, [item["footprint"].get("kicad") or "", ln.get("footprint") or "", item.get("description") or ""]
        try:
            self.local(st, row, kind, fp_name, sym_name)
        except Exception as e:
            st["tried"].append(f"local library: failed ({type(e).__name__}: {e})")
        for s in self.sources:
            if st["symbol"] and st["footprint"]:
                break
            if dnp and s == "datasheet":
                continue
            try:
                getattr(self, f"src_{s}")(st, item, ln, row, kind, mpn, fp_name, sym_name)
            except Exception as e:            # one bad source or part must not stop the run
                msg = cost_hint(str(e)) if s == "datasheet" else str(e)
                st["tried"].append(f"{s}: failed ({type(e).__name__}: {msg}{_where(e)})")
        bom_fp = str(ln.get("footprint") or "").strip()
        if st.get("hint_fp") and bom_fp and AX.lands_differ(st["hint_fp"], bom_fp):    # R-BF-5: the BOM's land is
            st["verify"].append(f"footprint: the BOM names {bom_fp} but the symbol's Footprint property is "   # built;
                                f"{st['hint_fp']}: check which package the part has")    # the symbol says another
        lc = (row.get("lcsc") or "").split(" ")[0]
        if row.get("src_description"):        # the part search's own text (Nexar / DigiKey / Mouser / LCSC), after a KiCad symbol's
            st["found"].setdefault("description", (row["src_description"], f"{_desc_label(row)} part data {lc}".strip()))
        if "easyeda_description" in st["found"]:                  # then the text on EasyEDA's component
            st["found"].setdefault("description", st["found"].pop("easyeda_description"))
        if row.get("src_manufacturer"):        # the maker a part search lists for this exact MPN (one maker only: an MPN
            src = _desc_label({"src": row.get("src_manufacturer_from", "")})       # sold by clones too fills nothing)
            st["found"].setdefault("manufacturer", (row["src_manufacturer"], f"{src} part data {lc} (exact MPN)".replace("  ", " ")))
        return st

    def local(self, st, row, kind, fp_name, sym_name):
        ls, lf = (row.get("local_symbol") or "").split(" ")[0], (row.get("local_footprint") or "").split(" ")[0]
        if ls and not kind and not st["symbol"]:
            lib, name = ls.split(":", 1)
            f = next((p for r in self.libs for p in Path(r).rglob(f"{lib}.kicad_sym")), None)
            if f:
                chain, _ = K.load_symbol(f"{f}:{name}", None)
                st["hint_fp"] = K._props(chain).get("Footprint", "")
                self.put_kicad_sym(st, chain, sym_name, fp_name, f"local:{ls}")
                how = re.search(r"\((.*)\)\s*$", row.get("local_symbol") or "")
                if how and "check" in how.group(1):      # matched on the Comment / Value, the MPN a variant (R-BF-6)
                    st["verify"].append(f"symbol {ls} matched by {how.group(1)}")
        if lf and lf in self.index.footprints and not st["footprint"]:
            text = Path(self.index.footprints[lf]["path"]).read_text(encoding="utf-8")
            spec = SPECS / f"{lf.split(':')[-1]}.json"
            hm = json.loads(spec.read_text(encoding="utf-8")).get("height_max") if spec.exists() else None
            self.put_fp(st, text, fp_name, f"local:{lf}", None, F.dim(hm)[2] if hm else None, f"package spec {spec.name}")
        if ls or lf:
            st["tried"].append(f"local library: symbol {'from ' + ls if st['symbol'] else 'not used' if ls else 'none'}, "
                               f"footprint {'from ' + lf if st['footprint'] else 'none'}")

    def _wild_other_pinout(self, lib, mpn, sym_ref, chain):
        """Another x-wildcard name the MPN fits whose pin number -> name map differs from sym_ref's (MCP6L01x-xOT
        against MCP6L01Rx-xOT: V+ and V- swapped), or one that cannot be read to compare; None when every one matches
        (R-SY-1)."""
        alts = lib.wild_alternates(mpn, sym_ref) if hasattr(lib, "wild_alternates") else []
        if not alts:
            return None
        def pinout(ch):
            sym = K.convert_symbol(ch, "x", None, [], None, [])
            return sorted((str(p[1].get("num")), str(p[1].get("name"))) for p in sym["prims"] if p[0] == "pin")
        try:
            mine = pinout(chain)
        except Exception:
            return alts[0]
        for alt in alts:
            try:
                theirs = pinout(K.load_symbol(alt, self.http)[0])
            except Exception:                      # not readable: it cannot be shown to agree
                return alt
            if theirs != mine:
                return alt
        return None

    def src_kicad(self, st, item, ln, row, kind, mpn, fp_name, sym_name):
        lib = self.kicad()
        if not lib.symbols:
            st["tried"].append(f"kicad: library index unavailable ({lib.error})")
            return
        sym_ref = how = fp_ref = fp_how = None
        own = False
        if kind:
            sym_ref, how = PASSIVE[kind][0], "generic passive symbol"
            if kind == "R" and RU.resistor_style(self.rules) == "us" and "Device" in lib.symbols.get("R_US", ()):
                sym_ref = "Device:R_US"                # [symbols] resistor = "us": the zigzag symbol
            pkg = package_of(item["footprint"].get("kicad") or fp_name, item.get("description"))
            names = [ln.get("footprint", ""), item["footprint"].get("kicad") or fp_name]
            named = next((lib.footprint(n) for n in names if lib.footprint(n)), None)
            if named:                              # the land the BOM names, as named (R-BF-4): L_Bourns_SRR1210A is
                fp_ref, fp_how = named, "BOM footprint name"            # not L_1210, R_Array_.. not a 2-pad R_0603
            elif PASSIVE[kind][1] and pkg in IMP2MET:
                guess = PASSIVE[kind][1].format(pkg, IMP2MET[pkg])
                own = _own_land(names, guess.split(":")[-1])
                if own:                            # a land of its own the index does not have: no package guess
                    st["tried"].append(f"kicad: the BOM names the land {own}, not in the KiCad index; "
                                       f"not guessed as {guess.split(':')[-1]} by package")
                else:
                    fp_ref, fp_how = lib.footprint(guess), f"by package {pkg}"
        elif not st["symbol"]:
            sym_ref, how = lib.find_symbol(mpn)
            own = bool(sym_ref)                        # this part's own symbol, not a generic one
            if not sym_ref:
                sym_ref, how = lib.generic_connector(ln.get("value", ""))
        chain = None
        if sym_ref and not st["symbol"]:
            chain, _ = K.load_symbol(sym_ref, self.http)
            props = K._props(chain)
            st["hint_fp"] = st["hint_fp"] or props.get("Footprint", "")
            if str(props.get("Datasheet", "")).startswith("http"):
                st["found"].setdefault("datasheet", (props["Datasheet"], f"KiCad symbol {sym_ref}"))
            shared = kind in ("R", "C", "CP", "L")            # one generic symbol for every value: no MPN, no footprint link
            if props.get("Description") and own:     # a generic symbol's "Resistor" / "Light emitting diode" /
                st["found"].setdefault("description", (props["Description"], f"KiCad symbol {sym_ref}"))  # "Generic
                # connector ..., script generated" describes the symbol, not this part
            self.put_kicad_sym(st, chain, sym_name, None if shared else fp_name, f"kicad:{sym_ref}", kicad_params(mpn, shared))
            st["tried"].append(f"kicad: symbol {sym_ref} ({how})")
            if "check" in how:                     # a near name: a person checks it before it is released
                st["verify"].append(f"symbol {sym_ref} matched by {how}")
            elif own and "x wildcards" in how:
                other = self._wild_other_pinout(lib, mpn, sym_ref, chain)
                if other:
                    st["verify"].append(f"symbol {sym_ref} matched by {how}, but the MPN also fits {other}, whose "
                                        f"pinout differs (check the variant)")
        elif not st["symbol"]:
            st["tried"].append(f"kicad: no symbol named like {mpn or '(no MPN)'}")
        if st["footprint"]:
            return
        bom_fp = ln.get("footprint", "")
        for cand, why in ((fp_ref, fp_how), (lib.footprint(bom_fp), "BOM footprint name"),     # the BOM's land first (R-BF-5)
                          (lib.footprint(st["hint_fp"]), "symbol's Footprint property"),
                          (None if kind else lib.footprint_by_mpn(mpn), "footprint named after the MPN"),
                          (lib.footprint_by_prefix(bom_fp.split(":")[-1]), f"shortest KiCad name starting with '{bom_fp}'")):
            if cand:
                text, _ = K.load_footprint(cand, self.http)
                self.put_fp(st, text, fp_name, f"kicad:{cand}", self.http)
                st["tried"].append(f"kicad: footprint {cand} ({why})")
                if why.startswith(("shortest", "footprint named")):     # a guess by name: held until a person looks
                    st["verify"].append(f"footprint {cand} chosen by {why}: compare with the datasheet land pattern")
                return
        st["tried"].append(f"kicad: no footprint for '{bom_fp or fp_name}'")

    def src_easyeda(self, st, item, ln, row, kind, mpn, fp_name, sym_name):
        m = re.match(r"C\d+", ln.get("lcsc") or row.get("lcsc") or "")
        if not m:
            st["tried"].append("easyeda: no LCSC number")
            return
        lcsc = m.group(0)
        r = self.http.get(P.EasyEDA.URL.format(lcsc), headers=P.EasyEDA.HEADERS)
        if not r.ok:
            st["tried"].append(f"easyeda {lcsc}: {r.error or f'HTTP {r.status}'}")
            return
        data = r.json()
        res = P.parse_easyeda(data, P.SourceResult("easyeda", lcsc=lcsc))
        if not res.found:
            st["tried"].append(f"easyeda {lcsc}: no component")
            return
        bare = norm_mpn(mpn) == norm_mpn(lcsc)      # the dialog's "type an LCSC number": the number is the MPN too
        if mpn and res.mpn and not bare and norm_mpn(res.mpn) != norm_mpn(mpn):
            st["tried"].append(f"easyeda {lcsc}: is {res.mpn}, BOM says {mpn}; not used")
            st["review"].append(f"LCSC {lcsc} is {res.mpn}, not {mpn}: fix the BOM's LCSC number")
            return
        result = data["result"]
        url = (result.get("lcsc") or {}).get("url") or ""
        if url.startswith("http"):
            st["found"].setdefault("datasheet", (url, f"EasyEDA {lcsc} (lcsc.url)"))
        # the maker of the BOM's own LCSC number; of a number a part search found, only when the triage found that
        # MPN under one maker (src_manufacturer): the search's first hit among clones is not this part's maker
        if res.manufacturer and (P.lcsc_number(ln.get("lcsc")) == lcsc or row.get("src_manufacturer")):
            st["found"].setdefault("manufacturer", (res.manufacturer, f"EasyEDA {lcsc}"))
        st["found"].setdefault("lcsc", (lcsc, f"EasyEDA {lcsc}" + ("" if ln.get("lcsc") else " (LCSC number found by ecadlib, not in the BOM)")))
        if res.extra.get("description"):       # after the part search's LCSC / distributor text (resolve)
            st["found"].setdefault("easyeda_description", (res.extra["description"], f"EasyEDA {lcsc}"))
        if res.package:
            st["found"].setdefault("package", (res.package, f"EasyEDA {lcsc}"))
        got = []
        if not st["footprint"]:
            pkg = (result.get("packageDetail") or {})
            notes = []
            text = EC.footprint_to_kicad(pkg.get("dataStr") or {}, pkg.get("title") or fp_name, notes)
            text = FT.tidy(text, notes)                       # to the rule book: silk reference, pin 1, rounded pads
            model = self.easyeda_model(st, pkg.get("dataStr") or {}, lcsc)
            self.put_fp(st, text, fp_name, f"easyeda:{lcsc}", None, model=model)
            st["footprint"]["notes"] = notes + st["footprint"]["notes"]
            got.append(f"footprint {pkg.get('title', '')}")
        if not st["symbol"]:
            ds = result.get("dataStr") or {}
            if not ds.get("shape") and result.get("subparts"):
                st["tried"].append(f"easyeda {lcsc}: multi-unit symbol (subparts) not converted")
            else:
                notes = []
                desc = item.get("description") or row.get("src_description") or res.extra.get("description") or ""
                text = EC.symbol_to_kicad(ds, sym_name, fp_name, {"MPN": mpn, "LCSC": lcsc, "Manufacturer": res.manufacturer,
                                                                  "Description": desc}, notes)
                chain = [K._find_symbol(sexpr.parse(text), sym_name)]
                self.put_kicad_sym(st, chain, sym_name, fp_name, f"easyeda:{lcsc}", [("MPN", mpn)] if mpn else [], layout=True)
                st["symbol"]["notes"] = notes + st["symbol"]["notes"]
                got.append("symbol")
        if got:
            st["found"].setdefault("credit", (EC.CREDIT, f"EasyEDA {lcsc}"))
        st["tried"].append(f"easyeda {lcsc}: " + (", ".join(got) or "nothing needed"))
        if got:                                # a converted EasyEDA part is held until a person has compared it
            st["verify"].append(f"EasyEDA {lcsc} conversion: check pads and pins against the datasheet (native converter, format assumed)")

    def easyeda_model(self, st, data, lcsc):
        """The EasyEDA footprint's own 3D model (STEP), downloaded and placed on the footprint (ecadlib.easyeda_3d), or
        None: no model named, the download failed or the file is not a STEP (the generated model or box is used)."""
        ref = E3.model_ref(data)
        if ref is None:
            st["tried"].append(f"easyeda {lcsc}: no 3D model named")
            return None
        text, why = E3.fetch(self.http, ref)
        if text is None:
            st["tried"].append(f"easyeda {lcsc}: 3D model {ref['uuid']} not downloaded ({why}); package model made here instead")
            return None
        try:
            placed, info = E3.place(text, ref)
        except ValueError as e:
            st["tried"].append(f"easyeda {lcsc}: 3D model {ref['uuid']} not used ({e})")
            return None
        st["tried"].append(f"easyeda {lcsc}: 3D model {ref['title'] or ref['uuid']}, height {info['height']} mm")
        return {"text": placed, "info": info, "ref": ref}

    def src_standard(self, st, item, ln, row, kind, mpn, fp_name, sym_name):
        """Standard packages and pinouts, built offline (ecadlib.stdparts); fills only what is still missing."""
        from . import stdparts as SP
        desc = " ".join(dict.fromkeys(str(x) for x in (item.get("description"), ln.get("description")) if x))
        k = kind or SP.part_kind(item["folder"], item["footprint"].get("kicad") or fp_name, desc, ln.get("designator"))
        res = SP.resolve([st["hint_fp"], ln.get("footprint"), item["footprint"].get("kicad"), ln.get("value")], k, mpn, desc)
        got = []
        if not st["footprint"] and res["footprint"]:
            sp = SP.spec(res["footprint"])
            text, _ = SP.build(res["footprint"], RU.density_for(sp, self.rules))
            h = F.dim(sp["height_max"])[2] if sp.get("height_max") else None
            self.put_fp(st, text, fp_name, f"standard:{res['kicad_ref']}", None, h, "standard package table", sp)
            got.append(f"footprint {res['kicad_ref']} ({res['fp_how']})")
        fp_src = str((st["footprint"] or {}).get("source") or "")
        same_fp = res["footprint"] and fp_src.endswith(":" + res["footprint"])
        if res["symbol"] == "R" and RU.resistor_style(self.rules) == "us":
            res["symbol"] = "R_US"
        if not st["symbol"] and res["symbol"] and (k in ("R", "C", "CP", "L") or same_fp):
            text, name = SP.symbol_text(res["symbol"], mpn)
            shared = k in ("R", "C", "CP", "L")
            self.put_kicad_sym(st, [K._find_symbol(sexpr.parse(text), name)], sym_name, None if shared else fp_name,
                               f"standard:{name}", [] if shared or not mpn else [("MPN", mpn)])
            got.append(f"symbol {name} ({res['sym_how']})")
            if ":" in res["symbol"]:               # SOT-23 & co: pinouts differ by maker, so a person checks it
                st["verify"].append(f"symbol pinout {name} from the {mpn} family table (ecadlib.stdparts): check pins 1-3 against the datasheet")
        elif not st["symbol"] and res["symbol"]:
            res["sym_how"] = f"footprint came from {fp_src or 'nowhere'}, not {res['footprint']}: pin numbers not known to match"
        why = [] if st["footprint"] else [f"footprint: {res['fp_how']}"]
        why += [] if st["symbol"] else [f"symbol: {res['sym_how']}"]
        st["tried"].append("standard: " + "; ".join(got + why))

    def src_datasheet(self, st, item, ln, row, kind, mpn, fp_name, sym_name):
        st["tried"] = [t for t in st["tried"] if "waiting for your OK to read it with AI" not in t   # asked again now
                       and "your [datasheet] folder" not in t]     # said below as the folder is now (`batch PLAN`)
        urls = D.datasheet_urls(item, ln, row, st["found"])
        cfg = getattr(self.reader, "cfg", None) or D.settings(self.rules)   # the reader's [datasheet] settings
        local = None if urls else D.local_datasheet(cfg.get("folder"), mpn or item["name"])[0]   # [datasheet] folder
        if not urls and local is None and not cfg.get("web_search"):   # (no link: [datasheet] web_search may find one)
            note = D.folder_problem(cfg.get("folder"))
            st["tried"].append("datasheet: no datasheet URL known" + (f"; {note}" if note else ""))
            self.reading(st)                          # (no link: none refused)
            return
        # (the folder file's name only, never its path)
        first = urls[0] if urls else (local.name, "datasheet folder") if local else ("a web search", "web search")
        maker = st.get("maker") or (st.get("found", {}).get("manufacturer") or ("",))[0] or None
        if self.reader is None:
            self.reader = D.Reader(self.out, Path(self._kicad_args[0] or self.out / "cache").parent, self.api_key, self.rules, self.http)
        rd = self.reader
        if getattr(rd, "offline", False) and not rd.estimate_only:   # --offline: an answer already cached, else none
            try:
                job = rd.job(mpn or item["name"], urls, fp_name, name=item["name"], fp_needed=not st["footprint"],
                             mpn=mpn or None, maker=maker)
                if self.reading(st) and not rd.answered(job):
                    self.refused(st, job, None, cfg.get("folder"))
                self.folder_file_failed(st, job)
                res = rd.cached(job)
            except CC.Held as e:
                self.cross_check_held(st, e)
                return
            except D.DatasheetError:
                res = None
            if res is None:
                st["tried"].append(f"datasheet: {D.OFFLINE} ({first[0]})")
                return
            self.put_datasheet(st, res, job, fp_name, sym_name, "cached")
            return
        if not rd.key and not rd.estimate_only:
            st["tried"].append(f"needs datasheet reader (no API key): {first[0]} ({first[1]})")
            return
        if not getattr(rd, "_recollected", False):    # answers paid for in earlier batches, before a parser fix
            rd._recollected = True
            rd.recollect()
        refused_before = self.reading(st)             # (a link that no longer gives a PDF at all: said no more)
        job = rd.job(mpn or item["name"], urls, fp_name, name=item["name"], fp_needed=not st["footprint"],
                     mpn=mpn or None, maker=maker)
        if refused_before and not rd.answered(job):   # (an earlier answer: the API fetched the link)
            self.refused(st, job, None, cfg.get("folder"))
        self.folder_file_failed(st, job)
        try:
            res = hit = rd.cached(job)
            if res is None and rd.deferred:
                rd.submit(job)
                self.waiting.append((st, job, fp_name, sym_name))
                at = ", ".join(x for x in (job["url"], job["where"]) if x)
                st["tried"].append(f"datasheet: queued for the batch ({at})")
                return
            res = res or rd.read_now(job)
        except CC.Held as e:
            self.cross_check_held(st, e)
            return
        except Exception as e:
            self.read_failed(st, job, e, cfg.get("folder"), rd.answered(job, e))
            raise
        self.put_datasheet(st, res, job, fp_name, sym_name, "cached" if hit else "")

    @staticmethod
    def reading(st):
        """The datasheet is fetched or read again (`datasheet batch PLAN` keeps each part's state): an earlier run's
        refused-download advice is taken out -> whether there was one. `refused(st, job, None, folder)` says it again,
        as the folder is now, while the maker's site still refuses the link it is sent as and the API has not answered
        it (Reader.answered); an answer that comes back takes it out (put_datasheet, cross_check_held, read_failed,
        flush_datasheets)."""
        had = any(r.startswith("datasheet download refused by ") for r in st["review"])
        st["review"][:] = [r for r in st["review"] if not r.startswith("datasheet download refused by ")]
        return had

    @staticmethod
    def folder_file_failed(st, job):
        """The part's own file in the [datasheet] folder is no PDF (a web page saved as .pdf) or does not read: said
        first, so the person replaces it instead of saving it again under the same name."""
        bad = D.folder_failure(job.get("prov"))
        if bad:
            st["tried"].append(f"datasheet: {bad}")

    @staticmethod
    def refused(st, job, error, folder=None):
        """A maker's site refused the PDF here and the API's fetcher failed too: the cause and the way round it, which
        the release check shows in place of "no footprint". error: the API's, or None (`reading`). folder: [datasheet]
        folder (the review keeps its name)."""
        line = D.refused_line(job, error, folder)
        if line and line not in st["review"]:
            st["review"].append(line)

    @staticmethod
    def read_failed(st, job, error, folder=None, answered=False):
        """The read failed. answered (Reader.answered): the API answered it (declined, not readable, its cross-check
        not sent), so it fetched the link: an earlier run's advice goes, as on a read; an error before any answer leaves
        it. The API's fetcher failing says it."""
        if answered:
            Run.reading(st)
        Run.refused(st, job, error, folder)

    @staticmethod
    def cross_check_held(st, e):
        """A datasheet read whose cross-check disagreed: nothing is written; both values go to the review."""
        Run.reading(st)                               # (an answer came back: no refused-download advice)
        st["cross_check"] = {"status": "disagreed", "summary": CC.summary_line(e.record)}
        st["tried"].append("datasheet reader: held, the cross-check disagrees (see datasheet/*.review.md)")
        st["review"].append(f"cross-check disagrees, nothing written: {CC.summary_line(e.record)}")

    def flush_datasheets(self, log=print):
        """Send the parts queued for the datasheet batch and build what came back."""
        if not self.waiting:
            return
        try:
            done = self.reader.flush()
        except D.DatasheetError as e:
            done = {job["key"]: e for _, job, _, _ in self.waiting}
        for st, job, fp_name, sym_name in self.waiting:
            r = done.get(job["key"])
            if isinstance(r, CC.Held):
                self.cross_check_held(st, r)
            elif isinstance(r, dict):
                try:
                    self.put_datasheet(st, r, job, fp_name, sym_name, r["sidecar"].get("via") or "batch")
                except Exception as e:
                    st["tried"].append(f"datasheet: failed ({type(e).__name__}: {e})")
            elif isinstance(r, Exception):
                st["tried"].append(f"datasheet reader: failed ({r})")
                self.read_failed(st, job, r, (getattr(self.reader, "cfg", None) or D.settings(self.rules)).get("folder"),
                                 self.reader.answered(job, r))
            elif r == "estimate only":                    # priced, not sent: the person approves the cost first
                st["tried"].append(f"datasheet: waiting for your OK to read it with AI ({job['url'] or job['where']})")
            else:
                st["tried"].append(f"datasheet: {r or 'not sent'}" + ("; run again to collect" if str(r).startswith("pending") else ""))
                if self.reader.answered(job):          # (pending: next step: an answer came back, the route goes on)
                    self.reading(st)
        self.waiting = []

    def standard_land(self, spec, known=None):
        """[datasheet] standard_land: a read whose pin count, pitch, body and exposed pad name exactly one checked standard
        package (stdparts.identify) -> (table name, how, why it is used) or None. off (the default): never. auto: when
        the read left a number blank or gave no land of its own (the maker's recommended land, when printed, is kept);
        always: whenever it identifies one. known: the packages the PDF's own ordering key or ordering table name for
        this part (ds_route, "QFP144"); when it is one, a read that left the pitch or body blank can still name the
        one checked package of that family and pin count that fits what it did read."""
        from . import stdparts as SP
        mode = str(D.settings(self.rules).get("standard_land") or "off")
        if mode == "off":
            return None
        known = list(known or [])
        name, how = SP.identify(spec, known[0] if len(known) == 1 else None)
        if not name:
            return None
        own = spec.get("land") or spec.get("pad_table")
        if mode == "auto" and own and not spec.get("unresolved"):
            return None
        why = ("the read left " + ", ".join(spec["unresolved"]) + " blank") if spec.get("unresolved") else \
            "the datasheet gives no land of its own" if not own else "[datasheet] standard_land = always"
        return name, how, why

    def put_datasheet(self, st, res, job, fp_name, sym_name, how=""):
        pdf = res["sidecar"].get("pdf") or {}        # the link read (the next one when the API could not fetch the first)
        url, where = pdf.get("url") or job["url"], pdf.get("where") or job["where"]
        self.reading(st)                              # read: no refused-download advice left
        from . import fp_edit as FE                   # hand edits of this part's pad table (ecadlib fp-edit)
        res, edited = FE.apply(FE.for_part(FE.load(self.out), st.get("name"), job.get("name"), job["part"]), res)
        if edited:
            st["tried"].extend(edited)
            st["review"].extend(edited)
        from . import stdparts as SP
        if st.get("alternate"):                       # a variant's alternate: the package its own datasheet names
            try:
                known = list(res["sidecar"].get("package_from_pdf") or [])
                st["alt_read_package"] = SP.identify(res["spec"], known[0] if len(known) == 1 else None)[0]
            except Exception:                         # a read stdparts cannot judge names nothing
                st["alt_read_package"] = None
        other = [r for r in res["needs_review"] if r.startswith("datasheet from your folder:")]   # another file's PDF
        st["review"].extend(other)
        st["verify"].extend(other)                    # its own hold reason: a person confirms this PDF is this part's
        got = []

        def need_pins():                              # EPM240F100: the handbook has no pinout; checked before anything
            if not st["symbol"] and not (res.get("symbol") or {}).get("pins"):   # is written, after the footprint's holds
                raise D.DatasheetError("no symbol: the datasheet read gives no pin list for this part (the PDF may leave "
                                       "the pinout to a separate document), so it is held rather than built without its pins")
        std = self.standard_land(res["spec"], res["sidecar"].get("package_from_pdf")) if not st["footprint"] else None
        settled = (res.get("cross_check") or {}).get("standard")
        if settled and not st["footprint"]:           # the read gave this table package's sizes the other way round
            std = (settled, "the cross-check", "the read gave its length and width the other way round (crosscheck."
                   "_settle_turn)")
        if not std and not st["footprint"] and not (res["spec"].get("land") or res["spec"].get("pad_table")):
            # DPAK / D2PAK / SOT-223: the IPC tab is the exposed tab only
            tab, found = SP.identify_tab(res["spec"], [(q["num"], q["name"]) for q in (res.get("symbol") or {}).get("pins") or []])
            if tab:
                std = (tab, found, "a tab land runs under the body, where the IPC tab built from the read covers only "
                       "the exposed tab, and the datasheet gives no land of its own")
        # (a person's fp-edit settles the footprint's holds, below; the symbol's is judged on the edited read: a pad
        # rename that fixes the pin numbers lifts it, a pad-table edit that leaves the channels wrong does not)
        held = None if st["symbol"] else D.read_hold(res, False)
        if held:                                      # the read's symbol still fails a check that wires it wrong
            raise D.DatasheetError(held)
        if not st["footprint"]:                       # before any land choice: a table land never replaces these holds
            maker = st.get("maker") or (st.get("found", {}).get("manufacturer") or ("",))[0]
            joined = D.joined_to_pad(res["spec"], (res.get("symbol") or {}).get("pins")) or \
                D.thermal_land_missing(res["spec"], maker, url) or D.thermal_land_body_sized(res["spec"]) or \
                D.body_unread(res["spec"]) or \
                (None if std else D.tab_land_unread(res["spec"])) or D.tht_holes_unread(res["spec"]) or \
                D.body_on_holes(res["spec"])                  # (a checked standard land carries its own tab land)
            if joined:                                # leads merged with the pad, a TI thermal land not read or too
                raise D.DatasheetError(joined)        # small, no housing to draw, half a maker's land, guessed holes
            dip = D.standard_dip_holes(res["spec"])
            if dip:                                   # a DIP whose holes the datasheet doesn't print: KiCad's, not a guess
                res = dict(res, spec=dip)
                st["review"].append("DIP holes: the datasheet prints no hole size, so KiCad's DIP hole and pad (0.8 mm / "
                                    "1.6 mm) were used in place of the read's; check the maker's recommended hole")
            unread = None if std else D.land_unread(res["spec"], res["sidecar"].get("land_pages"))
            if unread:                                # the maker's land is printed but was not read: IPC pads from the
                known = list(res["sidecar"].get("package_from_pdf") or [])   # body would differ from it
                pkg, found = SP.identify(res["spec"], known[0] if len(known) == 1 else None)
                if not pkg:                           # no checked package either: nothing to build that is not a guess
                    raise D.DatasheetError(unread)
                pages = ", ".join(map(str, res["sidecar"]["land_pages"][:4]))
                std = (pkg, found, f"the datasheet prints its own land (page {pages}) but the read did not return it")
        if std:                                       # the read named a checked package: its land, not the read's pads
            pkg, found, why = std
            ren = SP.tab_renumber(res["spec"], pkg, [(q["num"], q["name"]) for q in (res.get("symbol") or {}).get("pins") or []])
            sp = SP.spec_renumbered(pkg, ren)
            res = drop_joined_tab(res, sp, st)
            text, _ = SP.build_renumbered(pkg, ren, RU.density_for(sp, self.rules))
            if ren:                                   # a cut N.C. lead's number on the table's tab (BD33FC0FP)
                (old, new), = ren.items()
                st["review"].append(f"tab: the datasheet numbers the tab {new}, and lead {old} is cut (N.C.); the land "
                                    f"numbers the tab {old}, so it was numbered {new} (check the pin table)")
                sym = res.get("symbol") or {}             # the cut lead has no pad: its N.C. pin leaves the symbol, as
                if not st["symbol"] and any(str(q["num"]) == old for q in sym.get("pins") or []):   # KiCad's does
                    res = dict(res, symbol=dict(sym, pins=[q for q in sym["pins"] if str(q["num"]) != old]))
                    st["review"].append(f"symbol: pin {old} (N.C., the cut lead) left out: it has no lead and no pad")
            hs = res["spec"].get("height_max") and "datasheet height_max" or sp.get("height_max") and "standard package table"
            h = F.dim((res["spec"] if hs == "datasheet height_max" else sp)["height_max"])[2] if hs else None   # this part's own height first
            need_pins()
            self.put_fp(st, text, fp_name, f"standard:{SP.kicad_ref(pkg)}", None, h, hs or "", sp)
            st["alt_own_land"] = bool(st.get("alternate"))   # from the alternate's own read, not the base part's name
            got.append(f"footprint {SP.kicad_ref(pkg)} (identified from the read)")
            st["review"].append(f"standard land: the datasheet read identified {pkg} ({found}); {why}, so the checked standard "
                                f"land was used. Check the package code on the drawing is this one")
        elif not st["footprint"]:
            held = None if edited else D.read_hold(res)   # built from the read's own numbers: after every rescue (no
            if held:                                  # checked land, no settle), a read that still fails a check
                raise D.DatasheetError(held)          # that can build it wrong is held, nothing written
            st["review"].extend(pad_table_problems(res["spec"]))
            try:
                text, _ = F.build(res["spec"], RU.density_for(res["spec"], self.rules))
            except (KeyError, TypeError) as e:     # a read that left a needed dimension blank: name it, not KeyError
                raise D.DatasheetError(D.no_footprint(res["spec"], e)) from e
            except ValueError as e:                # numbers that don't add up because the read left the drawing's
                unres = res["spec"].get("unresolved") or []   # dimensions blank (BD33FC0FP 2026-10-21: no TO252
                if not unres:                                 # drawing in the PDF): the blanks are the cause
                    raise
                raise D.DatasheetError(f"no footprint: the datasheet read left {', '.join(unres)} unresolved ({e})") from e
            h = F.dim(res["spec"]["height_max"])[2] if res["spec"].get("height_max") else None
            need_pins()
            self.put_fp(st, text, fp_name, f"datasheet:{url}", None, h, "datasheet height_max", res["spec"])
            st["alt_own_land"] = bool(st.get("alternate"))
            got.append("footprint")
        need_pins()
        if not st["symbol"]:
            from . import house_style as HS              # active-low pins in your library's notation ([style])
            sym, renamed = HS.adapt_symbol(res["symbol"], HS.active(self.rules, self.out))
            notes = [f"{len(sym['pins'])} pins grouped by function (ecadlib.symbol_layout)"] \
                if SL.wants_layout(sym["pins"], self.rules) else []
            if renamed:
                notes.append("house style pin names: " + ", ".join(renamed[:6]) + (" ..." if len(renamed) > 6 else ""))
            odd = SL.unknown_types_line(sym["pins"])
            if odd:                                      # drawn passive: never silently
                st["review"].append(f"symbol: {odd}")
                notes.append(odd)
            self.put_sym(st, AL.symbol_from_json(dict(sym, name=sym_name, footprint=fp_name), self.rules), f"datasheet:{url}",
                         notes, kicad_types=kicad_only_types(sym["pins"]))
            got.append("symbol")
        rt = res["sidecar"].get("route") or {}
        n_ans = len(rt.get("attempts") or [])
        st["tried"].append(f"datasheet reader ({where}{', ' + how if how else ''}): {', '.join(got) or 'nothing needed'}; "
                           f"{len(res['needs_review'])} review items"
                           + (f"; read by {rt['model']}" + (f" after {n_ans - 1} earlier answers (see review.md)" if n_ans > 1 else "")
                              if rt.get("model") else ""))
        cc = res.get("cross_check")
        if cc:
            st["cross_check"] = cross_check_state(cc)
            st["tried"].append(f"cross-check ({cc['mode']}): {cc['status']}: {CC.summary_line(cc)}")
        if url:                                       # (a PDF from the [datasheet] folder of a line with no link has none)
            st["found"].setdefault("datasheet", (url, f"datasheet reader ({where})"))
        st["verify"].append(f"datasheet reader output ({', '.join(got)}): compare with {url or where}; "
                            "see datasheet/*.review.md"
                            + (f" ({len(res['needs_review'])} items flagged)" if res["needs_review"] else ""))


def cross_check_state(cc):
    """The part's cross-check record in the plan: status and summary, and pins: unchecked when no second source
    compared the symbol's pin names (crosscheck.pins_status, review R-RT-2)."""
    from . import crosscheck as CC
    out = {"status": cc["status"], "summary": CC.summary_line(cc)}
    if cc.get("pins") == "unchecked":
        out["pins"] = "unchecked"
    return out


def kicad_chain_types(chain, pin_map=None):
    """{pin number: KiCad type} of a KiCad symbol's (extends chain's) pins whose type an Altium .SchLib cannot hold
    (power_out, no_connect): recorded with the plan's symbol, so a KiCad export that reads the .SchLib back (the source
    not reachable, or changed since the release) puts them back. pin_map: the conversion's {KiCad number: plan number}."""
    out = {}
    for s in chain or []:
        for u in sexpr.find(s, "symbol"):
            for p in sexpr.find(u, "pin"):
                num = sexpr.first(p, "number")
                if num and len(p) > 1 and str(p[1]) in ("power_out", "no_connect"):
                    n = str(num[1]).strip('"')
                    out[str((pin_map or {}).get(n, n))] = str(p[1])
    return out


def kicad_only_types(pins):
    """{pin number: KiCad type} for the pins whose type an Altium .SchLib cannot hold: power_out (Altium has one Power
    type) and no_connect (Altium has none, an NC pin is Passive there). The KiCad export puts them back on the read-back
    symbol, so ERC sees the rail a regulator's output drives."""
    k = {"power_out": "power_out", "nc": "no_connect"}
    return {str(q["num"]): k[t] for q in pins or [] if q.get("num") not in (None, "")
            for t in [SL._ptype(q.get("type"))] if t in k}


def pad_table_problems(spec):
    """A datasheet pad table's check errors as review lines ("datasheet pad table: ..."): why no footprint was built,
    in words the release check and ecadlib.messages explain (with the fix: ecadlib fp-edit)."""
    if not spec.get("pad_table"):
        return []
    from . import footprint_adv as FA
    try:
        errs = [p["message"] for p in FA.check_pad_table(spec) if p["level"] == "error"]
    except Exception:                                 # a table the layout rejects: footprint_gen.build says why
        return []
    names = sorted({str(p.get("number")) for p in spec["pad_table"].get("pads") or [] if p.get("number")},
                   key=lambda n: (len(n), n))
    return [f"datasheet pad table: {m}" + (f" - pads named {', '.join(names[:8])}{' ...' if len(names) > 8 else ''}"
                                           if m.startswith("no pad numbered 1") and names else "") for m in errs]


def _fingerprint(item, ln, row):
    return json.dumps([item["symbol"]["ref"], item["footprint"]["ref"], ln.get("mpn"), ln.get("lcsc"), ln.get("value"),
                       ln.get("footprint"), ln.get("datasheet"), row.get("local_symbol"), row.get("local_footprint")])


BUILD_REV = 3              # bump when a source's conversion changes what it writes (1: before the build_rev stamp; 3: descriptions)


def _built_now(st, rules):
    """True when this ecadlib (version and BUILD_REV) built the part with these rules. A held part built otherwise is
    built again on the next run: it is not in the library yet, and keeping it would make the release check judge the
    old version's models and fields (silk not cut back, no Description). Ready parts are kept as they are (updates
    never change a library's parts behind its back; `--refresh` rebuilds them)."""
    from .rebuild import rules_hash
    bw = st.get("built_with") or {}
    return bw.get("ecadlib") == __version__ and st.get("build_rev") == BUILD_REV and bw.get("rules_sha256") == rules_hash(rules)


def _done(st, out):
    return all(st.get(k) and (Path(out) / st[k]["file"]).exists() for k in ("symbol", "footprint"))


OWN_UNIT = {"Resistors": ("R", "kKMG"), "Capacitors": ("F", "pnuµμ"), "Inductors": ("H", "nuµμ")}


def passive_value(raw, cat):
    """A BOM value in the part type's own unit ('10kΩ', '100nF', '600R' is no inductance) or bare with a prefix only
    that type uses ('10k' resistor, '100n' cap, '4.7u' cap / inductor) -> float, else None: a bare number or EIA code
    ('104', '1002', '0.1') and another unit ('3.3V', '600R@100MHz' bead, '2A') are no value of it."""
    from .kicad_index import parse_value
    v, u = parse_value(raw, with_unit=True)
    unit, prefixes = OWN_UNIT[cat]
    m = re.match(r"\d+(?:[.,]\d+)?\s*(\D)", raw)
    return v if v is not None and (u == unit or not u and m and m.group(1) in prefixes) else None


def passive_description(item, st, ln):
    """A resistor / capacitor / inductor no source described, in a distributor's words from its own BOM line: 'RES 10kΩ
    1% 0603', 'CAP 100nF 16V X7R 0402' (value, tolerance, voltage, dielectric, power, package; nothing guessed). ''
    for any other part, or a value that is not a number: only a source describes those."""
    cat = str(item.get("folder") or "").rstrip("/").rsplit("/", 1)[-1]
    raw = str(_par(item).get("Value") or ln.get("value") or "").strip()
    v = passive_value(raw, cat) if cat in RU.PASSIVES and raw else None
    if v is None and cat in ("Capacitors", "Inductors") and raw:     # '100N', '4N7', '10U': BOM capitals
        v = passive_value(raw.lower(), cat)
    if v is None:
        return ""
    pkg = next((p for p in (package_of(str(t or "").split(":")[-1]) for t in (
        (st.get("footprint") or {}).get("name"), item.get("footprint", {}).get("kicad"), ln.get("footprint"))) if p), "")
    f = RU.line_fields(dict(ln, value=raw), cat, pkg)
    value = RU.fmt_number(v, cat, "si_unit") + ("Ω" if cat == "Resistors" else "")
    return " ".join(x for x in (f["cat3"], value, f["tolerance"], f["voltage"], f["dielectric"],
                                f["power"], pkg) if x)


def is_kicad_source(source):
    """A symbol or footprint taken from the KiCad libraries (CC-BY-SA 4.0), not generated or from your own files."""
    return str(source or "").startswith("kicad:")


def write_notice(out, items):
    """<out>/NOTICE.txt naming the third-party terms of what the plan holds (KiCad CC-BY-SA, EasyEDA's credit);
    removed again when nothing from those sources is left. Returns the path written, else None."""
    from . import easyeda_convert as EC, kicad_export as KX
    srcs = [str(s or "") for it in items for s in (it.get("source") or {}).values()]
    text = (KX.KICAD_NOTICE if any(is_kicad_source(s) for s in srcs) else "") + \
           (f"Parts converted from EasyEDA: {EC.CREDIT}. Keep this credit if you share the library.\n"
            if any(s.startswith("easyeda:") for s in srcs) else "")
    f = Path(out) / "NOTICE.txt"
    if not text:
        if f.is_file() and f.read_text(encoding="utf-8").startswith(("Parts of this library come from", "Parts converted from")):
            f.unlink()
        return None
    if f.is_file() and not f.read_text(encoding="utf-8").startswith(("Parts of this library come from", "Parts converted from")):
        return None                            # a NOTICE.txt you wrote is yours: never overwritten
    if not f.is_file() or f.read_text(encoding="utf-8") != text:
        write_text_atomic(f, text)
    return f


ALTERNATE = "variant alternate part"


def alternate_footprint(ln, st):
    """A project variant's alternate part (Kind=2, altium_project.apply_variant) keeps the base part's footprint:
    Altium's variation names no footprint for the alternate, and alternates usually share the land. When it does
    not (LM358DR SOIC-8 -> MCP6002T-I/MS, an MSOP), the part would be built on the wrong land, so the line is held
    for a person unless the alternate's own data confirms the land: its footprint was built from its own datasheet
    read, a person chose it (`ecadlib resolve`), or its datasheet read names the same checked package as the
    inherited footprint (stdparts.identify == stdparts.footprint_for). -> (verify line, review line); (None, None)
    for any other line."""
    from . import stdparts as SP
    fp = str(ln.get("footprint") or "").strip()
    if ln.get("altium_mpn_source") != "Alternate" or not fp:
        return None, None
    mpn, rec = ln.get("mpn") or "(no part number)", st.get("footprint") or {}
    if rec.get("chosen"):
        return None, f"{ALTERNATE} {mpn}: footprint chosen by a person"
    if st.get("alt_own_land") and str(rec.get("source") or "").startswith(("datasheet:", "standard:")):
        return None, f"{ALTERNATE} {mpn}: footprint built from its own datasheet read, not the base part's {fp}"
    try:
        want = SP.footprint_for([fp])[0]
    except Exception:
        want = None
    own = st.get("alt_read_package")
    if want and own == want:
        return None, (f"{ALTERNATE} {mpn}: the base part's footprint {fp} kept: {mpn}'s datasheet read names the same "
                      "package")
    mk, ms = SP.mpn_size(ln.get("mpn"))                # a passive whose part number codes its chip size (RC0603FR..,
    if ms and not SP.own_land_name(fp):                # GRM155..): that size and kind on a plain chip land (R-FX10-5)
        m = re.match(r"^(R|C|L)_(\d{4,5})_\d{4,5}Metric$", want or "")
        kind, size = (m.group(1), m.group(2)) if m else (st.get("kind"), SP.chip_size(fp, strict=True))
        if size == ms and kind == mk:
            return None, f"{ALTERNATE} {mpn}: the base part's footprint {fp} kept: {mpn} codes the same {ms} {mk} chip"
    return (f"{ALTERNATE} {mpn}: footprint {fp} is the base part's (an Altium variant names no footprint for its "
            "alternate)" + (f", and {mpn}'s datasheet read names {own}" if own else "") +
            f": check {mpn} comes in that package, or give the alternate its own footprint"), None


def explicit_mount_pads(spec):
    """How many unnumbered copper pads the read's pad table itself calls mounting / shield pads (role mount or
    shield, on the row or default_pad): the release check holds any other unnumbered copper pad (R-SY-2)."""
    t = (spec or {}).get("pad_table") or {}
    base = t.get("default_pad") or {}
    n = 0
    for p in t.get("pads") or []:
        q = dict(base, **{k: v for k, v in p.items() if v is not None})
        if str(q.get("number") if q.get("number") is not None else "").strip() == "" and q.get("type") != "npth" \
                and q.get("role") in ("mount", "shield"):
            n += 1
    return n


def apply_state(item, st, ln):
    """Copy a part's state into its plan item: model files, sources, filled-in fields, what was tried."""
    for k in ("symbol", "footprint"):
        if st.get(k):
            item[k]["file"] = st[k]["file"]
            if k == "symbol":                          # the pin types Altium cannot hold (power_out, no_connect)
                if st[k].get("kicad_types"):
                    item[k]["kicad_types"] = dict(st[k]["kicad_types"])
                else:
                    item[k].pop("kicad_types", None)
            if k == "footprint":
                item[k]["kicad_mod"] = st[k].get("kicad_mod")
                if st[k].get("mount_pads"):            # the pad table's own unnumbered mounting pads (R-SY-2)
                    item[k]["mount_pads"] = st[k]["mount_pads"]
                else:
                    item[k].pop("mount_pads", None)
                if st[k].get("model"):                 # where its 3D model came from (EasyEDA's STEP, with the credit)
                    item[k]["model"] = st[k]["model"]
                else:
                    item[k].pop("model", None)
                was = item[k].get("bom_ref") or item[k].get("ref")
                if st[k].get("name") and st[k].get("renamed_from") == was:     # another footprint has the BOM's name
                    item[k]["bom_ref"], item[k]["ref"] = was, st[k]["name"]
    item["source"] = {k: (st.get(k) or {}).get("source") for k in ("symbol", "footprint")}
    par, fields = _par(item), {}
    if ln.get("datasheet", "").startswith("http"):
        _set_par(item, "ComponentLink2Description", "Datasheet"); _set_par(item, "ComponentLink2URL", ln["datasheet"]); fields["ComponentLink2URL"] = "BOM"
    elif par.get("ComponentLink2URL"):
        fields["ComponentLink2URL"] = "LCSC datasheet URL pattern (not fetched)"
    elif "datasheet" in st.get("found", {}):
        u, w = st["found"]["datasheet"]
        _set_par(item, "ComponentLink2Description", "Datasheet"); _set_par(item, "ComponentLink2URL", u); fields["ComponentLink2URL"] = w
    if not par.get("Supplier Part Number 1") and "lcsc" in st.get("found", {}):     # an LCSC number the BOM did not give
        v, w = st["found"]["lcsc"]
        _set_par(item, "Supplier 1", "LCSC"); _set_par(item, "Supplier Part Number 1", v); fields["Supplier Part Number 1"] = w
        if not par.get("ComponentLink1URL"):
            _set_par(item, "ComponentLink1Description", "LCSC")
            _set_par(item, "ComponentLink1URL", f"https://www.lcsc.com/product-detail/{v}.html"); fields["ComponentLink1URL"] = w
    if not RC._filled(par.get("Manufacturer")) and "manufacturer" in st.get("found", {}):   # empty, or "-" / "N/A"
        v, w = st["found"]["manufacturer"]
        _set_par(item, "Manufacturer", v); fields["Manufacturer"] = w
    credits = [st["found"]["credit"]] if "credit" in st.get("found", {}) else []   # EasyEDA asks for credit on every reuse
    kicad = [k for k in ("symbol", "footprint") if is_kicad_source(item["source"][k])]
    if kicad:                                    # so does KiCad's CC-BY-SA library
        credits.append((K.KICAD_CREDIT, f"KiCad library {' and '.join(kicad)}"))
    if credits:
        _set_par(item, "Library Credit", "; ".join(v for v, _ in credits)); fields["Library Credit"] = "; ".join(w for _, w in credits)
    if not str(item.get("description") or "").strip() and "description" in st.get("found", {}):
        item["description"], fields["Description"] = st["found"]["description"]
    if not str(item.get("description") or "").strip():          # no source gave one: a passive's own BOM line, else
        desc = passive_description(item, st, ln)                # empty (held: a person types it); never made up
        if desc:
            item["description"], fields["Description"] = desc, "the BOM line's value and package"
    item["field_sources"] = fields
    item["verify"] = list(st.get("verify", []))
    alt_hold, alt_note = alternate_footprint(ln or {}, st)
    if alt_hold:
        item["verify"].append(alt_hold)
    if st.get("cross_check"):                     # ecadlib.crosscheck: agreed / disagreed (held) / unchecked / skipped
        item["cross_check"] = dict(st["cross_check"])
    else:
        item.pop("cross_check", None)
    item["librarian"] = {"tried": st.get("tried", []),
                         "review": list(dict.fromkeys(st.get("review", []) + item.get("bom_notes", []) +
                                                      ([alt_note] if alt_note else []))),
                         "notes": [n for k in ("symbol", "footprint") for n in (st.get(k) or {}).get("notes", [])]}


def review_rows(items):
    rows = []
    for it in items:
        if it.get("a365") and it.get("status") in ("existing", "deleted_in_workspace"):
            rows.append({"part": it["name"], "status": it["status"], "symbol_from": (it.get("source") or {}).get("symbol") or "",
                         "footprint_from": (it.get("source") or {}).get("footprint") or "",
                         "needs_human": "yes" if it["status"] != "existing" else "",
                         "why": "deleted in the Altium 365 workspace" if it["status"] != "existing" else "", "tried": ""})
            continue
        why = [f"no {k} found" for k in ("symbol", "footprint") if not it[k].get("file")]
        if why:
            why += it["librarian"]["tried"]
        why += [h for h in it.get("hold_reasons", []) if not re.match(r"files: no (symbol|footprint) model", h)] + it["librarian"]["review"]
        ch = it.get("change") or {}
        if ch.get("state") == "held":              # change control: the released revision stays until a person approves
            why.append(f"change to released r{ch.get('base')} held ({ch.get('summary')}): ecadlib part show plan.json {it['name']}")
        rows.append({"part": it["name"], "status": it["status"], "symbol_from": it["source"]["symbol"] or "",
                     "footprint_from": it["source"]["footprint"] or "", "needs_human": "yes" if why else "",
                     "why": "; ".join(dict.fromkeys(why)), "tried": "; ".join(it["librarian"]["tried"]),
                     "cross_check": (it.get("cross_check") or {}).get("status", "")})
    return rows


def refresh_review(plan, items):
    """review.csv / .md / .html again after a person changed the plan outside a librarian run (resolve, release-check
    --allow, review, approve), so they say what plan.json says. Only where a librarian run wrote them."""
    out = Path(plan).parent
    if not (out / "review.md").is_file():
        return
    try:
        bom = str(out / json.loads((out / "library.lock.json").read_text(encoding="utf-8"))["bom"]["file"])
    except (OSError, ValueError, KeyError, TypeError):
        bom = str(plan)
    write_review(review_rows(items), out, bom, items)


def write_review(rows, out, bom, items=None):
    """review.csv (machine columns, unchanged), review.md and review.html (ecadlib.reports: outcome first, held parts
    with a plain reason and the next step). items: the plan items (default: <out>/plan.json, written just before)."""
    out = Path(out)
    with open(out / "review.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = SS.DictWriter(f, fieldnames=list(rows[0]) if rows else ["part"])
        w.writeheader(); w.writerows(rows)
    if items is None:
        try:
            items = json.loads((out / "plan.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            items = [{"name": r["part"], "status": r["status"], "source": {"symbol": r["symbol_from"], "footprint": r["footprint_from"]},
                      "librarian": {"tried": r["tried"].split("; ") if r["tried"] else [], "review": []}} for r in rows]
    src = lambda s: M.SOURCES.get((s or "-").split(":")[0], "none")
    counts = {}
    for r in rows:
        k = (src(r["symbol_from"]), src(r["footprint_from"]))
        counts[k] = counts.get(k, 0) + 1
    from . import reports
    details = ["### Where the parts came from", "", "| Symbol from | Footprint from | Parts |", "|---|---|---|"]
    details += [f"| {a} | {b} | {n} |" for (a, b), n in sorted(counts.items())]
    details += ["", "### What was tried, per part", ""] + [f"- **{r['part']}**: " + ("; ".join(reports.tried_line(t) for t in r["tried"].split("; ")) or "-") for r in rows]
    details += CC.review_lines(rows)          # which datasheet parts were cross-checked, agreed, or held
    held_ch = [i for i in items if (i.get("change") or {}).get("state") == "held"]
    if held_ch:                               # change control: what a refresh wanted to change in released parts
        details += ["", "### Changes to released parts, held", "",
                    "The released revision is kept. `ecadlib part show plan.json PART` shows the change; "
                    "`ecadlib part approve` / `reject` decides it.", ""]
        for i in held_ch:
            ch = i["change"]
            details.append(f"- **{i['name']}** (r{ch.get('base')} kept, {', '.join(f'{n} {c}' for c, n in (ch.get('classes') or {}).items())})")
            details += [f"  - {c['class']}: {c['text']}" for c in (ch.get("changes") or [])[:8]]
            if len(ch.get("changes") or []) > 8:
                details.append(f"  - ... {len(ch['changes']) - 8} more")
    from . import ds_route
    details += ds_route.review_lines(out)     # datasheet reads: model, pages, tokens and USD per answer
    from . import fallbacks
    reports.write_review_files(items, out, bom, details, fallbacks.take())


def _desc_label(row):
    """Who wrote the part search's description: LCSC/JLCPCB for the unofficial routes, else the official API's name."""
    src = row.get("src_description_from") or row.get("src") or ""
    return "LCSC/JLCPCB" if src in ("", "jlcpcb_search", "easyeda") else src


def handoff_says(reader, progress):
    """The datasheet hand-off ("save the PDF to ...") prints above the live progress line, never through the
    per-step log (on a terminal that log keeps only a few lines)."""
    hand = getattr(getattr(reader, "store", None), "handoff", None)
    if hand is not None and hasattr(getattr(progress, "t", None), "write"):
        hand.say = progress.t.write


def run(bom, libs=(), out="out/librarian", sources=None, refresh=None, refresh_index=False, allow=(), offline=False,
        cache="out/http_cache", rate=0.5, providers=None, http=None, api_key=None, log=print, gen_step=True,
        rules=None, user=None, datasheet_batch=False, reader=None, progress=None):
    """Whole pipeline; returns the plan items. datasheet_batch: parts that reach the datasheet source are read as one
    Message Batch after every other source ran (reader: a ready datasheet.Reader, e.g. for --no-wait / --estimate). refresh: None = resume, [] = redo all, [names] = redo those.
    rules: library rules (a loaded dict or a path); None uses the ecadlib.toml found from `out` (defaults when none).
    sources: None = the rules' [sources] order. user: who gives the --allow (default: approvals.identity()).
    progress: a term.Progress-like object (start(name) / done(name, status, detail)) for the per-part line."""
    out = Path(out)
    rules = RU.resolve(rules, out)
    lines, mapping = T.read_bom(bom, RU.bom_columns(rules))       # first: an unreadable BOM writes nothing
    if not lines:                                                 # (no -o folder, no HTTP cache)
        raise ValueError(f"{bom}: the BOM has a header but no part rows")
    out.mkdir(parents=True, exist_ok=True)
    from . import migrate as MG, rebuild as LK           # versioned files (backed up, migrated) and library.lock.json
    MG.open_folder(out, log=log)
    sources = list(sources) if sources is not None else list(rules["sources"]["order"])
    if not P.unofficial_allowed(rules) and "easyeda" in sources:
        sources.remove("easyeda")                      # [providers] unofficial = false: no undocumented endpoints
        log("  easyeda source skipped: [providers] unofficial = false")
    http = http or CachedHTTP(cache, min_interval=rate, offline=offline, timeout=60)
    bad = set(sources) - set(SOURCES)
    if bad:
        raise ValueError(f"unknown sources {sorted(bad)}; use {','.join(SOURCES)}")
    from .approvals import load_policy
    load_policy(out)                                   # a broken approval policy stops the run before anything is written
    if "standard" in sources and "kicad" in sources and sources.index("standard") < sources.index("kicad"):
        sources.remove("standard")                     # never ahead of the KiCad library
        sources.insert(sources.index("kicad") + 1, "standard")
    if "standard" in sources and "datasheet" in sources and sources.index("datasheet") < sources.index("standard"):
        from .llm import run as LR                     # rail prefer_standard: a guided (smaller) model reads datasheets
        if LR.wants(rules, "prefer_standard", probe=not offline):   # only for what the offline standard parts can't build
            sources.remove("standard")
            sources.insert(sources.index("datasheet"), "standard")
    from . import timing as TI                       # wall time per step (timing.jsonl, `ecadlib status`)
    t_run = time.time()
    log(f"  BOM: {len(lines)} lines; looking them up in your libraries and the part sources (triage) ...")
    index = KiCadIndex(libs)
    from . import plugins as PL                      # providers: None = [providers] order, else P.DEFAULT_ORDER
    provs = [c(http) for c in PL.select(providers, rules, default=P.DEFAULT_ORDER)]
    rows = T.triage(lines, index, provs, http)
    T.write_reports(rows, mapping, provs, http, out / "triage", bom)
    t_build = time.time()
    TI.record(out, "triage", t_run, t_build, new_run=True, bom=Path(bom).name, lines=len(lines))
    state_f, plan_f = out / "librarian_state.json", out / "plan.json"
    state = json.loads(state_f.read_text(encoding="utf-8")) if state_f.exists() else {}
    old = {i["name"]: i for i in json.loads(plan_f.read_text(encoding="utf-8"))} if plan_f.exists() else {}
    from .altium_export import prior_names           # a part keeps its name when a line is added above it
    items = A365.plan(lines, pd.DataFrame(rows, dtype=str).fillna("") if rows else None, rules=rules,
                      prior=prior_names(old.values()))
    from . import change_gate as CG                  # change control: released parts are compared with their ledger head
    gate = CG.Guard(out, "librarian", rules=rules, user=user, log=log)
    gate.begin(list(old.values()))
    if reader is None and "datasheet" in sources:
        reader = D.Reader(out, cache, api_key, rules, http, batch=datasheet_batch, log=log)
    handoff_says(reader, progress)
    if reader is not None and offline and not getattr(reader, "replay", None):   # --offline: nothing goes to the model
        reader.offline, reader.key = True, None                                # (a replay of recorded answers sends nothing)
    r = Run(out, http, index, libs, list(sources), api_key, Path(cache) / "kicad_library_index.json", refresh_index, gen_step, rules,
            reader)
    todo = []
    for it in items:
        ln, row = _line_for(it, lines, rows)
        if not it["footprint"]["ref"]:
            it["footprint"]["ref"] = AL.lib_name(it["name"])
        fpr, st = _fingerprint(it, ln, row), state.get(it["name"])
        redo = refresh is not None and (not refresh or it["name"] in refresh)
        stale = bool(st) and (old.get(it["name"]) or {}).get("status") == "held" and not _built_now(st, rules)
        if stale and st.get("key") == fpr and _done(st, out) and not redo:
            bw = st.get("built_with") or {}
            why = (f"built by ecadlib {bw['ecadlib']}" if bw.get("ecadlib") and bw["ecadlib"] != __version__ else
                   "built with other rules" if st.get("build_rev") == BUILD_REV else "built by an older ecadlib")
            log(f"  {it['name']}: held, {why}: built again, so its models, fields and release check are this version's")
        if st and st.get("key") == fpr and _done(st, out) and not redo and not stale and not FS.unchecked_share(st.get("footprint")):
            st["skipped"] = True
            for k in ("symbol", "footprint"):
                r.written.setdefault(str(out / st[k]["file"]), st[k])
        else:
            todo.append((it, ln, row, fpr))
    redone = [old[it["name"]] for it, *_ in todo if it["name"] in old]
    if redone:                                 # a hand edit of a released part is held (kept) before it is built again
        gate.keep_edits(redone)
    keeps = {}
    for it, ln, row, fpr in todo:
        old_st = state.get(it["name"]) or {}
        keeps[it["name"]] = keep = {k: old_st[k] for k in ("symbol", "footprint")
                                    if (old_st.get(k) or {}).get("chosen") and (out / old_st[k]["file"]).exists()}
        for rec in keep.values():          # a chosen file another part shares is reused, never overwritten by its fetch
            r.written.setdefault(str(out / rec["file"]), rec)
    r.log = log
    rec = LK.Recorder(http)                    # what each part fetched, for library.lock.json
    if todo:
        log(f"  finding symbols and footprints for {len(todo)} part(s) (sources: {', '.join(sources)}) ...")
    if progress is not None:
        progress.total = len(todo)

    def save_state():                          # finished parts survive a cancel or a kill (a datasheet batch not sent
        held = {id(w[0]) for w in r.waiting}   # yet is not finished)
        write_text_atomic(state_f, json.dumps({k: v for k, v in state.items() if id(v) not in held}, indent=2))
        MG.write_stamp(out, ["librarian_state"])
    saved = time.time()
    try:
        for n, (it, ln, row, fpr) in enumerate(todo, 1):
            if progress is not None:
                progress.start(it["name"])
            elif len(todo) > 3:
                log(f"  [{n}/{len(todo)}] {it['name']}")
            with rec.part(it["name"]):
                st = r.resolve(it, ln, row, keeps[it["name"]])
            st["key"], st["skipped"] = fpr, False
            LK.stamp_state(st, rec.take(it["name"]), rules)
            st["build_rev"] = BUILD_REV
            state[it["name"]] = st
            if progress is not None:
                gone = [k for k in ("symbol", "footprint") if not st.get(k)]
                progress.done(it["name"], "held" if gone else "ready",
                              f"no {' or '.join(gone)} yet" if gone else "symbol and footprint found")
            if time.time() - saved > 2:
                save_state()
                saved = time.time()
    except BaseException:                      # stopped (job_status cancel, Ctrl+C): keep what is finished
        if todo:
            save_state()
        raise
    r.flush_datasheets(log)                    # --datasheet-batch: one batch for every part that got this far
    if todo:
        save_state()                           # a stop while the summary is logged keeps them too
    from . import ledger as LG                 # the library lock across the whole block, then plan.lock (fsutil's order):
    with LG.locked(out), _decisions_lock(plan_f):   # the gate steps below let go of their own hold between settle and baseline
        # decisions people recorded while this run looked parts up (the web UI,
        old = _fresh_decisions(old, plan_f)    # `ecadlib approve`) are kept, not overwritten with the run's old copy
        for it in items:
            apply_state(it, state[it["name"]], _line_for(it, lines, rows)[0])
            for k in DECISIONS:                                  # decisions people made survive re-runs
                if old.get(it["name"], {}).get(k):
                    it[k] = old[it["name"]][k]
            prev = old.get(it["name"]) or {}
            for k, was in (prev.get("merged_from") or {}).items():   # `ecadlib dedupe --merge` pointed this half at a keeper
                if k in ("symbol", "footprint") and it[k].get("ref") == was.get("ref") and (prev.get(k) or {}).get("file") \
                        and RC._path(out, prev[k]["file"]).exists():
                    it[k] = dict(prev[k])
                    it.setdefault("merged_from", {})[k] = was
            log(f"  {'skip ' if state[it['name']].get('skipped') else 'done ' if _done(state[it['name']], out) else 'open '}"
                f"{it['name']:<28} sym={it['source']['symbol'] or '-'}  fp={it['source']['footprint'] or '-'}")
        state = {k: v for k, v in state.items() if k in {i["name"] for i in items}}
        for n in set(allow) - set(state):
            log(f"  --allow {n}: not a part in this plan (names are the plan's Part Numbers)")
        write_text_atomic(state_f, json.dumps(state, indent=2))
        from . import house_style as HS                 # new parts in your house style (house_style.json, [style] apply)
        prof = HS.active(rules, out)
        if prof:
            styled = HS.adapt_items(items, prof, rules)
            if styled:
                log(f"  style {len(styled)} part(s) written in your house style (descriptions, value formats)")
        gate.settle(items)                         # held changes put the released files and fields back first
        t_check = time.time()
        TI.record(out, "build", t_build, t_check, parts=len(todo))
        checks = RC.apply(items, out, allow, rules=rules)
        if allow:                                  # record who gave the --allow, so approvals can refuse self-approval
            from .approvals import identity, post_check, stamp_allow
            stamp_allow(items, allow, identity(user, cwd=out))
            post_check(items, out, checks)
        gate.baseline(items)                       # parts released for the first time: revision 1 in the ledger (a new
                                                   # part with a released part's MPN is compared with it first)
        gate.retire([n for n in old if n not in {i["name"] for i in items}], f"no longer in the plan ({Path(bom).name})")
        CG.record_bom_usage(out, bom, items)
        write_text_atomic(plan_f, json.dumps(items, indent=2))
    write_notice(out, items)
    rec.close()
    MG.write_stamp(out, ["plan", "librarian_state"])
    LK.write_lock(out, items, state, bom, libs, sources, cache, providers, gen_step, rules)
    RC.write_report(checks, out / "release_check", plan_f)
    write_review(review_rows(items), out, bom, items)
    TI.record(out, "release_check", t_check, held=sum(i.get("status") == "held" for i in items))
    return items


DECISIONS = ("allow", "approvals", "duplicate_of")    # what people decide on a plan item: a re-run keeps it


def _decisions_lock(plan_f):
    """The workspace's plan lock (<ws>/plan.lock, which every decision recorder takes) while the run settles and
    writes its plan, for a plan in a workspace (<ws>/librarian/<name>/plan.json); else no lock."""
    import contextlib
    from .approvals import plan_workspace
    from .fsutil import plan_lock
    ws = plan_workspace(plan_f)
    return plan_lock(ws, wait=True) if ws is not None else contextlib.nullcontext()


def _fresh_decisions(old, plan_f):
    """old (the plan as the run started, by name) with each part's decisions as plan.json has them now: a reject,
    approval, release-anyway or hold-again recorded during the run wins over the copy read at its start."""
    try:
        now = {i["name"]: i for i in json.loads(plan_f.read_text(encoding="utf-8"))}
    except (OSError, ValueError, TypeError, KeyError):
        return old
    out = dict(old)
    for n, it in now.items():
        if n in out and isinstance(it, dict):
            out[n] = {**{k: v for k, v in out[n].items() if k not in DECISIONS}, **{k: it[k] for k in DECISIONS if k in it}}
    return out


def keep_decisions(items, plan_f):
    """In place: each plan item's decisions as plan.json has them now (a step that read the plan long ago, such as a
    datasheet batch, must not write back the old ones). Call under _decisions_lock(plan_f). -> the items whose
    decisions changed."""
    by = {i["name"]: i for i in items if isinstance(i, dict) and i.get("name")}
    changed = []
    for n, fresh in _fresh_decisions(dict(by), plan_f).items():
        it = by[n]
        was = {k: it.get(k) for k in DECISIONS}
        for k in DECISIONS:
            if k in fresh:
                it[k] = fresh[k]
            else:
                it.pop(k, None)
        if was != {k: it.get(k) for k in DECISIONS}:
            changed.append(it)
    return changed


def save_plan(plan_f, items, rules=None, keep=None):
    """Write a librarian plan that a step read earlier (change control's `ecadlib part approve`, a publisher's settle,
    `ecadlib style apply`) while other front ends may record decisions into it (the web UI's Approvals, `ecadlib
    approve`, release anyway): under the plan lock, each part's decisions are taken as plan.json has them now, and a
    part whose decisions changed meanwhile gets the release check again, so a reject recorded meanwhile leaves it
    not ready. keep(item): which items go into the file (default all). Written atomically."""
    from . import release_check as RC
    plan_f = Path(plan_f)
    with _decisions_lock(plan_f):
        changed = keep_decisions(items, plan_f)
        if changed:
            RC.apply(changed, plan_f.parent, rules=rules)
        write_text_atomic(plan_f, json.dumps([i for i in items if keep is None or keep(i)], indent=2))
    return changed


def main(argv=None):
    ap = argparse.ArgumentParser(description="BOM -> library: triage, fill missing symbols/footprints, Altium models, plan, release check")
    ap.add_argument("bom")
    ap.add_argument("--lib", action="append", default=[], help="local KiCad library dir (repeatable)")
    ap.add_argument("-o", "--out", help="plan folder (default: <[setup] workspace>/librarian/<BOM name> after "
                    "`ecadlib init`, else out/librarian)")
    ap.add_argument("--sources", help="order of sources to try (default: the rules' [sources] order, else " + ",".join(SOURCES) + ")")
    ap.add_argument("--refresh", nargs="*", metavar="PART", help="redo finished parts: all, or the named ones")
    ap.add_argument("--refresh-index", action="store_true", help="rebuild the KiCad library name index")
    ap.add_argument("--allow", action="append", default=[], metavar="PART", help="release PART despite its release-check failures")
    ap.add_argument("--providers", help="triage providers in order (default: [providers] order, else nexar,digikey,mouser,jlcpcb_search,easyeda)")
    ap.add_argument("--offline", action="store_true", help="use the HTTP cache only")
    ap.add_argument("--cache", default="out/http_cache")
    ap.add_argument("--rate", type=float, default=0.5, help="min seconds between requests per host")
    ap.add_argument("--box", action="store_true", help="extruded 3D box instead of a generated STEP when there is no KiCad model")
    ap.add_argument("--user", help="who gives the --allow (default: $ECADLIB_USER, then git config user.name/email)")
    ap.add_argument("--rules", help="library rules file (default: ecadlib.toml found from -o or here; see `ecadlib init`)")
    ap.add_argument("--datasheet-batch", action="store_true",
                    help="read every datasheet still needed as one Message Batch (half price; waits for it, see --datasheet-no-wait)")
    ap.add_argument("--datasheet-no-wait", action="store_true", help="submit the batch and finish; re-run to collect it")
    ap.add_argument("--json", action="store_true", help='print {"plan", "review", "parts", "ready", "held", "blockers"} instead of the log')
    a = ap.parse_args(argv)
    from . import firstrun as FR
    given_out = bool(a.out)
    a.out = a.out or FR.default_out("librarian", a.bom)
    other = _plan_bom(a.out)                  # the folder holds another BOM's plan: say so before it is replaced
    if other and other != Path(a.bom).name and not a.json:
        print(f"note: {a.out} holds the plan for {other}; it is replaced by {Path(a.bom).name}'s "
              f"(give each BOM its own -o folder to keep both)", file=sys.stderr)
    rules = RU.load(a.rules, start=FR.rules_start(a.out, given_out))
    reader = None
    if a.datasheet_batch:
        reader = D.Reader(a.out, a.cache, None, rules, CachedHTTP(a.cache, min_interval=a.rate, offline=a.offline, timeout=60),
                          batch=True, wait=not a.datasheet_no_wait)
    from . import messages as M, term as TM
    t = TM.Term()
    loud = (lambda *x, **k: None) if a.json or t.quiet else print
    # on a terminal the per-step log is -v detail (the live progress line and the table below say it); piped, as before
    log = loud if (t.verbose or not t.styled) else (lambda m="", *x, **k: print(m) if str(m).startswith(("  BOM:", "  finding")) else None)
    prog = TM.Progress(t.err, 0, "parts") if t.styled and not a.json else None   # piped: the plain log lines
    try:
        items = run(a.bom, a.lib, a.out, [s for s in a.sources.split(",") if s] if a.sources else None, a.refresh,
                    a.refresh_index, a.allow, a.offline, a.cache, a.rate, a.providers, gen_step=not a.box, rules=rules,
                    user=a.user, http=reader.http if reader else None, reader=reader,
                    log=log, progress=prog)
    except ValueError as e:                              # unreadable / headerless BOM, unknown --sources
        from .firstrun import bom_hint
        print(f"ecadlib librarian: {e}" + bom_hint(e), file=sys.stderr)
        return 2
    from .firstrun import blockers
    held = sum(i["status"] != "ready" for i in items)
    why = blockers(review_rows(items), rules, a.offline)
    if a.json:
        print(json.dumps({"plan": str(Path(a.out) / "plan.json"), "review": str(Path(a.out) / "review.md"), "parts": len(items),
                          "ready": len(items) - held, "held": held, "blockers": [{"message": m, "fix": f} for m, f in why]}))
        return 2 if held else 0
    if prog is not None:
        prog.t.clear_live()
    print_result(items, Path(a.out), why, t, tool=(rules.get("setup") or {}).get("tool") if isinstance(rules, dict) else None)
    return 2 if held else 0


def _plan_bom(out):
    """The file name of the BOM whose plan is in `out` (library.lock.json), or None."""
    try:
        return Path(str(json.loads((Path(out) / "library.lock.json").read_text(encoding="utf-8"))["bom"]["file"])).name
    except (OSError, ValueError, KeyError, TypeError):
        return None


def print_result(items, out, why=(), t=None, tool=None):
    """The terminal ending of a librarian run: a row per part on a terminal (status, name, the reason in plain
    words), then notes, the counts, what was written and the next commands."""
    from . import messages as M, term as TM
    t = t or TM.Term()
    if t.styled or t.verbose:
        rows = []
        for it in items:
            why_ = M.explain_all(it.get("hold_reasons") or []) if it.get("status") == "held" else []
            src = it.get("source") or {}
            rows.append([t.status(it.get("status") or "", M.status_word(it.get("status"))), it["name"],
                         why_[0]["text"] if why_ else t.dim(f"{src.get('symbol') or '-'} / {src.get('footprint') or '-'}")])
        t.table(rows)
    held = [i["name"] for i in items if i.get("status") == "held"]
    changed = [i["name"] for i in items if (i.get("change") or {}).get("state") == "held"]
    plan = out / "plan.json"
    from .cliutil import shell_word as q             # a folder with a space stays one word when pasted
    nxt = []
    if held:
        nxt.append((f"ecadlib review {q(plan)}", "go through the held parts one by one"))
    if changed:
        nxt.append((f"ecadlib part status {q(plan)}", "changes to released parts, held until a person approves them"))
    # one KiCad library / Altium folder per plan, named after it: a second BOM's export never replaces the first one's
    ws = out.parent.parent if out.parent.name == "librarian" else Path(".")
    if tool in (None, "", "kicad", "both"):
        nxt.append((f"ecadlib kicad-export {q(plan)} -o {q(ws / 'kicad' / out.name)} --name {q(out.name)}", "write the KiCad library"))
    if tool in (None, "", "altium", "both"):
        nxt.append((f"ecadlib altium-pkg {q(plan)} --out {q(ws / 'altium_pkg' / out.name)}",
                    "the Altium hand-off folder (.LibPkg, DbLib)"))
    t.summary(f"{M.plural(len(items), 'part')}: {M.counts(i.get('status') for i in items)}"
              + (f"  (held: {', '.join(held[:5])}{' ...' if len(held) > 5 else ''})" if held else "")
              + (f"; {M.plural(len(changed), 'change')} to released parts held, the released version is kept "
                 f"({', '.join(changed[:5])}{' ...' if len(changed) > 5 else ''})" if changed else ""),
              written=[(str(plan), "library plan"), (str(out / "review.md"), "review: what to check, part by part"),
                       (str(out / "models") + "/", "Altium symbol and footprint files")],
              next=nxt, notes=[(m, f) for m, f in why])


if __name__ == "__main__":
    sys.exit(main())
