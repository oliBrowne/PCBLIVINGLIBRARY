"""Cheaper datasheet reads: only the pages that matter, a model per part, and what each part cost.

Used by datasheet.Reader (and so by the librarian, `ecadlib datasheet batch` and the workspace tools):

* pick_pages: for every PDF with a text layer, the pages most likely to hold the package drawing (for THIS part's
  package), the land pattern, the pin table and the ordering information that maps the MPN suffix to a package,
  within [datasheet] page_budget pages. levels() gives the widening steps: budget -> 3x budget -> the whole PDF.
* Router: [datasheet] route = "single" (one model for everything, as before) or "ladder" (a cheaper model for
  simple packages, escalating up [datasheet] ladder). difficulty() picks the first rung from the package family,
  pin count and page count.
* assess / next_step: after an answer, datasheet's own checks (schema_check, sanity, check_pins, self_check, the
  page citations) say ok / incomplete / failed. Incomplete widens the pages first; failed moves to the next model;
  the top of the ladder with every page is the last try, and its answer is kept with its review items.
* usage_usd / Ledger: USD per attempt from the API usage block (or an estimate), appended to
  <out>/datasheet/cost.jsonl; summarize() feeds the librarian review, `ecadlib status` and the estimate.

Prices: datasheet.PRICES (from the claude-api skill's model table, cached 2026-10-06), plus the cache-read prices
and the Haiku 5.5 long-prompt tier listed here."""
import argparse, datetime, io, json, re, sys
from pathlib import Path

# Cache reads where the model table gives a price that is not 0.1x input; Haiku 5.5 is dearer above 100K prompt tokens.
CACHE_READ = {"claude-opus-5-5": 0.20, "claude-sonnet-5-5": 0.20, "claude-fable-5-1": 0.25}
LONG_PROMPT = {"claude-haiku-5-5": (100_000, (0.50, 2.50))}
LADDER = ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"]
ROUTES = ("single", "ladder")

# ---------------------------------------------------------------- page picking
_DASH = str.maketrans({"−": "-", "–": "-", "—": "-", " ": " "})
FAMILY = {  # package family words -> a canonical family (SOIC-8 / SO8 / SOP-8 are one package)
    "SOIC": "SO", "SOP": "SO", "SO": "SO", "PSOP": "SO", "PDSO": "SO", "SSOP": "SSOP", "QSOP": "SSOP", "TSSOP": "TSSOP",
    "HTSSOP": "TSSOP", "MSOP": "MSOP", "VSSOP": "MSOP", "TSOP": "TSOP", "SOT": "SOT", "TSOT": "SOT", "SOD": "SOD",
    "SC": "SC", "TO": "TO", "DO": "DO", "DPAK": "TO252", "D2PAK": "TO263", "SMA": "SMA", "SMB": "SMB", "SMC": "SMC",
    "QFN": "QFN", "VQFN": "QFN", "WQFN": "QFN", "UQFN": "QFN", "HVQFN": "QFN", "UFQFPN": "QFN", "VFQFPN": "QFN",
    "DFN": "SON", "SON": "SON", "WSON": "SON", "VSON": "SON", "HVSON": "SON", "USON": "SON", "XSON": "SON",
    "QFP": "QFP", "LQFP": "QFP", "TQFP": "QFP", "PQFP": "QFP", "HTQFP": "QFP", "BGA": "BGA", "UFBGA": "BGA",
    "LFBGA": "BGA", "TFBGA": "BGA", "FBGA": "BGA", "WLCSP": "CSP", "CSP": "CSP", "DIP": "DIP", "PDIP": "DIP",
    "SIP": "SIP", "LGA": "LGA"}
NEEDS_NUM = {"SO", "SC", "TO", "DO", "SOT", "SOD", "SIP", "SMA", "SMB", "SMC"}   # too common as plain words
_FAM_RX = re.compile(r"(?<![A-Z0-9])(" + "|".join(sorted(FAMILY, key=len, reverse=True)) + r")(\s?-\s?|\s)?(\d{1,3})?(?:\s?-\s?(\d{1,3}))?(?![0-9])")
THREE_PIN = {"SOT23", "SOT323", "SOT523", "SOT416", "SOT346", "SC59", "SC70", "SC75", "SC89", "TO92", "SOT89"}
OUTLINE_FAMS = {"SOT", "SOD", "SC", "TO", "DO", "TO252", "TO263"}             # first number names the outline, not pins
CATS = {
    "drawing": re.compile(r"package (outline|drawing|dimensions?|information)|outline dimensions?|mechanical (data|drawing|"
                          r"dimensions?)|physical dimensions?|shape (&|and) dimensions|case outline|packaging information|"
                          r"dimensions\s*(-|in)\s*(mm|millimet)|chip resistor outlines|package profile|外形尺寸|封装尺寸", re.I),
    "land": re.compile(r"land pattern|board layout|recommended (footprint|land|pcb|solder|pad)|(suggested|recommended) pad layout|"
                       r"soldering footprint|solder lands|\bfootprint\b|pad layout|soldering pad|焊盘", re.I),
    "pinout": re.compile(r"pin (configurations?|functions?|descriptions?|assignments?|definitions?)|pinning information|"
                         r"\bpinouts?\b|terminal functions?|\(top view\)|top view|引脚", re.I),
    "ordering": re.compile(r"ordering information|order(ing)? code|orderable|package option addendum|device comparison|"
                           r"ordering|part number|订购|订货", re.I)}
TABLE = {"drawing": re.compile(r"\b(min\.?|minimum)\b[\s\S]{0,80}\b(max\.?|maximum)\b", re.I),
         "pinout": re.compile(r"\bpin\s*(no\.?|number|#)|\bno\.\s+name\b|\bname\s+(type|i/o)\b|pin name", re.I)}
DIM_SYMS = re.compile(r"(?<![A-Za-z0-9])(A1|A2|D1|E1|D2|E2)(?![A-Za-z0-9])")
NOISE = re.compile(r"tape and reel|reel dimensions|carrier tape|package materials information|revision history|"
                   r"important notice|tape dimensions|stencil design", re.I)
STOP = {"ACTIVE", "NRND", "OBSOLETE", "ROHS", "GREEN", "LEVEL", "TAPE", "REEL", "TUBE", "TRAY", "TBD", "YES", "NO",
        "PB", "FREE", "MSL", "LIFEBUY", "PREVIEW", "NIPDAU", "SNAGCU", "SN", "CU", "AU", "MM", "MAX", "MIN", "TYP", "UNIT",
        "PCS", "QTY", "PART", "NUMBER", "DEVICE", "PACKAGE", "PINS", "PIN", "TYPE", "TEMP", "RANGE"}


def norm_text(t):
    return (t or "").translate(_DASH)


def package_tokens(text):
    """'SOIC-8_3.9x4.9mm' -> {'SO8'}, 'SOT-223-3_TabPin2' -> {'SOT223'}, 'LQFP-32_7x7mm' -> {'QFP32'}; a family
    without a number ('WSON') gives the family alone, unless the text also numbers it ("LQFP144, 144-lead LQFP" is
    QFP144 only: a family-only token would match every other LQFP)."""
    out = set()
    for m in _FAM_RX.finditer(norm_text(text).upper()):
        fam, sep, n1 = FAMILY[m.group(1)], m.group(2) or "", m.group(3)
        if fam in NEEDS_NUM and (not n1 or sep.strip() == "" and sep):   # "TO-220", "SO8"; never "to 85" or "SO 2"
            continue
        out.add(fam + (n1 or ""))
    return {t for t in out if re.search(r"\d$", t) or not any(o != t and re.sub(r"\d+$", "", o) == t for o in out)}


def _tok_match(own, page_toks):
    """A page token matches when it is the same package (family + number), or the same family when one side has none."""
    for a in own:
        for b in page_toks:
            if a == b:
                return True
            fa, fb = re.sub(r"\d+$", "", a), re.sub(r"\d+$", "", b)
            if fa == fb and (fa == a or fb == b) and fa not in OUTLINE_FAMS:
                return True
    return False


def mpn_variants(part):
    p = re.sub(r"\s+", "", str(part or "")).upper()
    out = {p} if len(p) >= 4 else set()
    if len(p) > 7 and p[-1] in "RTG":                 # a tape-and-reel / lead-free suffix the ordering table may leave off
        out.add(p[:-1])
    return out


def ordering_codes(texts, part):
    """Package words and drawing codes printed on the ordering-table lines that name this MPN (or, failing that, under
    the nearest package heading above them): 'AZ1117CH-3.3TRG1' -> {'SOT223'}, 'BQ771800DPJR' -> {'SON', 'DPJ'}."""
    vs = mpn_variants(part)
    toks, codes = set(), set()
    if not vs:
        return toks, codes
    for t in texts:
        last = set()
        for ln in norm_text(t).upper().splitlines():
            here = package_tokens(ln)
            flat = re.sub(r"\s+", "", ln)
            if any(v in flat for v in vs):
                toks |= here or last
                for w in re.findall(r"(?<![A-Z0-9])([A-Z]{2,4}|\d{2}[A-Z]\d?(?:-[A-Z])?|[A-Z]{1,4}\d{4}[A-Z])(?![A-Z0-9])", ln):
                    if w not in STOP and w not in FAMILY and not any(w == v for v in vs) and (
                            any(w in v for v in vs) or re.fullmatch(r"\d{2}[A-Z]\d?(?:-[A-Z])?", w)):
                        codes.add(w)
            if here:
                last = here
    return toks, set(sorted(codes)[:6])


def page_scores(texts, part=None, package=None):
    """Per page: {cat: score} plus 'own' (names this part's package or drawing code) and 'other' (names only another
    package). Table-of-contents pages and tape/reel/revision pages score low."""
    hint = package_tokens(package or "")
    otoks, codes = ordering_codes(texts, part)
    own = hint | otoks
    vs = mpn_variants(part)
    n, rows = len(texts), []
    first_mech = None
    for i, raw in enumerate(texts):
        t = norm_text(raw)
        s = {c: min(2, len(rx.findall(t))) * (3 if c != "ordering" else 2) for c, rx in CATS.items()}
        for c, rx in TABLE.items():
            if rx.search(t) and (c != "drawing" or (DIM_SYMS.search(t) and re.search(r"\bmm\b|millimet|\binch", t, re.I))):
                s[c] += 2                             # a dimension table: min/max with A1/D1/E1/b/e and a length unit
        flat = re.sub(r"\s+", "", t).upper()
        s["mpn"] = 4 if vs and any(v in flat for v in vs) else 0
        if s["mpn"] and s["ordering"]:
            s["ordering"] += 4
        ptoks = package_tokens(t)
        has_code = any(re.search(rf"(?<![A-Z0-9]){re.escape(c)}(?![A-Z]|\d(?![\dA-Z]))", t.upper()) for c in codes)
        s["own"] = bool((own and _tok_match(own, ptoks)) or has_code)
        s["other"] = bool(ptoks) and not s["own"] and bool(own or codes)
        s["solo"] = s["own"] and len(ptoks) <= 1      # names this package and no other
        if re.search(r"orderable|package option addendum|ordering information", t, re.I):
            s["drawing"] //= 3                        # "Package Drawing" is a column of the ordering table there
        toc = len(re.findall(r"\.{5,}|(?:\. ){5,}", t)) >= 6
        if toc or NOISE.search(t):
            for c in CATS:
                s[c] = s[c] // 4
        s["toc"] = toc
        if first_mech is None and (s["drawing"] or s["land"]) and i >= n // 3:
            first_mech = i
        s["blank"] = len(t.strip()) < 40
        rows.append(s)
    for i, s in enumerate(rows):                      # image-only drawing pages at the back of the document
        if s["blank"] and first_mech is not None and i > first_mech - 3 and i >= n // 2 and i < n - 1:
            s["drawing"] = max(s["drawing"], 3)
    return rows, own, codes


def _rank(s, cat):
    v = s[cat]
    if not v:
        return 0
    if cat in ("drawing", "land") and (s["own"] or s["other"]):
        v = v * (3 if s["solo"] else 1.5) if s["own"] else v * 0.3   # a page about this package alone beats a shared one
    if cat == "pinout" and s["own"]:
        v *= 1.5
    return v


def pick_pages(texts, budget, part=None, package=None):
    """-> (sorted 1-based pages, {page: [why]}) or (None, {}) when there is no text layer or nothing matched.
    The best page for each need (drawing, pin table, land pattern, ordering) goes in first, then the page after the
    drawing (drawings and their tables run over), then the rest by score, up to budget pages."""
    if not texts or not any((t or "").strip() for t in texts) or budget <= 0:
        return None, {}
    rows, own, codes = page_scores(texts, part, package)
    total = lambda i: (sum(_rank(rows[i], c) for c in CATS) + rows[i]["mpn"])
    if not any(total(i) for i in range(len(rows))):
        return None, {}
    keep, why = [], {}

    def add(i, reason):
        if 0 <= i < len(rows) and len(keep) < budget and i + 1 not in keep:
            keep.append(i + 1)
            why.setdefault(i + 1, []).append(reason)
        elif 0 <= i < len(rows) and i + 1 in keep:
            why.setdefault(i + 1, []).append(reason)

    best = {}
    for cat in ("drawing", "pinout", "land", "ordering"):
        cands = [i for i in range(len(rows)) if _rank(rows[i], cat) > 0 and not (cat in ("drawing", "land") and rows[i]["other"])]
        if cands:
            best[cat] = max(cands, key=lambda i: (_rank(rows[i], cat), total(i), -i))
            add(best[cat], cat)
    pins_own = [i for i in range(len(rows)) if rows[i]["own"] and _rank(rows[i], "pinout") > 0 and i != best.get("pinout")]
    if pins_own:                                      # a second pin page naming this package (pinout figure + pin table)
        add(max(pins_own, key=lambda i: (rows[i]["solo"], _rank(rows[i], "pinout"), -i)), "pinout (this package)")
    if "drawing" in best:                             # the drawing's continuation (table, notes, second view)
        j = best["drawing"] + 1
        if j < len(rows) and (rows[j]["drawing"] or rows[j]["land"] or rows[j]["blank"]) and not rows[j]["other"]:
            add(j, "drawing (continued)")
    for i in sorted(range(len(rows)), key=lambda i: (-total(i), i)):
        if total(i) <= 0 or len(keep) >= budget:
            break
        if not rows[i]["other"]:                      # a page about another package only comes with the whole PDF
            add(i, "score")
    if len(keep) < budget and 1 not in keep:          # page 1: summary, package list, often the pinout of small parts
        add(0, "first page")
    if own or codes:
        why["_package"] = sorted(own | codes)
    return sorted(keep), why


def levels(texts, budget, part=None, package=None, n_pages=None):
    """The page sets to try, smallest first: [pages] for budget, 3x budget, then None (= the whole PDF). A PDF that
    fits the budget, or has no text layer, gets [None] (whole, as before)."""
    n = n_pages if n_pages is not None else len(texts or [])
    if not budget or not n or n <= budget:
        return [None], {}
    out, why = [], {}
    for b in (budget, budget * 3):
        if b >= n:
            break
        got, w = pick_pages(texts, b, part, package)
        if got and (not out or len(set(got) - set(out[-1])) >= max(2, budget // 2)):   # a wider step must add pages
            out.append(got)
            why = why or w
    return out + [None], why


# ---------------------------------------------------------------- routing
def difficulty(package=None, n_pages=None, sent_pages=None):
    """0 simple / 1 medium / 2 hard, with the reason. From the package hint (KiCad-style footprint name or package
    text): two- and three-terminal parts are simple; SOIC/SOT/QFN/DFN/DIP up to 32 pins medium; more pins, BGA/LGA/
    CSP, connectors and unknown packages hard. A long PDF (over 40 pages) or many pages sent moves a part up one."""
    h = norm_text(package or "")
    up = h.upper()
    toks = package_tokens(h)
    m = re.search(r"(?<![A-Z0-9])(?:[A-Z]+)-(\d{1,3})(?![0-9])", up)
    pins = None
    for tk in toks:
        fam, num = re.sub(r"\d+$", "", tk), re.sub(r"^\D+", "", tk)
        if num and fam not in OUTLINE_FAMS:
            pins = int(num)
    if pins is None and m and not any(re.sub(r"\d+$", "", t) in OUTLINE_FAMS for t in toks):
        pins = int(m.group(1))
    sot = re.search(r"(?<![A-Z0-9])(SOT|SC|TO)-?\d+(?:-(\d+))?", up)
    if sot and sot.group(2):
        pins = int(sot.group(2))
    elif pins is None and toks & THREE_PIN:           # SOT-23, SOT-323, TO-92 ... with no count: three leads
        pins = 3
    if re.match(r"(R|C|L|D|LED|FB|CP|Fuse|Crystal|Resonator)_", h) or re.search(r"\b(0201|0402|0603|0805|1206|1210|2512)\b", h) \
            or any(t.startswith(("SOD", "SMA", "SMB", "SMC", "DO")) for t in toks):
        tier, why = 0, "two-terminal / chip package"
    elif any(t.startswith(("BGA", "LGA", "CSP")) for t in toks) or re.search(r"connector|header|socket|terminal|usb|jack", h, re.I):
        tier, why = 2, "BGA/LGA/CSP or connector"
    elif pins is not None and pins <= 3:
        tier, why = 0, f"{pins}-pin package"
    elif pins is not None and pins <= 32 and toks:
        tier, why = 1, f"{pins}-pin {sorted(toks)[0]}"
    elif toks and pins is None and any(re.sub(r"\d+$", "", t) in OUTLINE_FAMS for t in toks):
        tier, why = 1, f"{sorted(toks)[0]} outline"
    elif pins is not None:
        tier, why = 2, f"{pins} pins"
    else:
        tier, why = 2, "package unknown"
    if tier < 2 and ((n_pages or 0) > 40 or (sent_pages or 0) > 12):
        tier, why = tier + 1, why + f"; long datasheet ({n_pages or sent_pages} pages)"
    return tier, why


class Router:
    """[datasheet] route / ladder / escalate / max_attempts. A model given explicitly (--model) means one model."""

    def __init__(self, cfg, model=None):
        cfg = cfg or {}
        self.route = cfg.get("route") or "single"
        self.escalate = bool(cfg.get("escalate", True))
        self.max_attempts = int(cfg.get("max_attempts") or 4)
        if model or self.route != "ladder":
            self.ladder = [model or cfg.get("model")]
            self.route = "single"
        else:
            self.ladder = [m for m in (cfg.get("ladder") or LADDER) if m] or [cfg.get("model")]

    def start(self, package=None, n_pages=None, sent_pages=None):
        """-> (rung index, reason)."""
        if len(self.ladder) == 1:
            return 0, "one model for every part ([datasheet] route = \"single\")"
        tier, why = difficulty(package, n_pages, sent_pages)
        return round(tier * (len(self.ladder) - 1) / 2), why

    def next_step(self, rung, level, n_levels, kind, attempts):
        """After an answer judged `kind` (ok | incomplete | failed) at (rung, level): the next (rung, level) to try,
        or None (keep this answer). Incomplete widens the pages before changing the model; failed does the opposite."""
        if kind == "ok" or not self.escalate or attempts >= self.max_attempts:
            return None
        wider = (rung, level + 1) if level + 1 < n_levels else None
        better = (rung + 1, level) if rung + 1 < len(self.ladder) else None
        nxt = (wider or better) if kind == "incomplete" else (better or wider)
        top = len(self.ladder) - 1
        if nxt and attempts + 1 >= self.max_attempts and nxt[0] < top:   # the last answer allowed goes to the top model:
            nxt = (top, n_levels - 1 if kind == "incomplete" else level)   # max_attempts never keeps a cheaper one's
        return nxt


INCOMPLETE = re.compile(r"no value printed|no nominal value printed|cited page\(s\)|pins missing|pin_count missing|"
                        r"pin count: drawing says|has no source page", re.I)
FAILED = re.compile(r"^(schema:|self-check:|symbol:|footprint_gen\.build failed|altium_lib\.symbol_from_json failed|"
                    r"pad_table:)|min <= nom <= max violated|implausib|leads would touch| span \d|pins per side|is odd for a "
                    r"dual-row|lead_span min|^exposed_pad\.\w+ [\d.]+ >=|under \d+% of", re.I)


def assess(res):
    """-> (ok | incomplete | failed, reasons) from datasheet.process()'s spec and review list."""
    rev = list(res.get("needs_review") or [])
    unres = list((res.get("spec") or {}).get("unresolved") or [])
    inc = [f"unresolved: {', '.join(unres)}"] if unres else []
    inc += [r for r in rev if INCOMPLETE.search(r)]
    bad = [r for r in rev if FAILED.search(r) and r not in inc]
    if inc:
        return "incomplete", inc + bad
    if bad:
        return "failed", bad
    return "ok", []


# ---------------------------------------------------------------- cost
def prices(model, prompt_tokens=0):
    from . import datasheet as D
    p = D.price_of(model)                             # Claude's table, else ecadlib.llm (a local endpoint is $0)
    if not p:
        return None
    lp = LONG_PROMPT.get(model)
    if lp and prompt_tokens > lp[0]:
        p = lp[1]
    return p


def usage_usd(model, usage, batch=False):
    """USD of one response from its usage block (input, cache write 1.25x, cache read, output); half on the Batches
    API. None when the model has no known price, or when there is no usage (a gateway or server that sends none: the
    cost is unknown, not $0)."""
    from . import datasheet as D
    u = usage or {}
    tin, cw, cr, tout = (max(0, int(u.get(k) or 0)) for k in ("input_tokens", "cache_creation_input_tokens",
                                                             "cache_read_input_tokens", "output_tokens"))
    if not (tin or cw or cr or tout):
        return None
    p = prices(model, tin + cw + cr)
    if not p:
        return None
    read = CACHE_READ.get(model, p[0] * 0.1)
    usd = (tin * p[0] + cw * p[0] * D.CACHE_WRITE + cr * read + tout * p[1]) / 1e6
    return round(usd * (D.BATCH_DISCOUNT if batch else 1.0), 6)


def estimate_usd(model, pages, batch=False, out_tokens=None):
    """Rough USD of a read without a usage block: pages x datasheet.PAGE_TOKENS in, a typical answer out."""
    from . import datasheet as D
    tin = (pages or D.URL_PAGES) * D.PAGE_TOKENS + 1500
    tout = D.OUT_TOKENS if out_tokens is None else out_tokens
    p = prices(model, tin)
    if not p:
        return tin, None
    return tin, round((tin * p[0] * (1.0 if batch else D.CACHE_WRITE) + tout * p[1]) / 1e6 * (D.BATCH_DISCOUNT if batch else 1.0), 6)


class Ledger:
    """<out>/datasheet/cost.jsonl: one line per answer that came back (synchronous or batch), with tokens and USD."""

    def __init__(self, folder):
        self.file = Path(folder) / "cost.jsonl"

    def add(self, rec):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        rec = {"at": datetime.datetime.now().isoformat(timespec="seconds"), **rec}
        with open(self.file, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")
        return rec


def billed_model(model, msg=None):
    """The model to price a response at: the one it says served it (a server-side fallback re-runs a declined request
    on another model, billed at that model's rates) when that model has a known price, else the one requested."""
    from . import datasheet as D
    served = (msg or {}).get("model")
    return served if served and served != model and D.price_of(served) else model


def attempt_record(part, name, model, msg, via, pages_sent, pages_total, verdict, nxt=None, error=None):
    batch = str(via or "").startswith("batch")
    u = (msg or {}).get("usage") or {}
    if u:
        usd, how = usage_usd(billed_model(model, msg), u, batch), "usage"
        tin = sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        tout = int(u.get("output_tokens") or 0)
    else:
        tin, usd = estimate_usd(model, pages_sent or pages_total, batch)
        from . import datasheet as D
        tout, how = D.OUT_TOKENS, "estimate"
    rec = {"part": part, "name": name or part, "model": model, "model_served": (msg or {}).get("model"), "via": via,
           "pages_sent": pages_sent, "pages_total": pages_total, "input_tokens": tin, "output_tokens": tout,
           "usd": usd, "how": how, "verdict": verdict}
    if nxt:
        rec["next"] = nxt
    if error:
        rec["error"] = str(error)[:300]
    return rec


def read_ledger(path, problems=None):
    out = []
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for ln in lines:
        try:
            r = json.loads(ln)
        except ValueError:
            if problems is not None:
                problems.append(f"{path}: a damaged line was skipped")
            continue
        if isinstance(r, dict):
            out.append(r)
    return out


def summarize(records):
    """Totals over ledger records: answers, parts, tokens, USD (live = from usage blocks), per model and per part."""
    s = {"answers": 0, "parts": 0, "input_tokens": 0, "output_tokens": 0, "usd": 0.0, "usd_estimated": 0.0,
         "escalations": 0, "by_model": {}, "by_part": {}}
    for r in records:
        s["answers"] += 1
        s["input_tokens"] += int(r.get("input_tokens") or 0)
        s["output_tokens"] += int(r.get("output_tokens") or 0)
        usd = r.get("usd") or 0.0
        s["usd" if r.get("how") == "usage" else "usd_estimated"] += usd
        s["escalations"] += 1 if r.get("next") else 0
        m = s["by_model"].setdefault(r.get("model") or "?", {"answers": 0, "usd": 0.0})
        m["answers"] += 1
        m["usd"] += usd
        p = s["by_part"].setdefault(r.get("name") or r.get("part") or "?", {"answers": 0, "usd": 0.0, "models": []})
        p["answers"] += 1
        p["usd"] += usd
        if r.get("model") not in p["models"]:
            p["models"].append(r.get("model"))
    s["parts"] = len(s["by_part"])
    for d in [s] + list(s["by_model"].values()) + list(s["by_part"].values()):
        for k in ("usd", "usd_estimated"):
            if k in d:
                d[k] = round(d[k], 4)
    return s


def review_lines(out):
    """Markdown for the librarian's review.md: what the datasheet reads of this output folder cost."""
    recs = read_ledger(Path(out) / "datasheet" / "cost.jsonl")
    if not recs:
        return []
    s = summarize(recs)
    md = ["", "## Datasheet reads: tokens and cost", "",
          f"{s['answers']} answers for {s['parts']} parts, {s['input_tokens']:,} tokens in / {s['output_tokens']:,} out, "
          f"${s['usd']:.4f} from the API usage" + (f" + ${s['usd_estimated']:.4f} estimated" if s["usd_estimated"] else "")
          + (f"; {s['escalations']} answers led to a wider or stronger retry" if s["escalations"] else "") + ".", "",
          "| Part | Model | Pages sent | Tokens in / out | USD | How | Verdict |", "|---|---|---|---|---|---|---|"]
    for r in recs:
        usd = f"{r['usd']:.4f}" if isinstance(r.get("usd"), (int, float)) else "n/a"
        pages = f"{r.get('pages_sent') or 'all'} of {r.get('pages_total') or '?'}"
        md.append(f"| {r.get('name')} | {r.get('model')} | {pages} | {r.get('input_tokens', 0):,} / {r.get('output_tokens', 0):,} "
                  f"| {usd} | {r.get('how')} | {r.get('verdict')}{' -> ' + r['next'] if r.get('next') else ''} |")
    return md


# ---------------------------------------------------------------- `ecadlib datasheet pages`: what a read would send
def page_texts(data):
    """Text of every page (pypdf), or None when there is no pypdf or the PDF can't be read."""
    try:
        import logging, pypdf
        logging.getLogger("pypdf").setLevel(logging.ERROR)
        rd = pypdf.PdfReader(io.BytesIO(data))
        if rd.is_encrypted and not rd.decrypt(""):    # a user password: the text can't be read here
            return None
        pages = list(rd.pages)
    except Exception:
        return None
    out = []
    for pg in pages:
        try:
            out.append(pg.extract_text() or "")
        except Exception:
            out.append("")
    return out


def pages_main(argv=None):
    from . import datasheet as D, rules as RU
    ap = argparse.ArgumentParser(prog="ecadlib datasheet pages",
                                 description="Which pages of a datasheet PDF a read would send (first try, then wider), and "
                                             "which model the route starts on. Reads the PDF locally; sends nothing.")
    ap.add_argument("pdf", help="a local datasheet PDF")
    ap.add_argument("--part", help="manufacturer part number (finds its row in the ordering table)")
    ap.add_argument("--package", help="package hint, e.g. a KiCad footprint name such as SOT-223-3_TabPin2")
    ap.add_argument("--budget", type=int, help="pages on the first try (default: [datasheet] page_budget)")
    ap.add_argument("--rules", help="library rules file (default: ecadlib.toml found from here)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        cfg = D.settings(RU.load(a.rules, start="."))
    except RU.RulesError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    data = Path(a.pdf).read_bytes()
    if not D.is_pdf(data):
        print(f"error: {a.pdf} is not a PDF", file=sys.stderr)
        return 1
    budget = cfg["page_budget"] if a.budget is None else a.budget
    texts, n = page_texts(data), D.pdf_pages(data) or 0
    lv, why = levels(texts or [], budget, a.part, a.package, n) if texts else ([None], {})
    router = Router(cfg)
    rung, reason = router.start(a.package, n, len(lv[0]) if isinstance(lv[0], list) else None)
    steps = [{"pages": p if p else "all", "count": len(p) if p else n} for p in lv]
    out = {"pdf": a.pdf, "pages_total": n, "text_layer": bool(texts and any(t.strip() for t in texts)), "budget": budget,
           "steps": steps, "why": {str(k): v for k, v in why.items()}, "model": router.ladder[rung], "route": router.route,
           "start_reason": reason, "rough_tokens_first_try": steps[0]["count"] * D.PAGE_TOKENS,
           "rough_tokens_whole": n * D.PAGE_TOKENS}
    if a.json:
        print(json.dumps(out, indent=2))
        return 0
    print(f"{a.pdf}: {n} pages{'' if out['text_layer'] else ' (no text layer: the whole PDF is sent)'}; "
          f"[datasheet] page_budget = {budget}")
    for i, st in enumerate(steps):
        label = "first try" if i == 0 else ("then the whole PDF" if st["pages"] == "all" else "then")
        print(f"  {label:<18} {st['count']:>4} pages  {st['pages'] if st['pages'] == 'all' else ','.join(map(str, st['pages']))}")
    for k, v in why.items():
        if k == "_package":
            print(f"  this part's package: {', '.join(v)}")
        else:
            print(f"    p{k}: {', '.join(v)}")
    print(f"  model: {router.ladder[rung]} ({reason}); rough input {out['rough_tokens_first_try']:,} tokens vs "
          f"{out['rough_tokens_whole']:,} for the whole PDF ({D.PAGE_TOKENS} per page, no key: not counted)")
    return 0
