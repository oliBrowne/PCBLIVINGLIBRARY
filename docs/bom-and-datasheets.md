# BOMs and datasheets

Reading BOMs, the datasheet reader and BOMs straight from an Altium project.

On this page:

* [`bom_triage.py`: BOM triage](#bom_triagepy-bom-triage)
* [Integration inputs and outputs: `bom_inputs.py`, `kicad_dblib.py`, `parts_export.py`](#integration-inputs-and-outputs-bom_inputspy-kicad_dblibpy-parts_exportpy)
* [`datasheet.py`: datasheet reader (step 1 of the design)](#datasheetpy-datasheet-reader-step-1-of-the-design)
* [`altium_project.py`: BOM from an Altium project, and where-used](#altium_projectpy-bom-from-an-altium-project-and-where-used)

## `bom_triage.py`: BOM triage

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

  Default order: `nexar,digikey,mouser,jlcpcb_search,easyeda`. The official APIs come first; each is skipped with a
  "not configured" note until its key is set. None of them hands out symbol or footprint geometry, so they identify
  the part (maker, description, datasheet link, package, and the LCSC number when LCSC sells it) and the EasyEDA
  route or the datasheet reader supplies the CAD data.

  | provider | official? | auth needed | what it gives |
  |---|---|---|---|
  | `nexar` | yes (Octopart / Altium supply API) | OAuth2 client credentials (`NEXAR_CLIENT_ID/SECRET` or `NEXAR_SUPPLY_TOKEN`), GraphQL, quota-limited | MPN match, maker, description, best datasheet, LCSC number from LCSC's offer |
  | `digikey` | yes (Product Information API v4) | OAuth2 client credentials (`DIGIKEY_CLIENT_ID/SECRET`) | MPN match, maker, description, datasheet, package |
  | `mouser` | yes (Search API v1) | `MOUSER_API_KEY` | MPN match, maker, description, datasheet |
  | `jlcpcb_search` | **no**: anonymous JSON POST to `jlcpcb.com/api/overseas-pcb-order/v1/shoppingCart/smtGood/selectSmtComponentList` (from easyeda2kicad 1.0.1) | none | MPN → LCSC, only when no official source found the LCSC number |
  | `easyeda` | **no**: `GET https://easyeda.com/api/products/{LCSC}/components` with `Referer: https://easyeda.com/` | none | the symbol, footprint and 3D data (the only free source of ready CAD data) |
  | `snapmagic` | partner token only, no self-serve public API | | stub |
  | `samacsys` | account plus Library Loader or partner access, no documented public REST API | | stub |

  * The two undocumented endpoints are off by default (`[providers] unofficial = false`): triage, the librarian's
    EasyEDA source and the UI leave them out, and parts come from your libraries, the KiCad libraries, the standard
    packages and the datasheet reader. `ecadlib init` asks whether to use them (`--unofficial` answers yes); set
    `[providers] unofficial = true` in `ecadlib.toml` to opt in.
  * `ecadlib sources-check` asks both unofficial endpoints about known parts (NE555DR, LM358DR, RP2040) and checks the
    converter still gives the right pad and pin counts. `.github/workflows/sources-canary.yml` runs it every Monday and
    keeps the raw answers as an artifact, so a change on their side shows up as a red run, not as broken user parts.
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

## Integration inputs and outputs: `bom_inputs.py`, `kicad_dblib.py`, `parts_export.py`

```
ecadlib triage board.kicad_sch --lib ... -o out/tri          # KiCad schematic, hierarchical sheets followed
ecadlib triage board.xml ...                                 # KiCad netlist / BOM XML (or the .net S-expression netlist)
ecadlib kicad-dblib out/lib/plan.json -o out/kicad --name mylib --export [--driver SQLite3]
ecadlib export-parts out/lib/plan.json --format csv|json|xlsx -o parts.csv
python3 -m tests.test_integration_io
```

* **BOM inputs** (every one gives the same lines `bom_triage` and the librarian already use):
  * `.kicad_sch` (KiCad 6 to 9): sub-sheets are followed, and a sheet file used twice counts twice with each instance's own references (KiCad 7+ `instances` paths, KiCad 6 root `symbol_instances`). Power symbols and parts marked "exclude from BOM" are left out, "Do not populate" becomes DNP, the units of a multi-unit part count once, and identical parts are grouped (`R1, R2, R30`, quantity 3). Custom fields (`MPN`, `Mfr Part #`, `LCSC`, ...) are matched like CSV headers; when sheets use different names for the same field, each part takes the first one it has. KiCad 5 `.sch` files get a clear "save it in KiCad 6+" message.
  * KiCad netlist / BOM exports: the XML from `kicad-cli sch export netlist --format kicadxml`, `sch export python-bom` and Eeschema's BOM generator, and the S-expression `.net`. Picked by content, so other `.xml` files are not taken for one; an XML with a DOCTYPE or ENTITY is refused.
  * `.xlsx` without openpyxl: when openpyxl is missing, the workbook is read with `zipfile` and ElementTree (shared and inline strings, sparse cells, numbers written the same way as before). pandas is still a dependency.
  * CSV: comma / semicolon / tab / pipe and UTF-8 (with or without BOM), cp1252, latin-1 and UTF-16 were already detected; they are now pinned by fixture files in `tests/fixtures/bom_inputs/`.
  * `[bom] columns` may give a list of house names for a field (`mpn = ["Herst.-Nr.", "Mfr Part #"]`): the first one present in a BOM is used. The named headers also help find the header row below title rows.
  * Altium ActiveBOM `.BomDoc` / `.PrjPcb` / `.SchDoc` were already read ([BOM from an Altium project](#altium_projectpy-bom-from-an-altium-project-and-where-used)).
* **Checked with real KiCad:** `tests/fixtures/kicad_sch/` is a hierarchical schematic (one sheet used twice, a nested sheet in a sub-folder, a 2-unit part, a power symbol, a DNP part and an excluded test point). KiCad 7.0.11's `kicad-cli` exported it as XML, S-expression and python-bom netlists, and all three read the same lines as our schematic reader. `test_kicad_cli_agrees_with_our_schematic_reader` repeats that whenever `kicad-cli` is installed and skips otherwise.
* **KiCad database library** (`kicad-dblib`): `mylib.sqlite` has one table per category with `Part ID` (key), `Symbol`, `Footprint` and the fields `kicad-export` puts on each symbol (house names from `[parameters] names`). `mylib.kicad_dbl` holds the KiCad 7+ settings: `meta`, `source` (`type` odbc, `connection_string` `Driver={...};Database=${CWD}/mylib.sqlite`, or `--dsn`), `cache`, and one library per table mapping every column to a field. `sym-lib-table` gets a `mylib-db` row of type `Database`, and its other rows are kept. Symbol and footprint ids point at `mylib:<name>` from `ecadlib kicad-export --name mylib` (`--export` runs it first). Parts that are held or have no symbol are listed in `kicad_dblib.json`.
  * Verified: the JSON keys (`source.connection_string`, `source.timeout_seconds`, `cache.max_age`, `visible_on_add`, `inherit_properties`, ...), `${CWD}` and the `Database` plugin type all appear in KiCad 7.0.11's `_eeschema.kiface`, and the SQLite tables answer the queries KiCad makes (`SELECT * FROM "table" WHERE "Part ID" = ?`).
  * **Not verified:** KiCad has not opened the database library itself, because no SQLite ODBC driver is installed here. On Windows, install the "SQLite3 ODBC Driver" (ch-werner.de, which is the default `--driver`). On Linux, install libsqliteodbc (driver `SQLite3`). On macOS, install sqliteodbc.
* **Parts table** (`export-parts`): one row per library part (held parts only with `--include-held`). The columns are `parts_export.COLUMNS`: Part ID, Status, Category, Folder, Description, Value, Manufacturer, Manufacturer Part Number, Datasheet, Supplier 1/2 and their part numbers, Lifecycle, Altium symbol/footprint refs and files, KiCad lib ids, 3D height, release check, hold reasons, sign-off and approvers (from the approvals policy), sources, and Other Parameters. New columns are only ever added at the end. CSV is UTF-8 with BOM, XLSX stores every cell as text, and both neutralise formula cells. JSON is `{"format": "ecadlib-parts", "version": 1, "columns": [...], "parts": [...]}` with Other Parameters as an object. Workspace: `export_parts(bom, fmt)` and `kicad_dblib(bom)`, also available as MCP tools.

## `datasheet.py`: datasheet reader (step 1 of the design)

```
ANTHROPIC_API_KEY=... python3 -m ecadlib.datasheet <datasheet.pdf | https://...pdf> --part <MPN> \
        [--package "<hint, e.g. RGT VQFN-16>"] [--pages 30-33] [--model claude-opus-5-5] [--fetch] -o out/datasheet
python3 -m ecadlib.datasheet <pdf> --part <MPN> --from-response out/datasheet/<MPN>.response.json -o out/datasheet  # re-check offline
python3 -m tests.test_datasheet
```

Claude reads the package drawing and pin table and returns every dimension as printed (min/nom/max plus unit), with the PDF page and figure it came from, through a structured-output schema derived from `package_schema.json`. The tool converts inches and mils to mm, then runs schema validation, `footprint_gen.validate`/`build`, sanity rules (pin count vs pin table, pitch x pins vs body, min <= nom <= max, unit plausibility) and symbol checks (unique pins, 1..N plus EP). Anything that fails goes in `needs_review`; nothing is corrected. Outputs: the package spec, the symbol JSON, a provenance sidecar, a review checklist (.md) and the raw response. The PDF is treated as untrusted data. Exit code 0 when clean, 2 when something needs review, 1 on error.

### A whole BOM in one batch

```
ecadlib keys set ANTHROPIC_API_KEY               # stored for ecadlib only, never an environment variable (docs/api-keys.md)
ecadlib datasheet estimate out/librarian/plan.json          # tokens and $ before anything is sent
ecadlib datasheet batch out/librarian/plan.json [--no-wait] [--no-batch] [--part P] [--yes]
ecadlib datasheet batch examples/sample_bom.csv -o out/librarian --lib ...   # librarian + batch in one go
ecadlib librarian BOM ... --datasheet-batch [--datasheet-no-wait]           # the same, as a librarian option
python3 -m tests.test_datasheet_batch
```

Every plan part that still has no symbol or footprint goes to the reader. Its datasheet link comes from the BOM's datasheet column, every link the official part APIs gave in triage (Nexar's best datasheet and the other copies in its document collections, such as Octopart's and DigiKey's; DigiKey's and Mouser's links; `src_datasheet` and `src_datasheet_more` in the triage table), the plan's `ComponentLink2URL`, a link a source found (KiCad symbol, EasyEDA `lcsc.url`) or LCSC's `https://www.lcsc.com/datasheet/<C-number>.pdf`, tried in that order. Each link is downloaded (stored under `<cache>/datasheets/` with its URL, SHA-256, size and page count) and must start with `%PDF`; an HTML viewer page is skipped. A link this machine cannot reach is sent as a URL document for the API to fetch, and the part is flagged. Only the pages for the part's package are sent first (`[datasheet] page_budget`, see *Fewer pages* below); with `page_budget = 0`, a PDF over `[datasheet] auto_pages` pages (default 40), or over the request limits, is cut to the pages whose text mentions the package outline, land pattern or pin table, plus pages 1 to 3. Page numbers in the outputs are mapped back to the original file. `--pages` chooses the pages yourself.

All parts go into one Message Batch (half price), one request per part with `custom_id` = part name. The batch is polled, the JSONL results are collected, and each part is built through the librarian's writers. Every result is marked `verify`, so the release check holds it until a person has looked; LLM output is never released automatically. The release check then runs again. The batch id is kept in `<out>/datasheet/batch_state.json`, so `--no-wait` or an interrupted run collects the same batch next time instead of paying twice. A batch create is never retried after an ambiguous failure. Errored or expired requests are sent again on the next run. `--no-batch` makes one Messages call per part instead, with the server-side refusal fallback; the Batches API does not accept that parameter.

* **Result cache:** answers that parsed are stored under `<cache>/datasheet_results/`, keyed by PDF SHA-256 + model + schema version (a hash of the output schema and prompt, so new `package_schema.json` families invalidate it) + part/package/pages/effort. Re-runs cost nothing. Refusals and truncated answers are not cached.
* **Errors and retries:** 429, 5xx and 529 (overloaded) are retried with exponential backoff, honouring `retry-after`. 401, 402/billing, 403, 404 (model) and 413 (too many pages) fail at once with what to do. Every request is logged to `<out>/datasheet/api_log.jsonl` (status, request-id, usage, a body summary with PDFs as size and hash). The key is never logged.
* **Estimate:** with a key, input tokens come from `/v1/messages/count_tokens`. Without one, the estimate is rough: pages x 3,000 tokens. Prices are $ per MTok from the claude-api model table, with the batch at half price. `[datasheet] max_cost_usd` (default $10 per run; 0 = no cap) refuses a run whose worst case (every answer at `max_tokens`) is higher, even with `--yes`; `--max-cost-usd N` changes it for one run.
* **Self-check:** compares the extraction with its own footprint name (pin count, EP, pitch, body and EP size) and with family norms (standard pitches; pitch, body and pin-count ranges per `package_type`). Disagreements are listed as `self-check:` review items. A family with no norms gets the generic checks, and the sidecar says so.
* **Workspace / MCP:** `Workspace.datasheet_estimate(bom)` and `datasheet_batch(bom, sync, wait, parts)`; MCP tools `datasheet_estimate` and `datasheet_batch`. The tool submits and returns by default; call it again to collect.

**Not tested live:** no API key is available here, so no successful extraction or batch has run. With an invalid key the live API returned 401 `authentication_error` for messages, count_tokens, batch create and batch retrieve, and the client reported each one correctly. The tests use a fake HTTP layer in the documented Messages, count_tokens and Batches shapes. Datasheet hosts (LCSC and the others) are blocked from this sandbox.

### Fewer pages, a model per part, and what it cost (`ds_route.py`)

```
ecadlib datasheet pages DS.pdf --part AZ1117CH-3.3TRG1 --package SOT-223-3_TabPin2   # pages + model a read would use
python3 -m tests.test_ds_route
```

```toml
[datasheet]
page_budget = 6          # pages on the first try (0 = the whole PDF, as before)
route = "single"         # single: `model` for every part | ladder: start cheap, move up when the checks fail
ladder = ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"]
escalate = true          # retry with more pages / the next model when an answer is incomplete or fails
max_attempts = 4         # most answers paid for per part
zoom_drawings = false    # also send the drawing pages as close-ups (two half-page pictures, ~260 dpi; ~9,000 tokens a page)
```

* **Pages:** every PDF longer than `page_budget` is cut to the pages that matter for *this* part: the package drawing and land pattern that name its package (from the footprint hint, or from the ordering-table row with its MPN, e.g. `AZ1117CH-3.3TRG1` sits under `SOT223`, `BQ771800DPJR` gives the TI drawing code `DPJ`), the pin table, the ordering information and the drawing's next page. Tables of contents, tape-and-reel and revision pages count little. When the answer is incomplete (unresolved dimensions, missing pins, a page cited outside the pages sent) the next try sends about three times as many pages, then the whole PDF. A PDF without a text layer is sent whole. When the PDF has bookmarks, a page under a bookmark that names a drawing, land pattern, pin table or ordering table ("Table 82. LQFP32 - Mechanical data", "62.5 LQFP100") scores for it, and a bookmark naming another package keeps its pages out; chapter-wide bookmarks ("Mechanical, Packaging, and Orderable Information") do not count, since they also hold tape-and-reel pages. `ecadlib datasheet pages` shows the bookmark behind each pick. The ordering table (TI's Package Option Addendum, read by column with pdfplumber when it is installed, `pip install 'ecadlib[pdf]'`, else from its text lines) gives this MPN's pin count: it fills a pin count the read left blank, and a different one is flagged for review. The package's dimension table (ST's "Symbol | millimeters Min Typ Max" grid, NXP and Nexperia's unruled "Unit A A1 bp ... / max / nom / min" rows, Diodes' tables) is read by column in code too, on the pages that were sent. Each dimension the model gives is matched to its row by the drawing's symbol on the page it cited (or the next or previous page): a number that differs is flagged for review and never changed, and a blank the table fills is filled, with a review line. TI and ADI print the numbers on the drawing itself, so for them the model's reading stands alone.
* **Model:** with `route = "ladder"` a part starts on the rung that suits its package: two- and three-terminal parts (chip passives, SOD, SOT-23, SOT-223) on the first, SOIC/SOT-23-6/QFN/DIP up to 32 pins on the middle one, more pins, BGA/LGA/CSP, connectors and unknown packages on the top one; a datasheet over 40 pages moves a part up one rung. An answer that fails datasheet's own checks (schema, sanity, pin table, self-check) or is refused goes to the next model; the top model with every page is the last try, and its answer is kept with its review items. An explicit `--model` means one model.
* **Cache:** every answer is cached under its PDF hash + model + pages + schema version, so a re-run replays the route without paying: a cheaper model's failed answer is cached as a failed step, never as the part's answer. In a batch, parts that need another step go into the next batch round (with `--no-wait`, the next run).
* **Cost:** each answer's tokens and USD (from the API usage block: input, cache write 1.25x, cache read, output, half price in a batch; the Haiku 5.5 rate above 100K prompt tokens) go to `<out>/datasheet/cost.jsonl`. The librarian's `review.md` lists them per part, `ecadlib status` (and the MCP `status` tool) sums them per model, and `ecadlib datasheet estimate` prices each part on the model its route starts on, with the worst case of the further steps.

**Why the default stays `route = "single"` on Claude Opus 5.5.** Page picking is on by default: it cuts what is sent without changing who reads it, and widening covers a miss. A cheaper model is a quality trade nobody has measured here yet (no API key in this sandbox), so the ladder is opt-in; run `ecadlib eval` with `--model` per rung and switch when its pass rate holds.

**Measured (offline, 24 parts in 23 public datasheets from gitlab.com: STM32G0B1, TJA1051, AZ1117C, USBLC6-2, BQ25750, ATmega8, KEMET/Samsung/Yageo/TDK passives, ...):** the pages each part needs (drawing for its package, pin table, land pattern, ordering table; 56 needs, marked by hand) are on the first try for 54 of 56 with 132 pages sent, against 49 of 56 with 503 pages before (whole PDF up to 40 pages, the old picker above); the second step covers 55 of 56, and the miss is a Samsung 84-page catalogue that the whole PDF covers. Rough input (3,000 tokens a page, no key to count) falls from about 1.51 M to 0.40 M tokens; rough first-try USD for the 24 parts, synchronous: $10.61 before, $5.04 with picked pages, $2.20 with the ladder (9 parts start on Haiku 5.5, 10 on Sonnet 5.5, 5 on Opus 5.5). These are page and price estimates, not measured answers: no extraction ran live, and the page labels were made on the same datasheets the picker was tuned on.

## `altium_project.py`: BOM from an Altium project, and where-used

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
