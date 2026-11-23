"""A free, model-free second source for a footprint that a datasheet read copied from the maker's own land drawing:
the numbers that drawing prints in the PDF's text layer.

A maker's land (an inductor, a crystal, an aluminium can, a fuse) is in no standard table, so nothing but the read
itself vouched for it, and every such part waited for a person. check(runs_by_page, land, part) looks for the land
drawing's title on a page ("Recommended Land Pattern", "Typical Pad Layout", "PCB Layout", "Footprint" ...), takes the
numbers printed around it (nearer to it than to any other drawing's title, and no farther than REACH), and asks
whether the read's land is made of those numbers:

    pad length (along the line through both pads)   printed
    pad width                                        printed
    the spacing                                      printed as the gap between the pads or the span over both
                                                     (centre distance -+ pad length), or as the centre distance
                                                     alone when the pads are square

A read that turned the pads 90 degrees (pad length and width swapped, the centre distance worked out from the printed
gap or span with its own wrong pad length) is made of the same three printed numbers, so "pad length + gap" and
"pad length + span" count only when that turned land is ruled out (2026-11-23, review): by where the numbers are
printed (pad length and gap on one dimension line, or pad length and gap / span in one text direction and the pad
width in the other: _layout; a number that only shares a height or a column with the gap is not on its line),
or, when the places show nothing, because the turned land is no land of this body by the wide limits of
fits_body(sure=True). Otherwise: no verdict.

Only two-pad lands are checked (spec["land"] of a two-pin part, or a two-pad pad_table), the numbers are compared in
mm as printed (a drawing in inches only is not converted: no verdict), within half the last printed digit.

Only sizes count: a number needs a decimal point (4.04 or 4,04), and these are left out because they are no land
size: a tolerance, a number printed with its tolerance or followed by Max/Min/Typ (a body size), a radius or
diameter (R, Ø), the inch twin of an mm number, and a second number printed at the same point. The three numbers
must be distinct and sit within SPREAD of one another; a size labelled on both pads is the pad length; the pad width
may not equal a size along the pads' line; the land must fit the body size the read gives (fits_body); and a PDF
with two different land drawings, or a drawing that agrees just as well with the pads turned, gives no verdict.
A drawing
whose numbers sit in a table of several sizes (two or more lines of numbers in columns, five or more numbers in all:
_lines) agrees only when the read's numbers are all on one line that names the part's series. The result is

    {"status": "agreed", "page": n, "printed": {...}}   every number found in one land drawing
    {"status": "unchecked", "why": ...}                 no land drawing in the text, a number not printed there, an
                                                        image drawing, a table row not named, anything unsure

never "disagreed": a number missing from the text is no proof the read is wrong (the drawing may be an image), so
the part keeps the hold it had. Only "agreed" counts as an independent source (crosscheck.run)."""
import math, re

LAND_TITLE = re.compile(r"(?i)(?:land(?:ing)?\s*(?:pattern|pad|layout|dimension|size)|"
                        r"pad\s*(?:layout|pattern|dimension|size|design)|foot\s*-?print|"
                        r"\bp\.?c\.?b\.?\s*(?:layout|pattern|land|pad|mounting|footprint)|mounting\s*pads?\b|"
                        r"solder(?:ing)?\s*(?:pad|land)|(?:recommended|suggested|typical)\s+(?:p\.?c\.?b\.?\s+)?(?:land|layout))")
OTHER_TITLE = re.compile(r"(?i)\b(?:dimensions?|outline|package|marking|tape|reel|schematic|circuit|profile|"
                         r"construction|carrier|packaging|reflow|structure|ordering|characteristics?)\b")
REACH = 160.0                  # points: a land drawing's numbers are around its title (an A4 page is 595 x 842)
SPREAD = 90.0                  # points: the numbers the read's land is made of sit within this of one another
_LEGEND = re.compile(r"(?i)dimensions?\s*(?::|are\s+in\b)|\bunit\s*:")   # 'DIMENSIONS: MM', a units legend
_NUM = re.compile(r"(?<![\w.,±/])(\d+\.\d+|\.\d+|\d{1,2},\d{1,2}(?!\d)|0,\d{3}|\d+)(?![\w.,]|\s*%)")
# a number followed by MAX / MIN / TYP (after its other unit, if any) or by its own tolerance is a body's size: a
# maker prints the land's sizes plain ('3.0 Max.', '5.0 TYP.(.197)', '0.8 +0.2/-0.3', '6,36 ±0,2')
_QUALIFIED = re.compile(r"\s*[\])]?\s*(?:\[[^\]]*\]|\([^)]*\))?\s*(?:(?:max|min|typ)\b|±|\+/?-|\+\s*\d)", re.I)
_TOL_MARK = re.compile(r"(?:±|\+/-|\+-|[+\-−])\s*$")


def _tol(tok):
    """Half the last printed digit (1.93 -> 0.005), at least 0.01 mm and at most 0.03 mm (2.8 -> 0.03: a read of
    2.85 for a printed 2.8 is another number)."""
    d = len(tok.split(".")[1]) if "." in tok else 0
    return min(max(0.5 * 10 ** -d, 0.01), 0.03) + 1e-9


def numbers(run):
    """The numbers one placed text run prints: [(value, token, x, y, vertical)], at the token's own place when the
    run has its characters' places. A tolerance (the 0.2 of '4.0 ±0.2', '+0.2/-0.3': a signed number) and a
    percentage are not sizes; a number inside a word (R2, 0402) is not one either."""
    x, y, text, size, vertical = run[:5]
    places = run[5] if len(run) > 5 else None
    out = []
    nonspace = [k for k, ch in enumerate(text) if not ch.isspace()]
    for m in _NUM.finditer(text):
        if _TOL_MARK.search(text[:m.start()]) or re.search(r"(?:\bR|[Øø⌀φΦ])\s*$", text[:m.start()]):
            continue                                    # a tolerance, or a radius / diameter (R 1,3): no pad size
        if _QUALIFIED.match(text[m.end():]):
            continue                                    # a body's limit or toleranced size, not a land's
        tok = m.group(1).replace(",", ".")              # a decimal comma (Coilcraft's mm: 4,04)
        if places and len(places) == len(nonspace):
            mine = [places[nonspace.index(k)] for k in range(m.start(), m.end()) if k in nonspace]
            px, py = sum(p[0] for p in mine) / len(mine), sum(p[1] for p in mine) / len(mine)
        else:
            off = 0.5 * (size or 1) * (m.start() + len(tok) / 2)
            px, py = (x, y + off) if vertical else (x + off, y)
        out.append((float(tok), tok, px, py, vertical))
    return out


def titles(runs):
    """[(x, y, is_land)]: the drawing titles on a page, short runs only (a sentence that mentions a land pattern is
    no title)."""
    out = []
    for x, y, t, size, vertical, *_ in runs:
        if len(t) > 60 or len(t.split()) > 8:
            continue
        cx = x + 0.25 * (size or 1) * len(t)
        if _LEGEND.search(t) and not LAND_TITLE.search(t):
            continue
        if LAND_TITLE.search(t):
            out.append((cx, y, True))
        elif OTHER_TITLE.search(t):
            out.append((cx, y, False))
    return out


def drawn_numbers(runs):
    """The page's numbers that can be a size in mm: a number with a decimal point (an integer is a count, a pin or a
    graph's axis as often as a size), not a tolerance (the 0.3 printed after a lone '±'), not an inch value printed
    beside the same size in mm (0.135 beside 3.429: 25.4 times), and not two numbers the PDF places at one point (their
    places say nothing)."""
    nums = [n for r in runs for n in numbers(r) if "." in n[1]]
    tol = [(x, y, size) for x, y, t, size, v, *_ in runs if not v and t.rstrip().endswith(("±", "+/-"))]
    out = []
    for n in nums:
        if any(abs(n[3] - y) <= 0.45 * (size or 1) + 0.5 and 0 <= n[2] - x <= 4 * (size or 1) for x, y, size in tol):
            continue
        if any(o is not n and abs(o[2] - n[2]) < 1 and abs(o[3] - n[3]) < 1 and o[1] != n[1] for o in nums):
            continue
        if any(o is not n and math.hypot(o[2] - n[2], o[3] - n[3]) <= 30 and abs(o[0] - 25.4 * n[0]) <= 0.02 * o[0]
               for o in nums):
            continue
        out.append(n)
    return out


def regions(runs):
    """[(title (x, y), [numbers])]: for each land drawing title, the numbers (drawn_numbers) nearer to it than to any
    other title and within REACH."""
    ts = titles(runs)
    nums = drawn_numbers(runs)
    out = []
    for i, (tx, ty, land) in enumerate(ts):
        if not land:
            continue
        mine = []
        for n in nums:
            d = math.hypot(n[2] - tx, n[3] - ty)
            if d <= REACH and all(d <= math.hypot(n[2] - ox, n[3] - oy) for j, (ox, oy, _) in enumerate(ts) if j != i):
                mine.append(n)
        out.append(((tx, ty), mine))
    return out


def two_pads(spec):
    """(along, across, centre distance) in mm of the two pads a read's spec builds, or None: spec["land"] of a two-pin
    part (pads at +-pad_center, pad_length along that line), or a pad_table of two equal pads on one line."""
    if not isinstance(spec, dict):
        return None
    num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0
    land = spec.get("land") if isinstance(spec.get("land"), dict) else {}
    table = spec.get("pad_table") if isinstance(spec.get("pad_table"), dict) else {}
    if spec.get("exposed_pad") or spec.get("tab"):
        return None
    if table.get("pads"):
        pads = [p for p in table["pads"] if isinstance(p, dict)]
        if len(pads) != 2 or not all(num(p.get("w")) and num(p.get("h")) for p in pads) or \
                str(table.get("unit") or "mm").lower() != "mm" or \
                any(str(p.get("shape") or "").lower() in ("circle", "round") and p.get("drill") for p in pads):
            return None
        a, b = pads
        if (a["w"], a["h"]) != (b["w"], b["h"]) or any(p.get("drill") for p in pads):
            return None
        dx, dy = abs(float(b.get("x") or 0) - float(a.get("x") or 0)), abs(float(b.get("y") or 0) - float(a.get("y") or 0))
        if dx > 1e-6 and dy < 1e-6:
            return float(a["w"]), float(a["h"]), dx
        if dy > 1e-6 and dx < 1e-6:
            return float(a["h"]), float(a["w"]), dy
        return None
    if spec.get("pin_count") == 2 and all(num(land.get(k)) for k in ("pad_length", "pad_width", "pad_center")):
        return float(land["pad_length"]), float(land["pad_width"]), 2 * float(land["pad_center"])
    return None


def body(spec):
    """(smaller, larger) body size in mm from a read's spec (body_length / body_width, or a pad table's body box), or
    None."""
    def nom(v):
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
        if isinstance(v, dict):
            for k in ("nom", "max", "min"):
                if isinstance(v.get(k), (int, float)) and not isinstance(v.get(k), bool):
                    return float(v[k])
        return None
    dims = [nom(spec.get(k)) for k in ("body_length", "body_width")]
    box = ((spec.get("pad_table") or {}) if isinstance(spec.get("pad_table"), dict) else {}).get("body")
    if (None in dims) and isinstance(box, dict):
        try:
            dims = [abs(float(box["x2"]) - float(box["x1"])), abs(float(box["y2"]) - float(box["y1"]))]
        except (KeyError, TypeError, ValueError):
            pass
    if None in dims or min(dims) <= 0:
        return None
    return min(dims), max(dims)


def fits_body(pads, size, can=False, sure=False):
    """Why the two pads cannot be this body's land, or None. The pads must not touch (a gap of 0.1 mm or more), their
    span must reach at least 0.6 x the body's smaller size and at most 3 mm past its larger one, and the pad width must
    be at least a fifth of the smaller size and at most 1.5 mm more than the larger: a land made of a body or
    terminal number, or of the gap read as the span, rarely fits. Each pad is 0.15 to 0.5 x the body's larger size long,
    reaches to within 0.5 mm of the body's end, and has at least 0.4 of its length under the body: the pads sit at the
    body's two ends, so a read that took the gap for the span (pads pushed out past the body) or the span for the gap
    or the gap for the pad length (pads pulled in short of the ends) breaks one of these. The body's larger size is
    taken as its length along the pads' line. can: an SMD aluminium electrolytic, whose maker's land reaches past the
    square base plate (pads up to 0.6 x its size long, as narrow as 0.15 x it: Panasonic FK/FC 1.6-2.0 mm on 8.3-10.3,
    Wurth WCAP-ASLI 3.5 mm on 6.6, 2026-11-22).
    sure (2026-11-23, review round 2): the question is no longer "is this read's land likely" (a doubt only holds the
    part) but "can this other land be ruled out", where a wrong yes lets a turned read through. The limits above
    turn real lands away (a 0603's pads are 0.9 mm long on a 1.6 mm body, an SOD-323's 1.0 on 1.7), and the body
    may lie either way under the pads (4.06 x 4.45). So: pads 0.1 to 0.8 x the body long, ending up to 1 mm short of
    its ends, a fifth of their length under it, 0.1 x the smaller size wide to 2.5 mm more than the larger; and a
    reason is given only when the land fits neither with the larger nor with the smaller size along the pads' line."""
    small, large = min(size), max(size)
    if sure:
        lim = (0.1, 0.8, 1.0, 0.2, 0.1, 2.5)
        return _fit(pads, large, small, lim, "0.8 x") and _fit(pads, small, large, lim, "0.8 x")
    return _fit(pads, large, small, (0.15, 0.6, 0.5, 0.4, 0.15, 1.5) if can else (0.15, 0.5, 0.5, 0.4, 0.2, 1.5),
                "0.6 x" if can else "half")


def _fit(pads, length, other, lim, long_word):
    """fits_body for a body `length` mm along the pads' line and `other` mm across it. lim: the least and the most of
    the body's length a pad is long, how far short of the body's ends the pads may end (mm), the least part of a pad
    under the body, the least pad width as a part of the body's smaller size, and how much wider than the body's
    larger size a pad may be (mm)."""
    along, across, centre = pads
    short, long_, ends, under, narrow, wide = lim
    small, large = min(length, other), max(length, other)
    if centre - along < 0.1:
        return f"the pads would touch (gap {centre - along:.2f} mm)"
    if not 0.6 * small <= centre + along <= length + 3.0:
        return f"a span of {centre + along:.2f} mm does not fit a {small:g} x {large:g} mm body"
    if along < short * length:
        return f"a pad {along:g} mm long is too short for a {length:g} mm body"
    if along > long_ * length:
        return f"a pad {along:g} mm long is more than {long_word} the {length:g} mm body"
    inner, outer, end = (centre - along) / 2, (centre + along) / 2, length / 2
    if outer < end - ends:
        return (f"the pads end {end - outer:.2f} mm short of the body's ends (the land reaches {outer:.2f} mm from the "
                f"centre, the body {end:g} mm)")
    if min(outer, end) - inner < under * along:
        return "the pads lie mostly outside the body"
    if not narrow * small <= across <= large + wide:
        return f"a pad width of {across:g} mm does not fit a {small:g} x {large:g} mm body"
    return None


def _has(region, value, extra=0.0):
    return [n for n in region if abs(n[0] - value) <= _tol(n[1]) + extra]


def _lines(region):
    """The rows of a table in the drawing's numbers: printed lines (same y, unrotated) of two or more numbers whose
    places line up in columns with another such line (two or more numbers at the same x, within 3 pt), when those
    lines hold five or more numbers between them: more than one land needs in aligned rows. Dimension labels that
    happen to share a y are no table: their places do not line up with another line's.
    2026-11-23 (review): a table needed three rows of three numbers, so a family sheet of two sizes was no table,
    and a row that lost a number (a broken run, a dash) took the whole table rule with it; in both the other
    size's row agreed. An inch label over the same in mm is no second row: drawn_numbers leaves the inch twin out."""
    lines = []
    for n in sorted((n for n in region if not n[4]), key=lambda n: n[3]):
        if lines and abs(n[3] - lines[-1][-1][3]) <= 1.5:
            lines[-1].append(n)
        else:
            lines.append([n])
    lines = [ln for ln in lines if len(ln) >= 2]
    cols = lambda a, b: sum(any(abs(p[2] - q[2]) <= 3 for q in b) for p in a)
    rows = [a for a in lines if any(b is not a and cols(a, b) >= 2 for b in lines)]
    return rows if sum(len(r) for r in rows) >= 5 else []


def _line_text(line_text, y):
    """The whole printed text of the line at y (every unrotated run within 1.5 pt of it). A number read with its
    characters' places sits at their centre, above its run's baseline, so a run counts up to its text height
    (2026-11-23, review: a real PDF's table row never found its own name, which only held true parts back)."""
    return " ".join(t for ly, t, size in line_text or () if ly - 1.5 <= y <= ly + 0.75 * (size or 0) + 1.5)


def _series(part):
    """The part number's leading letters and digits ('NRS4018T100MDGJ' -> 'NRS4018'), for naming a table's row."""
    m = re.match(r"[A-Za-z]*\d+", re.sub(r"[^A-Za-z0-9]", "", str(part or "")))
    return m.group(0).upper() if m and len(m.group(0)) >= 4 else None


def _symmetry_says_otherwise(region, used):
    """True when the drawing labels a size on both pads (two equal numbers on one line or one column) with another
    number between them, and the read takes the one between for the pad length, or the pair for a spacing: the pair
    is the pads' own size, the one between is the spacing."""
    for a in region:
        for b in region:
            if b is a or a[1] != b[1] or a[4] or b[4]:
                continue
            row, col = abs(a[3] - b[3]) <= 2, abs(a[2] - b[2]) <= 2
            if not (row or col):
                continue
            sep = math.hypot(a[2] - b[2], a[3] - b[3])
            mx, my = (a[2] + b[2]) / 2, (a[3] + b[3]) / 2
            mid = [m for m in region if m is not a and m is not b and m[1] != a[1]
                   and math.hypot(m[2] - mx, m[3] - my) <= 0.2 * sep]
            if not mid:
                continue
            pl = used.get("pad length")
            if pl is not None and any(pl is m for m in mid):
                return True
            if any(t is a or t is b for r, t in used.items() if r != "pad length"):
                return True
    return False


def _on_one_line(a, s, x, edge, chain):
    """True when the places of three numbers show that a is a size along the line s (a gap) is measured on, and x is
    not. Text turned with its dimension line: a and s are printed in one direction and x in the other. Or, all three
    printed in one direction, a and s stand on one dimension line (the same y, or the same x, within 2 pt, 6 pt or
    more apart), x is on neither s's row nor its column, and a is nearer to s than a number beside the pads would be.
    The last is what tells a dimension line from its look-alike: a gap printed in the gap with the pad WIDTH beside
    the pads at the same height, and the pad length over a pad. There the number over the pad (x) marks the pad's
    centre, the pad reaches edge times as far from the gap's number, and the number beside it is farther still; so
    a at 0.8 x edge times x's distance or more could be that width, and the places show nothing.
    edge: 1 + pad length / centre distance of the land that takes x for the pad length. chain: s is a gap (a span's
    dimension line runs over both pads, so no pad length is printed on it)."""
    if a[4] == s[4] != x[4]:
        return True
    if not chain or not a[4] == s[4] == x[4]:
        return False
    same = lambda p, q, k: abs(p[k] - q[k]) <= 2
    if same(x, s, 2) or same(x, s, 3):
        return False                                # the width on the gap's own row or column: either could be along
    for k, j in ((3, 2), (2, 3)):                   # k: the coordinate a line shares, j: the one running along it
        d = abs(a[j] - s[j])
        if same(a, s, k) and d >= 6 and d < 0.8 * edge * abs(x[j] - s[j]):
            return True
    return False


def _layout(length, spacing, width, pads, turned, role):
    """What the places of three printed numbers say about a read that takes them for the pad length, the gap or span
    (role) and the pad width: "kept" when they show the pad length along the spacing's line (_on_one_line) and do
    not show the same of the width; "turned" when it is the other way round; None when they show nothing or both
    (a stacked span, a table row, labels on leader lines, a gap and a width at one height). pads, turned: the read's
    land and the land with its pad turned 90 degrees, as (along, across, centre distance).
    2026-11-23 (review): the printed numbers alone agree just as well with the pads turned. Round 2 of that review:
    "the same x OR the same y" was taken for one dimension line, so the gap in the gap and the pad width beside the
    pads at the same height made the turned read "kept" and the true one "turned"."""
    chain = role == "gap"
    kept = _on_one_line(length, spacing, width, 1 + turned[0] / turned[2], chain)
    other = _on_one_line(width, spacing, length, 1 + pads[0] / pads[2], chain)
    return None if kept == other else "kept" if kept else "turned"


def check_region(region, pads, line_text=None, part=None, size=None, can=False):
    """The verdict on one land drawing's numbers. pads: two_pads(). line_text: [(y, text, text height or 0)] of the page's
    unrotated runs, for naming a table row. size, can: the body (body()) and whether it is an electrolytic can, to judge the turned
    land against (fits_body). The read agrees when its pad width and two of the four sizes along the line through the
    pads (pad length, gap, span, centre distance: any two give the other two) are printed, each by a number of its own
    (one number cannot be two sizes)."""
    along, across, centre = pads
    w = _has(region, across)
    roles = [("pad length", _has(region, along)), ("gap", _has(region, centre - along, 0.01)),
             ("span", _has(region, centre + along, 0.01)), ("centre distance", _has(region, centre, 0.01))]
    found = [(r, h) for r, h in roles if h]
    if not w or len(found) < 2:
        missing = (["pad width"] if not w else []) + ([] if len(found) >= 2 else
                                                       ["two of pad length, gap, span and centre distance"])
        return {"status": "unchecked", "why": f"{' and '.join(missing)} not printed in the land drawing"}
    rows = _lines(region)
    in_rows = {id(n) for r in rows for n in r}
    name = _series(part)
    why = None
    for i, (r1, h1) in enumerate(found):
        for r2, h2 in found[i + 1:]:
            for t1 in h1:
                for t2 in h2:
                    for tw in w:
                        used = (t1, t2, tw)
                        if len({id(t) for t in used}) < 3:
                            continue              # one printed number standing for two sizes
                        if any(abs(tw[0] - v) <= _tol(tw[1]) + 0.01 for v in (along, centre - along, centre + along,
                                                                               centre)):
                            # the width is the same number as a size along the line (a square pad, or a gap as wide
                            # as the pads): the text cannot show the width was printed for the width, and a drawing
                            # that labels the pad length on both pads gives that number twice
                            why = why or ("the pad width is the same number as a size along the pads' line, so the "
                                          "text cannot show which is which")
                            continue
                        if max(math.hypot(p[2] - q[2], p[3] - q[3]) for p in used for q in used) > SPREAD:
                            continue              # numbers of two drawings (or a table far off): not one land
                        if any(id(t) in in_rows for t in used):
                            # a number of a table of sizes: all three on one row, and that row names this part
                            row = next((r for r in rows if all(any(t is n for n in r) for t in used)), None)
                            if row is None or not name or name not in re.sub(
                                    r"[^A-Za-z0-9]", "", _line_text(line_text, row[0][3])).upper():
                                why = why or ("the land drawing's numbers are a table of several sizes, and the "
                                              "read's numbers are not all on one row that names this part")
                                continue
                        if r1 == "pad length" and r2 in ("gap", "span"):
                            # 2026-11-23 (review): a read that swapped the pad's two sizes and worked the centre
                            # distance out from this gap / span is made of the same three numbers. It is ruled out
                            # by where they are printed, or by the turned land not fitting the body
                            turned = (across, along, centre - along + across if r2 == "gap" else centre + along - across)
                            lay = _layout(t1, t2, tw, pads, turned, r2)
                            if lay == "turned" or (lay is None and not (size and fits_body(turned, size, can, sure=True))):
                                why = why or (f"the printed numbers fit the pads turned 90 degrees as well (pad "
                                              f"{across:g} mm along the pads' line and {along:g} mm across it, "
                                              f"with the same {r2}), and " +
                                              ("they are printed as that turned land would be"
                                               if lay == "turned" else
                                               "neither their places nor the body size show which way the pads lie"))
                                continue
                        if _symmetry_says_otherwise(region, {r1: t1, r2: t2}):
                            why = why or ("the drawing prints one size on both pads and another between them, and "
                                          "the read gives them the other way round")
                            continue
                        return {"status": "agreed", "printed": {r1: t1[1], r2: t2[1], "pad width": tw[1]}}
    return {"status": "unchecked", "why": why or "the printed numbers do not give the read's land"}


def check(pages, spec, part=None):
    """pages: {page: [(x, y, text, size, vertical)]} (placed_runs). -> the verdict (see the module text). A PDF that
    prints two or more land drawings with different numbers (a family sheet: one land per size) gives no verdict: the
    read's numbers may be another size's drawing, which would agree just as well."""
    pads = two_pads(spec)
    if not pads:
        return {"status": "unchecked", "why": "not a two-pad land"}
    size = body(spec)
    if not size:
        return {"status": "unchecked", "why": "the read gives no body size to judge the land against"}
    from .datasheet import _electrolytic_can
    can = _electrolytic_can(spec)
    bad = fits_body(pads, size, can=can)
    if bad:
        return {"status": "unchecked", "why": bad}
    found = []
    for pg in sorted(pages):
        runs = pages[pg]
        line_text = [(y, t, size if len(rest) > 1 and rest[1] else 0) for x, y, t, size, *rest in runs if not rest[0]]
        found += [(pg, region, line_text) for _, region in regions(runs)]
    if not found:
        return {"status": "unchecked", "why": "no land drawing title in the PDF's text"}
    drawn = {tuple(sorted(n[1] for n in r)) for _, r, _ in found if len(r) >= 3}
    if len(drawn) > 1:
        return {"status": "unchecked", "why": f"the PDF prints {len(drawn)} land drawings with different numbers, so "
                                              "the text cannot show which one is this part's"}
    why = None
    for pg, region, line_text in found:
        v = check_region(region, pads, line_text, part, size, can)
        if v["status"] == "agreed":
            along, across, centre = pads
            if abs(along - across) > 0.011 and \
                    check_region(region, (across, along, centre), line_text, part, size, can)["status"] == "agreed":
                # the same numbers give the pads turned 90 degrees too (a centre distance and both pad sizes): the
                # text does not show which way they lie
                why = why or ("the printed numbers fit the pads turned 90 degrees as well, so the text does not show "
                              "which size runs along the line through the pads")
                continue
            return dict(v, page=pg)
        why = why or v["why"]
    return {"status": "unchecked", "why": why}


def placed_runs(pdf_bytes, limit=80):
    """{page: [(x, y, text, size, vertical, places)]}: the PDF's text as placed, one run per stretch of text with no
    wide gap, places = the (x, y) centre of each character (pdfium places every glyph where it is drawn, also in a
    line the PDF writes as one string with wide gaps: '2.0     2.0'). Turned text is marked vertical. pypdf, when
    pdfium cannot read the file: runs as the PDF writes them, no places."""
    try:
        import pypdfium2 as pdfium
        import pypdfium2.raw as raw
        doc = pdfium.PdfDocument(pdf_bytes)
    except Exception:
        return _pypdf_runs(pdf_bytes, limit)
    out = {}
    try:
        for i in range(min(len(doc), limit)):
            try:
                tp = doc[i].get_textpage()
                n = tp.count_chars()
                chars = []
                for k in range(n):
                    c = tp.get_text_range(k, 1)
                    l, b, r, t = tp.get_charbox(k)
                    chars.append((c, l, b, r, t, raw.FPDFText_GetCharAngle(tp.raw, k)))
            except Exception:
                continue
            out[i + 1] = _runs_of(chars)
    finally:
        doc.close()
    return out


def _runs_of(chars):
    """Characters [(char, left, bottom, right, top, angle)] -> runs, broken at a line break and at a gap wider than
    1.2 x the run's text height (a space the PDF writes as a character still joins words of one line)."""
    runs, cur = [], []

    def flush():
        txt = "".join(c[0] for c in cur).strip()
        if txt:
            boxes = [c for c in cur if not c[0].isspace()]
            vert = sum(1 for c in boxes if 0.785 < (c[5] % 3.1416) < 2.356) > len(boxes) / 2
            hgt = sorted((c[3] - c[1]) if vert else (c[4] - c[2]) for c in boxes)[len(boxes) // 2]
            x0, y0 = min(c[1] for c in boxes), min(c[2] for c in boxes)
            places = [((c[1] + c[3]) / 2, (c[2] + c[4]) / 2) for c in cur if not c[0].isspace()]
            runs.append((x0, y0, txt, max(hgt / 0.7, 1.0), vert, places))
        cur.clear()
    for ch in chars:
        c, l, b, r, t, a = ch
        if c in "\r\n":
            flush()
            continue
        if cur and not c.isspace():
            last = next((p for p in reversed(cur) if not p[0].isspace()), None)
            if last is not None:
                # 2026-11-23 (review): the gap is measured against the run's own text height (the median glyph, or
                # the coming one if larger), not the last glyph's: after a decimal point that is about 1 pt, and
                # '1.1' was broken into '1.' and '1', neither of them a number
                tall = sorted(max(p[4] - p[2], p[3] - p[1]) for p in cur if not p[0].isspace())
                h = max(tall[len(tall) // 2], t - b, r - l, 1.0)
                if max(l - last[3], last[1] - r, b - last[4], last[2] - t) > 1.2 * h:
                    flush()
        cur.append(ch)
    flush()
    return runs


def _pypdf_runs(pdf_bytes, limit=80):
    try:
        import io, logging
        import pypdf
        logging.getLogger("pypdf").setLevel(logging.ERROR)
        rd = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    except Exception:
        return {}
    out = {}
    for i, p in enumerate(rd.pages[:limit], 1):
        runs = []

        def seen(text, cm, tm, fd, fs, _r=runs):
            t = str(text or "").strip()
            if not t or "\n" in t:
                return
            m = pypdf.mult(tm, cm)
            if abs(m[4]) < 1e-6 and abs(m[5]) < 1e-6:
                return
            scale = math.hypot(m[0], m[1])
            if scale <= 0:
                return
            _r.append((m[4], m[5], t, (fs or 1) * scale, abs(m[1]) > abs(m[0]), None))
        try:
            p.extract_text(visitor_text=seen)
        except Exception:
            continue
        out[i] = runs
    return out


def check_pdf(pdf_bytes, spec, part=None):
    if not pdf_bytes:
        return {"status": "unchecked", "why": "no PDF to check the land against"}
    return check(placed_runs(pdf_bytes), spec, part)
