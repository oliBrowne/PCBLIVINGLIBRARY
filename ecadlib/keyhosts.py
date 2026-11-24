"""Which server a key may be sent to (review 2026-11-23, 2.2).

A rules file (ecadlib.toml) names the server an AI request goes to ([ai] base_url) and the NAME of the key sent with
it ([ai] api_key_env, [agent] gitlab_token_env, a notify target's token_env). The file may be one that merely sits in
the folder a command runs in (a project someone sent), so by itself it must not be able to send a key this user stored
for one service to another server. The rule, for every key ecadlib sends on a rules file's word:

  - a key ecadlib knows (HOMES: ANTHROPIC_API_KEY, OPENAI_API_KEY, GITLAB_TOKEN, the part-lookup keys ...) goes to
    the service it is for, and nowhere else;
  - a key under a name of your own goes to the known services (a second Claude key under another name), and to a
    server on this computer (localhost: LM Studio or vLLM started with a key);
  - anything else needs your word, once, given where a rules file cannot write it:
        ecadlib keys allow NAME HOST        asks on the terminal, kept in this user's settings.json ("key_hosts")
        ECADLIB_KEY_HOSTS="NAME=host,NAME2=host2"     the environment, for CI and scripts (nothing to answer there)
    Without it nothing is sent and the message names the key and the host.

Hosts are compared whole and lower-case (the port does not matter: a key allowed for gateway.example goes to it on any
port, over https or, on your own network, http as rules.validate allows)."""
import ipaddress, os, re
from urllib.parse import urlsplit

ENV = "ECADLIB_KEY_HOSTS"
SETTING = "key_hosts"                       # settings.json: {"NAME": ["host", ...]}
ANTHROPIC = "api.anthropic.com"
# the service each key ecadlib knows belongs to: the only hosts it is sent to without your word. () = never sent as a
# key at all (AWS credentials sign a request; a webhook URL is an address, not a key).
HOMES = {
    "ANTHROPIC_API_KEY": (ANTHROPIC,),
    "OPENAI_API_KEY": ("api.openai.com",),
    "OPENROUTER_API_KEY": ("openrouter.ai",),
    "GEMINI_API_KEY": ("generativelanguage.googleapis.com",),
    "GOOGLE_API_KEY": ("generativelanguage.googleapis.com",),
    "GITHUB_TOKEN": ("api.github.com",), "GH_TOKEN": ("api.github.com",),
    "GITLAB_TOKEN": ("gitlab.com",),
    "DIGIKEY_CLIENT_ID": ("api.digikey.com",), "DIGIKEY_CLIENT_SECRET": ("api.digikey.com",),
    "MOUSER_API_KEY": ("api.mouser.com",),
    "NEXAR_CLIENT_ID": ("identity.nexar.com",), "NEXAR_CLIENT_SECRET": ("identity.nexar.com",),
    "NEXAR_TOKEN": ("api.nexar.com", "files.nexar.com"), "NEXAR_SUPPLY_TOKEN": ("api.nexar.com",),
    "NEXAR_SIGNIN": (),
    "AWS_ACCESS_KEY_ID": (), "AWS_SECRET_ACCESS_KEY": (), "AWS_SESSION_TOKEN": (),
    "SLACK_WEBHOOK_URL": (),
}
KNOWN_HOSTS = frozenset(h for hosts in HOMES.values() for h in hosts)
HOST_RE = re.compile(r"[a-z0-9]([a-z0-9._-]{0,251}[a-z0-9])?|\[?[0-9a-f:]+\]?")


_ODD = re.compile(r"[\\\s\x00-\x20\x7f-\x9f\u200b-\u200f\u2028-\u202f\u2060-\u206f\ufeff]")


def split_host(url_or_host):
    """(scheme, host, port) of a URL or a bare host: host lower case, no brackets, no trailing dot. None when it has
    no host, or when it is written so that two programs could read two different hosts out of it (second review
    2026-11-23, C1: "https://evil\\@api.anthropic.com" is api.anthropic.com to urlsplit and evil to the HTTP client,
    which got the key): a backslash, a blank or control character anywhere, a user@ part, or a host that is
    another one once `requests` has prepared the address for sending."""
    s = str(url_or_host or "").strip()
    if not s or _ODD.search(s):
        return None
    try:
        u = urlsplit(s if "://" in s else "//" + s)
        host, port = (u.hostname or "").lower().rstrip("."), u.port
    except ValueError:
        return None
    if not host or "@" in u.netloc or not HOST_RE.fullmatch(host) or "%" in host:
        return None
    if "://" in s:                                    # the address as the HTTP client itself would send it
        try:
            from requests.models import PreparedRequest
        except ImportError:
            PreparedRequest = None
        if PreparedRequest is not None:
            try:
                sent = PreparedRequest()
                sent.prepare_url(s, None)
                theirs = (urlsplit(sent.url).hostname or "").lower().rstrip(".")
            except Exception:
                return None
            if theirs != host:
                return None
    return u.scheme.lower(), host, port


def host_of(url_or_host):
    """The host of a URL or a bare host (lower case, no port, no brackets), or "" when there is none (split_host:
    also "" for an address two parsers could read differently, so nothing is ever sent on its word)."""
    got = split_host(url_or_host)
    return got[1] if got else ""


def this_computer(host):
    """localhost or a loopback address: a server on this computer (not merely on your network). A name under
    .localhost is not taken for it: outside a browser such a name is looked up like any other."""
    host = host_of(host)
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _env_pairs(environ=None):
    env = os.environ if environ is None else environ
    out = set()
    for part in re.split(r"[,;\s]+", str(env.get(ENV) or "")):
        name, sep, host = part.partition("=")
        if sep and name and host_of(host):
            out.add((name.strip(), host_of(host)))
    return out


def allowed_hosts(name, environ=None):
    """The hosts this user allowed for a key: settings.json and $ECADLIB_KEY_HOSTS (sorted)."""
    from . import userconfig as UC
    try:
        got = (UC.load_settings().get(SETTING) or {}).get(name) or []
    except Exception:                                 # a broken config folder: nothing was allowed
        got = []
    hosts = {host_of(h) for h in got if isinstance(h, str)} if isinstance(got, list) else set()
    return sorted({h for h in hosts if h} | {h for n, h in _env_pairs(environ) if n == name})


def ok(name, url_or_host, environ=None):
    """May the key stored (or set in the environment) under `name` be sent to this server? See the module text."""
    host = host_of(url_or_host)
    if not name or not host:
        return False
    if host in allowed_hosts(name, environ):
        return True
    if name in HOMES:
        return host in HOMES[name]
    return host in KNOWN_HOSTS or this_computer(host)


def refusal(name, url_or_host, what="the key"):
    """One plain message for a key that was not sent: names the key and the host, and how to allow it."""
    host = host_of(url_or_host) or str(url_or_host or "?")
    mine = f"{name} is the key of another service" if HOMES.get(name) else \
        f"{name} is never sent to a server" if name in HOMES else f"you have not allowed {name} for it"
    return (f"the settings file asks to send {what} {name} to {host}, and {mine}. Nothing was sent. If {host} is a "
            f"server you trust with this key, allow it once in a terminal: ecadlib keys allow {name} {host} "
            f"(or set {ENV}=\"{name}={host}\" for a script)")


def allow(name, host):
    """Remember this user's word that `name` may go to `host` (settings.json). -> the hosts now allowed for it."""
    from . import userconfig as UC
    UC.check_name(name)
    h = host_of(host)
    if not h or not HOST_RE.fullmatch(h):
        raise ValueError(f"{host!r} is not a host name (give it as gateway.example.com, or the server's URL)")
    with UC._WRITE:
        cur = UC.load_settings().get(SETTING)
        cur = dict(cur) if isinstance(cur, dict) else {}
        hosts = [x for x in (cur.get(name) or []) if isinstance(x, str)] if isinstance(cur.get(name), list) else []
        if h not in hosts:
            hosts.append(h)
        cur[name] = hosts
        UC.save_settings({SETTING: cur})
    return hosts


def forget(name, host=None):
    """Take an allowed host (or every one) of a key back. -> True when something was removed."""
    from . import userconfig as UC
    with UC._WRITE:
        cur = UC.load_settings().get(SETTING)
        cur = dict(cur) if isinstance(cur, dict) else {}
        had = [x for x in (cur.get(name) or []) if isinstance(x, str)] if isinstance(cur.get(name), list) else []
        keep = [] if host is None else [x for x in had if x != host_of(host)]
        if keep == had:
            return False
        if keep:
            cur[name] = keep
        else:
            cur.pop(name, None)
        UC.save_settings({SETTING: cur})
    return True


TEST_ENDPOINTS = "ECADLIB_TEST_ENDPOINTS"            # = 1: a test's own server may stand in for a service


def service_url(var, default, environ=None, same_host=False, warn=None):
    """The address of a service whose key or token ecadlib sends (Nexar's token and API addresses), which an
    environment variable may replace: review 2026-11-23, NEXAR_TOKEN_URL was used as given, so http:// or another
    host got the Client Secret. The variable's value is used only when it is https:// (same_host: and on the
    default's own host), or http:// to this computer (a test server); $ECADLIB_TEST_ENDPOINTS=1 lifts both for a
    test. A value that is refused is said once on stderr (the variable's name and host, never the value) and the
    default is used."""
    env = os.environ if environ is None else environ
    got = str(env.get(var) or "").strip()
    if not got or got == default:
        return default
    try:
        u = urlsplit(got)
        scheme, host = u.scheme.lower(), (u.hostname or "").lower()
    except ValueError:
        scheme = host = ""
    if str(env.get(TEST_ENDPOINTS) or "").strip() == "1" and scheme in ("http", "https") and host:
        return got
    if scheme == "http" and host and this_computer(host) and not same_host:
        return got
    if scheme == "https" and host and (not same_host or host == host_of(default)):
        return got
    why = f"it must be https:// on {host_of(default)}" if same_host else "it must be an https:// address"
    (warn or _warn)(f"ecadlib: ${var} ({host or 'no host'}) is not used: {why}; using {host_of(default)} "
                    f"(a test server: set {TEST_ENDPOINTS}=1)")
    return default


def _warn(text):
    import sys
    print(text, file=sys.stderr)
