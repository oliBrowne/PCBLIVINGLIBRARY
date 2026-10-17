"""Check the unofficial part sources against the live sites: is each endpoint still there, still the same shape, and
does ecadlib's EasyEDA converter still turn a real answer into the right footprint and symbol?

    ecadlib sources-check [--save DIR] [--json]

EasyEDA's component endpoint and JLCPCB's part search are undocumented (easyeda2kicad uses the same ones), so they can
change without notice. This asks each one about a few well-known parts and compares the answer with what the parts
really are (pad and pin counts from their datasheets):
  easyeda        C7593 (NE555DR, SOIC-8: 8 pads, 8 pins), C5423 (LM358DR, SOIC-8: 8 pads, 8 pins),
                 C2040 (RP2040, QFN-56 + exposed pad: 57 pads, 57 pins)
  jlcpcb_search  NE555DR -> C7593
Each check reports ok / changed (the answer arrived but is not what it should be: the format moved) / unreachable
(blocked, rate-limited or down). --save DIR keeps every raw answer (DIR/<source>-<part>.json) so a format change can
be studied and the converter's tests updated from real data. Nothing is cached: the point is today's answer.

Exit codes: 0 every check ok, 1 a source changed shape or the converter no longer gives the right counts, 3 every
source was unreachable (nothing could be checked). .github/workflows/sources-canary.yml runs this every Monday."""
import argparse, json, re, sys
from pathlib import Path

from . import easyeda_convert as EC, providers as P, sexpr
from .httpcache import Response

EASYEDA = [("C7593", "NE555DR", 8, 8), ("C5423", "LM358DR", 8, 8), ("C2040", "RP2040", 57, 57)]
JLCPCB = [("NE555DR", "C7593")]


class LiveHTTP:
    """No cache, no retries: one plain request per check (the providers' own headers are used)."""
    def __init__(self, timeout=30):
        import requests
        self.s, self.timeout = requests.Session(), timeout

    def _do(self, method, url, body=None, headers=None):
        try:
            r = self.s.request(method, url, data=body, headers=headers or {}, timeout=self.timeout)
            return Response(r.status_code, r.text)
        except Exception as e:                   # network errors are a result ("unreachable"), not a crash
            return Response(0, "", error=f"{type(e).__name__}: {e}")

    def get(self, url, headers=None, **kw):
        return self._do("GET", url, headers=headers)

    def post(self, url, body, headers=None, **kw):
        return self._do("POST", url, body, headers)


def _save(save, name, r):
    if save:
        Path(save).mkdir(parents=True, exist_ok=True)
        (Path(save) / f"{name}.json").write_text(r.text, encoding="utf-8")


def count_pads(kicad_mod):
    """Copper pads with a number (a paste-only window or an unnumbered mounting hole is not a pad of the part)."""
    t = sexpr.parse(kicad_mod)
    return sum(1 for p in sexpr.find(t, "pad") if str(p[1]).strip() and p[2] != "np_thru_hole")


def count_pins(kicad_sym):
    return len(sexpr.find(sexpr.parse(kicad_sym), "pin"))


def check_easyeda_answer(data, mpn, pads, pins):
    """-> (status, detail) for one parsed EasyEDA answer: the fields ecadlib reads are there, and the converter gives
    the part's real pad and pin counts."""
    res = P.parse_easyeda(data, P.SourceResult("easyeda"))
    if not res.found:
        return "changed", "no component in the answer (result missing or success false)"
    if P.clean_text(res.mpn).upper() != mpn.upper():
        return "changed", f"answer is for {res.mpn!r}, not {mpn}"
    r = data["result"]
    pkg = r.get("packageDetail") or {}
    notes = []
    try:
        got_pads = count_pads(EC.footprint_to_kicad(pkg.get("dataStr") or {}, pkg.get("title") or mpn, notes))
    except ValueError:                           # the converter found nothing it knows: 0 pads
        got_pads = 0
    try:
        got_pins = count_pins(EC.symbol_to_kicad(r.get("dataStr") or {}, mpn, "", {}, []))
    except ValueError:
        got_pins = 0
    bad = [f"{what} {got} (should be {want})" for what, got, want in (("pads", got_pads, pads), ("pins", got_pins, pins))
           if got != want]
    if bad:
        return "changed", "converter gives " + ", ".join(bad)
    return "ok", f"{pkg.get('title', '')}: {got_pads} pads, {got_pins} pins" + (f" ({len(notes)} converter notes)" if notes else "")


def check_easyeda(http, save=None):
    out = []
    for lcsc, mpn, pads, pins in EASYEDA:
        r = http.get(P.EasyEDA.URL.format(lcsc), headers=P.EasyEDA.HEADERS)
        _save(save, f"easyeda-{lcsc}", r)
        if not r.ok:
            out.append(("easyeda", lcsc, "unreachable", r.error or f"HTTP {r.status}"))
            continue
        try:
            status, detail = check_easyeda_answer(r.json(), mpn, pads, pins)
        except (ValueError, KeyError, TypeError, AttributeError, IndexError) as e:
            status, detail = "changed", f"answer no longer parses: {type(e).__name__}: {e}"
        out.append(("easyeda", lcsc, status, detail))
    return out


def check_jlcpcb(http, save=None):
    out = []
    for mpn, lcsc in JLCPCB:
        prov = P.JLCPCBSearch(http)
        r = prov.post(mpn)
        _save(save, f"jlcpcb_search-{mpn}", r)
        if not r.ok:
            out.append(("jlcpcb_search", mpn, "unreachable", r.error or f"HTTP {r.status}"))
            continue
        res = P.parse_jlcpcb(r, mpn, P.SourceResult("jlcpcb_search", mpn=mpn))
        if res.error:
            out.append(("jlcpcb_search", mpn, "changed", res.error))
        elif res.found and res.lcsc == lcsc:
            out.append(("jlcpcb_search", mpn, "ok", f"-> {lcsc}"))
        elif res.found:
            out.append(("jlcpcb_search", mpn, "changed", f"-> {res.lcsc}, expected {lcsc}"))
        else:
            out.append(("jlcpcb_search", mpn, "changed", "no exact match in the answer (field names moved?)"))
    return out


def run(http=None, save=None):
    http = http or LiveHTTP()
    return check_easyeda(http, save) + check_jlcpcb(http, save)


def exit_code(rows):
    if any(r[2] == "changed" for r in rows):
        return 1
    return 3 if rows and all(r[2] == "unreachable" for r in rows) else 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ecadlib sources-check", description=__doc__.split("\n\n")[0])
    ap.add_argument("--save", help="folder for the raw answers (one .json per check)")
    ap.add_argument("--json", action="store_true", help="print the results as JSON")
    a = ap.parse_args(argv)
    rows = run(save=a.save)
    if a.json:
        print(json.dumps([dict(zip(("source", "part", "status", "detail"), r)) for r in rows], indent=2))
    else:
        for src, part, status, detail in rows:
            print(f"{status:12} {src:14} {part:10} {detail}")
    code = exit_code(rows)
    if code == 1:
        print("An unofficial source changed: see the lines marked 'changed'. Until it is fixed, set "
              "[providers] unofficial = false or rely on the official APIs and the datasheet reader.", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
