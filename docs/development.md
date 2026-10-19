# Working on ecadlib

Requirements, the code layout, the test suites, the accuracy gate, releases and open next steps.

On this page:

* [Robustness tests: real-world BOMs, fuzzers, CLI smoke](#robustness-tests-real-world-boms-fuzzers-cli-smoke)
* [End-to-end tests and the test pack (`tests/test_e2e.py`, `ecadlib testpack`)](#end-to-end-tests-and-the-test-pack-teststest_e2epy-ecadlib-testpack)
* [`eval`: accuracy test set and scorer (`ecadlib/eval/`)](#eval-accuracy-test-set-and-scorer-ecadlibeval)
* [Releases](#releases)
* [Exact commands run (in `/mnt/project-files/ecad-librarian`)](#exact-commands-run-in-mntproject-filesecad-librarian)
* [Next steps](#next-steps)

Tests: `python3 -m tests.test_ui` covers the workspace, web server, MCP server and CLI. `python3 -m tests.test_offline` covers the core modules. `python3 -m tests.test_footprints` checks generated footprints by part type against KiCad's library and the KLC rules.

Requirements: Python 3.10 or later (tested on 3.11 and 3.13) with `requests`, `pandas`, `openpyxl`, `jsonschema` and `olefile` (and `tomli` on Python 3.10), which pip installs. `pypdf` and `pillow` are optional extras. `easyeda2kicad` was installed only so its source could be read for the endpoints; nothing imports it at runtime.

```
ecad-librarian/
├── README.md
├── ecadlib/
│   ├── footprint_gen.py      # IPC-7351B generator -> .kicad_mod  (+ `compare` vs a reference footprint)
│   ├── footprint_adv.py      # BGA/LGA/CSP, pin headers, connectors, odd shapes (pad_table from the datasheet drawing)
│   ├── footprint_bench.py    # pad-by-pad comparison with KiCad's official footprints
│   ├── package_schema.json   # JSON Schema: the datasheet-dimension contract an LLM fills
│   ├── bom_triage.py         # BOM CSV/XLSX -> triage report (.md + .csv)
│   ├── providers.py          # Provider interface: EasyEDA, JLCPCB search, + DigiKey/Mouser/Nexar/SnapMagic/SamacSys stubs
│   ├── kicad_index.py        # scans .kicad_sym / *.pretty for MPN / LCSC / value+package matches
│   ├── altium_export.py      # Altium DbLib: parts.sqlite + parts.xlsx + two .DbLib files
│   ├── app.py                # Workspace: the shared actions behind the UI, MCP server and CLI
│   ├── ui/                   # local web UI (python -m ecadlib.ui), one static page, stdlib server
│   ├── mcp_server.py         # MCP server over stdio (python -m ecadlib.mcp_server)
│   ├── altium365.py          # Altium 365 workspace: release plan + push via the Nexar Design API
│   ├── altium_dblib_sync.py  # Altium without 365: the same plan -> .DbLib / .SVNDbLib library folder
│   ├── altium_lib.py         # pure-Python .PcbLib/.SchLib writer (+ minimal reader, `dump`)
│   ├── kicad_export.py       # librarian plan -> .kicad_sym + .pretty + .3dshapes + lib tables
│   ├── cfb.py                # Compound File Binary (OLE2) v3 writer used by altium_lib
│   ├── assets/pcblib_template.json.gz   # Library header + FileVersionInfo from an Altium-saved file (see NOTICE)
│   ├── httpcache.py          # disk cache + per-host rate limit around requests
│   ├── part_lookup.py        # part identification through Nexar, DigiKey and Mouser (+ JLCPCB search)
│   ├── upkeep.py             # weekly lifecycle/upstream/A365/change checks, only new changes, one GitHub issue or PR
│   └── sexpr.py              # tiny KiCad S-expression reader (+ quote-keeping writer)
├── examples/
│   ├── sample_bom.csv        # 14 lines, LCSC numbers checked against lcsc.com (see below)
│   ├── packages/*.json       # 9 package specs (0402/0603/0805/1206, SOIC-8/16, QFN-32, VQFN-16)
│   ├── symbols/*.json        # symbol pin lists: CH340C, TPS62133RGTR (both UNVERIFIED), Resistor, Capacitor
│   └── kicad_lib/ecadlib_demo.kicad_sym   # 5-symbol "local library" for the triage demo
├── tests/test_offline.py     # offline self-tests (header mapping, values, IPC math, provider parsing)
└── out/
    ├── footprints/ecadlib.pretty/*.kicad_mod   # 9 generated footprints
    ├── footprints/gen_log.txt, compare_vs_kicad_official.txt
    ├── triage_sample_bom.{md,csv}                       # live run (sources blocked, see below)
    ├── triage_sample_bom_stub_providers_only.{md,csv}   # shows the needs_footprint_generation bucket
    ├── altium/{parts.sqlite, parts.xlsx, ecadlib_sqlite.DbLib, ecadlib_excel.DbLib}
    ├── altium_models/{PcbLib/*.PcbLib (9), SchLib/*.SchLib (4)}   # written by altium_lib.py
    └── altium365/{plan.json, push_dry_run.json}
```

## Robustness tests: real-world BOMs, fuzzers, CLI smoke

```
python3 -m tests.test_realworld_boms        # BOM exports as engineers make them, plus the error contracts
python3 -m tests.test_fuzz [SCALE]          # deterministic fuzzers (fixed seeds, ~12 s; SCALE=15 for a long run)
python3 -m tests.test_cli_smoke [--subprocess]   # every command: -h and a minimal offline run
```

* **BOMs:** Altium ActiveBOM CSV/XLSX (title rows, `Manufacturer Part Number 1/2`, totals), KiCad 8 (`${QUANTITY}`, `${DNP}`), JLCPCB, DigiKey and Mouser cart exports, Octopart, semicolon CSV with decimal commas in latin-1, UTF-8 with BOM, UTF-16 (Excel "Unicode Text"), hand-made Excel with merged title rows, a cover sheet, blank lines, DNP marks and `R1-R10` ranges. They were run through triage, librarian, resolve, release-check, kicad-export, dblib, dblib-sync, dedupe, upkeep, the web UI upload and the MCP tools.
* **Spreadsheet safety:** report CSVs go through `ecadlib/sheetsafe.py`. A cell that a spreadsheet would run as a formula (`= + - @`, tab, CR) gets a leading apostrophe, and `read_bom` removes it again. Numbers (`-5`, `+3.3`) are not changed. The DbLib's `.xlsx` stores every cell as text and its `.csv` keeps bare `=Name` references, so Altium's `=Value` Comment still works.
* **Fuzzers:** the BOM reader, `sexpr.parse`, the KiCad footprint/symbol importer, the Altium OLE readers (`.SchDoc`, `.PcbLib`), package-spec validation and build, `ecadlib.toml` loading, the web UI's JSON endpoints and the MCP tools. Each target may raise only its documented error type (listed in the file's docstring); anything else fails with the input that caused it.
* **CLI smoke:** every registered command answers `-h`. A minimal real invocation of each one (offline, in a temporary folder with `$HOME` redirected) must exit with an expected code, print no traceback and change nothing outside its output folder. `--subprocess` runs the installed `ecadlib` script instead, for the fresh-install check: `pip wheel --no-deps .`, install the wheel into a clean venv (Python 3.11 and 3.13), and run `python -m tests.test_cli_smoke --subprocess` from outside the checkout. `test_third_party_imports_are_declared` fails when a module imports a package that is not in `[project] dependencies` (only `pillow` and `pypdf` are optional extras).
* **Files and locales:** every report, plan, state and KiCad text file is read and written as UTF-8 whatever the system locale (Windows' cp1252 used to crash on `10kΩ`). `plan.json`, `librarian_state.json` and the datasheet batch state are replaced atomically (`ecadlib/fsutil.py`: temp file, fsync, rename), so a crash or a full disk leaves the previous file instead of a truncated one; a damaged HTTP cache entry counts as a miss. `python3 -m tests.test_review_rvh` holds the regression tests.

## End-to-end tests and the test pack (`tests/test_e2e.py`, `ecadlib testpack`)

```
python3 -m tests.run_all test_e2e test_testpack    # about a minute; the KiCad checks skip without kicad-cli / pcbnew
ecadlib testpack --out testpack                    # then open testpack/CHECKLIST.md in Altium Designer / KiCad
```

**Golden path.** `tests/test_e2e.py` runs the real `ecadlib` command in subprocesses, from an empty folder, as a user
would: `init --yes`; `project` on an Altium `.PrjPcb` (two `.SchDoc` sheets, written by ecadlib's test helper, not by
Altium); `librarian --offline` on a KiCad 7 hierarchical `.kicad_sch`, on that Altium BOM and on an `.xlsx`;
`release-check --allow` after the datasheet part is reviewed; `kicad-export` (three libraries into one KiCad project
folder), `kicad-dblib`, `export-parts`, `dblib-sync` and `altium365 push` (`--dry-run` and against a local fake Nexar).
Sources are offline and recorded (`tests/e2e_support.py`): real KiCad library files fetched from gitlab.com on 2026-10-16
(`tests/fixtures/e2e/kicad/`), put into the HTTP cache the way an online run leaves them; a SYNTHETIC EasyEDA payload;
SYNTHETIC Claude answers from a local server reached through `ANTHROPIC_BASE_URL` (which the datasheet reader now
honours, as the Anthropic SDKs do). Every other request goes to a dead proxy, so nothing reaches the internet.

Each output is then loaded by its tool:
* **KiCad** (7.0.11 here): `kicad-cli sym export svg` / `fp upgrade` / `fp export svg` on every library; pcbnew places
  every part of the database library's SQLite table through the project's `fp-lib-table` (DRC's library check finds each
  one, unchanged; a bogus library and a bogus name are reported, so the check is live) and DRC passes on a board of each
  library. The export is KiCad 8 format, so on KiCad 7 a KiCad 7 copy is loaded (`e2e_support.kicad7_copy`: properties to
  `fp_text`, `(hide yes)` to `hide`, `(fill no)` to `(fill none)`; pads untouched). The optional CI job `kicad` also runs
  these tests on KiCad 8 (PPA), as they are.
* **`.kicad_dbl` + SQLite** (no KiCad needed): every row's Symbol and Footprint is in the `.kicad_sym` / `.pretty`, every
  mapped column exists, and the rows are exactly the ready parts.
* **Altium** (file level only, nothing was opened in Altium Designer or Altium 365): every `.PcbLib` / `.SchLib` reads
  back with pyaltiumlib and ecadlib's reader with the same names; every Library Ref / Footprint Ref in the DbLib table and
  the parts table is in the file it names; the Altium 365 release (fake Nexar) names files uploaded in the same run, byte
  for byte our libraries, with the part's parameters.
* **MCP:** `ecadlib mcp` over stdio: initialize, tools/list, then `workspace_info`, `librarian_run`, `held_parts` and a
  call with a missing BOM (a clear error, no traceback).
* **Again, and after a crash:** the whole path runs a second and a third time on the same folder. The second run changes
  only the API log, the librarian's per-run state, `review.md` (written before the `--allow`) and init's
  `ecadlib.toml.proposed` (the rules file is kept); the third changes nothing but the log. A librarian killed with
  SIGKILL after its first parts recovers on the next run with the same plan and library files as an uninterrupted run.

Bugs these tests found, fixed (each with a test that fails without the fix): a KiCad part whose 3D model could not be
downloaded lost its official footprint; `kicad-export` replaced the project's whole library tables; `init` crashed
printing a numeric proposal; a re-run rewrote unchanged `.PcbLib` files with a new date, and `--allow` left `plan.json`
in a key order the next run changed. `.PcbLib` / `.SchLib` writes are now atomic.

**Test pack.** `ecadlib testpack --out DIR` builds six parts offline through the same pipeline (librarian with the
`standard` source and the pack's own KiCad library as `--lib`, release check, `dblib-sync`, `kicad-export`,
`kicad-dblib`, `export-parts`): 0603 resistor, SOT-23 NPN, SOIC-8 dual op amp, VQFN-16 with exposed pad, USB-C
receptacle (stacked pads, slots, NPTH) and a 1x05 2.54 mm THT header. `CHECKLIST.md` says what to open in Altium
Designer (the DbLib, with its Excel file at `C:\ecadlib\testpack`), Altium 365 and KiCad 8+, and what each part must
look like; `tests/test_testpack.py` checks those geometry claims against the files, reads the Altium side back and
loads the KiCad side in KiCad. Whether Altium Designer opens the DbLib, previews the parts and releases them is exactly
what the checklist is for: not verified here.

## `eval`: accuracy test set and scorer (`ecadlib/eval/`)

The live benchmark on 110 of these parts (method, results per family, every miss, cost, how to rerun) is in
[docs/benchmark.md](benchmark.md).

How often does the pipeline get a part right, and what does it cost? `ecadlib/eval/sets/kicad-v1.json` holds 331 real
parts with a known answer, taken from KiCad's official libraries (kicad-symbols 9280a93, kicad-footprints f8440fa):
chip passives 36, SOT 40, SOIC 30, TSSOP/MSOP/SSOP 30, QFN/DFN/SON 40, QFP 25, BGA/CSP 25, diodes 25, power SMD
(DPAK, LFPAK ...) 15, THT 30, connectors 35. Each case has the MPN, manufacturer, the package a BOM would name, a
datasheet link (315 of 331), the KiCad footprint it should match, the symbol's pin numbers and names, and `from`:
a KiCad symbol whose name is the MPN (footprint = its Footprint property, manufacturer from the datasheet's domain), a
connector footprint whose name carries the maker's part number (JST, Molex, Wurth), or a curated chip-passive ordering
code (Yageo, Panasonic, Murata, Samsung, KEMET, Lite-On; size code -> KiCad chip footprint). Only numbers are kept from
KiCad's files (pad centres, sizes, drills, courtyard box; pin names), CC-BY-SA 4.0 with the libraries exception.
`ecadlib eval build-set --symbols DIR --footprints DIR` rebuilds it from fresh checkouts (deterministic selection).

```
ecadlib eval run [--limit 30] [--family qfn,bga] [--model M] [--sources standard,easyeda,datasheet] -o out/eval/a
ecadlib eval run --record out/eval/rec -o out/eval/a      # live, and keep every Claude answer
ecadlib eval run --replay out/eval/rec -o out/eval/b      # re-score later (new scorer, new builder) without paying
ecadlib eval compare out/eval/a out/eval/b                # pass rate and $/part per family, cases that flipped
```

`run` writes one BOM of the cases and sends it through `librarian.run` (default sources: the rules' order without
`kicad`, which is the ground truth here). Whatever source answers, its `.kicad_mod` and `.SchLib` are scored
(`ecadlib/eval/score.py`): pad count and numbers; pad 1 (A1) position, which catches mirrored and rotated parts;
pitch (most common nearest-neighbour spacing); every pad's centre/size/drill; courtyard box; symbol pin numbers and
names (80 % must agree after normalising overbars and case). Positions are compared after centring both pad boxes, so a
different origin is reported but does not fail. Tolerances (`--tol pos=0.05 size=0.10 pitch=0.01 courtyard=0.25
pin1=0.25 drill=0.05`) are looser than `footprint-bench`'s because a datasheet's nominal dimensions differ a little
from KiCad's. Thermal vias inside an exposed pad are ignored. The report (`report.md`, `report.json`) gives the pass
rate overall, per family and per answering source, per-field counts, tokens and USD per part (from the message's
usage block when live, cached or replayed; otherwise datasheet's estimate, rough without a key) and wall time. Without
a key and without `--replay` nothing is sent: parts that need Claude are reported unresolved, with an estimate.

Checked here (the live accuracy number is in [docs/benchmark.md](benchmark.md)): KiCad's footprints and symbols score 331/331 against
themselves; the scorer fails a 0.65 mm-pitch QFN built in place of a 0.5 mm one (pitch), a mirrored footprint (pin 1),
a missing exposed pad (pad count). The whole set through the `kicad` source (KiCad libraries served from local
clones; GitLab rate-limited the live crawl): first 295 parts resolved, 246 pass, with 29 connectors without a
symbol by MPN and 20 parts that got another part's footprint because both BOM lines named the same package (`SOIC-14`
for a 3.9 mm and a 5.3 mm body). Fixed (`fp_share.py`, below), and the eval BOM now gives chip passives and diodes
their R/C/L/D designator (a `U` line is not a capacitor to any source): 331 resolved, 302 pass, the 29 connectors
are the only failures. Shared footprints are still flagged (`footprint shared with`); they now have the same pads.
The `standard` source alone (offline, no key): 74 of 331 resolved, all 74 with the right footprint (0 wrong), 57
pass (the others get a footprint but no symbol by design: ICs, and TVS diodes, whose uni/bidirectional pin names the
MPN does not settle); chip 36/36, diode 21 resolved, sot 15. It answers only what the name settles: no JEDEC default
body any more (an earlier version gave 115 answers, 14 of them wrong, from bare `SO-14` / `SOIC-8` / `TDFN-12-1EP`). Estimate mode on the whole set: ~$0.60 per part on Opus 5.5
synchronous, mostly because PDFs that cannot be downloaded here are guessed at 30 pages.

### Accuracy can't silently drop: `eval gate` and `eval golden` (`ecadlib/eval/gate.py`, `golden.py`)

```
python -m ecadlib eval gate                     # whole set, offline, vs ecadlib/eval/baselines/kicad-v1.json (exit 1 on a drop)
python -m ecadlib eval gate --update            # deliberate change: rewrite the baseline, review the diff, commit it
python -m ecadlib eval gate --make-replay       # rebuild the replay pack (after a set or standard-table change)
python -m ecadlib eval golden                   # rebuild testpack / demo / package_specs outputs, compare fingerprints
python -m ecadlib eval golden --update [--group specs]   # deliberate change: rewrite ecadlib/eval/golden/*.txt, commit
```

**Gate** (CI job `accuracy-gate`, Python 3.11 and 3.13): `eval run` on all 331 cases with sources `standard,datasheet`,
no network (every request refused and counted) and no keys, the datasheet step answered from the committed replay pack
`ecadlib/eval/replay/kicad-v1.json.gz` (154 KB, one gzip JSON). The pack is **synthetic**, not a model's reading: the
answer a perfect reader would give, in the strict output schema the reader validates: printed min/nom/max dimensions
for the 154 packages in the standard table (gullwing, no-lead, QFP, chip; the IPC generator builds the land) and the
land-pattern table (`pad_table`) copied from the reference pads for the rest (BGA, connectors, THT, power), with the
case's pins; and the perfect answer to the cross-check's second read (`crosscheck.py`: the same critical numbers and
pins), so `[datasheet] cross_check = "auto"` runs as shipped (82 reads agree with the standard table, 171 pad-table reads
compare pin count and pins only and stay "unchecked", none held). So the gate measures the pipeline (reader checks, spec conversion, generators, pad tables, symbol writer,
librarian, scorer), not a model: the footprint is right when the reader's answer is right. How often ecadlib reads a
real datasheet correctly is the live benchmark, [docs/benchmark.md](benchmark.md). The gate fails when a family's pass rate drops more than `--tol` (default 0.01) below the
baseline or when any case that passes in the baseline fails; new passes are listed (`--update` locks them in).
Committed baseline: 327/331 (bga 25/25, chip 36/36, connector 33/35, diode 25/25, power 15/15, qfn 38/40, qfp 25/25,
soic 30/30, sot 40/40, tht 30/30, tssop 30/30), 20-22 s here. The four failures: footprint_adv's pad-table checks reject
two Neutrik jacks (no pad 1: pads `G`, `S`, `T`; overlapping pads) and two `_ThermalVias` QFNs whose copied reference
lists the exposed pad's pieces twice at one place or over the pin pads (a reader would give the land once). (Two TO
cans failed before `sexpr.courtyard_bbox` counted arc-drawn courtyards; the set's three affected references were
rebuilt.) A real model's accuracy is still `eval run --record` / `--replay` with
a key (not run here: no key).

**Golden snapshots** (`tests/test_evalgate.py`, in the test job): normalised fingerprints of the files we write for the
six testpack parts, the demo BOM and 96 package specs (all `package_specs/*.json`, `advanced/*.json` and every 20th
standard-table entry, written the librarian's way: `convert_footprint` -> `write_pcblib`), in
`ecadlib/eval/golden/{testpack,demo,specs}.txt` (390 KB of text). One line per thing that matters: KiCad footprint
properties and every pad (more than 16 pads: pad 1 in full, the others grouped by shape/size/drill/layers with count,
centre box and hash), graphics per layer as count + bounding box + hash; symbol fields and every pin; Altium pads in mm,
every footprint / body / component parameter and every SchLib pin; DbLib and KiCad database rows. Not in it: uuids,
generator stamps, Altium UNIQUEIDs, GUIDs, MODELID / checksum and the file header's DATE / TIME (Altium files are read
back with `altium_lib`'s reader, so the container's timestamps never enter; a test writes the same footprint at two
`SOURCE_DATE_EPOCH`s: different bytes, same fingerprint). A change fails with a diff such as
`~ changed: spec/SOT-23.kicad_mod` / `-(pad 1 smd roundrect (at -0.9375 -0.95) (size 1.475 0.6) ...` / `+(... (size 1.485 0.6) ...`.

**Updating** after a deliberate change (a better generator, a new exporter field): run the command with `--update`,
read `git diff ecadlib/eval/baselines ecadlib/eval/golden`, and commit it in the same commit as the code that caused it,
saying why in the message. Never update to make an unexplained failure go away. When the set or the standard table
changes, `eval gate --make-replay` first (a test fails while the committed pack is stale).

## Releases

The version lives in one place, `ecadlib/__init__.py` (`__version__`); `pyproject.toml` reads it from there. To cut a release:

1. Bump `__version__` (for example `0.3.0`).
2. In `CHANGELOG.md`, rename `## [Unreleased]` to `## [0.3.0] - YYYY-MM-DD`, add a new empty `## [Unreleased]` above it, and update the compare links at the bottom.
3. Run `python -m tests.run_all`, commit, and merge to `main`.
4. Tag that commit and push the tag: `git tag v0.3.0 && git push origin v0.3.0`.

The `release` workflow (`.github/workflows/release.yml`) then checks that the tag, `__version__` and the changelog agree, runs the tests, builds the sdist and wheel, smoke-tests both in a clean venv, creates the GitHub release with that version's changelog section as its notes, and publishes to PyPI. Every PR also builds and smoke-tests the packages (the `package` job in `tests.yml`).

PyPI publishing uses [trusted publishing](https://docs.pypi.org/trusted-publishers/), so no API token is stored in the repository. The repository owner sets it up once:

* On PyPI, add a trusted publisher (a "pending publisher" before the first upload, under *Your projects > Publishing*): project `ecadlib`, owner `oliBrowne`, repository `PCBLIVINGLIBRARY`, workflow `release.yml`, environment `pypi`.
* On GitHub, create an environment named `pypi` (*Settings > Environments*). Restricting it to `v*` tags and adding a required reviewer is recommended.

If the `ecadlib` name is taken on PyPI, change `name` in `pyproject.toml` and the publisher entry to match.

## Exact commands run (in `/mnt/project-files/ecad-librarian`)

```
python3 -m pip install easyeda2kicad                       # 1.0.1, used only to read its endpoint code
python3 -m ecadlib.footprint_gen gen ecadlib/package_specs/*.json -o out/footprints/ecadlib.pretty
for each of 6 footprints:  python3 -m ecadlib.footprint_gen compare out/footprints/ecadlib.pretty/<fp>.kicad_mod \
      https://gitlab.com/kicad/libraries/kicad-footprints/-/raw/master/<Lib>.pretty/<fp>.kicad_mod   > out/footprints/compare_vs_kicad_official.txt
python3 -m tests.test_offline                               # 11/11 ok (incl. Altium 365 plan/push, Nexar schema, PcbLib/SchLib writer)
python3 -m ecadlib.bom_triage examples/sample_bom.csv --lib examples/kicad_lib --lib out/footprints -o out/triage_sample_bom
python3 -m ecadlib.bom_triage examples/sample_bom.csv --lib examples/kicad_lib --lib out/footprints \
        --providers digikey,mouser,nexar,snapmagic,samacsys -o out/triage_sample_bom_stub_providers_only
python3 -m ecadlib.altium_export examples/sample_bom.csv --triage out/triage_sample_bom.csv -o out/altium
export SOURCE_DATE_EPOCH=1791936000                         # reproducible PcbLib dates
python3 -m ecadlib.altium_lib pcblib ecadlib/package_specs/*.json -o out/altium_models/PcbLib
python3 -m ecadlib.altium_lib schlib examples/symbols/*.json -o out/altium_models/SchLib
python3 -m ecadlib.altium365 plan examples/sample_bom.csv --triage out/triage_sample_bom.csv --models out/altium_models -o out/altium365/plan.json
python3 -m ecadlib.altium365 push out/altium365/plan.json --workspace https://example.365.altium.com --dry-run -o out/altium365/push_dry_run.json
```

The HTTP cache (`out/http_cache/`) and `__pycache__` were deleted after the runs. They are recreated on the next run.

## Next steps

* Rerun triage from a network that can reach easyeda.com and jlcpcb.com, and confirm the EasyEDA JSON paths against real payloads.
* Load the generated footprints in KiCad and run `kicad-cli fp upgrade` plus DRC.
* Add dual-flat no-lead (DFN/SON) and QFP/SOT families.
* Open `out/altium_models/*` in Altium Designer (and run its library validation) before the first real release. Then release one part to a test Altium 365 workspace.
* Verify the CH340C and TPS62133RGTR pinouts against their datasheets (both match KiCad's official symbols by pin number).
* Run the datasheet reader with a real API key on a known part (TPS62133RGTR) and compare with the hand-written spec.
* Pick symbols for TYPE-C-31-M-12 and X322512MSB4SI and a footprint for FTSH-105, then rerun the librarian.
* Run the lifecycle watcher and part lookup with real Nexar/DigiKey/Mouser credentials and confirm the response shapes (lifecycle fields, spec attribute names).
* Add an `easyeda2kicad` conversion step for the `available_easyeda` bucket.
