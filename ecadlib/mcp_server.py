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
                                    "sources": {"type": "array", "items": {"type": "string", "enum": ["kicad", "easyeda", "datasheet"]},
                                                "description": "sources to try, in order (default all three)"},
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
    {"name": "find_duplicates",
     "description": "Find duplicate parts (same MPN once packaging suffixes are stripped), footprints (same pad "
                    "geometry) and symbols (same pinout) across the librarian's plan and the workspace's altium_models/ "
                    "and footprints/. Each group has a recommended keeper. merge=true rewrites the plan to the keepers "
                    "(duplicates are then skipped by push; library files are never changed): only when the user asks.",
     "inputSchema": {"type": "object", "required": ["bom"],
                     "properties": {"bom": {"type": "string"}, "merge": {"type": "boolean", "default": False}}}},
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
    {"name": "list_example_specs",
     "description": "Return the example package specs (useful as templates for preview_footprint).",
     "inputSchema": {"type": "object", "properties": {}}},
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
        if name == "preview_footprint":
            r = ws.preview_footprint(args["spec"], args.get("density"))
            return {"name": r["name"], "report": r["report"]}
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
        if name == "find_duplicates":
            r = ws.find_duplicates(args["bom"], args.get("merge") is True)
            if "summary" in r:
                r["summary"].pop("log", None)
            return r
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
        if name == "list_example_specs":
            return ws.example_specs()
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
            if not isinstance(e, (ValueError, FileExistsError)):
                traceback.print_exc(file=sys.stderr)
            msg = str(e) if isinstance(e, (ValueError, FileExistsError)) else f"{type(e).__name__}: {e}"
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
