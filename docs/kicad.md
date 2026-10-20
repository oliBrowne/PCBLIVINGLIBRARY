# KiCad

The KiCad side: the library as KiCad files, real KiCad checks and KiCad/Altium parity.

On this page:

* [`kicad_export.py`: the same library as KiCad files](#kicad_exportpy-the-same-library-as-kicad-files)
* [`parity.py`: KiCad and Altium always match](#paritypy-kicad-and-altium-always-match)
* [Real KiCad checks (`tests/test_kicad_real.py`)](#real-kicad-checks-teststest_kicad_realpy)

## `kicad_export.py`: the same library as KiCad files

```
ecadlib kicad-export out/librarian/plan.json -o out/kicad --name mylib [--lib examples/kicad_lib] [--include-held] [--offline]
python3 -m tests.test_kicad_export
```

Writes what the librarian built for Altium as a KiCad library: `mylib.kicad_sym` (one symbol per plan part, named after it, with Reference, Value, Footprint `mylib:<footprint>`, Datasheet, Description, Manufacturer, MPN, supplier numbers such as `LCSC` and the plan's other parameters), `mylib.pretty/` (one `.kicad_mod` per footprint, renamed to the plan's footprint name), `mylib.3dshapes/` (STEP models, referenced as `${KIPRJMOD}/mylib.3dshapes/X.step`), `sym-lib-table` / `fp-lib-table` with `${KIPRJMOD}` paths (the library's own row is added or replaced; other rows, such as a project's own libraries or another export, are kept, so `-o` can be the KiCad project folder), and `kicad_export.json` (what came from where, and the checks). To put it into a KiCad project, export with `-o` set to the project folder (its own table rows stay), or copy `mylib.kicad_sym`, `mylib.pretty/` and `mylib.3dshapes/` and paste the two `lib` lines into the project's tables; copying the generated `sym-lib-table` / `fp-lib-table` over the project's would drop its rows. Held parts are skipped unless `--include-held`; output is sorted and repeatable.

* **Symbols:** a part whose symbol came from KiCad (`kicad:` or a `--lib` folder) gets that KiCad symbol again, with its `extends` chain flattened and renamed; an EasyEDA symbol is converted again from the EasyEDA data; anything else (datasheet reader, or a source that can't be reached) is drawn from its `.SchLib`, read back with `altium_lib.read_schlib` (pins with type, shape and visibility, body graphics, parts as units, display modes as De Morgan styles).
* **Footprints:** the plan's `.kicad_mod` when there is one, else the `.PcbLib` converted back. STEP files come from `kicad-packages3D` (through the HTTP cache); ecadlib-generated footprints have none, since Altium gets an extruded body.
* **Format:** KiCad 8 (`.kicad_sym` version 20231120, footprints at most 20240108), so KiCad 8, 9 and 10 can open it. KiCad 9/10 syntax from the upstream library is written the KiCad 8 way and flags KiCad 8 doesn't know are dropped.
* **Checks:** every file parses with `ecadlib.sexpr`, each footprint passes release_check's footprint rule at the level the release check recorded, and symbol pins are checked against footprint pads. **In KiCad itself** (7.0.11, see [real KiCad checks](#real-kicad-checks-teststest_kicad_realpy)): the `fp-lib-table` resolves in a KiCad project and its footprints match the library; KiCad 7 refuses the KiCad 8 symbol library as such, and loads it with the two KiCad 8 tokens (`exclude_from_sim`, `generator_version`) removed, every pin, graphic and field unchanged. KiCad 8, 9 and 10 were not run here. A KiCad 7 symbol's `ki_description` becomes the `Description` field.
* **License:** KiCad library content is CC-BY-SA 4.0. An export that includes some gets a `NOTICE.txt`, and the export stays out of git (`out/kicad/` is in `.gitignore`).

## `parity.py`: KiCad and Altium always match

```
ecadlib parity out/librarian/plan.json                                   # plan's .kicad_mod vs .PcbLib / .SchLib
ecadlib parity out/librarian/plan.json --kicad out/kicad [--name mylib] [--altium D:/libs/ecadlib]   # the exports
ecadlib parity pair X.kicad_mod X.PcbLib | Lib.kicad_sym:Name X.SchLib    # one pair of files
ecadlib parity survey [--quick] [--kicad-footprints DIR]                 # every generator / standard / KiCad footprint
python3 -m tests.run_all test_parity
```

Reads both libraries back from the files actually written (KiCad through `sexpr`, Altium through `altium_lib`'s reader
and, when installed, `pyaltiumlib` as a second reader) and compares, per part: pad count and numbers; each pad's
position, size, rotation, shape (round / rect / rounded rect with corner %, custom polygon outline), side (top / bottom
SMD, plated through-hole, NPTH), drill (width, slot length, slot direction), paste and mask (on / off, manual margins);
paste / mask windows without copper and the exposed pad's paste coverage; courtyard extent; 3D model presence; symbol
pin count, numbers, names (`~{RST}` = `R\S\T\`) and electrical types; pins without a pad in one tool but not the
other; MPN, Manufacturer, Value, Description and Datasheet (KiCad symbol properties vs the DbLib row with `--altium`,
else the plan parameters); with `--kicad` and `--altium`, the `.kicad_dbl` SQLite rows vs the DbLib rows (same parts,
same symbol / footprint, same fields). Conventions live in one place (`_a2k`, `canon_pad`): KiCad frame, mm, y down;
Altium 1 mil = 10000 units with y up; a pad is reduced to rotation in [0, 90). Tolerances (`parity.TOL`): 0.001 mm for
positions, sizes, drills and margins, 0.005 mm courtyard, 0.01 degrees, 1 % corner radius. Converter approximations that
are documented (a curved custom pad as its bounding rectangle, trapezoid as rectangle, chamfers) are `approx`, not held.

* **Release check / librarian:** rule `parity` (plan mode) holds a part with one line per field, e.g.
  `parity: pad 1 size/rotation: KiCad (1.95, 0.65, 0) / Altium (1.95, 0.6, 0) (w, h mm, rotation deg)`; `--allow`
  overrides it like the other rules. `[release] parity = "hold"` (default) | `"warn"` | `"off"`.
* **Also run by** `ecadlib testpack` (`parity.md`, export mode with both databases) and `ecadlib demo`; Workspace
  `parity(bom, kicad=False)` and the MCP tool `parity`.
* **Found and fixed:** `kicad-export` wrote no 3D model for any footprint without a KiCad one (every generated or
  standard footprint), while its Altium footprint carried a STEP: the KiCad export now writes the `.PcbLib`'s embedded
  STEP (byte for byte) or its extruded body as a STEP to `<lib>.3dshapes/` and references it. Running it over KiCad's
  own footprint library found four converter bugs, each with a regression test in `tests/test_parity.py`: a
  through-hole pad's drill offset was dropped (now written to the Altium hole-offset field, the layout KiCad's own
  Altium importer reads), an SMD pad's shape offset was ignored, copper-less mask / paste apertures became a square on
  paste only (now their round or custom shape on every layer they have), and `kicad-export` did not fold a custom
  pad's regions that only touch the anchor (or the mask / paste of a stroked outline) back into the pad.
* **Numbers (2026-10-16):** survey: 68 package specs (27 classic + 41 footprint_adv) through both the approval and the
  librarian converter, 615 standard-table footprints, each also converted back by `kicad-export`: 751 compared, 0
  mismatched. KiCad's own library (kicad-footprints f8440fa, fetched from gitlab.com, not committed), all 15468
  footprints through the librarian and `kicad-export` (`ecadlib parity survey --kicad-footprints DIR`, 3D paths not
  compared): 15442 match (248 of them with documented approximations), 26 still differ (open: custom pads whose
  primitives do not touch their anchor, several pads of one number with thermal vias, exposed-pad detection).
  Testpack (6 parts, export mode, 6 / 6 database rows) and demo (6 ready parts then; 4 since its family-table
  pinouts are held, and those 4 match): all match.
* **Not verified:** nothing was opened in Altium Designer; KiCad models that cannot be downloaded offline stay missing
  in an export (parity reports them as `file missing`).

## Real KiCad checks (`tests/test_kicad_real.py`)

```
python3 -m tests.test_kicad_real          # needs kicad-cli and KiCad's pcbnew Python module; skips cleanly without
```

Run 2026-10-15 with KiCad 7.0.11 (`kicad-cli`, `pcbnew` from `/usr/bin/python3`). The pcbnew work runs in
`tests/kicad_pcbnew_job.py` in a subprocess (KiCad's Python is often not the one running the tests).

* **Footprints:** all 68 package specs, density A and C of the 27 IPC parts and 30 random BGA / header / connector specs
  (152 files, 6389 pads) load in pcbnew with our pad numbers, centres, sizes and layers, one closed courtyard outline
  with our box, and the same graphics per layer; `kicad-cli fp upgrade` writes the same pads back and `fp export svg`
  plots them all. Done by hand once more over 174 files and 120 random specs: no difference.
* **DRC** (`pcbnew.WriteDRCReport`, one net per pad number) on a board of all 122 spec footprints: the only findings
  are the ones KiCad 7.0.11's own footprints of the same names show on the same board (0.2 mm thermal-via drills,
  the 1.00 mm header's 0.15 mm pad gap, USB-C's stacked pads, mounting pads with one number, REF** over the SOT-23
  pin-1 triangle). Density C put REF** on the silk outline; fixed.
* **Library tables:** the exported `fp-lib-table` (`version 7`, `${KIPRJMOD}`) resolves in a KiCad 7 project; DRC's
  library check finds every footprint and reports none as different from the library (a wrong library or name is
  reported, so the check is live). `sym-lib-table` has the same syntax; KiCad 7 has no CLI that loads it.
* **Symbols:** see [KiCad export](#kicad_exportpy-the-same-library-as-kicad-files); EasyEDA symbols too (a slanted EasyEDA pin made KiCad reject the whole library: pins
  are now drawn at 0/90/180/270 with a note).
* **kicad_import** reads pads (number, centre, size, rotation, drill, slot direction, plating) exactly as pcbnew does:
  6304 pads of ours in the test, and by hand 71,605 pads of 2291 KiCad 7.0.11 library footprints (18 libraries) and
  2548 KiCad 7 symbols without an error after two fixes (text `(at x y unlocked)`, zero-length arcs).
* **PcbLib back to KiCad:** `kicad_export.footprint_from_pcblib` output for all 68 specs loads (on KiCad 7 after
  rewriting its KiCad 8 header and Reference/Value properties) with the same copper pads, holes and pad outlines.
* **STEP:** `step_gen` models of 10 families, put on a board, go through KiCad's OpenCascade reader
  (`kicad-cli pcb export step`) with no read error and every solid in the output; by hand all 68 specs (3151 solids).
* **Not checked:** KiCad 8, 9 and 10 (not installed; the optional CI job `kicad` also runs the tests on KiCad 8 from
  the KiCad PPA, not run from here); `sym-lib-table` loaded by KiCad; STEP geometry inside KiCad beyond loading.
