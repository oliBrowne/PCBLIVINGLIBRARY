"""Who may use the local page (review 2026-11-23, 2.1): the page on 127.0.0.1 used to obey any program on the
computer. Each run of the server now has a random key, and every /api request must carry a key of this run in the
X-Ecadlib-Key header:

  - the launcher (`ecadlib ui`, `ecadlib start`, the Altium / KiCad menu) opens the browser with a link that works
    once and for two minutes (a ticket); the server answers it with a small page that puts a session key into the
    tab's own storage (sessionStorage: kept per address AND port, never sent by the browser on its own) and goes on
    to the address without the key. The page's script sends the key with each request;
  - the run's key itself is kept in a file only this user can read (<settings folder>/run/ui-<port>.json), so that
    a second `ecadlib start` or a menu click of the same person can ask the running server for such a link. Another
    account, or a sandboxed program that cannot read the person's files, has no key and is refused.

Not a cookie (second review 2026-11-23, I1): a browser sends a 127.0.0.1 cookie to every port of 127.0.0.1, so any
other local server the browser was led to received the page's key, and could set one of its own. A header the
page's script adds goes to this server only, and no other site can add it (HOST and ORIGIN checks, no CORS).

On Linux a connection from another account's program is refused before any key is looked at (other_account: the
link the launcher hands the browser can be seen in the process list for a moment). Nothing here is ever printed or
logged, except the link `ecadlib ui --no-browser` prints on a terminal for the person to open (there is no other
way to hand it over, e.g. through an SSH port forward)."""
import contextlib, json, os, secrets, socket, struct, sys, threading, time
from pathlib import Path

from .. import userconfig as UC

TICKET_S = 120              # a link the launcher opened: good once, for two minutes (a browser can be slow to start)
KEEP_S = 900                # a used ticket is remembered this long, to notice a second use
MAX_SESSIONS = 200


def _same(a, b):
    """Constant-time comparison of two texts (compare_digest takes str only when it is ASCII)."""
    return secrets.compare_digest(str(a).encode("utf-8", "replace"), str(b).encode("utf-8", "replace"))


class Access:
    """The key of one run and the browser sessions it let in. One object lives as long as the app: the desktop app
    keeps it when the server restarts in another workspace, so the open page keeps working."""

    def __init__(self):
        self.key = secrets.token_urlsafe(32)
        self.sessions, self.tickets, self.lock = {}, {}, threading.Lock()   # session -> made at; ticket -> [until, session]


    def ticket(self):
        """A key for one link: `?key=<ticket>` is answered once, within TICKET_S seconds."""
        now = time.monotonic()
        with self.lock:
            for t in [t for t, (until, _) in self.tickets.items() if until + KEEP_S < now]:
                del self.tickets[t]
            t = secrets.token_urlsafe(24)
            self.tickets[t] = [now + TICKET_S, None]
            return t

    def _session(self):
        s = secrets.token_urlsafe(32)
        self.sessions[s] = time.monotonic()
        for old in list(self.sessions)[:-MAX_SESSIONS]:
            del self.sessions[old]
        return s

    def enter(self, value):
        """The `key` of a link -> a new session (the key the page then sends), or None: not a key, used before, too
        old. A ticket shown a second time means two programs had the link: the session
        the first one got is closed, so whoever was quicker than the person's browser keeps nothing."""
        if not isinstance(value, str) or not value:
            return None
        with self.lock:
            if _same(value, self.key):                      # the run's key (the terminal's link): any number of times
                return self._session()
            hit = None
            for t in self.tickets:                          # every ticket is compared: no early exit to time
                if _same(value, t):
                    hit = t
            if hit is None:
                return None
            until, used = self.tickets[hit]
            if used is not None:
                self.sessions.pop(used, None)
                return None
            if time.monotonic() > until:
                return None
            self.tickets[hit][1] = self._session()
            return self.tickets[hit][1]

    def owner(self, value):
        """Is this the run's key itself (this user's own program, which read it from the run file)? Only that may
        ask for a new link: a page's session cannot hand the page on to another browser."""
        return isinstance(value, str) and bool(value) and _same(value, self.key)

    def allowed(self, value):
        """Is this header value a session of this run (or the run's key, sent by the same person's own program)?"""
        if not isinstance(value, str) or not value:
            return False
        with self.lock:
            ok = _same(value, self.key)
            for s in self.sessions:
                ok = _same(value, s) or ok
            return ok


# ------------------------------------------------------------------ the run file (this user only)
def run_file(port):
    return UC.config_dir() / "run" / f"ui-{int(port)}.json"


def publish(port, access, workspace=None):
    """Write the run's key where only this user can read it. A settings folder that cannot be written is no reason
    to not start: the page that this start opens still works, only a second start or a menu cannot open it."""
    with contextlib.suppress(OSError):
        UC._write_private(run_file(port), json.dumps({"key": access.key, "pid": os.getpid(),
                                                      "workspace": str(workspace or "")}) + "\n")


def unpublish(port, access):
    """Remove the run file when it is still this run's."""
    with contextlib.suppress(OSError, ValueError):
        f = run_file(port)
        if _same(json.loads(f.read_text(encoding="utf-8")).get("key") or "", access.key):
            f.unlink()


def key_for(port):
    """The key of the ecadlib this user runs on the port, or None."""
    try:
        v = json.loads(run_file(port).read_text(encoding="utf-8")).get("key")
    except (OSError, ValueError, AttributeError):
        return None
    return v if isinstance(v, str) and v else None


HEADER = "X-Ecadlib-Key"                       # carries a session key (the page) or the run's key (this user's programs)
STORE = "ecadlib_key"                          # the name the page keeps its session key under (sessionStorage)


def header_for(port):
    """The request header for this user's own programs (the Altium / KiCad menu, a second `ecadlib start`): {} when
    this user runs no ecadlib on the port."""
    key = key_for(port)
    return {HEADER: key} if key else {}


def _proc_addr(ip, port):
    """An address as /proc/net/tcp and tcp6 print it: each 4 bytes as one number in the machine's byte order."""
    raw = socket.inet_pton(socket.AF_INET6 if ":" in ip else socket.AF_INET, ip)
    words = [raw[i:i + 4] for i in range(0, len(raw), 4)]
    return "".join((w[::-1] if sys.byteorder == "little" else w).hex() for w in words).upper() + f":{int(port):04X}"


def other_account(client, server, proc="/proc/net"):
    """Is the program at the other end of this loopback connection run by another account of this computer? Linux
    only (the kernel's socket list names each socket's owner); everywhere else, and whenever the socket is not
    found, the answer is no and the key alone decides. root is never "another account" (it can read the key file).
    Second review 2026-11-23 (I2): the one-time link is on the browser's command line, which other accounts can
    list, and whoever opened it first acted as the person until their browser arrived."""
    if not sys.platform.startswith("linux") or not hasattr(os, "getuid"):
        return False
    try:
        c_ip, c_port, s_ip, s_port = client[0], client[1], server[0], server[1]
        v6 = ":" in c_ip
        mine, theirs = _proc_addr(c_ip, c_port), _proc_addr(s_ip if (":" in s_ip) == v6 else c_ip, s_port)
        with open(os.path.join(proc, "tcp6" if v6 else "tcp"), encoding="ascii", errors="replace") as f:
            for line in f:
                p = line.split()
                if len(p) > 7 and p[1] == mine and p[2] == theirs:
                    return int(p[7]) not in (0, os.getuid())
    except (OSError, ValueError, IndexError, TypeError):
        return False
    return False


def with_key(url, key):
    """http://host:port/path#anchor -> http://host:port/path?key=...#anchor"""
    base, sep, anchor = str(url).partition("#")
    return f"{base}{'&' if '?' in base else '?'}key={key}{sep}{anchor}"


def link(port, path="", host="127.0.0.1", timeout=5.0):
    """The address that opens the page of the ecadlib this user runs on the port: with a one-time key when this user
    has the run's key, else the plain address (the page then says how to open it)."""
    import urllib.error, urllib.request
    shown = f"[{host}]" if ":" in host and not host.startswith("[") else host
    url, head = f"http://{shown}:{int(port)}/{path}", header_for(port)
    if not head:
        return url
    try:
        direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # never through a system proxy
        req = urllib.request.Request(f"http://{shown}:{int(port)}/api/access/ticket", data=b"{}", method="POST",
                                     headers={**head, "Content-Type": "application/json"})
        with direct.open(req, timeout=timeout) as r:
            t = json.loads(r.read().decode("utf-8")).get("ticket")
    except (OSError, ValueError, AttributeError):
        return url
    return with_key(url, t) if isinstance(t, str) and t else url


def _cfg(path):
    return UC.config_dir().resolve(), Path(path).resolve()


def no_workspace(path):
    """Is the folder ecadlib's settings folder (the key file lives there), a folder inside it, or one that holds it?
    Everything under a workspace can be read through the page, so the page never moves the workspace there."""
    try:
        cfg, p = _cfg(path)
    except OSError:
        return True
    return p == cfg or cfg in p.parents or p in cfg.parents


def private_file(path, ws_root):
    """A file of the settings folder, asked for through a workspace that is that folder or above it (`ecadlib ui
    --workspace ~`): not served. (A workspace the person keeps inside the settings folder serves its own files.)"""
    try:
        cfg, p = _cfg(path)
        root = Path(ws_root).resolve()
    except OSError:
        return True
    return (p == cfg or cfg in p.parents) and cfg not in root.parents
