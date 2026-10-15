"""Team mode for the web UI: users, roles, passwords, sessions and the team audit log. Standard library only.

    ecadlib team add-user ann --role admin [--name "Ann Lee"] [--email ann@x.com]   (asks for the password)
    ecadlib team set-role bob librarian [--sign-as librarian,hardware]
    ecadlib team reset-password bob [--password-stdin]
    ecadlib team remove bob
    ecadlib team list
    ecadlib team setup [--domain D] [--admin NAME]    Docker Compose + Caddy (HTTPS), first admin, start (team_setup.py)
    (all take --workspace DIR, default ecadlib_workspace; then: ecadlib ui --team --host 0.0.0.0 --cert c.pem --key k.pem)

Files in the workspace (never served by /api/file):
  team_users.json   users: role, name, e-mail, scrypt password hash (per-user salt), generation counter
  team.json         optional settings (see CONFIG_DEFAULTS): allowed host names, trusted proxy, session lengths, lockout
  team_audit.jsonl  one line per write request in team mode: time, user, client address, route, result

Roles, lowest first; each includes the ones before it:
  viewer     reads everything in the workspace (reports, plans, approval status), downloads files
  librarian  uploads BOMs, triage, librarian, resolve, footprints, exports, release-check overrides, Altium 365 dry run
  approver   approves / rejects plan items (approvals.py), live Altium 365 push
  admin      users, library rules (ecadlib.toml), scanning folders on the server
The approval role recorded with a sign-off is the user's `sign_as` list (default: the team role); approvals.json
`users`/`roles` then work as before, keyed by the e-mail (or name) of the signed-in user."""
import argparse, base64, contextlib, datetime, getpass, hashlib, hmac, ipaddress, json, os, re, secrets, sys, threading, time
from pathlib import Path

ROLES = ("viewer", "librarian", "approver", "admin")
RANK = {r: i for i, r in enumerate(ROLES)}
USERS_FILE, CONFIG_FILE, AUDIT_FILE, LOCK_FILE = "team_users.json", "team.json", "team_audit.jsonl", ".team.lock"
TEAM_FILES = (USERS_FILE, CONFIG_FILE, AUDIT_FILE, LOCK_FILE)
USERNAME = re.compile(r"^[a-z0-9][a-z0-9._@+-]{0,63}$")
MIN_PASSWORD = 10
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}            # ~16 MiB, ~50 ms per check
CONFIG_DEFAULTS = {
    "allowed_hosts": [],          # extra Host names (any IP literal, localhost and this machine's names always pass)
    "trusted_proxies": [],        # proxy IPs whose proxy_user_header is believed (SSO reverse proxy); empty = off
    "proxy_user_header": "X-Forwarded-User",
    "session_hours": 12,          # absolute session lifetime
    "idle_minutes": 120,          # signed out after this long without a request
    "max_failures": 5,            # failed sign-ins per user name within lockout_minutes before it locks
    "lockout_minutes": 15,
    "max_failures_per_ip": 30,
    "secure_cookies": False,      # force the Secure cookie flag (behind an HTTPS proxy that does not send X-Forwarded-Proto)
    "request_seconds": 120,       # whole request (TLS handshake, headers and body) must arrive within this
    "max_connections_per_ip": 64, # open connections per client address (trusted proxies exempt); 0 = no limit
    "audit_max_mb": 10,           # team_audit.jsonl is rotated to .1, .2 ... at this size
    "audit_keep": 5,              # rotated audit files kept
    "audit_refusals_per_minute": 20,   # unauthenticated refusals logged per address per minute; the rest are counted
}


def now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------- passwords
def hash_password(password):
    if not isinstance(password, str) or len(password) < MIN_PASSWORD:
        raise ValueError(f"password must be at least {MIN_PASSWORD} characters")
    if len(password) > 1024:
        raise ValueError("password is too long")
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **SCRYPT)
    return {"alg": "scrypt", **SCRYPT, "salt": base64.b64encode(salt).decode(), "hash": base64.b64encode(dk).decode()}


_DUMMY = None


def verify_password(password, rec):
    """Constant-time check; a missing record still costs one scrypt so unknown names take as long as known ones."""
    global _DUMMY
    if not isinstance(password, str) or len(password) > 1024:
        password = ""
    if not rec or rec.get("alg") != "scrypt":
        _DUMMY = _DUMMY or hash_password("x" * MIN_PASSWORD)
        rec, ok = _DUMMY, False
    else:
        ok = True
    try:
        dk = hashlib.scrypt(password.encode(), salt=base64.b64decode(rec["salt"]), dklen=32,
                            n=int(rec["n"]), r=int(rec["r"]), p=int(rec["p"]))
    except (ValueError, KeyError, TypeError):
        return False
    return hmac.compare_digest(dk, base64.b64decode(rec["hash"])) and ok


# ---------------------------------------------------------------- users file
@contextlib.contextmanager
def _file_lock(root):
    """Serialise writers of team_users.json (the server's admin page and the CLI). No-op where fcntl is missing."""
    try:
        import fcntl
    except ImportError:                               # Windows: single writer assumed
        yield
        return
    with open(Path(root) / LOCK_FILE, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _write_private(path, text):
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def check_username(name):
    name = str(name or "").strip().lower()
    if not USERNAME.match(name):
        raise ValueError("user name: 1-64 characters, letters, digits and . _ @ + - (starting with a letter or digit)")
    return name


def _check_role(role):
    if role not in ROLES:
        raise ValueError(f"role must be one of {', '.join(ROLES)}")
    return role


def _one_line(v, what, n=200):
    v = str(v or "").strip()
    if len(v) > n or any(c in v for c in "\r\n\0<>"):
        raise ValueError(f"{what} must be one line of at most {n} characters without < or >")
    return v


class UserStore:
    """team_users.json, re-read whenever it changes on disk (so `ecadlib team ...` applies to a running server)."""
    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root / USERS_FILE
        self._sig, self._users, self._lock = None, {}, threading.Lock()

    def users(self):
        with self._lock:
            try:
                st = self.path.stat()
                sig = (st.st_mtime_ns, st.st_size)
            except FileNotFoundError:
                self._sig, self._users = None, {}
                return {}
            if sig != self._sig:
                data = json.loads(self.path.read_text() or "{}")
                self._users, self._sig = dict(data.get("users") or {}), sig
            return self._users

    def get(self, name):
        try:
            return self.users().get(check_username(name))
        except ValueError:
            return None

    def find(self, name_or_email):
        """A user by user name or e-mail (what an SSO proxy usually sends)."""
        u = self.get(name_or_email)
        if u:
            return u
        key = str(name_or_email or "").strip().lower()
        return next((u for u in self.users().values() if key and (u.get("email") or "").lower() == key), None)

    def _edit(self, fn):
        with _file_lock(self.root):
            data = json.loads(self.path.read_text() or "{}") if self.path.exists() else {}
            users = data.setdefault("users", {})
            out = fn(users)
            data["version"] = 1
            _write_private(self.path, json.dumps(data, indent=2, sort_keys=True))
        with self._lock:
            self._sig = None
        return out

    def add(self, name, role, password=None, full_name="", email="", sign_as=None):
        name, role = check_username(name), _check_role(role)
        full_name, email = _one_line(full_name, "name"), _one_line(email, "e-mail")
        if email and not re.match(r"^[^@\s]+@[^@\s]+$", email):
            raise ValueError("e-mail looks wrong")
        pw = hash_password(password) if password is not None else None

        def fn(users):
            if name in users:
                raise FileExistsError(f"user {name} already exists")
            users[name] = {"username": name, "role": role, "name": full_name, "email": email, "password": pw,
                           "sign_as": _sign_as(sign_as), "gen": secrets.randbits(48), "created": now()}
            # gen starts random: a session of a removed user must not come back when the name is added again
            return dict(users[name])
        return self._edit(fn)

    def set_role(self, name, role, sign_as=None):
        name, role = check_username(name), _check_role(role)

        def fn(users):
            u = _need(users, name)
            if u["role"] == "admin" and role != "admin" and _admins(users) == 1:
                raise ValueError("that is the last admin: make someone else admin first")
            u["role"] = role
            if sign_as is not None:
                u["sign_as"] = _sign_as(sign_as)
            return dict(u)
        return self._edit(fn)

    def reset_password(self, name, password):
        name = check_username(name)
        pw = hash_password(password)

        def fn(users):
            u = _need(users, name)
            u["password"], u["gen"] = pw, int(u.get("gen") or 1) + 1      # signs out every session of this user
            return dict(u)
        return self._edit(fn)

    def remove(self, name):
        name = check_username(name)

        def fn(users):
            u = _need(users, name)
            if u["role"] == "admin" and _admins(users) == 1:
                raise ValueError("that is the last admin and cannot be removed")
            del users[name]
            return u
        return self._edit(fn)

    def public(self):
        return [{k: u.get(k) for k in ("username", "name", "email", "role", "sign_as", "created")} | {"password": bool(u.get("password"))}
                for u in sorted(self.users().values(), key=lambda u: u["username"])]


def _sign_as(v):
    if v is None:
        return []
    v = [x.strip() for x in (v.split(",") if isinstance(v, str) else v) if isinstance(x, str) and x.strip()]
    for x in v:
        if not re.match(r"^[A-Za-z0-9 ._-]{1,40}$", x):
            raise ValueError(f"sign-as role {x!r}: letters, digits, space . _ - only")
    return v


def _need(users, name):
    if name not in users:
        raise KeyError(f"no user {name}")
    return users[name]


def _admins(users):
    return sum(u.get("role") == "admin" for u in users.values())


def identity(user):
    """How a team user is recorded in approvals: 'Name <email>' (approvals.user_key then keys on the e-mail)."""
    name = user.get("name") or user["username"]
    return f"{name} <{user['email']}>" if user.get("email") else name


def sign_roles(user):
    return list(user.get("sign_as") or []) or [user["role"]]


# ---------------------------------------------------------------- settings
def load_config(root, overrides=None):
    f = Path(root) / CONFIG_FILE
    raw = json.loads(f.read_text() or "{}") if f.exists() else {}
    bad = set(raw) - set(CONFIG_DEFAULTS)
    if bad:
        raise ValueError(f"{f}: unknown keys {sorted(bad)}; known: {sorted(CONFIG_DEFAULTS)}")
    cfg = {**CONFIG_DEFAULTS, **raw}
    for k, v in (overrides or {}).items():
        if v not in (None, [], ()):
            cfg[k] = list(cfg[k]) + list(v) if isinstance(CONFIG_DEFAULTS[k], list) else v
    for ip in cfg["trusted_proxies"]:
        ipaddress.ip_address(ip)                       # raises ValueError on a typo
    cfg["allowed_hosts"] = [h.lower() for h in cfg["allowed_hosts"]]
    return cfg


# ---------------------------------------------------------------- sessions and sign-in throttling
class Sessions:
    """In memory: a restart signs everyone out. token -> {user, gen, csrf, created, seen}."""
    def __init__(self, hours=12, idle_minutes=120):
        self.max_age, self.idle = hours * 3600, idle_minutes * 60
        self.s, self.lock = {}, threading.Lock()

    def create(self, username, gen):
        tok = secrets.token_urlsafe(32)
        t = time.time()
        with self.lock:
            self.s = {k: v for k, v in self.s.items() if not self._expired(v, t)}
            self.s[tok] = {"user": username, "gen": gen, "csrf": secrets.token_urlsafe(32), "created": t, "seen": t}
        return tok, self.s[tok]

    def _expired(self, v, t):
        return t - v["created"] > self.max_age or t - v["seen"] > self.idle

    def get(self, tok):
        if not tok:
            return None
        t = time.time()
        with self.lock:
            v = self.s.get(tok)
            if v and self._expired(v, t):
                del self.s[tok]
                return None
            if v:
                v["seen"] = t
            return v

    def drop(self, tok):
        with self.lock:
            self.s.pop(tok, None)

    def remaining(self, v):
        return max(0, int(v["created"] + self.max_age - time.time()))


class Throttle:
    """Failed sign-ins per user name and per client address. Unknown names count too (no user enumeration)."""
    def __init__(self, max_failures=5, lockout_minutes=15, max_per_ip=30):
        self.max, self.window, self.max_ip = max_failures, lockout_minutes * 60, max_per_ip
        self.by_user, self.by_ip, self.lock = {}, {}, threading.Lock()

    def _recent(self, d, k, t):
        d[k] = [x for x in d.get(k, []) if t - x < self.window]
        return d[k]

    def _wait(self, user, ip, t):
        u, i = self._recent(self.by_user, user, t), self._recent(self.by_ip, ip, t)
        waits = [int(u[-self.max] + self.window - t) + 1] if len(u) >= self.max else []
        waits += [int(i[-self.max_ip] + self.window - t) + 1] if len(i) >= self.max_ip else []
        return max(waits, default=0)

    def blocked(self, user, ip):
        """Seconds until this user name / address may try again, or 0."""
        with self.lock:
            return self._wait(user, ip, time.time())

    def attempt(self, user, ip):
        """Check and count one password try in one step: (seconds to wait, None) or (0, ticket). The try counts as a
        failure until ok(user, ip, ticket), so parallel requests cannot all pass the check before any failure is
        recorded (that let N concurrent guesses through instead of max_failures)."""
        t = time.time()
        with self.lock:
            wait = self._wait(user, ip, t)
            if wait:
                return wait, None
            self.by_user[user].append(t)
            self.by_ip[ip].append(t)
            return 0, t

    def fail(self, user, ip):
        t = time.time()
        with self.lock:
            self._recent(self.by_user, user, t).append(t)
            self._recent(self.by_ip, ip, t).append(t)

    def ok(self, user, ip=None, ticket=None):
        """A right password: forget this user's failures (and the try counted by attempt())."""
        with self.lock:
            self.by_user.pop(user, None)
            if ticket is not None:
                with contextlib.suppress(ValueError):
                    self.by_ip.get(ip, []).remove(ticket)

    def unlock(self, user):
        self.ok(user)


class Team:
    """Everything the web server needs for team mode, for one workspace."""
    def __init__(self, root, overrides=None, tls=False):
        self.root = Path(root)
        self.cfg = load_config(root, overrides)
        self.store = UserStore(root)
        self.sessions = Sessions(self.cfg["session_hours"], self.cfg["idle_minutes"])
        self.throttle = Throttle(self.cfg["max_failures"], self.cfg["lockout_minutes"], self.cfg["max_failures_per_ip"])
        self.secret = secrets.token_bytes(32)
        self.tls = tls
        self.refusals = RefusalLog(self.cfg["audit_refusals_per_minute"])

    def audit(self, entry):
        """One team audit line; unauthenticated refusals are rate-limited per address (RefusalLog)."""
        if entry.get("user") is None and entry.get("status", 0) >= 400:
            extra = self.refusals.admit(entry.get("ip"))
            if extra is None:
                return
            entry = {**entry, **extra}
        audit(self.root, entry, int(self.cfg["audit_max_mb"] * 1024 * 1024), int(self.cfg["audit_keep"]))

    def has_admin(self):
        return any(u.get("role") == "admin" for u in self.store.users().values())

    def proxy_csrf(self, username):
        return hmac.new(self.secret, b"proxy\0" + username.encode(), hashlib.sha256).hexdigest()

    def host_ok(self, host_header):
        """DNS rebinding guard: the Host must be an IP literal, localhost, this machine's name or an allowed name."""
        import socket
        h = str(host_header or "").strip().lower()
        if not h:
            return False
        name = h[1:h.index("]")] if h.startswith("[") and "]" in h else h.rsplit(":", 1)[0] if h.count(":") == 1 else h
        try:
            ipaddress.ip_address(name)
            return True
        except ValueError:
            pass
        names = {"localhost", socket.gethostname().lower(), socket.getfqdn().lower(), *self.cfg["allowed_hosts"]}
        return name in names


_audit_lock = threading.Lock()
AUDIT_MAX_BYTES, AUDIT_KEEP = CONFIG_DEFAULTS["audit_max_mb"] * 1024 * 1024, CONFIG_DEFAULTS["audit_keep"]


def _audit_files(root, keep):
    f = Path(root) / AUDIT_FILE
    return [f] + [f.with_name(f"{AUDIT_FILE}.{i}") for i in range(1, keep + 1)]


def audit(root, entry, max_bytes=AUDIT_MAX_BYTES, keep=AUDIT_KEEP):
    """Append one line; at max_bytes the file is rotated (team_audit.jsonl -> .1 -> .2 ..., `keep` old files)."""
    line = json.dumps({"ts": now(), **entry}, sort_keys=True) + "\n"
    with _audit_lock:
        files = _audit_files(root, keep)
        with contextlib.suppress(FileNotFoundError):
            if keep > 0 and files[0].stat().st_size + len(line) > max_bytes:
                for src, dst in reversed(list(zip(files, files[1:]))):
                    if src.exists():
                        os.replace(src, dst)
        with open(files[0], "a") as f:
            f.write(line)


def _tail(path, n, block=65536):
    """The last n lines of a file, reading backwards from the end (never the whole file)."""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        pos, data = f.tell(), b""
        while pos > 0 and data.count(b"\n") <= n:
            step = min(block, pos)
            pos -= step
            f.seek(pos)
            data = f.read(step) + data
    lines = data.splitlines()
    return lines[-n:] if pos == 0 else lines[1:][-n:]       # the first line may be cut when we stopped early


def read_audit(root, n=200, keep=AUDIT_KEEP):
    """The newest n entries, newest first, from team_audit.jsonl and then its rotated files."""
    out = []
    for f in _audit_files(root, keep):
        if len(out) >= n:
            break
        if not f.exists():
            continue
        for l in reversed(_tail(f, n - len(out))):
            with contextlib.suppress(json.JSONDecodeError, UnicodeDecodeError):
                out.append(json.loads(l))
    return out[:n]


class RefusalLog:
    """Unauthenticated refusals (401/403/429 before sign-in) cost an anonymous client nothing, so each address gets
    `per_minute` audit lines per minute; the rest are counted and reported as "suppressed" on its next line."""
    def __init__(self, per_minute=20):
        self.per_minute, self.seen, self.lock = per_minute, {}, threading.Lock()

    def admit(self, ip):
        """None: drop this line. Else a dict to merge into it ({} or {"suppressed": n})."""
        if self.per_minute <= 0:
            return {}
        t = time.time()
        with self.lock:
            if len(self.seen) > 10000:                  # forget quiet addresses
                self.seen = {k: v for k, v in self.seen.items() if t - v[0] < 60}
            start, count, dropped = self.seen.get(ip, (t, 0, 0))
            if t - start >= 60:
                start, count = t, 0
            if count >= self.per_minute:
                self.seen[ip] = (start, count, dropped + 1)
                return None
            self.seen[ip] = (start, count + 1, 0)
            return {"suppressed": dropped} if dropped else {}


# ---------------------------------------------------------------- CLI
def _read_password(a, prompt="password"):
    if a.password_stdin:
        return sys.stdin.readline().rstrip("\r\n")
    p1 = getpass.getpass(f"{prompt} (at least {MIN_PASSWORD} characters): ")
    if p1 != getpass.getpass("again: "):
        sys.exit("passwords differ")
    return p1


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["setup"]:                         # Docker Compose + Caddy for the whole team (team_setup.py)
        from . import team_setup
        return team_setup.main(argv[1:])
    ap = argparse.ArgumentParser(prog="ecadlib team", description="manage web UI team users (ecadlib ui --team)",
                                 epilog="ecadlib team setup -h: run the team server with Docker and HTTPS in one command")
    ap.add_argument("--workspace", default="ecadlib_workspace")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("add-user", help="add a user (the first one must be an admin)")
    p.add_argument("user")
    p.add_argument("--role", required=True, choices=ROLES)
    p.add_argument("--name", default="", help="full name, recorded with approvals")
    p.add_argument("--email", default="", help="e-mail, recorded with approvals and matched by approvals.json users")
    p.add_argument("--sign-as", help="comma list of approval roles this user signs as (default: the team role)")
    p.add_argument("--password-stdin", action="store_true", help="read the password from one line of stdin")
    p.add_argument("--no-password", action="store_true", help="sign-in only through the trusted SSO proxy header")
    p = sub.add_parser("set-role", help="change a user's role")
    p.add_argument("user")
    p.add_argument("role", choices=ROLES)
    p.add_argument("--sign-as", help="comma list of approval roles (empty string: the team role)")
    p = sub.add_parser("reset-password", help="set a new password; signs the user out everywhere")
    p.add_argument("user")
    p.add_argument("--password-stdin", action="store_true")
    p = sub.add_parser("remove", help="remove a user")
    p.add_argument("user")
    sub.add_parser("list", help="list users")
    a = ap.parse_args(argv)
    root = Path(a.workspace).resolve()
    root.mkdir(parents=True, exist_ok=True)
    st = UserStore(root)
    try:
        if a.cmd == "add-user":
            if not st.users() and a.role != "admin":
                sys.exit("the first user must be an admin (--role admin)")
            pw = None if a.no_password else _read_password(a)
            u = st.add(a.user, a.role, pw, a.name, a.email, a.sign_as)
            print(f"added {u['username']} as {u['role']} -> {st.path}")
        elif a.cmd == "set-role":
            u = st.set_role(a.user, a.role, a.sign_as)
            print(f"{u['username']} is now {u['role']}" + (f", signs as {', '.join(u['sign_as'])}" if u.get("sign_as") else ""))
        elif a.cmd == "reset-password":
            st.reset_password(a.user, _read_password(a, "new password"))
            print(f"password reset for {check_username(a.user)}; their sessions end on their next request")
        elif a.cmd == "remove":
            st.remove(a.user)
            print(f"removed {check_username(a.user)}")
        else:
            for u in st.public():
                print(f"  {u['username']:<20} {u['role']:<10} {identity(u) if u.get('name') or u.get('email') else '':<36}"
                      + ("" if u["password"] else " (SSO proxy only)"))
            if not st.users():
                print(f"no users yet: ecadlib team --workspace {a.workspace} add-user NAME --role admin")
    except (ValueError, KeyError, FileExistsError) as e:
        sys.exit(f"ecadlib team: {e.args[0] if e.args else e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
