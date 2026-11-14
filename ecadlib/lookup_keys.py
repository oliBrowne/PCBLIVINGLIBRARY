"""DigiKey, Mouser and Nexar part lookups: the keys they need, how to get them, and a test search that proves they work.

    ecadlib keys check digikey|mouser|nexar      one test search with the saved keys (also the setup page's Check)

Each lookup is optional: KiCad's libraries, EasyEDA/LCSC and the standard packages need no key. The keys are stored like
every other key (ecadlib.userconfig: encrypted for the Windows account on Windows) and never put into the environment.

GUIDES is the one copy of the sign-up steps: the setup page draws its "Part lookups" card from it, and
docs/part-lookup-keys.md and site/keys.html say the same (tests/test_lookup_keys.py checks the addresses and key names
match). The steps were checked against the providers' own pages on 2026-11-14, and the DigiKey and Mouser ones were
also walked through live that day, screen by screen (Nexar's were not):
  DigiKey  developer.digikey.com/tutorials-and-resources/organization-and-production (Organizations > Production Apps >
           Create Production App > Add production app; the app's page holds its credentials) and
           .../oauth-20-2-legged-flow (client-credentials token; the app must be subscribed to the API product)
  Mouser   mouser.com/api-search (sign in, fill in the Search API request form, the key comes by email; 30 calls a
           minute, 1,000 a day)
  Nexar    support.nexar.com "Creating an application" (Apps > Create App, scope Supply and/or Design, which cannot be
           changed later; a new app's part limit is 0 until a plan is chosen under "Manage supply plan") and
           "Getting free parts in the API" (free parts sit on the Evaluation app)."""
import re, tempfile

from . import userconfig as UC

TEST_MPN = "NE555DR"            # a common part every distributor stocks

GUIDES = [
    {"id": "digikey", "label": "DigiKey", "url": "https://developer.digikey.com",
     "fields": [("DIGIKEY_CLIENT_ID", "Client ID"), ("DIGIKEY_CLIENT_SECRET", "Client Secret")],
     "cost": "Free.",
     "steps": [
         "Go to developer.digikey.com and click Register / Login. New here? Click Register now and make a free "
         "account (DigiKey emails you to confirm the address). The first time, accept the API User Agreement.",
         "On Organizations, click + Create Organization. Give it a name no other DigiKey user has (for example "
         "your name plus \"ecadlib\") and click Add organization.",
         "Open the organization's Production Apps tab and click + Create Production App.",
         "Name the app (for example ecadlib). OAuth Callback must be filled in and start with https: type "
         "https://localhost (ecadlib never uses it). Under Products, set Product Information V4 to "
         "ProductInformation V4 and leave the rest on None. Click Add production app. It is approved at once.",
         "Click the app's name. Its Team App Details page shows the Client ID and Client Secret: copy both into the two boxes here. "
         "To find them again later: developer.digikey.com > Organizations > your organization > Production Apps > "
         "the app.",
     ]},
    {"id": "mouser", "label": "Mouser", "url": "https://www.mouser.com/api-search/",
     "fields": [("MOUSER_API_KEY", "Search API key")],
     "cost": "Free. Up to 30 searches a minute and 1,000 a day.",
     "steps": [
         "Open mouser.com/api-search and click Sign Up for Search API. Sign in, or click Create a My Mouser "
         "Account first (free).",
         "Fill in the request form. Phone and Company Name are required (a hobbyist can write Personal; Company "
         "Type: Personal - Hobbyist, Student, Etc). Pick the Country first: the Language and Currency lists fill "
         "in after it.",
         "Application Name: ecadlib. Application Type: Internal desktop application. Estimated Userbase: 1-10 "
         "Users. Users charged: No. Advertising: No.",
         "Tick I agree to Mouser's Web API Terms of Service and click Submit Request.",
         "Mouser emails you the Search API key (it came within minutes on 14 Nov 2026). Paste it here, and keep the "
         "email: it is where you find the key again.",
     ]},
    {"id": "nexar", "label": "Nexar (Octopart)", "url": "https://portal.nexar.com",
     "fields": [("NEXAR_CLIENT_ID", "Client ID"), ("NEXAR_CLIENT_SECRET", "Client Secret")],
     "cost": "Free parts to start with; Nexar counts every part it returns, and more parts need a Nexar plan.",
     "steps": [
         "Sign in or sign up at portal.nexar.com.",
         "Click Apps, then Create App. Tick Supply for part lookups. If you also want to send parts to Altium 365, "
         "tick Design as well: an app's scope cannot be changed later.",
         "A new app can look up 0 parts until it has a plan: click Manage supply plan and pick one. Or use the "
         "Evaluation app Nexar puts on your dashboard, which comes with free parts.",
         "Open the app and copy its Client ID and Client Secret into the two boxes here.",
         "Altium 365 only: give the app the redirect address http://localhost:3000/login and sign in on the "
         "Altium 365 card (or run ecadlib nexar signin). The same app then does both.",
     ]},
]
NAMES = {f[0] for g in GUIDES for f in g["fields"]}


def guide(pid):
    g = next((g for g in GUIDES if g["id"] == pid), None)
    if g is None:
        raise ValueError(f"unknown part lookup {pid!r}: one of {', '.join(x['id'] for x in GUIDES)}")
    return g


def state(environ=None):
    """The setup page's card: each lookup's guide and which of its keys are saved. Never holds a key value."""
    out = []
    for g in GUIDES:
        fields = []
        for name, label in g["fields"]:
            src = UC.key_source(name, environ)
            fields.append({"name": name, "label": label, "stored": src == "stored", "set": bool(src),
                           "from_env": src == "environment"})
        token = g["id"] == "nexar" and bool(UC.key_source("NEXAR_SUPPLY_TOKEN", environ))   # Nexar's other way in
        out.append({"id": g["id"], "label": g["label"], "url": g["url"], "cost": g["cost"], "steps": list(g["steps"]),
                    "fields": fields, "ready": all(f["set"] for f in fields) or token, "supply_token": token})
    return out


def _client(pid, http):
    from . import part_lookup as S
    return {"digikey": S.DigiKeySourcing, "mouser": S.MouserSourcing, "nexar": S.NexarSourcing}[pid](http)


def _part(pid, c):
    """One search for TEST_MPN -> PartInfo. Nexar is asked for 1 result (it counts every part it returns)."""
    p = {"mpn": TEST_MPN, "manufacturer": "", "lcsc": ""}
    if pid != "nexar":
        return c.part(p)
    from . import part_lookup as S
    res = S.PartInfo(c.name, mpn=TEST_MPN)
    data, err = c._gql(c.Q_PART, c._vars(TEST_MPN, 1))
    if err:
        res.error = err
        return res
    return S.parse_nexar_part(data, p, res, c.currency)


def _why(pid, err):
    """The provider's error, plus what to do about it in words a first-time user can act on."""
    g, e = guide(pid), str(err or "")
    low = e.lower()
    if any(w in low for w in ("proxy", "connection", "timeout", "timed out", "offline")):   # first: a network error's
        return f"could not reach {g['label']} ({e}): check the internet connection or proxy, then try again"  # URL may say apiKey
    refused = "token" in low and re.search(r"HTTP 40[013]\b", e)        # an outage (5xx, 429) is not a wrong key
    if pid == "digikey" and refused:
        return f"DigiKey refused the Client ID / Client Secret ({e}): copy both again from your app's page on {g['url']}"
    if pid == "digikey" and ("403" in e or "401" in e):
        return (f"DigiKey knows the app but answered {e}: open the app on {g['url']} and check that Products has "
                "Product Information V4 set to ProductInformation V4 (and that it is a production app, not a sandbox one)")
    if pid == "nexar" and refused:
        return (f"Nexar refused the app ({e}): copy the Client ID and Client Secret again, and check the app has the "
                "Supply scope (a Design-only app cannot look up parts; make a new app with Supply ticked)")
    if pid == "nexar" and any(w in low for w in ("limit", "quota", "plan", "exceed")):
        return f"Nexar: {e}. The app has no parts left: pick a plan under Manage supply plan, or use the Evaluation app"
    if pid == "mouser" and any(w in low for w in ("invalid", "unique identifier", "key")):
        return f"Mouser refused the key ({e}): copy it again from Mouser's email"
    return f"{g['label']} answered: {e}"


def check(pid, http=None):
    """One test search for TEST_MPN with the saved keys -> {ok, message}. Sends nothing when a key is missing. The keys go
    only to the provider's own address; no answer is cached (a fresh folder each time), so an old answer never passes."""
    g = guide(pid)
    missing = [n for n, _ in g["fields"] if not UC.secret(n)]
    if missing and not (pid == "nexar" and UC.secret("NEXAR_SUPPLY_TOKEN")):
        return {"ok": False, "message": f"save {' and '.join(missing)} first (see the steps for {g['label']})"}
    tmp = None
    if http is None:
        from .httpcache import CachedHTTP
        tmp = tempfile.TemporaryDirectory(prefix="ecadlib-keycheck-")
        http = CachedHTTP(tmp.name, min_interval=0, timeout=20)
    try:
        info = _part(pid, _client(pid, http))
    except Exception as e:                       # a provider answering in an odd shape is the check's answer, not a crash
        return {"ok": False, "message": _why(pid, f"{type(e).__name__}: {e}"[:200])}
    finally:
        if tmp is not None:
            tmp.cleanup()
    via = " (tested with NEXAR_SUPPLY_TOKEN, which comes before the Client ID and Secret)" \
        if pid == "nexar" and UC.secret("NEXAR_SUPPLY_TOKEN") else ""
    if getattr(info, "error", ""):
        return {"ok": False, "message": _why(pid, info.error) + via}
    if info.found:
        maker = f" ({info.manufacturer})" if info.manufacturer else ""
        return {"ok": True, "message": f"{g['label']} works: it found {info.mpn or TEST_MPN}{maker}{via}"}
    return {"ok": True, "message": f"{g['label']} accepted the keys (the test part {TEST_MPN} was not found, which is fine){via}"}


def main(names, out=print, http=None):
    """`ecadlib keys check [digikey|mouser|nexar ...]`. With no name, every lookup whose keys are saved is tested and the
    rest are listed as not set up (they are optional). 0 when every tested lookup works."""
    if names:
        ids = [guide(n.lower())["id"] for n in names]
    else:
        ids = [g["id"] for g in state() if g["ready"]]
        for g in state():
            if not g["ready"]:
                out(f"{g['label']}: not set up (optional)")
    bad = 0
    for pid in ids:
        r = check(pid, http=http)
        bad += not r["ok"]
        out(f"{guide(pid)['label']}: {'OK' if r['ok'] else 'NOT WORKING'}: {r['message']}")
    out("How to get the keys: docs/part-lookup-keys.md, or the Part lookups card on the setup page (ecadlib setup).")
    return 1 if bad else 0
