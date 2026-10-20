# How ecadlib talks to people

ecadlib's readers are hardware engineers and librarians, not programmers. Every screen, terminal line, report,
dialog and tool result follows these rules. The sentences for held parts and errors live in one place,
`ecadlib/messages.py`; use it instead of writing new ones.

## The rules

1. **Lead with what happened, then what to do next.** "6 ready, 2 held. Next: fix the held parts in the review."
2. **One idea per line.** Short sentences. No semicolon chains.
3. **The same word for the same thing, everywhere** (see the glossary). A part is **ready**, **held** or
   **waiting for sign-off**; never "pass", "blocked", "failed check" or "awaiting_approval" in text people read.
4. **Numbers carry units**: 0.5 mm, 16 pins, $0.42, 3 min. Counts read as "6 ready, 2 held" (zero counts are left out).
5. **Every held part says why and what to do**, in one sentence each, with the values filled in:
   "The two datasheet reads disagree on pin pitch (0.5 mm vs 0.65 mm); nothing was written to the library."
   "Check the datasheet (page 34) and pick one, or release anyway."
6. **Errors are two lines**: what went wrong, how to fix it. Never a traceback, raw JSON or an absolute server path
   (only with `--debug`). Paths are shown relative to the workspace, or as a file name.
7. **No internal names** in text people read unless they asked for detail: not `plan.json`, `ds_route`, `stdparts`,
   `crosscheck`, `parity`, `release_check`, `hold_reasons`, `ecadlib.<module>`. Say what the thing *is*:
   "the library plan", "the datasheet reader", "the standard package table", "the cross-check",
   "KiCad and Altium match", "the release check".
8. **Machine output does not change.** `--json`, plan files and CSV columns keep their codes and field names; the
   human sentence is shown next to the code, never instead of it in machine output.
9. **Commands in a next step are real commands** the reader can paste (`ecadlib review`, `ecadlib ui`), at most three.

## Status words

| Status (machine) | Say | Meaning |
|---|---|---|
| `ready` | **ready** | Passed every check (or released anyway); goes to the library on the next push. |
| `held` | **held** | Failed a check. Always shown with the reason and the fix. |
| `awaiting_approval` | **waiting for sign-off** | Ready, but the sign-off policy needs a reviewer's approval first. |
| `duplicate` | **duplicate** | Same part as another one in the library; the other one is used. |
| `existing` | **already in Altium 365** | Pulled from the workspace; nothing to do. |
| result `allowed` | **released anyway** | A person released it despite a held reason; recorded with their name. |

Actions use the same words in every front end: **Pick…** (a symbol or footprint candidate), **Release anyway**,
**Hold again**, **Approve**, **Reject**, **Open datasheet**.

## Glossary

| Word | Means | Not |
|---|---|---|
| library | The symbols, footprints and 3D models ecadlib builds for you (Altium and/or KiCad files). | lib set, models dir |
| BOM | The bill of materials you give it (CSV/XLSX, Altium, KiCad or hand-made). | input file |
| part | One BOM line that becomes one library component. | item, row |
| symbol | The schematic symbol (`.SchLib` / `.kicad_sym`). | sym |
| footprint | The PCB land pattern (`.PcbLib` / `.kicad_mod`). | fp, land pattern model |
| 3D model | The STEP body on the footprint, with its height. | body, STEP model |
| datasheet reader | The model that reads a datasheet's drawings when no library has the part. | LLM, ds route |
| cross-check | A second, independent source (the standard package table or a second read) compared with the datasheet read. | crosscheck |
| standard package table | ecadlib's built-in table of standard packages (SOT-23, SOIC-8, 0603, ...). | stdparts |
| release check | The checks a part must pass before it is released (pins vs pads, footprint, height, fields, KiCad/Altium match). | release_check |
| KiCad and Altium match | The KiCad and Altium files of a part read back the same (pads, holes, paste, courtyard, 3D). | parity |
| review queue | Held parts and held changes that wait for a person to decide. | held_parts |
| library plan | The record of every part, its sources and its state (a file next to the library). | plan.json |
| release anyway | Override a held reason after looking; recorded with who and when. | --allow |

## Reason codes

Each held reason has a code (kept in JSON next to the text) and a sentence + next step in
`ecadlib/messages.py`:

```python
from ecadlib import messages as M
M.held("crosscheck.disagree", field="pin pitch", first=0.5, second=0.65, page=34)
# ('The two datasheet reads disagree on pin pitch (0.5 mm vs 0.65 mm); nothing was written to the library.',
#  'Check the datasheet (page 34) and pick one, or release anyway.')
M.explain("pins: symbol pins without a pad: 5-8")      # any reason already stored in a plan
# {'code': 'pins.pin_without_pad', 'text': 'Symbol pins 5-8 have no pad on the footprint.', 'fix': ..., 'note': '', 'raw': ...}
M.counts(["ready"] * 6 + ["held"] * 2)                  # '6 ready, 2 held'
M.error("no_api_key")                                   # (what went wrong, how to fix it)
```

| Code | When |
|---|---|
| `files.no_symbol`, `files.no_footprint` | No source had the symbol / footprint. |
| `files.missing`, `files.unreadable` | A library file is gone or damaged (cannot be released anyway). |
| `pins.pin_without_pad`, `pins.pad_without_pin`, `pins.no_pins`, `pins.no_pads` | Symbol pins and footprint pads do not match. |
| `footprint.*` | The footprint check: overlapping pads, pads too close, no pin 1, clockwise numbering, ball rows, silkscreen, courtyard. |
| `height.missing`, `height.out_of_range` | No 3D height, or one outside the usual range for the package. |
| `fields.no_datasheet`, `fields.no_description`, `fields.empty` | A required field is empty. |
| `parity.*` | The KiCad and Altium files differ (pad count, a pad, a pin, courtyard, 3D model). |
| `verify.datasheet_read`, `verify.other` | A person must look first (datasheet-read part). |
| `crosscheck.disagree`, `crosscheck.unchecked` | The datasheet read and its second source disagree / could not be compared. |
| `datasheet.*` | No datasheet link, no API key, PDF not downloaded, only some pages read, the read failed. |
| `librarian.*` | How a part was found and what to double-check (LCSC mismatch, footprint chosen by name, EasyEDA conversion, family pinout). |
| `approval.waiting`, `approval.changed`, `dedupe.duplicate`, `a365.deleted` | Sign-off, duplicates, Altium 365 workspace state. |
| `change.held` | A change to a released part waits for `ecadlib part approve` (release anyway cannot release it). |
| `proof.doubt`, `proof.finding` | The independent visual check. |
| `other` | Anything not in the table: shown as written (cleaned), with a generic next step. |

`tests/test_messages.py` fails when a release-check rule, a footprint-check rule, a KiCad/Altium comparison field or a
new review/verify note is added without a sentence here.
