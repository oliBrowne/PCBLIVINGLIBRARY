"""Terminal output for people: one small layer every command prints through (stdlib only).

    from ecadlib import term
    t = term.Term()                       # stdout; t.err is the same for stderr
    t.table([[t.status("ready"), "SOT-23", "AO3401A"], ...])
    with t.progress(14, "parts") as p:    # "[3/14] SOT-23  ✓ ready" on one live line on a terminal
        p.done("SOT-23", "ready")
    with term.waiting("DigiKey"):          # "… waiting on DigiKey  4 s" while a slow call runs
        ...
    t.summary("6 ready, 1 held", written=[("ws/kicad/", "KiCad library")], next=[("ecadlib review PLAN", "held parts")])

Rules (docs/ui-voice.md):
- colour and symbols only when the stream is a terminal; never with NO_COLOR, --json, in CI logs (CI, GITHUB_ACTIONS,
  GITLAB_CI, ...) or TERM=dumb. ECADLIB_COLOR=always|never overrides (tests, `less -R`).
- one status vocabulary: ✓ ready, ! held, ○ waiting for sign-off, … working, ✗ failed, - skipped. Plain mode prints the
  word only, so piped output and logs stay greppable ("ready", "held").
- tables fit the terminal width (COLUMNS, else the terminal; no limit when piped): the last column wraps under itself.
- verbosity: -q (only the outcome and errors), normal, -v (every detail). cli.main sets it from the flags.
- live lines (progress, waiting) go to stderr and only on a terminal; piped, progress prints one plain line per part
  (none with -q) and a long wait prints "still waiting on X (30 s)" now and then.
"""
import os, re, shutil, sys, textwrap, threading, time

VERBOSITY = 0            # -1 quiet, 0 normal, 1 verbose (set by ecadlib.cli from -q / -v)
JSON = False             # --json on the command line: never style anything
CI_VARS = ("CI", "GITHUB_ACTIONS", "GITLAB_CI", "BUILDKITE", "TF_BUILD", "JENKINS_URL", "TEAMCITY_VERSION")

# status -> (glyph, ascii glyph, colour, word)
STATUS = {
    "ready": ("✓", "+", "green", "ready"),
    "held": ("!", "!", "yellow", "held"),
    "signoff": ("○", "o", "cyan", "waiting for sign-off"),
    "working": ("…", "~", "blue", "working"),
    "failed": ("✗", "x", "red", "failed"),
    "skipped": ("-", "-", "dim", "skipped"),
    "ok": ("✓", "+", "green", "ok"),
    "warn": ("!", "!", "yellow", "warning"),
    "info": ("·", "-", "dim", "note"),
}
ALIASES = {"pass": "ready", "allowed": "ready", "match": "ready", "done": "ready", "added": "ready", "updated": "ready",
           "awaiting_approval": "signoff", "sign-off": "signoff", "waiting": "signoff", "review": "signoff",
           "mismatch": "held", "fail": "failed", "error": "failed", "skip": "skipped", "unchanged": "skipped",
           "warning": "warn", "WARN": "warn", "OK": "ok", "FAIL": "failed", "open": "held", "building": "working"}
COLORS = {"green": "32", "yellow": "33", "red": "31", "blue": "34", "cyan": "36", "dim": "2", "bold": "1"}
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def visible_len(s):
    return len(_ANSI.sub("", str(s)))


def wants_style(stream, environ=None):
    """True when `stream` should get colour and glyphs."""
    env = os.environ if environ is None else environ
    force = (env.get("ECADLIB_COLOR") or "").strip().lower()
    if JSON or env.get("NO_COLOR", "") != "" or force in ("never", "no", "0", "off"):
        return False
    if force in ("always", "yes", "1", "on"):
        return True
    if any(env.get(v) for v in CI_VARS) or env.get("TERM") == "dumb":
        return False
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        return False


def _can_encode(stream, text="✓…○✗·"):
    try:
        text.encode(getattr(stream, "encoding", None) or "utf-8")
        return True
    except (UnicodeEncodeError, LookupError):
        return False


def _print_safe(text, stream):
    """print(), but a character the stream's encoding cannot hold (a part value's "Ω" on a cp1252 pipe on Windows)
    becomes "?" instead of a UnicodeEncodeError that stops the command."""
    try:
        print(text, file=stream)
    except UnicodeEncodeError:
        enc = getattr(stream, "encoding", None) or "utf-8"
        print(str(text).encode(enc, "replace").decode(enc, "replace"), file=stream)


def term_width(stream, environ=None):
    """Columns to fit: $COLUMNS, else the terminal's width; None (no limit) when piped."""
    env = os.environ if environ is None else environ
    try:
        c = int(env.get("COLUMNS", ""))
        if c > 0:
            return c
    except ValueError:
        pass
    try:
        if stream.isatty():
            return shutil.get_terminal_size((100, 24)).columns
    except (AttributeError, ValueError, OSError):
        pass
    return None


def _wrap(text, width):
    """textwrap.wrap that never cuts a word (a path, a URL, an MPN) in two: a long one gets a line of its own, wider
    than the terminal, and stays whole to copy."""
    return textwrap.wrap(text, width, break_long_words=False, break_on_hyphens=False)


class Term:
    """Writes to one stream. styled / width / verbosity: None = detect (see wants_style, term_width, VERBOSITY)."""

    def __init__(self, stream=None, styled=None, width=0, verbosity=None, environ=None):
        self.stream = stream if stream is not None else sys.stdout
        self.styled = wants_style(self.stream, environ) if styled is None else styled
        self.unicode = self.styled and _can_encode(self.stream)
        self.width = term_width(self.stream, environ) if width == 0 else width
        self._verbosity = verbosity
        self.lock = threading.RLock()
        self.live = ""                      # the text of a live line on screen (progress / waiting), if any

    @property
    def verbosity(self):
        return VERBOSITY if self._verbosity is None else self._verbosity

    @property
    def quiet(self):
        return self.verbosity < 0

    @property
    def verbose(self):
        return self.verbosity > 0

    @property
    def err(self):
        """A Term on stderr with the same verbosity (live lines, warnings, errors)."""
        if self.stream is sys.stderr:
            return self
        return Term(sys.stderr, verbosity=self._verbosity)

    # ------------------------------------------------------------ words
    def color(self, text, name):
        if not self.styled or not name:
            return str(text)
        return "".join(f"\x1b[{COLORS[n]}m" for n in name.split("+")) + str(text) + "\x1b[0m"

    def bold(self, text):
        return self.color(text, "bold")

    def dim(self, text):
        return self.color(text, "dim")

    def glyph(self, status):
        key = ALIASES.get(status, status)
        g, a, c, _ = STATUS.get(key, STATUS["info"])
        return self.color(g if self.unicode else a, c) if self.styled else ""

    def status(self, status, word=None):
        """A status cell: "✓ ready" on a terminal, "ready" in plain mode. word: say it differently ("match")."""
        key = ALIASES.get(status, status)
        g, a, c, w = STATUS.get(key, ("·", "-", "dim", str(status).replace("_", " ")))
        w = word or w
        if not self.styled:
            return w
        return self.color(f"{g if self.unicode else a} {w}", c)

    # ------------------------------------------------------------ lines
    def write(self, text=""):
        with self.lock:
            self._clear_live()
            _print_safe(text, self.stream)
            self._redraw_live()

    def line(self, text="", level=0):
        """Print when the verbosity allows: level -1 always (outcome, errors), 0 normal, 1 only with -v."""
        if self.verbosity >= level:
            self.write(text)

    def detail(self, text):
        self.line(text, 1)

    def heading(self, text):
        self.line(self.bold(text))

    def note(self, text, fix=None, level=0):
        """A finding: what happened, then how to fix it on the next line (the shared voice)."""
        self.line(f"{self.glyph('warn') + ' ' if self.styled else ''}{text}", level)
        if fix:
            self.line(f"  {self.dim('fix:') if self.styled else 'fix:'} {fix}", level)

    def error(self, cmd, text, fix=None):
        """One line saying what went wrong, one saying how to fix it (always printed, on this stream)."""
        lead = self.color("✗" if self.unicode else "x", "red") + " " if self.styled else ""
        self.write(f"{lead}ecadlib {cmd}: {text}")
        if fix:
            self.write(f"  {'fix:' if not self.styled else self.dim('fix:')} {fix}")

    # ------------------------------------------------------------ tables
    def table(self, rows, headers=None, indent=2, gap=2, level=0, right=()):
        """Aligned columns; the last column wraps to fit the width. rows: lists of cells (str; styled cells OK).
        right: column numbers to right-align (counts)."""
        if self.verbosity < level:
            return
        for line in self.table_lines(rows, headers, indent, gap, right):
            self.write(line)

    def table_lines(self, rows, headers=None, indent=2, gap=2, right=()):
        rows = [[("" if c is None else str(c)) for c in r] for r in rows]
        if headers:
            rows = [[self.dim(h) if self.styled else h for h in headers]] + rows
        if not rows:
            return []
        n = max(len(r) for r in rows)
        rows = [r + [""] * (n - len(r)) for r in rows]
        widths = [max(visible_len(r[i]) for r in rows) for i in range(n - 1)] + [0]
        pad = " " * indent
        out = []
        lead_w = indent + sum(w + gap for w in widths[:-1])
        avail = (self.width - lead_w) if self.width else None
        wrap_under = avail is None or avail >= 24
        for r in rows:
            cells = []
            for i, c in enumerate(r[:-1]):
                fill = " " * (widths[i] - visible_len(c))
                cells.append(fill + c if i in right else c + fill)
            head = pad + (" " * gap).join(cells) + (" " * gap if n > 1 else "")
            last = r[-1]
            if not last:
                out.append(head.rstrip())
            elif avail is None or visible_len(last) <= avail:
                out.append(head + last)
            elif wrap_under:
                parts = _wrap(_ANSI.sub("", last), avail) or [""]
                out.append(head + parts[0])
                out += [" " * lead_w + p for p in parts[1:]]
            else:                                   # very narrow: the last column on its own lines below
                out.append(head.rstrip())
                w = max(20, (self.width or 80) - indent - 4)
                out += [pad + "    " + p for p in _wrap(_ANSI.sub("", last), w)]
        return out

    def wrap(self, text, indent=2, level=0):
        """A paragraph wrapped to the width, indented."""
        if self.verbosity < level:
            return
        w = (self.width or 10 ** 6) - indent
        for p in _wrap(str(text), max(20, w)) or [""]:
            self.write(" " * indent + p)

    # ------------------------------------------------------------ the end of a command
    def summary(self, headline, written=(), next=(), notes=()):
        """The final block: the outcome (always, even with -q), what was written where, notes (text, fix) and the
        next 1-3 commands. written: [(path, what)]; next: [(command, why)] or [command]."""
        self.write("")
        for h in (textwrap.wrap(headline, self.width) if self.width and len(headline) > self.width else [headline]):
            self.write(self.bold(h))
        if self.quiet:
            return
        for text, fix in notes:
            self.note(text, fix)
        if written:
            self.write("written:")
            self.table([[p, w] for p, w in written])
        nxt = [(n, "") if isinstance(n, str) else n for n in next][:3]
        if nxt:
            self.write("next:")
            self.table([[c, f"# {w}" if w else ""] for c, w in nxt])

    # ------------------------------------------------------------ live lines (stderr, terminals only)
    def _clear_live(self):
        if self.live and self.styled:
            self.stream.write("\r\x1b[2K")
            self.stream.flush()

    def _redraw_live(self):
        if self.live and self.styled:
            self.stream.write(self.live)
            self.stream.flush()

    def set_live(self, text):
        """Replace the live line (terminal only; does nothing when piped)."""
        if not self.styled:
            return
        with self.lock:
            if self.width and visible_len(text) > self.width - 1:
                text = _ANSI.sub("", text)[: self.width - 2] + ("…" if self.unicode else ".")
            self._clear_live()
            self.live = text
            self._redraw_live()

    def clear_live(self):
        with self.lock:
            self._clear_live()
            self.live = ""

    def progress(self, total, what="parts"):
        return Progress(self, total, what)


class Progress:
    """[n/total] name  status - one live line on a terminal, a plain line per finished item when piped."""

    def __init__(self, t, total, what="parts"):
        self.t, self.total, self.what, self.n, self.t0 = t, total, what, 0, time.monotonic()
        self.counts = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.t.clear_live()
        return False

    def _tag(self, n=None):
        k = len(str(self.total))
        return f"[{str(n if n is not None else self.n).rjust(k)}/{self.total}]"

    def start(self, name):
        """An item begins (live line only)."""
        self.current = name
        self.t.set_live(f"{self._tag(self.n + 1)} {name}  {self.t.status('working')}")

    def done(self, name, status, detail=""):
        """An item finished with status (ready / held / skipped / failed / ...)."""
        self.n += 1
        key = ALIASES.get(status, status)
        self.counts[key] = self.counts.get(key, 0) + 1
        text = f"{self._tag()} {name}  {self.t.status(status)}" + (f"  {detail}" if detail else "")
        if self.t.styled:
            self.t.set_live(text)
        elif not self.t.quiet:
            self.t.write(text)

    def elapsed(self):
        return time.monotonic() - self.t0


def working(what, t=None):
    """with working("rebuilding into a temporary copy"): like waiting(), for a slow local step."""
    return waiting(what, t, verb="working:")


def fmt_seconds(s):
    return f"{s:.1f} s" if s < 10 else f"{int(s)} s" if s < 120 else f"{int(s // 60)} min {int(s % 60)} s"


_WAIT = threading.local()
HEARTBEAT = (10, 30, 60)                # plain mode: "still waiting" after these seconds, then every 60 s


class waiting:
    """with waiting("DigiKey"): a slow network or model call. On a terminal (stderr) a live line counts the
    seconds; piped, a "still waiting on X (30 s)" line after 10 s, 30 s, 60 s and then every minute. Nested waits
    show the innermost. Quiet mode shows nothing; --json never styles. Thread-safe enough for one CLI."""

    def __init__(self, what, t=None, verb="waiting on"):
        self.what, self.t, self.verb = what, t, verb

    def __enter__(self):
        self.t = self.t or Term(sys.stderr)
        self.t0 = time.monotonic()
        self.stop = threading.Event()
        self.prev = getattr(_WAIT, "live", None)
        _WAIT.live = self
        if not self.t.quiet and not JSON:
            self.th = threading.Thread(target=self._tick, daemon=True)
            self.th.start()
        else:
            self.th = None
        return self

    def _tick(self):
        beats = list(HEARTBEAT)
        while not self.stop.wait(0.5 if self.t.styled else 1.0):
            el = time.monotonic() - self.t0
            if getattr(_WAIT, "live", None) not in (self, None):
                continue                            # an inner wait is showing
            if self.t.styled:
                if el >= 1.0:
                    self.t.set_live(f"  {self.t.status('working', self.verb)} {self.what}  {fmt_seconds(el)}")
            elif el >= beats[0]:
                self.t.write(f"  still {self.verb} {self.what} ({fmt_seconds(el)})")
                beats = beats[1:] or [beats[0] + HEARTBEAT[-1]]

    def __exit__(self, *exc):
        self.stop.set()
        if self.th:
            self.th.join(timeout=1)
        if self.t.styled:
            self.t.clear_live()
        _WAIT.live = self.prev
        self.seconds = time.monotonic() - self.t0
        if self.seconds >= 5 and self.t.verbose:
            self.t.write(f"  {self.verb} {self.what}: {fmt_seconds(self.seconds)}")
        return False


SOURCES = {"gitlab.com": "the KiCad library (gitlab.com)", "api.anthropic.com": "Claude", "api.nexar.com": "Nexar / Octopart",
           "easyeda.com": "EasyEDA", "lcsc.com": "LCSC", "jlcpcb.com": "JLCPCB", "api.digikey.com": "DigiKey",
           "api.mouser.com": "Mouser", "github.com": "GitHub", "api.github.com": "GitHub"}


def source_name(host):
    """A network host -> what people call it ("gitlab.com" -> "the KiCad library (gitlab.com)")."""
    h = str(host or "").split(":")[0].lower()
    for k, v in SOURCES.items():
        if h == k or h.endswith("." + k):
            return v
    return h or "the network"


def set_from_argv(argv):
    """Strip -q/--quiet/-v/--verbose (anywhere before a bare --) and set VERBOSITY; note --json. Returns argv."""
    global VERBOSITY, JSON
    out, rest, v = [], False, 0
    JSON = False
    for a in argv:
        if a == "--":
            rest = True
        if not rest and a in ("-q", "--quiet"):
            v = -1
            continue
        if not rest and a in ("-v", "--verbose"):
            v = v + 1 if v >= 0 else v
            continue
        if not rest and a == "--json":
            JSON = True
        out.append(a)
    VERBOSITY = max(-1, min(2, v))
    return out


def explain(reason):
    """A raw hold reason -> (text, fix) in the shared voice (ecadlib.messages)."""
    from . import messages as M
    e = M.explain(reason)
    return e["text"] + (f" {e['note']}" if e.get("note") else ""), e["fix"]
