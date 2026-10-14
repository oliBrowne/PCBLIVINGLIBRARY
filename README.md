# ecad-librarian: "AI living library" prototypes

These are small Python prototypes for an open-source tool that manages ECAD components for KiCad and Altium. The design has three parts:

1. An LLM reads a datasheet mechanical drawing and fills `ecadlib/package_schema.json`.
2. A deterministic generator turns that JSON into an IPC-7351B land pattern.
3. A triage step works out which BOM lines already exist locally, which can be pulled from a public source, and which need a generated footprint.

## Quick start

```
ecadlib init --from path/to/your/libraries      # reads SchLib/PcbLib/DbLib/KiCad/BOMs, writes ecadlib.toml
ecadlib librarian my_bom.csv                     # or: ecadlib ui  -- every tool now follows ecadlib.toml
```

`init` works out your naming, parameters, folders, layers and density from the libraries you already have, and asks only where the evidence is split (`--yes` takes the best guess). See section 18.

## How you use it

Install it with pip (Python 3.10 or later):

```
pip install ecadlib                 # once a release is on PyPI (see Releases below)
pip install "ecadlib[pdf,altium]"   # pypdf trims datasheets to --pages; olefile reads .PcbLib/.SchLib back
pip install -e ".[dev]"             # from a checkout: editable, plus everything the test suite uses
```

`ecadlib --version` prints the installed version. Changes are listed in [CHANGELOG.md](CHANGELOG.md).

That gives you a single `ecadlib` command (`python -m ecadlib` works too). There are three ways in, and all of them call the same actions in `ecadlib/app.py`:

1. **Local web UI** (for most people). It runs on your own machine, so your API keys and libraries stay local.

   ```
   ecadlib ui --lib path/to/your/kicad/libs
   ```

   A browser tab opens at `http://127.0.0.1:8765`.
   * **Check a BOM:** drop in a CSV or XLSX, whether it's an Altium BOM, an Altium ActiveBOM, a KiCad BOM or a hand-made spreadsheet. A progress bar runs while parts are looked up. You then get a table of what is already in your library, what a part source can supply, what needs a footprint, and what is DNP. You can filter it by status or by search.
   * **Fill the library:** one button runs the librarian on the BOM. It finds each missing symbol and footprint (official KiCad libraries, then EasyEDA, then the datasheet reader), writes the Altium files, and runs the release check. Each part shows where its symbol and footprint came from and what still needs a person. Parts that fail the check are held back from Altium 365; **Release anyway…** overrides that after you have looked (it is recorded in the plan), and **Hold again** takes it back.

     ![Fill the library](docs/ui-librarian.png)
     * **Pick…** on a held part lists ranked symbol or footprint candidates (official KiCad library, your `--lib` libraries, ecadlib's package specs) with why each ranked, its pin/pad count, whether it matches the part's other half and a suggested pin map for a near miss. **Choose** converts it, records the choice in the plan (a librarian re-run keeps it) and re-runs the release check.

       ![Pick a candidate](docs/ui-resolve.png)
     * **Sign-off:** type your name, then **Approve** or **Reject…** a part; **Approval status** shows the policy and who has signed. There is no login (it is a local tool), so the name you type is recorded as given, in the plan and in `approvals_audit.jsonl`. With an `approvals.json` next to the plan (section 12), parts wait as *awaiting approval* and are not pushed until signed off.
     * **Find duplicates** lists parts with the same MPN, footprints with the same geometry and symbols with the same pinout across the plan and the workspace's `altium_models/` and `footprints/`; **Merge…** points the plan at the keepers (library files are never changed).

       ![Sign-off and duplicates](docs/ui-signoff.png)
   * **Make a footprint:** click **Generate…** on a missing part. It picks a matching example package when one exists. Enter the datasheet dimensions, preview the IPC-7351B land pattern, and approve it. Approving writes a KiCad `.kicad_mod` and an Altium `.PcbLib`, and asks before replacing an existing footprint. The BOM is then re-checked automatically.
   * **Send to Altium:** there are three options.
     * **Export DbLib** writes the SQLite/XLSX/.DbLib set.
     * **Build .PcbLib / .SchLib** converts the BOM's footprints and writes generic R/C symbols.
     * **Altium 365** plans a release with a ready / needs-symbol / needs-footprint status per part. **Dry run** shows the exact requests without sending anything. **Push** is enabled only when `NEXAR_TOKEN` is set and a workspace is picked, and it asks for confirmation first.

   ![Triage table](docs/ui-triage.png)
   ![Footprint preview](docs/ui-footprint.png)
   ![Send to Altium](docs/ui-altium.png)

2. **MCP server**, so Claude or another agent can do the same work in conversation (for example, "check this BOM and draft footprints for anything missing").

   ```
   claude mcp add ecadlib -- ecadlib mcp --lib /path/to/libs --workspace /path/to/workspace
   ```

   Its tools are `workspace_info`, `triage_bom` (with `summary_only` for large BOMs), `preview_footprint`, `approve_footprint`, `librarian_run` (fill every missing part, then the release check), `release_check` (with `allow` for parts a person has signed off), `held_parts`, `part_candidates` and `choose_part` (fix a held part), `approval_status` and `approve_part` (sign-off; `user` is required, the agent must pass the person's own name), `find_duplicates` (with `merge`), `build_altium_models`, `export_altium_dblib`, `altium365_plan`, `altium365_push` (dry run unless `dry_run` is false; held parts are never pushed), `sim_models` (SPICE/IBIS models; `attach` writes them) and `list_example_specs`.
3. **CLI**, for scripts and CI: `ecadlib triage`, `ecadlib footprint`, `ecadlib check`, `ecadlib dblib`, `ecadlib dblib-sync`, `ecadlib altium-lib`, `ecadlib altium365`, `ecadlib kicad-export` (the library as KiCad files, section 11), `ecadlib resolve`, `ecadlib approve`, `ecadlib dedupe`, `ecadlib sourcing`, `ecadlib step-gen`, `ecadlib upkeep` (section 15), `ecadlib a365`, `ecadlib project`, `ecadlib where-used` and `ecadlib sim-models` (section 17), `ecadlib init` and `ecadlib rules` (section 18). Run `ecadlib <command> -h` for options. Each is described below; the older `python3 -m ecadlib.<module>` form still works.

The UI and MCP server write everything to one workspace folder (default `./ecadlib_workspace`). It has these subfolders:
* `uploads/`
* `reports/`
* `footprints/ecadlib.pretty/`: approved footprints plus their source JSON
* `altium_models/PcbLib|SchLib/`
* `altium/`
* `altium365/`
* `http_cache/`

Both servers use only the standard library on top of the package requirements. The web UI binds to 127.0.0.1. It refuses requests with an unexpected Host header (DNS rebinding), cross-origin POSTs, and any file path outside the workspace.

Tests: `python3 -m tests.test_ui` covers the workspace, web server, MCP server and CLI. `python3 -m tests.test_offline` covers the core modules. `python3 -m tests.test_footprints` checks generated footprints by part type against KiCad's library and the KLC rules.

Requirements: Python 3.10 or later (tested on 3.11 and 3.13) with `requests`, `pandas`, `openpyxl` and `jsonschema`, which pip installs. `pypdf` and `olefile` are optional extras. `easyeda2kicad` was installed only so its source could be read for the endpoints; nothing imports it at runtime.

```
ecad-librarian/
├── README.md
├── ecadlib/
│   ├── footprint_gen.py      # IPC-7351B generator -> .kicad_mod  (+ `compare` vs a reference footprint)
│   ├── package_schema.json   # JSON Schema: the datasheet-dimension contract an LLM fills
│   ├── bom_triage.py         # BOM CSV/XLSX -> triage report (.md + .csv)
│   ├── providers.py          # Provider interface: EasyEDA, JLCPCB search, + DigiKey/Mouser/Nexar/SnapMagic/SamacSys stubs
│   ├── kicad_index.py        # scans .kicad_sym / *.pretty for MPN / LCSC / value+package matches
│   ├── altium_export.py      # Altium DbLib: parts.sqlite + parts.xlsx + two .DbLib files
│   ├── app.py                # Workspace: the shared actions behind the UI, MCP server and CLI
│   ├── ui/                   # local web UI (python -m ecadlib.ui), one static page, stdlib server
│   ├── mcp_server.py         # MCP server over stdio (python -m ecadlib.mcp_server)
│   ├── altium365.py          # Altium 365 workspace: release plan + push via the Nexar Design API
│   ├── a365_sync.py          # two-way Altium 365 sync: pull workspace components into the plan, status, reconcile
│   ├── altium_dblib_sync.py  # Altium without 365: the same plan -> .DbLib / .SVNDbLib library folder
│   ├── altium_lib.py         # pure-Python .PcbLib/.SchLib writer (+ minimal reader, `dump`)
│   ├── kicad_export.py       # librarian plan -> .kicad_sym + .pretty + .3dshapes + lib tables
│   ├── cfb.py                # Compound File Binary (OLE2) v3 writer used by altium_lib
│   ├── assets/pcblib_template.json.gz   # Library header + FileVersionInfo from an Altium-saved file (see NOTICE)
│   ├── httpcache.py          # disk cache + per-host rate limit around requests
│   ├── sourcing.py           # price/stock per BOM line, cost rollup, second sources and drop-in passives
│   ├── upkeep.py             # weekly lifecycle/upstream/price/A365 checks, only new changes, one GitHub issue or PR
│   ├── sim_models.py         # SPICE / IBIS models: find, parse, pin-map check, attach (SchLib SIM link, KiCad Sim.*)
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

---

## 1. `footprint_gen.py`: IPC-7351B footprint generator

```
ecadlib footprint gen ecadlib/package_specs/*.json -o out/footprints/ecadlib.pretty [--density A|B|C]
ecadlib footprint compare <mine.kicad_mod> <reference path or URL>
ecadlib check <any.kicad_mod> ...          # KLC-style rule check, works on KiCad's own footprints too
```

* **Package types** (`package_type`, with `lead_type` to pick a different IPC lead family):
  * `chip`: 2-terminal R/C/L/LED. IPC table 3-5 for 0603 and up, table 3-6 below 0603. `inner_span` takes the gap directly when the drawing gives it (LEDs).
  * `molded`: inward-L / molded body: tantalum, SMA/SMB/SMC, SOD-123F (table 3-13). `lead_type: "melf"` for MELF.
  * `gullwing`: SOIC/SOP/TSSOP/MSOP, plus SOT-23/SOD-123 via `pin_positions` and SOT-223/DPAK via `tab`.
  * `qfp`: 4-sided gull-wing. `qfn`: QFN/DFN/SON with an optional exposed pad (`pins_top_bottom: 0` for DFN).
  * `polarized: true` adds KiCad's pin-1 silk bracket and fab chamfer (LED/diode: pin 1 = cathode; tantalum: pin 1 = +).
  * `land`: the maker's recommended land pattern, when the datasheet has one. It replaces the IPC pads.
* **Numbers** follow what KiCad builds its own library with (fillet tables from KiCad's `ipc_7351b.yaml`; see [docs/footprint-rules.md](docs/footprint-rules.md)):
  `Z = Lmin + 2Jt + √(CL²+F²+P²)`, `G = Smax − 2Jh − √(CS²+F²+P²)`, `X = Wmin + 2Js + √(CW²+F²+P²)`,
  RMS tolerance for S, F = 0.10, P = 0.05, rounded to 0.05 mm (0.02 for chips under 0603).
  Opposite gull-wing rows stay ≥ 0.4 mm apart, and pad heels are pulled back to keep 0.2 mm from an exposed pad.
* **Output** (KLC F5/F6): silk 0.12 cut back 0.2 mm from the real rounded pad outline (0.1 mm on tiny parts),
  fab 0.10 with a pin-1 chamfer, courtyard 0.05 on the 0.01 mm grid with 0.25 mm excess (0.15 under 0603),
  roundrect pads, exposed-pad paste split into windows at 65 %. Format `version 20221018` (KiCad 7+; Altium imports it).
* **Checks:** every generated footprint is run through `footprint_check` (copper gaps, silk clearance, fab,
  courtyard, pin 1, paste). Spec mistakes, such as lead span and body width swapped, mils instead of mm,
  or more pins than fit, are refused with a plain-language reason.

### Compared with KiCad's official footprints

`tests/test_footprints.py` generates 26 example parts and compares pad 1 and the courtyard with KiCad's library:

| Kind | Parts | Result |
|---|---|---|
| Resistors | R_0402, R_0603, R_0805, R_1206 | identical pads, courtyard and silk |
| Capacitors | C_0805 | identical; C_0402 within 0.03 mm (typical body sizes, not KiCad's) |
| LEDs | LED_0603, LED_0805 | identical, including the cathode bracket |
| Inductor | L_0805 | within 0.3 mm (KiCad uses a different body size for inductors) |
| ICs | SOT-23, SOT-23-5, SOIC-8, SOIC-16, TSSOP-16, LQFP-48, QFN-16, QFN-32 | identical |
| ICs (typical inputs) | MSOP-8, DFN-8, SOT-223 | within 0.15 / 0.3 / 0.6 mm |
| Diodes, tantalum | SMA, SMB, SOD-123, SOD-323, tantalum A | IPC-7351B result; KiCad draws these by hand from the maker's land, so give `land` to match it exactly |

All 26 pass the rule check, as do all 27 KiCad reference footprints.

---|---|---|---|---|---|
| R_0603_1608Metric | 0.825×0.91 @0.7925 | 0.80×0.95 @0.825 | +0.025 / −0.04 | −0.033 | 2.92×1.42 / 2.96×1.46 |
| C_0402_1005Metric | 0.465×0.57 @0.4025 | 0.56×0.62 @0.48 | −0.095 / −0.05 | −0.078 | 1.58×0.88 / 1.82×0.92 |
| R_0805_2012Metric | 0.96×1.36 @0.925 | 1.025×1.40 @0.9125 | −0.065 / −0.04 | +0.013 | 3.32×1.86 / 3.36×1.90 |
| R_1206_3216Metric | 1.09×1.71 @1.46 | 1.125×1.75 @1.4625 | −0.035 / −0.04 | −0.003 | 4.52×2.22 / 4.56×2.26 |
| **SOIC-8_3.9x4.9mm_P1.27mm** | 1.965×0.58 @2.4725 | 1.95×0.60 @2.475 | **+0.015 / −0.02** | −0.003 | 7.42×5.40 / 7.40×5.40 |
| **QFN-32-1EP_5x5mm_P0.5mm** | 0.845×0.24 @2.4325 | 0.875×0.25 @2.4375 | −0.03 / −0.01 | −0.005 | 6.22×6.22 / 6.26×6.26 |

SOIC-8 (JEDEC MS-012AA inputs) and QFN-32 come out within 0.03 mm. The fab outline with its pin-1 chamfer matches KiCad's coordinates exactly for SOIC-8 and QFN-32. The QFN pin-1 triangle is identical to KiCad's, and the SOIC one is 0.09 mm higher. The 0402 is the outlier: KiCad deliberately uses larger pads than IPC table 3-6 for parts smaller than 0603. That gap is a policy choice, so it could become an option. Density A gives an 0603 pad of 1.025×1.01 and density C gives 0.625×0.81.

---

## 2. `bom_triage.py`: BOM triage

```
python3 -m ecadlib.bom_triage examples/sample_bom.csv --lib examples/kicad_lib --lib out/footprints -o out/triage_sample_bom
python3 -m ecadlib.bom_triage examples/sample_bom.csv --lib examples/kicad_lib --lib out/footprints \
        --providers digikey,mouser,nexar,snapmagic,samacsys -o out/triage_sample_bom_stub_providers_only
python3 -m tests.test_offline
```

* **Headers:** headers are matched fuzzily and case-insensitively, using synonyms, word containment and difflib, with guards so "LCSC Part Number" is not taken as the MPN and "Mfr Part #" is not taken as the manufacturer. The header row is found automatically within the first 10 rows, so XLSX files with a title row work; this was tested with an XLSX that had renamed headers (`RefDes`, `MANUFACTURER PART NUMBER`, `lcsc#`). Malformed LCSC numbers are flagged.
* **Local library:**
  * `.kicad_sym` symbols are matched by MPN (symbol name or an MPN-like property), by `LCSC` property, or by value plus package for passives (`0.1uF` equals `100nF`, and `4k7` is understood).
  * The symbol's `Footprint` must resolve inside one of the indexed `*.pretty` folders.
  * A generated footprint counts when the package matches and the designator class agrees, so an LED 0603 is not given `R_0603`.
* **Statuses:**
  * `in_library`
  * `in_library (generated footprint)`
  * `available_easyeda` (symbol and footprint, with 3D reported separately)
  * `easyeda_footprint_only`
  * `needs_footprint_generation`
  * `unverified (source unreachable)`, which is kept separate on purpose so a network failure is never reported as "needs generation"
* **Providers** (`providers.py`, interface `lookup(line) -> SourceResult`, raising `NotConfigured`):

  | provider | auth needed | state in prototype |
  |---|---|---|
  | `jlcpcb_search` | none: anonymous JSON POST to `jlcpcb.com/api/overseas-pcb-order/v1/shoppingCart/smtGood/selectSmtComponentList` (taken from easyeda2kicad 1.0.1 `search_jlcpcb_components`; its docstring says "Works anonymously") | implemented; MPN → LCSC by exact normalised match |
  | `easyeda` | none: `GET https://easyeda.com/api/products/{LCSC}/components` with `Referer: https://easyeda.com/` | implemented; parses `result.dataStr.head.c_para`, `packageDetail.dataStr.shape` and `SVGNODE~{…c_etype: outline3D}` for 3D, following easyeda2kicad's importer |
  | `digikey` | OAuth2 client credentials (`DIGIKEY_CLIENT_ID/SECRET`), Product Information API v4 | stub |
  | `mouser` | `MOUSER_API_KEY` (Search API) | stub |
  | `nexar` | OAuth2 client credentials (`NEXAR_CLIENT_ID/SECRET`), GraphQL, quota-limited | stub |
  | `snapmagic` | partner token only, with no self-serve public API | stub |
  | `samacsys` | account plus Library Loader or partner access, with no documented public REST API | stub |

  * easyeda2kicad 1.0.1 calls `/components` with no `?version=`. The `?version=6.4.19.5` suffix comes from older releases; I kept it, and it is untested.
  * easyeda2kicad 1.0.1 also knows `pro.easyeda.com/api/components/searchByNumbers` (LCSC → v2 UUID, unauthenticated). Its other v2 endpoints are marked "requires auth" in its source.
  * I found no public, unauthenticated LCSC search endpoint. The `wmsc.lcsc.com` endpoints are undocumented and were also blocked here.
* **HTTP:** `httpcache.py` caches 2xx and 404 responses on disk under `out/http_cache/`, keyed by a sha256 of method, URL and body, with a 7-day TTL. It allows at most one request per second per host (`--rate`), supports `--offline`, and returns network errors as data rather than exceptions. A cache filled on a machine with open egress can be copied over and replayed with `--offline`.

### What happened live (2026-10-13, from this sandbox)

* **Blocked:** `easyeda.com`, `jlcpcb.com`, `pro.easyeda.com`, `modules.easyeda.com`, `lcsc.com`, `wmsc.lcsc.com`, `api.digikey.com`, `api.mouser.com`, `api.nexar.com` and `altium.com`. Every one was refused by the sandbox's egress proxy with a CONNECT 403 before any request reached the vendor. This is an environment policy, not a vendor response, so **the EasyEDA and JLC providers were not exercised against real responses here.** Their parsers are tested only with synthetic payloads shaped after easyeda2kicad's field paths (`tests/test_offline.py`).
* **Reachable:** `gitlab.com` (raw files and API), `pypi.org` and `raw.githubusercontent.com`. KiCad reference footprints and two public `.DbLib` examples were fetched from these.
* **Live triage result** (`out/triage_sample_bom.md`): 4 `in_library` (10k 0603, 100nF 0402, TPS62133 → generated VQFN-16, CH340C → generated SOIC-16) and 10 `unverified (source unreachable)`. AMS1117-3.3 matched a local symbol, but its footprint `Package_TO_SOT_SMD:SOT-223-3_TabPin2` is a KiCad stock-library footprint and was not indexed. Pass the KiCad system footprint directory as another `--lib` to resolve it.
* **Stub-only run:** with only the stub providers, nothing errors, so the same 10 lines land in `needs_footprint_generation`.
* **LCSC numbers in `sample_bom.csv`:** these were checked against each `lcsc.com/product-detail/Cxxxx.html` page using the assistant's web-fetch tool, outside the sandbox proxy. They were *not* checked through the EasyEDA endpoint, which was blocked.

  | LCSC | MPN on page | package |
  |---|---|---|
  | C8734 | STM32F103C8T6 | LQFP-48 |
  | C6186 | AMS1117-3.3 | SOT-223 |
  | C25804 | 0603WAF1002T5E | 0603 |
  | C1525 | CL05B104KO5NNNC | 0402 |
  | C165948 | TYPE-C-31-M-12 | |
  | C2913202 | **ESP32-S3-WROOM-1-N16R8** | |
  | C73973 | TPS62133RGTR | VQFN-16 3×3 |
  | C84681 | CH340C | SOP-16 |
  | C9002 | X322512MSB4SI | 3225 |
  | C15127 | AO3401A | SOT-23 |
  | C2286 | KT-0603R | LED 0603 |
  | C160404 | SM04B-SRSS-TB(LF)(SN) | |

  * C2913202 is the N16R8 module, not N8R8, which I had guessed first.
  * J3 (Samtec FTSH-105-01-L-DV-K) deliberately has no LCSC number.
  * U6 (TPS62162DSGR) has its LCSC number omitted to exercise MPN search. A search result suggests it is C40256, which I did not verify.
  * The lcsc.com pages for C8734 and C6186 show an "EasyEDA Library" section with symbol and footprint diagrams.

---

## 3. `altium_export.py`: Altium Database Library

```
python3 -m ecadlib.altium_export examples/sample_bom.csv --triage out/triage_sample_bom.csv -o out/altium [--win-dir C:\ecadlib\altium]
```

This writes one table per category (Capacitors, Connectors, Crystals, Diodes, ICs, Resistors, Transistors) to `parts.sqlite`, and the same tables as sheets to `parts.xlsx`. It also writes two `.DbLib` files.

* **Columns:** `Part Number` (key), `Library Ref`, `Library Path`, `Footprint Ref`, `Footprint Path`, `Description`, `Value`, `Comment`, `Manufacturer`, `Manufacturer Part Number`, `Supplier 1`, `Supplier Part Number 1`, `Supplier 2`, `Supplier Part Number 2`, `ComponentLink1Description/URL` (LCSC page) and `ComponentLink2Description/URL` (datasheet).
* **Generic symbols:** passives use a generic symbol (`Resistor`, `Capacitor`) with `Comment = "=Value"`, and their Part Number is synthesised (`RES-0603-10k`).
* **Footprints:** footprint refs reuse the KiCad footprint names, so `Footprint Path` points at a `PcbLib\ecadlib.PcbLib`. That file has to be produced by importing `out/footprints/ecadlib.pretty`, as described below.

**Verified**, from Altium's online docs and two real `.DbLib` files fetched from GitHub (`ximtech/altium-library`, a PostgreSQL/ODBC library, and `ttrinidad/altium-library`, the Celestial MSSQL/UDL library):
* A `.DbLib` is an INI file. It has an `[OutputDatabaseLinkFile] Version=1.1` section, and a `[DatabaseLinks]` section holding `ConnectionString=`, `AddMode=3`, `RemoveMode=1`, `UpdateMode=2`, `LeftQuote`/`RightQuote`, `QuoteTableNames`, `UseTableSchemaName`, `DefaultColumnType=VARCHAR(255)`, `SchemaName` and `LastFocusedTable`. The quote characters are `"` for Postgres and `[`/`]` for MSSQL.
* Each table gets a `[TableN]` section with `TableName`, `Enabled=True`, `UserWhere=0` and `UserWhereText=`.
* Field mappings are written as `[FieldMapN] Options=FieldName=T.C|TableNameOnly=T|FieldNameOnly=C|FieldType=…|ParameterName=…|VisibleOnAdd=…|AddMode=0|RemoveMode=0|UpdateMode=0`. Reserved names are bracketed (`[Library Ref]`, `[Footprint Ref]`, …), and the key column `Part Number` had `FieldType=0` while every other column had `FieldType=1`.
* The connection is OLE DB, either as a connection string or `FILE NAME=…\x.udl`. ODBC sources go through `Provider=MSDASQL.1`. Excel is supported, and each sheet becomes a table with a `$` suffix.
* Reserved fields include `Library Ref`, `Library Path`, `Footprint Ref`, `Footprint Path`, `Footprint Ref n`/`Footprint Path n`, `Description`, `PCB3D Ref/Path` and `Sim *`. `Library Ref` must be mapped. `Supplier n`, `Supplier Part Number n` and `ComponentLinkNURL/Description` columns appear in the real files.

**Assumed (not tested, since there is no Windows or Altium here):**
* That `FieldType=0` means "key". It was observed, not documented.
* The SQLite connection string, which relies on Christian Werner's *SQLite3 ODBC Driver* being installed. Altium's docs do not mention SQLite.
* The ACE OLE DB Excel string, which needs the *Microsoft Access Database Engine* redistributable with matching bitness (Altium is 64-bit).
* That unlisted columns auto-map by name. I also wrote explicit FieldMaps for every column.
* The absolute `C:\ecadlib\altium\…` path: set it with `--win-dir`, or edit it in Altium's DbLib editor ("Connect").
* An Access `.mdb` file is Altium's most-trodden path, but it cannot be written portably from Python, so it was skipped.

### Getting footprints and symbols into Altium (options researched before section 5 was built)

* **Altium's own KiCad importer** (Import Wizard): the docs list `*.kicad_mod`, `*.kicad_sym`, `*.lib`, `*.kicad_pcb`, `*.kicad_sch` and `*.kicad_pro` "up to KiCad version 8.x". This is the least-effort route. It is also why the generator emits the KiCad 7 file format rather than KiCad 9/10.
* **Altium scripting** (DelphiScript/JScript/VBScript inside Altium, using `PCBServer.CreatePCBLibComp`, `IPCB_Pad`, …): this can build PcbLib footprints directly from the same JSON. Altium also ships an **IPC Compliant Footprint Wizard / Batch Generator** that takes package dimensions from a spreadsheet. I recall that tool from memory and did not verify it here; mapping `package_schema.json` onto its template could be the cleanest Altium-native route.
* **Python libraries** (versions from PyPI on 2026-10-13):
  * **`altium-monkey`** 2026.9.22 (wavenumber-eng, **AGPL-3.0**, alpha, CPython 3.12–3.14, manylinux wheels): its README claims reading *and writing* of `.SchLib` and `.PcbLib` ("create schematic symbols and PCB footprints"). This is the only Python option found with write support, but the AGPL licence matters for an open-source tool's licensing.
  * **`pyaltiumlib`** 0.7.1 (ChrisHoyer): read-only. It extracts metadata and renders to SVG.
  * **`pyaltium`** 0.4.0 (pluots, 2022): read-only and stale.
  * **`olefile`** (BSD): generic OLE2 read/write, useful as a base for a writer.
  * **AltiumSharp** (C#, github.com/issus/AltiumSharp): its README support table says SchLib and PcbLib are **Write: Yes**. The licence is stated inconsistently on the page (MIT in the README, Apache-2.0 in the sidebar).

## 4. `altium365.py`: Altium 365 workspace exporter (Nexar Design API)

```
python3 -m ecadlib.altium365 plan examples/sample_bom.csv --triage out/triage_sample_bom.csv \
        --models <dir with SchLib/<ref>.SchLib and PcbLib/<ref>.PcbLib> -o out/altium365/plan.json
NEXAR_TOKEN=... python3 -m ecadlib.altium365 workspaces
NEXAR_TOKEN=... python3 -m ecadlib.altium365 push out/altium365/plan.json --workspace <url> [--dry-run]
```

`plan` turns each unique part into one managed workspace component under `Components/<Category>`, with the same parameters as the DbLib rows (manufacturer, MPN, value, supplier, links). `push` then, for each part:

1. looks the component up by name (`desLibrary.components(where: {name: {eq}})`);
2. if it exists, updates its parameters with `desUpdateComponentRevisionParameters` (this is the "keep the library current" path);
3. otherwise uploads the `.SchLib` and `.PcbLib` to `files.nexar.com/File/Upload` and calls `desReleaseComponent`, which releases symbol, footprint and component in one call, using the workspace's default naming schemes and lifecycles for each content type.

Parts without Altium model files are planned as `needs_altium_models` and skipped on release (they still get parameter updates if they already exist in the workspace). `--dry-run` sends nothing and writes the mutation inputs it would send.

**Verified** (from Nexar's support articles and the schema and code in `NexarDeveloper/nexar-release-component-demo-wpf`, last commit 2024-07-23):
* `desReleaseComponent(input: DesReleaseComponentInput!)` takes component name, comment, description, parameters, folder, naming scheme and lifecycle ids, plus `symbolFiles` and `footprintFiles` as `{fileId, relativePath}`. The demo uploads real `.SchLib`/`.PcbLib` binaries from a `Released\` folder, so **release needs Altium-format files; KiCad files cannot go up directly**.
* Uploads are `multipart/form-data` POSTs to `https://files.nexar.com/File/Upload` with the access token in a `token` header; the response body is the file id, valid 24 hours.
* Design queries need a user token with `openid`, `user.access`, `design.domain` scopes (the token the Nexar portal generates has them).
* `desUpdateComponentRevisionParameters` adds or edits parameters; `replaceExisting: true` replaces all of them. The component id changes after each update.
* The release payload exposes only `errors`, not the new component id. Part choices (manufacturer/supplier links) and 3D models are not inputs; 3D models travel inside the `.PcbLib`.

**Tested offline** (2026-10-13):
* All five GraphQL operations and a release input built by `plan` validate against Nexar's published schema (`tests/nexar/schema.graphql`, MIT, from the demo repo). Run with `pip install graphql-core`.
* End to end against a local mock of api.nexar.com and files.nexar.com that executes requests against that schema: `workspaces` listed the mock workspace; `push` uploaded two real Altium files (the demo repo's sample `.SchLib`/`.PcbLib`, byte counts intact), released CH340C with them, updated parameters on an existing `RES-0603-10k`, and skipped the 12 parts without models; a wrong token exits with a clear 401 message.

**Not tested live** (this sandbox's proxy blocks api.nexar.com and files.nexar.com, and there is no workspace or token here):
* Whether `componentReleaseFolder` takes a path like `Components/ICs` or only a single folder name.
* Which Nexar or Altium 365 plan is needed for Design API write access.
* Altium also has a newer native Altium 365 GraphQL API at `https://{workspace}/api/graphql` with its own files service. Its library mutations aren't documented publicly yet; this exporter targets Nexar.

**Getting the `.SchLib`/`.PcbLib` files:** `altium_lib.py` (section 5) now writes them. With `--models out/altium_models` the plan has **4 of 14 parts `ready`** (CAP-0402-100nF, TPS62133RGTR, CH340C, RES-0603-10k) and the dry-run push releases those 4 and skips 10. Those files have not been opened in Altium Designer, so the first real release is also their first real test. The fallback is still Altium's KiCad importer on `out/footprints/ecadlib.pretty`.

### Two-way sync: `a365_sync.py` (manage the library you already have)

```
NEXAR_TOKEN=... ecadlib a365 pull      out/librarian/<bom>/plan.json --workspace <url> [--folder Components] [--no-models]
NEXAR_TOKEN=... ecadlib a365 status    PLAN --workspace <url>            # read-only; exit 1 when anything differs
NEXAR_TOKEN=... ecadlib a365 reconcile PLAN --workspace <url> [--prefer workspace|local]
```

`pull` lists every component in the workspace (`desLibrary.components`, paged) with its folder, comment, description, parameters, revision id, lifecycle state and the names and GUIDs of its symbols and footprints. Components the plan lacks are added with status `existing` and `source.component = "a365"`. Their `.SchLib`/`.PcbLib` are downloaded from each symbol's and footprint's `dataDownloadUrl` into `<plan dir>/a365_models/` and read back with `altium_lib`'s readers. A raw OLE file and a zip that holds one are both accepted; anything else is recorded in `a365.model_errors`. Every item keeps the snapshot it was pulled from (`a365.base`, plus revision, id and GUIDs). `<plan dir>/a365_state.json` holds the same data for all components, in the format `upstream check-a365 --state` and `dedupe` already read.

On the next `pull`, `status` or `reconcile`, each field is compared three ways (base, workspace, plan): parameters one at a time, then comment, description, folder, symbol and footprint names, and model GUIDs. Each component comes out as `in_sync`, `changed_in_workspace` (someone edited it in Altium), `changed_locally`, `merged` (both sides changed different fields; both are kept), `conflict` (both sides changed the same field), `deleted_in_workspace`, `deleted_locally`, `new_in_workspace`, `new_local` or `linked` (a BOM part whose name is already in the workspace). `pull` brings in workspace edits, keeps local ones and leaves conflicts alone. `reconcile` also settles conflicts: by default the workspace wins and the plan's value is kept in `a365.overwritten`; with `--prefer local` the plan's value wins and the next push sends it. Report: `<plan dir>/a365_sync.md` and `.csv`. A backup of the previous plan goes to `plan.json.pre-a365`.

**Push.** An item with an `a365` block is updated, never released again. It is found by name, then by its node id. Its parameters go up with `replaceExisting: true`, merged over the workspace's current set, and parameters removed locally are cleared. If the workspace revision moved since the pull and the parameters there differ from the snapshot, push skips the item and says to run `reconcile`, so an edit made in Altium is never overwritten without notice. After an update the item's revision is written back to the plan. Nexar has no mutation for comment, description, folder or models, so local changes to those are reported as `not pushable`. Pushing new parts works as before.

**Elsewhere.** Pulled items pass `release-check` unchanged, because they are already released. `dedupe` treats them as library entries: they rank as keepers and are never rewritten. If a BOM part matches a pulled item by name or MPN, the `librarian` reuses that item and fetches nothing for it; pulled items the BOM does not use stay in the plan. The shared Workspace class (`app.py`) has `a365_pull` and `a365_status` methods, and the MCP server exposes them as tools. The web UI has no buttons for them yet. With a BOM, these read and write that BOM's librarian plan; without one, `altium365/workspace_plan.json`. If an `ecadlib.toml` rules file is present, its house name for the MPN parameter and its folder map are used (`--rules`).

**Schema fields used** (validated against `tests/nexar/schema.graphql` with graphql-core): `DesComponent{id name comment description isManaged createdAt modifiedAt folder{path} revision{id details{lifeCycleState{name} createdAt modifiedAt}} details{parameters{name value} symbols{name guid dataDownloadUrl folder{path}} footprints{...}}}`, `components(first, after){totalCount pageInfo{hasNextPage endCursor}}`, and `desComponentById`. The schema has no component GUID and no revision label or number. It has only `revision.id`, which, like the component id, changes with each revision, so drift is detected from content plus revision id. Model GUIDs come from the symbols and footprints.

**Not tested live.** api.nexar.com is blocked from this sandbox. Everything above runs against a stateful mock (`tests/test_a365_sync.py`) that validates every query against the schema. Still unknown: what `dataDownloadUrl` actually returns (a raw `.SchLib`/`.PcbLib`, a zip, or something else), and whether it needs the bearer token (the client tries without it first, then with it). Also unknown: whether `details` is too slow at 50 components per page on a large workspace (`PAGE` in the module).

### Altium without 365: `altium_dblib_sync.py` (`.DbLib` / `.SVNDbLib`)

```
ecadlib dblib-sync out/librarian/plan.json --to D:/libs/ecadlib [--svn [--svn-url URL] [--commit]] [--prune] [--win-dir D:\libs\ecadlib] [--dry-run]
```

For teams that keep a Database Library or an SVN Database Library instead of an Altium 365 workspace. It takes the same `plan.json` that `altium365 push` uses and keeps one library folder in step with it: `SchLib/<Library Ref>.SchLib` and `PcbLib/<Footprint Ref>.PcbLib` (one model per file), `<name>.xlsx` (one sheet per category, every cell text), a flat `<name>.csv` for diffs, the `.DbLib` (or `.SVNDbLib` with `--svn`) and `ecadlib_sync.json`, which records the rows and files the tool wrote. Each part is reported the way `push` reports it: `added`, `updated` (with the changed columns or model files), `up_to_date`, `held` (left exactly as it is), `skipped` (no models), or `error` (a model that does not read back through `altium_lib`'s readers, or does not contain the ref its row names; it never reaches the table). Re-runs rewrite only what changed. Columns added by hand to the sheet are kept, and the plan's `cleared` fields are blanked. Rows and files that drop out of the plan are reported as `not_in_plan`. `--prune` deletes them, but only the ones this tool wrote. An existing `.DbLib` is merged rather than replaced: edits made in Altium's DbLib editor survive, and missing tables and field maps are appended. With `--svn`, `--commit` runs `svn add`/`delete`/`commit` on exactly the touched files when `svn` is on PATH and `--to` is a working copy; otherwise it prints those commands. svn is not installed here, so that path is tested with a fake runner. On the sample plan (`out/librarian/plan.json`) the result was 11 added and 3 held; a second run wrote nothing.

**Verified** against two real files fetched from GitHub: `ryankurte/altium-library/electronpowered.dblib` (Excel) and `mechanart/AltiumLibrary/mechanart_oshw.SVNDbLib`. The Excel fast connection writes `LibraryDatabaseType=Microsoft Excel`, `LibraryDatabasePath=<file>` and `DatabasePathRelative=1`, and its tables are `<sheet>$`. `[Library Path]`/`[Footprint Path]` are mapped as reserved fields. An SVNDbLib has the DbLib layout plus `SVNRepositoryURL=` and `FileRepositoryIsRelative=0` in `[DatabaseLinks]`, and its `LibrarySearchPath` lists the repository's symbol and footprint folders, separated by `;`. Altium's docs say that SVNDbLib model paths are "relative to the root of the repository" and that Access/Excel paths can be stored "relative to the Database Library/SVN Database Library file".

**Unverified** (there is no Altium or Windows here): that the `.xlsx` opens through ACE `Excel 12.0 Xml`, since the real example used `.xls` with `Excel 8.0`; that `SVNRepositoryURL` should be the working-copy URL (the URL `--to` is checked out from) so `SchLib\x.SchLib` resolves; that `LibrarySearchPath` is where Altium keeps the "Models Location" folders; the `.SVNDbLib` extension case (Altium writes `.SVNDbLib`); and the INI encoding (UTF-8 here, though only ASCII names are expected).

## 5. `altium_lib.py`: Altium `.PcbLib` / `.SchLib` writer (pure Python)

```
python3 -m ecadlib.altium_lib pcblib ecadlib/package_specs/*.json -o out/altium_models/PcbLib [--density A|B|C] [--single lib.PcbLib]
python3 -m ecadlib.altium_lib schlib examples/symbols/*.json  -o out/altium_models/SchLib
python3 -m ecadlib.altium_lib dump out/altium_models/PcbLib/SOIC-8_3.9x4.9mm_P1.27mm.PcbLib
python3 -m ecadlib.altium365 plan examples/sample_bom.csv --triage out/triage_sample_bom.csv \
        --models out/altium_models -o out/altium365/plan.json      # -> 4 ready, 10 needs_altium_models
                                                                     #    add --models out/altium_models_kicad (section 6) -> 14 ready
```

No dependencies beyond the standard library. `cfb.py` writes the OLE2 container (v3, 512-byte sectors, mini stream, DIFAT for files over ~7 MB). `altium_lib.py` builds the Altium streams inside it. The record layouts are ported from **AltiumSharp** (C#, Apache-2.0; attribution in the file header and `NOTICE`). `altium-designer-mcp` (GPL-3.0) was read for notes only, and none of its code is used.

What gets written:
* **Footprints** come from the same `footprint_gen.build()` output as the `.kicad_mod` files: SMD pads with rounded-rect corner %, F.SilkS to Top Overlay, F.Fab to Mechanical 13, F.CrtYd to Mechanical 15, and the pin-1 triangle as a region. Exposed-pad paste windows become Top Paste fills, and the EP's own paste is suppressed. Units are exact: 1 mil = 10000 internal units and 1 mm = 10^8/254. One footprint per file by default (Altium 365 releases one model per file). `SOURCE_DATE_EPOCH` makes output byte-reproducible.
* **Symbols** come from a small JSON pin list (`examples/symbols/*.json`): a rectangle-body IC with pins on the left and right with electrical types, plus generic 2-pin Resistor and Capacitor symbols. Each has a Designator and Comment, plus a footprint link to the PcbLib name.
* **Designator and comment text:** the reference becomes `.Designator` on Top Overlay and the value `.Comment` on Mechanical 13 (stroke font, KiCad's height, stroke width, rotation and justification; the anchor is shifted so a nominal 3-character designator and 8-character comment centre where KiCad centres them).
* **Pin graphics:** pins in `examples/symbols/*.json` take an optional `"shape"`: a KiCad shape name (`inverted`, `clock`, `inverted_clock`, `input_low`, `clock_low`, `output_low`, `edge_clock_high`, `non_logic`) or `{"inside_edge"|"outside_edge"|"inside"|"outside": symbol}` with Altium's IEEE symbols. `~{EN}` in a pin name becomes Altium's overline `E\N\`.
* **Display modes:** symbols can carry alternate (De Morgan) views, written as Altium 24 writes them.
* `CH340C.json` and `TPS62133RGTR.json` carry `"needs_datasheet_verification": true`. **Their pinouts were written from memory and must be checked against the datasheets before anything is released.**

**Verified here** (2026-10-13; `python3 -m tests.test_offline` runs 11/11, 5 of them new):
* **Container:** `olefile` reads all 13 outputs with `raise_defects=DEFECT_INCORRECT` and logs nothing. 200 random trees and an 8 MB file that needs DIFAT read back byte-exact. 18 real Altium files from AltiumSharp's TestData and the Nexar demo were rewritten through `cfb.to_bytes`, and each came back with an identical stream tree.
* **Records, byte for byte against real Altium-saved files** (AltiumSharp TestData): re-encoding the decoded records with this writer gives
  * tracks 190/190, fills 5/5 and regions 4/4 identical;
  * SMD pads: 12 identical, and 2106 that differ only at pad main-block byte 103, a paste-mask flag that real files set to 1 and this writer leaves at 0;
  * SchLib pins: 63 identical. 106 more differ only in an empty swap-id string, and 4 that use pin symbols were skipped.
* **Text and pins (added 2026-10-14):** 16 of 17 stroke-font PCB text records identical (the other differs only in stale bytes after the font name; 19 TrueType/barcode/framed texts skipped). SchLib pins, all 595 in TestData: 384 identical, 111 differ only in the not-accessible bit 0x20, 4 in swap-id/default, 96 use other formal types. Display modes and edge symbols match an Altium 24-saved sample record for record apart from UniqueIDs.
* **Third-party reader:** `pyaltiumlib` 0.7.1 parses all 13 files. Coordinates, sizes, rounded-rect shape and corner %, paste expansion and pin names/types all match the input. One example is SOIC-8 pad 1 at (-2.4725, 1.905) mm, 1.965×0.58 mm, corner 50%. Its only warnings are "unsupported RecordID 45/46/48" (the footprint-link records), and it prints the same warnings for the real Nexar demo `.SchLib`. Footprints and symbols rendered to SVG looked right.
* **Structure:** compared with an Altium Designer 2025-era TestData library and the 2014-era Nexar sample, the stream set is the same, apart from `PrimitiveGuids` (optional, and also absent from the Nexar sample) and `Models/` (no 3D bodies).
* **Not run:** AltiumSharp's own reader. It needs .NET 10, and the proxy blocked the .NET download.

**Assumed (not checked in Altium Designer, which was never run here):**
* That Altium Designer and Altium 365 accept these files. Nothing was opened in Altium Designer. The `Library/Data` header is copied from an Altium-saved library rather than synthesized, because altium-designer-mcp's notes report that Altium rejects synthesized ones with "Catastrophic failure whilst loading section Library". That header still carries the source library's view settings.
* Pad format: the 202-byte main block and the 596-byte per-layer size/shape block match the TestData files. Newer Altium versions write a longer full-stack block. The byte-103 flag above is assumed to be a cache that Altium recomputes.
* EP paste is suppressed with a negative manual paste expansion, which is assumed to read as "no paste". KiCad draws the paste windows as rounded rectangles, and they are written here as rectangular fills.
* `LayerKindMapping` is empty, so Mechanical 13 and 15 have no layer types and appear as plain mechanical layers. Solder mask and paste use "from rule" except on the EP.
* SchLib: `Weight` = records + 1 and the pin flag bits were inferred from real files.
* Not written by the generator path: through-hole pads and 3D bodies (the KiCad import in section 6 adds through-hole pads, arcs, keep-outs and 3D bodies) and `PrimitiveGuids`.
* Text: Altium is assumed to recalculate the cached text size (exact only for `.Designator`/`.Comment`). Not checked in Altium: signal-flow arrow direction, the falling-edge clock written as clock plus active-low input, and how display modes behave when switching views. Names over 31 characters are truncated as CFB storage names, and `SectionKeys` holds the mapping in the same way Altium does.

## 6. `kicad_import.py`: KiCad library parts to Altium

```
python3 -m ecadlib.kicad_import batch examples/kicad_import_parts.json -o out/altium_models_kicad [--step]
python3 -m ecadlib.kicad_import footprint Package_TO_SOT_SMD:SOT-23 [--as NAME] [--height MM] [--step] -o out/altium_models_kicad
python3 -m ecadlib.kicad_import symbol Transistor_FET:AO3401A [--as NAME] [--footprint SOT-23] [--param K=V] -o out/altium_models_kicad
    (local inputs work too: X.kicad_mod, Lib.kicad_sym:Name, Name.kicad_sym; --offline uses only out/http_cache)
python3 -m tests.test_kicad_import
```

Fetches official footprints, symbols (KiCad 7-9, including `.kicad_symdir` and `extends`) and STEP models from gitlab.com/kicad/libraries and writes one `.PcbLib`/`.SchLib` per part. It handles through-hole pads (round and slotted, plated and NPTH), rect/roundrect/oval/circle pads (custom and trapezoid pads become their bounding rectangle, with a note), arcs and circles, keep-out zones, and hidden or stacked power pins. Every part gets a 3D body: an extruded body over the fab outline by default, with the height taken from the KiCad STEP model, else a per-package table, else `--height`; `--step` embeds the STEP model instead. Without a KiCad model, a generated STEP is embedded instead of the box (section 14; `--box` keeps the box). `examples/kicad_import_parts.json` maps the 10 sample parts that had no models; with it the plan goes from 4 to 14 ready. Outputs are not committed because the KiCad libraries are CC-BY-SA 4.0 (with a design-use exception).

Reference and Value text become `.Designator` and `.Comment`; pin shapes map onto Altium pin symbols, `~{X}` names onto overlines, and De Morgan body styles onto Altium display mode 1. Multi-unit symbols become multi-part.

Checked here: text records, pin symbols, through-hole pads, arcs, 3D bodies, keep-out regions and embedded STEP models re-encode byte for byte against Altium-saved files from AltiumSharp's TestData (except for colour, opacity and thermal-relief settings this writer doesn't set). All outputs read back with pyaltiumlib, `altium_lib dump` and olefile in strict mode, and every symbol pin has a pad with the same number. **Not checked in Altium:** slot orientation, through-hole paste, the extruded-body checksum, and stacked hidden pins. FTSH-105 uses KiCad's generic 1.27 mm 2x05 SMD header as a stand-in, so compare it with the Samtec drawing.

## 7. `datasheet.py`: datasheet reader (step 1 of the design)

```
ANTHROPIC_API_KEY=... python3 -m ecadlib.datasheet <datasheet.pdf | https://...pdf> --part <MPN> \
        [--package "<hint, e.g. RGT VQFN-16>"] [--pages 30-33] [--model claude-opus-5-5] [--fetch] -o out/datasheet
python3 -m ecadlib.datasheet <pdf> --part <MPN> --from-response out/datasheet/<MPN>.response.json -o out/datasheet  # re-check offline
python3 -m tests.test_datasheet
```

Claude reads the package drawing and pin table and returns every dimension as printed (min/nom/max plus unit), with the PDF page and figure it came from, through a structured-output schema derived from `package_schema.json`. The tool converts inches and mils to mm, then runs schema validation, `footprint_gen.validate`/`build`, sanity rules (pin count vs pin table, pitch x pins vs body, min <= nom <= max, unit plausibility) and symbol checks (unique pins, 1..N plus EP). Anything that fails goes in `needs_review`; nothing is corrected. Outputs: the package spec, the symbol JSON, a provenance sidecar, a review checklist (.md) and the raw response. The PDF is treated as untrusted data. Exit code 0 when clean, 2 when something needs review, 1 on error.

### A whole BOM in one batch

```
export ANTHROPIC_API_KEY=...                      # or name another variable in ecadlib.toml [datasheet] api_key_env
ecadlib datasheet estimate out/librarian/plan.json          # tokens and $ before anything is sent
ecadlib datasheet batch out/librarian/plan.json [--no-wait] [--no-batch] [--part P] [--yes]
ecadlib datasheet batch examples/sample_bom.csv -o out/librarian --lib ...   # librarian + batch in one go
ecadlib librarian BOM ... --datasheet-batch [--datasheet-no-wait]           # the same, as a librarian option
python3 -m tests.test_datasheet_batch
```

Every plan part that still has no symbol or footprint goes to the reader. Its datasheet link comes from the BOM's datasheet column, the plan's `ComponentLink2URL`, a link a source found (KiCad symbol, EasyEDA `lcsc.url`) or LCSC's `https://www.lcsc.com/datasheet/<C-number>.pdf`, tried in that order. Each link is downloaded (stored under `<cache>/datasheets/` with its URL, SHA-256, size and page count) and must start with `%PDF`; an HTML viewer page is skipped. A link this machine cannot reach is sent as a URL document for the API to fetch, and the part is flagged. A PDF over `[datasheet] auto_pages` pages (default 40), or over the request limits, is cut to the pages whose text mentions the package outline, land pattern or pin table, plus pages 1 to 3. Page numbers in the outputs are mapped back to the original file. `--pages` chooses the pages yourself.

All parts go into one Message Batch (half price), one request per part with `custom_id` = part name. The batch is polled, the JSONL results are collected, and each part is built through the librarian's writers. Every result is marked `verify`, so the release check holds it until a person has looked; LLM output is never released automatically. The release check then runs again. The batch id is kept in `<out>/datasheet/batch_state.json`, so `--no-wait` or an interrupted run collects the same batch next time instead of paying twice. A batch create is never retried after an ambiguous failure. Errored or expired requests are sent again on the next run. `--no-batch` makes one Messages call per part instead, with the server-side refusal fallback; the Batches API does not accept that parameter.

* **Result cache:** answers that parsed are stored under `<cache>/datasheet_results/`, keyed by PDF SHA-256 + model + schema version (a hash of the output schema and prompt, so new `package_schema.json` families invalidate it) + part/package/pages/effort. Re-runs cost nothing. Refusals and truncated answers are not cached.
* **Errors and retries:** 429, 5xx and 529 (overloaded) are retried with exponential backoff, honouring `retry-after`. 401, 402/billing, 403, 404 (model) and 413 (too many pages) fail at once with what to do. Every request is logged to `<out>/datasheet/api_log.jsonl` (status, request-id, usage, a body summary with PDFs as size and hash). The key is never logged.
* **Estimate:** with a key, input tokens come from `/v1/messages/count_tokens`. Without one, the estimate is rough: pages x 3,000 tokens. Prices are $ per MTok from the claude-api model table, with the batch at half price. `[datasheet] max_cost_usd` refuses a run whose worst case (every answer at `max_tokens`) is higher, unless `--yes`.
* **Self-check:** compares the extraction with its own footprint name (pin count, EP, pitch, body and EP size) and with family norms (standard pitches; pitch, body and pin-count ranges per `package_type`). Disagreements are listed as `self-check:` review items. A family with no norms gets the generic checks, and the sidecar says so.
* **Workspace / MCP:** `Workspace.datasheet_estimate(bom)` and `datasheet_batch(bom, sync, wait, parts)`; MCP tools `datasheet_estimate` and `datasheet_batch`. The tool submits and returns by default; call it again to collect.

**Not tested live:** no API key is available here, so no successful extraction or batch has run. With an invalid key the live API returned 401 `authentication_error` for messages, count_tokens, batch create and batch retrieve, and the client reported each one correctly. The tests use a fake HTTP layer in the documented Messages, count_tokens and Batches shapes. Datasheet hosts (LCSC and the others) are blocked from this sandbox.

## 8. `lifecycle.py`: lifecycle watcher (keep the library current)

```
python3 -m ecadlib.lifecycle examples/sample_bom.csv out/altium/parts.sqlite out/altium365/plan.json \
        [--a365 https://<ws>.365.altium.com] [--providers nexar,digikey,mouser,jlcpcb_search] \
        [--state out/lifecycle_state.json] [-o out/lifecycle_report] [--strict] [--offline]
python3 -m ecadlib.lifecycle out/altium365/plan.json --update-a365 --workspace <url> [--apply]
python3 -m tests.test_lifecycle
```

Reads parts from BOMs, the DbLib `parts.sqlite`, a `plan.json` or (with `--a365`) the released components of a workspace, and checks them against Nexar Supply (Octopart), DigiKey v4, Mouser and JLC stock. Statuses are normalised to `active < nrnd < ltb < obsolete` or `unknown`; a part takes the worst status and the highest stock its sources report. Each run is compared with the `--state` snapshot and written to `.md` and `.csv`. Exit codes: 0 nothing got worse, 1 a part got worse, 2 bad input, 3 no source answered (missing secrets or blocked network). `--update-a365` writes a `Lifecycle` parameter to released components (dry run unless `--apply`; it sends the full parameter set with `replaceExisting: true` so nothing is duplicated). `examples/lifecycle-watch.yml` is a sample weekly GitHub Actions job. Credentials: `NEXAR_CLIENT_ID`/`NEXAR_CLIENT_SECRET` (lifecycle needs a Nexar plan with Tech Specs), `DIGIKEY_CLIENT_ID`/`DIGIKEY_CLIENT_SECRET`, `MOUSER_API_KEY`, `NEXAR_TOKEN` for workspace access. Secrets are never written to the HTTP cache. **Not tested live:** every vendor host is blocked from this sandbox; the parsers are tested against the documented response shapes.

## 9. `librarian.py` and `release_check.py`: one command from BOM to library

```
python3 -m ecadlib.librarian examples/sample_bom.csv --lib examples/kicad_lib --lib out/footprints -o out/librarian \
        [--sources kicad,easyeda,datasheet] [--allow PART] [--refresh [PART ...]] [--refresh-index] [--offline]
python3 -m ecadlib.release_check out/librarian/plan.json [--allow PART] [--unallow PART]
python3 -m tests.test_librarian; python3 -m tests.test_release_check
```

Triages the BOM, converts parts already in your `--lib` libraries, and for anything still missing tries in order:
* **kicad**: the official KiCad libraries. A name index is built once from the GitLab tree API and cached. Symbols match the MPN by exact name, KiCad `x` wildcards, or the MPN minus an ordering suffix; the footprint comes from the symbol's Footprint property, the BOM footprint, or a footprint named after the MPN. R/C/L/LED use `Device:*` plus the footprint by package.
* **easyeda**: by LCSC number, through a native converter (`easyeda_convert.py`).
* **datasheet**: `ecadlib.datasheet`, when a datasheet URL is known and `ANTHROPIC_API_KEY` is set. `--datasheet-batch` reads every part that reaches this step as one Message Batch (section 7).

Outputs in `out/librarian/`: `models/`, a `plan.json` for `altium365 push` recording each part's source, `review.md/.csv` (what came from where and what needs a person), and the release-check report. Re-running skips finished parts unless `--refresh` is given.

The release check runs at the end and works on any plan. A part is **held** (and `altium365 push` skips it) unless: every pin has a pad and every pad a pin (mechanical and mounting pads excepted); the footprint passes `footprint_check` (KiCad-library parts: pad overlap, courtyard and pin 1); a 3D height is present and plausible for the package; Manufacturer, MPN, datasheet link and Description are filled in; the files exist and read back; and no datasheet-reader output is unreviewed. `--allow PART` overrides the failures present at that moment and is recorded in the plan; missing or unreadable files cannot be overridden.

Live run on the sample BOM (2026-10-14): 11 of 14 ready, 3 held. TYPE-C-31-M-12 has no symbol and KiCad's footprint has intentionally overlapping same-net pads. FTSH-105 has no footprint, LCSC number or datasheet. X322512MSB4SI has no crystal symbol chosen automatically. **Not tested live:** EasyEDA (blocked from this sandbox; the format assumptions are listed in `easyeda_convert.py`) and the datasheet step (no API key).

### Fixing held parts: `resolve.py`

```
python3 -m ecadlib.resolve out/librarian/plan.json list                               # held parts and why
python3 -m ecadlib.resolve out/librarian/plan.json --part X322512MSB4SI show          # both halves, pins vs pads
python3 -m ecadlib.resolve out/librarian/plan.json --part X322512MSB4SI list          # ranked candidates
python3 -m ecadlib.resolve out/librarian/plan.json --part X322512MSB4SI choose symbol kicad:Device:Crystal_GND24
python3 -m ecadlib.resolve out/librarian/plan.json --part P choose symbol kicad:Lib:Name --map S1=SH   # pin -> pad
python3 -m tests.test_resolve
```

You don't need to edit `plan.json` by hand. `list --part` ranks candidates for the missing half (or for both halves):
* official KiCad names (`kicad:Lib:Name`, from the librarian's cached index)
* your `--lib` libraries (`local:Lib:Name`)
* ecadlib's package specs (`spec:Name`, built by `footprint_gen`)
* any `alternatives` recorded on the item

Ranking uses shared name tokens (rare ones weigh more), MPN prefix, package, body size, pitch and pin grid, and the usual libraries for the designator class. For connectors it also adds `Conn_` symbols for the pad count. The best names are then opened, and their pins or pads are compared with the item's other half using the release-check rule. A full match ranks first. A near miss gets a suggested `--map`.

`choose` converts the part into `models/` the same way the librarian does. It then updates the item, recording "chosen by user" in `librarian.tried`, and `librarian_state.json`, so a librarian re-run keeps the choice. Finally it re-runs the release check on that item only. The Python API (`held`, `show`, `candidates`, `choose`) returns plain dicts and lists.

Live run on a copy of the 14-part plan (2026-10-14):
* TYPE-C-31-M-12: top candidate `Connector:USB_C_Receptacle_USB2.0_16P` (pins match), now **ready**. With a symbol present, the stacked A1/B12 pads are warnings.
* X322512MSB4SI: the four 4-pin `Crystal_GND23/24` symbols match. `Crystal_GND24` was chosen, and it is now **ready**. Which pins are ground still has to be checked against the datasheet.
* FTSH-105-01-L-DV-K: the top candidates are the `PinHeader_2x05_P1.27mm` footprints (10 pads, match). With `_Vertical_SMD` chosen, the only hold left is the missing datasheet link.

## 10. `upstream.py`: upstream change alerts

```
python3 -m ecadlib.upstream check-kicad out/altium_models_kicad/manifest.json [-o out/upstream_kicad] [--update] [--offline]
NEXAR_TOKEN=... python3 -m ecadlib.upstream check-a365 out/altium365/plan.json --workspace <url> \
        [--folder Components] [--state out/a365_state.json] [-o out/upstream_a365]
python3 -m tests.test_upstream
```

Every `kicad_import` conversion records in `<out>/manifest.json` where each output came from: source URLs (footprint, STEP, symbol and its `extends` parents), sha256 and git blob id, the last GitLab commit, a pad/courtyard/pin summary, the output hash and the converter version. `check-kicad` asks the GitLab API for each source's blob id without downloading, then fetches and diffs only changed files: pads added, removed, moved or resized, courtyard changes, pins added, removed or renamed, and files deleted or renamed upstream. `--update` reconverts the changed parts. `check-a365` compares a release plan with the workspace: missing components, parameter, comment, description, folder and model-name differences (parameters added by hand in Altium 365 are not drift), and workspace components the plan doesn't list. Nexar exposes no symbol or footprint revision, so `--state` records component revisions and model GUIDs and flags changes since the last run. Exit codes: 0 no changes, 1 changes, 2 error. `examples/upstream-watch.yml` is a sample weekly job. **Tested live** against gitlab.com; `check-a365` only against a mock (Nexar is blocked here).

## 11. `kicad_export.py`: the same library as KiCad files

```
ecadlib kicad-export out/librarian/plan.json -o out/kicad --name mylib [--lib examples/kicad_lib] [--include-held] [--offline]
python3 -m tests.test_kicad_export
```

Writes what the librarian built for Altium as a KiCad library: `mylib.kicad_sym` (one symbol per plan part, named after it, with Reference, Value, Footprint `mylib:<footprint>`, Datasheet, Description, Manufacturer, MPN, supplier numbers such as `LCSC` and the plan's other parameters), `mylib.pretty/` (one `.kicad_mod` per footprint, renamed to the plan's footprint name), `mylib.3dshapes/` (STEP models, referenced as `${KIPRJMOD}/mylib.3dshapes/X.step`), `sym-lib-table` / `fp-lib-table` with `${KIPRJMOD}` paths, and `kicad_export.json` (what came from where, and the checks). Copy the folder's contents into the KiCad project, or paste the two `lib` lines into its tables. Held parts are skipped unless `--include-held`; output is sorted and repeatable.

* **Symbols:** a part whose symbol came from KiCad (`kicad:` or a `--lib` folder) gets that KiCad symbol again, with its `extends` chain flattened and renamed; an EasyEDA symbol is converted again from the EasyEDA data; anything else (datasheet reader, or a source that can't be reached) is drawn from its `.SchLib`, read back with `altium_lib.read_schlib` (pins with type, shape and visibility, body graphics, parts as units, display modes as De Morgan styles).
* **Footprints:** the plan's `.kicad_mod` when there is one, else the `.PcbLib` converted back. STEP files come from `kicad-packages3D` (through the HTTP cache); ecadlib-generated footprints have none, since Altium gets an extruded body.
* **Format:** KiCad 8 (`.kicad_sym` version 20231120, footprints at most 20240108), so KiCad 8, 9 and 10 can open it. KiCad 9/10 syntax from the upstream library is written the KiCad 8 way and flags KiCad 8 doesn't know are dropped.
* **Checks:** every file parses with `ecadlib.sexpr`, each footprint passes release_check's footprint rule at the level the release check recorded, and symbol pins are checked against footprint pads. **Not verified in KiCad itself:** `kicad-cli` isn't installed here, and `tests/test_kicad_export.py` runs it only when it is.
* **License:** KiCad library content is CC-BY-SA 4.0. An export that includes some gets a `NOTICE.txt`, and the export stays out of git (`out/kicad/` is in `.gitignore`).

## 12. `approvals.py` and `dedupe.py`: sign-off and duplicates

```
ecadlib approve out/librarian/plan.json status
ecadlib approve out/librarian/plan.json approve --part CH340C [--role librarian] [-m "pinout checked"] [--user "Ann <ann@x.com>"]
ecadlib approve out/librarian/plan.json reject  --part AO3401A -m "pin 3 is the drain"
ecadlib dedupe  out/librarian/plan.json [--lib out/altium_models --lib out/altium/ecadlib_sqlite.DbLib ...] [--merge] [--bom BOM.csv]
python3 -m tests.test_approvals; python3 -m tests.test_dedupe
```

**Approvals.** Put an `approvals.json` (or `.toml`) next to the plan, for example `{"required": 2, "roles": ["librarian"], "users": {"ann@x.com": ["librarian", "hardware"]}}`. Other keys are `no_self_approval` (default true: whoever gave the `--allow` override cannot approve that part), `applies_to` (`all` or `allowed`), `parts` (glob patterns) and `reject_blocks`. With a policy, the release check marks parts that pass but are not yet signed off as `awaiting_approval`, and `altium365 push` releases only parts that are ready **and** approved. Without a policy nothing changes. Each decision is stored in the part's `approvals` list with who, when, role, comment and a hash of the part's `.SchLib`/`.PcbLib` bytes and key fields. If any of those change later, the approval goes stale and push holds the part again. Identity comes from `--user`, then `$ECADLIB_USER`, then `git config user.name/email`. Every decision, including refused ones, is appended to `approvals_audit.jsonl` next to the plan. `release-check --allow P --user U` records who gave the override. The librarian keeps approvals across re-runs.

**Duplicates.** `dedupe` compares the plan with existing libraries: `.SchLib`, `.PcbLib`, `.kicad_mod`, a `.DbLib` with its SQLite or Excel file, and the Altium 365 component list from a `check-a365 --state` file (it reads `a365_state.json` next to the plan automatically). It finds three kinds of match:
* the same MPN once packaging suffixes are stripped (`/TR`, `-REEL7`, `T&R`, `#TRPBF`, `(LF)(SN)`, `,115`). A suffix with no separator, like TI's `RGTR`/`RGTT`, is kept unless you add a pattern to `dedupe.json`;
* the same footprint geometry under another name (pads and courtyard, with `.PcbLib` and `.kicad_mod` compared directly);
* the same symbol pinout, for symbols with 3 or more pins.

It also reports footprints and symbols that share a name but differ. Each group gets a recommended keeper; `--keep` overrides it. `--merge` first saves `plan.json.pre-dedupe`, then rewrites plan references to the keeper. A duplicate part gets `duplicate_of`, and push skips it. Footprint and symbol references are pointed at the keeper's file. It also writes `dedupe_map.csv` (and, with `--bom`, a remapped BOM copy). Library files, DbLib rows and workspace components are only reported, never changed or deleted.

Live run on a copy of `out/librarian/plan.json` (2026-10-14), with `--lib out/altium_models out/altium_models_kicad out/footprints out/altium/ecadlib_sqlite.DbLib`:
* No MPN duplicates.
* One geometry duplicate: `R_0402_1005Metric` = `C_0402_1005Metric` in `out/footprints`.
* 13 name conflicts, where `out/altium_models` and `out/footprints` hold different versions of the same footprint name (e.g. SOIC-8 pads 1.95×0.60 vs 1.965×0.58 mm), plus three symbols that differ from older conversions.

All 13 of the librarian's `.PcbLib` files hash the same as their source `.kicad_mod`.

## 13. `sourcing.py`: second sources and cost

```
python3 -m ecadlib.sourcing examples/sample_bom.csv --qty 100 [--alternates] [--write-plan out/altium365/plan.json] \
        [--providers nexar,digikey,mouser,jlcpcb_search] [--currency USD] [--country US] [-o out/sourcing] [--offline]
python3 -m tests.test_sourcing
```

For each fitted BOM line it needs BOM qty x `--qty` boards and costs every offer from Nexar/Octopart (all its authorized sellers), DigiKey v4, Mouser and JLCPCB/LCSC at the quantity you would really order: MOQ, order multiple and price breaks, buying up to the next break when that is cheaper in total. It picks the cheapest offer whose stock covers the order. The report (`.md` + `.csv`) gives the cost rollup (total and per board, plus what each distributor could supply on its own), lead time and MOQ per line, and flags `no_stock`, `low_stock` (below 3x need, `--low-stock-factor`), `single_distributor`, `lifecycle:nrnd|ltb|obsolete`, `not_found` and `no_price`.

`--alternates` looks for second sources:
* **Passives (R, C, L):** a parametric search, then a drop-in check against the part's own specs and the BOM. Value and package must match. Tolerance must be no wider, and voltage, power and current ratings no lower. Dielectric must be the same or better (C0G > X7R > X5R ...). A candidate whose data does not state every parameter is `unconfirmed` and is never used.
* **ICs and everything else:** Nexar `similarParts`, DigiKey substitutions and Mouser's suggested replacement are listed as **needs engineer review** and never substituted.
* NRND, LTB and obsolete candidates are dropped. `single_source` means nothing usable was found.

`--write-plan` fills empty `Supplier 1/2` and `Supplier Part Number 1/2` parameters in an altium365 `plan.json`. It never replaces a supplier that is already set, such as the BOM's LCSC number. For passives it also fills `Alternate Manufacturer N` and `Alternate Manufacturer Part Number N` with confirmed drop-ins. Filled names are removed from the item's `cleared` list, so `altium365 push` sends them instead of deleting them. Prices and IC review candidates go in `item["sourcing"]` only, so a price change does not cause a new component revision on every push. Exit codes: 0 every line can be bought from stock, 1 some cannot, 2 bad input, 3 no source answered.

Credentials are the lifecycle ones: `NEXAR_CLIENT_ID`/`NEXAR_CLIENT_SECRET` or `NEXAR_SUPPLY_TOKEN` (pricing needs a Nexar plan that includes it), `DIGIKEY_CLIENT_ID`/`DIGIKEY_CLIENT_SECRET` and `MOUSER_API_KEY`; JLCPCB needs none. Responses are cached for 12 h (`--max-age`); Mouser's `apiKey` is redacted from cache keys and files, and token requests are never cached. **Not tested live:** every vendor host is blocked from this sandbox. The Nexar queries are validated against `tests/nexar/schema.graphql`. The other shapes come from public docs and third-party clients, and each provider's `shapes` string (repeated in the report) says what is assumed.

## 14. `step_gen.py`: package-shaped 3D models (STEP)

```
python3 -m ecadlib.step_gen ecadlib/package_specs/SOIC-8_3.9x4.9mm_P1.27mm.json -o out/SOIC-8.step [--height MM]
python3 -m ecadlib.step_gen QFN-24-1EP_4x4mm_P0.5mm_EP2.6x2.6mm -o out/qfn24.step     # from a footprint name
python3 -m ecadlib.step_gen --check out/*.step                                          # validate any STEP file
python3 -m tests.test_step_gen
```

When a footprint has no KiCad STEP model, `kicad_import` and the librarian now embed a generated STEP in the PcbLib instead of extruding a box (`--box` on either command, or `gen_step=False`, keeps the old behaviour; the box is still used when the family is unknown). The generator is pure Python: manifold solid B-reps with planar faces (round parts are 24-sided prisms), AP214, colours per solid (dark body, silver leads, light pin-1 dot or polarity band), units mm. Placement matches KiCad's library models: origin at the footprint origin, z = 0 on the board, pin 1 where the footprint has it (2-terminal parts at -x, dual/quad rows top left, through-hole at 0,0).

Families: chip R/C/L/LED (body + end caps; MELF round), molded tantalum/SMA/SMB/SMC (terminals folded under, polarity band), gull-wing SOIC/SSOP/TSSOP/MSOP/SOT-23(-5/-6)/SC-70/SOT-223 (tab)/SOD-123/323/523 and flat-lead SOT-89, QFP/LQFP/TQFP, QFN/DFN/SON (bottom terminals, exposed pad), and through-hole vertical pin headers, radial capacitors and axial resistors and diodes. Dimensions come from the package spec (datasheet-reader output, or `package_specs/<name>.json`), else from the footprint name and typical JEDEC/EIA outlines. The height comes from the caller, then `height_max`, then a per-family table that stays inside `release_check.HEIGHTS`.

`--check` (and the tests) parse the STEP file and check that every `#ref` resolves, that every closed shell is closed (each edge used by two faces in opposite directions), that loops connect, that faces are planar and oriented outward, that each volume is positive and every solid has a colour. The tests also compare the bounding box with the spec and check that each lead sits on the footprint_gen pad with the same number, for all 26 specs and 25 more footprint names. **Not checked:** no STEP kernel is installed here (no OCC, FreeCAD or cadquery), so the files have never been opened in Altium, KiCad or FreeCAD. Shapes are simplified: no pin-1 dimples, rounded edges or radial-cap polarity stripe. The SOT-89 tab and the name-derived dimensions are approximations.

## 15. `upkeep.py`: weekly upkeep (one issue or PR)

```
ecadlib upkeep library/altium365/plan.json [--bom bom.csv] [--manifest models/manifest.json] [--workspace URL] \
        [--checks lifecycle,upstream,sourcing,a365] [--state out/upkeep_state] [-o out/upkeep] [--apply] [--github issue|pr]
python3 -m tests.test_upkeep
```

This runs the lifecycle watcher, the KiCad upstream check, the price and stock check, and the Altium 365 drift check together, in one process. It compares the results with what the last run saw (`--state DIR`) and reports only what is **new**. That covers parts that became NRND, last time buy or EOL; stock that ran out or came back; a best unit price that rose by more than 20 % (`--price-jump`, or `price_jump_pct` under `[upkeep]` in `ecadlib.toml`); KiCad sources that changed, were renamed or were deleted; and new Altium 365 drift rows. The output is one `out/upkeep.md` plus `.json`. Exit codes: 0 nothing new, 1 new changes, 2 error. When a check fails, the other checks still run, its state is kept, and its changes are reported again next time.

A check without credentials is **skipped**, not failed, and the report says what to set. Lifecycle needs Nexar, DigiKey or Mouser keys. Sourcing also uses JLCPCB, which needs no key. A365 needs `NEXAR_TOKEN` and a workspace, from `--workspace` or `[altium365] workspace`. Upstream needs the provenance `manifest.json` that `kicad_import` writes. It is found next to the plan or in `<plan dir>/models/`.

`--apply` only reconverts KiCad parts whose upstream source *changed*. Renamed and deleted sources are left for a person. This gives a pull request a real library diff. `--github issue` updates **one** rolling issue labelled `ecadlib-upkeep`: it replaces the body and adds a short comment, so it never opens duplicates. `--github pr` commits the applied files to `ecadlib/upkeep-YYYY-MM-DD`, pushes the branch and opens or updates the PR. With nothing to commit, it falls back to the issue. When nothing is new, GitHub is not touched. The GitHub calls use the REST API when `GITHUB_TOKEN` and `GITHUB_REPOSITORY` (or `--repo`) are set, and `gh api` otherwise. If the GitHub step fails, no state is saved, so nothing is lost. [`examples/upkeep.yml`](examples/upkeep.yml) is a ready-to-copy weekly workflow (cron plus a manual run with an `apply` switch) for **your** library repo, with the secrets it reads listed at the top. It is not active in this repo. The same checks are available from the web/MCP side as the `upkeep_run` tool (`Workspace.upkeep_run`), which never calls GitHub. **Not tested live:** GitHub, GitLab, Nexar and the distributors are fakes in the tests.

## 16. `altium_project.py`: BOM from an Altium project, and where-used

```
ecadlib project Board.PrjPcb [-o bom.csv] [--variant Lite] [--json]      # also a single .SchDoc
ecadlib triage Board.PrjPcb --lib ...        ecadlib librarian Board.PrjPcb --lib ...   (ECADLIB_VARIANT=Lite for a variant)
ecadlib where-used --projects ~/altium/projects [--part GRM188] [--plan out/librarian/plan.json] [--lib DIR] \
        [--a365 a365_state.json] [--lifecycle out/lifecycle_state.json] [-o out/where_used]
python3 -m tests.test_altium_project
```

The project file's `[DocumentN]` list gives the schematics; each `.SchDoc` is read like a SchLib (OLE `FileHeader` stream of
`|KEY=VALUE|` records): components (RECORD=1: LibReference, DesignItemId, SourceLibraryName, UniqueID, ComponentKind,
Altium 365 VaultGUID/ItemGUID/RevisionGUID), their designator (34), parameters (41, `=Value` indirection resolved) and the
current footprint (44 -> 45 with IsCurrent). Parts are grouped by MPN parameter (or a DbLib DesignItemId, or LibRef +
value + footprint) with designators and a quantity per project variant; `[ProjectVariantN]` variations (Kind 1 not
fitted, 2 alternate part, matched by UniqueId) and parameter variations are applied with `--variant`. Graphical, Net Tie
(No BOM) and Standard (No BOM) components are left out; a multi-part component counts once. The CSV has the ActiveBOM-style
headers `read_bom` maps back, plus `Qty [variant]` columns. A `.BomDoc` listed in the project overrides the MPN with its
chosen solution and lists the others as alternates.

`where-used` reads every project under the folders and writes `.md`, `.csv` (part x project: designators, quantity,
variants where not fitted, revision), `_flags.csv` and `.json`. Flags: `missing_from_library` and `unused_library_part`
(against a librarian plan, library folders/DbLibs and Altium 365 state, matched by MPN key, Design Item ID or ItemGUID),
`duplicate_in_use` (dedupe group with another keeper), `old_revision` / `mixed_revisions` (same Altium 365 item at
different revisions across projects) and `lifecycle` (NRND / LTB / obsolete from `lifecycle_state.json`, the plan, or the
component's own Lifecycle Status parameter). Exit code 1 when anything is flagged. MCP tools: `project_bom`, `where_used`.

**Checked:** the `.SchDoc` reader against eight Altium-saved schematics from AltiumSharp's TestData (DbLib and Altium 365
components, multiple footprint models, no-BOM test points); `.PrjPcb` variants follow AltiumSharp's reader and fixture.
**Not checked:** no real `.PrjPcb` with variants or `.BomDoc` was available; the ActiveBOM layout is not published, so that
reader is tolerant and warns when it finds nothing. Multi-channel (Repeat) sheets are counted once, with a warning.
Revision order uses the `-N` suffix of Altium 365 item-revision ids; without one, differing revisions are only reported.

## 17. `sim_models.py`: SPICE and IBIS simulation models

```
ecadlib sim-models out/librarian/plan.json [--models ~/vendor_models] [--attach] [--offline | --no-net]
python3 -m tests.test_sim_models
```

**Find.** Resistors, capacitors and inductors get a built-in model from their Value (no download); a capacitor with an `ESR`/`ESL` parameter (or `[sim] capacitor_esr` in the rules file) or an inductor with a `DCR` gets a small generated `.subckt`. Diodes, transistors, regulators and op-amps look for SPICE, digital ICs (MCUs, USB/UART bridges, logic, interfaces) for IBIS, in this order: your `--models` folders (`.lib .cir .sub .mod .spi .spice .mdl .ckt .ibs`, also inside `.zip`; matched by `.subckt`/`.model`/IBIS `[Component]` name or file name, allowing an ordering suffix), Octopart documents named SPICE/IBIS/simulation (Nexar `documentCollections`, with `NEXAR_SUPPLY_TOKEN` or client credentials), then direct-download URL patterns per manufacturer, fetched only when the host's robots.txt allows it. Otherwise the report lists the manufacturer's model page and the IBIS Open Forum model list (ibis.org/models/) for a person: vendor download pages sit behind JavaScript, licence click-throughs or logins and are never scraped. Every fetch goes through the HTTP cache. **Reachability (2026-10-14, from this sandbox):** ibis.org, ti.com, onsemi.com, nexperia.com, diodes.com, st.com, infineon.com, analog.com, microchip.com, vishay.com, rohm.com, toshiba, wch-ic.com and ftdichip.com all refuse the proxy CONNECT (403); gitlab.com works, but KiCad has no SPICE model library (only symbols, and test models in its QA data). So the network sources are tested only against fakes; run with your own folder of downloaded vendor models, or on a machine that can reach the vendors.

**Check.** Each file is parsed by the small parsers in the module: SPICE `.subckt` name and ports (continuation lines, `PARAMS:`, comments), `.model` name and type, `.include`, encrypted PSpice/LTspice blocks; IBIS `[IBIS Ver]`, `[File Name]`, `[Component]`, `[Manufacturer]`, `[Package]`, `[Pin]`, `[Model]`, `[Comment Char]`, and pins whose model does not exist. Model pins are matched to the symbol's pins (the `.SchLib` read back): **verified** when every model pin matches a symbol pin by name (with aliases: A/ANODE, K/CATHODE, G/GATE, +IN/IN+/INP, VCC/V+ ...), or for a 2-pin passive; **verify** when matched by pin number, by position or by the pin-1-is-cathode convention; **mismatch** when the counts disagree or an IBIS `[Pin]` list is not the symbol's pins (also compared with the footprint's pad count). A mismatch is never attached.

**Attach** (`--attach`). Altium: a SIM implementation is added to the part's `.SchLib` (RECORD=45 `ModelType=SIM`, `ModelName`, `ModelDatafile0` = the model file, `ModelDatafileEntity0`/`Kind0`, then RECORD=46, one RECORD=47 per pin mapping the symbol pin (`DesIntf`) to the model node index (`DesImp0`), and RECORD=48); IBIS models are linked as `ModelType=SI`. The model file is copied beside the `.SchLib` as `<ModelName>.ckt` (subcircuit), `.mdl` (`.model`) or `.ibs`. Generic passives on the shared Resistor/Capacitor symbols get a value-driven `RESISTOR`/`CAPACITOR`/`INDUCTOR` link; part-specific models are not added to a symbol another part shares. KiCad: the plan item gets `Sim.Device`/`Sim.Library`/`Sim.Name`/`Sim.Pins`/`Sim.Params`, which `ecadlib kicad-export` writes into the `.kicad_sym` with the model files in `<lib>.sim/` (field names and the `Sim.Pins "1=K 2=A"` form checked against KiCad's own `qa/data/eeschema/spice_netlists` schematics). An attached model whose pin map is **verify** is added to the item's `verify` list, so the release check holds the part until someone looks (`--allow`). Outputs: `sim_models.md`/`.json` next to the plan, `models/Sim/`.

**Altium 365.** `DesReleaseComponentInput` takes symbol and footprint files only; the schema has a `SIMULATION_MODEL` content kind but no way to release one with a component, so model files are not pushed. The SIM link travels inside the released `.SchLib`; `altium365 push` notes this per part. MCP tool: `sim_models` (with `attach`); Workspace: `Workspace.sim_models(bom, attach, models)`, which also searches the workspace's `sim_models/` folder.

**Not verified.** AltiumSharp confirms the record types (45 implementation, 46 map-definer list, 47 map definer with `DesIntf`/`DesImpCount`/`DesImp{i}`, 48 parameters) and that `ModelType` can be `SIM` or `SI`, but none of its Altium-saved test files has a SIM or SI link. The datafile kind (`SIM`), the pin-map owner (the RECORD=46) and the `.ckt`/`.mdl`-beside-the-library lookup are assumptions, and Altium's own SIM parameters (kind, sub-kind, SPICE prefix, netlist template) are not written. Open one attached library in Altium Designer and run a simulation before relying on it. In KiCad, an IBIS device is one pin plus a driver model, so IBIS files are copied but no `Sim.*` fields are set. `Sim.Type` is left out for `.model` devices; the assumption is that KiCad takes the type from the library file (KiCad's own BJT example sets `Sim.Type GUMMELPOON` as well).

## 18. `rules.py` and `ecadlib init`: one file for your library conventions

`ecadlib.toml` holds your naming conventions, required parameters, folder map, layer mapping, footprint density, source order, BOM column names and approval policy. Every tool reads it: the librarian, `altium_export`, `altium365`, `altium_dblib_sync`, `kicad_import`, `altium_lib pcblib`, `footprint_gen gen`, `release_check`, `upstream check-a365`, `kicad_export`, the web UI and the MCP server. Each CLI takes `--rules PATH`. Without it, the tool looks for `ecadlib.toml` in the input's folder and its parents, then in the current folder and its parents (a Workspace uses its own `ecadlib.toml`). Without any file, the outputs are byte-for-byte what they were before (a test checks this).

**Making the file (minimal setup).** `ecadlib init --from DIR [--from DIR ...] [--a365 parts.json] [--yes]` scans:

* `.SchLib`/`.PcbLib`: component and footprint naming patterns, parameter names and how often each is used, which mechanical layers carry the courtyard, assembly and 3D body, and the density (from IPC name suffixes, or from chip pad toe-to-toe compared with IPC-7351 A/B/C);
* `.DbLib` and its data (`.xlsx`/`.csv`/`.sqlite`; for `.mdb` only the field map is read);
* an Altium 365 part list (an `altium365` state file, or JSON in the `a365_sync` shape `{name, folder, parameters, symbol, footprint}`): folders and parameters;
* KiCad `.kicad_sym`/`.pretty`, and BOM/parts spreadsheets (column names).

Names are pattern-mined per category (`RES_{package}_{value}_{tolerance}`, `R{package}_{value}`, `{mpn}` ...) with value style (`4K7`, `4.7k`, `4.7kΩ`) detected. The written file says, for every setting, where it came from and how confident the guess is. `init` asks a question only where the evidence is split (the best pattern under 60% with a runner-up at 20% or more); `--yes` takes the best guess. If `ecadlib.toml` already exists, `init` shows a diff, writes the proposal to `ecadlib.toml.proposed` and changes nothing unless you confirm or pass `--force` (the old file is kept as `.bak`).

The UI has a **Library rules** card (type the folders to scan, review the proposals with their evidence, edit the TOML, save; replacing an existing file asks first and shows the diff). The MCP server has `propose_rules` and `save_rules`.

`ecadlib rules check [PATH]` validates a file (errors name the bad key and suggest the nearest valid one); `ecadlib rules show` prints the effective rules.

Plans keep ecadlib's own parameter names internally; your house names (`[parameters.names]`) are applied at the outputs: the Altium 365 push, DbLib export and sync, KiCad export, and the A365 drift check.

**Accuracy and limits.** On synthetic libraries in four house styles (classic Altium SchLib/PcbLib, glued names in a DbLib, MPN-named Altium 365 list, KiCad), inference gets 34 of 34 settings right, and parts planned with the inferred file come out named like the existing ones. Not checked: no real customer library has been scanned and nothing written was opened in Altium; IPC-7351 names from the generator are approximate; name case (upper/lower) is not inferred; `.mdb` DbLib data is not read. Because the current folder's `ecadlib.toml` is used when the input folder has none, a stray file there will change output; pass `--rules` to be explicit.

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

---

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
* Run the lifecycle watcher and `sourcing` with real Nexar/DigiKey/Mouser credentials and confirm the response shapes (prices, spec attribute names, DigiKey substitutions).
* Add an `easyeda2kicad` conversion step for the `available_easyeda` bucket.
