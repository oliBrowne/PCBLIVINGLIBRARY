"""A free, model-free second source for a symbol's pin table: the datasheet's own text layer.

check(page_texts, pins) compares a pin list (number, name) with the pin tables printed in the PDF's text, line by
line, with no model and no network. A pin agrees when one line of a page carries its name and its number, and the
pins as a whole agree when, on ONE page, every pin is found on its own line AND every number sits in the same column
of those lines (a table with SOIC and SOT-23 columns prints each name with several numbers: the read must have taken
one column for all of them, not a mix). The result is

    {"status": "agreed", "page": n, "column": c, "pins": k}     every pin found, one page, one column
    {"status": "disagreed", "why": ...}                         a name printed with other numbers only, in one column
    {"status": "unchecked", "why": ...}                         no text layer, no table found, ambiguous: no verdict
                                                                ("doubt": True when names are printed with other
                                                                numbers or columns disagree: a doubt, not a gap)

A table whose other columns give the same pins other numbers (SOIC and SOT-23 columns, or a variant with its
supplies swapped) is unchecked even when one column agrees: the text does not say which column is this part's. So is
an agreeing table when another page names every pin too but prints some only with other numbers (a variant's own
table on another page), whichever page comes first.
Only "agreed" lets a part through without a person; "unchecked" keeps the hold it had. A scanned PDF (no text) is
always unchecked. Pins named N.C. / NC / DNC are not looked for by name (tables often print them as one row
"NC 1, 5, 8"), but once the named pins agree the table's row for such a pin's number must be a no-connect row
(_quiet; no row the check recognises for it: unchecked), and the active-low marks are compared on their own
(_marks)."""
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


# Names two sources write differently for one pin. KiCad draws an op-amp's or comparator's inputs as '+' and '-' and
# its rails as V+ / V-; datasheets print '1IN+', '+IN A', 'INPUT2-', 'VCC', 'GND'. A CMOS part's rails are VDD / VSS in
# one text and VCC / GND in the other. The number must still be the same: only the spelling is set aside.
_INPUT = {"+": {"+", "IN+", "+IN", "INPUT+", "+INPUT", "VIN+"}, "-": {"-", "IN-", "-IN", "INPUT-", "-INPUT", "VIN-"}}
RAILS = ({"V+", "+V", "VCC", "VDD", "VCC+", "VS", "VS+", "+VS", "VDD+"},
         {"V-", "-V", "VEE", "VSS", "VCC-", "VS-", "-VS", "GND", "VEE-"})


def name_class(name):
    """'+' / '-' for an amplifier input however its channel is written (1IN+, IN1+, +INA, INPUT2- ...), 'pos' / 'neg'
    for a supply rail's usual names, else None. One channel mark (1-4 or A-D) may stand anywhere in an input's name."""
    s = norm_name(name)
    for sign, forms in _INPUT.items():
        if s in forms or any(s[:i] + s[i + 1:] in forms for i, c in enumerate(s) if c in "1234ABCD"):
            return sign
    return "pos" if s in RAILS[0] else "neg" if s in RAILS[1] else None


IO = {"vin": {"IN", "VIN", "VI", "INPUT"}, "vout": {"OUT", "VOUT", "VO", "OUTPUT"}}
GROUND_ADJ = {"ADJGND", "GNDADJ"}                   # a fixed regulator's ground pin, printed 'ADJ/GND' or 'GND/ADJ'


def loose_class(name):
    """name_class, plus 'vin' / 'vout' for a regulator's or switch's single input / output however it is spelt (IN,
    VIN, VI; OUT, VOUT, VO) and 'neg' for 'ADJ/GND'."""
    got = name_class(name)
    if got:
        return got
    s = norm_name(name)
    if s in GROUND_ADJ:
        return "neg"
    return next((k for k, forms in IO.items() if s in forms), None)


def loose_names(pins):
    """The pins' names that may be matched by class (name_class) instead of letter for letter: an amplifier input
    written as the bare sign ('+', '-': KiCad's way, which no datasheet prints), and a rail when it is the symbol's
    only name of its class (a part with VCC and VDD, or GND and V-, keeps them apart by their exact names)."""
    names = {norm_name(nm) for _, nm in pins}
    out = {n for n in names if n in ("+", "-")}
    for sign in ("+", "-"):                           # the part's only input of that sign (INA226: Vin+ is TI's IN+)
        mine = {n for n in names if name_class(n) == sign}
        if len(mine) == 1:
            out |= mine
    for rails in RAILS:
        mine = names & rails
        if len(mine) == 1:
            out |= mine
    for forms in IO.values():                          # the part's only input / output pin (a regulator's VI, VO)
        mine = names & forms
        if len(mine) == 1:
            out |= mine
    return out


_TAB = re.compile(r"(?<=\d)\s*,\s*(?i:tab)\b")


def _cells(line):
    """A text line -> its tokens, with "2, 3" kept as one cell and ranges written out."""
    line = _TAB.sub("", line)                           # 'OUT 2, Tab': pin 2 (the tab is that pin too)
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


_LONE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9+\-−–_/#~]{0,11}$")


def _joined_lines(text):
    """A page's lines, with a table row the text layer broke in two joined again: a line that is one pin-like name
    ('Vref', 'RS') followed by a line that is one pin number ('5') and then the row's type and description ('O SN65HVD230
    and ...') is the row 'Vref 5'. A number after a sentence, and a drawing's labels and values ('IN+B' / '2' / 'IN+A 2'
    in a layout example), are left alone."""
    lines = str(text or "").splitlines()
    out, i = [], 0
    while i < len(lines):
        a = lines[i].strip()
        b = lines[i + 1].strip() if i + 1 < len(lines) else ""
        c = (lines[i + 2].split() if i + 2 < len(lines) else [])
        if _LONE_NAME.match(a) and re.fullmatch(r"\d{1,3}", b) and len(c) >= 3 and not re.fullmatch(r"[\d,]+", c[1]):
            out.append(f"{a} {b}")
            i += 2
            continue
        out.append(lines[i])
        i += 1
    return out


def _rows(text, at=None):
    """Lines of a page -> [(joined name candidates, [cells that are pin-number cells])]. at: a list that is given
    each row's line number (in _joined_lines)."""
    rows = []
    for li, raw in enumerate(_joined_lines(text)):
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
        if len(nums) >= 2 and nums[0][0] == 0 and not nums[0][1][0].isdigit() and "," not in nums[0][1] \
                and (nums[1][1].isdigit() or not nums[1][1]):
            nums = nums[1:]                            # "A0 1 1 — 1": a pin NAMED like a ball, then its numbers
        while nums and not nums[-1][1]:
            nums.pop()
        if not any(c for _, c in nums):
            continue
        rows.append((cells, nums))
        if at is not None:
            at.append(li)
    if any(len(n) >= 2 and n[0][0] == 0 and n[1][0] == 1 and n[0][1].isdigit() and n[1][1].isdigit() for _, n in rows):
        for k, (cells, nums) in enumerate(rows):       # a table with two number columns: the dash that leads a row is
            if cells[0] in EMPTY and nums[0][0] == 1:  # the first column's empty cell ("- 3 CE": no such pin in that
                rows[k] = (cells, [(0, "")] + nums)    # package), not the row's name
    return rows


def _find(rows, name, num, found=None, loose=False):
    """Columns (index among the line's number cells) where `num` is printed on a line that names `name`, and whether
    the name was printed at all (with any numbers). found: {column: [the line's number cells]} of the lines that
    matched, for the variant check in check(). loose: the name may be matched by its class (loose_names), but only
    when no line of the page prints it letter for letter."""
    target = norm_name(name)
    named = []
    for cells, nums in rows:
        first = nums[0][0]
        words = cells[:first]                          # the name is printed before the numbers ("OUT A 1 1 O ...")
        if not words:                                  # or after them ("1 OUT A ...": number first)
            words = cells[nums[-1][0] + 1:][:3]
        named.append({norm_name("".join(words[k:j])) for k in range(len(words)) for j in range(k + 1, min(len(words), k + 3) + 1)})
    if target in ("+", "-"):                           # a bare sign is printed only as a whole name: the '-' cell
        exact = any(norm_name("".join(c[:n[0][0]] or c[n[-1][0] + 1:][:3])) == target   # of '15 IN2 -' in a
                    and all(re.fullmatch(r"[\d,]+", x) for _, x in n) for c, n in rows)        # drawing is IN2-'s
    else:
        exact = any(target in c for c in named)
    kind = loose_class(target) if loose and not exact else None
    cols, seen = set(), False
    for (cells, nums), cand in zip(rows, named):
        if target not in cand and not (kind and any(loose_class(c) == kind for c in cand)):
            continue
        seen = True
        for col, (_, c) in enumerate(nums):
            if c and str(num) in _numbers(c):
                cols.add(col)
                if found is not None:
                    found.setdefault(col, [x for _, x in nums])
    return cols, seen


def _other_package(lines, col, k, most, table=()):
    """Column k of the matched lines is plainly another package than column `col`'s numbered one: it prints a pin
    number above this part's highest (a 14-pin TSSOP column beside the 8-pin ones, a 16-pin QFN beside the 14-pin
    SOIC), or ball names (B1, C2: a DSBGA column) where this part's pins are plain numbers. table: every row of the
    table's span (its number cells), so a pin the symbol leaves unnamed counts too."""
    span = [c for f in lines for c in [f.get(col)] if c] + [c for c in table if len(c) > max(col, k)]
    vals = [x for c in span if k < len(c) for x in _numbers(c[k]) if x]
    mine = [x for c in span if col < len(c) for x in _numbers(c[col]) if x]
    if not vals or not mine or not all(x.isdigit() for x in mine):
        return False
    return any(not x.isdigit() for x in vals) or bool(most) and any(int(x) > most for x in vals if x.isdigit())


def variant_columns(lines, col, most=0, table=()):
    """The other number columns of the matched lines that give some pin another number than column `col` does (a
    table with SOIC and SOT-23 columns, or MCP6L01 / MCP6L01R): '' when every column agrees or there is no other.
    most: this part's highest pin number, table: the table's rows: a column that is plainly another package
    (_other_package) is not counted."""
    out = []
    width = max((len(c) for f in lines for c in f.values()), default=0)
    for k in range(width):
        if k == col or _other_package(lines, col, k, most, table):
            continue
        if any(c and k < len(c) and not any(_numbers(c[k])) and any(_numbers(c[col])) for c in (f.get(col) for f in lines)):
            continue                                   # no number for a pin this part names: a package without that pin
        for f in lines:
            cells = f.get(col)
            if cells and k < len(cells) and set(_numbers(cells[k])) != set(_numbers(cells[col])) \
                    and not set(_numbers(cells[k])) & set(_numbers(cells[col])):
                out.append(f"column {k + 1}")
                break
    return ", ".join(out)


def _span(rows, lines, col):
    """The number cells of every row of the table the pins matched in (column `col`): from the first to the last
    matched row, and on to the rows next to them while they have as many number cells (its unnamed pins, its N.C.s)."""
    hit = [f[col] for f in lines if f.get(col)]
    cells = [[x for _, x in nums] for _, nums in rows]
    at = [i for i, c in enumerate(cells) if c in hit]
    if not at:
        return []
    lo, hi, wide = min(at), max(at), max(len(c) for c in hit)
    while lo > 0 and len(cells[lo - 1]) == wide:
        lo -= 1
    while hi + 1 < len(cells) and len(cells[hi + 1]) == wide:
        hi += 1
    return cells[lo:hi + 1]


def _top(rows, pins, loose=()):
    """The highest pin number printed on the lines of `rows` that name one of `pins` (0 when none). loose: the names
    matched by class (loose_names), as _find matches them: 4IN+ names the symbol's '+'."""
    names, top = {norm_name(nm) for _, nm in pins}, 0
    kinds = {loose_class(n) for n in loose} - {None}
    for cells, nums in rows:                          # a table row only: the name is all that comes before the numbers
        before = cells[:nums[0][0]]                   # ("4OUT 20 Output"), or what starts after them ("1 OUTA ..."),
        after = cells[nums[-1][0] + 1:][:3]           # never a name inside a sentence or a spec line ("VCC supply
        cand = {norm_name("".join(before))} if before else {norm_name("".join(after[:j])) for j in (1, 2, 3)}
        if not before and not after or not (cand & names or kinds and any(loose_class(c) in kinds for c in cand)):
            continue                                  # current 25 100")
        top = max([top] + [int(x) for _, c in nums for x in _numbers(c) if x.isdigit()])
    return top


def _row_names(cells, nums):
    """What a table row is named, when the line is one: the name is all that comes before its numbers ("4OUT 20
    Output"), or what starts after them ("1 OUTA ..."); never a name inside a sentence or a spec line."""
    before, after = cells[:nums[0][0]], cells[nums[-1][0] + 1:][:3]
    return {norm_name("".join(before))} if before else {norm_name("".join(after[:j])) for j in (1, 2, 3) if after[:j]}


def _a_table_names(rows, names, loose=()):
    """Does some line of `rows` name one of `names` as a table row does (_row_names; a loose name by its class)? A
    page of spec lines that only mentions the pins ('VI = 5.5 V or GND 0 to 5.5 V 5 uA') does not."""
    want = {norm_name(n) for n in names}
    kinds = {loose_class(n) for n in want if n in loose} - {None}
    return any(c in want or kinds and loose_class(c) in kinds for cells, nums in rows for c in _row_names(cells, nums))


def _fewer_channels(rows, pins):
    """The page is the table of a device with fewer amplifiers than the symbol: the symbol has two or more inputs of
    one sign ('+' on 3 and 5), the page fewer rows naming an input of that sign (the single's IN+ row beside the
    dual's table elsewhere in a family datasheet)."""
    for sign in ("+", "-"):
        mine = sum(1 for _, nm in pins if norm_name(nm) == sign)
        if mine >= 2 and sum(1 for cells, nums in rows if any(name_class(c) == sign for c in _row_names(cells, nums))) < mine:
            return True
    return False


def _figure_rows(text):
    """figure_lines' "NAME NUMBER" lines as _rows gives a table's (a name such as E0 or A1 is not taken for a number)."""
    rows = []
    for line in str(text or "").splitlines():
        cells = line.split()
        if len(cells) >= 2 and _INT.match(cells[-1]):
            rows.append((cells, [(len(cells) - 1, cells[-1])]))
    return rows


# Package families a pin table's header may name, one per number column ('NAME SOT-23 SC70', 'NAME X2SON SOT-23').
# Longest first, so 'TSOT23' is not read as 'SOT23' and 'HTSSOP' not as 'TSSOP'. A name not in this list is no
# package: a header with one is not used.
FAMILIES = sorted({"X2SON", "WSON", "VSON", "USON", "SON", "WQFN", "VQFN", "UQFN", "QFN", "TQFN", "TDFN", "WDFN", "UDFN",
                   "DFN", "DSBGA", "WCSP", "BGA", "LGA", "HTSSOP", "TSSOP", "MSOP", "VSSOP", "HVSSOP", "SSOP", "QSOP",
                   "TSOP", "SOIC", "SO", "PDIP", "DIP", "SOT223", "TSOT23", "SOT23", "SOT353", "SOT363", "SOT323",
                   "SOT553", "SOT563", "SOT89", "SC70", "SC88", "SC59", "SC74", "TO220", "TO92", "TO263", "TO252", "DPAK",
                   "D2PAK", "LCCC", "CDIP", "CFP", "TQFP", "LQFP", "QFP", "PLCC", "CSP", "SOIJ"}, key=len, reverse=True)


def _family(token):
    """'SOT-23' / 'SOT23' / 'SOT-23-5' / '8-SOIC' / 'SC70' -> its family ('SOT23', 'SOIC', 'SC70'), else ''. A token
    that is a family and more ('SOT-23-THN', 'SOICW') is none."""
    t = str(token or "").strip().strip("(),;:").upper()
    cands = [t]
    m = re.match(r"^\d{1,3}-(.+)$", t)                   # '8-SOIC': DigiKey's pin count first
    if m:
        cands.append(m.group(1))
    for c in list(cands):
        m = re.match(r"^(.+?)-\d{1,3}$", c)                # 'SOT-23-5', 'SOIC-8': the pin count last
        if m:
            cands.append(m.group(1))
    for c in cands:
        if c.replace("-", "") in FAMILIES:
            return c.replace("-", "")
    return ""


def package_families(texts):
    """The package families a part's land names and case texts name ('SOT-23-5', 'Package_SO:SOIC-8_3.9x4.9mm',
    '8-SOIC (0.154", 3.90mm Width)', 'SOT-353_SC-70-5' -> {'SOT353', 'SC70'})."""
    out = set()
    for t in texts or ():
        t = str(t or "").split(":")[-1]
        for tok in re.split(r"[_\s,]+", re.sub(r"\([^)]*\)", " ", t)):
            f = _family(tok)
            if f:
                out.add(f)
    return out


def _package_header(text, width):
    """The columns of the page's pin-table header that names one package per number column ('NAME SOT-23 SC70' for a
    two-column table -> [('SOT23', 'SOT-23'), ('SC70', 'SC70')]: family, as printed), or None: no such line, several different ones, or one whose
    package names are not `width`."""
    found = []
    for line in str(text or "").splitlines():
        toks = line.split()
        fams = [_family(t) for t in toks]
        named = [f for f in fams if f]
        if len(named) < 2:
            continue
        rest = [t for t, f in zip(toks, fams) if not f]
        if any(not re.fullmatch(r"(?i)NAME|PIN|NO\.?|FUNCTION|DESCRIPTION|TYPE|I/O|[A-Z]{1,4}\d?|\([A-Z0-9]{1,5}\)", t)
               for t in rest):
            continue                                  # a sentence that names packages, not a header ('DBV' / '(D)' codes may stand beside them)
        if len(named) == width and named not in found:
            found.append(named)
            printed = [t.strip("(),;:") for t, f in zip(toks, fams) if f]
    return list(zip(found[0], printed)) if len(found) == 1 else None


_TITLE = re.compile(r"(?i)\bpin\s+(?:functions?|descriptions?|configurations?|assignments?)\s*[:\-–]\s*(.+)$")
_DEVICE = re.compile(r"\b[A-Z]{1,6}\d{2,}[A-Za-z0-9]*")


def _titled_for(text):
    """The devices a page's pin table titles name: 'Table 4-1. Pin Functions: TL071x' -> {'TL071'} (the title's
    trailing lower-case x is its wildcard); a page with no such title: empty."""
    out = set()
    for line in str(text or "").splitlines():
        m = _TITLE.search(line.strip())
        if m:
            out |= {d for d in (re.sub(r"x+$", "", t).upper() for t in _DEVICE.findall(m.group(1))) if len(d) >= 4}
    return out


def _is_part(devices, part):
    """Does one of a title's device names begin this part number ('TL072' and TL072CDR; not 'TL072M', not 'TL071')?"""
    p = re.sub(r"[^A-Z0-9]", "", str(part or "").upper())
    return bool(p) and any(p.startswith(d) for d in devices)


def _named_column(text, lines, per, mine):
    """A table whose number columns are packages, its header naming each ('NAME SOT-23 SC70'): (the column of this
    part's family, a 'where' text) when exactly one header column is one of `mine` and every pin is printed with its
    number in that column; else None (the table stays unchecked)."""
    if not mine:
        return None
    width = max((len(c) for f in lines for c in f.values()), default=0)
    head = _package_header(text, width)
    if not head:
        return None
    cols = [k for k, (f, _) in enumerate(head) if f in mine]
    if len(cols) != 1 or not all(cols[0] in c for c, _ in per):
        return None
    if any(len(f.get(cols[0]) or ()) != width for f in lines):
        return None                                   # a row the text layer printed with fewer cells ('VSS 8 444 4 44'):
        #                                               its numbers may not sit under the header's columns
    return cols[0], f"pin table, its {head[cols[0]][1]} column (the header names one package per column)"


ABOVE = 8                 # the lines over a pin table that are its heading: its title and the lines just above


def _heading(text, pins, col, loose=()):
    """A pin table's own heading on its page, as text: the ABOVE printed lines over the first row the pins matched
    in column `col`, and the table down to the last such row (a header may name the package in a row of its own). ''
    when the rows are not found. Review 2026-11-23, round 2 (C2): what names a text pin order's package."""
    at = []
    rows, lines = _rows(text, at), _joined_lines(text)
    hit = [at[k] for k in range(len(rows)) for n, nm in pins
           if col in _find([rows[k]], nm, n, loose=norm_name(nm) in loose)[0]]
    if not hit:
        return ""
    lo, left = min(hit), ABOVE
    while lo > 0 and left:
        lo -= 1
        left -= bool(lines[lo].strip())
    return "\n".join(lines[lo:max(hit) + 1])


def _printed_low(rows, name, num, col, names=()):
    """Does the row of pin `num` (its number in column `col`) print `name` with an active-low mark the text layer
    itself carries (/EN, EN#, nRESET, a combining overline)? A bar drawn as a line is not in the text."""
    from .eval.score import active_low
    target = norm_name(name)
    for cells, nums in rows:
        if col >= len(nums) or str(num) not in _numbers(nums[col][1]):
            continue
        words = cells[:nums[0][0]] or cells[nums[-1][0] + 1:][:3]
        if any(norm_name(raw) == target and active_low(raw, names) for k in range(len(words))
               for j in range(k + 1, min(len(words), k + 3) + 1) for raw in ["".join(words[k:j])]):
            return True
    return False


def _drawn_low(lines, name, num):
    """Is `name` on the row of pin `num` printed with a bar over it, as the reader sees the page (bar_rows' lines)?
    True / False when every line that has the name within three words of the number says the same, else None (the
    row was not found, or two lines differ)."""
    target, hits = norm_name(name), []
    for line in lines or ():
        at = [i for i, (_, w, _) in enumerate(line) if str(num) in _numbers(w.strip(",;:()").replace("–", "-"))]
        mine = [(min(abs(i - k) for k in at), b) for i, (_, w, b) in enumerate(line)
                if at and i not in at and norm_name(w.strip(",;:()")) == target]
        if mine and min(mine)[0] <= 3:                 # the copy nearest the number (two as near, a drawing printed
            hits += [b for d, b in mine if d == min(mine)[0]]   # twice on one line: both count)
    return None if not hits or len(set(hits)) > 1 else hits[0]


def _marks(pins, rows, col, pages, bars=None):
    """The active-low marks of the pins that agreed, compared on their own (review 2026-11-23, 1.9): norm_name drops
    the mark, so ~{Q} and Q are both 'Q' and each is found on both rows; a PDF draws the bar as a line, so the text
    has 'Q' twice. -> None when no mark is in doubt, else the verdict: 'disagreed' when the PDF marks a pin active
    low (in its text, or a bar drawn over that row's name: bars) and the symbol does not; 'unchecked' when the symbol
    marks a pin active low, or has the same name with and without the mark on two pins, and neither the text nor a
    drawn bar shows which row is the barred one."""
    from .eval.score import active_low
    names = [nm for _, nm in pins]
    low = {n: active_low(nm, names) for n, nm in pins}
    printed = {n: _printed_low(rows, nm, n, col, names) for n, nm in pins}
    group = {}
    for n, nm in pins:
        group.setdefault(norm_name(nm), []).append(n)
    try:
        seen = bars(pages) if bars else {}
    except Exception:
        seen = {}
    lines = [ln for pg in pages for ln in (seen or {}).get(pg) or ()]
    flipped, unsettled = [], []
    for n, nm in pins:
        twins = [m for m in group[norm_name(nm)] if m != n]
        pair = any(low[m] != low[n] for m in twins)             # Q beside ~{Q}
        bar = _drawn_low(lines, nm, n) if lines else None
        pdf = True if printed[n] or bar else False if bar is False or any(printed[m] for m in twins) else None
        if pdf and not low[n]:
            flipped.append(f"pin {n} {nm}")
        elif low[n] and not pdf or pair and pdf is None:
            unsettled.append(f"pin {n} {nm}")
    page = pages[0]
    if flipped:
        return {"status": "disagreed", "page": page,
                "why": f"the pin numbers agree with page {page}, but it prints {', '.join(flipped[:4])} with an "
                       "active-low bar or mark and the symbol's pin has none"}
    if unsettled:
        return {"status": "unchecked", "page": page,
                "why": f"the pin numbers agree with page {page}, but the text layer cannot show an active-low bar (a "
                       f"drawn line), and {'no bar is drawn over the name on the row of' if lines else 'there is no PDF to look for the bar of'} "
                       f"{', '.join(unsettled[:4])}"}
    return None


_NC_ROW = re.compile(r"(?i)^(?:N\.?\s*/?\s*C\.?|DNC|DNU|NU|NO(?:T|N)?[\s-]*(?:INTERNAL(?:LY)?\s*)?CONNECT(?:ION|ED)?|"
                     r"DO\s*NOT\s*CONNECT|RESERVED|RSVD|NOT\s*USED|UNUSED|[-–—‒]+)$")


# What a no-connect row may not say of its pin (review 2026-11-23, round 3, minor: 'RESERVED 3 Must be tied to VIN'
# counted as not connected): an order to connect it. 'Do not connect', 'must be left open', 'may be tied to GND' are none.
_TIE = re.compile(r"(?i)\b(?:must|shall|should|needs?\s+to|ha(?:s|ve)\s+to|required?)\b[^.;]*?\b(?:tie|tied|connect|connected|"
                  r"pull|pulled|ground|grounded|short|shorted|bias|biased|bypass|bypassed)\b|"
                  r"^\W*(?:tie|connect|pull|short|ground|bypass)\b")
_NOT_TIE = re.compile(r"(?i)\bnot\b|\bleft\b|\bleave\b|float|\bopen\b|unconnected|\bmay\b|\bcan\b|\boptional")
_NC_SAID = re.compile(r"(?i)\bno(?:t|n)?[\s-]*(?:internal(?:ly)?\s*)?(?:connect|bond|used|pin)|\bunconnected|\bunused|\bleave\b|"
                      r"\bleft\b|\bfloat|\bopen\b|\bN\.?\s*/?\s*C\b|\breserved\b")
_DASHES = re.compile(r"^[-\u2013\u2014\u2012\s]+$")
# A line between two rows that starts another table (round 3, A7): a table's title or header, or a short line that
# names a package or a version ('SOT-23-5 version'). A row's description running on to the next line is none.
_TABLE_HEAD = re.compile(r"(?i)\bpin\s+(?:functions?|descriptions?|configurations?|assignments?|names?|outs?)\b|"
                         r"^\s*(?:table|figure|fig\.)\s*\d|^\s*(?:(?:PIN|NAME|NO\.?|NUMBER|SYMBOL|FUNCTION|DESCRIPTION|TYPE|I/O)\s*){2,}$")
_VERSION = re.compile(r"(?i)\b(?:versions?|variants?|options?|packages?)\b")


def _breaks(text, rows=None):
    """The rows of a page (their index in _rows) before which another table starts: a blank line, or a heading
    (_TABLE_HEAD; a line of six words or fewer that names a package or a version), stands between the row and the one
    before it. Review 2026-11-23, round 3 (A7): the rows of a second table lower on the page, or of one above, were
    taken for rows of the table the pins agreed with."""
    at = []
    got = _rows(text, at)
    if rows is not None and len(got) != len(rows):
        return set()
    lines, out = _joined_lines(text), set()
    for k in range(1, len(at)):
        for ln in lines[at[k - 1] + 1:at[k]]:
            if not ln.strip() or _TABLE_HEAD.search(ln) \
                    or len(ln.split()) <= 6 and (_VERSION.search(ln) or package_words(ln)):
                out.add(k)
                break
    return out


def _quiet(quiet, pins, rows, col, pages, loose=(), cut=()):
    """The pins the symbol calls NC, or leaves without a name, confirmed too (review 2026-11-23, 1.10: they were
    dropped before comparing, so a read that called EN and FB 'NC' agreed on the six pins left). In the table the
    named pins agreed with (its rows from the first to the last matched one, and the rows next to them of the same
    width), the row that prints such a pin's number in the agreeing column must be a no-connect row (NC, N.C., DNC,
    RESERVED, a dash, 'No internal connection'). -> None, or the verdict: 'disagreed' when the table names
    the number of a pin the symbol calls NC otherwise, 'unchecked' for one the symbol leaves blank (check's
    `blank`), and 'unchecked' when the table has no row the check recognises for such a pin's number (review
    2026-11-23, round 2, C4: no row counted as nothing against it, and a row is not recognised when its number is
    printed '3*', '3(1)', '03', after a dash, first in a table that prints the name first, or under its name on the
    line above: the read's 'NC' for EN then agreed). Only a no-connect row confirms, and EVERY row of the table that
    prints the number must be one (round 3, A7: 'NC 3 No connection (fixed)' beside 'EN 3 Enable' confirmed the
    read's NC). cut: the rows before which another table starts (_breaks): the table is the stretch between two of
    them that holds most of the agreeing rows; another pin table on the page that names the number otherwise leaves
    it 'unchecked' (which of the two is this part's is not in the text)."""
    if not quiet:
        return None
    page = pages[0]
    said = lambda ps: f"{', '.join(ps[:4])}, which the symbol {'calls not connected' if any(nm for _, nm in quiet) else 'gives no name'}"
    names = [(n, norm_name(nm), loose_class(nm) if norm_name(nm) in loose else None) for n, nm in pins]
    cells = [[x for _, x in nums] for _, nums in rows]

    def cands(k):
        c, nums = rows[k]
        words = c[:nums[0][0]] or c[nums[-1][0] + 1:][:3]
        return {" ".join(words[i:j]) for i in range(len(words)) for j in range(i + 1, min(len(words), i + 3) + 1)}

    def has(k, n):
        return col < len(cells[k]) and str(n) in _numbers(cells[k][col])

    def lead(k):
        """The dash cells between a row's name and its first number: the columns in which it has no pin."""
        c, nums = rows[k]
        before = c[:nums[0][0]]
        return sum(1 for w in before if w in EMPTY) if any(w not in EMPTY for w in before) else 0

    def nc_row(k):
        """Is the row's whole name a no-connect one ('NC', 'N.C.', a dash, 'No internal connection')? Not a part of
        it: 'EN - 3' is EN's row with a dash column, not a no-connect row."""
        c, nums = rows[k]
        before = [w for w in c[:nums[0][0]] if w not in EMPTY] or c[:nums[0][0]]
        after = c[nums[-1][0] + 1:]
        for name, rest in ([(before, after)] if before else [(after[:j], after[j:]) for j in (1, 2, 3)]):
            if not name or not _NC_ROW.match(" ".join(name).strip(" .,;:()")):
                continue
            said = " ".join(rest)
            if _TIE.search(said) and not _NOT_TIE.search(said):
                return False                           # 'RESERVED 3 Must be tied to VIN': a pin that needs a net
            if _DASHES.match(" ".join(name)) and said.strip(" .,;:()-\u2013\u2014\u2012") and not _NC_SAID.search(said):
                return False                           # '- 3 Enable input': a dash for a name, and a function after it
            return True
        return False
    hit = [k for k in range(len(rows)) for n, nm, kind in names if has(k, n)
           and any(norm_name(c) == nm or kind and loose_class(norm_name(c)) == kind for c in cands(k))]
    if not hit:                                        # the table's rows are not found again: nothing was confirmed
        return {"status": "unchecked", "page": page,
                "why": f"page {page}'s table has no no-connect row for {said([f'pin {n}' for n, _ in quiet])}: not confirmed"}
    part = lambda k: sum(1 for b in cut if b <= k)       # which table of the page row k stands in
    count = {}
    for k in set(hit):
        count[part(k)] = count.get(part(k), 0) + 1
    own = max(count, key=lambda t: (count[t], -t))       # the table most of the named pins agreed in
    hit = [k for k in hit if part(k) == own]
    lo, hi, wide = min(hit), max(hit), max(len(cells[k]) for k in hit)
    while lo > 0 and len(cells[lo - 1]) == wide and part(lo - 1) == own:
        lo -= 1
    while hi + 1 < len(rows) and len(cells[hi + 1]) == wide and part(hi + 1) == own:
        hi += 1
    first = sum(1 for k in set(hit) if rows[k][1][0][0] == 0) * 2 > len(set(hit))     # the table prints the number first
    named, blank, unseen, elsewhere = [], [], [], []
    title = lambda k: " ".join(rows[k][0][:rows[k][1][0][0]] or rows[k][0][rows[k][1][-1][0] + 1:][:1])
    for n, nm in quiet:
        # another pin table on the page (two or more of the named pins agree in it too) that names this number
        elsewhere += [f"pin {n} {title(k)}" for k in range(len(rows)) if count.get(part(k), 0) >= 2 and part(k) != own
                      and has(k, n) and (rows[k][1][0][0] == 0) == first and not nc_row(k)]
        mine = [k for k in range(lo, hi + 1) if has(k, n) and (rows[k][1][0][0] == 0) == first]
        if not mine:                                   # no row for its number that the check can read: it may be
            # left out of the table, or printed in a way not recognised. A no-connect row whose number stands in this
            # column once the dashes before it are counted as cells confirms it (TI's 'N/C - 4 - No internal
            # connection' under X2SON and SOT-23 columns: _rows starts the cells at the first number)
            if not any(len(cells[k]) < wide and (rows[k][1][0][0] == 0) == first and nc_row(k) and 0 <= col - lead(k) < len(cells[k])
                       and lead(k) and str(n) in _numbers(cells[k][col - lead(k)]) for k in range(lo, hi + 1)):
                unseen.append(f"pin {n}")
            continue
        other = [k for k in mine if not nc_row(k)]
        if not other:
            continue                                   # no-connect rows only: confirmed
        c, nums = rows[other[0]]                       # a row that names it otherwise, whatever the rows beside it say
        what = title(other[0])
        if _NC_ROW.match(what.strip(" .,;:()")):       # a no-connect name, and a row that gives the pin a net or a function
            what += f" ('{' '.join(c[nums[-1][0] + 1:][:6])}')"
        (named if nm else blank).append(f"pin {n} {what}")
    if named:
        return {"status": "disagreed", "page": page,
                "why": f"page {page}'s table names {', '.join(named[:4])}, and the symbol calls "
                       f"{'it' if len(named) == 1 else 'them'} not connected"}
    if blank:
        return {"status": "unchecked", "page": page,
                "why": f"page {page}'s table names {', '.join(blank[:4])}, and the symbol gives "
                       f"{'it' if len(blank) == 1 else 'them'} no name: not compared"}
    if unseen:
        return {"status": "unchecked", "page": page,
                "why": f"page {page}'s table has no no-connect row for {said(unseen)}: not confirmed (the row may be "
                       "left out, or printed in a way the text check does not read)"}
    if elsewhere:
        return {"status": "unchecked", "page": page,
                "why": f"page {page} has another pin table that names {', '.join(elsewhere[:4])}, and the symbol calls "
                       f"{'it' if len(elsewhere) == 1 else 'them'} not connected: which table is this part's is not in the text"}
    return None


def check(page_texts, pins, figures=(), pin_count=None, part=None, package=(), bars=None, blank=False):
    """page_texts: {page number: text}; pins: [(num, name)]; figures: [(page, text)] of pinout drawings
    (figure_lines), each read as a table of its own; bars: bar_rows for these pages, when there is a PDF (the bars
    drawn over active-low names: _marks); pin_count: the package's, when known; part: the part number,
    when known (a table on another page titled only for other devices of the family is then not a doubt about the
    agreeing table titled for this part) -> verdict dict (see the
    module doc). A table elsewhere that names these pins otherwise but prints a pin number above this part's highest
    (TL074's 20-pin LCCC table after its 14-pin SOIC one) is another package's: it is not a doubt about this one."""
    pins = [(str(n).strip(), str(nm or "").strip()) for n, nm in pins or [] if str(n).strip()]
    most = max([int(n) for n, _ in pins if n.isdigit()] + [pin_count if isinstance(pin_count, int) else 0])
    # confirmed after the named ones agree (_quiet). blank: a pin with no name too (a datasheet read's symbol; not a
    # KiCad symbol's, which draws an op-amp's outputs and a gate's pins without a name)
    quiet = [(n, nm) for n, nm in pins if NC.match(nm) or blank and not nm]
    pins = [(n, nm) for n, nm in pins if nm and not NC.match(nm)]
    if len(pins) < 2:
        return {"status": "unchecked", "why": "fewer than two named pins to compare"}
    units = sorted((page_texts or {}).items()) + [(pg, t) for pg, t in figures or ()]
    if not any(str(t or "").strip() for _, t in units):
        return {"status": "unchecked", "why": "the PDF has no text layer (scanned?)"}
    loose = loose_names(pins)
    mine = package_families(package)
    best, agreed, conflicts, at = None, None, [], None     # at: the agreeing table's rows, column and page(s)
    cut = set()                                            # the rows of that page before which another table starts
    for i, (page, text) in enumerate(units):
        drawn = i >= len(units) - len(figures or ())
        rows = _figure_rows(text) if drawn else _rows(text)
        if not rows:
            continue
        lines = [{} for _ in pins]
        per = [_find(rows, nm, n, f, loose=norm_name(nm) in loose) for (n, nm), f in zip(pins, lines)]
        named = sum(1 for _, seen in per if seen)
        if named < len(pins):
            if best is None or named > best[1]:
                best = (page, named, None)
            continue
        common = set.intersection(*[cols for cols, _ in per])
        if common:
            col = min(common)
            other = variant_columns(lines, col, most, _span(rows, lines, col))
            named = _named_column(text, lines, per, mine) if other and not drawn else None
            if named is not None:                   # the header names each column's package, and one is this part's
                col, other = named
            elif other:                             # another package or variant of the part numbers these pins other
                return {"status": "unchecked", "page": page, "doubt": True,   # ways: which column is this part's is not in
                        "why": f"page {page}'s table gives these pins other numbers in another column ({other}): which "
                               "column is this part's package or variant is not in the text"}
            if agreed is None:
                agreed = {"status": "agreed", "page": page, "column": col, "pins": len(pins),
                          "where": "pin drawing" if drawn else other if named is not None else "pin table"}
                if not drawn:                       # the table's own heading: what may name its package (package_doubt)
                    agreed["above"] = _heading(text, pins, col, loose)
                at = (rows, col, [page])
                cut = set() if drawn else _breaks(text, rows)
            continue
        miss = [f"{nm}={n}" for (n, nm), (cols, _) in zip(pins, per) if not cols]
        if miss and most and _top(rows, pins, loose) > most:  # a bigger package's table (TL074M's 20-pin LCCC): not this one
            continue
        missed = [nm for (n, nm), (cols, _) in zip(pins, per) if not cols]
        if miss and (not _a_table_names(rows, missed, loose) or _fewer_channels(rows, pins)):
            continue                                   # spec lines that only mention the names, or a single's table
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
    if agreed is None:                                 # a table that runs over two pages: read as one
        pages = sorted((page_texts or {}).items())
        for (pa, ta), (pb, tb) in zip(pages, pages[1:]):
            rows = _rows(ta) + _rows(tb) if pb == pa + 1 else []
            lines = [{} for _ in pins]
            per = [_find(rows, nm, n, f, loose=norm_name(nm) in loose) for (n, nm), f in zip(pins, lines)] if rows else []
            common = set.intersection(*[cols for cols, _ in per]) if per else set()
            if common and not variant_columns(lines, min(common), most, _span(rows, lines, min(common))):
                agreed = {"status": "agreed", "page": pa, "column": min(common), "pins": len(pins),
                          "where": f"pin table (pages {pa} and {pb})", "above": ""}
                at = (rows, min(common), [pa, pb])
                cut = _breaks(ta) | {k + len(_rows(ta)) for k in _breaks(tb)}     # the page break itself is none
                break
    if agreed is not None and conflicts and part and _is_part(_titled_for((page_texts or {}).get(agreed["page"])), part):
        conflicts = [(pg, miss) for pg, miss in conflicts           # 'Pin Functions: TL071x' beside the agreeing
                     if not _titled_for((page_texts or {}).get(pg))  # 'Pin Functions: TL072x': another device's table
                     or _is_part(_titled_for((page_texts or {}).get(pg)), part)]
    if agreed is not None:
        if conflicts:                                  # a table on another page names the same pins with other numbers:
            pg, miss = conflicts[0]                    # which table is this part's is not in the text, whichever came first
            return {"status": "unchecked", "page": agreed["page"], "doubt": True,
                    "why": f"page {agreed['page']}'s table agrees, but page {pg} prints {', '.join(miss[:4])} with other "
                           "numbers (another package or variant?): which table is this part's is not in the text"}
        return _quiet(quiet, pins, *at, loose=loose, cut=cut) or _marks(pins, *at, bars=bars) or agreed
    if best is None:
        return {"status": "unchecked", "why": "no pin table found in the text layer"}
    out = {"status": "unchecked", "why": best[2] or f"page {best[0]} names {best[1]} of {len(pins)} pins"}
    if best[2] or conflicts:                           # names printed with other numbers somewhere: a doubt about the
        out["doubt"] = True                            # read, which no other check may settle (pin_drawing)
    return out


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


TERMINAL = {"GATE": "G", "SOURCE": "S", "DRAIN": "D", "BASE": "B", "EMITTER": "E", "COLLECTOR": "C"}
_TERMINAL_SETS = ({"G", "S", "D"}, {"B", "E", "C"})


_PIN_WORD = re.compile(r"(?i)^pin\s?([1-3])$")


def _terminal_pairs(runs, words, letters=(), pin_words=True):
    """(x, y, key, number) for each terminal name on a page (a word of 'words': a dict, or a function of the run's
    text that gives its key or None; or a capital letter of 'letters')
    whose pin number 1-3 is right beside it: the number within two text heights, clearly nearer (1.6 x) than any
    other, and the name that number's nearest too. A number between two names: nothing."""
    names, nums = [], []
    for x, y, t, sz in runs:
        if not sz or sz <= 0:
            continue
        key = (words(t) if callable(words) else words.get(t.upper())) if len(t) > 1 else t if t in letters else None
        pin = pin_words and _PIN_WORD.match(t)         # Infineon: 'Gate' over 'pin1', 'Drain' over 'pin 3'
        if key:
            names.append((x + 0.25 * sz * len(t), y, key, sz))
        elif t in ("1", "2", "3") or pin:
            nums.append((x + 0.25 * sz * len(t), y, int(pin.group(1) if pin else t), sz))
    dist = lambda a, b: ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5

    def nearest(a, others):
        near = sorted((dist(a, o), i) for i, o in enumerate(others))
        if not near or near[0][0] > 2 * a[3] or len(near) > 1 and near[1][0] < 1.6 * near[0][0]:
            return None
        return near[0][1]
    pairs = []
    for i, nm in enumerate(names):
        j = nearest(nm, nums)
        if j is not None and nearest(nums[j], names) == i:
            pairs.append((nm[0], nm[1], nm[2], nums[j][2]))
    return pairs


def terminal_lines(runs):
    """A transistor's pin drawing on one page -> one text per drawing, "G 1" a line (as figure_lines gives). The
    schematic symbol is drawn with each terminal's name (GATE / SOURCE / DRAIN, BASE / EMITTER / COLLECTOR, or the
    capital letter) and its pin number right beside it, above or below (_terminal_pairs). A drawing is three such
    pairs close together: one full set of terminals, the numbers 1, 2, 3 each once.
    Letters with no numbers (Diodes Inc.), a number between two names, a set drawn twice side by side: nothing."""
    pairs = _terminal_pairs(runs, TERMINAL, ("G", "S", "D", "B", "E", "C"))
    dist = lambda a, b: ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
    groups = []                                        # pairs within 110 pt of one another: one drawing
    for pr in sorted(pairs):
        mine = [g for g in groups if any(dist(pr, q) <= 110 for q in g)]
        for g in mine[1:]:
            mine[0].extend(g)
            groups.remove(g)
        (mine[0] if mine else groups.append([]) or groups[-1]).append(pr)
    out = []
    for g in groups:
        keys, vals = [q[2] for q in g], [q[3] for q in g]
        if len(g) == 3 and set(keys) in _TERMINAL_SETS and sorted(vals) == [1, 2, 3]:
            out.append("\n".join(f"{k} {v}" for k, v in zip(keys, vals)))
    return out


DIODE_TERMINAL = {"ANODE": "A", "CATHODE": "K"}
_CASE_STYLE = re.compile(r"\bCASE\s+(\d{2,4}[A-Z]{0,2})(?:[-−–]\d{2})?\s*,?\s*STYLE\s+(\d{1,2})\b", re.I)
_STYLE_BLOCK = r"\bSTYLE\s+{n}\s*:\s*PIN\s+1\s*\.\s*(.+?)\s+2\s*\.\s*(.+?)\s+3\s*\.\s*(.+?)(?=\s+STYLE\s+\d|\s+(?:4\s*\.|DIM\b|NOTES?\b)|$)"
_NC_WORDS = re.compile(r"(?i)^(?:N\.?\s*C\.?|NO\s+CONNECT(?:ION)?|NOT\s+CONNECTED)$")


def _diode_role(word):
    w = " ".join(str(word or "").split()).strip(" .,;").upper()
    return "A" if w == "ANODE" else "K" if w == "CATHODE" else "N" if _NC_WORDS.match(w) else None


def _diode_word(text):
    """A text run that CONTAINS anode or cathode is a diode terminal: 'A', 'K', or 'X' when it names both (a series
    pair's joined third lead, 'CATHODE/ANODE': review 2026-11-23, only the exact words counted and the pair was read
    as one diode with pin 3 not connected)."""
    t = str(text or "").upper()
    a, k = "ANODE" in t, "CATHODE" in t
    return "X" if a and k else "A" if a else "K" if k else None


def diode_orders(page_texts, page_runs=(), package=None, doubts=None):
    """The pin orders of a single diode in a three-lead package (SOT-23, SC-70) that the datasheet's own words give:
    'ANK' = pin 1 anode, pin 2 not connected, pin 3 cathode. Two sources, read as text, never guessed:
    (a) the symbol drawn with the words ANODE and CATHODE, each with its pin number right beside it (onsemi's '3
    CATHODE' / '1 ANODE'), the third lead drawn there with no label or a no-connect one (any other label, 'COMMON',
    'CATHODE/ANODE': a pair, nothing); (b) the outline's style table that the front page points to ('CASE 318 STYLE
    8' -> 'STYLE 8: PIN 1. ANODE 2. NO CONNECTION 3. CATHODE'). Every ANODE / CATHODE pair in the PDF must name the
    same pin (a sheet that draws a common-cathode pair too has two anodes: nothing), the front page names one style
    only, that style's table is found (a style named but not found is not settled, whatever is drawn), and it names
    one anode, one cathode and one pin not connected. -> sorted list of distinct orders: one
    order settles it; none or two do not. package: this part's land name(s): the one order then counts only when
    every page it was read on is this package's (package_doubt; review 2026-11-23, 1.4), else [] and the reason is
    added to the list `doubts`. Not given (None): the bare text check, which no release may rest on."""
    found, used = set(), []                            # used: the pages an order was read on
    drawn = []                                         # (page, pairs) of every page with an ANODE / CATHODE pair
    for page, runs in page_runs or ():
        pairs = _terminal_pairs(runs, _diode_word, pin_words=False)
        if pairs:
            drawn.append((page, runs, pairs))
    if drawn:
        if len(drawn) != 1 or len(drawn[0][2]) != 2 or {q[2] for q in drawn[0][2]} != {"A", "K"}:
            return []        # pairs on two pages, a second anode or cathode: a pair, a family, two parts drawn
        _, runs, pairs = drawn[0]
        nums = {q[3] for q in pairs}
        if len(nums) != 2:
            return []
        third = ({1, 2, 3} - nums).pop()
        cx, cy = sum(q[0] for q in pairs) / 2, sum(q[1] for q in pairs) / 2
        thirds = [r for r in runs if r[2] == str(third) and abs(r[0] - cx) <= 110 and abs(r[1] - cy) <= 110]
        mid = lambda r: (r[0] + 0.25 * r[3] * len(r[2]), r[1])           # as _terminal_pairs places a run
        taken = {(q[0], q[1]) for q in pairs}                             # the two words, each by its own number
        if any(len(r[2]) > 1 and _diode_word(r[2]) and mid(r) not in taken and abs(mid(r)[0] - cx) <= 110
               and abs(r[1] - cy) <= 110 for r in runs if r[3] and r[3] > 0):
            return []            # another anode / cathode word in the drawing, by no number of its own: not one diode
        for x, y, t, sz in thirds:                                        # the third lead's own label, when it has one,
            for r in runs:                                                # must say not connected: any other wording
                if r[3] and r[3] > 0 and not _INT.match(r[2]) and not _PIN_WORD.match(r[2]) and mid(r) not in taken \
                        and ((mid(r)[0] - mid((x, y, t, sz))[0]) ** 2 + (r[1] - y) ** 2) ** 0.5 <= 3 * (sz or r[3]) \
                        and not _NC_WORDS.match(" ".join(r[2].split()).strip(" .,;")):
                    return []                                             # ('COMMON', 'K1, A2') is a second diode's
        if max(abs(a[0] - b[0]) + abs(a[1] - b[1]) for a in pairs for b in pairs) <= 110 and thirds:
            order = ["N", "N", "N"]
            for q in pairs:
                order[q[3] - 1] = q[2]
            found.add("".join(order))
            used.append(drawn[0][0])
        # else: the two words far apart, or no third lead drawn beside them (a 2-pin variant's symbol, or the
        # package not drawn there): the words settle nothing; the style table may
    pages = sorted((page_texts or {}).items())
    texts = [" ".join(str(t or "").split()) for _, t in pages]
    styles = {(m.group(1), m.group(2)) for t in texts[:2] for m in _CASE_STYLE.finditer(t)}
    if len(styles) > 1:
        return []                                      # two styles named up front: two variants on one sheet
    if styles:
        case, n = next(iter(styles))                   # that case's own outline page: its style table
        blocks = {m.groups() for t in texts if re.search(r"\bCASE\s+" + re.escape(case) + r"\b", t, re.I)
                  for m in re.finditer(_STYLE_BLOCK.format(n=n), t, re.I)}
        if not blocks:
            return []                                  # the style the front page names is not in the text: not settled
        orders = set()
        for b in blocks:
            roles = [_diode_role(w) for w in b]
            if sorted(r or "?" for r in roles) == ["A", "K", "N"]:
                orders.add("".join(roles))
            else:
                return []                              # the style names something else (a pair, a transistor)
        found |= orders
        if orders:                                     # the front page that names the case and style ties it to the part
            used += [pg for (pg, _), t in list(zip(pages, texts))[:2] if _CASE_STYLE.search(t)]
    if len(found) == 1 and package is not None:
        why = next((w for w in (package_doubt(page_texts, pg, package, 3) for pg in used) if w), None)
        if why:
            if doubts is not None:
                doubts.append(f"it names pins 1, 2, 3 {', '.join(sorted(found)[0])}, but {why}")
            return []
    return sorted(found)


def diode_orders_pdf(pdf_bytes, package=None, doubts=None):
    """diode_orders on a PDF's text and placed text, [] when it cannot be read."""
    if not pdf_bytes:
        return []
    return diode_orders(page_texts(pdf_bytes), page_runs(pdf_bytes), package, doubts)


_PKG_LETTERS = "|".join(sorted({f for f in FAMILIES if f.isalpha()} | {"D2PAK", "SOP"}, key=len, reverse=True))
# A numbered package name as makers print it (review 2026-11-23, round 2, C2: 'TO 92', 'to-92', 'TO-' at a line's end
# and '92' on the next, 'TO-92MOD', 'TO-92Package' were not seen, so a sheet with one of them 'named one package
# only'): the family in either case when a hyphen or nothing joins it to its number, in capitals when only a space or
# a line break does (see _spaced), then any letters run on to it.
_PKG_NUM = re.compile(r"(?<![A-Za-z0-9])(?:(\d{1,3})-)?(?:(?i:(SOT|SOD|SC|TO|DO))(-\s*|)|(SOT|SOD|SC|TO|DO)(\s+))"
                      r"(\d{2,3})(?!\d)([A-Za-z]*)(?![A-Za-z0-9])(?:-(\d{1,3})(?![A-Za-z0-9]))?")
_PKG_WORD = re.compile(r"(?i)(?<![A-Za-z0-9])(?:(\d{1,3})-)?(" + _PKG_LETTERS + r")(?:-(\d{1,3}))?(?![A-Za-z0-9])")
# Makers' own names for a package, which carry no JEDEC family: Zetex's E-Line and ROHM's SPT are TO-92-like leaded
# bodies; ROHM's SST3 / SMT3 / UMT3 / EMT3 / VMT3 / MPT3 / CPT3 are its names for SOT-23, SC-59, SOT-323, SOT-416,
# SOT-723, SOT-89 and DPAK (_SAME_PACKAGE)
_PKG_MAKER = re.compile(r"(?<![A-Za-z0-9])(?:(?i:E-?Line)|SPT|SST3|SMT3|UMT3|EMT3|VMT3|MPT3|CPT3)(?![A-Za-z0-9])")
_TO_NUMBERS = {"18", "39", "46", "52", "66", "72", "92", "126", "202", "218", "220", "225", "226", "236", "243", "247",
               "251", "252", "253", "261", "262", "263", "264", "268", "269", "270", "274", "277"}
_SAME_PACKAGE = ({"SOIC", "SO", "SOP"}, {"DPAK", "TO252", "TO252AA", "CPT3"}, {"D2PAK", "TO263", "TO263AB"},
                 {"SOT23", "SST3"}, {"SC59", "SMT3"}, {"SOT323", "SC70", "UMT3"}, {"SOT416", "SC75", "EMT3"},
                 {"SOT723", "VMT3"}, {"SOT89", "MPT3"})


def _spaced(text, m):
    """Is 'TO 92' (the family, a space or a line break, the number) a package name and not words? The family in
    capitals, the number one JEDEC gives a TO outline, and no number before it on its line ('-55 TO 150', '-65 TO
    92': a range)."""
    if m.group(4) == "TO" and m.group(6) not in _TO_NUMBERS:
        return False
    return not re.search(r"\d[ \t]*(?:°[ \t]*C|[A-Za-z%]{0,2})[ \t]*\Z", text[:m.start()])     # on its own line


def package_words(text):
    """The packages a text names as words: {(name, lead count or None)}: 'SOT-23 (TO-236AB)' -> {('SOT23', None),
    ('TO236AB', None)}, 'SOT-23-5' -> {('SOT23', 5)}, '8-SOIC' -> {('SOIC', 8)}, 'to-92' / 'TO 92' -> {('TO92', None)},
    'TO-92MOD' -> {('TO92MOD', None)}, 'E-Line' -> {('ELINE', None)}. Up to three capitals after the number are part
    of the name (TO-236AB, TO-92MOD: another outline than TO-92); a word run on to it is not ('SOT-23Package' is
    SOT-23). A short family word with no lead count (SO, DIP, QFN: plain words and prefixes too) is not one."""
    text = re.sub("[−–—‐‑‒]", "-", str(text or ""))             # onsemi's minus sign
    out = set()
    for m in _PKG_NUM.finditer(text):
        if m.group(4) and not _spaced(text, m):
            continue
        tail = m.group(7) if m.group(7).isupper() and len(m.group(7)) <= 3 else ""
        count = m.group(1) or m.group(8)
        out.add(((m.group(2) or m.group(4)).upper() + m.group(6) + tail, int(count) if count else None))
    for m in _PKG_WORD.finditer(text):
        count = m.group(1) or m.group(3)
        if count or len(m.group(2)) >= 4:
            out.add((m.group(2).upper(), int(count) if count else None))
    for m in _PKG_MAKER.finditer(text):
        out.add((m.group().replace("-", "").upper(), None))
    return out


def own_packages(package):
    """The names this part's package goes by, from its land's name and the names the package tables file under that
    land ('SOT-23' -> SOT23, TO236, TO236AB; 'SOT-323_SC-70' -> SOT323, SC70; 'SOIC-8_3.9x4.9mm_P1.27mm' -> SOIC, SO,
    SOP with 8 leads) -> (names, lead count or None). package: the land name(s); a distributor's case group is not
    used here (DigiKey's 'TO-236-3, SC-59, SOT-23-3' lists SC-59, another body, for every SOT-23 part)."""
    from .stdparts import ALIASES
    names, count = set(), None
    for t in package or ():
        land = str(t or "").split(":")[-1]
        for base, n in package_words(land.replace("_", " ")):
            names.add(base)
            count = count or n
        names |= {b for k, v in ALIASES.items() if v and v == land for b, _ in package_words(k)}
    for same in _SAME_PACKAGE:
        if names & same:
            names |= same
    return names, count


def package_doubt(page_texts, page, package, leads=None, where="", above=None):
    """Why a pin order read on page `page` of a PDF may not be this part's package's, or None (review 2026-11-23,
    1.4: a family datasheet whose text prints only the TO-92 table gave the TO-92 order to the SOT-23 part). It is
    this package's when every package the PDF names is this part's own (one package only), or the table's header
    named this package's column (check's `where`), or the table's own heading names it: above, check()'s 'above' of
    an agreeing table (its title and the lines just over it), names this part's package and no other, and nothing
    else on the page names another (round 2, C2: the rule turned round; a package named somewhere on the page was
    enough). above None: an order read from a drawing or a style table, which has no heading in the text: the page
    it is on names this part's package and no other.
    leads: the part's lead count ('SOT-23-5' is not the three-lead SOT-23). A PDF that names no package: a doubt."""
    mine, count = own_packages(package)
    if not mine:
        return "this part's package is not known, so the order cannot be tied to it"
    is_mine = lambda w: w[0] in mine and (w[1] is None or w[1] in (count, leads) or not (count or leads))
    said = lambda ws: ", ".join(sorted({b + (f"-{n}" if n else "") for b, n in ws}))
    every = set().union(*[package_words(t) for t in (page_texts or {}).values()]) if page_texts else set()
    if every and all(is_mine(w) for w in every):
        return None                                    # the PDF names this package only
    if "the header names one package per column" in str(where or ""):
        return None                                    # the table's own header picked this package's column
    here = package_words((page_texts or {}).get(page))
    near = here if above is None else package_words(above)
    if near and all(is_mine(w) for w in here | near):
        return None                                    # a family PDF, but this table (this page) is headed for this package alone
    if not every:
        return "the PDF names no package, so the order cannot be tied to this part's"
    if here and (near or above is None):
        return f"page {page} is not headed for this part's package alone (it names {said(here | near)}): which " \
               "package's order it prints is not in the text"
    return f"the PDF names other packages too ({said(w for w in every if not is_mine(w))}) and " + \
           (f"the lines above page {page}'s table name" if above is not None and here else f"page {page} names") + \
           " none: which package's order it prints is not in the text"


def pin_orders(page_texts, names, figures=(), part=None, package=None, doubts=None):
    """The orders of a three-terminal part's names ('GSD', 'BEC') on pins 1, 2, 3 that the datasheet's text agrees
    with (check): ['GSD'] when it prints one order and no other. Two orders agreeing (two packages drawn), or none: the
    text does not settle it. package: this part's land name(s), as check() takes them: the one order then counts only
    when it is this package's (package_doubt; review 2026-11-23, 1.4), else [] and the reason is added to the list
    `doubts`. Not given (None): the bare text check, which no release may rest on."""
    from itertools import permutations
    got = [("".join(o), v) for o in permutations(names)
           for v in [check(page_texts, list(zip("123", o)), figures, part=part, package=package or ())]
           if v["status"] == "agreed"]
    if len(got) == 1 and package is not None:
        why = package_doubt(page_texts, got[0][1].get("page"), package, len(names), got[0][1].get("where"),
                            got[0][1].get("above"))
        if why:
            if doubts is not None:
                doubts.append(f"it prints {', '.join(got[0][0])} on pins 1, 2, 3, but {why}")
            return []
    return [o for o, _ in got]


def pin_orders_pdf(pdf_bytes, names, part=None, package=None, doubts=None):
    return pin_orders(page_texts(pdf_bytes), names, page_figures(pdf_bytes), part, package, doubts) if pdf_bytes else []


def check_pdf(pdf_bytes, pins, pin_count=None, part=None, package=(), blank=False):
    """check() on a PDF: its text layer's tables and its pinout drawings (page_figures). No PDF: unchecked.
    package: this part's land name and case texts (a table with one column per package then checks its column)."""
    if not pdf_bytes:
        return {"status": "unchecked", "why": "none of the part's datasheet links gave a PDF to check it against"}
    return check(page_texts(pdf_bytes), pins, figures=page_figures(pdf_bytes), pin_count=pin_count, part=part,
                 package=package, bars=lambda pages: bar_rows(pdf_bytes, pages), blank=blank)


def bar_rows(pdf_bytes, pages):
    """{page: lines} for the 1-based pages: each line the words on it as the reader sees them, left to right,
    [(x, word, barred)]. barred: the word is printed with a bar over it (active low), which the text layer does not
    carry: a thin line drawn over it, a font that draws the bar, or combining overlines (ecadlib.pdf_overline reads
    them; here by position, row by row). {} when the PDF cannot be read."""
    try:
        import ctypes
        import pypdfium2 as pdfium
        import pypdfium2.raw as R
        from . import pdf_overline as OV
        doc = pdfium.PdfDocument(bytes(pdf_bytes))
    except Exception:
        return {}
    out = {}
    try:
        for p in pages:
            if not isinstance(p, int) or not 1 <= p <= len(doc):
                continue
            try:
                page = doc[p - 1]
                tp = page.get_textpage()
                n = tp.count_chars()
                text = tp.get_text_range(0, n) if n else ""
                drawn = OV._bars(page)
                buf, flags = ctypes.create_string_buffer(256), ctypes.c_int()

                def font_bar(i):
                    R.FPDFText_GetFontInfo(tp.raw, i, buf, 256, ctypes.byref(flags))
                    return bool(OV.FONT_BAR.search(buf.value.decode("latin-1", "replace")))
                words = []
                for m in re.finditer(r"\S+", text):
                    core = re.search(r"[A-Za-z0-9].*[A-Za-z0-9]|[A-Za-z0-9]", m.group().replace("̅", " "))
                    if not core:
                        continue
                    a, b = m.start() + core.start(), m.start() + core.end()
                    box = [tp.get_charbox(i) for i in range(a, b) if text[i] != "̅"]
                    if not box or any(q[3] - q[1] <= 0 for q in box):
                        continue
                    barred = "̅" in m.group() or all(font_bar(i) for i in range(a, b)) \
                        or bool(drawn) and OV._under_bar(tp, drawn, a, b)
                    words.append((box[0][0], (box[0][1] + box[0][3]) / 2, max(q[3] - q[1] for q in box), m.group(), barred))
                lines = []
                for x, y, h, w, barred in sorted(words, key=lambda r: (-r[1], r[0])):
                    if lines and abs(lines[-1][0] - y) <= 0.5 * max(h, lines[-1][1]):
                        lines[-1][2].append((x, w, barred))
                    else:
                        lines.append([y, h, [(x, w, barred)]])
                out[p] = [sorted(ln[2]) for ln in lines]
            except Exception:
                continue
    finally:
        doc.close()
    return out


def page_figures(pdf_bytes, limit=80):
    """[(page, text)] of the pinout drawings in the PDF (figure_lines on each page's placed text), or [] when it
    cannot be read."""
    out = [(i, f) for i, runs in page_runs(pdf_bytes, limit) for f in figure_lines(runs) + terminal_lines(runs)]
    seen = set(out)
    for i, runs in glyph_runs(pdf_bytes, min(limit, GLYPH_PAGES)):     # the same drawings read glyph by glyph: what
        for f in figure_lines(runs) + terminal_lines(runs):              # pypdf joins ('12' for pins 1 and 2 an inch
            if (i, f) not in seen:                                       # apart, numbers in a form) comes apart here
                seen.add((i, f))
                out.append((i, f))
    return out


GLYPH_PAGES = 15          # a pinout drawing is on the first pages; reading glyph by glyph is slower than pypdf


def glyph_runs(pdf_bytes, limit=GLYPH_PAGES):
    """[(page, runs)] as page_runs gives them, built from each character's own box (pypdfium2): characters on one
    line and close together make one run, a space or a gap wider than about half a character ends it. pypdf hands
    over text as the PDF writes it, which can be two pin numbers far apart in one string ('12') or letters inside a
    form at the form's own origin; the glyph boxes are where the reader sees them. [] when it cannot be read."""
    try:
        import pypdfium2 as pdfium
        import pypdfium2.raw  # noqa: F401  (FPDFText_GetCharAngle)
        pdf = pdfium.PdfDocument(bytes(pdf_bytes))
    except Exception:
        return []
    out = []
    try:
        for i in range(min(len(pdf), limit)):
            try:
                tp = pdf[i].get_textpage()
                runs, cur = [], None
                for k in range(tp.count_chars()):
                    c = tp.get_text_range(k, 1)
                    x0, y0, x1, y1 = tp.get_charbox(k)
                    turned = abs(pdfium.raw.FPDFText_GetCharAngle(tp.raw, k)) > 0.02    # rotated text: not read
                    if not c.strip() or y1 - y0 <= 0 or turned:
                        if cur:
                            runs.append(cur)
                        cur = None
                        continue
                    h = y1 - y0
                    if cur and abs((y0 + y1) / 2 - cur[1]) < 0.3 * max(h, cur[3]) and -0.5 <= x0 - cur[4] <= 0.6 * max(h, 1):
                        cur[2] += c
                        cur[4] = x1
                    else:
                        if cur:
                            runs.append(cur)
                        cur = [x0, (y0 + y1) / 2, c, h, x1]
                if cur:
                    runs.append(cur)
                # a glyph box is the ink's height (a digit about 0.7 of the font size): size as pypdf gives it
                out.append((i + 1, [(r[0], r[1], r[2], r[3] * 1.4) for r in runs]))
            except Exception:
                continue
    finally:
        pdf.close()
    return out


def page_runs(pdf_bytes, limit=80):
    """[(page, runs)]: each page's text as the PDF places it, [(x, y, text, size)], upright text only; [] when the
    PDF cannot be read."""
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
        out.append((i, runs))
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
