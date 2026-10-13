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
        if params.get("name") not in {t["name"] for t in TOOLS}:
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
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            resp = handle(ws, msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
