# Changelog

All notable changes to ecadlib are recorded here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Until 1.0.0,
a minor release may change command-line options and file formats.

## [Unreleased]

### Fixed

- Spreadsheet formula injection: every report and export CSV (triage, sourcing, release check, librarian review, dedupe and its BOM copy, where-used, project BOM, lifecycle, upstream, the agent's library BOM) writes cells starting with `= + - @`, tab or CR with a leading apostrophe (`ecadlib/sheetsafe.py`); plain numbers such as `-5` stay, the DbLib keeps Altium's `=Value` literal, and BOMs read back unchanged. Excel number cells read as typed (`1e-07` F becomes `100n`, `1.0` becomes `1`), so part names never show exponents. UTF-16 BOMs without a byte-order mark are detected and read.
- Team server security review: the sign-in lockout could be beaten with parallel requests (now counted before the password check), **Change password** checked the current password with no lockout, a removed user's sessions came back when the name was added again, `TEAM_USERS.JSON` (case-insensitive disks) or a hard link served the password hashes, team files could be triaged as a BOM, sign-in accepted 20 MB JSON bodies, the Docker stack believed a `proxy_user_header` from `team.json` that Caddy passed on from any client, and the audit line was written after the response (a flaky test).
- Datasheet batches: a create whose answer was lost (timeout, 5xx) leaves a marker; the next run lists recent batches and adopts the matching one instead of paying twice, and refuses to resubmit an unconfirmed one without `--yes`. `output_config.effort` is only sent to models that accept it (stepping down to a supported level), 200K-context models get the 100-page PDF limit, worst-case estimates include a fallback re-run, responses are shape-checked by hand when jsonschema is missing, and a part's spec and symbol JSON never overwrite each other.
- Datasheet reader and visual proof (review): `[datasheet] max_cost_usd` now holds on every path (the librarian's synchronous calls, the single-part CLI with new `--rules`/`--yes`, cumulatively across parts) and refuses a model with no known price; collecting an already-submitted batch is never blocked by the cap. A visual proof no longer says "pass" when the model marked every check not_applicable (pad_count / pin_count / pin_numbers must match). Staggered pin headers report their real pitch and alternating numbering. Password-protected or damaged PDFs give a clear error instead of a crash when cut to pages, and a page the model cites outside the pages sent is flagged instead of shown as an original page number.
- Team server hardening: one deadline per request (a client trickling bytes held a thread forever), a cap on connections per address, listen backlog 128 (60 simultaneous sign-ins got connection resets), `team_audit.jsonl` rotation with tail-only reading, and per-address rate limiting of unauthenticated refusals in the audit log.
- Library agent follow-up (rvC): rejected/failed parts' files leave the batch branch and the PR says so; decided batches with a pushed part get one "ready to merge" comment and stay open, those with nothing pushed are closed; `[agent] manufacturer_aliases` extends the built-in maker alias table; every approval path respects `agent/plan.lock`; manufacturer names are normalised in part keys (ST/STMicroelectronics/blank = one part) with a one-time migration of queue and watch state; `agent run --dry-run` writes nothing.
- Library agent, long-run safety (review rvC; `tests/test_agent_soak.py`): a pass killed between the scan and the queue no longer loses those changes; items left in `building`/`proving` by a dead pass (or after a clock step back) are resumed instead of stuck forever; an approve made while the librarian ran is no longer overwritten in `plan.json`; an approve is refused when the part was rebuilt after the reviewer opened it; approved removals/upkeep findings and parts whose files changed since approval are no longer pushed to Altium 365; a change after approval is no longer lost once the part is pushed; one bad change, a concurrent reject or a rules typo no longer stops the run or `serve`; PR bodies escape part data (HTML, links, @mentions, "fixes #N"); the batch branch starts from the remote base (local unpushed commits were pushed); retried parts get a new batch and closed PRs are replaced; a half-written BOM no longer reports every part removed; weekly upkeep restarts after a clock that ran ahead; upserting a huge BOM is no longer quadratic.
- Real-world BOMs and robustness (tests `test_realworld_boms.py`, `test_fuzz.py`, `test_cli_smoke.py`): `olefile` is now a core dependency (on a plain `pip install` the release check held every part and `kicad-export` wrote nothing); European decimals (`4,7k`, `2,2µF`) parse as 4.7 k / 2.2 µF, not 4 Ω / 2 F; a `Total` footer row is no longer a part; DigiKey's own `Part Number` is not taken as the MPN; a workbook's BOM sheet is found behind a cover sheet; a CSV named `.xlsx` reads, a corrupt workbook is a clear error; quoted cells spanning lines and `R1–R4` (en dash) ranges read right; UTF-16 BOMs upload in the web UI / MCP; `sourcing`, `upkeep` and the lifecycle reader honour the rules' `[bom] columns`; `parts.xlsx` keeps `=Value` as text, not an Excel formula; unreadable BOMs, bad `ecadlib.toml` and malformed KiCad / Altium files give a message, not a traceback; package-spec validation is ~40x faster.
- Footprints and symbols (review): same-looking supply pins (VDD1 / VDD2 of an isolator, VDD18 / VDD33) are no longer stacked onto one spot, which joined them in Altium; hidden power pins keep their Altium HiddenNetName in the KiCad export; single-row SMD pin headers get KiCad's single-row land (they used the dual-row one) and the 2.00 mm dual-row land is KiCad's; drill-only through-hole pads honour the pad rotation; BGA row H is no longer taken for mounting holes (footprint_check, release_check); a BGA with A1 depopulated passes, a misnamed ball is reported instead of switching the BGA rules off; SMD-defined lands keep their paste margin in the PcbLib, and PcbLib -> KiCad keeps mask margins and tented pads; STEP models of SMD headers no longer push pins through the board, and rotated connector pads turn in the model. The KiCad benchmark grows from 20 to 57 footprints (2942 pads).

- Library agent integration (end-to-end test `tests/test_agent_e2e.py` with the real watch, queue, visual check and agent): the proof now gets the queue item's built footprint, symbol and datasheet (it always said "nothing to check"). `ecadlib agent approve|reject` no longer crashes. A rebuild keeps who asked for it, so that person cannot approve it. A BOM line without an MPN no longer stops the run. A part changed in its source while in review is rebuilt and its PR updated. The queue's batch record gets the PR link. `visual-check PART --workspace` reads librarian plans again. The batch PR links http(s) datasheets and does not name a model when no proof ran.

### Added

- Library agent watch step (`ecadlib agent-watch [--dry-run]`, MCP `agent_watch_scan`, `[agent.watch]` rules): new, changed and removed parts in watched BOM files, folders (including web UI uploads), Altium projects and Altium 365 projects (Nexar queries schema-checked, not yet run live), with state in `agent/watch_state.json`.
- Library agent (`ecadlib agent run|serve|status|approve|reject|push`): watches projects, builds and proves new parts through the librarian, opens one GitHub PR per review batch with before/after pictures (or writes the batch to `agent/batches/` without a token or git), pushes approved parts to Altium 365, turns upkeep findings into queue items; `[agent]` rules (off by default) and an optional `agent` Docker Compose profile.
- Library agent review queue (`ecadlib/agent_queue.py`, `ecadlib queue`): a durable, locked work-item store under `<workspace>/agent/queue/` with a state machine, history, batches and per-part dedupe. A **Review queue** page in the web UI shows the change, the build, the proof findings and before/after pictures, with Approve (approver role), Reject and Rebuild (librarian role); approvals are also recorded through `approvals.py`. MCP tools: `agent_queue_list`, `agent_queue_get`, `agent_queue_decide`.
- Visual proof (`ecadlib visual-check`, `Workspace.visual_check`, MCP `visual_check`, `[agent.proof]` rules): top-view footprint and symbol PNGs (pin 1, pitch, dimensions, grid, BGA maps) next to the datasheet pages, one Claude call with a JSON-schema verdict checked strictly (pass / doubt / fail / skipped without a key), cached by content hash, with a cost cap.

## [0.3.0] - 2026-10-14

### Added

- Web UI: a **Past triages** list that any signed-in user (viewer and up), and local mode, can open read-only (`GET /api/triage/history`, `/api/triage/result`, MCP `triage_history`). Each upload is now stored in its own folder, `uploads/<time>-<random>/<name>`, so BOMs with the same file name no longer overwrite each other, or each other's reports.
- Resolve held parts: work through the release check's held items and record how each was fixed.
- KiCad export: write the library's symbols, footprints and 3D models as KiCad libraries.
- Altium DbLib/SVNDbLib sync: keep a database library in step with the living library.
- STEP generation: build simple 3D bodies for generated footprints.
- Sourcing and alternates: look up stock and suggest drop-in alternates per BOM line.
- Approvals and dedupe: sign off parts before release and merge duplicate library entries.
- Weekly upkeep (`ecadlib upkeep`): lifecycle, KiCad upstream, price/stock and Altium 365 drift in one run,
  only new changes reported, one rolling GitHub issue or a PR with applied KiCad reconversions; `examples/upkeep.yml`.
- Live datasheet reader (`ecadlib datasheet batch|estimate PLAN|BOM`, `librarian --datasheet-batch`, MCP `datasheet_batch`/`datasheet_estimate`): a whole BOM's missing symbols and footprints from datasheets in one Message Batch, with datasheet download and PDF checks, page selection, retries and backoff, a request log without the key, a cost estimate and cap, a result cache, and a self-check. All results are held as `verify`.
- Run the team server for a team in one command: `python3 -m ecadlib team setup` (or `scripts/team-setup.sh`) writes `.env` (no secrets), builds the image, creates the first admin in the data volume without writing the password anywhere, and starts ecadlib behind Caddy (Let's Encrypt for a public name, Caddy's internal CA for an IP/LAN name) or with your own certificate (`docker-compose.own-cert.yml`); `Dockerfile` (non-root, `/data` volume, health check), `docker-compose.yml` with `--trusted-proxy` pinned to Caddy's fixed address, and a `docker` workflow that runs the stack on PRs and publishes to ghcr.io only on `v*` tags.
- Team server (`ecadlib ui --team`, `ecadlib team add-user|set-role|reset-password|remove|list`): sign-in with scrypt-hashed passwords, viewer/librarian/approver/admin roles enforced per route, CSRF tokens, lockout, built-in TLS (`--cert/--key`) or `--allow-http` behind a proxy, optional trusted SSO proxy header, approvals signed as the signed-in user, per-user jobs, `team_audit.jsonl`, and a users page. Local mode is unchanged.
- Harder footprints: BGA/LGA/CSP grids (JEDEC row letters, depopulated balls or a ball map, IPC-7351B land from the ball size, NSMD/SMD), pin headers, connectors and odd shapes from a datasheet `pad_table` (mm/mil/in, y up/down, bottom views, datum origins; polygon pads, slots, NPTH, mounting/shield pads, split paste, thermal vias, keep-outs, union courtyard) with consistency checks; BGA and connector rules in `ecadlib check`; BGA/connector STEP models; `ecadlib footprint-bench` compares 20 parts pad by pad with KiCad's library.
- Simulation models (`ecadlib sim-models`): built-in R/C/L SPICE models, vendor SPICE/IBIS models found and parsed, pin maps checked, attached as Altium SIM/SI links and KiCad `Sim.*` fields.
- Tidy big-chip symbols (`ecadlib symbol`, `ecadlib/symbol_layout.py`): pins grouped by function (power top, ground bottom, control/debug left, ports right), ports in order, same-name power pins stacked, optional multi-part split, SVG preview; used for datasheet and EasyEDA symbols of 40+ pins, `[symbols]` rules section.

### Fixed

- Footprint approval now writes the Altium `.PcbLib` for through-hole parts (pin headers, connectors, custom), with drills, slots, NPTH holes and polygon pads as anchor pad + copper/mask/paste regions instead of a bounding rectangle; BGA NSMD pads get their mask expansion. SMD output is unchanged. The footprint preview fits its view to the actual pads, holes and courtyard, so through-hole connectors are no longer drawn off-centre.

## [0.2.0] - 2026-10-14

### Added

- Packaging for PyPI: complete project metadata, `pdf`, `altium` and `all` extras,
  version single-sourced from `ecadlib.__version__`, `ecadlib --version`, this changelog,
  a CI job that builds and smoke-tests the sdist and wheel, and a tag-triggered release
  workflow (GitHub release plus PyPI trusted publishing).
- Altium 365 workspace exporter through the Nexar Design API (`ecadlib altium365 plan|workspaces|push`),
  with every GraphQL operation validated against Nexar's published schema and a dry-run mode.
- Pure-Python Altium `.PcbLib` / `.SchLib` writer (`ecadlib altium-lib`): SMD and through-hole pads,
  arcs, keep-outs, extruded and embedded-STEP 3D bodies, multi-part symbols, hidden pins,
  designator and comment text, pin graphics (clock, inverted, active-low) and De Morgan display modes.
- Local web UI (`ecadlib ui`) and MCP server (`ecadlib mcp`) on a shared Workspace layer, plus the
  single `ecadlib` command for all tools.
- KiCad library import (`kicad_import`): official KiCad footprints, symbols and STEP models converted
  to Altium files, with provenance recorded in `manifest.json`.
- Datasheet reader (`datasheet`): Claude reads a PDF and fills the package spec and the symbol.
- Lifecycle watcher (`lifecycle`): checks part status with Nexar, DigiKey and Mouser and can write the
  lifecycle back to Altium 365.
- Librarian (`ecadlib librarian`): one command from a BOM to a filled library, sourcing missing parts
  from KiCad, then LCSC/EasyEDA, then the datasheet reader; available in the UI and the MCP server.
- Release check (`ecadlib release-check`): holds back parts that fail pin/pad, footprint, 3D-height,
  required-field and verification checks before an Altium 365 push; `--allow` overrides per part.
- Upstream change alerts (`ecadlib upstream check-kicad|check-a365`): diffs KiCad sources against
  imported parts and reports Altium 365 drift from the release plan.
- Footprint checker (`ecadlib check`) for KLC/IPC rules; SOT-223 tabs, polarity marks, QFP and
  DFN families; IPC-7351B numbers taken from the tables KiCad builds its library with.
- GitHub Actions workflow running the offline test suite (`python -m tests.run_all`) on every PR.

### Changed

- `jsonschema` is a runtime dependency; the example package specs ship as package data
  (`ecadlib/package_specs`).
- Altium 365 push skips parts already up to date, sends the merged parameter set, and never
  pushes parts the release check held.

### Fixed

- Real-world BOM shapes: Altium ActiveBOM, UTF-16 files, designator ranges, binary and
  headerless BOMs, and generic LibRefs no longer promoted to MPNs.
- Independent review findings in the core modules, the Altium writer (P1-P11) and the UI,
  MCP server and footprint code (U1-U14).

### Security

- Query-string secrets (API keys, tokens) are redacted from the HTTP cache.
- The UI checks the Host header, listens on loopback only, and reads BOMs only from the workspace.

## [0.1.0] - 2026-10-13

### Added

- Initial prototypes: IPC-7351B footprint generator driven by `package_schema.json`, BOM triage
  against local KiCad libraries and public part sources, and Altium Database Library (DbLib) export.

[Unreleased]: https://github.com/oliBrowne/PCBLIVINGLIBRARY/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/oliBrowne/PCBLIVINGLIBRARY/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/oliBrowne/PCBLIVINGLIBRARY/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/oliBrowne/PCBLIVINGLIBRARY/releases/tag/v0.1.0
