# The librarian: BOM to released library

The librarian run, held parts, sign-off, library rules, safe updates and keeping the library current.

On this page:

* [`librarian.py` and `release_check.py`: one command from BOM to library](#librarianpy-and-release_checkpy-one-command-from-bom-to-library)
* [`approvals.py` and `dedupe.py`: sign-off and duplicates](#approvalspy-and-dedupepy-sign-off-and-duplicates)
* [`rules.py` and `ecadlib init`: one file for your library conventions](#rulespy-and-ecadlib-init-one-file-for-your-library-conventions)
* [Same part, same answer: cross-checks and repeatable reads (`crosscheck.py`, `llm/sampling.py`)](#same-part-same-answer-cross-checks-and-repeatable-reads-crosscheckpy-llmsamplingpy)
* [Updates never break a library: `migrate.py`, `library.lock.json` and `ecadlib rebuild`](#updates-never-break-a-library-migratepy-librarylockjson-and-ecadlib-rebuild)
* [`lifecycle.py`: lifecycle watcher (keep the library current)](#lifecyclepy-lifecycle-watcher-keep-the-library-current)
* [`upstream.py`: upstream change alerts](#upstreampy-upstream-change-alerts)
* [`upkeep.py`: weekly upkeep (one issue or PR)](#upkeeppy-weekly-upkeep-one-issue-or-pr)

## `librarian.py` and `release_check.py`: one command from BOM to library

```
python3 -m ecadlib.librarian examples/sample_bom.csv --lib examples/kicad_lib --lib out/footprints -o out/librarian \
        [--sources kicad,easyeda,standard,datasheet] [--allow PART] [--refresh [PART ...]] [--refresh-index] [--offline]
python3 -m ecadlib.release_check out/librarian/plan.json [--allow PART] [--unallow PART]
python3 -m tests.test_librarian; python3 -m tests.test_release_check
```

Triages the BOM, converts parts already in your `--lib` libraries, and for anything still missing tries in order:
* **kicad**: the official KiCad libraries. A name index is built once from the GitLab tree API and cached. Symbols match the MPN by exact name, KiCad `x` wildcards, or the MPN minus an ordering suffix; the footprint comes from the symbol's Footprint property, the BOM footprint, or a footprint named after the MPN. R/C/L/LED use `Device:*` plus the footprint by package.
* **easyeda**: by LCSC number, through a native converter (`easyeda_convert.py`).
* **standard**: `ecadlib.stdparts` ([standard parts](footprints-and-symbols.md#stdpartspy-standard-parts-without-claude)), offline: chip passives and standard packages built from checked dimensions, plus symbols for R/C/L/LED/diodes and listed transistor/LDO families. Never placed ahead of kicad; fills only a missing half.
* **datasheet**: `ecadlib.datasheet`, when a datasheet URL is known and `ANTHROPIC_API_KEY` is set (never with `--offline`: it is network and it bills, so the part is held with "--offline: datasheet not read"). `--datasheet-batch` reads every part that reaches this step as one Message Batch ([datasheet reader](bom-and-datasheets.md#datasheetpy-datasheet-reader-step-1-of-the-design)).

Outputs in `out/librarian/`: `models/`, a `plan.json` for `altium365 push` recording each part's source, `review.md/.csv` (what came from where and what needs a person), and the release-check report. Re-running skips finished parts unless `--refresh` is given.

The release check runs at the end and works on any plan. A part is **held** (and `altium365 push` skips it) unless: every pin has a pad and every pad a pin (mechanical and mounting pads excepted); a footprint link in the symbol names the part's footprint; the footprint passes `footprint_check` (KiCad-library parts: pad overlap, courtyard and pin 1); a 3D height is present and plausible for the package; Manufacturer, MPN, datasheet link and Description are filled in; the files exist and read back; and nothing from a source that needs a second look is unreviewed (datasheet-reader output, an EasyEDA conversion, a pinout from a family table, a KiCad symbol or footprint picked by a near name). A person releases such a part with `--allow` / Release anyway; picking another symbol or footprint for that half (`ecadlib resolve`, the web UI's Pick) also drops the note once the pick is applied, for an EasyEDA conversion once neither half comes from EasyEDA (datasheet-reader output always needs `--allow`). `--allow PART` overrides the failures present at that moment and is recorded in the plan; missing or unreadable files cannot be overridden. Every publisher that reads a plan (`altium365 push`, `dblib-sync` / `altium-pkg`, `kicad-export`, `kicad-dblib`, `export-parts` and exporter plugins) runs the check again on the files as they are and holds a part that fails it, whatever `status` the plan says; `kicad-export` (and `kicad-dblib --export`, for the part's database row) keeps the version it exported before of a released part it now holds, unless the library file was edited by hand since.

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

`choose` converts the part into `models/` the same way the librarian does. A footprint file another part uses is shared only when the pads are the same; otherwise the chosen footprint gets its own name (`LED_0603_1608Metric_LED_0805_2012Metric`), so the other part keeps its released pads. The part's symbol then links that name (its `.SchLib` is relinked in place, whatever source drew it); parts sharing that symbol file are re-checked with it. A KiCad symbol `choose` writes carries the KiCad libraries' CC-BY-SA credit, as the librarian's do. It then updates the item, recording "chosen by user" in `librarian.tried`, and `librarian_state.json`, so a librarian re-run keeps the choice. Finally it re-runs the release check on that item (and on the parts sharing a file it rewrote). The Python API (`held`, `show`, `candidates`, `choose`) returns plain dicts and lists.

Live run on a copy of the 14-part plan (2026-10-14):
* TYPE-C-31-M-12: top candidate `Connector:USB_C_Receptacle_USB2.0_16P` (pins match), now **ready**. With a symbol present, the stacked A1/B12 pads are warnings.
* X322512MSB4SI: the four 4-pin `Crystal_GND23/24` symbols match. `Crystal_GND24` was chosen, and it is now **ready**. Which pins are ground still has to be checked against the datasheet.
* FTSH-105-01-L-DV-K: the top candidates are the `PinHeader_2x05_P1.27mm` footprints (10 pads, match). With `_Vertical_SMD` chosen, the only hold left is the missing datasheet link.

## `approvals.py` and `dedupe.py`: sign-off and duplicates

```
ecadlib approve out/librarian/plan.json status
ecadlib approve out/librarian/plan.json approve --part CH340C [--role librarian] [-m "pinout checked"] [--user "Ann <ann@x.com>"]
ecadlib approve out/librarian/plan.json reject  --part AO3401A -m "pin 3 is the drain"
ecadlib dedupe  out/librarian/plan.json [--lib out/altium_models --lib out/altium/ecadlib_sqlite.DbLib ...] [--merge] [--bom BOM.csv]
python3 -m tests.test_approvals; python3 -m tests.test_dedupe
```

**Approvals.** Put an `approvals.json` (or `.toml`) next to the plan, for example `{"required": 2, "roles": ["librarian"], "users": {"ann@x.com": ["librarian", "hardware"]}}`. Other keys are `no_self_approval` (default true: whoever gave the `--allow` override cannot approve that part), `applies_to` (`all` or `allowed`), `parts` (glob patterns) and `reject_blocks`. With a policy, the release check marks parts that pass but are not yet signed off as `awaiting_approval`, and `altium365 push` releases only parts that are ready **and** approved. Without a policy approvals are recorded but not required; a current reject still holds the part (`awaiting_approval`: not pushed or exported) until the same person approves it again. Each decision is stored in the part's `approvals` list with who, when, role, comment and a hash of the part's `.SchLib`/`.PcbLib` bytes and key fields. If any of those change later, the approval goes stale and push holds the part again. Identity comes from `--user`, then `$ECADLIB_USER`, then `git config user.name/email`. Every decision, including refused ones, is appended to `approvals_audit.jsonl` next to the plan. `release-check --allow P --user U` records who gave the override. The librarian keeps approvals across re-runs.

**Duplicates.** `dedupe` compares the plan with existing libraries: `.SchLib`, `.PcbLib`, `.kicad_mod`, a `.DbLib` with its SQLite or Excel file, and the Altium 365 component list from a `check-a365 --state` file (it reads `a365_state.json` next to the plan automatically). It finds three kinds of match:
* the same MPN once packaging suffixes are stripped (`/TR`, `-REEL7`, `T&R`, `#TRPBF`, `(LF)(SN)`, `,115`). A suffix with no separator, like TI's `RGTR`/`RGTT`, is kept unless you add a pattern to `dedupe.json`;
* the same footprint geometry under another name (pads and courtyard, with `.PcbLib` and `.kicad_mod` compared directly);
* the same symbol pinout, for symbols with 3 or more pins.

It also reports footprints and symbols that share a name but differ. Each group gets a recommended keeper; `--keep` overrides it. `--merge` first saves the plan as `plan.json.pre-dedupe` (kept from the first merge; later merges save `plan.json.pre-dedupe-<UTC time>`), then rewrites plan references to the keeper. A duplicate part gets `duplicate_of`, and push skips it. Footprint and symbol references are pointed at the keeper's file. It also writes `dedupe_map.csv` (and, with `--bom`, a remapped BOM copy). Library files, DbLib rows and workspace components are only reported, never changed or deleted.

Live run on a copy of `out/librarian/plan.json` (2026-10-14), with `--lib out/altium_models out/altium_models_kicad out/footprints out/altium/ecadlib_sqlite.DbLib`:
* No MPN duplicates.
* One geometry duplicate: `R_0402_1005Metric` = `C_0402_1005Metric` in `out/footprints`.
* 13 name conflicts, where `out/altium_models` and `out/footprints` hold different versions of the same footprint name (e.g. SOIC-8 pads 1.95×0.60 vs 1.965×0.58 mm), plus three symbols that differ from older conversions.

All 13 of the librarian's `.PcbLib` files hash the same as their source `.kicad_mod`.

## `rules.py` and `ecadlib init`: one file for your library conventions

`ecadlib.toml` holds your naming conventions, required parameters, folder map, layer mapping, footprint density, source order, BOM column names and approval policy. Every tool reads it: the librarian, `altium_export`, `altium365`, `altium_dblib_sync`, `kicad_import`, `altium_lib pcblib`, `footprint_gen gen`, `release_check`, `upstream check-a365`, `kicad_export`, the web UI and the MCP server. Each CLI takes `--rules PATH`. Without it, the tool looks for `ecadlib.toml` in the input's folder and its parents, then in the current folder and its parents (a Workspace uses its own `ecadlib.toml`). Without any file, the outputs are byte-for-byte what they were before (a test checks this).

**Making the file (minimal setup).** `ecadlib init --from DIR [--from DIR ...] [--a365 parts.json] [--yes]` scans:

* `.SchLib`/`.PcbLib`: component and footprint naming patterns, parameter names and how often each is used, which mechanical layers carry the courtyard, assembly and 3D body, and the density (from IPC name suffixes, or from chip pad toe-to-toe compared with IPC-7351 A/B/C);
* `.DbLib` and its data (`.xlsx`/`.csv`/`.sqlite`; for `.mdb` only the field map is read);
* an Altium 365 part list (an `altium365` state file, or JSON in the shape `{name, folder, parameters, symbol, footprint}`): folders and parameters;
* KiCad `.kicad_sym`/`.pretty`, and BOM/parts spreadsheets (column names).

Names are pattern-mined per category (`RES_{package}_{value}_{tolerance}`, `R{package}_{value}`, `{mpn}` ...) with value style (`4K7`, `4.7k`, `4.7kΩ`) detected. The written file says, for every setting, where it came from and how confident the guess is. Confidence counts how much of the library shows the convention, not only how much of the evidence agrees: a setting backed by fewer than 3 parts, footprints or symbols, or a footprint layer seen in fewer than half of the footprints (one `.Designator` in 48 footprints), is low. Low settings are not written; they are listed as comments at the top of the file, and ecadlib's default stays. The courtyard is never learned onto a layer that holds the library's 3D bodies, and a `.Designator` on a mechanical layer is not proposed when the library has silkscreen on the overlay. `init` asks a question only where the evidence is split (the best pattern under 60% with a runner-up at 20% or more); `--yes` takes the best guess. If `ecadlib.toml` already exists, `init` shows a diff, writes the proposal to `ecadlib.toml.proposed` and changes nothing unless you confirm or pass `--force` (the old file is kept as `.bak`).

The UI has a **Library rules** card (type the folders to scan, review the proposals with their evidence, edit the TOML, save; replacing an existing file asks first and shows the diff). The MCP server has `propose_rules` and `save_rules`.

`ecadlib rules check [PATH]` validates a file (errors name the bad key and suggest the nearest valid one) and prints the effective rules; `ecadlib rules show` prints the built-in defaults.

Plans keep ecadlib's own parameter names internally; your house names (`[parameters.names]`) are applied at the outputs: the Altium 365 push, DbLib export and sync, KiCad export, and the A365 drift check. When you adopt house names in a library already synced or pushed, the next dblib-sync blanks the value under ecadlib's old name (the empty column and its DbLib field map stay) and the next push drops the old-named workspace parameter, so each field has one value.

**Accuracy and limits.** On synthetic libraries in four house styles (classic Altium SchLib/PcbLib, glued names in a DbLib, MPN-named Altium 365 list, KiCad), inference gets 34 of 34 settings right, and parts planned with the inferred file come out named like the existing ones. Not checked: no real customer library has been scanned and nothing written was opened in Altium; IPC-7351 names from the generator are approximate; name case (upper/lower) is not inferred; `.mdb` DbLib data is not read. Because the current folder's `ecadlib.toml` is used when the input folder has none, a stray file there will change output; pass `--rules` to be explicit.

## Same part, same answer: cross-checks and repeatable reads (`crosscheck.py`, `llm/sampling.py`)

A footprint read from a datasheet (any provider or model) is not accepted on one reading alone. Before the
footprint is written, `ecadlib.crosscheck` compares the critical numbers with an independent second source.
These numbers are package type, pin count, pitch, body length/width, lead span, exposed-pad size and the pin table.

| Source | When | Cost |
|---|---|---|
| Standard table (`stdparts` / `package_specs`) | the BOM package or the read's package name resolves to a standard table and at least 2 fields can be compared | free, offline |
| Second read | otherwise. It is a separate request with a different prompt that asks only for the critical numbers, with the same picked pages **in reverse order**, optionally on another model (`cross_check_model`) | one small request (`max_tokens` 8000), cached, logged in `cost.jsonl` (`verdict: "cross-check"`, `purpose: "cross_check"`), counted against `max_cost_usd` |

```toml
[datasheet]
cross_check = "auto"        # "auto" (default) | "always" | "off"
cross_check_model = ""      # "" = the model that made the first read
```

- `auto` (the default) always runs the free table check. It pays for a second read only when no table covers
  the package and the datasheet supplies the footprint (a part whose footprint came from KiCad/EasyEDA only has its
  symbol read). We chose this default because a wrong footprint costs a board spin, while the extra read costs a few
  cents and runs once per PDF/part/model because it is cached.
- `always` adds a second read even when the table agrees. `off` gives the behaviour before this change.
- Comparison is done in code. The tolerance is `max(abs, rel x value)`: pitch 0.01 mm; body and lead span 0.10 mm
  or 3 %; EP 0.10 mm or 5 %. Against a table, a value also agrees when the table nominal lies inside the read's
  printed min..max. Pins are compared by count, numbers and normalised names.
- **Agree**: the part is accepted, marked `cross_check: agreed`, and the report says which fields were checked.
  **Disagree**: the part is **held** and nothing is written. Both values go to the review (`review.md` section "Cross-checks
  of datasheet reads" and the datasheet report). Neither value is picked or averaged.
  **Unchecked** (the second read refused, was over budget, or had nothing comparable): the part is accepted with a review item.
- With `batch = true`, the first reads go in the batch and the second reads are sent synchronously afterwards.
- The single-read CLI (`ecadlib datasheet PDF --part`) is not cross-checked; the librarian and the eval are.
- Workspace `cross_checks(bom)` and MCP tool `cross_checks` list agreed / disagreed / unchecked parts of a librarian plan.

**Repeatable runs.** `[ai] temperature = "auto"` (default) sends `temperature 0` (plus a fixed `seed` on
OpenAI-compatible endpoints) only to models known to accept it. Recent Claude models (Opus 5.x / 4.7+, Sonnet 5.x)
and reasoning models (gpt-5, o-series, `*reasoner*`) get no sampling parameters, so **a default Claude request is
byte-identical to before** (the golden hashes in `tests/test_llm.py` are unchanged). A provider profile can set `sampling =
true|false`. A number forces that temperature, and `"off"` never sends one. Sampling is not part of the cache key,
so answers already paid for are kept. Cached answers, sorted output and content-derived GUIDs make repeat runs give the same files.

**Eval.** Each row records `cross_check` and a `files` fingerprint: the bytes for KiCad, and the read-back
records for Altium, so the `.PcbLib` header date/time does not count. The summary counts agreed/disagreed parts.
`ecadlib eval run ... --repeat 2` re-runs the set from the recorded answers and reports **Consistency**: the share
of cases with identical files and cross-check results. `ecadlib eval consistency A B` compares two result files
(exit 2 on any difference).

Tested offline only, with fake and recorded answers (`tests/test_crosscheck.py`). No live model of any provider was run.

## Datasheets you downloaded, and standard lands from a read (`[datasheet] folder`, `standard_land`)

Some makers' sites refuse downloads from scripts (analog.com and Nexperia did in the benchmark of 2026-10-18), and
some parts have no link at all. Download those PDFs by hand into one folder and point ecadlib at it:

```toml
[datasheet]
folder = "~/datasheets"     # <MPN>.pdf, read before any link; "" = links only
standard_land = "auto"      # "off" (default) | "auto" | "always"
```

- The file is matched by MPN, ignoring case and punctuation: `<MPN>.pdf` first, then the MPN alone after a separator
  (`数据手册-TP4056.pdf`), else the MPN followed by a separator (`LTM4671IY-PBF_datasheet.pdf`, `TP4056中文资料.pdf`;
  `WS2812B-2020.pdf` never beats `数据手册-WS2812B.pdf` for `WS2812B`). Names compare as part numbers do elsewhere in
  ecadlib: `µ` is `U`, accented and full-width letters are plain, and every other letter counts (`К561ТМ2.pdf` is never
  `К561ЛН2`'s, `HX-2.54-4P直针` never takes `HX-2.54-4P弯针.pdf`). Before or after a part number in letters and digits, a
  Chinese, Japanese or Korean word or a symbol is a separator (`数据手册-TP4056.pdf`); a symbol inside it (`°`, `×`, `±`) is
  not. That file is read before any link, and a BOM line with no link at all is read from it too. Any other match is
  used only with a hold that names the file (the part is not released until a person confirms the PDF is this part's),
  and only after the part's own links: the longest name the MPN starts with, with only words after it (`LTM4671.pdf` or
  `LTM4671 中文资料.pdf` for `LTM4671IY#PBF`, at least 4 characters and half the MPN; never another variant's such as
  `HX-2.54-4P弯针.pdf` or `PH2.0-2×4P.pdf`, and no name with a word at all where the part number has a word or symbol of
  its own: `HX-2.54 弯针.pdf` is no family file of `HX-2.54-4P直针`'s) is a family datasheet, read when no link gave a PDF
  (before ecadlib opens a refused link in your browser), and a name that goes on with more letters or digits
  (`BAT54S.pdf` for `BAT54`, `LM317HV.pdf` for `LM317`) is another part number, read only when no link gave a PDF here
  and your browser got none either (before a refused link is left to the API to fetch). A part whose own link works
  never reads either.
- When a maker's site refuses the download, the review says to save the PDF in this folder as `<MPN>.pdf` and run
  again. A character no file name can hold (`/ \ : * ? " < > |`) is written as `-` (`LM1117MPX-3.3-NOPB.pdf` for
  `LM1117MPX-3.3/NOPB`), and that file is then read as the part's own. With no `folder` set, it says how to set one. A
  file already there under that name that is not a PDF (a web page saved as `.pdf`) is named first: replace it.
  `ecadlib datasheet batch PLAN` run again says this as the folder is now: once an answer comes back (even one held
  by the cross-check), or the link no longer gives a PDF at all, the advice goes; while the site still refuses the
  link and no answer came back (the API busy, over the cost cap, a batch not back yet or expired), it stays.
- A `folder` that does not exist, is not a folder or cannot be read (no access) is named, by its name only, in the
  refused link's review line, in what was tried for a line with no link and in the part's error; the links are tried
  as ever, and `ecadlib doctor` warns about it.
- The part keeps its public datasheet link in the library; the read's provenance records only the file's name, never
  a path on your machine.
- `ecadlib eval run --datasheets DIR` does the same for a benchmark run. The report counts the parts read from the
  folder and gives the pass rate without them, so the number for maker links stays its own.

**Standard land from a read.** A read that left one number blank (a missing terminal length, tab length or annular ring) used to
be held with no footprint. Now, `stdparts.identify` checks whether the read's pin count, pitch, body and exposed pad fit
exactly one package in the checked standard table, within the family the drawing is titled with. Every other table
entry the read could be, and every KiCad generic package that is not in the table, counts as a rival; a rival with
other pads makes it ambiguous, and then nothing changes. The chosen entry must also agree with the whole read, lead span included.

- `off` (the default for now): the read's pads, as before. It stays the default until a live benchmark run with
  `auto` shows the number moves the right way.
- `auto`: the table's land is used when the read left a number blank or the datasheet prints no land of its own. A
  maker's recommended land, when printed, is kept.
- `always`: the table's land whenever the read identifies one.
- The symbol and the 3D height (`height_max`, when read) still come from the read. The review says which package was identified and asks a person to check
  the package code on the drawing.
- Fed every table entry as if a datasheet printed exactly its numbers, `identify` names 294 of 618 (itself or an
  entry with the same pads) and never one with other pads (`tests/test_datasheet_folder.py`). The other entries are
  ambiguous or a package type the table does not cover (chips, SOT, connectors). Its effect on the live benchmark has not been
  measured yet.

## A web search for a datasheet nobody linked (`[datasheet] web_search`)

Maker links die: documents get new numbers, sites move their files, a link lands on a product page instead of the PDF.
When none of a part's links, part sources or folder files gives its PDF (or the BOM line has no link at all), ecadlib
can ask Claude's web search tool for the maker's current PDF.

```toml
[datasheet]
web_search = true          # off by default: every search is billed
web_search_model = ""      # "" = claude-haiku-5-5 (see the 2026-10-22 test below)
web_search_max_uses = 3    # searches per part
```

- One short request per part names the MPN, the BOM's maker and the package, and asks for direct PDF links: the
  maker's own first, then a major distributor's copy. Each link is only a candidate: it is downloaded the usual way
  and kept only if it is a PDF that names the part.
- The cost (tokens plus $10 per 1,000 searches; about $0.03 to $0.36 a part on Opus 5.5 in the 2026-10-21 test, mostly
  the search results it reads) counts toward `max_cost_usd` and is written to `cost.jsonl` (via "web search"). A
  search that could pass the cap (budgeted at its worst case) is not sent, and the part's review says so.
- A link found is kept in `<out>/datasheet/web_search.json`, so the part is not searched again.
- The search needs a Claude model and key: with another provider it fails with a note and the part goes on as
  before. `--offline` never searches.
- The links are downloaded in this order: the maker's own site (a label of the host starts with the maker's name),
  then a major distributor's copy (Mouser, DigiKey, Arrow, Farnell, LCSC, ...), then any other site. A mirror's copy
  can be an old revision, so it is used only when no better copy gives the part's PDF.
- The 2026-10-21 test on the kicad-v1 parts whose links had died: 14 of the 16 tried were found (6 on the maker's
  site, 8 as distributor copies).
- The 2026-10-22 test on the 41 kicad-v1 parts whose BOM links were dead (`$2` cap each): Haiku 5.5 found 39 (23 on
  the maker's site, 12 distributor copies, 4 mirrors) for $1.04, about $0.025 a part. Opus 5.5 found 14 of the 18 its
  cap let it try for $1.47: it often answered from memory without searching, and those were its misses. So the search
  runs on Haiku unless `web_search_model` names another model.

**The package drawing, when the datasheet has none.** Some datasheets have no drawing of the part's package at all. Neutrik's
product sheets have only photos and spec tables. Altera, Lattice and Microchip family sheets keep their BGA drawings in
a separate package-information document. An onsemi revision dropped a package. On the 2026-10-23 live benchmark these
were 12 of the 17 parts held for a blank dimension. With `web_search = true`, such a part gets a second search, for its
package drawing: a package-information document, a case outline, a product drawing or a PCB layout.

- It runs only after a read that left a dimension the footprint needs blank, so a part read right from its datasheet
  never pays for a search. The PDF's text must also show no drawing of this package: no dimension table for it, no
  short page captioned as its drawing, and no image-only page after the first (that page may be the drawing). A PDF
  with no text layer is never searched for. When a drawing is found, the part is read again with it, on the same
  model.
- A PDF the search finds is kept only if it names the part, a document number the datasheet gives for it, or the
  part's package with its pin count (FBGA-484, not just "BGA"). Its pages go after the datasheet's, like an outline
  document's. The review says where the drawing came from, and that it must be checked against the part's package.
- The search costs the same as a datasheet search. It is written to `cost.jsonl` as "web search (package drawing)".
  The link it finds is kept in `web_search.json` as "<part> package drawing". A search that found nothing is kept
  there too, for 30 days and for that datasheet only, so re-running a held part doesn't pay for the same empty
  search again. Delete the entry to search sooner.
- If nothing is found, the first read stands and the review says no package drawing was found. The part is still
  held for the blank dimension.

## Updates never break a library: `migrate.py`, `library.lock.json` and `ecadlib rebuild`

**Versioned files.** Everything ecadlib writes and reads back later has a schema version, one integer per kind of file
(`ecadlib/migrate.py`, `KINDS`): `plan` (plan.json), `librarian_state` (now v3), `approvals_audit`, `agent`
and `queue` (files an older ecadlib's library agent left in `<workspace>/agent/`), `http_cache`, `rules` (ecadlib.toml),
`approvals_policy` (approvals.json) and `lock`. Files other code iterates (a plan is a JSON list) keep the
version in the folder's `ecadlib_schema.json`; files a person edits and the lock carry a top-level `schema_version`
(absent = 1). A folder without a version is a workspace from before versioning (v1 of everything).

* **Older** files are migrated: in memory when they are only read (`migrate.read`), on disk when a command writes the
  folder anyway (the librarian, `ecadlib migrate`), after a copy to `<folder>/.ecadlib_backup/<kind>-v<old>-<time>/`.
  Migrations are registered in `MIGRATIONS` and are safe to run twice. Two so far: `librarian_state` v1 -> v2
  records each footprint's name and geometry hash (what `fp_share` would compute from its `.kicad_mod` anyway; a
  footprint shared by name only is left unchecked so the librarian re-checks it), and v2 -> v3 computes each recorded
  hash again, because the hash now covers pad shapes and holes (one whose `.kicad_mod` is gone is dropped).
* **Newer** files are refused with one line, never misread: `librarian/board/plan.json was made by ecadlib 0.5.0 (plan
  schema 2; this ecadlib 0.3.0 reads up to 1): upgrade ecadlib to the build that made it (pip install -U ecadlib; when
  that says it is up to date, the main-branch build: pip install -U "ecadlib @ git+https://github.com/oliBrowne/PCBLIVINGLIBRARY")`
  (a main-branch build can write a newer format while still saying the last release's version). Checked when the web UI / MCP
  workspace opens, by the librarian, HTTP cache, rules and approvals loaders. A kind only a newer ecadlib
  knows is left alone.
* `ecadlib migrate [PATH] [--check] [--dry-run] [--json]` lists every versioned file under PATH (ok / old / new) and
  migrates the old ones; `--check` exits 1 when anything needs migrating or is newer and, like `--dry-run`, writes
  nothing. `ecadlib doctor` has a "file formats" line. Workspace `file_formats(migrate=False)`, MCP tool `file_formats`.

**Proved on real old workspaces.** `tests/fixtures/upgrade/*.tar.gz` (180 KB) are workspaces made by the code of four
earlier commits (`python3 -m tests.upgrade_fixtures build OLD_CHECKOUT LABEL`, run from a `git worktree` of that commit,
offline, from the end-to-end tests' recorded KiCad / EasyEDA inputs, through that version's own Workspace API):
`v0.2.0` (da4e1ad), `v0.3.0` (9916ca1: + approvals, ecadlib.toml), `r5-agent` (f2eedbf) and `r7-stdparts`
(26ca9a5). `tests/test_upgrade.py` opens each with today's code: the librarian
migrates the state (backup byte-identical to the original), keeps the parts the old version finished (files
untouched) and builds the rest exactly as a fresh build would (same statuses and sources); Ann's approval from 0.3.0
still counts (content hash unchanged).

**Reproducible rebuilds.** The librarian writes `<out>/library.lock.json` at the end of every run (no timestamps: a
re-run that changes nothing leaves it byte-identical). Per part: the BOM-line fingerprint, the ecadlib version and
rules hash it was built with, every HTTP response its sources fetched (`method`, URL with secrets redacted, sha256 of
the text; recorded as they arrive by `rebuild.Recorder`), the `--lib` files it came from, for datasheet parts the PDF's
sha256, model, provider and prompt/schema version (`datasheet.schema_version()`), and the sha256 of every file written
(plus a normalised hash). Plus the BOM's path and sha256, sources, libraries, cache folder and rules file.

`ecadlib rebuild LIB --check [--keep DIR] [--json]` copies the folder, deletes every model file the librarian made
(files chosen with `ecadlib resolve` stay), runs the librarian again from the lock with `--offline` (the HTTP cache and
the cached model answers only: a model is never called) and reports per part and file *identical*, *normalised* (same
after blanking the `.PcbLib` header's DATE/TIME, the only wall-clock value ecadlib writes; GUIDs and unique ids are
derived from content) or *DRIFT*, plus inputs that changed (BOM, rules, a cached response, a `--lib` file) and parts
built by another ecadlib version. Exit 0 / 1 (drift) / 2 (no lock, BOM missing, or a refused `--keep`). `--keep DIR`
puts the copy in `DIR/<library name>`, which must not be, hold or sit inside the library or anything the lock reads
(BOM, HTTP cache, `--lib` folders and files, rules file); an existing `DIR/<library name>` is replaced only when an
earlier `--check --keep` made it (it holds `.ecadlib-rebuild-copy`). `ecadlib rebuild LIB` rebuilds in
place. Workspace `rebuild_check(bom)`, MCP tool `rebuild_check`. Tested: the 5-part BOM rebuilds with 0 drift; a
cached KiCad footprint edited under the lock is reported as a changed input and a drifted `.PcbLib`; a changed BOM is
reported; a datasheet part rebuilds from its cached answer while a fake API server records zero calls.

## `lifecycle.py`: lifecycle watcher (keep the library current)

```
python3 -m ecadlib.lifecycle examples/sample_bom.csv out/altium/parts.sqlite out/altium365/plan.json \
        [--a365 https://<ws>.365.altium.com] [--providers nexar,digikey,mouser,jlcpcb_search] \
        [--state out/lifecycle_state.json] [-o out/lifecycle_report] [--strict] [--offline]
python3 -m ecadlib.lifecycle out/altium365/plan.json --update-a365 --workspace <url> [--apply]
python3 -m tests.test_lifecycle
```

Reads parts from BOMs, the DbLib `parts.sqlite`, a `plan.json` or (with `--a365`) the released components of a workspace, and checks them against Nexar Supply (Octopart), DigiKey v4, Mouser and JLC stock. Statuses are normalised to `active < nrnd < ltb < obsolete` or `unknown`; a part takes the worst status and the highest stock its sources report. Each run is compared with the `--state` snapshot and written to `.md` and `.csv`. Exit codes: 0 nothing got worse, 1 a part got worse, 2 bad input, 3 no source answered (missing secrets or blocked network). `--update-a365` writes a `Lifecycle` parameter to released components (dry run unless `--apply`; it sends the full parameter set with `replaceExisting: true` so nothing is duplicated). `examples/lifecycle-watch.yml` is a sample weekly GitHub Actions job. Credentials: `NEXAR_CLIENT_ID`/`NEXAR_CLIENT_SECRET` (lifecycle needs a Nexar plan with Tech Specs), `DIGIKEY_CLIENT_ID`/`DIGIKEY_CLIENT_SECRET`, `MOUSER_API_KEY`, `NEXAR_TOKEN` for workspace access. Secrets are never written to the HTTP cache. **Not tested live:** every vendor host is blocked from this sandbox; the parsers are tested against the documented response shapes.

## `upstream.py`: upstream change alerts

```
python3 -m ecadlib.upstream check-kicad out/altium_models_kicad/manifest.json [-o out/upstream_kicad] [--update] [--offline]
NEXAR_TOKEN=... python3 -m ecadlib.upstream check-a365 out/altium365/plan.json --workspace <url> \
        [--folder Components] [--state out/a365_state.json] [-o out/upstream_a365]
python3 -m tests.test_upstream
```

Every `kicad_import` conversion records in `<out>/manifest.json` where each output came from: source URLs (footprint, STEP, symbol and its `extends` parents), sha256 and git blob id, the last GitLab commit, a pad/courtyard/pin summary, the output hash and the converter version. `check-kicad` asks the GitLab API for each source's blob id without downloading, then fetches and diffs only changed files: pads added, removed, moved or resized, courtyard changes, pins added, removed or renamed, and files deleted or renamed upstream. `--update` reconverts the changed parts. `check-a365` compares a release plan with the workspace: missing components, parameter, comment, description, folder and model-name differences (parameters added by hand in Altium 365 are not drift), and workspace components the plan doesn't list. Nexar exposes no symbol or footprint revision, so `--state` records component revisions and model GUIDs and flags changes since the last run. Exit codes: 0 no changes, 1 changes, 2 error. `examples/upstream-watch.yml` is a sample weekly job. **Tested live** against gitlab.com; `check-a365` only against a mock (Nexar is blocked here).

## `upkeep.py`: weekly upkeep (one issue or PR)

```
ecadlib upkeep library/altium365/plan.json [--bom bom.csv] [--manifest models/manifest.json] [--workspace URL] \
        [--checks lifecycle,upstream,a365,changes] [--state out/upkeep_state] [-o out/upkeep] [--apply] [--github issue|pr]
python3 -m tests.test_upkeep
```

This runs the lifecycle watcher, the KiCad upstream check, the Altium 365 drift check and the change-control check together, in one process. It compares the results with what the last run saw (`--state DIR`) and reports only what is **new**. That covers parts that became NRND, last time buy or EOL; stock that ran out or came back; KiCad sources that changed, were renamed or were deleted; new Altium 365 drift rows; and released parts with changes waiting for approval or projects that use an older revision ([change control](change-control.md)). The output is one `out/upkeep.md` plus `.json`. Exit codes: 0 nothing new, 1 new changes, 2 error. When a check fails, the other checks still run, its state is kept, and its changes are reported again next time.

A check without credentials is **skipped**, not failed, and the report says what to set. Lifecycle needs Nexar, DigiKey or Mouser keys. A365 needs `NEXAR_TOKEN` and a workspace, from `--workspace` or `[altium365] workspace`. Upstream needs the provenance `manifest.json` that `kicad_import` writes. It is found next to the plan or in `<plan dir>/models/`.

`--apply` only reconverts KiCad parts whose upstream source *changed*. Renamed and deleted sources are left for a person. This gives a pull request a real library diff. `--github issue` updates **one** rolling issue labelled `ecadlib-upkeep`: it replaces the body and adds a short comment, so it never opens duplicates. `--github pr` commits the applied files to `ecadlib/upkeep-YYYY-MM-DD`, pushes the branch and opens or updates the PR. With nothing to commit, it falls back to the issue. When nothing is new, GitHub is not touched. The GitHub calls use the REST API when `GITHUB_TOKEN` and `GITHUB_REPOSITORY` (or `--repo`) are set, and `gh api` otherwise. If the GitHub step fails, no state is saved, so nothing is lost. [`examples/upkeep.yml`](../examples/upkeep.yml) is a ready-to-copy weekly workflow (cron plus a manual run with an `apply` switch) for **your** library repo, with the secrets it reads listed at the top. It is not active in this repo. The same checks are available from the web/MCP side as the `upkeep_run` tool (`Workspace.upkeep_run`), which never calls GitHub. **Not tested live:** GitHub, GitLab, Nexar and the distributors are fakes in the tests.
