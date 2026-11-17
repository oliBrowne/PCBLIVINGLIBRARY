"""A free, model-free second source for a symbol's pin table: the datasheet's own text layer.

check(page_texts, pins) compares a pin list (number, name) with the pin tables printed in the PDF's text, line by
line, with no model and no network. A pin agrees when one line of a page carries its name and its number, and the
pins as a whole agree when, on ONE page, every pin is found on its own line AND every number sits in the same column
of those lines (a table with SOIC and SOT-23 columns prints each name with several numbers: the read must have taken
one column for all of them, not a mix). The result is

    {"status": "agreed", "page": n, "column": c, "pins": k}     every pin found, one page, one column
    {"status": "disagreed", "why": ...}                         a name printed with other numbers only, in one column
    {"status": "unchecked", "why": ...}                         no text layer, no table found, ambiguous: no verdict

A table whose other columns give the same pins other numbers (SOIC and SOT-23 columns, or a variant with its
supplies swapped) is unchecked even when one column agrees: the text does not say which column is this part's. So is
an agreeing table when another page names every pin too but prints some only with other numbers (a variant's own
table on another page), whichever page comes first.
Only "agreed" lets a part through without a person; "unchecked" keeps the hold it had. A scanned PDF (no text) is
always unchecked. Pins named N.C. / NC / DNC are skipped (tables often print them as one row "NC 1, 5, 8")."""
import re

EMPTY = {"—", "–", "-", "--", "‒", "N/A", "NA"}     # a dash in a table column: no pin in that package
NC = re.compile(r"(?i)^(?:N\.?C\.?|DNC|NO\s*CONNECT(?:ION)?|RESERVED|RSVD)$")
_OVER = re.compile(r"[~{}̅/#*_]|\\")
_LIST = re.compile(r"(?<=\d)\s*,\s*(?=\d)")          # "2, 3" (one cell listing two pins) -> "2,3"
_RANGE = re.compile(r"\b(\d{1,3})\s*[–-]\s*(\d{1,3})\b")   # "5–12" -> 5..12
_CELL_NUM = re.compile(r"^(?:\d{1,3}|[A-Z]{1,2}\d{1,2})(?:,(?:\d{1,3}|[A-Z]{1,2}\d{1,2}))*$")


def norm_name(s):
    """Pin name for comparing: case, spaces, active-low marks and minus/plus signs as printed."""
    s = str(s or "").replace("−", "-").replace("–", "-").replace("–", "-")
    s = _OVER.sub("", s)
    return re.sub(r"\s+", "", s).upper()


def _cells(line):
    """A text line -> its tokens, with "2, 3" kept as one cell and ranges written out."""
    line = _LIST.sub(",", line.replace("–", "-").replace("–", "-"))
    return line.split()


def _numbers(cell):
    out = []
    for part in cell.split(","):
        m = _RANGE.fullmatch(part)
        if m and int(m.group(1)) < int(m.group(2)) <= 300:
            out += [str(i) for i in range(int(m.group(1)), int(m.group(2)) + 1)]
        else:
            out.append(part)
    return out


def _rows(text):
    """Lines of a page -> [(joined name candidates, [cells that are pin-number cells])]."""
    rows = []
    for raw in str(text or "").splitlines():
        cells = _cells(raw)
        if len(cells) < 2:
            continue
        nums, started = [], False                      # the table's number cells: one run of them ("1 1", "6,7,8 1",
        for i, c in enumerate(cells):                  # "— 4"), not the numbers in the description after it
            if _CELL_NUM.match(c):
                nums.append((i, c))
                started = True
            elif started and c in EMPTY:
                nums.append((i, ""))
            elif started:
                break
        nums = [(i, c) for i, c in nums]
        while nums and not nums[-1][1]:
            nums.pop()
        if not any(c for _, c in nums):
            continue
        rows.append((cells, nums))
    return rows


def _find(rows, name, num, found=None):
    """Columns (index among the line's number cells) where `num` is printed on a line that names `name`, and whether
    the name was printed at all (with any numbers). found: {column: [the line's number cells]} of the lines that
    matched, for the variant check in check()."""
    target = norm_name(name)
    cols, seen = set(), False
    for cells, nums in rows:
        first = nums[0][0]
        words = cells[:first]                          # the name is printed before the numbers ("OUT A 1 1 O ...")
        if not words:                                  # or after them ("1 OUT A ...": number first)
            words = cells[nums[-1][0] + 1:][:3]
        cand = {norm_name("".join(words[k:j])) for k in range(len(words)) for j in range(k + 1, min(len(words), k + 3) + 1)}
        if target not in cand:
            continue
        seen = True
        for col, (_, c) in enumerate(nums):
            if c and str(num) in _numbers(c):
                cols.add(col)
                if found is not None:
                    found.setdefault(col, [x for _, x in nums])
    return cols, seen


def variant_columns(lines, col):
    """The other number columns of the matched lines that give some pin another number than column `col` does (a
    table with SOIC and SOT-23 columns, or MCP6L01 / MCP6L01R): '' when every column agrees or there is no other."""
    out = []
    width = max((len(c) for f in lines for c in f.values()), default=0)
    for k in range(width):
        if k == col:
            continue
        for f in lines:
            cells = f.get(col)
            if cells and k < len(cells) and set(_numbers(cells[k])) != set(_numbers(cells[col]))                     and not set(_numbers(cells[k])) & set(_numbers(cells[col])):
                out.append(f"column {k + 1}")
                break
    return ", ".join(out)


def _top(rows, pins):
    """The highest pin number printed on the lines of `rows` that name one of `pins` (0 when none)."""
    names, top = {norm_name(nm) for _, nm in pins}, 0
    for cells, nums in rows:                          # a table row only: the name is all that comes before the numbers
        before = cells[:nums[0][0]]                   # ("4OUT 20 Output"), or what starts after them ("1 OUTA ..."),
        after = cells[nums[-1][0] + 1:][:3]           # never a name inside a sentence or a spec line ("VCC supply
        cand = {norm_name("".join(before))} if before else {norm_name("".join(after[:j])) for j in (1, 2, 3)}
        if not before and not after or not cand & names:    # current 25 100")
            continue
        top = max([top] + [int(x) for _, c in nums for x in _numbers(c) if x.isdigit()])
    return top


def _figure_rows(text):
    """figure_lines' "NAME NUMBER" lines as _rows gives a table's (a name such as E0 or A1 is not taken for a number)."""
    rows = []
    for line in str(text or "").splitlines():
        cells = line.split()
        if len(cells) >= 2 and _INT.match(cells[-1]):
            rows.append((cells, [(len(cells) - 1, cells[-1])]))
    return rows


def check(page_texts, pins, figures=(), pin_count=None):
    """page_texts: {page number: text}; pins: [(num, name)]; figures: [(page, text)] of pinout drawings
    (figure_lines), each read as a table of its own; pin_count: the package's, when known -> verdict dict (see the
    module doc). A table elsewhere that names these pins otherwise but prints a pin number above this part's highest
    (TL074's 20-pin LCCC table after its 14-pin SOIC one) is another package's: it is not a doubt about this one."""
    pins = [(str(n).strip(), str(nm or "").strip()) for n, nm in pins or [] if str(n).strip()]
    most = max([int(n) for n, _ in pins if n.isdigit()] + [pin_count if isinstance(pin_count, int) else 0])
    pins = [(n, nm) for n, nm in pins if nm and not NC.match(nm)]
    if len(pins) < 2:
        return {"status": "unchecked", "why": "fewer than two named pins to compare"}
    units = sorted((page_texts or {}).items()) + [(pg, t) for pg, t in figures or ()]
    if not any(str(t or "").strip() for _, t in units):
        return {"status": "unchecked", "why": "the PDF has no text layer (scanned?)"}
    best, agreed, conflicts = None, None, []
    for i, (page, text) in enumerate(units):
        drawn = i >= len(units) - len(figures or ())
        rows = _figure_rows(text) if drawn else _rows(text)
        if not rows:
            continue
        lines = [{} for _ in pins]
        per = [_find(rows, nm, n, f) for (n, nm), f in zip(pins, lines)]
        named = sum(1 for _, seen in per if seen)
        if named < len(pins):
            if best is None or named > best[1]:
                best = (page, named, None)
            continue
        common = set.intersection(*[cols for cols, _ in per])
        if common:
            col = min(common)
            other = variant_columns(lines, col)
            if other:                               # another package or variant of the part numbers these pins other
                return {"status": "unchecked", "page": page,      # ways: which column is this part's is not in
                        "why": f"page {page}'s table gives these pins other numbers in another column ({other}): which "
                               "column is this part's package or variant is not in the text"}
            if agreed is None:
                agreed = {"status": "agreed", "page": page, "column": col, "pins": len(pins),
                          "where": "pin drawing" if drawn else "pin table"}
            continue
        miss = [f"{nm}={n}" for (n, nm), (cols, _) in zip(pins, per) if not cols]
        if miss and most and _top(rows, pins) > most:  # a bigger package's table (TL074M's 20-pin LCCC): not this one
            continue
        if miss:                                       # every pin named on this page, some only with other numbers:
            conflicts.append((page, miss))             # maybe another variant's table (MCP6L01 / MCP6L01R on two pages)
        if agreed is not None:
            continue
        if all(cols for cols, _ in per):
            best = (page, named, "the numbers are in different columns of the table (a mix of packages?)")
            continue
        if len(per) >= 3 and len(miss) * 3 <= len(per):  # most pins right, a few printed with other numbers: a real
            return {"status": "disagreed", "page": page,      # difference, not a page that only mentions the names
                    "why": f"page {page} prints {', '.join(miss[:4])} with other numbers"}
        best = (page, named, f"page {page}: {', '.join(miss[:4])} not printed with that number")
    if agreed is not None:
        if conflicts:                                  # a table on another page names the same pins with other numbers:
            pg, miss = conflicts[0]                    # which table is this part's is not in the text, whichever came first
            return {"status": "unchecked", "page": agreed["page"],
                    "why": f"page {agreed['page']}'s table agrees, but page {pg} prints {', '.join(miss[:4])} with other "
                           "numbers (another package or variant?): which table is this part's is not in the text"}
        return agreed
    if best is None:
        return {"status": "unchecked", "why": "no pin table found in the text layer"}
    return {"status": "unchecked", "why": best[2] or f"page {best[0]} names {best[1]} of {len(pins)} pins"}


_INT = re.compile(r"^\d{1,3}$")
_NAME = re.compile(r"^[A-Za-z0-9+\-−–_/#~.()]{1,12}$")


def _merged(runs):
    """Text runs (x, y, text, size) with a name split across runs joined (pypdf gives "V" and the subscript "CC" as
    two, the subscript a little lower): a run that starts where an earlier one on its line ends. Pin numbers are never
    joined."""
    out = []
    for x, y, t, sz in sorted(runs, key=lambda r: r[0]):
        for i, (px, py, pt, psz) in enumerate(out):
            if not _INT.match(t) and not _INT.match(pt) and abs(y - py) <= 0.45 * psz \
                    and -0.5 <= x - (px + 0.6 * psz * len(pt)) <= 0.3 * psz:
                out[i] = (px, py, pt + t, psz)
                break
        else:
            out.append((x, y, t, sz))
    return out


def figure_lines(runs):
    """Pinout drawings on one page -> one text per drawing, "NAME NUMBER" a line, for check(figures=...).
    runs: [(x, y, text, size)] as the PDF places its text (page_figures). A drawing is columns of pin numbers (each
    column at least two numbers one above the other, at one x) whose numbers together are exactly 1..K, each once
    (a lone number beside them, SOT-23's pin 3, joins when it completes the run). Each number takes the one name on its
    line nearest to it, when that name is clearly nearer (1.6 x) than any other on the line and its own nearest
    number is this one: a name halfway between two numbers is left out (unchecked, never guessed). Rotated text
    (a QFN's top and bottom rows) is not read here."""
    runs = _merged(runs)
    nums = [(x, y, int(t), sz) for x, y, t, sz in runs if _INT.match(t) and 0 < int(t) <= 300]
    names = [(x, y, t, sz) for x, y, t, sz in runs if not _INT.match(t) and _NAME.match(t)]
    cols = []                                          # numbers at one x (left or right edge), one above the other
    for n in sorted(nums, key=lambda r: (r[0], -r[1])):
        for c in cols:
            if abs(c[0][0] - n[0]) <= 1.5 or abs(c[0][0] + 0.6 * c[0][3] * len(str(c[0][2]))
                                                   - n[0] - 0.6 * n[3] * len(str(n[2]))) <= 1.5:
                c.append(n)
                break
        else:
            cols.append([n])
    cols = [c for c in cols if len(c) >= 2 and len({round(r[1]) for r in c}) == len(c)]
    figs = []                                          # columns side by side (rows overlapping, under 250 pt apart)
    for c in sorted(cols, key=lambda c: c[0][0]):
        lo, hi = min(r[1] for r in c), max(r[1] for r in c)
        for f in figs:
            if lo <= f["hi"] + 1 and hi >= f["lo"] - 1 and c[0][0] - f["x"] < 250:
                f["nums"] += c
                f["lo"], f["hi"], f["x"] = min(lo, f["lo"]), max(hi, f["hi"]), c[0][0]
                break
        else:
            figs.append({"nums": list(c), "lo": lo, "hi": hi, "x0": c[0][0], "x": c[0][0]})
    out = []
    for f in figs:
        have = {r[2] for r in f["nums"]}
        k = max(have)
        for n in nums:                                 # a lone number completing 1..K (SOT-23's pin 3)
            if n not in f["nums"] and (n[2] == k + 1 or n[2] not in have) and n[2] <= k + 1 \
                    and f["lo"] - 2 <= n[1] <= f["hi"] + 2 and f["x0"] - 150 <= n[0] <= f["x"] + 150:
                f["nums"].append(n)
                have.add(n[2])
                k = max(have)
        vals = [r[2] for r in f["nums"]]
        if sorted(vals) != list(range(1, len(vals) + 1)) or len(vals) < 3:
            continue
        line_of = lambda a, b: abs(a[1] - b[1]) <= 0.45 * max(a[3], b[3]) + 0.5
        lines = []
        for n in f["nums"]:
            near = sorted((abs(m[0] - n[0]), m) for m in names if line_of(m, n) and abs(m[0] - n[0]) <= 60)
            if not near or len(near) > 1 and near[1][0] < 1.6 * near[0][0]:
                continue
            nm = near[0][1]
            mine = sorted((abs(nm[0] - o[0]), o[2]) for o in nums if line_of(nm, o))
            if mine[0][1] != n[2] or len(mine) > 1 and mine[1][0] < 1.6 * mine[0][0]:
                continue                               # the name is another number's, or halfway between two
            lines.append(f"{nm[2]} {n[2]}")
        if lines:
            out.append("\n".join(lines))
    return out


def check_pdf(pdf_bytes, pins, pin_count=None):
    """check() on a PDF: its text layer's tables and its pinout drawings (page_figures). No PDF: unchecked."""
    if not pdf_bytes:
        return check({}, pins)
    return check(page_texts(pdf_bytes), pins, figures=page_figures(pdf_bytes), pin_count=pin_count)


def page_figures(pdf_bytes, limit=80):
    """[(page, text)] of the pinout drawings in the PDF (figure_lines on each page's placed text), or [] when it
    cannot be read."""
    try:
        import io, logging
        import pypdf
        logging.getLogger("pypdf").setLevel(logging.ERROR)
        rd = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    except Exception:
        return []
    out = []
    for i, p in enumerate(rd.pages[:limit], 1):
        runs = []

        def seen(text, cm, tm, fd, fs, _r=runs):
            t = str(text or "").strip()
            if not t or "\n" in t:
                return
            m = pypdf.mult(tm, cm)
            if abs(m[1]) > 0.01 or abs(m[2]) > 0.01 or m[0] <= 0:      # rotated or mirrored text: not read here
                return
            _r.append((m[4], m[5], t, fs * m[0] if fs else m[0]))
        try:
            p.extract_text(visitor_text=seen)
        except Exception:
            continue
        out += [(i, f) for f in figure_lines(runs)]
    return out


def page_texts(pdf_bytes, limit=80):
    """{page: text} from the PDF (pypdf), or {} when it cannot be read."""
    try:
        import io, logging
        import pypdf
        logging.getLogger("pypdf").setLevel(logging.ERROR)
        rd = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        out = {}
        for i, p in enumerate(rd.pages[:limit], 1):
            try:
                out[i] = p.extract_text() or ""
            except Exception:
                out[i] = ""
        return out
    except Exception:
        return {}
