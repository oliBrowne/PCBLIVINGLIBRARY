"""Find a datasheet link by web search, when no link of the part gave a PDF ([datasheet] web_search = true).

The last way to a datasheet, after the BOM's links, the part sources and the [datasheet] folder: one short Claude
request with the server-side web search tool asks for the maker's own PDF of this part. The links it gives (and the
PDF links among the search results) are only candidates: datasheet.PdfStore downloads each, with the usual
download ways, and keeps the first PDF that names the part. Off by default; every search is billed (tokens plus
SEARCH_USD a search), held under [datasheet] max_cost_usd and written to cost.jsonl like any read. A link found is
kept in <out>/datasheet/web_search.json, so the next run for the part does not search again.

Benchmark 2026-10-21: the 20 parts of kicad-v1 whose links had died (Vishay document numbers renumbered, Wolfspeed
and Infineon asset paths moved, Broadcom /docs/ pages, ST family files renamed per part, Altera handbooks moved to
Intel, product pages instead of PDFs) all had a current PDF a web search finds."""
import json
import re
from pathlib import Path

TOOL = "web_search_20260209"        # dynamic filtering: Opus 5.5 / 5 / 4.8 / 4.7 / 4.6, Sonnet 5.5 / 5 / 4.6
TOOL_BASIC = "web_search_20250305"  # every other model
SEARCH_USD = 0.01                   # $10 per 1,000 searches, on top of the tokens
MAX_USES = 3                        # searches per part
MAX_TURNS = 3                       # pause_turn continuations
# input / output tokens one part's search may use, for the cost cap: the search results the model reads are most of
# it (benchmark 2026-10-21, Opus 5.5, 3 searches: $0.03-$0.36 a part, ENS210 about 80K input tokens)
EST_TOKENS = (90000, 4000)
URL_RE = re.compile(r"https?://[^\s\"'<>\])}]+")
NEW_TOOL_MODELS = ("claude-opus-5-5", "claude-opus-5", "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6",
                   "claude-sonnet-5-5", "claude-sonnet-5", "claude-sonnet-4-6", "claude-fable")

PROMPT = """Find the datasheet PDF for the electronic part {mpn}{maker}{package}.

Search the web for the manufacturer's current datasheet. I need direct links to the PDF file itself (not a product
page, not a search page). Prefer the manufacturer's own site; a major distributor's copy of the manufacturer's PDF
(Mouser, DigiKey, Farnell, LCSC) is the next best. Never give a datasheet aggregator or mirror site. If the part is
obsolete, the last manufacturer datasheet is fine.

The PDF must cover this part's package drawing. Answer with one line of JSON and nothing after it:
{{"urls": ["<best direct PDF link>", "<second choice>", ...]}}"""


def tool_type(model):
    return TOOL if str(model).startswith(NEW_TOOL_MODELS) else TOOL_BASIC


def request_body(mpn, model, maker=None, package=None, max_uses=MAX_USES):
    text = PROMPT.format(mpn=mpn, maker=f" made by {maker}" if maker else "",
                         package=f" (package {package})" if package else "")
    return {"model": model, "max_tokens": 4096, "output_config": {"effort": "low"},
            "tools": [{"type": tool_type(model), "name": "web_search", "max_uses": max_uses}],
            "messages": [{"role": "user", "content": text}]}


def links_in(content):
    """The answer's links, in its order, then the PDF-looking links of the search results. -> [url]"""
    said, found = [], []
    for b in content or []:
        if b.get("type") == "text":
            m = re.findall(r"\{[^{}]*\"urls\"[^{}]*\}", b.get("text") or "")
            for blob in m[-1:]:
                try:
                    said += [u for u in json.loads(blob).get("urls") or [] if isinstance(u, str)]
                except ValueError:
                    said += URL_RE.findall(blob)
        elif b.get("type") == "web_search_tool_result" and isinstance(b.get("content"), list):
            found += [r.get("url") for r in b["content"] if isinstance(r, dict) and r.get("url")
                      and re.search(r"\.pdf($|[?#])|datasheet", r["url"], re.I)]
    return [u for u in dict.fromkeys(u.strip().rstrip(".,;") for u in said + found) if u.startswith("http")]


def add_usage(total, u):
    for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens"):
        total[k] = total.get(k, 0) + int((u or {}).get(k) or 0)
    total["web_search_requests"] = total.get("web_search_requests", 0) + \
        int(((u or {}).get("server_tool_use") or {}).get("web_search_requests") or 0)
    return total


def search(client, mpn, model, maker=None, package=None, max_uses=MAX_USES, headers=None, extra=None):
    """One search conversation (continued on pause_turn). extra: more request fields (fallbacks), with their beta in
    headers. -> (links, usage summed over the turns, last message)"""
    body = dict(request_body(mpn, model, maker, package, max_uses), **(extra or {}))
    usage, msg = {}, {}
    for _ in range(MAX_TURNS):
        msg = client.messages(body, headers)
        add_usage(usage, msg.get("usage"))
        if msg.get("stop_reason") != "pause_turn":
            break
        body = dict(body, messages=body["messages"] + [{"role": "assistant", "content": msg.get("content") or []}])
    return links_in(msg.get("content")), usage, msg


def usd(model, usage):
    """Tokens at the model's price plus the searches. None when the model has no known price."""
    from . import ds_route as DR
    tok = DR.usage_usd(model, usage)
    return None if tok is None else round(tok + SEARCH_USD * usage.get("web_search_requests", 0), 6)


def estimate(model, max_uses=MAX_USES):
    from . import datasheet as D
    p = D.price_of(model)
    return None if not p else (EST_TOKENS[0] * p[0] + EST_TOKENS[1] * p[1]) / 1e6 + SEARCH_USD * max_uses


class Found:
    """<out>/datasheet/web_search.json: part -> the link a search found that gave its PDF."""

    def __init__(self, folder):
        self.file = Path(folder) / "web_search.json"

    def get(self, part):
        try:
            return (json.loads(self.file.read_text(encoding="utf-8")) or {}).get(part)
        except (OSError, ValueError):
            return None

    def put(self, part, url):
        try:
            d = json.loads(self.file.read_text(encoding="utf-8")) if self.file.exists() else {}
        except (OSError, ValueError):
            d = {}
        d[part] = url
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text(json.dumps(d, indent=1, sort_keys=True), encoding="utf-8")
