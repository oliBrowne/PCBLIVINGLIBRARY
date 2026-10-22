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

# (document number pattern, the maker whose links are tried)
CODES = [(re.compile(r"(?<![A-Z0-9])(98A[A-Z]{2,3}\d{5}[A-Z])(\d)?(?![A-Z0-9])"), "nxp"),     # 98ASS23174W(1)
         (re.compile(r"(?<![A-Z0-9])(C04-\d{3,5}[A-Z]?)(?![A-Z0-9])"), "microchip")]          # C04-076
LINKS = {"nxp": ["https://www.nxp.com/docs/en/package-information/{code}.pdf"],
         "microchip": []}                                # versioned Packaging Specification: the folder or web search
MAX_DOCS = 2


def _has_drawing(texts, rows):
    """Does the PDF itself hold a dimension table for this part's package (or one that names no other package)?"""
    for t, r in zip(texts, rows):
        t = DR.norm_text(t)
        if DR.TABLE["drawing"].search(t) and DR.DIM_SYMS.search(t) and not r["other"] and not r["toc"]:
            return True
    return False


def refs(texts, part=None, package=None):
    """[(document number, maker, 1-based page)] the PDF prints for this part's package, when it has no drawing of
    it. A number counts only on a line (or the line before it) that names this package: a list of every package's
    document numbers gives this one's only."""
    if not texts or not any(rx.search(DR.norm_text(t).upper()) for t in texts for rx, _ in CODES):
        return []
    rows, own, _, _ = DR.page_scores(texts, part, package)
    if not own or _has_drawing(texts, rows):
        return []
    out, seen = [], set()
    for i, t in enumerate(texts):
        lines = DR.norm_text(t).upper().splitlines()
        for k, ln in enumerate(lines):
            for rx, maker in CODES:
                for m in rx.finditer(ln):
                    before = ln[:m.start()]
                    toks = DR.package_tokens(before) or DR.package_tokens(ln) or \
                        (DR.package_tokens(lines[k - 1]) if k else set())
                    if toks and DR._tok_match(own, toks) and m.group(1) not in seen:
                        seen.add(m.group(1))
                        out.append((m.group(1), maker, i + 1))
    return out[:MAX_DOCS]


def links(code, maker):
    return [u.format(code=code) for u in LINKS.get(maker, [])]


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
