"""Bug reports: the user sends them themselves, or, only if they switched it on, ecadlib sends a short public-safe part.

    ecadlib report-bug              show the last error ecadlib saved, then open a pre-filled GitHub issue
    ecadlib report-bug --print      only show the text and the link
    ecadlib report-bug --list       the saved errors, newest first
    ecadlib report-bug --clear      delete the saved errors
    ecadlib report-bug --auto on|off|status    automatic crash reports (opt-in; see send() for what goes)

When a command, the web UI or the MCP server hits an error it did not expect (a bug, not a wrong path or a bad
setting), record() saves it in the config folder (crashes/, the newest MAX_KEPT) with API keys, tokens, e-mail
addresses, the user name, the company in an Altium 365 address and the home folder blanked out. report-bug turns
the newest one into a GitHub issue link; the user reads the text on GitHub and decides whether to press Submit."""
import argparse, getpass, itertools, json, os, platform, re, sys, threading, time, traceback, urllib.request, webbrowser
from pathlib import Path
from urllib.parse import quote, urlencode

ISSUES_NEW = "https://github.com/oliBrowne/PCBLIVINGLIBRARY/issues/new"
MAX_KEPT = 20
MAX_URL = 7000                                # GitHub refuses much longer links; the traceback's top is cut first
MAX_TB_LINES = 80
MAX_LINE, MAX_LAST, MAX_BODY_LINE = 500, 1000, 300   # characters per traceback line: saved / its last line / in the link
SECRET_NAME = re.compile(r"(?:^|_)(?:API_?KEY|KEY|TOKEN|SECRET|PASSWORD|PASSWD|PAT|CREDENTIALS?|SIGNIN)(?:$|_)", re.I)
_SHAPES = [                                   # keys and tokens by their look, wherever they appear
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),                          # Anthropic, OpenAI, OpenRouter
    re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"),                         # Google / Gemini
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"),                   # Slack webhooks / tokens
    re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]*"),   # JWTs (Nexar access tokens)
    re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"),   # UUID keys (Mouser)
    re.compile(r"\b[0-9a-fA-F]{32,}\b"),                            # hex client ids and secrets
]
_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.\-]{0,15}://)[^/\s:@]{1,200}:[^/\s@]{1,500}@")
_BEARER = re.compile(r"(?i)\b(bearer|basic|token)\s+[A-Za-z0-9._~+/=\-]{8,}")
_SECRET_WORDS = r"(?:api[_-]?key|apikey|key|token|secret|password|passwd|pass|sig|client[_-]?id)"
_QUOTED = re.compile(r"(?i)((?<![A-Za-z0-9])[\w-]{0,40}" + _SECRET_WORDS + r"[\"']?\s*[=:]\s*)"   # a quoted value, spaces
                     r"(\"(?:\\.|[^\"\\\n]){1,500}\"|'(?:\\.|[^'\\\n]){1,500}')")                   # and \" inside it too
_QUERY = re.compile(r"(?i)((?<![A-Za-z0-9])[\w-]{0,40}" + _SECRET_WORDS +
                    r"[\"']?\s*[=:]\s*[\"']?)(?!\[secret\])([^&\s\"',;)}\]]+)")
_LONG = re.compile(r"[A-Za-z0-9_\-]{40,}")
_EMAIL = re.compile(r"(?<![\w.+\-])[\w.+\-]{1,64}@[\w\-]{1,63}(?:\.[\w\-]{1,63})+")   # bounded: linear on long runs
_ORG = re.compile(r"OneDrive - [^\\/\"'\n]{1,100}")                # the employer in a synced folder's name
_A365 = re.compile(r"(?i)\b[a-z0-9][a-z0-9\-]{0,62}(?=\.365\.altium\.com\b)")   # the company in its Altium 365 address
_USER_DIRS = re.compile(r"(?i)([A-Za-z]:[\\/]+(?:Users|Documents and Settings)[\\/]+|/home/|/Users/)([^\\/\"'\n<>|]+)")


_COMMON_USERS = {"root", "user", "admin", "administrator", "runner", "owner", "guest", "ubuntu", "pi", "test"}
# account names that are also ordinary words in code (ws.root): blanked only inside home-folder paths


def crash_dir(environ=None):
    from .userconfig import config_dir
    return config_dir(environ) / "crashes"


def _known_secrets():
    """Values of the keys ecadlib can see (stored keys, environment variables named like a key): blanked by value."""
    vals = {v for k, v in os.environ.items() if SECRET_NAME.search(k) and v}
    try:
        from . import userconfig as U
        for name in U.stored_names():
            v = U.get_key(name)
            if v:
                vals.add(v)
                vals.update(_json_strings(v))     # NEXAR_SIGNIN: a JSON blob; its tokens on their own too
    except Exception:                         # a broken key store must not stop a bug report
        pass
    vals |= {quote(v, safe="") for v in vals}  # the same value inside a URL
    return sorted((v for v in vals if len(v) >= 8), key=len, reverse=True)


def _json_strings(v):
    try:
        data = json.loads(v)
    except (ValueError, TypeError):
        return set()
    out, todo = set(), [data]
    while todo:
        x = todo.pop()
        if isinstance(x, dict):
            todo += list(x.values())
        elif isinstance(x, list):
            todo += x
        elif isinstance(x, str):
            out.add(x)
    return out


def _long_token(m):
    s = m.group(0)
    return "[secret]" if re.search(r"\d", s) and re.search(r"[A-Za-z]", s) else s


def scrub(text):
    """text with API keys, tokens, e-mail addresses, the home folder and user names blanked out."""
    text = str(text)
    for v in _known_secrets():
        text = text.replace(v, "[secret]")
    for rx in _SHAPES:
        text = rx.sub("[secret]", text)
    text = _USERINFO.sub(lambda m: m.group(1) + "[user]:[secret]@", text)
    text = _BEARER.sub(lambda m: m.group(1) + " [secret]", text)
    text = _QUOTED.sub(lambda m: m.group(1) + m.group(2)[0] + "[secret]" + m.group(2)[0], text)   # 'a pass phrase'
    text = _QUERY.sub(lambda m: m.group(1) + "[secret]", text)
    text = _LONG.sub(_long_token, text)
    text = _EMAIL.sub("[email]", text)
    home = str(Path.home())
    if len(home) > 3:
        for h in {home, home.replace("\\", "/"), home.replace("/", "\\")}:
            text = re.sub(re.escape(h) + r"(?=[\\/\"'\s:]|$)", "~", text, flags=re.I if os.name == "nt" else 0)
    text = _USER_DIRS.sub(lambda m: m.group(1) + "[user]", text)
    text = _ORG.sub("OneDrive - [org]", text)
    text = _A365.sub("[workspace]", text)
    try:
        node = platform.node()
    except Exception:
        node = ""
    if len(node) >= 3:                        # the computer's name (DESKTOP-5JE5S2P, JANES-MBP)
        text = re.sub(r"(?i)(?<![A-Za-z0-9])" + re.escape(node) + r"(?![A-Za-z0-9])", "[computer]", text)
    try:
        user = getpass.getuser()
    except Exception:
        user = ""
    if len(user) >= 3 and user.lower() not in _COMMON_USERS:
        text = re.sub(r"(?i)(?<![A-Za-z0-9])" + re.escape(user) + r"(?![A-Za-z0-9])", "[user]", text)
    return text


def _short_paths(text):
    """Python's and ecadlib's own folders as <python> / ecadlib/: the frames stay readable, the install path goes."""
    import sysconfig
    pkg = str(Path(__file__).resolve().parent)
    roots = [(pkg, "ecadlib")] + [(p, "<python>") for p in {sysconfig.get_paths().get(k) for k in ("purelib", "platlib", "stdlib")} if p]
    for p, short in sorted(roots, key=lambda r: len(r[0]), reverse=True):
        for v in {p, p.replace("\\", "/")}:
            text = text.replace(v, short)
    return text


def _installed():
    return "Windows app" if getattr(sys, "frozen", False) else "pip"


_SEQ = itertools.count()
_PKG = Path(__file__).resolve().parent


def _own_frames(exc):
    """The lines in ecadlib's own code that exc went through, as "ecadlib/x.py:line:function" lines (oldest first).
    Taken from the traceback object itself, never from text: an error message cannot pose as a frame."""
    out = []
    try:
        for fr in traceback.extract_tb(exc.__traceback__):
            try:
                rel = Path(fr.filename).resolve().relative_to(_PKG.parent).as_posix()
            except (ValueError, OSError):
                rel = fr.filename.replace("\\", "/")              # a frozen app: ecadlib\\ui\\server.py
            if rel.startswith("ecadlib/") and rel.endswith(".py"):
                out.append(f"{rel}:{fr.lineno or 0}:{fr.name}")
    except Exception:
        pass
    return "\n".join(out[-30:])


def record(where, exc, wait=False):
    """Save exc (an error ecadlib did not expect) for a later bug report; -> the file, or None. Never raises.
    The same error again (a page polling a broken endpoint) is not saved twice in a row. When the person switched
    automatic reports on, the publishable part is also sent (a background thread; wait: block until it is done)."""
    try:
        from . import __version__
        from .userconfig import _write_private
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).splitlines()[-MAX_TB_LINES:]
        tb = [ln[:MAX_LINE] for ln in tb[:-1]] + [ln[:MAX_LAST] for ln in tb[-1:]]
        rec = {"time": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()), "version": __version__,
               "os": platform.platform(terse=True), "python": platform.python_version(), "installed": _installed(),
               "where": re.sub(r"[`\r\n]", " ", scrub(str(where)[:200])), "error": type(exc).__name__,
               "message": scrub(_short_paths(str(exc)[:8000]))[:2000],
               "traceback": scrub(_short_paths("\n".join(tb))), "frames": _own_frames(exc)}
        last = latest()
        if last and all(last.get(k) == rec[k] for k in ("where", "error", "message")):
            return _files()[0]
        d = crash_dir()
        d.mkdir(mode=0o700, parents=True, exist_ok=True)
        f = d / f"{time.time_ns()}-{os.getpid()}-{next(_SEQ)}.json"
        _write_private(f, json.dumps(rec, indent=1))     # mode 600: other accounts on the PC cannot read it
        for old in _files()[MAX_KEPT:]:
            old.unlink(missing_ok=True)
        if auto_on():
            _send_in_background(rec, f, wait)
        return f
    except Exception:
        return None


def _files():
    try:
        return sorted(crash_dir().glob("*.json"), key=lambda p: p.name, reverse=True)   # newest first
    except OSError:
        return []


def saved():
    out = []
    for f in _files():
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(r, dict):                   # a damaged or hand-edited file: text fields as text, never a crash
            out.append({k: v if isinstance(v, str) else "" if v is None else str(v) for k, v in r.items()})
    return out


def latest():
    s = saved()
    return s[0] if s else None


def clear():
    for f in _files():
        f.unlink(missing_ok=True)


def _system():
    from . import __version__
    return {"version": __version__, "os": platform.platform(terse=True), "python": platform.python_version(),
            "installed": _installed()}


def build(rec=None, tb_lines=None):
    """(title, Markdown body) of a GitHub issue for rec (a saved error), or a blank report when rec is None."""
    info = rec or _system()
    if rec:
        msg = rec.get("message", "").splitlines()[0] if rec.get("message") else ""
        title = f"{rec.get('where')}: {rec.get('error')}: {msg}"[:120]
    else:
        title = "Bug: "
    lines = ["**What happened**", "", "<!-- What were you doing? e.g. checked a BOM from Altium, filled the library "
             "for 12 parts. What did you expect instead? -->", "", "",
             "**Details** (filled in by ecadlib; keys, e-mail addresses, your user name and home folder are blanked "
             "out. Please check before you submit.)", "",
             f"- ecadlib version: {info.get('version')} ({info.get('installed', '?')})",
             f"- OS: {info.get('os')}", f"- Python: {info.get('python')}"]
    if rec:
        tb = [ln[:MAX_BODY_LINE] for ln in rec.get("traceback", "").splitlines()]
        if tb_lines is not None and tb_lines < len(tb):          # the newest frames and the error line are kept
            tb = ["..."] + tb[len(tb) - max(tb_lines, 1):]
        fence = "`" * max([3] + [len(r) + 1 for r in re.findall(r"`{3,}", "\n".join(tb))])   # a ``` in the error
        lines += [f"- Where: `{rec.get('where')}`", f"- When: {rec.get('time')}", "",   # does not end the block
                  fence, *tb, fence]
    return title, "\n".join(lines) + "\n"


def issue_url(title, body, rec=None):
    """The new-issue link, pre-filled. Too long for GitHub: the traceback's oldest lines are dropped first (the error
    line itself is always kept), then, if still too long, the body is cut and its code block closed again."""
    def url(t, b):
        return ISSUES_NEW + "?" + urlencode({"title": t, "labels": "bug", "body": b}, quote_via=quote)
    u = url(title, body)
    if len(u) <= MAX_URL:
        return u
    if rec:                                   # binary search: the most traceback lines that fit
        lo, hi = 1, len(rec.get("traceback", "").splitlines())
        best = url(title, build(rec, 1)[1])
        while lo <= hi:
            mid = (lo + hi) // 2
            cand = url(title, build(rec, mid)[1])
            if len(cand) <= MAX_URL:
                best, lo = cand, mid + 1
            else:
                hi = mid - 1
        if len(best) <= MAX_URL:
            return best
        body = build(rec, 1)[1]
    fence = re.search(r"`{3,}$", body.rstrip())
    fence = fence.group(0) if fence else ""
    core = body.rstrip()[:-len(fence)] if fence else body
    while len(u) > MAX_URL and core:          # a huge message on its own: cut it, keep the fence closed
        core = core[: int(len(core) * 0.8)]
        u = url(title, core + (f"\n...\n{fence}\n" if fence else "\n..."))
    return u


def report_for(rec=None):
    """{title, body, url} of the GitHub issue for rec (the newest saved error when rec is None)."""
    rec = latest() if rec is None else rec
    title, body = build(rec)
    u = issue_url(title, body, rec)
    return {"title": title, "body": body, "url": u, "saved": bool(rec), "when": (rec or {}).get("time")}


# ------------------------------------------------------------------ automatic reports (opt-in)
# The endpoint (api/crash-report.js on the project's site) files each crash as a public GitHub issue, one issue per
# distinct crash. Only the publishable part goes: the error's type, the lines in ecadlib's own code it went through,
# where it happened and the versions. The message never leaves the computer: it can hold a project or part name.
REPORT_URL = "https://pcblivinglibrary.vercel.app/api/crash-report"
URL_ENV, OFF_ENV, SETTING = "ECADLIB_REPORT_URL", "ECADLIB_NO_CRASH_REPORTS", "crash_reports"
MAX_SENT_PER_DAY = 10                          # per user account (its config folder)
SCHEMA = 1
SEND_DEADLINE = 1.5                            # seconds a finished command may wait for a send still under way
_FRAME_LINE = re.compile(r"^(ecadlib/[\w/.\-]{1,120}\.py):(\d{1,6}):([\w<>.]{1,80})$", re.ASCII)
_PLACES = ("MCP server", "Altium / KiCad menu")
_SENT_LOCK = threading.Lock()
_PENDING = []


def report_url():
    return os.environ.get(URL_ENV, REPORT_URL).strip()


def available():
    """Automatic reports are possible in this build and on this computer (an address; not switched off by env)."""
    off = any(os.environ.get(k, "").strip() not in ("", "0", "false", "no") for k in (OFF_ENV, "DO_NOT_TRACK"))
    return bool(report_url()) and not off


def auto_choice():
    """True / False as the person answered, None when never asked."""
    try:
        from .userconfig import load_settings
        v = load_settings().get(SETTING)
    except Exception:
        return None
    return {"on": True, "off": False}.get(v)


def set_auto(on):
    from .userconfig import save_settings
    save_settings({SETTING: "on" if on else "off"})


def auto_on():
    return available() and auto_choice() is True


def _place(where):
    """Where it happened, as one of the fixed values the endpoint accepts: a command name, never a URL or a path."""
    w = str(where or "")
    if w in _PLACES:
        return w
    if w.startswith("web UI job"):
        return "web UI job"
    if w.startswith("web UI"):
        return "web UI"
    try:
        from .cli import COMMANDS
        if w in COMMANDS:
            return w
    except Exception:
        pass
    return "command line"


def _match(rx, v, other):
    m = re.match(rx, str(v or ""), re.ASCII)
    return m.group(0) if m else other


def payload(rec):
    """The publishable part of a saved report: no message, only frames in ecadlib's own files, fixed-shape fields.
    A report saved before frames were kept (no "frames" field) goes without frames: text is never parsed for them."""
    frames = []
    for ln in (rec.get("frames") or "").splitlines():
        m = _FRAME_LINE.match(ln.strip())
        if m:
            frames.append({"file": m.group(1), "line": max(1, int(m.group(2))), "function": m.group(3)})
    osm = re.match(r"^(Windows|Linux|macOS|Darwin|FreeBSD)-([0-9A-Za-z._-]{1,50})", str(rec.get("os") or ""), re.ASCII)
    return {"schema": SCHEMA, "version": _match(r"\d{1,3}\.\d{1,3}\.\d{1,3}((a|b|rc)\d{1,3})?", rec.get("version"), "0.0.0"),
            "installed": "Windows app" if rec.get("installed") == "Windows app" else "pip",
            "os": f"{osm.group(1)}-{osm.group(2)}" if osm else "Other-unknown",
            "python": _match(r"3\.\d{1,2}\.\d{1,3}((a|b|rc)\d{1,2})?", rec.get("python"), "3.0.0"),
            "where": _place(rec.get("where")), "error": _match(r"[A-Z][A-Za-z0-9]{1,40}$", rec.get("error"), "Exception"),
            "frames": frames[-30:]}


def _sent_today():
    """How many reports went today; counts this one (the daily cap, kept in crashes/sent.txt)."""
    from .userconfig import _write_private
    f = crash_dir() / "sent.txt"
    day = time.strftime("%Y-%m-%d", time.gmtime())
    with _SENT_LOCK:                                # the web UI can save two errors at once
        try:
            d, n = f.read_text(encoding="utf-8").split()
            n = int(n) if d == day else 0
        except (OSError, ValueError):
            n = 0
        _write_private(f, f"{day} {n + 1}\n")
    return n + 1


def send(rec, path=None, timeout=5):
    """Send rec's publishable part; mark its file as sent. -> True when the endpoint took it. Never raises."""
    try:
        if not auto_on() or _sent_today() > MAX_SENT_PER_DAY:
            return False
        req = urllib.request.Request(report_url(), data=json.dumps(payload(rec)).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": "ecadlib-crash-report"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ok = 200 <= r.status < 300
        if ok and path and Path(path).exists():     # cleared (or pruned) meanwhile: stays gone
            from .userconfig import _write_private
            _write_private(path, json.dumps(dict(rec, sent="yes"), indent=1))
        return ok
    except Exception:
        return False


def _finish_sends():
    """At exit: give sends still under way SEND_DEADLINE seconds in all, then let the program end."""
    end = time.monotonic() + SEND_DEADLINE
    for th in list(_PENDING):
        th.join(max(0.0, end - time.monotonic()))


def _send_in_background(rec, path, wait=False):
    """A daemon thread, so a dead or slow server never keeps a finished command from exiting for long: at exit the
    send gets SEND_DEADLINE seconds (atexit), then is dropped. wait: block until it is done (the tests)."""
    th = threading.Thread(target=send, args=(rec, path), daemon=True, name="ecadlib-crash-report")
    if not _PENDING:
        import atexit
        atexit.register(_finish_sends)
    _PENDING[:] = [t for t in _PENDING if t.is_alive()] + [th]
    th.start()
    if wait:
        th.join()


def _interactive(out):
    """A person at a terminal will see the question and can answer it: stdin and `out` are terminals, not CI."""
    if os.environ.get("CI", "").strip() not in ("", "0", "false"):
        return False
    try:
        return bool(sys.stdin and sys.stdin.isatty() and out.isatty())
    except Exception:
        return False


def ask_once(out, inp=None):
    """After a crash in a terminal: ask once whether to send reports automatically; a yes also sends this one.
    The question goes to `out` (stderr from the CLI, so `--json > file` stays clean). Ctrl-C or end of input counts
    as no answer: nothing is saved and it is asked again after the next crash."""
    try:
        if not available() or auto_choice() is not None:
            return
        if inp is None:
            if not _interactive(out):
                return

            def inp(prompt):
                print(prompt, end="", file=out, flush=True)
                line = sys.stdin.readline()
                if not line:
                    raise EOFError
                return line
        print("\necadlib can send reports like this one to its developer automatically. They become public GitHub "
              "issues, so only the error's type, the lines in ecadlib's own code and the versions go: never the "
              "message, your files or your keys. `ecadlib report-bug --auto off` stops it.", file=out)
        try:
            answer = str(inp("Send crash reports automatically from now on? [y/N] "))
        except (KeyboardInterrupt, EOFError):
            print("", file=out)
            return
        yes = answer.strip().lower() in ("y", "yes")
        set_auto(yes)
        print("Crash reports: on, thank you." if yes else "Crash reports: off.", file=out)
        files, rec = _files(), latest()
        if yes and rec and files:
            send(rec, files[0])
    except Exception:
        pass


def _open(url):
    webbrowser.open(url)


def main(argv=None, out=None, inp=None):
    out = out or sys.stdout
    ap = argparse.ArgumentParser(prog="ecadlib report-bug",
                                 description="Report a bug: shows the last error ecadlib saved on this computer and "
                                             "opens a pre-filled GitHub issue you read before sending.")
    ap.add_argument("--print", action="store_true", help="only print the report and the link; open nothing")
    ap.add_argument("--yes", "-y", action="store_true", help="open GitHub without asking")
    ap.add_argument("--list", action="store_true", help="list the saved errors, newest first")
    ap.add_argument("--clear", action="store_true", help="delete the saved errors")
    ap.add_argument("--auto", choices=("on", "off", "status"),
                    help="automatic crash reports (public GitHub issues, without the message): on, off, or show")
    a = ap.parse_args(argv)
    if a.auto:
        if a.auto != "status":
            set_auto(a.auto == "on")
        c = auto_choice()
        state = "on" if c else "off" if c is False else "off (never asked)"
        print(f"Automatic crash reports: {state}" + ("" if available() else " (switched off on this computer)"), file=out)
        return 0
    if a.clear:
        clear()
        print("Saved errors deleted.", file=out)
        return 0
    if a.list:
        recs = saved()
        if not recs:
            print("No saved errors.", file=out)
        for r in recs:
            msg = ((r.get("message") or "").splitlines() or [""])[0]
            print(f"{r.get('time')}  {r.get('where')}: {r.get('error')}: {msg}", file=out)
        print(f"Folder: {crash_dir()}", file=out)
        return 0
    r = report_for()
    print("No saved error: the report below is blank, describe what went wrong on GitHub." if not r["saved"] else
          f"The last error ecadlib saved ({r['when']}):", file=out)
    print("", file=out)
    print(r["title"], file=out)
    print(r["body"], file=out)
    print("Nothing has been sent yet. Opening the link passes this text to GitHub so it can show it to you; there you "
          "can edit it, and it becomes a public issue only when you press Submit (a free GitHub account is needed).",
          file=out)
    print(f"Link: {r['url']}", file=out)
    if a.print:
        return 0
    if not a.yes:
        if inp is None:
            if not sys.stdin or not sys.stdin.isatty():
                return 0
            inp = input
        if inp("Open GitHub in your browser now? [Y/n] ").strip().lower() not in ("", "y", "yes"):
            return 0
    _open(r["url"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
