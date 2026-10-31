"""Bug reports the user sends themselves: ecadlib sends nothing on its own.

    ecadlib report-bug              show the last error ecadlib saved, then open a pre-filled GitHub issue
    ecadlib report-bug --print      only show the text and the link
    ecadlib report-bug --list       the saved errors, newest first
    ecadlib report-bug --clear      delete the saved errors

When a command, the web UI or the MCP server hits an error it did not expect (a bug, not a wrong path or a bad
setting), record() saves it in the config folder (crashes/, the newest MAX_KEPT) with API keys, tokens, e-mail
addresses, the user name, the company in an Altium 365 address and the home folder blanked out. report-bug turns
the newest one into a GitHub issue link; the user reads the text on GitHub and decides whether to press Submit."""
import argparse, getpass, itertools, json, os, platform, re, sys, time, traceback, webbrowser
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


def record(where, exc):
    """Save exc (an error ecadlib did not expect) for a later bug report; -> the file, or None. Never raises.
    The same error again (a page polling a broken endpoint) is not saved twice in a row."""
    try:
        from . import __version__
        from .userconfig import _write_private
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).splitlines()[-MAX_TB_LINES:]
        tb = [ln[:MAX_LINE] for ln in tb[:-1]] + [ln[:MAX_LAST] for ln in tb[-1:]]
        rec = {"time": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()), "version": __version__,
               "os": platform.platform(terse=True), "python": platform.python_version(), "installed": _installed(),
               "where": re.sub(r"[`\r\n]", " ", scrub(str(where)[:200])), "error": type(exc).__name__,
               "message": scrub(_short_paths(str(exc)[:8000]))[:2000],
               "traceback": scrub(_short_paths("\n".join(tb)))}
        last = latest()
        if last and all(last.get(k) == rec[k] for k in ("where", "error", "message")):
            return _files()[0]
        d = crash_dir()
        d.mkdir(mode=0o700, parents=True, exist_ok=True)
        f = d / f"{time.time_ns()}-{os.getpid()}-{next(_SEQ)}.json"
        _write_private(f, json.dumps(rec, indent=1))     # mode 600: other accounts on the PC cannot read it
        for old in _files()[MAX_KEPT:]:
            old.unlink(missing_ok=True)
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
        if isinstance(r, dict):
            out.append(r)
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
        lines += [f"- Where: `{rec.get('where')}`", f"- When: {rec.get('time')}", "",
                  "```", *tb, "```"]
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
    fenced = body.rstrip().endswith("```")
    core = body.rstrip()[:-3] if fenced else body
    while len(u) > MAX_URL and core:          # a huge message on its own: cut it, keep the fence closed
        core = core[: int(len(core) * 0.8)]
        u = url(title, core + ("\n...\n```\n" if fenced else "\n..."))
    return u


def report_for(rec=None):
    """{title, body, url} of the GitHub issue for rec (the newest saved error when rec is None)."""
    rec = latest() if rec is None else rec
    title, body = build(rec)
    u = issue_url(title, body, rec)
    return {"title": title, "body": body, "url": u, "saved": bool(rec), "when": (rec or {}).get("time")}


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
    a = ap.parse_args(argv)
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
