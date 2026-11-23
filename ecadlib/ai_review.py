"""The AI hold reviewer: a second look at a fully built part that is held only for a person's look ([review]
ai_reviewer; off by default).

A part the librarian builds but cannot confirm on its own (a datasheet read with no independent second source, a KiCad
symbol matched by name with an ordering suffix, a land picked by name) is held with "verify: ..." reasons: a person
compares the symbol and footprint with the datasheet. With ai_reviewer = "assistant" the run writes that look as a task
for your own assistant (no API key, no cost), like the datasheet reads of ecadlib.ds_assistant:

    <plan folder>/review/assistant/READ_ME.md           what to do, and every review still waiting
    <plan folder>/review/assistant/<part>-<key>/
        request.md      the rules and the task for this part
        case.json       the built symbol's pins and the built footprint's pads (mm, KiCad top view)
        datasheet.pdf   the part's datasheet, as the run has it
        answer.json     <- written by your assistant: {"verdict": "release" | "hold", "reason", "numbers",
                           "evidence_pages", "checked": {"symbol": "match", "footprint": "match"}}

On the next run a "release" answer, with both checks "match", releases the part (release_check result
"ai_reviewed"); anything else leaves it held. Rules, so it can never make the library worse:
  - only a part whose every open hold is a "verify:" look: never a missing file or field, a failed copper or pin
    check, or anything else the release check fails (those are never covered);
  - only a part with both a built symbol and a built footprint, and a datasheet PDF on this machine;
  - the folder's key is the hash of the PDF, the symbol file, the footprint file and these rules: a part rebuilt with
    other pins or pads, or another datasheet, asks again and an old answer never covers it;
  - its pick is recorded on the part (plan.json "ai_review") and in the release check, by name. Only the librarian run
    that read the answer passes it to release_check.apply (ai=...): a later check of plan.json (part approve, resolve,
    the app) never counts an old "ai_review", and with ai_reviewer = "off" nothing is released.
Trial (2026-11-23, Haiku, the rules below): 0 of 51 planted errors released after the pad-size rule was added (one pad
0.2 mm off, two adjacent pins swapped, a pitch one step off, a mirrored land, the narrow body's land, half-width pads,
reversed polarity); 12 of 15 correct parts released. Nothing here talks to the network."""
import hashlib
import json
import re
import shutil
from pathlib import Path

MODES = ("off", "assistant")
README = "READ_ME.md"
VERSION = "1"                                         # part of every key: new rules ask every part again

RULES = """You are a hold reviewer for an electronics component library. The library generator built this part (a
schematic symbol and a PCB footprint) but held it back for a person's look. Decide whether it is SAFE TO RELEASE or
must STAY HELD.

Safety first: releasing a wrong part costs a scrapped circuit board; holding a right part only costs a person's look.
Errors can be subtle (one pad 0.2 mm off, two adjacent pins swapped, a pitch one standard step off, a mirrored land,
the land of a different body width, pads narrower than the leads). Check every pin and every pad, not a sample.

1. Open datasheet.pdf (read it in page ranges). Confirm it covers THIS part number and package (a family datasheet that
   lists it is fine). Find (a) the pin table / pin drawing for this package and (b) the package outline and, if
   printed, the recommended land pattern / PCB layout.
2. SYMBOL: for EVERY symbol pin number, the name must be the function the datasheet gives that pin number in this
   package. The same signal spelled differently is fine (VDD = VCC, GND = VSS, P2[0] = P2.0, NC = no connect). For
   2-pin parts with blank or numeric names, check the numbering sense only (polarity: which number is anode/cathode or
   +/-, where the datasheet says).
3. FOOTPRINT (case.json: mm, KiCad top view, origin at the part centre, y grows downward). Work out and WRITE DOWN,
   from case.json and from the PDF:
   - pad count; the pitch between neighbouring pads in each row (every step must be equal, not just the first); the
     row span (centre to centre between opposite rows) or the gap / centres of a two-pad part; pad length and width;
   - where pad 1 is, and the direction pins count (counter-clockwise seen from the top for ICs). A land mirrored
     left-right (pins counting the wrong way round) is WRONG. A whole-part rotation (90/180 degrees) with the same
     numbering is fine.
   - Compare with the datasheet's recommended land; if none is printed, with the package dimensions (pads must sit
     under the leads / terminals; a little longer outward is normal).
   PAD SIZE (check it for every pad group, as carefully as pitch): find the lead's width b (min/max) and foot length L
   in the package table. Each pad must be at least as wide as the lead's MAXIMUM width b and leave at least 0.15 mm of
   copper gap to its neighbour (pad width <= pitch - 0.15); its length must cover the lead's foot L (max) with some toe
   outside. A pad narrower than the lead's maximum width, or shorter than the foot, is WRONG even if pitch and span are
   right. Two-pad parts: each pad at least as wide as the terminal. Write these numbers down.
   Tolerances: positions and pitch +/-0.05 mm per step (pitch errors add up along a row), span +/-0.15 mm, pad sizes
   +/-0.2 mm or a land a bit larger than the minimum. Anything beyond that is a mismatch.
4. Verdict "release" ONLY if you found both the pinout and the package / land data in the PDF and every check above
   matches. If the PDF does not show something you need, a page is unreadable, the PDF is for another part or package,
   or you are unsure: "hold". Never guess.
"""

ANSWER = """Write answer.json in this folder:
{"verdict": "release" | "hold", "reason": "one or two sentences", "numbers": "the pitch / span / pad sizes you
compared, case vs PDF", "evidence_pages": [3, 12], "checked": {"symbol": "match|mismatch|not found",
"footprint": "match|mismatch|not found"}}
"""


def mode(rules):
    m = str(((rules or {}).get("review") or {}).get("ai_reviewer") or "off").strip().lower()
    return m if m in MODES else "off"


def root(out):
    return Path(out) / "review" / "assistant"


def _stem(s):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(s or "part")).strip("._") or "part"


# the looks a pinout + land comparison with the datasheet settles; any other "verify:" hold (copper lost in the
# .PcbLib, BOM lines that disagree, a value read two ways, a PDF a person must confirm, another maker's part) is not
# this reviewer's to pass (review 2026-11-23)
LOOKS = re.compile(r"^verify: (?:datasheet reader output \(|symbol \S.* matched by |footprint \S+ chosen by |"
                   r"symbol pinout \S.* family table)")


def looks_only(item):
    """The part's open holds when every one is a look this reviewer can settle (LOOKS), else None. Read from the
    release check the run just made, so a hold any other rule raised (files, pins, footprint, fields, parity) keeps
    the part out."""
    rc = item.get("release_check") or {}
    msgs = list(rc.get("failures") or [])
    if item.get("status") != "held" or not msgs or rc.get("result") != "held":
        return None
    if any(not LOOKS.match(str(m)) for m in msgs):
        return None
    return msgs


def _files(item, out):
    from . import release_check as RC
    sym, fp = item.get("symbol") or {}, item.get("footprint") or {}
    s = RC._path(out, sym["file"]) if sym.get("file") else None
    k = RC._path(out, fp["kicad_mod"]) if fp.get("kicad_mod") else None
    b = RC._path(out, fp["file"]) if fp.get("file") else None
    return (s if s and s.exists() else None), (k if k and k.exists() else None), (b if b and b.exists() else None)


def _links(item):
    urls = [str(p.get("value") or "") for p in item.get("parameters") or []
            if p.get("name") in ("Datasheet", "ComponentLink1URL", "ComponentLink2URL")]
    for k in ("symbol", "footprint"):
        src = str((item.get("source") or {}).get(k) or "")
        if src.startswith("datasheet:"):
            urls.append(src[len("datasheet:"):])
    return [u for u in dict.fromkeys(urls) if u.startswith(("http://", "https://"))]


def _mpn(item):
    return next((str(p.get("value") or "") for p in item.get("parameters") or []
                 if p.get("name") == "Manufacturer Part Number"), "") or str(item.get("name") or "")


def pdf_for(item, out, cache=None, folder=None):
    """The part's datasheet PDF on this machine -> Path or None: the assistant's pdfs folder or your [datasheet]
    folder (<MPN>.pdf, an exact name only), else the run's PDF cache for one of the part's datasheet links."""
    from . import datasheet as DS, ds_assistant as DA
    mpn = _mpn(item)
    flat = lambda t: re.sub(r"[^a-z0-9]", "", str(t).lower())
    for f in (DA.pdf_folder(DA.root(out)), folder):
        if f:
            p, _ = DS.local_datasheet(f, mpn, ("exact",))
            if p is not None and flat(p.stem) == flat(mpn):  # <MPN>.pdf itself, never TPS7A20-Q1.pdf for TPS7A20
                return p
    if cache:
        for u in _links(item):
            p = Path(cache) / "datasheets" / (hashlib.sha256(u.encode()).hexdigest()[:24] + ".pdf")
            if p.exists():
                return p
    return None


def _pads(kicad_mod):
    from . import sexpr as SX
    tree = SX.parse(Path(kicad_mod).read_text(encoding="utf-8"))
    return [f"{p['number']} {p['type']} at({round(p['x'], 4)},{round(p['y'], 4)}) size {round(p['w'], 4)}x"
            f"{round(p['h'], 4)}" for p in SX.pads(tree)]


def _pins(schlib, name):
    from . import altium_lib as AL, symbol_layout as SL
    _, syms = AL.read_schlib(schlib)
    idx = [i for i, s in enumerate(syms) if str(s.get("name") or "").casefold() == str(name or "").casefold()]
    if len(idx) != 1:                                 # never another symbol's pins: no task, a person looks
        raise LookupError(f"symbol {name!r} is not one symbol in {Path(schlib).name}")
    idx = idx[0]
    return [f"{p['num']}:{p['name']}" for p in SL.pins_from_schlib(schlib, idx)[1]]


def case(item, out, pdf):
    """(case dict, key) for one part, or (None, why)."""
    s, k, b = _files(item, out)
    if not (s and k and b):
        return None, "no built symbol and footprint to compare"
    try:
        pins = _pins(s, (item.get("symbol") or {}).get("ref"))
        pads = _pads(k)
    except Exception as e:                            # a file this cannot read, a symbol not found: a person looks
        return None, f"the built files could not be read ({type(e).__name__})"
    c = {"part": item["name"], "mpn": _mpn(item), "description": item.get("description") or "",
         "symbol": (item.get("symbol") or {}).get("ref"), "symbol_pins": pins, "footprint_pads_mm": pads,
         "holds": looks_only(item) or []}
    h = hashlib.sha256()                              # the files released (the .PcbLib too) and everything shown
    for blob in (Path(pdf).read_bytes(), s.read_bytes(), k.read_bytes(), b.read_bytes(), (VERSION + RULES).encode(),
                 json.dumps(c, sort_keys=True).encode()):
        h.update(hashlib.sha256(blob).digest())
    return c, h.hexdigest()[:16]


def folder(out, item, key):
    return root(out) / f"{_stem(item['name'])}-{key[:10]}"


def ask(out, item, c, key, pdf):
    """Write the review request folder (once; an answer already there is kept)."""
    d = folder(out, item, key)
    d.mkdir(parents=True, exist_ok=True)
    if not (d / "datasheet.pdf").exists():
        shutil.copyfile(pdf, d / "datasheet.pdf")
    (d / "case.json").write_text(json.dumps(c, indent=1), encoding="utf-8")
    (d / "request.md").write_text(f"# Review {item['name']}\n\n{RULES}\n{ANSWER}\nThe part: case.json. Its datasheet:"
                                  f" datasheet.pdf.\n", encoding="utf-8")
    return d


def answer(d):
    """A release answer in folder d -> (True, reason) | (False, why)."""
    f = Path(d) / "answer.json"
    if not f.exists():
        return False, "waiting for your assistant's review"
    try:
        a = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return False, f"answer.json does not read ({type(e).__name__}): held"
    if isinstance(a, list):
        a = a[0] if len(a) == 1 and isinstance(a[0], dict) else {}
    if not isinstance(a, dict):
        return False, "answer.json is not one answer object: held"
    ck = a.get("checked") if isinstance(a.get("checked"), dict) else {}
    if a.get("verdict") == "release" and ck.get("symbol") == "match" and ck.get("footprint") == "match":
        return True, str(a.get("reason") or "")[:300]
    return False, "held by the reviewer: " + str(a.get("reason") or a.get("verdict") or "no verdict")[:300]


def review(items, out, rules, cache=None, log=None):
    """Mark each part the reviewer released (item["ai_review"]) and write a request for every part still waiting.
    -> {name: the holds it passed}, for release_check.apply(ai=...). A part with no answer, a hold answer or anything
    outside the rules stays held."""
    if mode(rules) == "off":
        return []
    log = log or (lambda *a, **k: None)
    ds_folder = ((rules or {}).get("datasheet") or {}).get("folder") or None
    released, waiting = {}, []
    for it in items:
        it.pop("ai_review", None)                     # set fresh every run, never carried over
        holds = looks_only(it)
        if not holds:
            continue
        try:                                          # one part that cannot be asked stays held; the run goes on
            pdf = pdf_for(it, out, cache, ds_folder)
            if pdf is None:
                continue
            c, key = case(it, out, pdf)
            if c is None:
                continue
            d = ask(out, it, c, key, pdf)
            ok, why = answer(d)
        except (OSError, ValueError) as e:
            log(f"  review {it['name']}: not asked ({type(e).__name__}: {str(e)[:120]}); a person looks")
            continue
        rel = str(d.relative_to(Path(out))).replace("\\", "/")
        if ok:
            it["ai_review"] = {"by": "ai reviewer (your assistant)", "folder": rel, "key": key, "failures": holds,
                               "reason": why}
            released[it["name"]] = list(holds)
        else:
            waiting.append((it["name"], rel, why))
    _index(out, waiting)
    if released or waiting:
        log(f"  review {len(released)} part(s) released by the AI reviewer; {len(waiting)} waiting or held "
            f"({root(out) / README})")
    return released


def _index(out, waiting):
    r = root(out)
    if not waiting and not r.exists():
        return
    r.mkdir(parents=True, exist_ok=True)
    lines = ["# Parts waiting for your assistant's review", "",
             "Each folder holds request.md (the rules), case.json (the built part) and datasheet.pdf. Write",
             "answer.json in the folder, then run the same librarian command again.", ""]
    lines += [f"- {n}: {f} ({w})" for n, f, w in waiting] or ["(none)"]
    (r / README).write_text("\n".join(lines) + "\n", encoding="utf-8")
