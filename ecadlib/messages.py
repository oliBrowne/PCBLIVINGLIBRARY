"""Every sentence a person reads about a part's state, in one place (the shared voice: docs/ui-voice.md).

The tiny API front ends call (web UI, terminal, reports, Altium menu, KiCad plugin, MCP):

    held(code, **values) -> (text, fix)      # a reason code -> one plain sentence and the next step
    explain(reason)      -> {code, text, fix, note, raw}
                                              # any hold reason / review line already in a plan -> the same
    error(code, **values) -> (text, fix)     # an error: what went wrong, how to fix it
    friendly_error(exc)  -> (text, fix)       # an exception -> the same, without a traceback or a server path
    status_word(status)  -> "ready" | "held" | "waiting for sign-off" | ...
    counts(statuses)     -> "6 ready, 2 held"
    clean(text)          -> text without absolute paths or internal module names

Plans keep their machine strings (`hold_reasons`: "pins: symbol pins without a pad: 5-8"), so --json output and
plan.json never change; front ends show explain(reason)["text"] / ["fix"] and keep the code alongside.

Templates: {name} is a value; [ ... {name} ... ] is an optional part, left out when any value in it is missing.
A code with no table entry raises KeyError in held(); tests/test_messages.py fails when a release-check rule, a
footprint-check rule or a parity field has no entry here."""
import re

# ---------------------------------------------------------------- the words
STATUS = {"ready": "ready", "held": "held", "awaiting_approval": "waiting for sign-off", "duplicate": "duplicate",
          "existing": "already in Altium 365", "deleted_in_workspace": "deleted in Altium 365", "pass": "ready",
          "allowed": "released anyway", "rejected": "rejected", "failed": "failed", "pending": "waiting",
          "queued": "waiting", "building": "working", "review": "in the review queue", "approved": "approved",
          "pushed": "released", "match": "KiCad and Altium match", "mismatch": "KiCad and Altium differ"}
ORDER = ("ready", "released anyway", "held", "waiting for sign-off", "duplicate", "already in Altium 365")


def status_word(status):
    """A plan / queue status -> the word people see (unknown statuses: underscores become spaces)."""
    s = str(status or "").strip()
    return STATUS.get(s, s.replace("_", " "))


def counts(statuses, order=ORDER):
    """Iterable of statuses (or {status: n}) -> "6 ready, 2 held, 1 waiting for sign-off" (zero counts left out;
    "nothing yet" when empty)."""
    n = {}
    items = statuses.items() if isinstance(statuses, dict) else ((s, 1) for s in statuses)
    for s, k in items:
        w = status_word(s)
        n[w] = n.get(w, 0) + int(k)
    keys = [w for w in order if n.get(w)] + sorted(w for w in n if w not in order and n[w])
    return ", ".join(f"{n[w]} {w}" for w in keys) or "nothing yet"


SOURCES = {"kicad": "KiCad library", "local": "your library", "easyeda": "EasyEDA", "standard": "standard parts",
           "datasheet": "datasheet reader", "resolve": "picked by a person", "spec": "package spec",
           "a365": "Altium 365", "generated": "footprint generator"}


def source_word(src):
    """"kicad:Package_TO_SOT_SMD:SOT-23" -> "KiCad library (SOT-23)"; "datasheet:https://..." -> "datasheet reader";
    empty -> "none"."""
    s = str(src or "").strip()
    if not s:
        return "none"
    kind, _, rest = s.partition(":")
    word = SOURCES.get(kind)
    if not word:
        return clean(s)
    name = "" if kind in ("datasheet", "a365") or "://" in rest else rest.split(":")[-1]
    return f"{word} ({name})" if name else word


def plural(n, one, many=None):
    return f"{n} {one if n == 1 else (many or one + 's')}"


# ---------------------------------------------------------------- held reasons
# code -> (text, fix, patterns). patterns: regexes matched (re.search) against a raw reason (release-check
# "rule: message", librarian review lines, cross-check summaries); their named groups are the values.
CODES = {
    # files (release check rule `files`; cannot be released anyway)
    "files.no_symbol": ("There is no symbol for this part yet.",
                        "Pick a symbol from the candidates, or let the datasheet reader draw one.",
                        [r"^files: no symbol model", r"^no symbol found"]),
    "files.no_footprint": ("There is no footprint for this part yet.",
                           "Pick a footprint from the candidates, or let the datasheet reader build one.",
                           [r"^files: no footprint model", r"^no footprint found"]),
    "files.missing": ("The {kind} file is missing.",
                      "Fill the library again so the file is written.",
                      [r"^files: (?P<kind>symbol|footprint) file missing"]),
    "files.unreadable": ("The {kind} file is damaged and does not open.",
                         "Fill the library again; if it happens again, report it with the file attached.",
                         [r"^files: (?P<kind>symbol|footprint) file does not read back"]),
    # pins (rule `pins`)
    "pins.no_pins": ("The symbol has no pins.", "Pick another symbol.", [r"^pins: symbol has no pins"]),
    "pins.no_pads": ("The footprint has no pads.", "Pick another footprint.", [r"^pins: footprint has no pads"]),
    "pins.pin_without_pad": ("Symbol {pins_have} no pad on the footprint.",
                             "Pick a footprint that matches the symbol's pin numbers, or release anyway if those pins are not connected.",
                             [r"^pins: symbol pins without a pad: (?P<pins>.+)"]),
    "pins.pad_without_pin": ("Footprint {pads_have} no pin on the symbol.",
                             "Pick a symbol that has those pins, or a footprint that matches; mounting pads are fine.",
                             [r"^pins: pads without a symbol pin: (?P<pads>.+)"]),
    # the datasheet's pad table failed footprint_adv.check_pad_table, so no footprint was built (librarian, release check)
    "padtable.no_pin1": ("The footprint read from the datasheet has no pad numbered 1 or A1[ (its pads are {pads})], so "
                         "nothing says which pad is pin 1.",
                         "Find pin 1 in the datasheet, rename that pad to 1 and the others in order (ecadlib fp-edit PLAN "
                         "--part PART rename OLD=1 ...), then fill the library again.",
                         [r"datasheet pad table: no pad numbered 1(?: \(or A1\))?(?: - pads named (?P<pads>.+))?"]),
    "padtable.overlap": ("In the footprint read from the datasheet, pads {a} and {b} overlap, and their pins have "
                         "different names, so they would join two nets.",
                         "If the datasheet shows them as one net, join them (ecadlib fp-edit PLAN --part PART same-net "
                         "{a},{b}); if not, a pad was misread: compare the land pattern with the datasheet, or pick a "
                         "footprint by hand (ecadlib resolve).",
                         [r"datasheet pad table: pads (?P<a>\S+) \([^)]*\) and (?P<b>\S+) \([^)]*\) overlap"]),
    "padtable.duplicate": ("The datasheet read lists pad {pad} twice at the same place and size, which usually means a "
                           "row of the land-pattern table was read twice.",
                           "Compare the land pattern with the datasheet, then read it again or pick a footprint by hand.",
                           [r"datasheet pad table: pad (?P<pad>\S+) is listed twice at the same place"]),
    "padtable.symbol_waits": ("The symbol was read from the datasheet too; it is written once the footprint passes.",
                              "Fix the footprint (the other reason here); the symbol comes with it.",
                              [r"datasheet pad table: the symbol waits for the footprint"]),
    "padtable.other": ("The footprint read from the datasheet does not add up: {detail}.",
                       "Compare the land pattern with the datasheet; fix it with ecadlib fp-edit, or pick a footprint "
                       "by hand (ecadlib resolve).",
                       [r"datasheet pad table: (?P<detail>.+)"]),
    # footprint (rule `footprint`: footprint_check findings, or the PcbLib minimum)
    "footprint.overlap": ("Pads {a} and {b} overlap[ by {mm} mm].",
                          "Pick another footprint or fix the pad sizes; if the pads are one net on purpose, release anyway.",
                          [r"pads (?P<a>\S+) and (?P<b>\S+) overlap(?: by (?P<mm>[\d.]+) mm)?"]),
    "footprint.pad_gap": ("Some pads are closer together than the fab can reliably make ({detail}).",
                          "Compare the land pattern with the datasheet; release anyway if your fab can make it.",
                          [r"^footprint: copper: (?P<detail>pads? .*? (?:apart|from pad \S+))"]),
    "footprint.hole_clearance": ("Pad {pad} is only {mm} mm from a mounting hole.",
                                 "Move the pad away from the hole, or pick another footprint.",
                                 [r"pad (?P<pad>\S+) is (?P<mm>[\d.]+) mm from the hole"]),
    "footprint.no_pin1": ("No pad is numbered 1, so pin 1 cannot be found.",
                          "Number the pads as the datasheet does, or pick another footprint.",
                          [r"no pad numbered 1", r"^footprint: no pin 1 pad"]),
    "footprint.clockwise": ("The pads are numbered clockwise; KiCad and IPC number them counter-clockwise.",
                            "Number the pads counter-clockwise from pin 1 (seen from the top), or pick another footprint.",
                            [r"numbered clockwise"]),
    "footprint.ball_rows": ("The ball rows and their letters do not line up[ ({detail})].",
                            "Compare the ball map with the datasheet.",
                            [r"^footprint: bga: (?P<detail>.+)"]),
    "footprint.silk_on_pad": ("Silkscreen covers pad {pad}.",
                              "Move the silkscreen off the pad, or pick another footprint.",
                              [r"silkscreen shape covers pad (?P<pad>\S+)"]),
    "footprint.silk_close": ("The silkscreen is only {mm} mm from pad {pad}.",
                             "Move the silkscreen away from the pads (0.2 mm is usual), or release anyway.",
                             [r"silkscreen is (?P<mm>[\d.]+) mm from pad (?P<pad>\S+)"]),
    "footprint.no_courtyard": ("The footprint has no courtyard.",
                               "Add a courtyard around the pads and body, or pick another footprint.",
                               [r"no courtyard"]),
    "footprint.courtyard_small": ("The courtyard does not cover every pad and the body.",
                                  "Make the courtyard bigger, or pick another footprint.",
                                  [r"courtyard does not enclose"]),
    "footprint.pad_outside": ("Pad {pad} sticks out of the courtyard.",
                              "Make the courtyard bigger, or pick another footprint.",
                              [r"pad (?P<pad>\S+) sticks out of the courtyard"]),
    "footprint.courtyard_open": ("The courtyard outline is not closed.",
                                 "Close the courtyard outline, or pick another footprint.",
                                 [r"courtyard is not a closed outline"]),
    "footprint.other": ("The footprint check found a problem: {detail}.",
                        "Compare the footprint with the datasheet, then fix it or release anyway.",
                        [r"^footprint: (?P<detail>.+)"]),
    # height (rule `height`)
    "height.missing": ("There is no 3D height (no 3D model and no height value).",
                       "Attach a 3D model, or set the height from the datasheet.",
                       [r"^height: no 3D height"]),
    "height.out_of_range": ("The 3D height is {h} mm, outside the usual {lo}-{hi} mm for this package.",
                            "Check the height in the datasheet; fix the 3D model, or release anyway if it is right.",
                            [r"^height: 3D height (?P<h>[\d.]+) mm outside (?P<lo>[\d.]+)-(?P<hi>[\d.]+) mm"]),
    # fields (rule `fields`)
    "fields.no_datasheet": ("There is no datasheet link.", "Add the datasheet link to the BOM line or the part.",
                            [r"^fields: no datasheet link"]),
    "fields.no_description": ("The description is empty.",
                              "Add a short description, e.g. \"MOSFET N-CH 30 V 5 A SOT-23\".",
                              [r"^fields: Description is empty"]),
    "fields.empty": ("{field} is empty.", "Fill in {field} on the BOM line or the part.",
                     [r"^fields: (?P<field>.+?) is empty"]),
    # parity (rule `parity`: the KiCad and Altium files of the part read back differently)
    "parity.pad_count": ("The KiCad footprint has {kicad} pads and the Altium one {altium}.",
                         "Fill the library again so both are written from the same footprint; report it if they still differ.",
                         [r"^(?:parity: )?(?:export: )?footprint pad count: KiCad (?P<kicad>\S+) / Altium (?P<altium>\S+)"]),
    "parity.pad_missing": ("Pad {pad} is in the {side} footprint only.",
                           "Fill the library again so both are written from the same footprint; report it if they still differ.",
                           [r"^(?:parity: )?(?:export: )?pad (?P<pad>\S+) missing: KiCad (?P<k>\S+) / Altium (?P<al>\S+)"]),
    "parity.pin_pad": ("Pin {pin} has a pad in one of KiCad and Altium but not in the other.",
                       "Fill the library again; report it if the pin still maps differently.",
                       [r"^(?:parity: )?(?:export: )?pin (?P<pin>\S+) pad: "]),
    "parity.symbol": ("The KiCad and Altium symbols differ: pin {pin} {field} is {kicad} in KiCad and {altium} in Altium.",
                      "Fill the library again; report it if the symbols still differ.",
                      [r"^(?:parity: )?(?:export: )?pin (?P<pin>\S+) (?P<field>name|electrical type|missing): KiCad (?P<kicad>.+?) / Altium (?P<altium>.+?)(?: \(|$)"]),
    "parity.courtyard": ("The courtyard differs between KiCad ({kicad}) and Altium ({altium}).",
                         "Fill the library again; report it if they still differ.",
                         [r"^(?:parity: )?(?:export: )?courtyard \w+: KiCad (?P<kicad>.+?) / Altium (?P<altium>.+?)(?: \(|$)"]),
    "parity.model3d": ("The 3D model differs between KiCad ({kicad}) and Altium ({altium}).",
                       "Attach the 3D model to both, or fill the library again.",
                       [r"^(?:parity: )?(?:export: )?3D model presence: KiCad (?P<kicad>.+?) / Altium (?P<altium>.+?)(?: \(|$)"]),
    "parity.pad": ("Pad {pad} {field} differs: KiCad {kicad}, Altium {altium}.",
                   "Fill the library again so both are written from the same footprint; report it if they still differ.",
                   [r"^(?:parity: )?(?:export: )?pad (?P<pad>\S+) (?P<field>[A-Za-z /]+?): KiCad (?P<kicad>.+?) / Altium (?P<altium>.+?)(?: \(|$)"]),
    "parity.other": ("KiCad and Altium differ: {what}[ {field}][ (KiCad {kicad}, Altium {altium})].",
                     "Fill the library again; report it if they still differ.",
                     [r"^(?:parity: )?(?:export: )?(?P<what>.+?) (?P<field>[A-Za-z0-9 /]+): KiCad (?P<kicad>.+?) / Altium (?P<altium>.+?)(?: \(|$)",
                      r"^parity: (?P<what>.+)"]),
    # verify (rule `verify`: a person must look first)
    "verify.datasheet_read": ("The {made} came from a model reading the datasheet, so a person has to check before it is released[ ({flagged})].",
                              "Compare it with the datasheet, then approve it or release anyway.",
                              [r"^(?:verify: )?datasheet reader output \((?P<made>[^)]*)\)(?:.*\((?P<flagged>\d+ items flagged)\))?"]),
    "verify.sim_pin_map": ("The simulation model {model} is attached, but its pin map is a guess ({method}).",
                           "Check the model's pins against the symbol, then release it.",
                           [r"^(?:verify: )?sim model (?P<model>\S+): pin map (?:not verified|to verify) \((?P<method>.+)\)"]),
    "verify.other": ("A person has to check this part first: {detail}.", "Check it, then approve it or release anyway.",
                     [r"^verify: (?P<detail>.+)"]),
    # cross-check of a datasheet read (crosscheck)
    "crosscheck.disagree": ("{who} disagree on {field} ({first} vs {second}); nothing was written to the library.",
                            "Check the datasheet[ (page {page})] and pick one, or release anyway.",
                            [r"cross-check (?:disagrees|disagreed)(?:, nothing written)?: (?P<detail>.+)"]),
    "crosscheck.unchecked": ("The datasheet read could not be cross-checked[ ({why})].",
                             "Compare the footprint with the datasheet before you release it.",
                             [r"cross-check unchecked: (?P<why>.+)", r"not checked \((?P<why>[^)]+)\)"]),
    # the datasheet reader
    "datasheet.no_link": ("No datasheet link is known, so the datasheet could not be read.",
                          "Add the datasheet link to the BOM line, put the PDF in your [datasheet] folder, or pick a footprint by hand.",
                          [r"no datasheet URL known"]),
    "datasheet.no_key": ("The datasheet reader is not set up (no model API key).",
                         "Add a model API key (ecadlib init shows how), or pick a footprint by hand.",
                         [r"needs datasheet reader \(no API key\)"]),
    "datasheet.awaiting_ok": ("The datasheet is not read yet: reading it with AI costs money, so ecadlib waits for your OK.",
                              "Click Read with AI (it shows the model and the cost first), or pick a footprint by hand.",
                              [r"waiting for your OK to read it with AI"]),
    "datasheet.not_downloaded": ("The datasheet PDF was not downloaded here, so its content was not checked locally.",
                                 "Open the datasheet and compare the footprint with it.",
                                 [r"PDF was not downloaded here"]),
    "datasheet.some_pages": ("Only some datasheet pages were read[ ({pages})].",
                             "Check that the other pages change nothing in the footprint or pinout.",
                             [r"only some pages were sent \((?P<pages>[^)]*)\)"]),
    "datasheet.family_pdf": ("The datasheet came from your datasheet folder, from a file not named exactly for this part ({file}).",
                             "Check the PDF covers this exact part number, package and pinout.",
                             [r"datasheet from your folder: (?P<file>\S+) in the datasheet folder"]),
    "datasheet.standard_land": ("The datasheet read named the standard package {package} ({why}), so its checked land was used "
                                "instead of the pads read from the drawing.",
                                "Check the package code on the datasheet's drawing is {package}.",
                                [r"standard land: the datasheet read identified (?P<package>\S+) \(.*\); (?P<why>.+?), so the checked standard land"]),
    "datasheet.failed": ("Reading the datasheet failed[ ({why})].",
                         "Try again later, or pick a footprint by hand.",
                         [r"datasheet(?: reader)?: failed \((?P<why>.+)\)"]),
    # how the librarian found the part (review lines)
    "librarian.lcsc_mismatch": ("The BOM's LCSC number {lcsc} is the {got}, not the {mpn}.",
                                "Fix the LCSC number on the BOM line.",
                                [r"LCSC (?P<lcsc>\S+) is (?P<got>\S+), not (?P<mpn>[^\s:;,]+)"]),
    "librarian.footprint_guess": ("The footprint {fp} was chosen by {how}, not taken from the datasheet.",
                                  "Compare it with the land pattern in the datasheet.",
                                  [r"^footprint (?P<fp>\S+) chosen by (?P<how>.+?): compare"]),
    "librarian.symbol_match": ("The symbol {sym} was matched by {how}.",
                               "Check its pin names and numbers against the datasheet.",
                               [r"^symbol (?P<sym>\S+) matched by (?P<how>.+)"]),
    "librarian.easyeda": ("The footprint and symbol were converted from EasyEDA ({lcsc}).",
                          "Check the pads and pins against the datasheet.",
                          [r"EasyEDA (?P<lcsc>\S+) conversion"]),
    "librarian.family_pinout": ("The pinout of {sym} comes from the {mpn} family table, not from this part's datasheet.",
                                "Check pins 1-3 against the datasheet.",
                                [r"symbol pinout (?P<sym>\S+) from the (?P<mpn>\S+) family table"]),
    # sign-off, duplicates, Altium 365
    "approval.waiting": ("Waiting for sign-off[ ({missing})].", "Ask a reviewer to approve it in the review queue.",
                         [r"^awaiting approval(?:: (?P<missing>.+))?"]),
    "approval.changed": ("The part changed after its last check.", "Run the release check again.",
                         [r"changed since the release check"]),
    "dedupe.duplicate": ("This part is a duplicate of {of}.", "Use {of} instead, or keep both on purpose.",
                         [r"duplicate of (?P<of>\S+)"]),
    "a365.deleted": ("The part was deleted in the Altium 365 workspace.",
                     "Restore it there, or take it off the BOM.",
                     [r"deleted in the Altium 365 workspace"]),
    # the independent visual proof (ecadlib visual-check)
    "proof.doubt": ("The independent check is not sure about this part[: {reason}].",
                    "Compare the part with the datasheet before you approve it.",
                    [r"^proof doubt: ?(?P<reason>.*)"]),
    "proof.skipped": ("The independent check did not run[: {reason}].",
                      "Compare the pictures with the datasheet yourself before you approve it.",
                      [r"^proof skipped: ?(?P<reason>.*)"]),
    "proof.finding": ("The independent check found a problem: {what}.",
                      "Compare that detail with the datasheet before you approve it.",
                      [r"^proof: (?P<what>.+)"]),
    # anything else: shown as written, with a generic next step
    "other": ("{detail}", "Look at the part, then approve it or release anyway.", []),
}

NOTES = {"cannot be overridden": "This one cannot be released anyway.",
         "not covered by --allow": "It is new since the part was released anyway."}
OVERRIDE = re.compile(r"\s*\((cannot be overridden|not covered by --allow)\)\s*$")
_COMPILED = [(code, re.compile(p)) for code, (_, _, ps) in CODES.items() for p in ps]


def _num(v):
    """0.500 -> 0.5, 2.0 -> 2; other values as written."""
    if isinstance(v, float):
        return f"{v:.3f}".rstrip("0").rstrip(".")
    s = str(v)
    return f"{float(s):.3f}".rstrip("0").rstrip(".") if re.fullmatch(r"-?\d+\.\d+", s) else s


def fill(template, values):
    """Fill {name}s; drop each [optional part] whose values are missing or empty."""
    have = {k: _num(v) for k, v in values.items() if v not in (None, "", [], ())}

    def opt(m):
        names = re.findall(r"{(\w+)}", m.group(1))
        return m.group(1) if all(n in have for n in names) else ""
    t = re.sub(r"\[([^\[\]]*)\]", opt, template)
    return re.sub(r"{(\w+)}", lambda m: have.get(m.group(1), "?"), t)


def held(code, **values):
    """Reason code + values -> (text, fix). Unknown code: KeyError (every code needs a table entry)."""
    text, fix, _ = CODES[code]
    values = _derive(code, dict(values))
    return fill(text, values), fill(fix, values)


def _derive(code, v):
    if code == "crosscheck.disagree":
        if v.get("detail") and not v.get("field"):
            v.update(_crosscheck_detail(v["detail"]))
        other = str(v.get("other") or "second read")
        v.setdefault("who", "The datasheet read and the standard package table" if "table" in other
                     else "The two datasheet reads")
        unit = "" if v.get("field") in ("pin count", "pin numbers", "pin names", "exposed pad", "package type") else " mm"
        for k in ("first", "second"):
            s = _num(v.get(k, "?"))
            v[k] = s + unit if unit and re.fullmatch(r"-?[\d.]+", s) else s
    elif code in ("pins.pin_without_pad", "pins.pad_without_pin"):
        k = "pins" if code == "pins.pin_without_pad" else "pads"
        one = re.fullmatch(r"[^\s,-]+", str(v.get(k) or "?"))
        v[k + "_have"] = f"{k[:-1] if one else k} {v.get(k) or '?'} {'has' if one else 'have'}"
    elif code == "parity.pad_missing":
        v.setdefault("side", "KiCad" if str(v.get("k")) == "present" else "Altium")
    elif code == "verify.datasheet_read":
        v["made"] = " and ".join(x.strip() for x in str(v.get("made") or "part").split(",") if x.strip()) or "part"
    elif code == "other":
        v["detail"] = str(v.get("detail") or "").rstrip(".") + "."
    return v


def _crosscheck_detail(detail):
    """First disagreement of a crosscheck.summary_line: "<against>: pitch read 0.5, table 0.65; ..." -> values."""
    m = re.search(r"(?:^|: |; )(?P<field>[a-z ]+?) read (?P<first>[^,;:]+), (?P<other>table|second read) (?P<second>[^;]+)", detail)
    return {k: v.strip() for k, v in m.groupdict().items()} if m else {"field": "the numbers", "first": "?", "second": "?"}


def explain(reason):
    """A raw reason string (plan.json hold_reasons, librarian review lines, verify notes) -> {code, text, fix, note,
    raw}. Never raises; unmatched text gets code "other" and is shown as written (cleaned)."""
    raw = str(reason or "").strip()
    note = ""
    m = OVERRIDE.search(raw)
    body = raw
    if m:
        note, body = NOTES[m.group(1)], raw[:m.start()]
    for code, rx in _COMPILED:
        hit = rx.search(body)
        if hit:
            values = {k: v for k, v in hit.groupdict().items() if v is not None}
            text, fix = held(code, **values)
            return {"code": code, "text": clean(text), "fix": fix, "note": note, "raw": raw}
    detail = re.sub(r"^[a-z0-9_]+: ", "", body)
    text, fix = held("other", detail=clean(detail))
    return {"code": "other", "text": text, "fix": fix, "note": note, "raw": raw}


def explain_all(reasons):
    """Several raw reasons -> explained, one per distinct sentence (same text + fix only once)."""
    out, seen = [], set()
    for r in reasons or []:
        e = explain(r)
        if (e["text"], e["fix"]) not in seen:
            seen.add((e["text"], e["fix"]))
            out.append(e)
    return out


# ---------------------------------------------------------------- errors
ERRORS = {
    "file_not_found": ("{what} was not found[: {name}].", "Check the name and the folder, then try again."),
    "not_a_bom": ("{name} does not look like a BOM.", "Use a CSV or XLSX with at least a part number or value column."),
    "no_plan": ("There is no library plan here yet.", "Fill the library from a BOM first (ecadlib librarian BOM.csv)."),
    "no_api_key": ("No model API key is set, so datasheets cannot be read.",
                   "Add a key (ecadlib init shows how), or work with the standard parts and KiCad libraries only."),
    "network": ("{host} could not be reached.", "Check the internet connection or the proxy, or run with --offline."),
    "permission": ("ecadlib is not allowed to write {name}.", "Pick a folder you can write to, or fix its permissions."),
    "locked": ("Someone else is changing this library right now.", "Wait a moment and try again."),
    "bad_value": ("{what} is not valid[: {why}].", "Fix it and try again."),
    "unexpected": ("Something went wrong[ ({what})].", "Run it again with --debug and report the output."),
}


def error(code, **values):
    text, fix = ERRORS[code]
    return fill(text, values), fill(fix, values)


def friendly_error(exc):
    """Exception -> (text, fix): one line each, no traceback, no absolute path."""
    import errno, os
    name = lambda p: os.path.basename(str(p or "").rstrip("/\\")) or str(p or "")
    if isinstance(exc, FileNotFoundError):
        return error("file_not_found", what="A file", name=name(exc.filename))
    if isinstance(exc, PermissionError):
        return error("permission", name=name(exc.filename) or "that folder")
    if isinstance(exc, (ConnectionError, TimeoutError)) or type(exc).__name__ in ("URLError", "timeout"):
        host = getattr(getattr(exc, "reason", None), "host", None) or "The server"
        return error("network", host=host)
    if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
        return error("unexpected", what="the disk is full")
    if isinstance(exc, ValueError):
        return error("bad_value", what="A value", why=clean(str(exc))[:160])
    return error("unexpected", what=clean(f"{type(exc).__name__}: {exc}")[:160])


# ---------------------------------------------------------------- clean text
INTERNAL = ("plan.json", "ds_route", "stdparts", "crosscheck", "release_check", "footprint_check", "kicad_import",
            "symbol_layout", "hold_reasons")
_MOD = r"ecadlib\.(?!toml\b)[a-z_]+\b"
_ABS = re.compile(r"(?<![\w.:/])(?:/(?:[\w.@+-]+/)+[\w.@+-]+|[A-Za-z]:\\(?:[^\s\\]+\\)+[^\s\\]+)")
_MODULE = re.compile(r"\s*\((?:see |via |from |by )?" + _MOD + r"[^)]*\)|\b" + _MOD)


def clean(text):
    """Absolute paths -> file name; "(ecadlib.stdparts)"-style module mentions removed."""
    t = _ABS.sub(lambda m: re.split(r"[\\/]", m.group(0))[-1], str(text or ""))
    t = re.sub(r"\s*\((?:%s)\)" % "|".join(map(re.escape, INTERNAL)), "", t)        # "(kicad_import)" notes
    return re.sub(r"\s{2,}", " ", _MODULE.sub("", t)).strip()


def leaks(text):
    """Internal names and absolute paths found in user-facing text (for tests): [str]."""
    t = str(text or "")
    out = [n for n in INTERNAL if n in t]
    out += re.findall(r"\b" + _MOD, t) + _ABS.findall(t)
    return out
