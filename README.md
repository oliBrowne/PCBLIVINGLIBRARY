# ecad-librarian: "AI living library" prototypes

These are small Python prototypes for an open-source tool that manages ECAD components for KiCad and Altium. The design has three parts:

1. An LLM reads a datasheet mechanical drawing and fills `ecadlib/package_schema.json`.
2. A deterministic generator turns that JSON into an IPC-7351B land pattern.
3. A triage step works out which BOM lines already exist locally, which can be pulled from a public source, and which need a generated footprint.

## How you use it

There are three ways in, and all of them call the same actions in `ecadlib/app.py`:

1. **Local web UI** (for most people). It runs on your own machine, so your API keys and library stay local.

   ```
   python3 -m ecadlib.ui --lib path/to/your/kicad/libs
   ```

   A browser tab opens at `http://127.0.0.1:8765`. Drop in a BOM (CSV or XLSX) to get a triage table showing what is already in your library, what a part source can supply, and what needs a footprint. Click **Generate…** on a missing part to fill in its datasheet dimensions, preview the IPC-7351B land pattern, and approve it into the workspace library. Re-triage picks it up. **Export Altium DbLib** writes the SQLite/XLSX/.DbLib set. The Altium 365 push button stays disabled until the Nexar exporter lands.
2. **MCP server**, so Claude or another agent can do the same work in conversation (for example, "triage this BOM and draft footprints for anything missing").

   ```
   claude mcp add ecadlib -- python3 -m ecadlib.mcp_server --lib /path/to/libs
   ```

   Its tools are `workspace_info`, `triage_bom`, `preview_footprint`, `approve_footprint`, `export_altium_dblib` and `list_example_specs`.
   ![Triage table](docs/ui-triage.png)
   ![Footprint preview](docs/ui-footprint.png)

3. **CLI modules**, for scripts and CI. They are described below.

Everything the UI and MCP server write goes to one workspace folder (default `./ecadlib_workspace`) with `uploads/`, `reports/`, `footprints/ecadlib.pretty/` (approved footprints plus their source JSON), `altium/` and `http_cache/`. Both servers use only the standard library on top of the existing requirements. The web UI binds to 127.0.0.1 and refuses cross-origin POSTs and any file path outside the workspace.

Requirements: Python 3.13 with `requests`, `pandas` and `openpyxl`. `jsonschema` is optional; when it is installed, specs are validated against the schema. `easyeda2kicad` was installed only so its source could be read for the endpoints; nothing imports it at runtime.

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
│   ├── altium_lib.py         # pure-Python .PcbLib/.SchLib writer (+ minimal reader, `dump`)
│   ├── cfb.py                # Compound File Binary (OLE2) v3 writer used by altium_lib
│   ├── assets/pcblib_template.json.gz   # Library header + FileVersionInfo from an Altium-saved file (see NOTICE)
│   ├── httpcache.py          # disk cache + per-host rate limit around requests
│   └── sexpr.py              # tiny KiCad S-expression reader (parse only)
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
python3 -m ecadlib.footprint_gen gen examples/packages/*.json -o out/footprints/ecadlib.pretty [--density A|B|C]
python3 -m ecadlib.footprint_gen compare <mine.kicad_mod> <reference path or URL>
```

* **Package types:**
  * `chip`: 2-terminal R/C/L. IPC uses table 3-5 for 0603 and larger, and table 3-6 for parts smaller than 0603.
  * `gullwing`: dual-row SOIC/SOP/TSSOP. Fine-pitch side fillets apply at a pitch of 0.625 mm or less.
  * `qfn`: 4-sided, with an optional exposed pad. Its paste is split into an N×N window grid, and the coverage is a parameter.
* **Equations:** they are implemented in `ipc_land()`, and the module docstring shows them:
  `Z = Lmin + 2Jt + √(CL²+F²+P²)`, `G = Smax − 2Jh − √(CS²+F²+P²)`, `X = Wmin + 2Js + √(CW²+F²+P²)`.
  * S uses the IPC RMS tolerance.
  * F = 0.05 and P = 0.025.
  * Z and X are rounded up to 0.01 mm, and G is rounded down.
  * The toe, heel, side and courtyard-excess values come from the per-density `FILLETS` table.
  * Pad width is clamped to `pitch − 0.2 mm` to leave a solder-mask web.
* **Output:**
  * Layers:
    * F.Fab: body outline, with a pin-1 chamfer on ICs, plus `${REFERENCE}`.
    * F.SilkS: kept 0.2 mm clear of pads, with a KiCad-style filled pin-1 triangle.
    * F.CrtYd: the union of pads and body plus the courtyard excess.
  * Text: `REF**` and the value.
  * Pads are roundrect (25% corner ratio, capped at 0.25 mm).
  * The `descr` field carries Z, G and X plus the source.
  * The format is `version 20221018` (KiCad 7). KiCad 8 and 9 load it, and Altium's KiCad importer accepts files "up to KiCad 8.x".
* **Sanity checks:**
  * Each output is re-parsed to confirm the S-expression is balanced.
  * The QFN generator warns when the gap from a pad to the exposed pad is under 0.2 mm. It does so for the VQFN-16, where the gap is 0.17 mm.
* **Not verified:** none of the generated files has been opened in KiCad, because there is no KiCad or kicad-cli in this sandbox. Running `kicad-cli fp upgrade` or opening the files in the footprint editor is the next step.

### Pad-size deltas vs KiCad official library (live fetch from gitlab.com, `master`)

All values are in mm. Each delta is mine minus KiCad. My inputs are typical datasheet or JEDEC dimensions; they are not the generator YAML KiCad used, which is why the chip parts differ slightly.

| Footprint | mine pad (w×h @x) | KiCad pad | Δ size | Δ x | courtyard mine / KiCad |
|---|---|---|---|---|---|
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

## 5. `altium_lib.py`: Altium `.PcbLib` / `.SchLib` writer (pure Python)

```
python3 -m ecadlib.altium_lib pcblib examples/packages/*.json -o out/altium_models/PcbLib [--density A|B|C] [--single lib.PcbLib]
python3 -m ecadlib.altium_lib schlib examples/symbols/*.json  -o out/altium_models/SchLib
python3 -m ecadlib.altium_lib dump out/altium_models/PcbLib/SOIC-8_3.9x4.9mm_P1.27mm.PcbLib
python3 -m ecadlib.altium365 plan examples/sample_bom.csv --triage out/triage_sample_bom.csv \
        --models out/altium_models -o out/altium365/plan.json      # -> 4 ready, 10 needs_altium_models
```

No dependencies beyond the standard library. `cfb.py` writes the OLE2 container (v3, 512-byte sectors, mini stream, DIFAT for files over ~7 MB). `altium_lib.py` builds the Altium streams inside it. The record layouts are ported from **AltiumSharp** (C#, Apache-2.0; attribution in the file header and `NOTICE`). `altium-designer-mcp` (GPL-3.0) was read for notes only, and none of its code is used.

What gets written:
* **Footprints** come from the same `footprint_gen.build()` output as the `.kicad_mod` files: SMD pads with rounded-rect corner %, F.SilkS to Top Overlay, F.Fab to Mechanical 13, F.CrtYd to Mechanical 15, and the pin-1 triangle as a region. Exposed-pad paste windows become Top Paste fills, and the EP's own paste is suppressed. Units are exact: 1 mil = 10000 internal units and 1 mm = 10^8/254. One footprint per file by default (Altium 365 releases one model per file). `SOURCE_DATE_EPOCH` makes output byte-reproducible.
* **Symbols** come from a small JSON pin list (`examples/symbols/*.json`): a rectangle-body IC with pins on the left and right with electrical types, plus generic 2-pin Resistor and Capacitor symbols. Each has a Designator and Comment, plus a footprint link to the PcbLib name.
* `CH340C.json` and `TPS62133RGTR.json` carry `"needs_datasheet_verification": true`. **Their pinouts were written from memory and must be checked against the datasheets before anything is released.**

**Verified here** (2026-10-13; `python3 -m tests.test_offline` runs 11/11, 5 of them new):
* **Container:** `olefile` reads all 13 outputs with `raise_defects=DEFECT_INCORRECT` and logs nothing. 200 random trees and an 8 MB file that needs DIFAT read back byte-exact. 18 real Altium files from AltiumSharp's TestData and the Nexar demo were rewritten through `cfb.to_bytes`, and each came back with an identical stream tree.
* **Records, byte for byte against real Altium-saved files** (AltiumSharp TestData): re-encoding the decoded records with this writer gives
  * tracks 190/190, fills 5/5 and regions 4/4 identical;
  * SMD pads: 12 identical, and 2106 that differ only at pad main-block byte 103, a paste-mask flag that real files set to 1 and this writer leaves at 0;
  * SchLib pins: 63 identical. 106 more differ only in an empty swap-id string, and 4 that use pin symbols were skipped.
* **Third-party reader:** `pyaltiumlib` 0.7.1 parses all 13 files. Coordinates, sizes, rounded-rect shape and corner %, paste expansion and pin names/types all match the input. One example is SOIC-8 pad 1 at (-2.4725, 1.905) mm, 1.965×0.58 mm, corner 50%. Its only warnings are "unsupported RecordID 45/46/48" (the footprint-link records), and it prints the same warnings for the real Nexar demo `.SchLib`. Footprints and symbols rendered to SVG looked right.
* **Structure:** compared with an Altium Designer 2025-era TestData library and the 2014-era Nexar sample, the stream set is the same, apart from `PrimitiveGuids` (optional, and also absent from the Nexar sample) and `Models/` (no 3D bodies).
* **Not run:** AltiumSharp's own reader. It needs .NET 10, and the proxy blocked the .NET download.

**Assumed (not checked in Altium Designer, which was never run here):**
* That Altium Designer and Altium 365 accept these files. Nothing was opened in Altium Designer. The `Library/Data` header is copied from an Altium-saved library rather than synthesized, because altium-designer-mcp's notes report that Altium rejects synthesized ones with "Catastrophic failure whilst loading section Library". That header still carries the source library's view settings.
* Pad format: the 202-byte main block and the 596-byte per-layer size/shape block match the TestData files. Newer Altium versions write a longer full-stack block. The byte-103 flag above is assumed to be a cache that Altium recomputes.
* EP paste is suppressed with a negative manual paste expansion, which is assumed to read as "no paste". KiCad draws the paste windows as rounded rectangles, and they are written here as rectangular fills.
* `LayerKindMapping` is empty, so Mechanical 13 and 15 have no layer types and appear as plain mechanical layers. Solder mask and paste use "from rule" except on the EP.
* SchLib: `Weight` = records + 1 and the pin flag bits were inferred from real files.
* Not written: through-hole pads, PCB text and designators, 3D bodies, `PrimitiveGuids`, multi-part symbols and pin graphics (dots, clocks). Names over 31 characters are truncated as CFB storage names, and `SectionKeys` holds the mapping in the same way Altium does.

---

## Exact commands run (in `/mnt/project-files/ecad-librarian`)

```
python3 -m pip install easyeda2kicad                       # 1.0.1, used only to read its endpoint code
python3 -m ecadlib.footprint_gen gen examples/packages/*.json -o out/footprints/ecadlib.pretty
for each of 6 footprints:  python3 -m ecadlib.footprint_gen compare out/footprints/ecadlib.pretty/<fp>.kicad_mod \
      https://gitlab.com/kicad/libraries/kicad-footprints/-/raw/master/<Lib>.pretty/<fp>.kicad_mod   > out/footprints/compare_vs_kicad_official.txt
python3 -m tests.test_offline                               # 11/11 ok (incl. Altium 365 plan/push, Nexar schema, PcbLib/SchLib writer)
python3 -m ecadlib.bom_triage examples/sample_bom.csv --lib examples/kicad_lib --lib out/footprints -o out/triage_sample_bom
python3 -m ecadlib.bom_triage examples/sample_bom.csv --lib examples/kicad_lib --lib out/footprints \
        --providers digikey,mouser,nexar,snapmagic,samacsys -o out/triage_sample_bom_stub_providers_only
python3 -m ecadlib.altium_export examples/sample_bom.csv --triage out/triage_sample_bom.csv -o out/altium
export SOURCE_DATE_EPOCH=1791936000                         # reproducible PcbLib dates
python3 -m ecadlib.altium_lib pcblib examples/packages/*.json -o out/altium_models/PcbLib
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
* Verify the CH340C and TPS62133RGTR pinouts against their datasheets.
* Add through-hole pads, a designator string and 3D STEP bodies to the PcbLib writer.
* Add an `easyeda2kicad` conversion step for the `available_easyeda` bucket.
* Wire an LLM extraction step that outputs `package_schema.json` along with the source page and figure, keeping `unresolved` dimensions for human review.
