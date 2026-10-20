# Altium and Altium 365

Every way ecadlib gets parts into Altium: Altium 365, database libraries, .SchLib/.PcbLib files, the install package and the ecadlib menu.

On this page:

* [`altium365.py`: Altium 365 workspace exporter (Nexar Design API)](#altium365py-altium-365-workspace-exporter-nexar-design-api)
* [Signing in to Altium 365: `ecadlib nexar signin`](#signing-in-to-altium-365-ecadlib-nexar-signin)
* [`altium_export.py`: Altium Database Library](#altium_exportpy-altium-database-library)
* [`altium_lib.py`: Altium `.PcbLib` / `.SchLib` writer (pure Python)](#altium_libpy-altium-pcblib--schlib-writer-pure-python)
* [`kicad_import.py`: KiCad library parts to Altium](#kicad_importpy-kicad-library-parts-to-altium)
* [Plugging into the tools: `kicad_pcm.py` and `altium_pkg.py`](#plugging-into-the-tools-kicad_pcmpy-and-altium_pkgpy)

## `altium365.py`: Altium 365 workspace exporter (Nexar Design API)

```
python3 -m ecadlib.altium365 plan examples/sample_bom.csv --triage out/triage_sample_bom.csv \
        --models <dir with SchLib/<ref>.SchLib and PcbLib/<ref>.PcbLib> -o out/altium365/plan.json
ecadlib nexar signin                      # once; or NEXAR_TOKEN=<portal token> for a day
python3 -m ecadlib.altium365 workspaces
python3 -m ecadlib.altium365 push out/altium365/plan.json --workspace <url> [--dry-run]
```

`plan` turns each unique part into one managed workspace component under `Components/<Category>`, with the same parameters as the DbLib rows (manufacturer, MPN, value, supplier, links). `push` then, for each part:

1. looks the component up by name (`desLibrary.components(where: {name: {eq}})`);
2. if it exists, updates its parameters with `desUpdateComponentRevisionParameters` (this is the "keep the library current" path);
3. otherwise uploads the `.SchLib` and `.PcbLib` to `files.nexar.com/File/Upload` and calls `desReleaseComponent`, which releases symbol, footprint and component in one call, using the workspace's default naming schemes and lifecycles for each content type.

Parts without Altium model files are planned as `needs_altium_models` and skipped on release (they still get parameter updates if they already exist in the workspace). `--dry-run` sends nothing and writes the mutation inputs it would send.

**Only checked parts go up.** In a plan, `ready` only means the model files were found. Before `push` uploads a part's files (a new part, or a new revision of a changed one), it runs the release check (`ecadlib release-check`) on those files as they are now. A part that fails the check is skipped as held, and its failures are listed in the push report, unless a person signed them off with release anyway (`--allow`). `dblib-sync`, `altium-pkg` and `kicad-export` run the same check. A part from a doubtful source (an EasyEDA conversion, a pinout from a family table, a KiCad symbol or footprint picked by a near name, a datasheet read) is held until someone looks at it and releases it anyway.

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

**Getting the `.SchLib`/`.PcbLib` files:** `altium_lib.py` ([Altium library writer](#altium_libpy-altium-pcblib--schlib-writer-pure-python)) now writes them. With `--models out/altium_models` the plan has **4 of 14 parts `ready`** (CAP-0402-100nF, TPS62133RGTR, CH340C, RES-0603-10k) and the dry-run push releases those 4 and skips 10. Those files have not been opened in Altium Designer, so the first real release is also their first real test. The fallback is still Altium's KiCad importer on `out/footprints/ecadlib.pretty`.

### Altium without 365: `altium_dblib_sync.py` (`.DbLib` / `.SVNDbLib`)

```
ecadlib dblib-sync out/librarian/plan.json --to D:/libs/ecadlib [--svn [--svn-url URL] [--commit]] [--prune] [--win-dir D:\libs\ecadlib] [--dry-run]
```

For teams that keep a Database Library or an SVN Database Library instead of an Altium 365 workspace. It takes the same `plan.json` that `altium365 push` uses and keeps one library folder in step with it: `SchLib/<Library Ref>.SchLib` and `PcbLib/<Footprint Ref>.PcbLib` (one model per file), `<name>.xlsx` (one sheet per category, every cell text), a flat `<name>.csv` for diffs (both under an ASCII spelling of `--name`, since Altium can't open a workbook such as `Bibliothèque.xlsx`: `Bibliotheque.xlsx`; a folder an older version wrote is moved over), the `.DbLib` (or `.SVNDbLib` with `--svn`) and `ecadlib_sync.json`, which records the rows and files the tool wrote. Each part is reported the way `push` reports it: `added`, `updated` (with the changed columns or model files), `up_to_date`, `held` (left exactly as it is), `skipped` (no models), or `error` (a model that does not read back through `altium_lib`'s readers, or does not contain the ref its row names; it never reaches the table). Re-runs rewrite only what changed. Columns added by hand to the sheet are kept, and the plan's `cleared` fields are blanked. Rows and files that drop out of the plan are reported as `not_in_plan`. `--prune` deletes them, but only the ones this tool wrote. An existing `.DbLib` is merged rather than replaced: edits made in Altium's DbLib editor survive, and missing tables and field maps are appended. With `--svn`, `--commit` runs `svn add`/`delete`/`commit` on exactly the touched files when `svn` is on PATH and `--to` is a working copy; otherwise it prints those commands. svn is not installed here, so that path is tested with a fake runner. On the sample plan (`out/librarian/plan.json`) the result was 11 added and 3 held; a second run wrote nothing.

**Verified** against two real files fetched from GitHub: `ryankurte/altium-library/electronpowered.dblib` (Excel) and `mechanart/AltiumLibrary/mechanart_oshw.SVNDbLib`. The Excel fast connection writes `LibraryDatabaseType=Microsoft Excel`, `LibraryDatabasePath=<file>` and `DatabasePathRelative=1`, and its tables are `<sheet>$`. `[Library Path]`/`[Footprint Path]` are mapped as reserved fields. An SVNDbLib has the DbLib layout plus `SVNRepositoryURL=` and `FileRepositoryIsRelative=0` in `[DatabaseLinks]`, and its `LibrarySearchPath` lists the repository's symbol and footprint folders, separated by `;`. Altium's docs say that SVNDbLib model paths are "relative to the root of the repository" and that Access/Excel paths can be stored "relative to the Database Library/SVN Database Library file".

**Unverified** (there is no Altium or Windows here): that the `.xlsx` opens through ACE `Excel 12.0 Xml`, since the real example used `.xls` with `Excel 8.0`; that `SVNRepositoryURL` should be the working-copy URL (the URL `--to` is checked out from) so `SchLib\x.SchLib` resolves; that `LibrarySearchPath` is where Altium keeps the "Models Location" folders; the `.SVNDbLib` extension case (Altium writes `.SVNDbLib`); and the INI encoding (UTF-8 here, though only ASCII names are expected).

## Signing in to Altium 365: `ecadlib nexar signin`

A token made on the Nexar portal lasts 24 hours, so pasting one (`ecadlib keys set NEXAR_TOKEN`) means doing it again
every day. Instead, make your own free Nexar app once and let ecadlib keep itself signed in:

1. On [portal.nexar.com](https://portal.nexar.com/applications) sign in with the Altium account of your Altium 365
   workspace and create an application with **Design** access (the Design API is free; nothing here uses the paid
   supply API).
2. On the app's **Authorization** tab, add `http://localhost:3000/login` as a redirect URI (ecadlib listens there on
   your own computer for the one browser sign-in; `NEXAR_REDIRECT_URI` picks another port).
3. Run `ecadlib nexar signin`, paste the app's **client ID** and **client secret** when asked (once), and allow access
   in the browser that opens.

`ecadlib nexar status` says whether you are signed in and until when the current access token is valid (never the
token); `ecadlib nexar signout` forgets the sign-in. Every Altium 365 command, the web UI and the Altium menu use the
sign-in without change.

**How it works.** Nexar accepts only confidential apps (an app with a secret), so ecadlib can't ship one shared
"Sign in with Altium" app: its secret would be public. With your own app, ecadlib does the standard authorization
code sign-in with PKCE and the scopes `openid user.access design.domain offline_access`. `offline_access` gives a
refresh token; when the access token is within five minutes of running out, ecadlib swaps the refresh token for a new
one (and keeps the new refresh token when Nexar rotates it). The app's id and secret (`NEXAR_CLIENT_ID`,
`NEXAR_CLIENT_SECRET`, the same names the supply lookups use) and the sign-in (`NEXAR_SIGNIN`) go into ecadlib's key
store ([API keys](api-keys.md)): encrypted for your Windows account, never in the environment, a config file or a log.
A pasted `NEXAR_TOKEN` still works when there is no sign-in. If a refresh fails (the app was deleted, the secret
changed), commands say so and ask you to run `ecadlib nexar signin` again.

**Checked** (2026-10-20) against `identity.nexar.com/.well-known/openid-configuration`: authorization code, refresh
token, PKCE S256, the `offline_access` scope, and only `client_secret_basic` / `client_secret_post` client
authentication. **Tested offline** with a fake token endpoint and a fake browser (`tests/test_nexar_signin.py`); the
first live sign-in needs a real Nexar app.

## `altium_export.py`: Altium Database Library

```
python3 -m ecadlib.altium_export examples/sample_bom.csv --triage out/triage_sample_bom.csv -o out/altium [--win-dir C:\ecadlib\altium]
```

This writes one table per category (Capacitors, Connectors, Crystals, Diodes, ICs, Resistors, Transistors) to `parts.sqlite`, and the same tables as sheets to `parts.xlsx`. It also writes two `.DbLib` files.

* **Columns:** `Part Number` (key), `Library Ref`, `Library Path`, `Footprint Ref`, `Footprint Path`, `Description`, `Value`, `Comment`, `Manufacturer`, `Manufacturer Part Number`, `Supplier 1`, `Supplier Part Number 1`, `Supplier 2`, `Supplier Part Number 2`, `ComponentLink1Description/URL` (LCSC page) and `ComponentLink2Description/URL` (datasheet).
* **Generic symbols:** passives use a generic symbol (`Resistor`, `Capacitor`) with `Comment = "=Value"`, and their Part Number is synthesised (`RES-0603-10k`).
* **Footprints:** footprint refs reuse the KiCad footprint names. `Footprint Path` points at `PcbLib\<footprint>.PcbLib` and `Library Path` at `SchLib\<symbol>.SchLib` (one file each, relative to the DbLib). This export does not write those files; `ecadlib dblib-sync` on a librarian plan writes the DbLib with the files beside it, and leaves held parts out.

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

### Getting footprints and symbols into Altium (options researched before the library writer was built)

* **Altium's own KiCad importer** (Import Wizard): the docs list `*.kicad_mod`, `*.kicad_sym`, `*.lib`, `*.kicad_pcb`, `*.kicad_sch` and `*.kicad_pro` "up to KiCad version 8.x". This is the least-effort route. It is also why the generator emits the KiCad 7 file format rather than KiCad 9/10.
* **Altium scripting** (DelphiScript/JScript/VBScript inside Altium, using `PCBServer.CreatePCBLibComp`, `IPCB_Pad`, …): this can build PcbLib footprints directly from the same JSON. Altium also ships an **IPC Compliant Footprint Wizard / Batch Generator** that takes package dimensions from a spreadsheet. I recall that tool from memory and did not verify it here; mapping `package_schema.json` onto its template could be the cleanest Altium-native route.
* **Python libraries** (versions from PyPI on 2026-10-13):
  * **`altium-monkey`** 2026.9.22 (wavenumber-eng, **AGPL-3.0**, alpha, CPython 3.12–3.14, manylinux wheels): its README claims reading *and writing* of `.SchLib` and `.PcbLib` ("create schematic symbols and PCB footprints"). This is the only Python option found with write support, but the AGPL licence matters for an open-source tool's licensing.
  * **`pyaltiumlib`** 0.7.1 (ChrisHoyer): read-only. It extracts metadata and renders to SVG.
  * **`pyaltium`** 0.4.0 (pluots, 2022): read-only and stale.
  * **`olefile`** (BSD): generic OLE2 read/write, useful as a base for a writer.
  * **AltiumSharp** (C#, github.com/issus/AltiumSharp): its README support table says SchLib and PcbLib are **Write: Yes**. The licence is stated inconsistently on the page (MIT in the README, Apache-2.0 in the sidebar).

## `altium_lib.py`: Altium `.PcbLib` / `.SchLib` writer (pure Python)

```
python3 -m ecadlib.altium_lib pcblib ecadlib/package_specs/*.json -o out/altium_models/PcbLib [--density A|B|C] [--single lib.PcbLib]
python3 -m ecadlib.altium_lib schlib examples/symbols/*.json  -o out/altium_models/SchLib
python3 -m ecadlib.altium_lib dump out/altium_models/PcbLib/SOIC-8_3.9x4.9mm_P1.27mm.PcbLib
python3 -m ecadlib.altium365 plan examples/sample_bom.csv --triage out/triage_sample_bom.csv \
        --models out/altium_models -o out/altium365/plan.json      # -> 4 with models, 10 needs_altium_models
                                                                     #    add --models out/altium_models_kicad (KiCad parts, below) -> 14 with models
python3 -m ecadlib.release_check out/altium365/plan.json             # what push will hold back, and why
```

**Altium can now build these files itself.** Every library written here also gets a plain-text recipe beside it
(`<file>.ecadbuild`), and the install script lets Altium rebuild each library from it with its own scripting API
before installing it. See [docs/altium-native.md](altium-native.md) and `ecadlib altium-native`.

No dependencies beyond the standard library. `cfb.py` writes the OLE2 container (v3, 512-byte sectors, mini stream, DIFAT for files over ~7 MB). `altium_lib.py` builds the Altium streams inside it. The record layouts are ported from **AltiumSharp** (C#, Apache-2.0; attribution in the file header and `NOTICE`). `altium-designer-mcp` (GPL-3.0) was read for notes only, and none of its code is used.

What gets written:
* **Footprints** come from the same `footprint_gen.build()` output as the `.kicad_mod` files: SMD pads with rounded-rect corner %, F.SilkS to Top Overlay, F.Fab to Mechanical 13, F.CrtYd to Mechanical 15, and the pin-1 triangle as a region. Exposed-pad paste windows become Top Paste fills, and the EP's own paste is suppressed. Units are exact: 1 mil = 10000 internal units and 1 mm = 10^8/254. One footprint per file by default (Altium 365 releases one model per file). `SOURCE_DATE_EPOCH` makes output byte-reproducible.
* **Symbols** come from a small JSON pin list (`examples/symbols/*.json`): a rectangle-body IC with pins on the left and right with electrical types, plus generic 2-pin Resistor and Capacitor symbols. Each has a Designator and Comment, plus a footprint link to the PcbLib name.
* **Designator and comment text:** the reference becomes `.Designator` on Top Overlay and the value `.Comment` on Mechanical 13 (stroke font, KiCad's height, stroke width, rotation and justification; the anchor is shifted so a nominal 3-character designator and 8-character comment centre where KiCad centres them).
* **Pin graphics:** pins in `examples/symbols/*.json` take an optional `"shape"`: a KiCad shape name (`inverted`, `clock`, `inverted_clock`, `input_low`, `clock_low`, `output_low`, `edge_clock_high`, `non_logic`) or `{"inside_edge"|"outside_edge"|"inside"|"outside": symbol}` with Altium's IEEE symbols. `~{EN}` in a pin name becomes Altium's overline `E\N\`.
* **Display modes:** symbols can carry alternate (De Morgan) views, written as Altium 24 writes them.
* `CH340C.json` and `TPS62133RGTR.json` carry `"needs_datasheet_verification": true`. **Their pinouts were written from memory and must be checked against the datasheets before anything is released.** Their `cross_check` notes record the checks made since (CH340C pin 8 is OUT#). ecadlib does not read the flag: a `.SchLib` built from these files is held only by what the release check finds.

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
* Not written by the generator path: through-hole pads and 3D bodies (the KiCad import in [KiCad parts to Altium](#kicad_importpy-kicad-library-parts-to-altium) adds through-hole pads, arcs, keep-outs and 3D bodies) and `PrimitiveGuids`.
* Text: Altium is assumed to recalculate the cached text size (exact only for `.Designator`/`.Comment`). Not checked in Altium: signal-flow arrow direction, and the falling-edge clock written as clock plus active-low input. (De Morgan display modes were checked in AD26; see docs/altium-native.md.) Names over 31 characters are truncated as CFB storage names, and `SectionKeys` holds the mapping in the same way Altium does.

**Checked against real Altium libraries (2026-10-15, `tests/test_altium_interop.py`):** 553 Altium-saved `.PcbLib`/`.SchLib` files from the
Celestial Altium Library (github.com/issus/altium-library; its license forbids redistribution, so none is committed) read with our reader
and with `pyaltiumlib` 0.7.1: 3367 pads agree field for field (position, size, hole, slot, rotation, plating, mask/paste modes), every
record count matches the files' own `Header` counts, and 3765 tracks/arcs/regions/texts agree (region vertices to 0.01 mil). The only disagreements are pyaltiumlib's
(it swallows the record after a 3D body, so regions after a body go missing there). Byte statistics of 1139 real SMD pads show our 202-byte
pad blocks differ only where we set manual paste/mask expansion. Real files keep the STEP checksum signed in `Library/Models` and unsigned
in the body (as we write it), store Mechanical 17-32 as layer byte 72 plus a v7 layer id (the reader now decodes it to Altium's id 66+n;
the writer and `[layers]` still stop at Mechanical 16), and put text cp1252 can't hold in a `|%UTF8%KEY=...||` twin before `|KEY=?`
(read, and now written by the SchLib writer; binary pin names stay cp1252). Our files read back the same through pyaltiumlib
(test skipped when it is not installed). Nothing here was opened in Altium Designer.

## `kicad_import.py`: KiCad library parts to Altium

```
python3 -m ecadlib.kicad_import batch examples/kicad_import_parts.json -o out/altium_models_kicad [--step]
python3 -m ecadlib.kicad_import footprint Package_TO_SOT_SMD:SOT-23 [--as NAME] [--height MM] [--step] -o out/altium_models_kicad
python3 -m ecadlib.kicad_import symbol Transistor_FET:AO3401A [--as NAME] [--footprint SOT-23] [--param K=V] -o out/altium_models_kicad
    (local inputs work too: X.kicad_mod, Lib.kicad_sym:Name, Name.kicad_sym; --offline uses only out/http_cache)
python3 -m tests.test_kicad_import
```

Fetches official footprints, symbols (KiCad 7-9, including `.kicad_symdir` and `extends`) and STEP models from gitlab.com/kicad/libraries and writes one `.PcbLib`/`.SchLib` per part. It handles through-hole pads (round and slotted, plated and NPTH), rect/roundrect/oval/circle pads (custom pads become an anchor pad plus copper, mask and paste regions with their outline, or their bounding rectangle when they use arcs or curves; trapezoids become rectangles; per-pad mask margins and tented pads carry over), arcs and circles, keep-out zones, and hidden or stacked power pins. Every part gets a 3D body: an extruded body over the fab outline by default, with the height taken from the KiCad STEP model, else a per-package table, else `--height`; `--step` embeds the STEP model instead. Without a KiCad model, a generated STEP is embedded instead of the box ([STEP models](footprints-and-symbols.md#step_genpy-package-shaped-3d-models-step); `--box` keeps the box). `examples/kicad_import_parts.json` maps the 10 sample parts that had no models; with it the plan goes from 4 to 14 ready. Outputs are not committed because the KiCad libraries are CC-BY-SA 4.0 (with a design-use exception).

Reference and Value text become `.Designator` and `.Comment`; pin shapes map onto Altium pin symbols, `~{X}` names onto overlines, and De Morgan body styles onto Altium display mode 1. Multi-unit symbols become multi-part.

Checked here: text records, pin symbols, through-hole pads, arcs, 3D bodies, keep-out regions and embedded STEP models re-encode byte for byte against Altium-saved files from AltiumSharp's TestData (except for colour, opacity and thermal-relief settings this writer doesn't set). All outputs read back with pyaltiumlib, `altium_lib dump` and olefile in strict mode, and every symbol pin has a pad with the same number. **Not checked in Altium:** slot orientation, through-hole paste, the extruded-body checksum, and stacked hidden pins. FTSH-105 uses KiCad's generic 1.27 mm 2x05 SMD header as a stand-in, so compare it with the Samtec drawing.

## Plugging into the tools: `kicad_pcm.py` and `altium_pkg.py`

```
ecadlib kicad-pcm out/librarian/plan.json --out out/pcm --name mylib [--kicad 7|8|9|10] [--prefix PCM_] [--pkg-version 1.2.0]
ecadlib kicad-pcm out/kicad --out site/kicad --base-url https://example.com/kicad   # + repository.json / packages.json
ecadlib kicad-pcm --plugin --out out/pcm                    # the ecadlib button and window for KiCad's PCB editor
ecadlib altium-pkg out/librarian/plan.json --out D:/libs/mylib --name mylib   # or a folder of .SchLib/.PcbLib
python3 -m tests.test_tool_packages
```

**KiCad.** The PCM package (`ecadlib kicad-pcm`) is frozen: it still works, but gets no new features. `kicad-pcm`
packages a plan (or a `kicad-export` folder) as a Plugin and Content Manager *library* package:
a zip with `metadata.json`, `symbols/<lib>.kicad_sym`, `footprints/<lib>.pretty/` and `3dmodels/<lib>.3dshapes/`.
In KiCad: Plugin and Content Manager > Install from File... > the zip. KiCad's PCM (read in its source,
`kicad/pcm/pcm_task_manager.cpp`, `pcbnew`/`eeschema` lib table loaders, 7.0 and master) extracts each folder to
`<3rd party>/<folder>/<identifier, '.' -> '_'>/` and adds every library to the global tables as `PCM_<lib>`, so the
symbols' Footprint fields say `PCM_<lib>:<footprint>` and the footprints' models
`${KICADn_3RD_PARTY}/3dmodels/<id>/<lib>.3dshapes/...` (n = `--kicad`; `--prefix ""` if you cleared KiCad's prefix).
`--kicad 7` writes KiCad 7 files; the default 8 keeps the export's KiCad 8 files (KiCad 8, 9, 10). The version is
`--pkg-version`, or kept while the content is unchanged, else today's date, so a re-run changes no file. With
`--base-url` it also writes `repository.json` and `packages.json` (sha256, sizes) for Manage Repositories; KiCad only
fetches repositories over http(s) (`kicad_curl_easy.cpp`), so a `file://` URL is refused.
`--plugin` writes a PCM *plugin* package: an **ecadlib** button in the PCB editor's top toolbar (and Tools > External
Plugins > ecadlib: add part / check project BOM / open review queue) that opens the ecadlib window, the same flow as
the ecadlib menu in Altium, drawn in the same dark colours: Add part from MPN (MPN, manufacturer optional, a live
lookup, then the footprint placed in the middle of the board), Check project BOM (the schematic's lines: already in
your library / a source can supply / needs a footprint, then Later or Fill the library now, and the report on
request) and Open review queue. The window does not block KiCad. It runs `ecadlib kicad-menu run-job` in the background
(`ecadlib/kicad_menu.py`) and reads its `progress.ini` / `result.ini`, as the Altium dialog does. Per project,
`<project>/ecadlib-work/` is the workspace (one parts list for every part added or filled, so one plan makes the whole
library) and `<project>/ecadlib/` the KiCad library, added to the project's tables as `${KIPRJMOD}/ecadlib/...`.
KiCad gives plugins no menu or toolbar slot in the schematic editor; its Tools > Generate BOM can run
`ecadlib kicad-menu check-bom "%I"` there, which checks the netlist against the library and opens the report.
Tried in KiCad 7.0.11: toolbar button, BOM check, fill and add part with the footprint placed.

**Altium.** `altium-pkg` writes a folder that opens in Altium as is: `SchLib/`, `PcbLib/`, `<name>.LibPkg` (an
integrated library package listing every `.SchLib`/`.PcbLib` as `SchLib\x.SchLib`; compile it to an `.IntLib`),
`<name>_Install.PrjScr` + `.pas` (DelphiScript: `InstallDatabaseLibrary`, `BuildIntegratedLibrary`,
`InstallFileLibraries`, `UninstallAll`, using `IntegratedLibraryManager.InstallLibrary` and working out its folder
with `GetRunningScriptProjectName`), `ALTIUM_README.txt`, and from a plan the DbLib, workbook and CSV of
`dblib-sync`. Workspace: `tool_packages(bom)` (MCP tool `tool_packages`) writes both into `packages/<bom>/`.

**Paths.** Nothing written holds an absolute path: KiCad tables use `${KIPRJMOD}`, the `.kicad_dbl` `${CWD}`, PCM
packages `${KICADn_3RD_PARTY}`; the LibPkg, the DbLib rows (`[Library Path]`) and the script use relative Windows
paths, and the Excel DbLibs (`dblib-sync` and now `ecadlib dblib`) say `DatabasePathRelative=1`. The SQLite DbLib of
`ecadlib dblib` keeps `--win-dir` (an ODBC string has no relative form).

**Checked here** (2026-10-16, KiCad 7.0.11, 17 tests in `tests/test_tool_packages.py`):
* metadata, packages.json and repository.json validate against KiCad's `pcm.v1.schema.json` (the copy KiCad 7.0.11
  installs, identical to gitlab.com/kicad 7.0's; master only adds `category` and `runtime`) with jsonschema, and the
  built-in checks always run; a bad identifier is caught by both. Field use matches two packages from KiCad's
  official repository metadata (SparkFun, 4ms).
* Installed the way the PCM does it into a throwaway 3rd-party folder and config dir (`KICAD_CONFIG_HOME`,
  `KICAD7_3RD_PARTY`) with the global table rows the PCM adds: on a board in a project with no table of its own,
  KiCad's DRC library check finds all 4 footprints as `PCM_lib:*`, identical to the library (control: an empty config
  dir -> all 4 missing); `kicad-cli pcb export step` loads the SOIC-8 model through `${KICAD7_3RD_PARTY}` (11 solids;
  control without the variable: 1, the board); `kicad-cli sym export svg` / `sym upgrade` load the installed symbol
  library and every Footprint field names a footprint in `PCM_lib`. All 68 spec footprints converted back from
  PcbLib and written in KiCad 7 syntax load in pcbnew. A crafted `../` entry is refused, as KiCad does.
* The action plugin package installs, KiCad's own `pcbnew.LoadPlugins` imports it from the 3rd-party plugins folder
  with no traceback and its `register()` reaches `register_action` with the right name and category.
* Altium: LibPkg sections and keys copied from Altium-saved files (particle-iot/hardware-libraries, sub-folder paths as in
  blueintegral/Level), PrjScr as in Altium-Designer-addons/scripts-libraries; every listed document exists and reads
  back; every footprint a symbol links to is in the package's PcbLibs; every DbLib row resolves from the DbLib's folder;
  all of this again after moving the whole output to another folder (and KiCad's library check on the moved project).
* **Not checked:** nothing was opened in Altium Designer: not that it compiles the LibPkg, runs the script (written
  from the documented scripting API and public scripts, never executed), or opens the relative DbLib. KiCad's PCM GUI
  was not run (no display; the install was replayed from its source); in headless pcbnew `register_action` asserts
  `PgmOrNull()`, so the plugin showing in the menu is unverified; KiCad 8/9/10 not run (the optional CI `kicad` job
  now also runs these tests on KiCad 8).

### An "ecadlib" menu inside Altium: `altium_menu.py`

```
ecadlib altium-menu build --out D:/ecadlib-menu --workspace D:/ecadlib-ws --to D:/libs/ecadlib [--win-dir D:\ecadlib-menu]
ecadlib altium-menu add-part LM358DR --manufacturer TI --workspace D:/ecadlib-ws --result-file r.ini   # what the menu runs
ecadlib altium-menu check-bom D:/boards/x/Board.PrjPcb | fill Board.PrjPcb | queue | run-job JOBFOLDER
python3 -m tests.test_altium_menu
```

`build` writes a DelphiScript script project (`ecadlib_menu.PrjScr` + `.pas`) with three procedures that a menu or
toolbar button can call: **AddPartFromMPN** (input boxes for the MPN and an optional manufacturer; the part is added
to `<workspace>/uploads/altium-menu/parts.csv`, the librarian runs on that list, and a ready part is synced into the
library folder with `altium-pkg`/`dblib-sync`, or released to Altium 365 when the rules set `[altium365] workspace`;
the dialog shows "ready / held + why"; the script then uninstalls/installs the DbLib and refreshes the installed
libraries, or for A365 says to refresh the Components panel), **CheckProjectBOM** (the focused project's `.PrjPcb`
path from `GetWorkspace.DM_FocusedProject.DM_ProjectFullPath`; ecadlib reads its BOM (`altium_project.py`), triages
it, marks which lines the library already has (the DbLib's parts table and ready plan items), writes and opens an
HTML report, and offers "Fill the library now?", which runs the librarian on the project) and **OpenReviewQueue**
(opens the held parts at `http://127.0.0.1:<port>/#libcard`, starting `ecadlib ui --workspace W --no-browser` detached when nothing
answers; refuses a port that serves another workspace or another program).
Settings live in `ecadlib_menu.ini` next to the script (workspace, library, name, command, hidden, timeout_s, port,
offline; relative paths start at that folder; a rebuild keeps the file). Built on Windows without `--command`,
`command=` is the Python that ran the build (`"<sys.executable>" -m ecadlib`), so an install in a venv is found; a
rebuild fills an empty `command=` (older builds) and changes nothing else. `ecadlib doctor --altium-menu <folder>`
checks that `command=` runs ecadlib. Without an AI key, parts no free source fully describes come back held with the
reason (the dialog and the review queue say which), and nothing held reaches the library. A held part whose reasons a
person may override (not a missing file, not a change waiting for `ecadlib part approve`) gets **Release anyway** at
the bottom left of the dialog: it lists why the part is held, then asks why it is fine, and releases it the way the
review queue does (`release-check --allow`, your name and the reason kept in the plan's `allow`, revision 1 in the
change-control ledger) before it puts the part into the library. The reason box starts with "Checked it: fine as it is", so Enter releases; an emptied box releases nothing. A missing 3D height does not hold a part (`[release] height = "hold"` makes it): the dialog adds a note to check the 3D body. Check this project BOM reads the
project's device sheets (`[DeviceSheetN]`; one it cannot find is a warning) and lists multi-channel sheets once per
channel (R1_CH1 ...); a Content Vault component without an MPN parameter uses its Comment as the MPN when it reads as
one. Workspace `altium_menu(win_dir)` and the MCP
tool `altium_menu` write it into `<workspace>/altium_menu/` with `workspace=..`.

**The ecadlib dialog.** The entries work in one dark, borderless dialog (`ecadlib_dialog.pas` + `.dfm`,
`ecadlib/altium_forms.py`) styled like Altium's own: **Check project BOM** shows "Checking...", the line being looked
up and a progress line, then the count ("24 lines") with a row per kind (already in your library, a source can supply,
needs a footprint, not fitted) and **Later** / **Fill the library now**; the fill shows the librarian part by part,
then "N of M ready" with the held parts. **Add part from MPN** asks for the MPN and manufacturer in the same dialog,
then lists where ecadlib looked, what it took and the release check, and reinstalls the library so the part shows in
the Components panel. A `TTimer` reads `jobs\<id>\progress.ini` (done, total, text, rows) while ecadlib runs and
`result.ini` (`big`, `rows`, `row1`.. as `label|value|ok`) at the end, so Altium keeps painting. Only the review
queue (for a held part) and the full BOM report (**Open the full report**, `action=open`, files in the workspace only)
open in the browser. `dialogs=0` in the .ini brings back Altium's plain message boxes. Run in Altium Designer 26.10:
script forms, colours, the timer and modal results work. Not used, and why: a web view (`TWebBrowser` is an
undeclared identifier to scripts) and a panel that stays open (any open script form, a server panel or a floating
one, keeps the script running, and Altium then runs no other script: "Another script executing now").

**How the script runs ecadlib.** No user text goes on a command line: each click makes `jobs\<id>\` next to the
script and writes `request.ini` with `TIniFile` (action, mpn, manufacturer, project, config). It then calls
`RunApplication('wscript.exe //B //NoLogo "<dir>\ecadlib_run.vbs" "<dir>\ecadlib_run.cmd" "<job>"')`; the .vbs runs
`cmd.exe /s /c ""<runner>" "<job>""` with window style 0 (no console) and the .cmd finds ecadlib (`command=` from the
.ini via `command.txt`, else `ecadlib` on PATH, else `py -3 -m ecadlib`, else `python -m ecadlib`), runs
`ecadlib altium-menu run-job "<job>"` and writes `exit.txt` (and `notfound.txt` when there is no ecadlib). The
script waits for `exit.txt` (`Sleep` + `FileExists`, the pattern of Altium-Designer-addons' XIA_Utils.pas; after
`timeout_s` it asks whether to keep waiting) and reads `result.ini` (`[result]` status, message, fix, install, offer,
...) with `TIniFile`. `result.ini` is UTF-16 with a BOM, which Windows' profile API reads whatever the code page;
values are one line; finished job folders older than 14 days are removed. Every failure becomes one dialog line with a fix (ecadlib missing, project not saved, no
NEXAR_TOKEN, port taken, ...); ecadlib's output is in `jobs\<id>\log.txt`. `hidden=0` runs the .cmd in a visible
console instead. No key is in any file: keys stay in environment variables.

**Keep working while ecadlib works.** The dialog is modal, so Altium waits while it is open. **Keep working** (or
the dialog's X) closes it and the job runs on in the background. The script writes `background.txt` in the job and
adds the job to `waiting.txt` next to the script. When the job ends, `ecadlib_run.cmd` runs
`ecadlib altium-menu wake "<job>"`, which calls the running Altium's `X2.EXE` with
`-RScriptingSystem:RunScript(ProjectName=<prj>|ProcName=<dir>\ecadlib_menu.pas>FinishBackgroundJob)` (8.3 short
paths: a space in a script path makes Altium start a second copy). Altium then opens the dialog on the result,
builds and installs the library (a native build locks Altium for those seconds) and writes `finished.txt`. While
Altium is busy with another script, wake tries again every minute, five times. When Altium is closed or never takes
it, the next menu entry asks whether to put the finished job in. Its output is in `jobs\<id>\wake.txt`.

**Install.** The quick way: close Altium and run `ecadlib altium-menu install-menu --out <folder>` (the setup
page's Add to Altium does this on Windows). It merges the ecadlib commands and the four menu insertions into Altium's
own menu file, `%APPDATA%\Altium\Altium Designer {GUID}\DXP.RCS` (backed up next to it first; other customisations
are kept; `--remove` takes ours out), so the "ecadlib" menu sits in the top menu bar before Window. Altium must be
closed because it writes that file when it closes. The menu entries run the script project by its path, so it need
not be a global project. By hand: Preferences > Scripting System > Global Projects > Install from file > `ecadlib_menu.PrjScr`; then
right-click a menu or toolbar > Customize > [Scripts] and drag the three procedures onto a menu (once per editor).
With `--win-dir` (or when built on Windows) it also writes `ecadlib_menu.DXPPrf`, a preferences file (zip of `DXP.RCS`
+ `IncludedFiles.ini`, the format of Altium-Designer-addons' Altium-saved `UI customizations/Menu_SVN`) that adds an
"&ecadlib" menu before Window in the schematic, PCB, SchLib and PcbLib editors (`ScriptingSystem:RunScript` with
`ProjectName=<dir>\ecadlib_menu.PrjScr|ProcName=<dir>\ecadlib_menu.pas>AddPartFromMPN`, TR0124) and lists the entries
under [Custom]: Preferences > Load, tick only System > Customizations. The README in the folder warns that loading
customisations may replace your own (save them first).

**Altium calls used** (`ALTIUM_API` in the module; the test fails on any other identifier): from Altium's scripting
references (TR0120 DelphiScript, TR0124 Server Process, TR0135 System, TR0136 Integrated Library, TR0140 Workspace
Manager; the 2008 PDFs in github.com/Altium-Designer-addons/scripting-reference, page numbers in the module):
`RunApplication`, `ShowMessage`, `ShowError`, `ConfirmNoYes`, `GetWorkspace`, `DM_FocusedProject`,
`DM_ProjectFullPath`, `DM_ProjectFileName`, `IntegratedLibraryManager.InstallLibrary / UninstallLibrary /
InstalledLibraryPath / AvailableLibraryCount`, `ResetParameters`, `AddStringParameter`, `RunProcess`
(`IntegratedLibrary:RefreshInstalledLibraries`, AllLibraries=True), `TIniFile` (Create, ReadString, ReadInteger,
WriteString), `TStringList` (Add, SaveToFile), `FileExists`, `DirectoryExists`, `MkDir`, `ExtractFilePath`, `Now`,
`Random`, `Randomize`, `IntToStr`, `Trim`, `Pos`, `Copy`, `Length`, `UpperCase`. Not in those references but used by
public Altium scripts: `InputBox` (BrettLMiller PolygonReFitBO.pas), `GetRunningScriptProjectName`, `Sleep`,
`FormatDateTime`, `Application.ProcessMessages` (Altium-Designer-addons XIA_Utils, QuickSilk, AutoPlaceSilkscreen).

**Checked here** (16 tests in `tests/test_altium_menu.py`, offline): the PrjScr and .ini parse as INI, every file is
CRLF ASCII with no secrets; the .pas tokenises (a small Pascal tokenizer: comments, `''` strings, `#13#10`), every
Begin/Try has its End, every identifier is declared in the unit or on the allow-list, each Function sets Result, the
menu entries are parameterless Procedures (the checker itself is tested on 7 broken units); the RCS follows the
Menu_SVN grammar, every Link names a defined entry and every ProcName a procedure of the .pas; the command lines,
parsed with CommandLineToArgvW rules and cmd's `/s /c` quote rule, give back the exact paths for folders with spaces,
`&`, parentheses and backslashes; `run-job` on a request.ini written in cp1252 (as TIniFile does) adds a ready
capacitor into a DbLib (UTF-16 result.ini, install= the .DbLib), holds NE555DR with the reasons, checks a two-sheet
.PrjPcb (1 line already in the DbLib table, 5 not; HTML report opened; offer to fill; fill: 6 held, offline), routes
to Altium 365 when the rules say so (and says to set NEXAR_TOKEN when it is missing), and the queue action really
starts `ecadlib ui` on a free port and opens `#libcard`.
**Not checked:** nothing was run inside Altium Designer (none here, no Windows): not that Altium loads the script
project, that `InputBox` / `GetRunningScriptProjectName` / `Sleep` / `Application.ProcessMessages` behave as in the
public scripts that use them in the Altium version you have, that `RunApplication` starts `wscript.exe` with that
command line, that the .DXPPrf imports (format copied from an Altium-saved file, never loaded), or that the DbLib
reinstall shows new rows without restarting. `cmd.exe` and `wscript.exe` were not run; the .cmd and .vbs were checked
as text and by re-implementing their quoting rules.
