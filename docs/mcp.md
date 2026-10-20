# Using ecadlib from an AI agent (MCP)

`ecadlib mcp` lets an AI agent do the librarian's work in conversation: "check this BOM, fill what is missing and
tell me what still needs me". It works with Claude (Claude Code and Claude Desktop), OpenAI Codex, xAI Grok and any
other client that speaks the Model Context Protocol.

There are two ways to connect:

- **stdio** (the default): the agent starts `ecadlib mcp` on your computer. Use it for Claude Code, Claude Desktop,
  Codex, Cursor and other desktop agents.
- **HTTP** (`ecadlib mcp --http`): ecadlib listens on a port and the agent connects to a URL. Use it when the agent
  cannot start a program on your computer, such as Grok through the xAI API.

Give an absolute `--workspace` either way, because desktop clients start servers from whatever folder they like.
If an agent says it cannot find `ecadlib`, use the full path to the program (on Windows, `ecadlib.exe` in the folder
the installer chose).

## Claude Code

```
claude mcp add ecadlib -- ecadlib mcp --lib /path/to/kicad/libs --workspace /path/to/workspace
```

Check it with `claude mcp list`, which should show `ecadlib ... Connected`.

## Claude Desktop

Add this to `claude_desktop_config.json` (Settings, Developer, Edit Config), then restart Claude Desktop:

```json
{"mcpServers": {"ecadlib": {"command": "ecadlib",
  "args": ["mcp", "--lib", "C:/path/to/libs", "--workspace", "C:/path/to/workspace"]}}}
```

## OpenAI Codex

```
codex mcp add ecadlib -- ecadlib mcp --lib /path/to/libs --workspace /path/to/workspace
```

Or add it to `~/.codex/config.toml`:

```toml
[mcp_servers.ecadlib]
command = "ecadlib"
args = ["mcp", "--lib", "/path/to/libs", "--workspace", "/path/to/workspace"]
startup_timeout_sec = 30
tool_timeout_sec = 120
```

Codex gives up on a tool after 60 seconds by default. ecadlib answers within 45 seconds anyway (see "Slow tools"
below), so the default works, and `tool_timeout_sec = 120` leaves some headroom.

## xAI Grok

The xAI API connects to remote MCP servers over HTTP only, so run ecadlib in HTTP mode and give it a public URL with
a tunnel:

```
export ECADLIB_MCP_TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(24))")
ecadlib mcp --http --workspace /path/to/workspace --tools core
cloudflared tunnel --url http://127.0.0.1:8766        # prints https://<random>.trycloudflare.com
```

Then pass the server to Grok in the request's `tools` (OpenAI SDK pointed at `https://api.x.ai/v1`):

```python
import os
from openai import OpenAI

client = OpenAI(api_key=os.environ["XAI_API_KEY"], base_url="https://api.x.ai/v1")
answer = client.responses.create(
    model="grok-4",                                  # any Grok model your key can use
    input="Upload this BOM to ecadlib, triage it and tell me what is missing: ...",
    tools=[{"type": "mcp", "server_label": "ecadlib",
            "server_url": "https://<random>.trycloudflare.com/mcp",
            "authorization": os.environ["ECADLIB_MCP_TOKEN"]}],
)
print(answer.output_text)
```

A remote client cannot see your files, so it sends the BOM with `upload_bom` (the CSV text, or an `.xlsx` as base64)
and uses the path that comes back.

Grok inside a desktop agent (Cursor, Cline, the Grok CLI and others that let you pick the model) uses stdio instead:
add ecadlib the same way as for Claude Desktop.

## HTTP mode and safety

```
ecadlib mcp --http [--host 127.0.0.1] [--port 8766] [--path /mcp] [--token ...]
```

A tunnel puts the server on the internet even though it listens on 127.0.0.1, so HTTP mode is locked down by default:

- Every request needs `Authorization: Bearer <token>`. The token comes from `--token` or `ECADLIB_MCP_TOKEN`; without
  either, ecadlib makes a new one at each start and prints it. Keep it secret: anyone with the URL and the token can
  use every tool you offer.
- `--no-auth` is only allowed on 127.0.0.1 or localhost, and then only requests addressed to a local name are answered.
- A request from a web page (it carries an `Origin` header) is refused unless the page is on this computer or you
  allow it with `--allow-origin`.
- File arguments must be inside the workspace or a `--lib` folder. `--any-path` lifts that.
- Offer fewer tools to a remote agent: `--tools read-only` changes nothing but its own reports in the workspace
  (parity, project BOM, where-used, the Altium 365 plan file when there is no librarian plan), and `--tools core`
  keeps only the BOM-to-library flow and leaves out the export tools.

claude.ai's web connectors need OAuth sign-in, which ecadlib does not offer. Use Claude Code or Claude Desktop.

## Choosing tools for smaller models

ecadlib has about 50 tools. Frontier models handle that well. A small or local model does better with fewer:

| `--tools` | What the agent gets |
|-----------|---------------------|
| `all` (default) | every tool |
| `core` | the BOM-to-library flow: status, upload, triage, librarian, held parts, candidates, sign-off, AI quote and read, footprint preview, Altium 365 plan and push, job status (21 tools) |
| `read-only` | only tools that change nothing |
| `triage_bom,held_parts,...` | exactly these (you can mix them with the sets) |

`ECADLIB_MCP_TOOLS` sets the same thing for clients where you cannot change the arguments easily.

The server also forgives the slips smaller models make. It accepts `null` for an optional argument, `"true"` or `"5"`
as text, an object sent as a JSON string, and a single name where a list goes. A wrong or missing argument comes back
as an error the model can read, naming the arguments the tool takes, so the model can try again.

## Slow tools

A full librarian run on a big BOM takes minutes, longer than some clients wait. A call that is not done after
`--wait` seconds (default 45) answers with a job instead:

```json
{"job": "3-9f2c41aa0b17", "state": "running", "tool": "librarian_run", "done": 12, "total": 40,
 "next": "Still running. Call job_status with id '3-9f2c41aa0b17' to wait for the result (up to 45 s per call)."}
```

The agent calls `job_status` with that id until it gets the result. `job_status` with `cancel: true` stops the job at
its next part, and finished parts are kept. While a job changes a BOM's library, other changes to that BOM are refused
until it is done. Reading is still allowed. Clients that ask for progress get progress notifications while they wait.
Jobs live in the server's memory, so restarting the server forgets them, but the work they finished is saved.

## Money

`librarian_run` never pays for a model. When some parts can only be finished by reading their datasheet, it returns
the price as `ai` (the model, about how much, at most how much). The agent shows that to you, and only after you say
yes does it call `ai_read` with the amount you approved. The reads are capped to that amount, and refused if the
estimate has gone up since. The house style guide works the same way: `learn_house_style` with `ai` returns a quote,
and `house_style_guide` spends it. `datasheet_batch`, `visual_check` and `apply_house_style` with `ai` are held the
same way, by the server and not only by their descriptions: each needs `approved_usd`, the amount you said yes to, and
is refused without it, when it is less than the quote, or when the quote is over your limit and `over_limit_ok` is not
set. The quotes come from `datasheet_estimate` (its `ai`, for `datasheet_batch`; a `sync` read is priced by
`datasheet_quote`, at full price), `visual_check` with `render_only` (its `ai`) and `apply_house_style` with `ai` but no
`approved_usd` (its `ai`: nothing is sent). A server started with `--offline` makes no model call at all. See
[api-keys.md](api-keys.md) for where your key lives.

## Other limits

- Answers longer than `--max-chars` (default 80,000 characters, about 20,000 tokens) are cut, with a note telling the
  agent how to ask for less with the arguments that tool takes (`library_parts` and `altium365_plan` page with `offset`
  and `limit`; `library_parts` also takes `part` and `status`), or that it cannot. Claude Code stops reading at about
  25,000 tokens.
- Nothing but protocol messages goes to stdout, even when a tool starts kicad-cli or pdftoppm. Logs go to stderr.
- Protocol versions 2024-11-05, 2025-03-26, 2025-06-18 and 2025-11-25. The server offers tools only; it answers
  resource and prompt list requests with empty lists.

## Tests

`tests/test_mcp_clients.py` checks every tool schema against what Codex and OpenAI-style APIs (xAI's included) accept,
the argument fixes, slow-tool jobs, the money rule, stdout, and the HTTP transport. When the official `mcp` Python SDK
is installed, its stdio and HTTP clients drive the server end to end. The schema check runs on every tool, so a new
tool with an untyped argument fails the tests.
