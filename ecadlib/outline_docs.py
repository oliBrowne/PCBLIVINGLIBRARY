"""Package drawings a datasheet keeps in a separate document.

Some family datasheets print no package drawing at all, only the drawing's document number: NXP's Kinetis sheets
("To find a package drawing, go to nxp.com and search for the drawing's document number": 80-pin LQFP 98ASS23174W),
Microchip's ("C04-..." in the Microchip Packaging Specification). refs() finds the numbers printed for THIS part's
package when the PDF has no drawing of it; Reader.job then gets each document (the [datasheet] folder first, as
<number>.pdf, then the maker's package-information link, with the same download ways as datasheets) and appends it
to the datasheet, so the page picker and the read see the drawing like any other page. The read's review names the
document."""
import io, re

from . import ds_route as DR

# (document number pattern, the maker whose links are tried, the word its line must have: Maxim's bare numbers are
# documents only as "Outline Number 21-0664" / "Land Pattern Number 90-0397", MAX22201's Package Information)
CODES = [(re.compile(r"(?<![A-Z0-9])(98A[A-Z]{2,3}\d{5}[A-Z])(\d)?(?![A-Z0-9])"), "nxp", None),   # 98ASS23174W(1)
         (re.compile(r"(?<![A-Z0-9])(C04-\d{3,5}[A-Z]?)(?![A-Z0-9])"), "microchip", None),       # C04-076
         (re.compile(r"(?<![A-Z0-9-])(21-\d{4,6}[A-Z]?)(?![A-Z0-9])"), "maxim", re.compile(r"OUTLINE")),
         (re.compile(r"(?<![A-Z0-9-])(90-\d{4,6}[A-Z]?)(?![A-Z0-9])"), "maxim", re.compile(r"LAND\s*PATTERN"))]
LINKS = {"nxp": ["https://www.nxp.com/docs/en/package-information/{code}.pdf"],
         "microchip": [],                                # versioned Packaging Specification: the folder or web search
         "maxim": {"21": ["https://pdfserv.maximintegrated.com/package_dwgs/{code}.PDF"],
                   "90": ["https://pdfserv.maximintegrated.com/land_patterns/{code}.PDF"]}}
BACK = 3                # lines above a number its package name may be on (Maxim: name, package code, outline number)
MAX_DOCS = 2
CAPTION_ONLY = 400                                       # characters: a drawing page's caption, header and footer


def _has_drawing(texts, rows):
    """Does the PDF itself hold a dimension table for this part's package (or one that names no other package)?"""
    for t, r in zip(texts, rows):
        t = DR.norm_text(t)
        if DR.TABLE["drawing"].search(t) and DR.DIM_SYMS.search(t) and not r["other"] and not r["toc"]:
            return True
    return False


def drawing_missing(texts, part=None, package=None):
    """Does the PDF hold no drawing of this part's package (the case for a web search for the package document,
    Reader._package_search)? True only when it can be told from the text: no dimension table of this package (or one
    naming no other package), no page captioned as a drawing of it, and no image-only page past the first (an image
    can be the drawing: CY14U256LA-BA, IR4301, benchmark 2026-10-23). A PDF with no text layer: False."""
    if not texts or not any((t or "").strip() for t in texts):
        return False
    rows = DR.page_scores(texts, part, package)[0]
    if _has_drawing(texts, rows):
        return False
    if refs(texts, part, package):                    # it names the drawing's document instead of printing it
        return True
    for i, (t, r) in enumerate(zip(texts, rows)):
        if r["toc"] or r["noise"]:
            continue
        if r["blank"] and i > 0:
            return False
        # a page captioned as this package's drawing with little else on it: an image drawing. A line pointing to
        # another document on a page of text ("Package information: see the Packaging Specification") is not one
        if r["drawing"] and not r["other"] and len(DR.norm_text(t).strip()) <= CAPTION_ONLY:
            return False
    return True


def names_it(texts, part, codes=(), own=(), url=""):
    """Is a package document found by search about this part? It names the part (datasheet.mentions_part), one of
    the document numbers the datasheet gives, or this part's package (a package information document lists packages,
    not parts). A PDF whose text can't be read counts only when its link names the part (a drawing PDF named for it)."""
    from . import datasheet as D
    said = D.mentions_part(texts, part, whole=True)
    if said:
        return True
    flat = re.sub(r"[^A-Z0-9]", "", " ".join(t or "" for t in texts).upper())
    if any(re.sub(r"[^A-Z0-9]", "", c.upper()) in flat for c in codes if c):
        return True
    codes = mpn_package_codes(part, own)              # EPM1270M256's M256: a 256-ball FineLine BGA is not it
    # no code in the MPN: a drawing that prints another code for this count (FTG256) is another maker's package
    printed = any(c in flat for c in codes) if codes else not other_codes(texts, own)
    if printed and _same_package(own, set().union(*(DR.package_tokens(DR.norm_text(t or "")) for t in texts))):
        return True
    key = re.sub(r"[^A-Z0-9]", "", str(part or "").upper())
    return said is None and len(key) >= 4 and key in re.sub(r"[^A-Z0-9]", "", str(url).upper())


def _counts(own):
    return {m.group() for t in own or () for m in [re.search(r"\d+$", t)] if m
            and re.sub(r"\d+$", "", t) not in DR.OUTLINE_FAMS}   # SOT23's 23 names the outline, not 23 pins


def mpn_package_codes(part, own=()):
    """Every form of the package code in an MPN whose number is this part's pin or ball count (own: BGA256 ...), the
    first the one mpn_package_code gives; [] when there is none. The code may carry an ordering tail after it (speed
    grade, temperature, lead-free, packing: EPM1270M256C5N, ICE40HX8K-BG121TR, LCMXO2-1200HC-4MG132C).
    - a later segment that starts with the code, after a speed-grade digit at most: BG121TR -> BG121, 4MG132C -> MG132;
    - in the first segment, a code after the part's own letters and number (EPM1270 | M256, 10M08SC | M153): which of
      the letters before the count are the code's is not known (10M08SC + M153), so each ending of them (up to 3) is
      a form, the shortest first. A number that only follows digits is the part number itself: 74HC14 is the 14th
      74HC logic part. The count is the number as printed: AT24C08's "08" is no 8."""
    counts = _counts(own)
    if not counts:
        return []
    segs = [x for x in re.split(r"[-_/#\s]+", str(part or "").strip().upper()) if x]
    for i, seg in reversed(list(enumerate(segs))):
        if i:
            m = re.match(r"\d?([A-Z]{1,3})(\d{2,4})(?!\d)", seg)
            if m and m.group(2) in counts:
                return [m.group(1) + m.group(2)]
            continue
        for m in re.finditer(r"([A-Z]+)(\d{2,4})(?!\d)", seg):
            if m.group(2) in counts and re.search(r"[A-Z]\d", seg[:m.start()]):
                run = m.group(1)[-3:]
                return [run[-k:] + m.group(2) for k in range(1, len(run) + 1)]
    return []


def mpn_package_code(part, own=()):
    """The package code in an MPN, when its number is this part's pin or ball count (own: BGA256 and so on):
    EPM1270M256C5N -> M256, M2GL090T-FG484 -> FG484, ICE40HX8K-BG121TR -> BG121; else None (GBU4A, LM358DR). A package
    document found by search must print it: a family and count alone matched a 1.0 mm FineLine BGA-256 for the 0.5 mm
    Micro FineLine M256, and a generic BGA layout note for BG121 (live benchmark 2026-10-24). mpn_package_codes."""
    codes = mpn_package_codes(part, own)
    return codes[0] if codes else None


def other_codes(texts, own=()):
    """Package codes a document prints for this part's count that name no package family ds_route knows (FTG256,
    FG256, M256 for a BGA256): another maker's own package code, which a part with no code of its own can't be told
    apart from. A family's own names (BGA-256, FBGA256, SOIC-14 for SO14) are not one."""
    counts = _counts(own)
    if not counts:
        return set()
    up = " ".join(DR.norm_text(t or "") for t in texts).upper()
    return {m.group(1) + m.group(2) for m in re.finditer(r"(?<![A-Z0-9])([A-Z]{1,6})-?(\d{2,4})(?![0-9])", up)
            if m.group(2) in counts and not DR.package_tokens(m.group(1) + m.group(2))}


def _same_package(own, toks):
    """This part's package by family and count (BGA484), never by family alone: a drawing of another BGA is not it.
    A part whose package is known only as a family (no count) matches that family."""
    counted = {t for t in own if re.search(r"\d$", t)}
    return bool(counted & toks) if counted else bool(own) and DR._tok_match(own, toks)


def refs(texts, part=None, package=None):
    """[(document number, maker, 1-based page)] the PDF prints for this part's package, when it has no drawing of
    it. A number counts only on a line (or the line before it) that names this package: a list of every package's
    document numbers gives this one's only."""
    if not texts or not any(rx.search(DR.norm_text(t).upper()) for t in texts for rx, _, _ in CODES):
        return []
    rows, own, _, _ = DR.page_scores(texts, part, package)
    if not own or _has_drawing(texts, rows):
        return []
    out, seen = [], set()
    for i, t in enumerate(texts):
        lines = DR.norm_text(t).upper().splitlines()
        for k, ln in enumerate(lines):
            for rx, maker, need in CODES:
                if need is not None and not need.search(ln):
                    continue
                for m in rx.finditer(ln):
                    before = ln[:m.start()]
                    # the nearest package name above: each block of a list owns the numbers under its name
                    toks = DR.package_tokens(before) or DR.package_tokens(ln) or \
                        next((t for t in map(DR.package_tokens, lines[max(0, k - BACK):k][::-1]) if t), set())
                    if toks and DR._tok_match(own, toks) and m.group(1) not in seen:
                        seen.add(m.group(1))
                        out.append((m.group(1), maker, i + 1))
    return out[:MAX_DOCS]


def links(code, maker):
    urls = LINKS.get(maker, [])
    if isinstance(urls, dict):                        # Maxim: outline (21-) and land pattern (90-) documents
        urls = urls.get(code.split("-")[0], [])
    return [u.format(code=code) for u in urls]


def append(data, extra):
    """The datasheet's PDF with the outline documents' pages after its own -> bytes, or None when pypdf can't."""
    try:
        import pypdf
        w = pypdf.PdfWriter()
        for b in (data, *extra):
            w.append(pypdf.PdfReader(io.BytesIO(b)), import_outline=False)
        buf = io.BytesIO()
        w.write(buf)
        return buf.getvalue()
    except Exception:
        return None
