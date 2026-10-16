"""MCP server (stdio) so Claude or another agent can drive ecadlib with the same actions as the web UI.

    ecadlib mcp --lib path/to/kicad/libs [--workspace ecadlib_workspace] [--offline]

Claude Code:  claude mcp add ecadlib -- ecadlib mcp --lib /path/to/libs --workspace /path/to/workspace
Claude Desktop (claude_desktop_config.json):
    {"mcpServers": {"ecadlib": {"command": "ecadlib", "args": ["mcp", "--lib", "/path/to/libs",
                                                           "--workspace", "/path/to/workspace"]}}}
Give an absolute --workspace: desktop clients start servers from an arbitrary directory.

Standard library only: newline-delimited JSON-RPC 2.0 on stdin/stdout, tools capability only.
Logs go to stderr so they never corrupt the protocol stream."""
import argparse, contextlib, json, sys, traceback

from . import __version__
from .app import Workspace

PROTOCOL = "2025-06-18"
SUPPORTED = (PROTOCOL, "2025-03-26", "2024-11-05")

TOOLS = [
    {"name": "workspace_info",
     "description": "Show the workspace folder, indexed KiCad libraries, symbol/footprint counts, part sources and uploaded BOMs.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "triage_bom",
     "description": "Triage a BOM (CSV/XLSX path) against the local library and part sources. Returns one row per line with "
                    "status in_library / in_library (generated footprint) / available_easyeda / easyeda_footprint_only / "
                    "needs_footprint_generation / unverified (source unreachable) / dnp (not fitted). Set summary_only "
                    "for counts plus the lines that need work, which keeps large BOMs short.",
     "inputSchema": {"type": "object", "required": ["bom"],
                     "properties": {"bom": {"type": "string", "description": "path to the BOM file"},
                                    "summary_only": {"type": "boolean"}}}},
    {"name": "triage_history",
     "description": "Past triages in this workspace (read-only, nothing is looked up again): without id, the list "
                    "newest first with BOM, when, who ran it and status counts; with an id from that list, that "
                    "triage's full result (one row per line with its status) and report files.",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "string", "description": "triage id from the list"}}}},
    {"name": "preview_footprint",
     "description": "Generate an IPC-7351B footprint from package dimensions (ecadlib package_schema.json) without saving. "
                    "Returns Z/G/X, pad list, courtyard and any warnings. Read dimensions from the datasheet drawing; "
                    "never invent them.",
     "inputSchema": {"type": "object", "required": ["spec"],
                     "properties": {"spec": {"type": "object", "description": "package spec matching package_schema.json"},
                                    "density": {"type": "string", "enum": ["A", "B", "C"]}}}},
    {"name": "approve_footprint",
     "description": "Save a generated footprint into the workspace library as KiCad .kicad_mod and Altium .PcbLib. Only "
                    "call after the user has reviewed the preview. Fails if a footprint with that name exists unless "
                    "overwrite is true; ask the user before overwriting.",
     "inputSchema": {"type": "object", "required": ["spec"],
                     "properties": {"spec": {"type": "object"}, "density": {"type": "string", "enum": ["A", "B", "C"]},
                                    "overwrite": {"type": "boolean"}}}},
    {"name": "preview_symbol",
     "description": "Lay out an IC symbol with pins grouped by function (power top, ground bottom, reset/clock/debug left, "
                    "ports and peripherals right; same-name power pins stacked; multi-part above the rules' split_pins). "
                    "Pins come from the datasheet pin table; never invent them. Returns the grouping, parts, problems "
                    "and an SVG. save writes an Altium .SchLib into the workspace (ask before overwrite).",
     "inputSchema": {"type": "object", "required": ["pins"],
                     "properties": {"pins": {"type": "array", "items": {"type": "object", "required": ["num", "name"],
                                             "properties": {"num": {"type": "string"}, "name": {"type": "string"},
                                                            "type": {"type": "string"}, "group": {"type": "string"}}}},
                                    "name": {"type": "string"}, "split": {"type": "integer", "description": "multi-part above this many pins (0 = never)"},
                                    "stack": {"type": "string", "enum": ["stack", "hide", "none"]},
                                    "save": {"type": "boolean"}, "overwrite": {"type": "boolean"}}}},
    {"name": "export_altium_dblib",
     "description": "Write an Altium DbLib (SQLite + XLSX + .DbLib files) for a BOM into the workspace altium/ folder.",
     "inputSchema": {"type": "object", "required": ["bom"], "properties": {"bom": {"type": "string"}}}},
    {"name": "librarian_run",
     "description": "BOM to library in one go: triage the BOM, fill every missing symbol and footprint (official KiCad "
                    "libraries, then EasyEDA, then the datasheet reader), write the Altium .SchLib/.PcbLib models and "
                    "the release plan, and run the release check. Slow (minutes for a big BOM); finished parts are "
                    "skipped on a re-run unless refresh is set. Returns ready/held counts and a review row per part "
                    "saying what still needs a person.",
     "inputSchema": {"type": "object", "required": ["bom"],
                     "properties": {"bom": {"type": "string"},
                                    "sources": {"type": "array", "items": {"type": "string", "enum": ["kicad", "easyeda", "standard", "datasheet"]},
                                                "description": "sources to try, in order (default all four)"},
                                    "refresh": {"description": "true = redo every part; or a list of part names to redo",
                                                "anyOf": [{"type": "boolean"}, {"type": "array", "items": {"type": "string"}}]}}}},
    {"name": "release_check",
     "description": "Re-run the release check on the librarian's plan for a BOM. allow releases the named parts despite "
                    "their current failures (only when the user has looked and agreed; it is recorded in the plan); "
                    "unallow takes that back. user names the person giving the allow (recorded for sign-off). Held "
                    "parts are never pushed to Altium 365.",
     "inputSchema": {"type": "object", "required": ["bom"],
                     "properties": {"bom": {"type": "string"},
                                    "user": {"type": "string"},
                                    "allow": {"type": "array", "items": {"type": "string"}},
                                    "unallow": {"type": "array", "items": {"type": "string"}}}}},
    {"name": "held_parts",
     "description": "List the parts the release check held in the librarian's plan for a BOM: what is missing (symbol, "
                    "footprint) and why each is held. Use part_candidates / choose_part to fix one.",
     "inputSchema": {"type": "object", "required": ["bom"], "properties": {"bom": {"type": "string"}}}},
    {"name": "part_candidates",
     "description": "Ranked symbol or footprint candidates for one plan part (official KiCad library, the indexed local "
                    "libraries, ecadlib package specs). Each has an id, why it ranked, its pin/pad count, whether its "
                    "pins match the part's other half, and a suggested pin_map for a near miss. kind defaults to the "
                    "missing half (both when nothing is missing). May fetch KiCad library files.",
     "inputSchema": {"type": "object", "required": ["bom", "part"],
                     "properties": {"bom": {"type": "string"}, "part": {"type": "string"},
                                    "kind": {"type": "string", "enum": ["symbol", "footprint"]},
                                    "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10}}}},
    {"name": "choose_part",
     "description": "Use a candidate from part_candidates (kicad:Lib:Name, local:Lib:Name or spec:Name) for one half of "
                    "a plan part: converts it to Altium files, records the choice in the plan (a librarian re-run keeps "
                    "it) and re-runs the release check. pin_map maps symbol pin numbers to pad numbers. Show the user "
                    "the candidates and let them pick; do not choose on a guess.",
     "inputSchema": {"type": "object", "required": ["bom", "part", "kind", "candidate_id"],
                     "properties": {"bom": {"type": "string"}, "part": {"type": "string"},
                                    "kind": {"type": "string", "enum": ["symbol", "footprint"]},
                                    "candidate_id": {"type": "string"},
                                    "pin_map": {"type": "object", "additionalProperties": {"type": "string"}}}}},
    {"name": "approval_status",
     "description": "Sign-off state of the librarian's plan for a BOM: the approvals policy (approvals.json next to the "
                    "plan; none = approvals are recorded but not enforced) and, per part, who approved and what is "
                    "still missing. Parts awaiting approval are not pushed to Altium 365.",
     "inputSchema": {"type": "object", "required": ["bom"], "properties": {"bom": {"type": "string"}}}},
    {"name": "approve_part",
     "description": "Record a person's approve or reject decision for one plan part (reject needs a comment). user is the "
                    "person deciding, e.g. \"Ann Lee <ann@example.com>\": ask the user who they are and pass their own "
                    "answer; never approve on your own behalf or invent a name. Logged in approvals_audit.jsonl.",
     "inputSchema": {"type": "object", "required": ["bom", "part", "decision", "user"],
                     "properties": {"bom": {"type": "string"}, "part": {"type": "string"},
                                    "decision": {"type": "string", "enum": ["approve", "reject"]},
                                    "user": {"type": "string"}, "role": {"type": "string"},
                                    "comment": {"type": "string"}}}},
    {"name": "agent_queue_list",
     "description": "The library agent's review queue: work items (parts found new/changed/removed in watched projects "
                    "and BOMs, or upkeep findings) with status detected/building/proving/review/approved/pushed/failed/"
                    "rejected, the proof verdict and batch; counts per status and the batches. Read-only.",
     "inputSchema": {"type": "object", "properties": {
         "status": {"type": "string", "enum": ["detected", "building", "proving", "review", "approved", "pushed", "failed", "rejected"]},
         "batch": {"type": "string", "description": "batch id: only its items, plus the batch itself"}}}},
    {"name": "agent_queue_get",
     "description": "One review-queue item in full: the change that triggered it, build sources and notes, the visual "
                    "proof (verdict, findings), before/after picture paths, history and the allowed next states.",
     "inputSchema": {"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}}},
    {"name": "agent_queue_decide",
     "description": "Record a person's decision on a review-queue item: approve (review -> approved), reject (needs a "
                    "note), rebuild (review -> building) or retry (failed/rejected -> detected). approve/reject are also "
                    "recorded in the part's librarian plan through the approvals policy. user is the person deciding, "
                    "e.g. \"Ann Lee <ann@example.com>\": ask the user and pass their own answer; never decide on your own "
                    "behalf or invent a name. The person who started a build cannot approve it.",
     "inputSchema": {"type": "object", "required": ["id", "decision", "user"],
                     "properties": {"id": {"type": "string"},
                                    "decision": {"type": "string", "enum": ["approve", "reject", "rebuild", "retry"]},
                                    "user": {"type": "string"}, "role": {"type": "string"}, "note": {"type": "string"},
                                    "expect": {"type": "string", "description": "build_token from agent_queue_get: the "
                                               "decision is refused if the item was rebuilt since it was shown"}}}},
    {"name": "find_duplicates",
     "description": "Find duplicate parts (same MPN once packaging suffixes are stripped), footprints (same pad "
                    "geometry) and symbols (same pinout) across the librarian's plan and the workspace's altium_models/ "
                    "and footprints/. Each group has a recommended keeper. merge=true rewrites the plan to the keepers "
                    "(duplicates are then skipped by push; library files are never changed): only when the user asks.",
     "inputSchema": {"type": "object", "required": ["bom"],
                     "properties": {"bom": {"type": "string"}, "merge": {"type": "boolean", "default": False}}}},
    {"name": "sim_models",
     "description": "Find SPICE / IBIS simulation models for the librarian plan's parts: built-in models for resistors, "
                    "capacitors and inductors from their value, vendor models from the workspace's sim_models/ folder (and "
                    "`models` folders inside the workspace), Nexar documents and vendor URL patterns. Each model is parsed "
                    "and its pins matched to the symbol's: verified, verify (a person must check) or mismatch (never "
                    "attached). attach=true writes the SIM links into the .SchLib files and the KiCad Sim.* fields and "
                    "re-runs the release check: only when the user asks. Returns per part the model, source, pin status "
                    "and manufacturer model pages to download from by hand.",
     "inputSchema": {"type": "object", "required": ["bom"],
                     "properties": {"bom": {"type": "string"}, "attach": {"type": "boolean", "default": False},
                                    "models": {"type": "array", "items": {"type": "string"},
                                               "description": "workspace-relative folders of model files"}}}},
    {"name": "datasheet_estimate",
     "description": "Before reading datasheets with Claude: how many parts of the librarian plan still need a symbol or "
                    "footprint and have a datasheet link, and the token count and cost (USD, batch price) of reading them. "
                    "Exact input counts when the API key is set, rough otherwise. Sends nothing to the model.",
     "inputSchema": {"type": "object", "required": ["bom"], "properties": {"bom": {"type": "string"}}}},
    {"name": "datasheet_batch",
     "description": "Fill a whole BOM from datasheets in one go: every part of the librarian plan that still has no symbol "
                    "or footprint is read by Claude from its datasheet (BOM link, LCSC link, KiCad/EasyEDA link) as one "
                    "Message Batch (half price), built into .SchLib/.PcbLib, marked verify (held until a person checks "
                    "it) and the release check re-run. Costs money: call datasheet_estimate first and only run it when "
                    "the user agrees. wait=false (default) submits and returns at once; call again later to collect "
                    "(the batch id is kept). sync=true uses one Messages call per part instead of a batch.",
     "inputSchema": {"type": "object", "required": ["bom"],
                     "properties": {"bom": {"type": "string"}, "sync": {"type": "boolean", "default": False},
                                    "wait": {"type": "boolean", "default": False},
                                    "parts": {"type": "array", "items": {"type": "string"}, "description": "only these parts"}}}},
    {"name": "cross_checks",
     "description": "For a BOM's librarian plan: which parts read from a datasheet were cross-checked against a second "
                    "source (the standard package table, or a second read of only the critical numbers) and the outcome: "
                    "agreed, disagreed (held, with both values), unchecked or skipped. Sends nothing.",
     "inputSchema": {"type": "object", "required": ["bom"], "properties": {"bom": {"type": "string"}}}},
    {"name": "build_altium_models",
     "description": "Write the Altium .PcbLib files for a BOM's footprints that are in the KiCad libraries, plus generic "
                    "Resistor/Capacitor .SchLib symbols, into the workspace altium_models/ folder.",
     "inputSchema": {"type": "object", "required": ["bom"], "properties": {"bom": {"type": "string"}}}},
    {"name": "altium365_plan",
     "description": "Plan an Altium 365 release for a BOM: one managed component per part, marked ready when its "
                    ".SchLib and .PcbLib exist. Sends nothing.",
     "inputSchema": {"type": "object", "required": ["bom"], "properties": {"bom": {"type": "string"}}}},
    {"name": "altium365_push",
     "description": "Release the ready parts of a BOM into an Altium 365 workspace and update parameters of parts already "
                    "there. dry_run defaults to true and only returns the requests. A real push (dry_run false) changes "
                    "the user's workspace: only do it when the user has explicitly asked for that push. Needs NEXAR_TOKEN.",
     "inputSchema": {"type": "object", "required": ["bom"],
                     "properties": {"bom": {"type": "string"}, "workspace": {"type": "string", "description": "workspace URL"},
                                    "dry_run": {"type": "boolean", "default": True}}}},
    {"name": "upkeep_run",
     "description": "Run the library upkeep checks for a BOM (its librarian plan when there is one): lifecycle (NRND/EOL, "
                    "stock-outs), KiCad upstream source changes, price jumps and stock, Altium 365 drift. Reports only what "
                    "is new since the last upkeep run; a check whose credentials are missing is skipped and says so. "
                    "apply=true reconverts KiCad parts whose upstream sources changed (rewrites library files): only when "
                    "the user asks. Never opens GitHub issues or PRs (that is the `ecadlib upkeep --github` CLI).",
     "inputSchema": {"type": "object", "required": ["bom"],
                     "properties": {"bom": {"type": "string"},
                                    "checks": {"type": "array", "items": {"type": "string",
                                               "enum": ["lifecycle", "upstream", "sourcing", "a365"]},
                                               "description": "checks to run (default: all, or [upkeep] checks in ecadlib.toml)"},
                                    "apply": {"type": "boolean", "default": False},
                                    "workspace": {"type": "string", "description": "Altium 365 workspace URL for the a365 check"}}}},
    {"name": "file_formats",
     "description": "Which workspace files (plans, librarian state, queue, agent state, caches, ecadlib.toml) are in an "
                    "older or newer ecadlib format. migrate true upgrades the older ones in place (backups first); files "
                    "from a newer ecadlib are never changed (upgrade ecadlib). Read-only unless migrate is true.",
     "inputSchema": {"type": "object", "properties": {"migrate": {"type": "boolean", "default": False}}}},
    {"name": "rebuild_check",
     "description": "Prove a BOM's library is reproducible: rebuild it from its library.lock.json offline (HTTP cache "
                    "and cached model answers only, never a live call) in a temporary copy and report per part and file "
                    "identical / identical after normalising dates / drift, plus inputs that changed. Changes nothing.",
     "inputSchema": {"type": "object", "required": ["bom"], "properties": {"bom": {"type": "string"}}}},
    {"name": "agent_status",
     "description": "The library agent's state: last run (detected, built, in review, failed), review-queue counts and "
                    "its batch pull requests (or the batch folders when there is no GitHub token).",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "agent_run",
     "description": "Run one pass of the library agent: scan watched projects/BOMs, build and prove new parts, open or "
                    "update the batch PR. dry_run defaults to true (reports only). A real run (dry_run false) commits to "
                    "the library repo and opens PRs: only when the user asked for it.",
     "inputSchema": {"type": "object", "properties": {"dry_run": {"type": "boolean", "default": True}}}},
    {"name": "project_bom",
     "description": "Read the BOM straight from an Altium project (.PrjPcb, or a single .SchDoc): components from every "
                    "schematic grouped by MPN / Design Item ID with designators and quantities, footprints, Altium 365 "
                    "GUIDs, ActiveBOM choices and per-variant quantities. variant applies one project variant (DNP / "
                    "alternate parts). Writes a CSV that triage_bom and librarian_run accept (the project path works too).",
     "inputSchema": {"type": "object", "required": ["project"],
                     "properties": {"project": {"type": "string", "description": "path to the .PrjPcb or .SchDoc"},
                                    "variant": {"type": "string"}}}},
    {"name": "where_used",
     "description": "Scan folders of Altium projects and report where each part is used (project, designators, quantity, "
                    "revision, variants where it is not fitted), with flags: parts missing from the library, library parts "
                    "used nowhere, duplicates in use, old Altium 365 revisions, NRND/obsolete parts. Read-only.",
     "inputSchema": {"type": "object", "required": ["projects"],
                     "properties": {"projects": {"type": "array", "items": {"type": "string"},
                                                 "description": "folders (searched recursively) or project files"},
                                    "part": {"type": "string", "description": "only this part (MPN, item id, LibRef or GUID)"},
                                    "plan": {"type": "string", "description": "librarian plan.json to compare against"}}}},
    {"name": "a365_pull",
     "description": "Import the components already in an Altium 365 workspace into the plan (status existing, with "
                    "revision and model GUIDs), refresh ones pulled before and download their .SchLib/.PcbLib. With bom, "
                    "into that BOM's librarian plan (the librarian then reuses them instead of fetching); without, into "
                    "altium365/workspace_plan.json. Changes local files only; conflicts are reported. Needs NEXAR_TOKEN.",
     "inputSchema": {"type": "object", "required": ["workspace"],
                     "properties": {"workspace": {"type": "string", "description": "workspace URL"},
                                    "bom": {"type": "string"},
                                    "folders": {"type": "array", "items": {"type": "string"},
                                                "description": "import only components under these workspace folders"}}}},
    {"name": "a365_status",
     "description": "Compare the plan with the Altium 365 workspace without changing anything: new in the workspace, "
                    "changed there since the last pull, changed locally, conflicts (both), deleted. Needs NEXAR_TOKEN.",
     "inputSchema": {"type": "object", "required": ["workspace"],
                     "properties": {"workspace": {"type": "string"}, "bom": {"type": "string"}}}},
    {"name": "propose_rules",
     "description": "Set up the library rules (ecadlib.toml: naming, required parameters, folders, layers, footprint density, "
                    "BOM columns) with minimal effort: scan the user's existing libraries (Altium .SchLib/.PcbLib/.DbLib "
                    "folders, parts tables, KiCad libraries, BOMs, an Altium 365 component list JSON) and propose each "
                    "setting with evidence and a confidence. Writes nothing. Show the user the proposal and ask the "
                    "`questions` (ambiguous settings) in plain words; pass their answers back as choices {key: value} to "
                    "get the final toml. paths default to the workspace's --lib folders and uploaded BOMs.",
     "inputSchema": {"type": "object",
                     "properties": {"paths": {"type": "array", "items": {"type": "string"},
                                              "description": "folders or files to scan"},
                                    "a365": {"type": "array", "items": {"type": "string"},
                                             "description": "Altium 365 component list JSON or check-a365 --state file"},
                                    "choices": {"type": "object", "description": "answers: setting key -> value"},
                                    "full": {"type": "boolean", "description": "include every evidence line (default: short)"}}}},
    {"name": "save_rules",
     "description": "Write the workspace's ecadlib.toml (the `toml` from propose_rules, possibly edited with the user). Only "
                    "after the user has agreed to the proposal. If a different file exists nothing is written and the diff "
                    "comes back (needs_confirm): show it and call again with overwrite true only when the user says to "
                    "replace it (the old file is kept as ecadlib.toml.bak).",
     "inputSchema": {"type": "object", "required": ["toml"],
                     "properties": {"toml": {"type": "string"}, "overwrite": {"type": "boolean", "default": False}}}},
    {"name": "agent_watch_scan",
     "description": "Library agent watch step: scan the watched projects and BOMs (rules [agent.watch] sources, or the "
                    "sources given: bom_file / folder / altium_project / a365_project with a ref) and return the parts that "
                    "are new (not yet in the workspace library), changed (footprint, value, datasheet or LCSC) or removed "
                    "since the last scan, plus per-source status and errors. A source that fails keeps its last state. "
                    "dry_run reports without saving the scan state. Altium 365 projects need NEXAR_TOKEN.",
     "inputSchema": {"type": "object",
                     "properties": {"sources": {"type": "array", "items": {"type": "object", "required": ["type", "ref"],
                                    "properties": {"type": {"type": "string", "enum": ["bom_file", "folder", "altium_project",
                                                                                        "a365_project"]},
                                                   "ref": {"type": "string"}, "name": {"type": "string"},
                                                   "workspace": {"type": "string"}, "variant": {"type": "string"}}}},
                                    "dry_run": {"type": "boolean", "default": False}}}},
    {"name": "visual_check",
     "description": "Visual proof of a part: renders our footprint (top view, pin 1, pitch, dimensions) and symbol next to "
                    "the datasheet's drawing / pin-table pages and asks Claude whether they match (pitch, pad count and "
                    "numbering, pin 1, top vs bottom view, body size, pin names/numbers). Verdict pass / doubt / fail / "
                    "skipped (no API key) with findings and the image paths, so you can look at them too.",
     "inputSchema": {"type": "object", "required": ["target"],
                     "properties": {"target": {"type": "string", "description": "part name in the workspace, or a workspace path "
                                                                                 "to a .kicad_mod / package spec .json"},
                                    "datasheet": {"type": "string", "description": "datasheet PDF (workspace path or URL)"},
                                    "pages": {"type": "string", "description": "datasheet pages, e.g. 3,7-8 (default: picked by text)"},
                                    "symbol": {"type": "string", "description": "symbol file (.SchLib / symbol JSON / pin CSV)"},
                                    "render_only": {"type": "boolean"}}}},
    {"name": "list_example_specs",
     "description": "Return the example package specs (useful as templates for preview_footprint).",
     "inputSchema": {"type": "object", "properties": {}}},    {"name": "workspace_status",
     "description": "One-screen summary of the workspace (read-only): parts in each librarian plan with ready / held / "
                    "waiting-for-sign-off counts, the last triage, the last upkeep run and the library agent's state and "
                    "review-queue counts. Start here to see what needs attention.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "doctor",
     "description": "Check the setup: Python version, packages and tools (pdftoppm, kicad-cli, git), which API keys are "
                    "set (names only), whether the rules file is valid (file:line of the error), the workspace is "
                    "writable and the library folders exist. Each check is OK / WARN / FAIL with a fix hint.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "list_plugins",
     "description": "Installed part-source and exporter plugins (Python entry points ecadlib.providers / ecadlib.exporters), "
                    "the part-source order in use ([providers] order) and the [notify] webhook targets (names of their "
                    "environment variables and whether they are set; never the URLs).",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "plugin_export",
     "description": "Run an exporter plugin on the librarian plan of a BOM; files go to <workspace>/exports/<name>/.",
     "inputSchema": {"type": "object", "required": ["name", "bom"],
                     "properties": {"name": {"type": "string", "description": "exporter name from list_plugins"},
                                    "bom": {"type": "string", "description": "the BOM the librarian ran on"}}}},
    {"name": "export_parts",
     "description": "Write the library's parts table for a BOM's librarian plan with stable columns (Part ID, Status, "
                    "Category, Description, Value, Manufacturer, Manufacturer Part Number, Datasheet, suppliers, symbol / "
                    "footprint refs and files, KiCad lib ids, release check, sign-off, ...) as CSV, JSON or XLSX for PLM / "
                    "ERP imports. Returns the workspace path of the file.",
     "inputSchema": {"type": "object", "required": ["bom"],
                     "properties": {"bom": {"type": "string"}, "format": {"type": "string", "enum": ["csv", "json", "xlsx"]},
                                    "include_held": {"type": "boolean", "default": False}}}},
    {"name": "kicad_dblib",
     "description": "Write the BOM's library as KiCad files (.kicad_sym, .pretty, lib tables) plus a KiCad 7+ database "
                    "library: a .kicad_dbl (ODBC to SQLite) and the SQLite parts table, one table per category, and a "
                    "Database row in sym-lib-table. KiCad needs an SQLite ODBC driver to open it.",
     "inputSchema": {"type": "object", "required": ["bom"],
                     "properties": {"bom": {"type": "string"}, "name": {"type": "string", "description": "library name"},
                                    "driver": {"type": "string", "description": "ODBC driver name (default SQLite3 ODBC Driver)"},
                                    "include_held": {"type": "boolean", "default": False}}}},
    {"name": "tool_packages",
     "description": "Package the BOM's library to plug into the tools: a KiCad Plugin and Content Manager zip (install "
                    "with 'Install from File...'; libraries appear as PCM_<name>) and an Altium folder with a .LibPkg "
                    "(compile to .IntLib), an install script project (.PrjScr) and a relative-path DbLib. Returns "
                    "workspace paths and any problems found.",
     "inputSchema": {"type": "object", "required": ["bom"],
                     "properties": {"bom": {"type": "string"}, "name": {"type": "string", "description": "library name"},
                                    "kicad": {"type": "integer", "enum": [7, 8, 9, 10], "default": 8,
                                              "description": "oldest KiCad version to install the zip"},
                                    "include_held": {"type": "boolean", "default": False}}}},
]


def call_tool(ws, name, args):
    with contextlib.redirect_stdout(sys.stderr):          # library code prints progress; keep stdout for JSON-RPC
        if name == "workspace_info":
            return ws.info()
        if name == "triage_bom":
            r = ws.triage(args["bom"])
            if args.get("summary_only"):
                todo = [x for x in r["rows"] if not x["status"].startswith(("in_library", "dnp"))]
                return {"bom": r["bom"], "counts": r["counts"], "reports": r["reports"],
                        "needs_work": [{k: x[k] for k in ("line", "designator", "mpn", "package", "status", "notes")} for x in todo]}
            return {k: r[k] for k in ("bom", "mapping", "counts", "reports", "rows")}
        if name == "triage_history":
            return ws.triage_result(args["id"]) if args.get("id") else ws.triage_history()
        if name == "preview_footprint":
            r = ws.preview_footprint(args["spec"], args.get("density"))
            return {"name": r["name"], "report": r["report"]}
        if name == "preview_symbol":
            return ws.preview_symbol(args["pins"], args.get("name") or "PART", args.get("split"), args.get("stack"),
                                     args.get("save") is True, args.get("overwrite") is True)
        if name == "approve_footprint":
            return ws.approve_footprint(args["spec"], args.get("density"), bool(args.get("overwrite")))
        if name == "librarian_run":
            ref = args.get("refresh")
            r = ws.librarian_run(args["bom"], args.get("sources"), (), [] if ref is True else (ref or None))
            r.pop("log", None)
            return r
        if name == "release_check":
            r = ws.release_check(args["bom"], args.get("allow") or [], args.get("unallow") or [], args.get("user"))
            r.pop("log", None)
            return r
        if name == "held_parts":
            return ws.held_parts(args["bom"])
        if name == "part_candidates":
            return ws.part_candidates(args["bom"], args["part"], args.get("kind"), args.get("limit") or 10)
        if name == "choose_part":
            r = ws.choose_part(args["bom"], args["part"], args["kind"], args["candidate_id"], args.get("pin_map") or None)
            r.pop("log", None)
            return r
        if name == "approval_status":
            return ws.approval_status(args["bom"])
        if name == "approve_part":
            r = ws.approve_part(args["bom"], args["part"], args["decision"], args["user"], args.get("role"),
                                args.get("comment") or "")
            r.pop("log", None)
            return r
        if name == "agent_queue_list":
            return ws.agent_queue_list(args.get("status") or None, args.get("batch") or None)
        if name == "agent_queue_get":
            return ws.agent_queue_get(args["id"])
        if name == "agent_queue_decide":
            return ws.agent_queue_decide(args["id"], args["decision"], args["user"], args.get("role"), args.get("note") or "",
                                         via="ecadlib mcp", expect=args.get("expect") or None)
        if name == "find_duplicates":
            r = ws.find_duplicates(args["bom"], args.get("merge") is True)
            if "summary" in r:
                r["summary"].pop("log", None)
            return r
        if name == "sim_models":
            return ws.sim_models(args["bom"], args.get("attach") is True, args.get("models") or None)
        if name == "datasheet_estimate":
            return ws.datasheet_estimate(args["bom"])
        if name == "datasheet_batch":
            r = ws.datasheet_batch(args["bom"], args.get("sync") is True, args.get("wait") is True, args.get("parts") or None)
            r["log"] = r["log"][-20:]
            return r
        if name == "cross_checks":
            return ws.cross_checks(args["bom"])
        if name == "build_altium_models":
            return ws.build_altium_models(args["bom"])
        if name == "altium365_plan":
            r = ws.altium365_plan(args["bom"])
            r["items"] = [{k: i[k] for k in ("name", "folder", "status", "symbol", "footprint")} for i in r["items"]]
            return r
        if name == "altium365_push":
            return ws.altium365_push(args["bom"], args.get("workspace"), dry_run=args.get("dry_run", True) is not False)
        if name == "a365_pull":
            return ws.a365_pull(args["workspace"], args.get("bom"), args.get("folders"))
        if name == "a365_status":
            return ws.a365_status(args["workspace"], args.get("bom"))
        if name == "export_altium_dblib":
            return ws.export_dblib(args["bom"])
        if name == "upkeep_run":
            return ws.upkeep_run(args["bom"], args.get("checks") or None, args.get("apply") is True, args.get("workspace"))
        if name == "file_formats":
            return ws.file_formats(args.get("migrate") is True)
        if name == "rebuild_check":
            return ws.rebuild_check(args["bom"])
        if name == "agent_status":
            return ws.agent_status()
        if name == "agent_run":
            return ws.agent_run(args.get("dry_run", True) is not False)
        if name == "project_bom":
            return ws.project_bom(args["project"], args.get("variant") or None)
        if name == "where_used":
            return ws.where_used(args["projects"], args.get("part") or None, args.get("plan") or None)
        if name == "agent_watch_scan":
            return ws.agent_watch_scan(args.get("sources") or None, args.get("dry_run") is True)
        if name == "visual_check":
            return ws.visual_check(args["target"], args.get("datasheet") or None, args.get("pages") or None,
                                   args.get("symbol") or None, args.get("render_only") is True)
        if name == "list_example_specs":
            return ws.example_specs()
        if name == "propose_rules":
            r = ws.propose_rules(args.get("paths") or None, args.get("a365") or None, args.get("choices") or None)
            if not args.get("full"):
                for st in r["settings"]:
                    st["evidence"] = st["evidence"][:3]
                r.pop("rules", None)
            return r
        if name == "save_rules":
            return ws.save_rules(args["toml"], args.get("overwrite") is True)
        if name == "workspace_status":
            return ws.status()
        if name == "doctor":
            return ws.doctor()
        if name == "list_plugins":
            return ws.plugins()
        if name == "plugin_export":
            return ws.plugin_export(args["name"], args["bom"])
        if name == "export_parts":
            return ws.export_parts(args["bom"], args.get("format") or "csv", args.get("include_held") is True)
        if name == "kicad_dblib":
            return ws.kicad_dblib(args["bom"], args.get("name") or "ecadlib", args.get("driver") or None,
                                  args.get("include_held") is True)
        if name == "tool_packages":
            return ws.tool_packages(args["bom"], args.get("name") or "ecadlib", args.get("kicad") or 8,
                                    args.get("include_held") is True)
    raise KeyError(f"unknown tool {name}")


def handle(ws, msg):
    """Return a JSON-RPC response dict, or None for notifications."""
    if not isinstance(msg, dict) or not isinstance(msg.get("params", {}), (dict, type(None))):
        mid = msg.get("id") if isinstance(msg, dict) else None
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32600, "message": "invalid request"}}
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if mid is None:
        return None                                       # notifications (initialized, cancelled) need no answer
    if method == "initialize":
        asked = params.get("protocolVersion")
        result = {"protocolVersion": asked if asked in SUPPORTED else PROTOCOL,
                  "capabilities": {"tools": {}}, "serverInfo": {"name": "ecadlib", "version": __version__}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        if not isinstance(params.get("name"), str) or params["name"] not in {t["name"] for t in TOOLS}:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"unknown tool: {params.get('name')}"}}
        if not isinstance(params.get("arguments") or {}, dict):
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "arguments must be an object"}}
        tool = next(t for t in TOOLS if t["name"] == params["name"])
        missing = [k for k in tool["inputSchema"].get("required", []) if k not in (params.get("arguments") or {})]
        if missing:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"missing argument(s): {', '.join(missing)}"}}
        try:
            out = call_tool(ws, params.get("name"), params.get("arguments") or {})
            result = {"content": [{"type": "text", "text": json.dumps(out, indent=1, default=str)}], "isError": False}
        except Exception as e:
            user_error = isinstance(e, (ValueError, KeyError, FileExistsError, FileNotFoundError, PermissionError))
            if not user_error:                            # a bug: keep the traceback; a wrong id / path is the caller's
                traceback.print_exc(file=sys.stderr)
            msg = (e.args[0] if isinstance(e, KeyError) and e.args else str(e)) if user_error else f"{type(e).__name__}: {e}"
            result = {"content": [{"type": "text", "text": msg}], "isError": True}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ecadlib mcp", description="ecadlib MCP server (stdio)")
    ap.add_argument("--lib", action="append", default=[])
    ap.add_argument("--workspace", default="ecadlib_workspace")
    ap.add_argument("--providers")
    ap.add_argument("--offline", action="store_true")
    a = ap.parse_args(argv)
    ws = Workspace(a.workspace, a.lib, a.providers.split(",") if a.providers else None, a.offline)
    serve(ws, sys.stdin.buffer, sys.stdout.buffer)


def serve(ws, inp, out):
    """JSON-RPC over newline-delimited UTF-8 (bytes in, bytes out, whatever the OS locale is).
    No single bad line can stop the server: it gets an error response and the next line is read."""
    for raw in inp:
        line = raw.decode("utf-8", errors="replace")
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except (ValueError, RecursionError):                # bad JSON, or nested deeper than Python can parse
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            try:
                resp = handle(ws, msg)
            except Exception as e:                          # a bug or odd input in one request: report and go on
                traceback.print_exc(file=sys.stderr)
                mid = msg.get("id") if isinstance(msg, dict) and isinstance(msg.get("id"), (str, int)) else None
                resp = {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": f"internal error: {type(e).__name__}"}}
        if resp is not None:
            out.write((json.dumps(resp, ensure_ascii=False) + "\n").encode("utf-8"))
            out.flush()


if __name__ == "__main__":
    main()
