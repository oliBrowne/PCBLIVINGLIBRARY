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
MAX_TOKENS = 4096                   # a turn's max_tokens: the most output it is billed for
# input / output tokens one part's search may use, for the cost cap: the search results the model reads are most of
# it (benchmark 2026-10-21, Opus 5.5, 3 searches: $0.03-$0.36 a part, ENS210 about 80K input tokens)
EST_TOKENS = (90000, MAX_TOKENS)
URL_RE = re.compile(r"https?://[^\s\"'<>\])}]+")
# datasheet aggregators and mirrors (the prompt asks for the maker's or a distributor's copy): a host or domain never taken
AGGREGATORS = ("alldatasheet", "alldatasheetcn", "datasheetspdf", "datasheet4u", "datasheetarchive", "datasheets360",
               "datasheetcatalog", "datasheetq", "datasheet.live", "datasheethub", "datasheetgo", "datasheets.com",
               "elcodis", "icpdf", "kynix", "datasheetz")
# the model a search runs on when [datasheet] web_search_model is "": test 2026-10-22, the 41 kicad-v1 parts whose BOM
# links were dead: Haiku 5.5 found 39 for $1.04 (it always searched); Opus 5.5 found 14 of the 18 its $2 cap let it try,
# for $1.47 (it often answered from memory without a search, and those were its misses)
DEFAULT_MODEL = "claude-haiku-5-5"
# major distributors whose copy of the maker's PDF is the next best thing to the maker's own (rank)
DISTRIBUTORS = ("mouser", "digikey", "arrow", "farnell", "newark", "element14", "lcsc", "rsonline", "tme", "avnet",
                "futureelectronics", "ttiinc", "rutronik")
# words of a maker's name that never name its web site (rank)
MAKER_WORDS = {"inc", "corp", "corporation", "co", "ltd", "llc", "gmbh", "ag", "sa", "bv", "nv", "plc", "group",
               "semiconductor", "semiconductors", "semi", "electronics", "electronic", "technology", "technologies",
               "international", "components", "devices", "the", "and", "of"}
NEW_TOOL_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6",
                   "claude-sonnet-5-5", "claude-sonnet-5", "claude-sonnet-4-6"}

PROMPT = """Find the datasheet PDF for the electronic part {mpn}{maker}{package}.

Search the web for the manufacturer's current datasheet. I need direct links to the PDF file itself (not a product
page, not a search page). Prefer the manufacturer's own site; a major distributor's copy of the manufacturer's PDF
(Mouser, DigiKey, Farnell, LCSC) is the next best. Never give a datasheet aggregator or mirror site. If the part is
obsolete, the last manufacturer datasheet is fine.

The PDF must cover this part's package drawing. Answer with one line of JSON and nothing after it:
{{"urls": ["<best direct PDF link>", "<second choice>", ...]}}"""


def tool_type(model):
    from . import datasheet as D
    return TOOL if D.base_model(model) in NEW_TOOL_MODELS else TOOL_BASIC


def request_body(mpn, model, maker=None, package=None, max_uses=MAX_USES):
    text = PROMPT.format(mpn=mpn, maker=f" made by {maker}" if maker else "",
                         package=f" (package {package})" if package else "")
    from . import datasheet as D
    levels = D.EFFORT_LEVELS.get(D.base_model(model))   # (Haiku 4.5, Sonnet 4.5 reject output_config.effort)
    body = {"model": model, "max_tokens": MAX_TOKENS}
    if levels is None or "low" in levels:
        body["output_config"] = {"effort": "low"}
    return dict(body, **{
            "tools": [{"type": tool_type(model), "name": "web_search", "max_uses": max_uses}],
            "messages": [{"role": "user", "content": text}]})


def _split(url):
    from urllib.parse import urlsplit
    try:
        u = urlsplit(url)
        return (u.hostname or "").lower(), u.path
    except ValueError:
        return None, ""


def aggregator(url):
    """Is the link on a datasheet aggregator or mirror (AGGREGATORS: the host or a domain it is under)?"""
    host = _split(url)[0]
    return host is None or any(re.search(r"(^|\.)" + re.escape(a) + r"(\.|$)", host) for a in AGGREGATORS)


def maker_names(maker):
    """The ways a maker's name may start a label of its site's host: each word of three letters or more (MAKER_WORDS
    left out) and the words run together ('Lite-On' -> liteon, 'Power Integrations' -> powerintegrations)."""
    words = [w for w in re.findall(r"[a-z0-9]+", str(maker or "").lower()) if w not in MAKER_WORDS]
    return {n for n in [w for w in words if len(w) >= 3] + ["".join(words)] if len(n) >= 3}


def rank(urls, maker=None):
    """The links in the order they are downloaded: the maker's own site first (a label of the host starts with its
    name: yageogroup.com for Yageo, assets.wolfspeed.com), then a major distributor's (DISTRIBUTORS), then any other (a
    mirror, or a site the maker's name does not show in); the search's order kept within each. A mirror's copy can be
    an old revision, so it is taken only when no better one gives the part's PDF. -> [url]"""
    names = maker_names(maker)

    def tier(url):
        labels = [re.sub(r"[^a-z0-9]", "", l) for l in (_split(url)[0] or "").split(".")]
        if any(l.startswith(n) for l in labels for n in names):
            return 0
        return 1 if any(l in DISTRIBUTORS for l in labels) else 2
    return sorted(urls, key=tier)


def links_in(content):
    """The answer's links, in its order, then the PDF-looking links of the search results (.pdf, or "datasheet" in the
    path: never the host's name); never an aggregator's (AGGREGATORS). -> [url]"""
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
                      and re.search(r"\.pdf$|datasheet", _split(r["url"])[1], re.I)]
    return [u for u in dict.fromkeys(u.strip().rstrip(".,;") for u in said + found)
            if u.startswith("http") and not aggregator(u)]


def add_usage(total, u):
    for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens"):
        total[k] = total.get(k, 0) + int((u or {}).get(k) or 0)
    total["web_search_requests"] = total.get("web_search_requests", 0) + \
        int(((u or {}).get("server_tool_use") or {}).get("web_search_requests") or 0)
    return total


def search(client, mpn, model, maker=None, package=None, max_uses=MAX_USES, headers=None, extra=None,
           may_continue=None):
    """One search conversation (continued on pause_turn while may_continue(turns) says the cost cap allows another
    turn). extra: more request fields (fallbacks), with their beta in headers. A request that fails ends it: the turns
    answered before it are kept, so what they cost is still counted. -> (links, usage summed over the turns, the
    answered turns' messages, the error or None)"""
    body = dict(request_body(mpn, model, maker, package, max_uses), **(extra or {}))
    usage, turns = {}, []
    for _ in range(MAX_TURNS):
        if turns and may_continue and not may_continue(turns):
            break
        try:
            msg = client.messages(body, headers)
        except Exception as e:                    # (DatasheetError, a timeout: the request may still have been billed)
            return links_in((turns[-1] if turns else {}).get("content")), usage, turns, e
        turns.append(msg)
        add_usage(usage, msg.get("usage"))
        if msg.get("stop_reason") != "pause_turn":
            break
        body = dict(body, messages=body["messages"] + [{"role": "assistant", "content": msg.get("content") or []}])
    return links_in((turns[-1] if turns else {}).get("content")), usage, turns, None


TOKENS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")


def _known(m):
    """Did the turn say what it used? (a gateway may send no usage block, or one without token counts)"""
    return any((m.get("usage") or {}).get(k) for k in TOKENS)


def usd(model, turns, unknown=None):
    """Each turn's tokens at the price of the model that served it (a server-side fallback is billed at its own
    rates), plus the searches. A turn with no usage, or whose model has no known price, costs `unknown` (the worst
    case held for it); None when such a turn was answered and unknown is None."""
    from . import ds_route as DR
    total = 0.0
    for m in turns:
        u = add_usage({}, m.get("usage"))
        tok = DR.usage_usd(DR.billed_model(model, m), u) if _known(m) else None
        if tok is None:
            if unknown is None:
                return None
            total += unknown
            continue
        total += tok + SEARCH_USD * u.get("web_search_requests", 0)
    return round(total, 6)


def _price(model, fallback):
    """(input, output) USD per MTok; fallback: the higher of the model's own and datasheet.FALLBACK_PRICE."""
    from . import datasheet as D
    p = D.price_of(model)
    if p and fallback:
        p = (max(p[0], D.FALLBACK_PRICE[0]), max(p[1], D.FALLBACK_PRICE[1]))
    return p


def estimate(model, max_uses=MAX_USES, fallback=False):
    """The most one part's search may cost, for the cost cap. fallback: a declined request may be re-run on another
    model, priced at the higher of the model's own and datasheet.FALLBACK_PRICE (as a read's worst case is)."""
    p = _price(model, fallback)
    if not p:
        return None
    return (EST_TOKENS[0] * p[0] + EST_TOKENS[1] * p[1]) / 1e6 + SEARCH_USD * max_uses


def next_turn(model, turns, max_uses=MAX_USES, fallback=False):
    """The most a pause_turn continuation after `turns` may cost: it resends the conversation (every search result
    read so far), so its input is at least what the turns read and wrote, plus EST_TOKENS more (a turn with no usage
    counts as EST_TOKENS). Priced as estimate() is; None with no known price."""
    p = _price(model, fallback)
    if not p:
        return None
    sent = sum(sum(add_usage({}, m.get("usage")).get(k, 0) for k in TOKENS) if _known(m) else sum(EST_TOKENS)
               for m in turns)
    return ((sent + EST_TOKENS[0]) * p[0] + EST_TOKENS[1] * p[1]) / 1e6 + SEARCH_USD * max_uses


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
        """url None: the part's link is dropped (it no longer gives its PDF)."""
        try:
            d = json.loads(self.file.read_text(encoding="utf-8")) if self.file.exists() else {}
        except (OSError, ValueError):
            d = {}
        d = d if isinstance(d, dict) else {}
        if url is None:
            d.pop(part, None)
        else:
            d[part] = url
        from .fsutil import write_text_atomic         # a temp file, then replaced: never half a file
        self.file.parent.mkdir(parents=True, exist_ok=True)
        write_text_atomic(self.file, json.dumps(d, indent=1, sort_keys=True))

    def drop(self, part):
        self.put(part, None)
