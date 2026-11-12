"""Pin names a datasheet prints with a bar over them (active low), read from the PDF's own text layer, in code.

A PDF's text gives SHDN for a pin printed with a bar over it: the bar is either a font of its own (Linear's
LinearHelvCondOvr: the letters are drawn with the bar) or a thin line drawn over the letters (TI, Analog Devices),
or a combining overline after each letter. The model reading the page images often copies the letters only, so the
symbol lost the bar (LT3652's SHDN, CHRG, FAULT; TPS70345's EN, RESET; full review 2026-11-10, 3.4).

overlined(data, pages) -> {name: (times printed with a bar, times printed without)} for whole words of 2 or more
letters / digits ('/' and '-' inside kept: DIS/FLT) on those pages. mark(pins, counts) gives a pin ~{NAME} when its
name, as read with no active-low mark, is printed with a bar twice or more and more often than without (a name also
used plainly, such as a gate driver's HO and LO in a figure with bars over other names, is left as read)."""
import re

FONT_BAR = re.compile(r"ovr|overline|overbar", re.I)
WORD = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9/\-]*[A-Za-z0-9])?")
MAX_PAGES = 60


def _apply(m, x, y):
    a, b, c, d, e, f = m
    return a * x + c * y + e, b * x + d * y + f


def _then(m, n):
    """The matrix that applies m, then n."""
    a, b, c, d, e, f = m
    return (a * n[0] + b * n[2], a * n[1] + b * n[3], c * n[0] + d * n[2], c * n[1] + d * n[3],
            e * n[0] + f * n[2] + n[4], e * n[1] + f * n[3] + n[5])


def _bars(page):
    """Thin horizontal strokes on the page, one per sub-path: (left, bottom, right, top) in page points. A PDF often
    draws every bar on a page as one path object (LTC4060: one object spanning the page), and a figure's bars sit in
    a form of their own with its own placement (TPS70345's application circuit), so each sub-path is read point by
    point and placed through every matrix above it."""
    import ctypes
    import pypdfium2.raw as R
    out, forms = [], {0: (1, 0, 0, 1, 0, 0)}
    x, y = ctypes.c_float(), ctypes.c_float()
    for o in page.get_objects(max_depth=4):
        lv = getattr(o, "level", 0)
        up = forms.get(lv, (1, 0, 0, 1, 0, 0))        # the placement of the form this object sits in
        if o.type == R.FPDF_PAGEOBJ_FORM:
            forms[lv + 1] = _then(o.get_matrix().get(), up)
            continue
        if o.type != R.FPDF_PAGEOBJ_PATH:
            continue
        m = _then(o.get_matrix().get(), up)           # the path's points are in its own space
        n = R.FPDFPath_CountSegments(o.raw)
        if n <= 0 or n > 20000:
            continue
        pts = []

        def flush():
            if len(pts) >= 2:
                xs, ys = [p[0] for p in pts], [p[1] for p in pts]
                if max(ys) - min(ys) < 1.2 and max(xs) - min(xs) > 1.0:
                    out.append((min(xs), min(ys), max(xs), max(ys)))
        for i in range(n):
            seg = R.FPDFPath_GetPathSegment(o.raw, i)
            if not R.FPDFPathSegment_GetPoint(seg, ctypes.byref(x), ctypes.byref(y)):
                continue
            if R.FPDFPathSegment_GetType(seg) == R.FPDF_SEGMENT_MOVETO:
                flush()
                pts = []
            pts.append(_apply(m, x.value, y.value))
        flush()
    return out


def _page_counts(page, counts):
    import ctypes
    import pypdfium2.raw as R
    tp = page.get_textpage()
    try:
        n = tp.count_chars()
        if not n:
            return
        text = tp.get_text_range(0, n)
        bars = _bars(page)
        fonts = {}
        buf = ctypes.create_string_buffer(256)
        flags = ctypes.c_int()

        def font_bar(i):
            R.FPDFText_GetFontInfo(tp.raw, i, buf, 256, ctypes.byref(flags))
            f = buf.value.decode("latin-1", "replace")
            if f not in fonts:
                fonts[f] = bool(FONT_BAR.search(f))
            return fonts[f]

        for m in WORD.finditer(text):
            w = m.group()
            if len(re.sub(r"[^A-Za-z0-9]", "", w)) < 2 or w.isdigit():
                continue
            nxt = text[m.end():m.end() + 1]
            if nxt == "̅":                       # combining overline: the bar is in the text itself
                barred = True
            elif all(font_bar(i) for i in range(m.start(), m.end())):
                barred = True
            elif bars:
                box = [tp.get_charbox(i) for i in range(m.start(), m.end())]
                l, r = min(x[0] for x in box), max(x[2] for x in box)
                top, bot = max(x[3] for x in box), min(x[1] for x in box)
                h = max(top - bot, 1.0)
                # one line over the whole word, no longer than it (a table's rule runs far past a word)
                barred = any(b[0] <= l + 1.5 and b[2] >= r - 1.5 and b[2] - b[0] <= 1.2 * (r - l) + 4.0
                             and top - 0.3 * h <= b[1] <= top + 0.8 * h for b in bars)
            else:
                barred = False
            c = counts.setdefault(w, [0, 0])              # as printed: "fault" in a sentence is no FAULT pin
            c[0 if barred else 1] += 1
    finally:
        tp.close()


_SEEN = {}


def overlined(data, pages=None):
    """{name as printed: [printed with a bar, printed without]} over the 1-based pages (None: the first MAX_PAGES).
    Kept for the PDF and pages last asked for: the first read's symbol and its cross-check ask the same."""
    import hashlib
    key = (hashlib.sha256(data).hexdigest(), tuple(pages) if pages else None)
    if key not in _SEEN:
        if len(_SEEN) > 32:
            _SEEN.clear()
        _SEEN[key] = _overlined(data, pages)
    return {k: list(v) for k, v in _SEEN[key].items()}


def _overlined(data, pages=None):
    try:
        import pypdfium2 as P
    except ImportError:
        return {}
    counts = {}
    try:
        doc = P.PdfDocument(data)
    except Exception:
        return {}
    try:
        n = len(doc)
        for p in (pages or range(1, min(n, MAX_PAGES) + 1)):
            if isinstance(p, int) and 1 <= p <= n:
                try:
                    _page_counts(doc[p - 1], counts)
                except Exception:
                    continue
    finally:
        doc.close()
    return counts


def mark(pins, counts):
    """pins [{num, name, ...}] -> (pins with ~{NAME} where the datasheet prints NAME with a bar, [names marked])."""
    from .eval.score import active_low
    names = [p.get("name") for p in pins]
    out, done = [], []
    for p in pins:
        name = str(p.get("name") or "").strip()
        c = counts.get(name)
        if name and c and c[0] >= 2 and c[0] > c[1] and not active_low(name, names):
            p = dict(p, name="~{" + name + "}")
            done.append(name)
        out.append(p)
    return out, done
