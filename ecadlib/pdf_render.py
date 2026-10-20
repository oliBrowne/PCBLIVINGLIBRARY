"""PDF page -> PNG, for the page pictures sent to vision models and drawn by visual-check.

  render_png(pdf, page, dpi)  -> PNG bytes of one page (1-based); pdf is bytes or a path
  render_png(..., band=(0, .55)) -> only that horizontal band of the page (fractions of its height, from the top)
  page_size(pdf, page)        -> (width, height) in points
  renderer()                  -> "pypdfium2", "pdftoppm" or None (what render_png will use)

pypdfium2 (Apache-2.0 / BSD-3, a pip wheel on Windows, macOS and Linux; pdfplumber already depends on it) renders in
process, about 4x faster than starting one pdftoppm per page (12 STM32 pages at 150 dpi: 0.67 s vs 2.73 s), with the
same pictures (mean pixel difference 2-7 of 255, anti-aliasing). poppler's pdftoppm stays the fallback, so nothing
that worked before stops working when pypdfium2 is missing.
"""
import shutil
import struct
import subprocess
import tempfile
import zlib
from pathlib import Path


class RenderError(Exception):
    pass


def renderer():
    try:
        import pypdfium2  # noqa: F401
        return "pypdfium2"
    except ImportError:
        return "pdftoppm" if shutil.which("pdftoppm") else None


def _png(w, h, rows):
    """Minimal RGB PNG encoder (so pypdfium2 does not also need Pillow)."""
    raw = b"".join(b"\x00" + r for r in rows)

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def page_size(pdf, page):
    import io
    from pypdf import PdfReader
    r = PdfReader(io.BytesIO(pdf) if isinstance(pdf, (bytes, bytearray)) else str(pdf))
    box = r.pages[page - 1].cropbox
    w, h = float(box.width), float(box.height)
    return (h, w) if (r.pages[page - 1].get("/Rotate") or 0) % 180 else (w, h)


def _band(h, band):
    return (0, h) if not band else (max(0, int(h * band[0])), min(h, int(round(h * band[1]))))


def _pdfium(pdf, page, dpi, band=None):
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(pdf if isinstance(pdf, (bytes, bytearray)) else str(pdf))
    try:
        if not 1 <= page <= len(doc):
            raise RenderError(f"page {page} is not in the PDF ({len(doc)} pages)")
        bm = doc[page - 1].render(scale=dpi / 72, rev_byteorder=True)   # rev_byteorder: RGB(A), not BGR(A)
        w, h, stride = bm.width, bm.height, bm.stride
        n = {"RGB": 3, "RGBA": 4, "RGBX": 4}.get(bm.mode)
        if n is None:
            raise RenderError(f"unexpected pdfium bitmap mode {bm.mode}")
        buf = bytes(bm.buffer)
        rows = []
        y0, y1 = _band(h, band)
        for y in range(y0, y1):
            r = buf[y * stride:y * stride + w * n]
            if n == 4:                                   # drop alpha / padding byte
                r = bytes(b for i, b in enumerate(r) if i % 4 != 3)
            rows.append(r)
        return _png(w, y1 - y0, rows)
    finally:
        doc.close()


def _poppler(pdf, page, dpi, band=None):
    exe = shutil.which("pdftoppm")
    if not exe:
        raise RenderError("no PDF renderer: pip install pypdfium2 (or install poppler's pdftoppm)")
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "in.pdf"
        if isinstance(pdf, (bytes, bytearray)):
            src.write_bytes(pdf)
        else:
            src = Path(pdf)
        stem = Path(d) / "p"
        crop = []
        if band:
            w, h = (round(v * dpi / 72) for v in page_size(pdf, page))
            y0, y1 = _band(h, band)
            crop = ["-x", "0", "-y", str(y0), "-W", str(w), "-H", str(y1 - y0)]
        r = subprocess.run([exe, "-png", "-r", str(int(dpi)), "-f", str(page), "-l", str(page), *crop, "-singlefile",
                            str(src), str(stem)], capture_output=True, timeout=120)
        f = stem.with_suffix(".png")
        if r.returncode or not f.exists():
            raise RenderError(f"pdftoppm failed on page {page}: {r.stderr.decode(errors='replace')[:200]}")
        return f.read_bytes()


def render_png(pdf, page, dpi=110, band=None):
    try:
        import pypdfium2  # noqa: F401
    except ImportError:
        return _poppler(pdf, page, dpi, band)
    return _pdfium(pdf, page, dpi, band)
