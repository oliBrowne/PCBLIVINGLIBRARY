"""A free, model-free second source for a symbol's pin table: the datasheet's own text layer.

check(page_texts, pins) compares a pin list (number, name) with the pin tables printed in the PDF's text, line by
line, with no model and no network. A pin agrees when one line of a page carries its name and its number, and the
pins as a whole agree when, on ONE page, every pin is found on its own line AND every number sits in the same column
of those lines (a table with SOIC and SOT-23 columns prints each name with several numbers: the read must have taken
one column for all of them, not a mix). The result is

    {"status": "agreed", "page": n, "column": c, "pins": k}     every pin found, one page, one column
    {"status": "disagreed", "why": ...}                         a name printed with other numbers only, in one column
    {"status": "unchecked", "why": ...}                         no text layer, no table found, ambiguous: no verdict

Only "agreed" lets a part through without a person; "unchecked" keeps the hold it had. A scanned PDF (no text) is
always unchecked. Pins named N.C. / NC / DNC are skipped (tables often print them as one row "NC 1, 5, 8")."""
import re

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
        nums = [(i, c) for i, c in enumerate(cells) if _CELL_NUM.match(c)]
        if not nums:
            continue
        rows.append((cells, nums))
    return rows


def _find(rows, name, num):
    """Columns (index among the line's number cells) where `num` is printed on a line that names `name`, and whether
    the name was printed at all (with any numbers)."""
    target = norm_name(name)
    cols, seen = set(), False
    for cells, nums in rows:
        first = nums[0][0]
        words = cells[:first]                          # the name is printed before the numbers ("OUT A 1 1 O ...")
        if not words:                                  # or after them ("1 OUT A ...": number first)
            later = [c for c in cells[1:] if not _CELL_NUM.match(c)]
            words = later[:3]
        cand = {norm_name("".join(words[k:j])) for k in range(len(words)) for j in range(k + 1, min(len(words), k + 3) + 1)}
        if target not in cand:
            continue
        seen = True
        for col, (_, c) in enumerate(nums):
            if str(num) in _numbers(c):
                cols.add(col)
    return cols, seen


def check(page_texts, pins):
    """page_texts: {page number: text}; pins: [(num, name)] -> verdict dict (see the module doc)."""
    pins = [(str(n).strip(), str(nm or "").strip()) for n, nm in pins or [] if str(n).strip()]
    pins = [(n, nm) for n, nm in pins if nm and not NC.match(nm)]
    if len(pins) < 2:
        return {"status": "unchecked", "why": "fewer than two named pins to compare"}
    if not any(str(t or "").strip() for t in (page_texts or {}).values()):
        return {"status": "unchecked", "why": "the PDF has no text layer (scanned?)"}
    best = None
    for page, text in sorted((page_texts or {}).items()):
        rows = _rows(text)
        if not rows:
            continue
        per = [_find(rows, nm, n) for n, nm in pins]
        named = sum(1 for _, seen in per if seen)
        if named < len(pins):
            if best is None or named > best[1]:
                best = (page, named, None)
            continue
        common = set.intersection(*[cols for cols, _ in per])
        if common:
            return {"status": "agreed", "page": page, "column": min(common), "pins": len(pins)}
        if all(cols for cols, _ in per):
            best = (page, named, "the numbers are in different columns of the table (a mix of packages?)")
            continue
        miss = [f"{nm}={n}" for (n, nm), (cols, _) in zip(pins, per) if not cols]
        if len(per) >= 3 and len(miss) * 3 <= len(per):  # most pins right, a few printed with other numbers: a real
            return {"status": "disagreed", "page": page,      # difference, not a page that only mentions the names
                    "why": f"page {page} prints {', '.join(miss[:4])} with other numbers"}
        best = (page, named, f"page {page}: {', '.join(miss[:4])} not printed with that number")
    if best is None:
        return {"status": "unchecked", "why": "no pin table found in the text layer"}
    return {"status": "unchecked", "why": best[2] or f"page {best[0]} names {best[1]} of {len(pins)} pins"}


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
