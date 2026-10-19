"""MCP server so Claude, Codex, Grok or another agent can drive ecadlib with the same actions as the web UI.

    ecadlib mcp --lib path/to/kicad/libs [--workspace ecadlib_workspace] [--offline] [--tools core] [--http]

Claude Code:  claude mcp add ecadlib -- ecadlib mcp --lib /path/to/libs --workspace /path/to/workspace
Claude Desktop (claude_desktop_config.json):
    {"mcpServers": {"ecadlib": {"command": "ecadlib", "args": ["mcp", "--lib", "/path/to/libs",
                                                           "--workspace", "/path/to/workspace"]}}}
Codex, Grok and HTTP mode: docs/mcp.md. Give an absolute --workspace: desktop clients start servers from an arbitrary
directory.

Standard library only (plus jsonschema): newline-delimited JSON-RPC 2.0 on stdin/stdout (ecadlib.mcp_http for
Streamable HTTP), tools capability only. Every call runs as a job: one that is not done within --wait seconds answers
with a job id for job_status instead of running into the client's timeout. Logs go to stderr; file descriptor 1 is
moved aside so not even a child process can corrupt the protocol stream."""
import argparse, contextlib, json, re, sys, threading, traceback

from . import __version__
from .app import Workspace

PROTOCOL = "2025-06-18"
SUPPORTED = ("2025-11-25", PROTOCOL, "2025-03-26", "2024-11-05")

TOOLS = [
    {"name": "workspace_info",
     "description": "Show the workspace folder, indexed KiCad libraries, symbol/footprint counts, part sources and uploaded BOMs.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "upload_bom",
     "description": "Put a BOM into the workspace when the file is not on this computer (a remote client, or a BOM the "
                    "user pasted): content is the CSV / TSV text, or content_base64 an .xlsx file. Returns the `bom` path "
                    "to pass to triage_bom, librarian_run and the other BOM tools.",
     "inputSchema": {"type": "object", "required": ["filename"],
                     "properties": {"filename": {"type": "string", "description": "e.g. my_board.csv"},
                                    "content": {"type": "string", "description": "the file's text (CSV / TSV)"},
                                    "content_base64": {"type": "string", "description": "the file's bytes, base64 (.xlsx)"}}}},
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
                     "properties": {"spec": {"type": "object", "additionalProperties": True,
                                             "description": "package spec matching package_schema.json (start from "
                                                            "list_example_specs)"},
                                    "density": {"type": "string", "enum": ["A", "B", "C"]}}}},
    {"name": "approve_footprint",
     "description": "Save a generated footprint into the workspace library as KiCad .kicad_mod and Altium .PcbLib. Only "
                    "call after the user has reviewed the preview. Fails if a footprint with that name exists unless "
                    "overwrite is true; ask the user before overwriting.",
     "inputSchema": {"type": "object", "required": ["spec"],
                     "properties": {"spec": {"type": "object", "additionalProperties": True,
                                             "description": "the same spec as preview_footprint"}, "density": {"type": "string", "enum": ["A", "B", "C"]},
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
                    "libraries, then EasyEDA, then the standard package tables), write the Altium .SchLib/.PcbLib models "
                    "and the release plan, and run the release check. Never pays for a model: parts only a datasheet "
                    "read can finish are priced in `ai` (model, about / at most USD); show that to the user and only if "
                    "they agree call ai_read. Slow (minutes for a big BOM; may come back as a job for job_status); "
                    "finished parts are skipped on a re-run unless refresh / refresh_all is set. Returns ready/held counts and a review row per part "
                    "saying what still needs a person: `says` holds each reason as a plain sentence (`text`) and the "
                    "next step (`fix`); show those to the user, keep `code` for logic.",
     "inputSchema": {"type": "object", "required": ["bom"],
                     "properties": {"bom": {"type": "string"},
                                    "sources": {"type": "array", "items": {"type": "string", "enum": ["kicad", "easyeda", "standard", "datasheet"]},
                                                "description": "sources to try, in order (default all four)"},
                                    "refresh": {"type": "array", "items": {"type": "string"},
                                                "description": "part names to redo"},
                                    "refresh_all": {"type": "boolean", "description": "redo every part"}}}},
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
                    "footprint) and why each is held: `reasons` has a plain sentence (`text`) and the next step (`fix`) "
                    "per reason, to show the user as written. Use part_candidates / choose_part to fix one.",
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
    {"name": "find_duplicates",
     "description": "Find duplicate parts (same MPN once packaging suffixes are stripped), footprints (same pad "
                    "geometry) and symbols (same pinout) across the librarian's plan and the workspace's altium_models/ "
                    "and footprints/. Each group has a recommended keeper. merge=true rewrites the plan to the keepers "
                    "(duplicates are then skipped by push; library files are never changed): only when the user asks.",
     "inputSchema": {"type": "object", "required": ["bom"],
                     "properties": {"bom": {"type": "string"}, "merge": {"type": "boolean", "default": False}}}},
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
    {"name": "library_parts",
     "description": "A BOM's library plan part by part, in plain words: ready, held (each reason as a sentence plus the "
                    "next step) or waiting for sign-off; the cross-check and KiCad/Altium results with both values when "
                    "they disagree; the datasheet link and page; what the datasheet reads cost and which model. No network.",
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
                    "stock-outs), KiCad upstream source changes, Altium 365 drift, held changes to released parts. Reports only what "
                    "is new since the last upkeep run; a check whose credentials are missing is skipped and says so. "
                    "apply=true reconverts KiCad parts whose upstream sources changed (rewrites library files): only when "
                    "the user asks. Never opens GitHub issues or PRs (that is the `ecadlib upkeep --github` CLI).",
     "inputSchema": {"type": "object", "required": ["bom"],
                     "properties": {"bom": {"type": "string"},
                                    "checks": {"type": "array", "items": {"type": "string",
                                               "enum": ["lifecycle", "upstream", "a365", "changes"]},
                                               "description": "checks to run (default: all, or [upkeep] checks in ecadlib.toml)"},
                                    "apply": {"type": "boolean", "default": False},
                                    "workspace": {"type": "string", "description": "Altium 365 workspace URL for the a365 check"}}}},
    {"name": "file_formats",
     "description": "Which workspace files (plans, librarian state, caches, ecadlib.toml) are in an "
                    "older or newer ecadlib format. migrate true upgrades the older ones in place (backups first); files "
                    "from a newer ecadlib are never changed (upgrade ecadlib). Read-only unless migrate is true.",
     "inputSchema": {"type": "object", "properties": {"migrate": {"type": "boolean", "default": False}}}},
    {"name": "rebuild_check",
     "description": "Prove a BOM's library is reproducible: rebuild it from its library.lock.json offline (HTTP cache "
                    "and cached model answers only, never a live call) in a temporary copy and report per part and file "
                    "identical / identical after normalising dates / drift, plus inputs that changed. Changes nothing.",
     "inputSchema": {"type": "object", "required": ["bom"], "properties": {"bom": {"type": "string"}}}},
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
                                    "choices": {"type": "object", "additionalProperties": True,
                                                "description": "answers: setting key -> value"},
                                    "full": {"type": "boolean", "description": "include every evidence line (default: short)"}}}},
    {"name": "save_rules",
     "description": "Write the workspace's ecadlib.toml (the `toml` from propose_rules, possibly edited with the user). Only "
                    "after the user has agreed to the proposal. If a different file exists nothing is written and the diff "
                    "comes back (needs_confirm): show it and call again with overwrite true only when the user says to "
                    "replace it (the old file is kept as ecadlib.toml.bak).",
     "inputSchema": {"type": "object", "required": ["toml"],
                     "properties": {"toml": {"type": "string"}, "overwrite": {"type": "boolean", "default": False}}}},
    {"name": "learn_house_style",
     "description": "Teach ecadlib the user's house style from their current library so new parts match it: naming, "
                    "description patterns, how values are written (1% or ±1%), the parameters most parts carry, the "
                    "active-low pin notation, and real example parts. `direction` is the user's own written instruction "
                    "(e.g. 'IC descriptions start with the function in capitals'); it wins over what the library does. "
                    "ai true: also prices having the configured model ([style] / [ai]) write a style guide and turn the "
                    "direction into ecadlib.toml settings; the quote comes back as `ai` and nothing is sent until "
                    "house_style_guide is called with the approved_usd the user agreed to. Writes house_style.json.",
     "inputSchema": {"type": "object",
                     "properties": {"paths": {"type": "array", "items": {"type": "string"},
                                              "description": "library folders or files (default: the --lib folders)"},
                                    "direction": {"type": "array", "items": {"type": "string"},
                                                  "description": "lines of the user's direction, added to the saved ones"},
                                    "ai": {"type": "boolean", "default": False},
                                    "model": {"type": "string", "description": "model for ai (default [style] / [ai])"}}}},
    {"name": "house_style",
     "description": "Show the learned house style (or add direction with `direction`, clear true to start over).",
     "inputSchema": {"type": "object",
                     "properties": {"direction": {"type": "string"}, "clear": {"type": "boolean", "default": False}}}},
    {"name": "check_house_style",
     "description": "How far each part of a BOM's librarian plan is from the house style, per part and field.",
     "inputSchema": {"type": "object", "required": ["bom"],
                     "properties": {"bom": {"type": "string"}, "part": {"type": "string"}}}},
    {"name": "apply_house_style",
     "description": "Adapt a BOM's plan to the house style: exact fixes by code; ai true also lets the model reword "
                    "descriptions and values (checked: no new numbers, manufacturer / part numbers / links never "
                    "touched; this calls the model, which costs money: ask the user first). dry_run (default true) shows the "
                    "changes; call again with dry_run false after the user agrees.",
     "inputSchema": {"type": "object", "required": ["bom"],
                     "properties": {"bom": {"type": "string"}, "part": {"type": "string"},
                                    "ai": {"type": "boolean", "default": False}, "model": {"type": "string"},
                                    "dry_run": {"type": "boolean", "default": True}}}},
    {"name": "visual_check",
     "description": "Visual proof of a part: renders our footprint (top view, pin 1, pitch, dimensions) and symbol next to "
                    "the datasheet's drawing / pin-table pages and asks Claude whether they match (pitch, pad count and "
                    "numbering, pin 1, top vs bottom view, body size, pin names/numbers); the Claude call costs money "
                    "(render_only true sends nothing). Verdict pass / doubt / fail / "
                    "skipped (no API key) with findings and the image paths, so you can look at them too.",
     "inputSchema": {"type": "object", "required": ["target"],
                     "properties": {"target": {"type": "string", "description": "part name in the workspace, or a workspace path "
                                                                                 "to a .kicad_mod / package spec .json"},
                                    "datasheet": {"type": "string", "description": "datasheet PDF (workspace path or URL)"},
                                    "pages": {"type": "string", "description": "datasheet pages, e.g. 3,7-8 (default: picked by text)"},
                                    "symbol": {"type": "string", "description": "symbol file (.SchLib / symbol JSON / pin CSV)"},
                                    "render_only": {"type": "boolean"}}}},
    {"name": "ai_quote",
     "description": "What reading the datasheets of a BOM's librarian plan with the configured model would cost, sending "
                    "nothing: parts to read, model, about / at most USD, the user's limit and whether an API key is set. "
                    "Show it to the user before ai_read.",
     "inputSchema": {"type": "object", "required": ["bom"],
                     "properties": {"bom": {"type": "string"},
                                    "parts": {"type": "array", "items": {"type": "string"}, "description": "only these parts"}}}},
    {"name": "ai_read",
     "description": "Read the datasheets ai_quote priced, after the user agreed to that cost: builds the parts, marks them "
                    "verify (held until a person checks them) and re-runs the release check. approved_usd is the amount "
                    "the user said yes to (the quote's at-most figure); the reads are capped to it and refused if the "
                    "estimate has gone up. Costs money: never call it without the user's yes. May come back as a job.",
     "inputSchema": {"type": "object", "required": ["bom", "approved_usd"],
                     "properties": {"bom": {"type": "string"},
                                    "approved_usd": {"type": "number", "description": "USD the user approved"},
                                    "over_limit_ok": {"type": "boolean", "description": "the user agreed to go over their limit"},
                                    "price_unknown_ok": {"type": "boolean",
                                                         "description": "the user agreed to a model with no known price"},
                                    "parts": {"type": "array", "items": {"type": "string"}, "description": "only these parts"}}}},
    {"name": "house_style_guide",
     "description": "Have the model write the house style guide priced by learn_house_style (ai true), after the user "
                    "agreed to that cost. approved_usd is the amount they said yes to. Costs money.",
     "inputSchema": {"type": "object", "required": ["approved_usd"],
                     "properties": {"approved_usd": {"type": "number"},
                                    "over_limit_ok": {"type": "boolean"}, "price_unknown_ok": {"type": "boolean"},
                                    "model": {"type": "string"}}}},
    {"name": "job_status",
     "description": "A slow tool that did not finish in time came back with a job id: this waits up to wait_seconds "
                    "(default the server's wait, about 45) and returns the tool's result once done, else the progress "
                    "so far. cancel true asks the job to stop at its next part. Without id: the recent jobs.",
     "inputSchema": {"type": "object",
                     "properties": {"id": {"type": "string", "description": "job id from the slow tool's answer"},
                                    "cancel": {"type": "boolean"},
                                    "wait_seconds": {"type": "integer", "minimum": 0, "maximum": 600}}}},
    {"name": "list_example_specs",
     "description": "Return the example package specs (useful as templates for preview_footprint).",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "workspace_status",
     "description": "One-screen summary of the workspace (read-only): parts in each librarian plan with ready / held / "
                    "waiting-for-sign-off counts, the last triage and the last upkeep run. Start here to see what needs "
                    "attention.",
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
    {"name": "parity",
     "description": "Check that the BOM's KiCad and Altium libraries match: read both back from the files and compare "
                    "every part's pads (position, size, shape, rotation, drill, side, paste, mask), exposed pad, courtyard, "
                    "3D model, symbol pins (numbers, names, types), pin-to-pad mapping and key fields. kicad=true compares "
                    "the KiCad export written by kicad_dblib (and its database rows). Returns counts and per-field diffs.",
     "inputSchema": {"type": "object", "required": ["bom"],
                     "properties": {"bom": {"type": "string"}, "kicad": {"type": "boolean", "default": False},
                                    "name": {"type": "string", "description": "KiCad library name (default ecadlib)"}}}},
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
    {"name": "altium_menu",
     "description": "Write the 'ecadlib' menu for Altium Designer into the workspace's altium_menu/ folder: a DelphiScript "
                    "project (Add part from MPN, Check this project BOM, Open review queue) that runs ecadlib on the "
                    "Altium PC and reinstalls the library, its settings and runner, and with win_dir a ready-made menu "
                    "file (.DXPPrf). Never run inside Altium by ecadlib's tests.",
     "inputSchema": {"type": "object",
                     "properties": {"win_dir": {"type": "string", "description": "Windows folder of altium_menu/ on the Altium PC"},
                                    "name": {"type": "string", "description": "library name"},
                                    "command": {"type": "string", "description": "how to run ecadlib there (default: find it)"},
                                    "port": {"type": "integer", "default": 8765}}}},
]


def call_tool(ws, name, args, progress=None):
    with contextlib.redirect_stdout(sys.stderr):          # library code prints progress; keep stdout for JSON-RPC
        if name == "workspace_info":
            return ws.info()
        if name == "upload_bom":
            import base64, binascii
            if args.get("content_base64"):
                try:
                    data = base64.b64decode(args["content_base64"], validate=False)
                except (binascii.Error, ValueError):
                    raise ValueError("content_base64 is not valid base64")
            else:
                data = str(args.get("content") or "").encode("utf-8")
            return {"bom": str(ws.save_upload(args["filename"], data))}
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
            ref = [] if args.get("refresh_all") is True or ref is True else (ref or None)   # [] = redo every part
            r = ws.librarian_run(args["bom"], args.get("sources"), (), ref, progress, ai=False)   # never a paid call
            r.pop("log", None)
            return r
        if name == "ai_quote":
            return ws.ai_quote(args["bom"], args.get("parts") or None) or {
                "parts": 0, "text": "no part of this plan needs a datasheet read"}
        if name == "ai_read":
            r = ws.ai_read(args["bom"], args["approved_usd"], args.get("over_limit_ok") is True,
                           args.get("price_unknown_ok") is True, args.get("parts") or None, progress)
            r.pop("log", None)
            return r
        if name == "house_style_guide":
            return ws.style_guide(args["approved_usd"], args.get("over_limit_ok") is True,
                                  args.get("price_unknown_ok") is True, args.get("model") or None, progress)
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
        if name == "find_duplicates":
            r = ws.find_duplicates(args["bom"], args.get("merge") is True)
            if "summary" in r:
                r["summary"].pop("log", None)
            return r
        if name == "datasheet_estimate":
            return ws.datasheet_estimate(args["bom"])
        if name == "datasheet_batch":
            r = ws.datasheet_batch(args["bom"], args.get("sync") is True, args.get("wait") is True, args.get("parts") or None)
            r["log"] = r["log"][-20:]
            return r
        if name == "cross_checks":
            return ws.cross_checks(args["bom"])
        if name == "library_parts":
            return ws.library_parts(args["bom"], pictures=False)
        if name == "build_altium_models":
            return ws.build_altium_models(args["bom"])
        if name == "altium365_plan":
            r = ws.altium365_plan(args["bom"])
            r["items"] = [{k: i[k] for k in ("name", "folder", "status", "symbol", "footprint")} for i in r["items"]]
            return r
        if name == "altium365_push":
            return ws.altium365_push(args["bom"], args.get("workspace"), dry_run=args.get("dry_run", True) is not False)
        if name == "export_altium_dblib":
            return ws.export_dblib(args["bom"])
        if name == "upkeep_run":
            return ws.upkeep_run(args["bom"], args.get("checks") or None, args.get("apply") is True, args.get("workspace"))
        if name == "file_formats":
            return ws.file_formats(args.get("migrate") is True)
        if name == "rebuild_check":
            return ws.rebuild_check(args["bom"])
        if name == "project_bom":
            return ws.project_bom(args["project"], args.get("variant") or None)
        if name == "where_used":
            return ws.where_used(args["projects"], args.get("part") or None, args.get("plan") or None)
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
        if name == "learn_house_style":
            r = ws.learn_house_style(args.get("paths") or None, args.get("direction") or None, args.get("ai") is True,
                                     args.get("model") or None, progress, ask_first=True)   # ai: a quote, nothing sent
            r.pop("summary", None)
            return r
        if name == "house_style":
            if args.get("direction") or args.get("clear"):
                return ws.style_direction(args.get("direction") or None, args.get("clear") is True)
            return ws.house_style()
        if name == "check_house_style":
            return ws.check_house_style(args["bom"], args.get("part") or None)
        if name == "apply_house_style":
            return ws.apply_house_style(args["bom"], args.get("ai") is True, args.get("model") or None,
                                        args.get("dry_run") is not False, args.get("part") or None)
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
        if name == "altium_menu":
            return ws.altium_menu(args.get("win_dir") or None, args.get("name") or "ecadlib", args.get("command") or "",
                                  args.get("port") or 8765)
        if name == "parity":
            return ws.parity(args["bom"], args.get("kicad") is True, args.get("name") or "ecadlib")
        if name == "tool_packages":
            return ws.tool_packages(args["bom"], args.get("name") or "ecadlib", args.get("kicad") or 8,
                                    args.get("include_held") is True)
    raise KeyError(f"unknown tool {name}")


# ------------------------------------------------------------------ what the client is told about the tools
INSTRUCTIONS = """ecadlib builds ECAD component libraries (Altium, Altium 365, KiCad) from a BOM.
Usual flow: workspace_status (what needs attention) -> triage_bom (what the BOM lacks) -> librarian_run (fill symbols and
footprints from free sources) -> held_parts / library_parts (what still needs a person) -> part_candidates + choose_part
(fix a held part with the user) -> approve_part (the user's own sign-off) -> altium365_plan -> altium365_push.
Rules:
- A slow tool may answer {"job": ..., "state": "running"}: call job_status with that id until it is done.
- Money: librarian_run never pays for a model. ai_read, datasheet_batch, house_style_guide, visual_check and ai true
  cost money: show the quote (ai_quote, datasheet_estimate, learn_house_style ai) and wait for the user's yes.
- Never approve, reject or allow parts on your own: pass the user's own name and decision.
- altium365_push defaults to a dry run; a real run only when the user asked for it.
- BOM and file paths are absolute, or relative to the workspace folder (workspace_info shows it).
- Show held parts' `text` and `fix` sentences to the user as written."""

# readOnlyHint: changes nothing; destructiveHint: may overwrite or change something outside the workspace (only
# meaningful when not read-only); openWorldHint: talks to the internet (part sources, Altium 365, a model).
READ_ONLY = {"workspace_info", "triage_history", "preview_footprint", "held_parts", "part_candidates", "approval_status",
             "datasheet_estimate", "cross_checks", "library_parts",
             "altium365_plan", "rebuild_check", "project_bom",
             "where_used", "propose_rules", "check_house_style", "list_example_specs",
             "workspace_status", "doctor", "list_plugins", "parity", "ai_quote"}
DESTRUCTIVE = {"approve_footprint", "preview_symbol", "find_duplicates", "upkeep_run", "altium365_push",
               "save_rules", "apply_house_style", "house_style",
               "file_formats"}
OPEN_WORLD = {"triage_bom", "part_candidates", "librarian_run", "datasheet_batch", "datasheet_estimate", "ai_quote",
              "ai_read", "house_style_guide", "learn_house_style", "apply_house_style", "visual_check", "altium365_push",
              "upkeep_run"}
# --tools core: the BOM-to-library flow in 21 tools, for small models that lose their way among 50
CORE = ["workspace_status", "workspace_info", "doctor", "upload_bom", "triage_bom", "librarian_run", "job_status", "held_parts",
        "library_parts", "part_candidates", "choose_part", "release_check", "approval_status", "approve_part",
        "ai_quote", "ai_read", "list_example_specs", "preview_footprint", "approve_footprint", "altium365_plan",
        "altium365_push"]
PROGRESS = {"librarian_run", "ai_read", "house_style_guide", "learn_house_style"}   # take progress= (and can be stopped)
WORDS = {"bom": "BOM", "ai": "AI", "altium365": "Altium 365", "a365": "Altium 365", "kicad": "KiCad", "dblib": "DbLib"}


def _annotate():
    for t in TOOLS:
        n = t["name"]
        t["title"] = " ".join(WORDS.get(w, w) for w in n.split("_"))
        t["title"] = t["title"][0].upper() + t["title"][1:]
        ro = n in READ_ONLY
        t["annotations"] = {"title": t["title"], "readOnlyHint": ro, "destructiveHint": (not ro) and n in DESTRUCTIVE,
                            "openWorldHint": n in OPEN_WORLD}


_annotate()


def tool_names(spec):
    """--tools / ECADLIB_MCP_TOOLS: 'all', 'core', 'read-only' and tool names, comma separated -> the set to offer."""
    names, known = set(), {t["name"] for t in TOOLS}
    for w in [x.strip() for x in str(spec or "all").split(",") if x.strip()]:
        if w == "all":
            names |= known
        elif w == "core":
            names |= set(CORE)
        elif w in ("read-only", "readonly", "read_only"):
            names |= READ_ONLY | {"job_status"}
        elif w in known:
            names.add(w)
        else:
            raise ValueError(f"--tools: unknown tool or set {w!r} (sets: all, core, read-only)")
    return names


class Options:
    """How this server behaves (ecadlib mcp flags). Tests and old callers get the defaults."""
    def __init__(self, tools=None, wait=45.0, max_chars=80000, confine=False):
        self.tools = tools                      # None = every tool
        self.wait = wait                        # seconds a call waits before it answers with a job id
        self.max_chars = max_chars              # answers longer than this are cut (Claude Code stops at ~25k tokens)
        self.confine = confine                  # file arguments must be inside the workspace or a --lib folder


def _opts(ws):
    return getattr(ws, "mcp_options", None) or Options()


_JOBS_LOCK = threading.Lock()


def _jobs(ws):
    with _JOBS_LOCK:                          # HTTP: the first calls arrive on several threads at once
        if getattr(ws, "_mcp_jobs", None) is None:
            from .ui.server import Jobs
            ws._mcp_jobs = Jobs(ws.root)
    return ws._mcp_jobs


def _offered(ws):
    allowed = _opts(ws).tools
    return [t for t in TOOLS if allowed is None or t["name"] in allowed]


# ------------------------------------------------------------------ arguments
TRUE, FALSE = {"true", "yes", "1", "on"}, {"false", "no", "0", "off", ""}


def _normalize(tool, args):
    """Arguments as the schema wants them, from what models really send: null for an optional argument (OpenAI-style
    strict schemas), "true" / "5" as strings, an object or list as a JSON string, a lone string for a list (used
    letter by letter otherwise), a number where a string goes (a part named 1). Returns a new dict."""
    props = tool["inputSchema"].get("properties", {})
    out = {k: v for k, v in args.items() if v is not None}
    if tool["name"] == "librarian_run" and "refresh" in out and not isinstance(out["refresh"], list):
        r = out.pop("refresh")                 # the old schema took refresh: true for "redo every part"
        if r is True or str(r).strip().lower() in TRUE | {"all", "*"}:
            out["refresh_all"] = True
        elif isinstance(r, str) and r.strip().lower() not in FALSE:
            out["refresh"] = [x.strip() for x in r.split(",") if x.strip()]
    for k, v in list(out.items()):
        sch = props.get(k) or {}
        typ = sch.get("type")
        if typ == "boolean":
            if isinstance(v, str) and v.strip().lower() in TRUE | FALSE:
                out[k] = v.strip().lower() in TRUE
            elif isinstance(v, int) and not isinstance(v, bool) and v in (0, 1):
                out[k] = bool(v)
        elif typ in ("integer", "number") and isinstance(v, str):
            try:
                f = float(v.strip())
                out[k] = int(f) if typ == "integer" and f == int(f) else f
            except ValueError:
                pass
        elif typ == "integer" and isinstance(v, float) and v == int(v):
            out[k] = int(v)
        elif typ == "string" and isinstance(v, (int, float)) and not isinstance(v, bool):
            out[k] = str(v)
        elif typ == "object" and isinstance(v, str):
            with contextlib.suppress(ValueError):
                if isinstance(json.loads(v), dict):
                    out[k] = json.loads(v)
        elif typ == "array" and not isinstance(v, list):
            if isinstance(v, str) and v.strip().startswith("["):
                with contextlib.suppress(ValueError):
                    if isinstance(json.loads(v), list):
                        out[k] = json.loads(v)
                        continue
            if isinstance(v, str):
                enum = (sch.get("items") or {}).get("enum")
                out[k] = [x.strip() for x in v.split(",") if x.strip()] if enum else [v]
            elif not isinstance(v, dict):
                out[k] = [v]
    return out


def _usage(tool):
    props, req = tool["inputSchema"].get("properties", {}), set(tool["inputSchema"].get("required", []))
    return ", ".join(f"{k} ({v.get('type', 'any')}{', required' if k in req else ''})" for k, v in props.items()) or "no arguments"


def _arg_errors(tool, args):
    """The arguments checked against the tool's inputSchema: '' when they fit, else one line per problem. They are
    normalized in place first (_normalize)."""
    import jsonschema
    fixed = _normalize(tool, args)
    args.clear()
    args.update(fixed)
    v = jsonschema.Draft7Validator(tool["inputSchema"])
    errs = sorted(v.iter_errors(args), key=lambda e: list(e.path))
    return "; ".join(f"{'.'.join(map(str, e.path)) or '(arguments)'}: {e.message}" for e in errs[:5])


PATH_ARGS = {"bom", "project", "projects", "paths", "a365", "target", "datasheet", "symbol", "models", "plan"}   # win_dir: a folder on the Altium PC, never opened here


def _check_paths(ws, args):
    """--confine (the default over HTTP): file arguments must be inside the workspace or a --lib folder, so a remote
    client cannot have the server read or write anywhere else on this computer."""
    from pathlib import Path
    roots = [ws.root] + [Path(x) for x in ws.libs]
    vals = []
    for k, v in args.items():
        if k in PATH_ARGS:
            vals += [(k, x) for x in (v if isinstance(v, list) else [v]) if isinstance(x, str)]
    if "toml" in args and isinstance(args["toml"], str):   # save_rules: rules settings that name a file ([style]
        vals += [(f"toml {k}", x) for k, x in _rule_paths(args["toml"])]   # profile, ...)
    for k, x in vals:
        if x.startswith(("http://", "https://")) or not x.strip():
            continue
        p = Path(x).expanduser()
        if not p.is_absolute() and re.match(r"[A-Za-z]:[\\/]|\\\\", x):   # a Windows path on another OS
            raise ValueError(f"{k}: {x} is outside the workspace and the --lib folders; this server only opens files "
                             "there (start it with --any-path to allow more)")
        p = (p if p.is_absolute() else ws.root / p).resolve()
        if not any(p == r or r in p.parents for r in (Path(r).resolve() for r in roots)):
            raise ValueError(f"{k}: {x} is outside the workspace and the --lib folders; this server only opens files "
                             "there (start it with --any-path to allow more)")


def _rule_paths(text):
    """(key, value) of every string in a rules TOML that reaches outside the folder it is read from: an absolute
    path (also C:\\..., ~/...) or one with a '..' step. Relative ones stay in the workspace."""
    import re
    from .rules import toml_module
    lib = toml_module()
    if lib is None:
        return []
    try:
        data = lib.loads(text)
    except Exception:
        return []                                 # save_rules reports the syntax error itself

    def walk(v, at):
        if isinstance(v, dict):
            for k, x in v.items():
                yield from walk(x, f"{at}.{k}" if at else str(k))
        elif isinstance(v, list):
            for x in v:
                yield from walk(x, at)
        elif isinstance(v, str) and (v.startswith(("/", "\\", "~")) or re.match(r"[A-Za-z]:[\\/]", v)
                                     or ".." in re.split(r"[\\/]", v)):
            yield at, v
    return list(walk(data, ""))


ENDPOINT_KEYS = {"provider", "base_url", "api_key_env", "endpoints"}


def _endpoints(text):
    """{dotted key: value} of every setting in a rules TOML that says where a model request (and the API key) goes."""
    from .rules import toml_module
    lib = toml_module()
    try:
        data = lib.loads(text) if lib and text else {}
    except Exception:
        return {}
    out = {}

    def walk(v, at):
        if isinstance(v, dict):
            for k, x in v.items():
                key = f"{at}.{k}" if at else str(k)
                if k in ENDPOINT_KEYS:
                    out[key] = json.dumps(x, sort_keys=True)
                else:
                    walk(x, key)
    walk(data, "")
    return out


def _check_endpoints(ws, text):
    """save_rules from an agent may not change where model requests go: the stored API key would follow the request
    to any URL. The person edits [ai] / [datasheet] base_url, provider and api_key_env themselves."""
    f = ws.rules_file()
    old = _endpoints(f.read_text(encoding="utf-8")) if f.is_file() else {}
    new = _endpoints(text if isinstance(text, str) else "")
    changed = sorted(k for k in set(old) | set(new) if old.get(k) != new.get(k))
    if changed:
        raise ValueError(f"save_rules cannot change {', '.join(changed)} (where model requests and your API key go): "
                         f"ask the user to edit {f.name} themselves")


# ------------------------------------------------------------------ answers
def _text(ws, out):
    s = json.dumps(out, ensure_ascii=False, default=str, separators=(",", ":"))
    cap = _opts(ws).max_chars
    if cap and len(s) > cap:
        s = (s[:cap] + f"\n... [cut: the answer was {len(s)} characters, over this server's limit of {cap} "
             "(ECADLIB_MCP_MAX_CHARS). Ask for less: summary_only, one part, a status filter or a smaller page.]")
    return s


def _result(ws, out, error=False):
    return {"content": [{"type": "text", "text": out if isinstance(out, str) else _text(ws, out)}], "isError": error}


def _error_text(e):
    from .visual_check import VisualCheckError          # e.g. Pillow not installed: the fix, not a bug report
    if isinstance(e, (ValueError, KeyError, FileExistsError, FileNotFoundError, PermissionError, VisualCheckError)):
        return e.args[0] if isinstance(e, KeyError) and e.args else str(e)   # a wrong id / path is the caller's
    traceback.print_exc(file=sys.stderr)                # a bug: keep the traceback on stderr
    from . import messages as M                         # one line what went wrong, one line what to do (shared voice)
    return "\n".join(M.friendly_error(e))


def _job_view(ws, j):
    wait = int(_opts(ws).wait)
    return {"job": j["id"], "state": j["state"], "tool": j.get("tool"), "done": j["done"], "total": j["total"],
            "step": j["label"], "elapsed_s": j["elapsed"],
            "next": f"Still running. Call job_status with id {j['id']!r} to wait for the result (up to {wait} s per call)."}


def _await(ws, jid, wait, notify=None, token=None, forget=False):
    """Wait up to `wait` seconds for a job; -> a tools/call result (the tool's answer once done, else the job).
    forget: drop the job once its answer is given here, so quick calls don't push a slow job's result out of the
    last 20 the job list keeps."""
    import time
    jobs, end, sent, last = _jobs(ws), time.time() + max(0.0, wait), [0], None
    while True:
        j = jobs.get(jid)
        if j is None:
            return _result(ws, f"unknown job {jid!r}: jobs live in this server's memory (a restarted server forgets them); "
                               "job_status without id lists the current ones", True)
        if j["state"] != "running":
            break
        now = (j["done"], j["total"], j["label"])
        if notify and token is not None and now != last:  # progress must increase: count the notifications
            sent[0] += 1
            notify({"jsonrpc": "2.0", "method": "notifications/progress",
                    "params": {"progressToken": token, "progress": sent[0],
                               "message": f"{j['label']} ({j['done']}/{j['total']})" if j["total"] else str(j["label"])}})
            last = now
        if time.time() >= end:
            return _result(ws, _job_view(ws, j))
        time.sleep(0.05 if time.time() - j["started"] < 2 else 0.25)
    out = j["result"]
    if forget and not j.get("shared"):
        with jobs.lock:
            jobs.jobs.pop(jid, None)
    if j["state"] == "cancelled":
        return _result(ws, "stopped: the job was cancelled before it finished (finished parts are kept)", True)
    if j["state"] == "error" or (isinstance(out, tuple) and out[0] == "error"):
        return _result(ws, out[1] if isinstance(out, tuple) else str(j["error"]), True)
    return _result(ws, out[1])


def _job_status(ws, args, notify=None, token=None):
    jobs = _jobs(ws)
    if not args.get("id"):
        return _result(ws, {"jobs": [{"job": j["id"], "tool": j.get("tool"), "state": j["state"],
                                      "elapsed_s": round(j.get("seconds") or 0, 1)} for j in list(jobs.jobs.values())]})
    jid = str(args["id"])
    if args.get("cancel") and jobs.cancel(jid) is None:
        return _result(ws, f"unknown job {jid!r}", True)
    wait = args.get("wait_seconds")
    return _await(ws, jid, _opts(ws).wait if wait is None else min(float(wait), 600), notify, token)


def _bom_key(ws, bom):
    """x.csv, ./x.csv and its full path are one BOM."""
    try:
        return str(ws._bom_path(bom).resolve())
    except (ValueError, OSError):                 # outside the workspace: the tool itself says so
        return str(bom)


def _call(ws, tool, args, notify=None, token=None):
    """Run a tool as a job and wait for it up to the server's wait: a quick tool answers at once, a slow one answers
    with a job id instead of running into the client's timeout (Codex stops waiting after 60 s by default)."""
    name = tool["name"]
    if name == "job_status":
        return _job_status(ws, args, notify, token)
    if _opts(ws).confine:
        _check_paths(ws, args)
    if name == "save_rules":
        _check_endpoints(ws, args.get("toml"))
    jobs = _jobs(ws)
    key = None if name in READ_ONLY or not args.get("bom") else f"bom:{_bom_key(ws, args['bom'])}"   # one writer per BOM
    busy = key and jobs.running(key)
    if busy:
        busy["shared"] = True                     # another caller was given its id: keep it for job_status
        return _result(ws, f"{busy.get('tool')} is still running on {args['bom']} (job {busy['id']}): call job_status "
                           f"with id {busy['id']!r} and try again once it is done", True)

    def run(progress=None):
        try:
            return ("ok", call_tool(ws, name, args, progress if name in PROGRESS else None))
        except Exception as e:
            from .ui.server import Cancelled
            if isinstance(e, Cancelled):
                raise
            return ("error", _error_text(e))
    jid = jobs.start(run, label=name, key=key, tool=name)["job"]
    return _await(ws, jid, _opts(ws).wait, notify, token, forget=True)


def handle(ws, msg, notify=None):
    """Return a JSON-RPC response dict, or None for notifications. notify(message) sends a notification (progress)
    while a tool runs."""
    if not isinstance(msg, dict) or not isinstance(msg.get("params", {}), (dict, type(None))):
        mid = msg.get("id") if isinstance(msg, dict) else None
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32600, "message": "invalid request"}}
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if mid is None:
        return None                                       # notifications (initialized, cancelled) need no answer
    if method == "initialize":
        asked = params.get("protocolVersion")
        result = {"protocolVersion": asked if asked in SUPPORTED else PROTOCOL,
                  "capabilities": {"tools": {"listChanged": False}},
                  "serverInfo": {"name": "ecadlib", "title": "ecadlib component librarian", "version": __version__},
                  "instructions": INSTRUCTIONS}
    elif method == "ping" or method == "logging/setLevel":
        result = {}
    elif method == "tools/list":
        result = {"tools": _offered(ws)}
    elif method in ("resources/list", "resources/templates/list", "prompts/list"):
        result = {"resources/list": {"resources": []}, "resources/templates/list": {"resourceTemplates": []},
                  "prompts/list": {"prompts": []}}[method]           # some clients ask whatever the capabilities say
    elif method == "tools/call":
        name, args = params.get("name"), params.get("arguments")
        if isinstance(args, str):                         # a client that sent the arguments as a JSON string
            with contextlib.suppress(ValueError):
                args = json.loads(args) if args.strip() else {}
        offered = {t["name"]: t for t in _offered(ws)}
        if not isinstance(name, str) or name not in offered:
            import difflib
            near = difflib.get_close_matches(str(name), list(offered), 3) if isinstance(name, str) else []
            hint = f" (did you mean {', '.join(near)}?)" if near else ""
            off = " (not enabled on this server: see its --tools)" if isinstance(name, str) and name in {
                t["name"] for t in TOOLS} else ""
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"unknown tool: {name}{off}{hint}"}}
        if not isinstance(args or {}, dict):
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "arguments must be an object"}}
        tool = offered[name]
        args = _normalize(tool, args or {})
        missing = [k for k in tool["inputSchema"].get("required", []) if k not in args]
        bad = (f"missing argument(s): {', '.join(missing)}" if missing else "") or (
            f"invalid argument(s): {_arg_errors(tool, args)}" if _arg_errors(tool, args) else "")
        if bad:                                           # a tool error, not a protocol error: the model reads it
            result = _result(ws, f"{bad}. {name} takes: {_usage(tool)}", True)                   # and calls again
        else:
            meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
            try:
                result = _call(ws, tool, args, notify, meta.get("progressToken"))
            except Exception as e:
                result = _result(ws, _error_text(e), True)
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _encode(resp):                                        # a lone surrogate (an undecodable file name) -> its \\u escape
    return json.dumps(resp, ensure_ascii=False).encode("utf-8", "backslashreplace")


def main(argv=None):
    import os
    ap = argparse.ArgumentParser(prog="ecadlib mcp", description="ecadlib MCP server: stdio (default) or HTTP (--http)")
    ap.add_argument("--lib", action="append", default=[])
    ap.add_argument("--workspace", default="ecadlib_workspace")
    ap.add_argument("--providers")
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--tools", default=os.environ.get("ECADLIB_MCP_TOOLS", "all"),
                    help="tools to offer: all (default), core (21, for small models), read-only, or tool names; comma separated")
    ap.add_argument("--wait", type=float, default=float(os.environ.get("ECADLIB_MCP_WAIT", 45)),
                    help="seconds a tool call may take before it answers with a job id for job_status (default 45)")
    ap.add_argument("--max-chars", type=int, default=int(os.environ.get("ECADLIB_MCP_MAX_CHARS", 80000)),
                    help="cut answers longer than this (default 80000, about 20k tokens; 0 = never)")
    ap.add_argument("--http", action="store_true", help="serve MCP over Streamable HTTP instead of stdio")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--path", default="/mcp")
    ap.add_argument("--token", default=os.environ.get("ECADLIB_MCP_TOKEN"),
                    help="HTTP bearer token (default: $ECADLIB_MCP_TOKEN, else a new one is printed)")
    ap.add_argument("--no-auth", action="store_true", help="HTTP without a token (only on 127.0.0.1 / localhost)")
    ap.add_argument("--allow-origin", action="append", default=[], help="HTTP: a browser origin allowed to call")
    ap.add_argument("--any-path", action="store_true", help="HTTP: allow file arguments outside the workspace and --lib")
    ap.add_argument("--confine", action="store_true", help="stdio: only allow file arguments inside the workspace and --lib")
    a = ap.parse_args(argv)
    try:
        tools = tool_names(a.tools)
    except ValueError as e:
        ap.error(str(e))
    from . import userconfig
    userconfig.apply_env()                         # kicad-cli on PATH; stored keys are read when needed, never exported
    ws = Workspace(a.workspace, a.lib, a.providers.split(",") if a.providers else None, a.offline)
    ws.mcp_options = Options(None if tools == {t["name"] for t in TOOLS} else tools, a.wait, a.max_chars,
                             confine=(a.http and not a.any_path) or a.confine)
    if a.http:
        from . import mcp_http
        return mcp_http.run(ws, a)
    out = _protect_stdout()
    serve(ws, sys.stdin.buffer, out)


def _protect_stdout():
    """The JSON-RPC stream gets its own copy of stdout and file descriptor 1 becomes stderr, so nothing else can write
    into the protocol: not a print() in library code, not a C extension, not a child process such as kicad-cli or
    pdftoppm that inherits stdout."""
    import os
    try:
        sys.stdout.flush()
        fd = os.dup(1)
        os.dup2(2, 1)
        out = os.fdopen(fd, "wb", buffering=0)
    except (OSError, ValueError, AttributeError):      # no real file descriptors (embedded, tests): best effort
        out = sys.stdout.buffer
    sys.stdout = sys.stderr
    return out


def serve(ws, inp, out):
    """JSON-RPC over newline-delimited UTF-8 (bytes in, bytes out, whatever the OS locale is).
    No single bad line can stop the server: it gets an error response and the next line is read."""
    def send(resp):
        out.write(_encode(resp) + b"\n")
        out.flush()
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
                resp = handle(ws, msg, send)
            except Exception as e:                          # a bug or odd input in one request: report and go on
                traceback.print_exc(file=sys.stderr)
                mid = msg.get("id") if isinstance(msg, dict) and isinstance(msg.get("id"), (str, int)) else None
                resp = {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": f"internal error: {type(e).__name__}"}}
        if resp is not None:
            send(resp)


if __name__ == "__main__":
    main()
