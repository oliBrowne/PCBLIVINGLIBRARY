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
    # no code in the MPN: a ball grid drawing that prints another maker's code for this count (FTG256) is another
    # package (pitch, body). A maker's own outline codes (ADI RW-20, LTC G48, Intel F256 for an F17 part) are not
    # the code as a whole token of the text, longest form first: "17 x 17 mm 256-ball" holds no M256 (R-FX13-5)
    up = " ".join(DR.norm_text(t or "") for t in texts).upper()
    printed = any(_token(c).search(up) for c in sorted(codes, key=len, reverse=True)) if codes \
        else not foreign_codes(texts, part, own)
    if printed and _same_package(own, set().union(*(DR.package_tokens(DR.norm_text(t or "")) for t in texts))):
        return True
    key = re.sub(r"[^A-Z0-9]", "", str(part or "").upper())
    return said is None and len(key) >= 4 and key in re.sub(r"[^A-Z0-9]", "", str(url).upper())


# ---------------------------------------------------------------- a searched drawing must be this part's body (R-FX19-1)
# A family and a pin count do not make a package: a QFP100 is 14 x 14 / 0.5 mm or 14 x 20 / 0.65 mm, a TSSOP28 4.4 or
# 6.1 mm wide. What the part's own datasheet prints for its package (body size, pitch, an outline code such as NXP's
# SOT407-1) is what a drawing found by web search, and the read built from it, must match.
_SIZE = re.compile(r"(?<![\d.])(\d{1,2}(?:\.\d{1,2})?)\s?(?:mm)?\s?[x×]\s?(\d{1,2}(?:\.\d{1,2})?)"
                   r"(?:\s?(?:mm)?\s?[x×]\s?\d{1,2}(?:\.\d{1,2})?)?\s?mm\b", re.I)
_PITCH = re.compile(r"(?<![\d.])(\d?\.\d{1,3})\s?(?:mm)?\s?(?:(?:lead|ball|pin|terminal)s?\s)?pitch\b|"
                    r"\bpitch\s?(?:of\s|=\s?|:\s?)?(\d?\.\d{1,3})\s?mm\b", re.I)
_OUTLINE = re.compile(r"(?<![A-Z0-9])(SOT\d{3,4}-\d{1,2})(?![0-9])")     # NXP / Nexperia outline versions
_FAM_PAREN = re.compile(r"(?<![A-Z0-9])([A-Z]{2,7})\s?\((\d{1,4})\)")      # TI's "TSSOP (20)": TSSOP20


def _pkg_toks(ln):
    """The packages a line names, an outline code (SOT407-1) not one of them and TI's "TSSOP (20)" as TSSOP20."""
    up = _FAM_PAREN.sub(r"\1\2", _OUTLINE.sub(" ", DR.norm_text(ln).upper()))
    return DR.package_tokens(up)


def _facts_of(lines):
    bodies, pitches, outlines = set(), set(), set()
    for ln in lines:
        for m in _SIZE.finditer(ln):
            a, b = sorted((float(m.group(1)), float(m.group(2))))
            if a > 0:
                bodies.add((a, b))
        for m in _PITCH.finditer(ln):
            v = float(m.group(1) or m.group(2))
            if 0.1 <= v <= 5.08:
                pitches.add(v)
        outlines |= set(_OUTLINE.findall(ln.upper()))
    return {"bodies": sorted([list(b) for b in bodies]), "pitches": sorted(pitches), "outlines": sorted(outlines)}


def printed_body(texts, part=None, own=()):
    """What a PDF prints for this part's package (own: its package tokens, QFP100): {"bodies": [[short, long] mm],
    "pitches": [mm], "outlines": [SOT407-1]}, from the lines that name this package (or the part) and no other
    package, and the line after such a line when it names no package (a wrapped ordering row). Empty lists: nothing
    printed. "LPC1768FBD100 LQFP100 ... body 14 x 14 x 1.4 mm SOT407-1" -> [[14, 14]], [], [SOT407-1]."""
    key = re.sub(r"[^A-Z0-9]", "", re.split(r"[#/]", str(part or "").upper())[0])
    out = []
    for t in texts or ():
        lines = DR.norm_text(t or "").splitlines()
        for k, ln in enumerate(lines):
            toks = _pkg_toks(ln)
            named = len(key) >= 4 and key in re.sub(r"[^A-Z0-9]", "", ln.upper())
            if toks:
                if not own or not all(_same_package(own, {x}) for x in toks):
                    continue                          # another package (or one of several) on this line
            elif not named:
                continue
            out.append(ln)
            if k + 1 < len(lines) and not _pkg_toks(lines[k + 1]):
                out.append(lines[k + 1])
    return _facts_of(out)


def _near(a, b, rel=0.05, ab=0.2):
    return abs(a - b) <= max(ab, rel * max(a, b))


def _body_in(body, bodies):
    return any(_near(body[0], o[0]) and _near(body[1], o[1]) for o in bodies)


def body_mismatch(found, facts):
    """Why a package document's printed body (printed_body of it) is not the one the part's datasheet prints (facts),
    or None: a different outline code, no printed body size or pitch among the datasheet's."""
    if not facts:
        return None
    if facts["outlines"] and found["outlines"] and not set(facts["outlines"]) & set(found["outlines"]):
        return f"it prints outline {', '.join(found['outlines'])}, the datasheet {', '.join(facts['outlines'])}"
    if facts["bodies"] and found["bodies"] and not any(_body_in(b, facts["bodies"]) for b in found["bodies"]):
        return (f"it prints body {' / '.join(f'{a:g} x {b:g} mm' for a, b in found['bodies'])}, the datasheet "
                f"{' / '.join(f'{a:g} x {b:g} mm' for a, b in facts['bodies'])}")
    if facts["pitches"] and found["pitches"] and not any(abs(p - q) <= 0.01 for p in found["pitches"]
                                                         for q in facts["pitches"]):
        return (f"it prints pitch {' / '.join(f'{p:g}' for p in found['pitches'])} mm, the datasheet "
                f"{' / '.join(f'{p:g}' for p in facts['pitches'])} mm")
    return None


def body_tied(found, facts):
    """Does a package document print something of this part's own body (the datasheet's outline code, a body size
    it prints, or its pitch when it prints no body)?"""
    if not facts:
        return False
    if set(facts["outlines"]) & set(found["outlines"]):
        return True
    if facts["bodies"]:
        return any(_body_in(b, facts["bodies"]) for b in found["bodies"])
    return any(abs(p - q) <= 0.01 for p in found["pitches"] for q in facts["pitches"])


def outline_named(own):
    """Is this part's package an outline whose name is its body (DO-34, SOT-223, TO-220, SOD-123: the number names
    the outline, not a pin count)? Then a document of that outline is of this body, whatever else it prints."""
    return bool(own) and all(t in DR.OUTLINE_FAMS or re.sub(r"\d+$", "", t) in DR.OUTLINE_FAMS and re.search(r"\d$", t)
                             for t in own)


def has_facts(facts):
    return bool(facts and (facts.get("bodies") or facts.get("pitches") or facts.get("outlines")))


def read_mismatch(crit, facts):
    """Why a read (crosscheck.critical of its spec) is not the body or pitch the part's datasheet prints, or None."""
    if not has_facts(facts):
        return None

    def nom(v):                                       # crosscheck.critical gives (min, nom, max)
        if isinstance(v, (list, tuple)):
            got = [x for x in v if isinstance(x, (int, float))]
            v = v[1] if len(v) == 3 and isinstance(v[1], (int, float)) else (sum(got) / len(got) if got else None)
        return v
    bl, bw = nom(crit.get("body_length")), nom(crit.get("body_width"))
    if facts["bodies"] and isinstance(bl, (int, float)) and isinstance(bw, (int, float)) and bl > 0 and bw > 0 \
            and not _body_in(sorted((bl, bw)), facts["bodies"]):
        return (f"the read's body {bl:g} x {bw:g} mm is not the datasheet's "
                f"{' / '.join(f'{a:g} x {b:g} mm' for a, b in facts['bodies'])}")
    p = nom(crit.get("pitch"))
    if facts["pitches"] and isinstance(p, (int, float)) and p > 0 and not any(abs(p - q) <= 0.01 for q in facts["pitches"]):
        return f"the read's pitch {p:g} mm is not the datasheet's {' / '.join(f'{q:g}' for q in facts['pitches'])} mm"
    return None


def _token(code):
    """A package code as a whole token (FG484, FG-484), never inside another word or number (MM256, LEAD100)."""
    m = re.fullmatch(r"([A-Z]+)(\d+)", code)
    body = f"{m.group(1)}-?{m.group(2)}" if m else re.escape(code)
    return re.compile(rf"(?<![A-Z0-9]){body}(?!\d)")


def _counts(own):
    return {m.group() for t in own or () for m in [re.search(r"\d+$", t)] if m
            and re.sub(r"\d+$", "", t) not in DR.OUTLINE_FAMS}   # SOT23's 23 names the outline, not 23 pins


def mpn_package_codes(part, own=()):
    """Every form of the package code in an MPN whose number is this part's pin or ball count (own: BGA256 ...), the
    first the one mpn_package_code gives; [] when there is none. The code may carry an ordering tail after it (speed
    grade, temperature, lead-free, packing: EPM1270M256C5N, ICE40HX8K-BG121TR, LCMXO2-1200HC-4MG132C).
    - a later segment that starts with the code (1 to 4 letters), after a speed grade of up to 2 digits: BG121TR ->
      BG121, 4MG132C -> MG132, 10VQG64C -> VQG64, 1SBVA484E -> SBVA484;
    - in the first segment, a code after the part's own letters and number (EPM1270 | M256, 10M08SC | M153): which of
      the letters before the count are the code's is not known (10M08SC + M153), so each ending of them (up to 3) is
      a form, the shortest first. A number that only follows digits is the part number itself: 74HC14 is the 14th
      74HC logic part, and so is it after a maker's prefix (SN74HC14N, MC74HC14ADR2G). A code in the first segment is
      read for a ball / land grid only, with an ordering tail after it or not (MSP430G2553IPW20R and MSP430G2553IPW20
      are no W20, LPC1768FBD100 no D100: a leaded package's family and count are enough, R-FX16-4). The count is the
      number as printed: AT24C08's "08" is no 8."""
    counts = _counts(own)
    if not counts:
        return []
    segs = [x for x in re.split(r"[-_/#\s]+", str(part or "").strip().upper()) if x]
    for i, seg in reversed(list(enumerate(segs))):
        if i:
            m = re.match(r"\d{0,2}([A-Z]{1,4})(\d{2,4})(?!\d)", seg)     # 10VQG64C, 1SBVA484E (R-FX13-4)
            if m and m.group(2) in counts:
                return [m.group(1) + m.group(2)]
            continue
        grid = any(re.sub(r"\d+$", "", t) in ARRAYS for t in own or ())
        for m in re.finditer(r"([A-Z]+)(\d{2,4})(?!\d)", seg):
            before = seg[:m.start()]
            if re.search(r"(?:^|[A-Z])[57]4$", before):      # SN74HC14N, MC74HC14ADR2G: a 74 logic part number
                continue
            if not grid:                                  # ball / land grids only (MSP430G2553IPW20(R): TI's PW +
                continue                                  # pin count, R-FX13-5, R-FX16-4)
            if m.group(2) in counts and re.search(r"[A-Z]\d", before):
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


ARRAYS = {"BGA", "LGA", "CSP"}                           # one count, many pitches and bodies: the code tells them apart
JEDEC = re.compile(r"(?<![A-Z0-9])[A-Z]-P[A-Z]{2,5}-[A-Z]\d{1,4}(?![A-Z0-9])")   # TI's R-PDSO-G14: the generic outline


def foreign_codes(texts, part, own=()):
    """other_codes that clearly name another maker's package, for a part number with no package code of its own:
    only for a ball / land grid (BGA, LGA, CSP), where one count comes in many pitches and bodies, and only a code
    whose letters the part number does not carry (Intel's F256 for EP4CE22F17C8N's F17, ADI's CC-14 for ADXL345BCCZ
    are the part's own maker's). One letter is the part's only right before a one- or two-digit number in it (Intel's
    F + body size: F17, BF14), never a letter before the device number (TMS320F28377's F: another maker's F337 is not
    its ZWT, R-FX16-4). A leaded package's family and count are enough: ADI RW-20, LTC G48, TI R-PDSO-G14
    name the right SOIC / SSOP (R-FX13-4)."""
    if not any(re.sub(r"\d+$", "", t) in ARRAYS for t in own or ()):
        return set()
    texts = [JEDEC.sub(" ", DR.norm_text(t or "").upper()) for t in texts]
    up = str(part or "").upper()
    runs = re.findall(r"[A-Z]+", up)
    def ours(code):
        letters = re.sub(r"\d+$", "", code)
        if len(letters) == 1:
            return bool(re.search(rf"{letters}\d{{1,2}}(?!\d)", up))
        return any(letters in r for r in runs)
    return {c for c in other_codes(texts, own) if not ours(c)}


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
