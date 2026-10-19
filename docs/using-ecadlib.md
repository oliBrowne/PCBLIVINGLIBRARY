# Using ecadlib: web UI, MCP server and command line

The three ways in: the local web UI, the MCP server and the command line.

Install it with pip (Python 3.10 or later):

```
pip install ecadlib                 # from PyPI (pypdf included: the datasheet reader sends only the pages it needs)
pip install -e ".[dev]"             # from a checkout: editable, plus everything the test suite uses
```

`ecadlib --version` prints the installed version. Changes are listed in [CHANGELOG.md](../CHANGELOG.md).

That gives you a single `ecadlib` command (`python -m ecadlib` works too). There are three ways in, and all of them call the same actions in `ecadlib/app.py`:

1. **Local web UI** (for most people). It runs on your own machine, so your API keys and libraries stay local.

   ```
   ecadlib ui --lib path/to/your/kicad/libs
   ```

   A browser tab opens at `http://127.0.0.1:8765`.
   * **Check a BOM:** drop in a CSV or XLSX, whether it's an Altium BOM, an Altium ActiveBOM, a KiCad BOM or a hand-made spreadsheet. A progress bar runs while parts are looked up. You then get a table of what is already in your library, what a part source can supply, what needs a footprint, and what is DNP. You can filter it by status or by search.
   * **Fill the library:** one button runs the librarian on the BOM. It finds each missing symbol and footprint (official KiCad libraries, then EasyEDA, then the datasheet reader), writes the Altium files, and runs the release check. Each part shows where its symbol and footprint came from and what still needs a person. Parts that fail the check are held back from Altium 365; **Release anyway…** overrides that after you have looked (it is recorded in the plan), and **Hold again** takes it back.

     ![Fill the library](ui-librarian.png)
     * **Pick…** on a held part lists ranked symbol or footprint candidates (official KiCad library, your `--lib` libraries, ecadlib's package specs) with why each ranked, its pin/pad count, whether it matches the part's other half and a suggested pin map for a near miss. **Choose** converts it, records the choice in the plan (a librarian re-run keeps it) and re-runs the release check.

       ![Pick a candidate](ui-resolve.png)
     * **Sign-off:** type your name, then **Approve** or **Reject…** a part; **Approval status** shows the policy and who has signed. There is no login (it is a local tool), so the name you type is recorded as given, in the plan and in `approvals_audit.jsonl`. With an `approvals.json` next to the plan ([sign-off](librarian.md#approvalspy-and-dedupepy-sign-off-and-duplicates)), parts wait as *awaiting approval* and are not pushed until signed off.
     * **Find duplicates** lists parts with the same MPN, footprints with the same geometry and symbols with the same pinout across the plan and the workspace's `altium_models/` and `footprints/`; **Merge…** points the plan at the keepers (library files are never changed).

       ![Sign-off and duplicates](ui-signoff.png)
   * **Make a footprint:** click **Generate…** on a missing part. It picks a matching example package when one exists. Enter the datasheet dimensions, preview the IPC-7351B land pattern, and approve it. Approving writes a KiCad `.kicad_mod` and an Altium `.PcbLib`, and asks before replacing an existing footprint. The BOM is then re-checked automatically.
   * **Send to Altium:** there are three options.
     * **Export DbLib** writes the SQLite/XLSX/.DbLib set.
     * **Build .PcbLib / .SchLib** converts the BOM's footprints and writes generic R/C symbols.
     * **Altium 365** plans a release with a ready / needs-symbol / needs-footprint status per part. **Dry run** shows the exact requests without sending anything. **Push** is enabled only when `NEXAR_TOKEN` is set and a workspace is picked, and it asks for confirmation first.

   * **Find your way:** a bar at the top links the steps in order (1. Check a BOM, 2. Fill the library, 3. Send to Altium) and the Review queue (with how many items wait), Footprints, Status and Settings (library rules). After each action a blue line says what happened and what to do next ("8 held: use Pick…"; "Next: Dry run"), with a button to the next step. Long actions show progress in their own card.
   * **Status:** counts of BOMs, parts in library plans, ready, held, awaiting approval and review-queue items, the last BOM check, and a list of what waits for someone. **Run setup check** shows `ecadlib doctor` in the page, failures first, with how to fix each one. Routes: `GET /api/status` and `GET /api/doctor`.
   * Errors are one plain line, with workspace paths shown relative to the workspace.
   * **Checked in a real browser:** `tests/test_ui_browser.py` drives Chromium (Playwright) through these flows at 1280 px and 390 px wide (next-step lines, progress, keyboard focus, no sideways scrolling, no console errors). It skips when node, Playwright or Chromium is missing. `tests/ui_fixture.py DIR [--empty]` serves the same offline workspace by hand.

   ![Triage table](ui-triage.png)
   ![Footprint preview](ui-footprint.png)
   ![Send to Altium](ui-altium.png)

2. **MCP server**, so Claude, Codex, Grok or another agent can do the same work in conversation (for example, "check this BOM and draft footprints for anything missing"). Setup for each client, HTTP mode, tool sets for small models and the cost rules: [docs/mcp.md](mcp.md).

   ```
   claude mcp add ecadlib -- ecadlib mcp --lib /path/to/libs --workspace /path/to/workspace
   ```

   Its tools include `workspace_status`, `workspace_info`, `upload_bom`, `triage_bom` (with `summary_only` for large BOMs), `preview_footprint`, `approve_footprint`, `librarian_run` (fill every missing part, then the release check), `release_check` (with `allow` for parts a person has signed off), `held_parts`, `part_candidates` and `choose_part` (fix a held part), `approval_status` and `approve_part` (sign-off; `user` is required, the agent must pass the person's own name), `find_duplicates` (with `merge`), `build_altium_models`, `export_altium_dblib`, `altium365_plan`, `altium365_push` (dry run unless `dry_run` is false; held parts are never pushed), `ai_quote` and `ai_read` (datasheet reads, only after the user approved the price), `job_status` (slow tools hand back a job) and `list_example_specs`.
3. **CLI**, for scripts and CI: `ecadlib librarian`, `ecadlib release-check`, `ecadlib resolve`, `ecadlib review`, `ecadlib approve`, `ecadlib part` (change control), `ecadlib altium365`, `ecadlib triage`, `ecadlib project`, `ecadlib datasheet`, `ecadlib footprint`, `ecadlib check`, `ecadlib symbol`, `ecadlib step-gen`, `ecadlib dblib`, `ecadlib dblib-sync`, `ecadlib altium-lib`, `ecadlib altium-pkg`, `ecadlib altium-menu`, `ecadlib kicad-export`, `ecadlib init`, `ecadlib demo`, `ecadlib ai`, `ecadlib migrate` and more. `ecadlib -h` lists every command and `ecadlib <command> -h` shows its options; the pages under [docs](README.md) describe each one. The older `python3 -m ecadlib.<module>` form still works.

The UI and MCP server write everything to one workspace folder (default `./ecadlib_workspace`). It has these subfolders:
* `uploads/`
* `reports/`
* `footprints/ecadlib.pretty/`: approved footprints plus their source JSON
* `altium_models/PcbLib|SchLib/`
* `altium/`
* `altium365/`
* `http_cache/`

Both servers use only the standard library on top of the package requirements. The web UI binds to 127.0.0.1. It refuses requests with an unexpected Host header (DNS rebinding), cross-origin POSTs, and any file path outside the workspace.

### Using the web UI

* **First visit.** An empty workspace shows a welcome card: *Try it with a sample BOM* builds the 7-part sample (the one `ecadlib demo` uses) from ecadlib's standard parts, offline and without an API key; *Run the setup check* runs `ecadlib doctor`.
* **Drop a BOM anywhere on the page** (CSV or XLSX). Long actions (checking a BOM, filling the library, the sample) show live progress with one line per part ("3/14 SOT-23  ready") and a **Stop** button; the rest of the page keeps working. A stopped librarian run leaves the library plan as it was.
* **Parts as cards.** Step 2 lists every part with a footprint picture and chips: ready / held / waiting for sign-off, the cross-check result, KiCad / Altium match, and what reading its datasheet cost and with which model; the header adds the totals so far. Search and status chips filter the list.
* **AI steps ask first.** *Fill missing parts* never calls a model. Parts only a datasheet read can finish come back priced: the model, about how much and at most how much, and nothing is sent until you click *Read with AI*; above `[datasheet] ask_above_usd` (default $1.00, or `max_cost_usd` when set) it asks a second time. The reads are held to the amount you approved, and the real cost then shows next to the estimate. *AI cost of the rest* prices an older plan. The Altium dialog and the KiCad window work the same way. See [docs/api-keys.md](api-keys.md).
* **Click a part** to open its side panel: why it is held in plain words with the next step (wording from `ecadlib/messages.py`), both values when the cross-check or the KiCad / Altium comparison disagrees, the datasheet (at the page in question when known), and the actions: pick a symbol or footprint, release anyway, approve or reject.
* **Undo.** Approve and reject (in step 2 and in the review queue) show at once and are sent when the few-second Undo window closes; *Release anyway* is recorded at once and its Undo holds the part again.
* **Keyboard.** `?` lists the shortcuts: `/` search, `j` / `k` next / previous part, `Enter` open, `Esc` close, `h` held only, `u` choose a file, `1` `2` `3` steps, `q` review queue, `s` status. Light and dark themes follow the system; `prefers-reduced-motion` turns animations off.
* The same part cards are available as `Workspace.library_parts(bom)`, `GET /api/parts?bom=` and the MCP tool `library_parts` (`ecadlib/partview.py`).
