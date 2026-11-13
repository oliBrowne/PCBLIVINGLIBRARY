"""The Internet Archive's saved copy of a datasheet link that gives no PDF here ([datasheet] archive, on by default).

A BOM's datasheet link often dies (the maker renumbered its documents, moved its site, was bought) or is refused to
every automatic way of downloading it (pdf_fetch). The Wayback Machine (web.archive.org) has usually saved the PDF
that link once gave. Once none of a part's links gave a PDF, datasheet.Reader.job asks the Archive's availability API
for the newest good capture of each such link and downloads it as it downloads any link; the PDF must still name the
part. Free, no key, no account.

What is sent: the link itself, to archive.org, and only a public one (asked()): never a link to this machine, a
private network's address, an intranet name, a link with a user name in it or one whose query holds a key or a
signature. The copy can be an older revision than the maker's current one, so the part's review says where the PDF
came from and when it was saved."""
import json
import re
from urllib.parse import parse_qsl, quote, urlsplit

API = "https://archive.org/wayback/available?url="
WHERE = "Internet Archive"
MAX_LINKS = 3                                          # links asked for per part (a part rarely has more)
TRIES = 3                                              # asks per lookup while the Archive is busy (429, 5xx, a timeout)
OLDER = 2                                              # older captures tried when the newest is not the PDF
STEP_DAYS = 365                                        # how far back each older capture is asked for
# names only a private network answers: never sent to the Archive (a name that does not resolve here says nothing:
# a maker's old domain no longer resolves either, and that is the link an archived copy is for)
_INTRANET = re.compile(r"\.(local|localdomain|lan|internal|intranet|corp|home|private|test|invalid|localhost)\.?$", re.I)
_SECRET_Q = re.compile(r"(key|token|secret|password|passwd|signature|sig|auth|credential|expires|policy)$|^x-amz-|^x-goog-",
                       re.I)
_SNAPSHOT = re.compile(r"https?://web\.archive\.org/web/(\d{14})/(\S+)$")
# what a link's failure says of this machine's network, not of the link: the Archive would not answer either
_NO_NETWORK = re.compile(r"proxy CONNECT refused|ProxyError|ERR_PROXY|ERR_TUNNEL_CONNECTION|ERR_INTERNET_DISCONNECTED|"
                         r"offline and not downloaded", re.I)
# ... and failures an older copy of the same document would repeat, or that say the link must not be used at all
_SAME_AGAIN = re.compile(r"doesn't mention |: not downloaded \(|larger than \d+ MB|redirected to .*, not (used|followed)")


def asked(url):
    """Why a link is never sent to the Archive, or None when it may be: a public http(s) link with nothing private
    in it (datasheet.unsafe_download's rule for a web search's link, plus intranet names, user names and signed
    queries)."""
    from .datasheet import unsafe_download
    try:
        u = urlsplit(str(url))
        host = (u.hostname or "").lower()
    except ValueError:
        return "not a valid URL"
    if u.username or u.password:
        return "the link holds a user name or password"
    if "." not in host.strip(".") or _INTRANET.search(host):
        return f"{host or 'no host'} is not a public site's name"
    if any(_SECRET_Q.search(k) for k, _ in parse_qsl(u.query, keep_blank_values=True)):
        return "the link's query holds a key or a signature"
    return unsafe_download(url, public=True)


def worth(error):
    """Could an archived copy help after this failure of the link itself? Not when this machine's network refused
    (the Archive is as far away), nor when the same document would fail the same way (it does not name the part, it
    is too large) or the link must not be downloaded at all."""
    e = re.sub(r" \(also tried .*\)$", "", str(error or ""), flags=re.S)
    return bool(e) and not _NO_NETWORK.search(e) and not _SAME_AGAIN.search(e)


def query(url, before=None):
    """The availability API's URL for a link (the link whole, percent-encoded: its own query and %20 kept).
    before: "YYYYMMDD", the capture closest to that day instead of the newest."""
    return API + quote(str(url), safe="") + (f"&timestamp={before}" if before else "")


def earlier(saved, days=STEP_DAYS):
    """The day `days` before a capture's day ("YYYY-MM-DD") as the API's timestamp "YYYYMMDD"."""
    import datetime
    return (datetime.date.fromisoformat(saved) - datetime.timedelta(days=days)).strftime("%Y%m%d")


def parse(text):
    """The API's answer -> (the capture's raw-bytes URL, the day it was saved "YYYY-MM-DD"), or None: no capture, one
    that was not a 200, or an answer that is not the API's. The raw URL (the capture's timestamp + "id_") gives the
    file as the site sent it, with none of the Wayback Machine's page around it."""
    try:
        snap = json.loads(text)["archived_snapshots"]["closest"]
        ok = snap.get("available") is True and str(snap.get("status")) == "200"
        m = _SNAPSHOT.match(str(snap.get("url") or "").strip())
    except (ValueError, TypeError, KeyError, AttributeError):
        return None
    if not ok or not m:
        return None
    ts = m.group(1)
    return f"https://web.archive.org/web/{ts}id_/{m.group(2)}", f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}"


def _ask(q, http, timeout):
    if http is not None and hasattr(http, "get"):
        r = http.get(q)
        return getattr(r, "status", 0), getattr(r, "text", "") or "", getattr(r, "error", None)
    import requests
    try:
        r = requests.get(q, timeout=timeout, headers={"Accept": "application/json"})
        return r.status_code, r.text, None
    except requests.RequestException as e:
        return 0, "", f"{type(e).__name__}: {str(e)[:120]}"


def lookup(url, http=None, timeout=20, before=None, sleep=None, tries=TRIES):
    """Ask the Archive for its newest good capture of url (before: the one closest to that day, "YYYYMMDD") ->
    ((raw URL, day), None) or (None, why). http: an httpcache.CachedHTTP (its cache, rate limit and offline
    switch), else one plain request. A busy Archive (429, 5xx, a timeout or a dropped connection) is asked again,
    up to `tries` times, 2 s then 4 s apart; a proxy refusal or being offline is not (asking again changes nothing)."""
    import time
    q, why = query(url, before), ""
    for n in range(tries):
        status, text, err = _ask(q, http, timeout)
        if not err and 200 <= (status or 0) < 300:
            got = parse(text)
            return (got, None) if got else (None, "the Internet Archive has no saved copy of it")
        why = f"the Internet Archive did not answer ({err if err and not status else f'HTTP {status}'})"
        busy = status == 429 or 500 <= (status or 0) < 600 or (not status and not _NO_NETWORK.search(err or "")
                                                                 and "offline" not in (err or ""))
        if not busy or n + 1 == tries:
            break
        (sleep or time.sleep)(2 * 2 ** n)
    return None, why + (f" ({n + 1} tries)" if n else "")


# a capture that is not the file the link once gave (a bot-check or "moved" page the Archive saved as a 200, a cut-off
# save): an older capture can be the PDF. One that is the PDF of another part, or too large, is the same document
_NOT_THE_FILE = re.compile(r"not a PDF|the PDF is cut off|HTTP [45]\d\d|the PDF opens as ")


def older_worth(error):
    """After a capture failed this way, is an older capture of the link worth asking for?"""
    return bool(_NOT_THE_FILE.search(str(error or ""))) and not _SAME_AGAIN.search(str(error or ""))


def review_line(prov):
    """The review line for a datasheet read from an archived copy (prov["archived"]: {"of", "saved"}), or None."""
    a = (prov or {}).get("archived")
    if not isinstance(a, dict):
        return None
    return (f"datasheet from the Internet Archive's copy of {a.get('of')} saved {a.get('saved')} (the link gave no PDF "
            "here): the maker may have a newer revision; check the package drawing against the current datasheet")
