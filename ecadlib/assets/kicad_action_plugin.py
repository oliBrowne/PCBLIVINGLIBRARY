"""ecadlib in KiCad's PCB editor: an "ecadlib" button in the top toolbar and Tools > External Plugins entries
(ecadlib: add part, ecadlib: check project BOM, ecadlib: open review queue). Each opens the ecadlib window:

  * Add part from MPN: the part number (and the manufacturer, optional); ecadlib looks it up live, builds it into
    the project's ecadlib library and the footprint is placed in the middle of the board;
  * Check project BOM: the schematic's lines against the library (already in your library / a source can supply /
    needs a footprint), then "Fill the library now" builds the missing ones, or the report opens in the browser;
  * Open review queue: ecadlib's page, where held parts get a symbol or footprint picked.

Installed by KiCad's Plugin and Content Manager from the zip `ecadlib kicad-pcm --plugin` writes (or copied into
KiCad's scripting/plugins folder by the setup page's "Add to KiCad" button). It runs in KiCad's own Python, so it does
not import ecadlib: it starts the `ecadlib` command (ECADLIB_CMD if set, else the command in ecadlib_cmd.json next to
this file, else `ecadlib` on PATH, else a python3 that can import ecadlib) as
`ecadlib kicad-menu run-job JOB --action ... --kicad N` (N: this KiCad's major version; 7 gets KiCad 7 library files)
and shows JOB/progress.ini while it works and JOB/result.ini when done, or what went wrong (JOB/log.txt).
The window does not block KiCad: keep working while ecadlib looks parts up. Closed while a job works, the job still
finishes and lists the library in the project's tables; the next press of the ecadlib button offers to place a footprint
it built. When the setup page's copy and the Plugin and Content Manager's are both installed, only the first registers.

Per project, next to the board: ecadlib-work/ (ecadlib's workspace and the jobs) and ecadlib/ (the KiCad library,
added to the project's sym-lib-table and fp-lib-table as "ecadlib", ${KIPRJMOD}/ecadlib/...). A placed footprint gets
the next free reference of its part (R3, U2...) and the part's value. Held parts (failed checks) are not placed: the
window says why and the review queue fixes them."""
import datetime, json, os, re, shlex, shutil, subprocess, sys

import pcbnew

LIB = "ecadlib"
HERE = os.path.dirname(os.path.abspath(__file__))
NO_WINDOW = {"creationflags": 0x08000000} if os.name == "nt" else {}      # CREATE_NO_WINDOW: no console flashes up
ROWS = 7
WIDTH = 440

# the Altium dialog's colours (ecadlib/altium_forms.py), as RGB
BG, HEAD, TEXT, DIM, RULE = (0x2A, 0x2A, 0x2E), (0x21, 0x21, 0x24), (0xE8, 0xE8, 0xE8), (0x9C, 0x9C, 0x9C), (0x3D, 0x3D, 0x43)
BLUE, BLUE_OFF, GREEN, RED, BTN, FIELD = ((0x2F, 0x77, 0xD9), (0x4A, 0x5A, 0x70), (0x5C, 0xB8, 0x5A), (0xE0, 0x60, 0x60),
                                         (0x3E, 0x3E, 0x44), (0x1D, 0x1D, 0x20))


def split_command(text, nt=None):
    """ECADLIB_CMD -> argv. On Windows backslashes are path separators, not escapes (shlex's POSIX rules would turn
    C:\\Tools\\ecadlib.exe into C:Toolsecadlib.exe); a whole value that is an existing file is one program even
    with spaces in it ("C:\\Program Files\\ecadlib\\ecadlib.exe")."""
    nt = os.name == "nt" if nt is None else nt
    text = text.strip()
    if os.path.isfile(text.strip('"')):
        return [text.strip('"')]
    if not nt:
        return shlex.split(text)
    return [w[1:-1] if len(w) > 1 and w[0] == w[-1] == '"' else w for w in shlex.split(text, posix=False)]


def ecadlib_command(environ=None, which=shutil.which, run=subprocess.run, cmd_file=None):
    """The command line that runs ecadlib, or None."""
    environ = os.environ if environ is None else environ
    if environ.get("ECADLIB_CMD"):
        return split_command(environ["ECADLIB_CMD"])
    cmd_file = cmd_file or os.path.join(HERE, "ecadlib_cmd.json")
    try:
        with open(cmd_file, encoding="utf-8") as f:
            cmd = json.load(f).get("command")
        if isinstance(cmd, list) and cmd and all(isinstance(x, str) for x in cmd) and os.path.isfile(cmd[0]):
            return cmd
    except (OSError, ValueError, AttributeError):
        pass
    exe = which("ecadlib")
    if exe:
        return [exe]
    for py in ("python3", "python"):
        p = which(py)
        if p and _imports_ecadlib(p, run):
            return [p, "-m", "ecadlib"]
    return None


def _imports_ecadlib(python, run=subprocess.run):
    """Whether `python` has the ecadlib package. Asked in an empty folder of its own: in KiCad's working folder (a
    project folder) the project's ecadlib/ library folder imports as a namespace package and answers yes; and a module
    of the package (kicad_menu), which no library folder has."""
    import tempfile
    d = tempfile.mkdtemp(prefix="ecadlib-probe-")
    try:
        return run([python, "-c", "import ecadlib.kicad_menu"], cwd=d, capture_output=True, **NO_WINDOW).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        shutil.rmtree(d, ignore_errors=True)


def kicad_major():
    """The running KiCad's major version (7, 8, 9, ...) from pcbnew.Version(), or None."""
    try:
        m = re.search(r"\d+", str(pcbnew.Version()))
    except Exception:                                # an old or a stand-in pcbnew without Version()
        return None
    return int(m.group(0)) if m else None


MPN_RE = re.compile(r"^[A-Za-z0-9][-A-Za-z0-9_.+/#,() ]{0,79}$")


AI_ARGS = (("bom", "--bom"), ("part", "--part"), ("approved_usd", "--approved-usd"))


def job_command(cmd, job, action, project, mpn="", manufacturer="", path="", kicad=None, **ai):
    """The ecadlib run for one job of the window. kicad: the running KiCad's major version (7: ecadlib writes the
    library in KiCad 7's formats). ai: the AI step's bom / part / approved_usd and the over_limit_ok /
    price_unknown_ok yeses (ai-read, after the window showed the cost and the person said yes)."""
    if action == "add-part" and not MPN_RE.match(mpn.strip()):
        raise ValueError(f"not a part number: {mpn!r}")
    # every value as --option=value: a value the person typed may start with '-' ("-TI"), which argparse would take
    # for an option when it is a separate argument
    c = cmd + ["kicad-menu", "run-job", job, "--action=" + action, "--project=" + project]
    if kicad:
        c.append(f"--kicad={int(kicad)}")
    for k, v in (("--mpn", mpn.strip()), ("--manufacturer", manufacturer.strip()), ("--path", path)):
        if v:
            c.append(f"{k}={v}")
    for k, flag in AI_ARGS:
        if ai.get(k):
            c.append(f"{flag}={ai[k]}")
    for k in ("over_limit_ok", "price_unknown_ok"):
        if ai.get(k) == "1":
            c.append("--" + k.replace("_", "-"))
    return c


def new_job(project_dir, action):
    jobs = os.path.join(project_dir, "ecadlib-work", "jobs")
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    job = os.path.join(jobs, f"kicad-{stamp}-{action}")
    os.makedirs(job, exist_ok=True)
    return job


def read_ini(path, section):
    """{key: value} of one section of the INI ecadlib writes (UTF-16 with a BOM); {} while it is not there yet."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return {}
    text = data.decode("utf-16") if data.startswith((b"\xff\xfe", b"\xfe\xff")) else data.decode("utf-8", "replace")
    out, inside = {}, False
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            inside = line[1:-1] == section
        elif inside and "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    return out


def rows_of(ini):
    """The "label|value|ok" rows of a progress / result section -> [(label, value, ok)]."""
    try:
        n = int(ini.get("rows") or 0)
    except ValueError:
        n = 0
    rows = []
    for i in range(1, min(n, ROWS) + 1):
        a, _, rest = ini.get(f"row{i}", "").partition("|")
        b, _, ok = rest.partition("|")
        rows.append((a, b, ok == "1"))
    return rows


def _field(row, key):
    """The value of (key "value") or (key value) in one lib table row, "" when it has none."""
    m = re.search(r"\(\s*" + key + r'\s+(?:"((?:[^"\\]|\\.)*)"|([^\s()]+))', row)
    if not m:
        return ""
    return m.group(1).replace('\\"', '"') if m.group(1) is not None else m.group(2)


def table_rows(text):
    """The rows of a lib table text as [(name, uri)]: each second-level ( ... ) group, strings and nesting respected,
    whatever the layout (one line, or pretty-printed over several as KiCad and kicad_dblib.add_lib_row write it)."""
    rows, depth, i, start, n = [], 0, 0, None, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            i += 1
            while i < n and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
        elif c == "(":
            depth += 1
            if depth == 2:
                start = i
        elif c == ")":
            if depth == 2 and start is not None:
                row = text[start:i + 1]
                if re.match(r"\(\s*lib\b", row):
                    rows.append((_field(row, "name"), _field(row, "uri")))
                start = None
            depth -= 1
        i += 1
    return rows


def ensure_tables(project_dir):
    """Add the ecadlib rows to the project's fp-lib-table / sym-lib-table (created when missing). Returns changed files.
    The job (`ecadlib kicad-menu run-job`) does this itself; this is for an older ecadlib whose result does not say."""
    changed = []
    for kind, table, uri in (("fp", "fp_lib_table", f"{LIB}/{LIB}.pretty"), ("sym", "sym_lib_table", f"{LIB}/{LIB}.kicad_sym")):
        path = os.path.join(project_dir, f"{kind}-lib-table")
        row = f'  (lib (name "{LIB}")(type "KiCad")(uri "${{KIPRJMOD}}/{uri}")(options "")(descr "ecadlib parts"))\n'
        text = open(path, encoding="utf-8").read() if os.path.isfile(path) else f"({table}\n  (version 7)\n)\n"
        mine = [u for name, u in table_rows(text) if name == LIB]
        other = [u for u in mine if u.replace("\\", "/").rstrip("/") != f"${{KIPRJMOD}}/{uri}"]
        if other:                                    # a row of another ecadlib library
            raise ValueError(f"the project's {kind}-lib-table already has a library named {LIB} at {other[0]}, not "
                             f"${{KIPRJMOD}}/{uri} where this window builds the parts: rename or remove that row "
                             f"(Preferences > Manage {'Footprint' if kind == 'fp' else 'Symbol'} Libraries > Project)")
        if mine:
            continue
        body = text.rstrip()
        if not body.endswith(")"):
            raise ValueError(f"{path}: unexpected layout; add this row by hand: {row.strip()}")
        open(path, "w", encoding="utf-8").write(body[:-1].rstrip() + "\n" + row + ")\n")
        changed.append(path)
    return changed


REOPEN = "The project's library tables now list the ecadlib library: reopen the project so the symbol chooser shows it."


def tables_after_fill(project_dir):
    """'Fill the library now' built <project>/ecadlib/: list it in the project's library tables -> the line the window
    shows ('' when the tables already list it, or nothing was built)."""
    if not os.path.isfile(os.path.join(project_dir, LIB, f"{LIB}.kicad_sym")):
        return ""
    try:
        return REOPEN if ensure_tables(project_dir) else ""
    except Exception as e:                           # the library is built; the tables are the extra step
        return f"Built, but not added to the project's library tables: {e}"


def next_reference(refs, prefix):
    """The first free reference after the highest one of `prefix` on the board: R1, R2, R10 -> R11 ("U" for none)."""
    prefix = prefix or "U"
    nums = [int(m.group(1)) for r in refs for m in [re.fullmatch(re.escape(prefix) + r"(\d+)", str(r))] if m]
    return f"{prefix}{max(nums, default=0) + 1}"


def place(board, project_dir, fp_name, ref_prefix="", value=""):
    """Load ecadlib:<fp_name> from the project's ecadlib library and put it in the middle of the board, with the next
    free reference of the part's prefix (R, C, U...) and the part's value instead of REF** and the footprint name.
    Inside an action plugin's Run() KiCad records the change for undo; outside it (the window's timer) the board is
    marked changed where this KiCad's pcbnew offers that."""
    fp = pcbnew.FootprintLoad(os.path.join(project_dir, LIB, f"{LIB}.pretty"), fp_name)
    if fp is None:
        raise ValueError(f"{fp_name} is not in {LIB}.pretty")
    fp.SetFPID(pcbnew.LIB_ID(LIB, fp_name))
    fp.SetReference(next_reference([f.GetReference() for f in board.GetFootprints()], ref_prefix))
    if value:
        fp.SetValue(value)
    box = board.GetBoardEdgesBoundingBox()
    fp.SetPosition(box.GetCenter() if box.GetWidth() else pcbnew.VECTOR2I(0, 0))
    board.Add(fp)
    for obj in (fp, board):
        if hasattr(obj, "SetModified"):
            obj.SetModified()
    pcbnew.Refresh()
    return fp


# ------------------------------------------------------------------ jobs finished while the window was closed
PENDING, PLACED = "place.txt", "placed.txt"


def mark_pending(job):
    """An add-part job whose footprint the window places when it ends (or the next window, if this one is closed)."""
    with open(os.path.join(job, PENDING), "w", encoding="utf-8") as f:
        f.write("place\n")


def mark_placed(job, how="placed"):
    """The job's footprint is placed (or will not be: how says why); no window offers it again."""
    try:
        os.replace(os.path.join(job, PENDING), os.path.join(job, PLACED))
    except OSError:
        return
    try:
        with open(os.path.join(job, PLACED), "a", encoding="utf-8") as f:
            f.write(how + "\n")
    except OSError:
        pass


def unplaced_jobs(project_dir):
    """[(job, result)] of add-part jobs that ended with a footprint to place that no window placed (it was closed while
    the job worked), newest first."""
    jobs = os.path.join(project_dir, "ecadlib-work", "jobs")
    out = []
    try:
        names = sorted(os.listdir(jobs), reverse=True)
    except OSError:
        return out
    for n in names:
        job = os.path.join(jobs, n)
        if not (os.path.isfile(os.path.join(job, PENDING)) and os.path.isfile(os.path.join(job, "exit.txt"))):
            continue
        res = read_ini(os.path.join(job, "result.ini"), "result")
        if res.get("footprint") and res.get("status") in ("ready", "duplicate") and res.get("tables") != "problem":
            out.append((job, res))
    return out


def tables_line(res, project_dir, built=False):
    """The line the window shows about the project's library tables after a job that built parts. The job lists the
    library itself ("tables" in its result); an older ecadlib's result does not say, and the window does it."""
    st = res.get("tables")
    if st is None:                                   # an ecadlib from before the job wrote the tables
        if not built:
            return tables_after_fill(project_dir)
        try:
            return REOPEN if ensure_tables(project_dir) else ""
        except Exception as e:
            return f"Built, but not placed: {e}"
    if st == "added":
        return REOPEN
    if st == "problem":
        return f"Built, but not {'placed' if built else 'added to the project library tables'}: {res.get('tables_note', '')}"
    return ""


def place_result(job, res, project_dir):
    """Place the footprint of a finished add-part job on the open board -> (the "Placed ..." line, or the problem)."""
    try:
        board = pcbnew.GetBoard()
        fp = place(board, project_dir, res["footprint"], res.get("reference", ""), res.get("value") or res.get("mpn", ""))
    except Exception as e:                           # the part is built; placing is the extra step
        mark_placed(job, f"not placed: {e}")
        return "", f"Built, but not placed: {e}"
    mark_placed(job)
    return f"Placed {fp.GetReference()} ({LIB}:{res['footprint']}) in the middle of the board.", ""


def job_failure(job):
    """What went wrong with a job that has ended, as the window shows it; "" when it did what it was asked."""
    res = read_ini(os.path.join(job, "result.ini"), "result")
    if not res:
        tail = ""
        try:
            with open(os.path.join(job, "log.txt"), encoding="utf-8", errors="replace") as f:
                tail = f.read()[-600:]
        except OSError:
            pass
        return f"ecadlib stopped without a result.\nNext: look at its log, {os.path.join(job, 'log.txt')}\n\n{tail}"
    if res.get("status") == "error":
        return (res.get("message") or "ecadlib could not do it.") + (f"\nNext: {res['fix']}" if res.get("fix") else "")
    return ""


def board_project():
    """(board, project folder, board file) of the open board, or (board, None, None) when it is not saved yet."""
    board = pcbnew.GetBoard()
    path = board.GetFileName() if board else ""
    if not path:
        return board, None, None
    return board, os.path.dirname(os.path.abspath(path)), os.path.abspath(path)


# ------------------------------------------------------------------ the window
_WINDOW = []


def _window_class():
    import wx

    def colour(rgb):
        return wx.Colour(*rgb)

    def font(size=10, bold=False, face="Segoe UI"):
        f = wx.Font(size, wx.FONTFAMILY_SWISS, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD if bold else wx.FONTWEIGHT_NORMAL,
                    faceName=face if os.name == "nt" else "")
        return f

    class Flat(wx.Panel):
        """A flat button like the Altium dialog's (a coloured panel with a centred caption)."""

        def __init__(self, parent, label, on_click, primary=False, width=None):
            super().__init__(parent, style=wx.BORDER_NONE)
            self.label, self.on_click, self.primary, self.busy, self.hover = label, on_click, primary, False, False
            w = width or max(90, self.GetTextExtent(label).width + 34)
            self.SetMinSize((w, 30))
            self.SetCursor(wx.Cursor(wx.CURSOR_HAND))
            self.SetBackgroundStyle(wx.BG_STYLE_PAINT)
            self.Bind(wx.EVT_PAINT, self.paint)
            self.Bind(wx.EVT_LEFT_UP, lambda e: (not self.busy) and self.on_click())
            self.Bind(wx.EVT_ENTER_WINDOW, lambda e: self.set_hover(True))
            self.Bind(wx.EVT_LEAVE_WINDOW, lambda e: self.set_hover(False))

        def set_hover(self, v):
            self.hover = v
            self.Refresh()

        def paint(self, _):
            dc = wx.AutoBufferedPaintDC(self)
            w, h = self.GetClientSize()
            dc.SetBackground(wx.Brush(colour(BG)))
            dc.Clear()
            c = BLUE_OFF if self.busy else BLUE if self.primary else BTN
            if self.hover and not self.busy:
                c = tuple(min(255, v + 18) for v in c)
            dc.SetBrush(wx.Brush(colour(c)))
            dc.SetPen(wx.Pen(colour(c)))
            dc.DrawRoundedRectangle(0, 0, w, h, 3)
            dc.SetFont(font(10, self.primary))
            dc.SetTextForeground(colour(TEXT))
            tw, th = dc.GetTextExtent(self.label)
            dc.DrawText(self.label, (w - tw) // 2, (h - th) // 2)

    class Meter(wx.Panel):
        """The progress bar: filled to done/total, or a short bar swinging along the track while total is 0."""

        def __init__(self, parent):
            super().__init__(parent, size=(-1, 4))
            self.SetMinSize((-1, 4))
            self.done, self.total, self.swing = 0, 0, -60
            self.SetBackgroundStyle(wx.BG_STYLE_PAINT)
            self.Bind(wx.EVT_PAINT, self.paint)

        def set(self, done, total):
            self.done, self.total = done, total
            if not total:
                self.swing = self.swing + 16 if self.swing + 16 <= self.GetClientSize().width else -60
            self.Refresh()

        def paint(self, _):
            dc = wx.AutoBufferedPaintDC(self)
            w, h = self.GetClientSize()
            dc.SetBackground(wx.Brush(colour(RULE)))
            dc.Clear()
            dc.SetBrush(wx.Brush(colour(BLUE)))
            dc.SetPen(wx.TRANSPARENT_PEN)
            if self.total:
                dc.DrawRectangle(0, 0, w * min(self.done, self.total) // self.total, h)
            else:
                dc.DrawRectangle(self.swing, 0, 60, h)

    class EcadlibWindow(wx.Frame):
        def __init__(self, parent):
            super().__init__(parent, title="ecadlib", style=wx.CAPTION | wx.CLOSE_BOX | wx.FRAME_FLOAT_ON_PARENT
                             | wx.FRAME_TOOL_WINDOW | wx.RESIZE_BORDER)
            self.SetBackgroundColour(colour(BG))
            self.job = self.proc = self.action = None
            self.project = self.board_file = None
            self.mpn = ""
            self.result = {}
            self.timer = wx.Timer(self)
            self.Bind(wx.EVT_TIMER, self.tick, self.timer)
            self.Bind(wx.EVT_CLOSE, self.on_close)
            self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
            self.body = wx.Panel(self)
            self.body.SetBackgroundColour(colour(BG))
            fs = wx.BoxSizer(wx.VERTICAL)
            fs.Add(self.body, 1, wx.EXPAND)
            self.SetSizer(fs)
            self.enter = self.escape = None

        # -------------------------------------------------- building blocks
        def text(self, parent, s, colour_=TEXT, size=10, bold=False, face="Segoe UI"):
            t = wx.StaticText(parent, label=s)
            t.SetForegroundColour(colour(colour_))
            t.SetFont(font(size, bold, face))
            t.SetMinSize(t.GetTextExtent(s) if "\n" not in s else t.GetBestSize())
            return t

        def page(self, title):
            """Clear the window and start a page: header 'ecadlib · title' and a sizer to fill."""
            self.body.DestroyChildren()
            self.enter = self.escape = None
            outer = wx.BoxSizer(wx.VERTICAL)
            head = wx.Panel(self.body)
            head.SetBackgroundColour(colour(HEAD))
            hs = wx.BoxSizer(wx.HORIZONTAL)
            hs.Add(self.text(head, "ecadlib" if title == "ecadlib" else f"ecadlib · {title}", TEXT, 10, True), 1, wx.ALL | wx.ALIGN_CENTER_VERTICAL, 10)
            head.SetSizer(hs)
            outer.Add(head, 0, wx.EXPAND)
            inner = wx.BoxSizer(wx.VERTICAL)
            outer.Add(inner, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 16)
            self.body.SetSizer(outer)
            return inner

        def rows(self, sizer, rows):
            for label, value, ok in rows:
                line = wx.BoxSizer(wx.HORIZONTAL)
                line.Add(self.text(self.body, label, TEXT), 1, wx.ALIGN_CENTER_VERTICAL)
                line.Add(self.text(self.body, value, GREEN if ok else DIM, 10, ok), 0, wx.ALIGN_CENTER_VERTICAL)
                sizer.Add(line, 0, wx.EXPAND | wx.TOP, 6)
                rule = wx.Panel(self.body, size=(-1, 1))
                rule.SetBackgroundColour(colour(RULE))
                sizer.Add(rule, 0, wx.EXPAND | wx.TOP, 6)

        def note(self, sizer, s, colour_=DIM):
            t = self.text(self.body, s, colour_)
            t.SetMinSize((-1, -1))
            t.Wrap(WIDTH - 40)
            t.InvalidateBestSize()
            t.SetMinSize(t.GetBestSize())
            sizer.Add(t, 0, wx.TOP, 10)

        def link(self, sizer, s, on_click):
            t = self.text(self.body, s, BLUE)
            t.SetCursor(wx.Cursor(wx.CURSOR_HAND))
            t.Bind(wx.EVT_LEFT_UP, lambda e: on_click())
            sizer.Add(t, 0, wx.TOP, 8)

        def buttons(self, sizer, primary, on_primary, secondary=None, on_secondary=None, busy=False):
            line = wx.BoxSizer(wx.HORIZONTAL)
            line.AddStretchSpacer()
            if secondary:
                line.Add(Flat(self.body, secondary, on_secondary), 0, wx.RIGHT, 8)
                self.escape = on_secondary
            b = Flat(self.body, primary, on_primary, primary=True)
            b.busy = busy
            line.Add(b)
            sizer.Add(line, 0, wx.EXPAND | wx.TOP, 16)
            if not busy:
                self.enter = on_primary
            if not secondary:
                self.escape = self.Close

        def done_layout(self):
            # the client size, not Fit(): on Linux the frame's caption height is not known before it is shown
            for c in self.body.GetChildren():          # fonts set after creation: sizes are measured again
                c.InvalidateBestSize()
            need = self.body.GetSizer().CalcMin()
            size = (max(WIDTH, need.width), need.height)
            self.SetMinClientSize(size)
            self.SetClientSize(size)
            self.body.Layout()
            self.Refresh()
            for ms in (60, 300):
                wx.CallLater(ms, self.refit, size)

        def refit(self, size):
            """The window manager's caption comes in after the first size: grow the frame by what it really takes."""
            if not self or tuple(self.GetClientSize()) == size:
                return
            dw, dh = self.GetSize().width - self.GetClientSize().width, self.GetSize().height - self.GetClientSize().height
            self.SetMinSize((size[0] + dw, size[1] + dh))
            self.SetSize((size[0] + dw, size[1] + dh))
            self.body.Layout()

        def on_key(self, e):
            k = e.GetKeyCode()
            if k in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER) and self.enter:
                self.enter()
            elif k == wx.WXK_ESCAPE and self.escape:
                self.escape()
            else:
                e.Skip()

        # -------------------------------------------------- pages
        def home(self):
            s = self.page("ecadlib")
            s.Add(self.text(self.body, "What would you like to do?", DIM), 0, wx.TOP, 12)
            for label, hint, go in (("Add part from MPN…", "look a part up and place its footprint", self.ask),
                                    ("Check project BOM", "which schematic parts the library already has", self.check),
                                    ("Open review queue", "pick symbols and footprints for held parts", self.queue)):
                b = Flat(self.body, label, go, primary=False, width=400)
                s.Add(b, 0, wx.EXPAND | wx.TOP, 10)
                s.Add(self.text(self.body, hint, DIM, 9), 0, wx.TOP | wx.LEFT, 3)
            self.buttons(s, "Close", self.Close)
            self.done_layout()

        def ask(self, mpn=""):
            s = self.page("Add part from MPN")
            fields = []
            for label, hint in (("Manufacturer part number", mpn), ("Manufacturer (optional)", "")):
                s.Add(self.text(self.body, label, DIM, 9), 0, wx.TOP, 12)
                t = wx.TextCtrl(self.body, value=hint, size=(400, -1))
                t.SetBackgroundColour(colour(FIELD))
                t.SetForegroundColour(colour(TEXT))
                t.SetFont(font(11, False, "Consolas"))
                s.Add(t, 0, wx.EXPAND | wx.TOP, 4)
                fields.append(t)
            self.fields = fields
            self.buttons(s, "OK", self.start_add, "Cancel", self.Close)
            self.done_layout()
            fields[0].SetFocus()

        def start_add(self):
            mpn, maker = (f.GetValue().strip() for f in self.fields)
            if not MPN_RE.match(mpn):
                self.fields[0].SetFocus()
                return
            self.mpn = mpn
            self.run("add-part", mpn, mpn=mpn, manufacturer=maker)

        def check(self):
            self.run("check-bom", "Check project BOM")

        def fill(self):
            self.run("fill", "Fill the library")

        def queue(self):
            self.run("queue", "Open review queue")

        def read_ai(self):
            """The AI step the last job offered: a second yes above the limit, then the reads, held to the amount shown."""
            r = self.result
            if r.get("ai_over") == "1" and wx.MessageBox(r.get("ai_confirm", ""), "ecadlib", wx.YES_NO | wx.NO_DEFAULT,
                                                         self) != wx.YES:
                return
            if self.action == "add-part":
                self.mpn = r.get("ai_mpn") or getattr(self, "mpn", "")
            self.run("ai-read", "Read with AI", mpn=r.get("ai_mpn", ""), bom=r.get("ai_bom", ""), part=r.get("ai_part", ""),
                     approved_usd=r.get("ai_approve", ""), over_limit_ok=r.get("ai_over", ""),
                     price_unknown_ok=r.get("ai_unknown", ""))

        def report(self):
            self.fire("open", path=self.result.get("report", ""))

        # -------------------------------------------------- jobs
        def command(self, action, **kw):
            cmd = ecadlib_command()
            if not cmd:
                raise ValueError("ecadlib is not installed where KiCad can find it.\nNext: install ecadlib (the Windows "
                                 "installer, or pip install ecadlib) and press \"Add to KiCad\" on its setup page again, "
                                 "or set ECADLIB_CMD to its command.")
            job = new_job(self.project, action)
            return job, job_command(cmd, job, action, self.board_file, kicad=kicad_major(), **kw)

        def start(self, action, **kw):
            """Start one job -> (job folder, process); its output goes to JOB/log.txt."""
            job, c = self.command(action, **kw)
            if action in ("add-part", "ai-read"):
                mark_pending(job)                        # placed by this window, or offered by the next one
            with open(os.path.join(job, "log.txt"), "w", encoding="utf-8") as log:
                # cwd: the job folder, never the project: `python -m ecadlib` would import the project's ecadlib/
                # library folder instead of the ecadlib package
                return job, subprocess.Popen(c, cwd=job, stdout=log, stderr=subprocess.STDOUT, **NO_WINDOW)

        def fire(self, action, **kw):
            """A job the window does not wait for (open a report): its failure is still shown when it ends."""
            try:
                job, proc = self.start(action, **kw)
            except (OSError, ValueError) as e:
                return self.fail(str(e))
            wx.CallLater(300, self.watch, job, proc)

        def watch(self, job, proc):
            if not self:                                 # the window was closed meanwhile
                return
            if proc.poll() is None:
                wx.CallLater(300, self.watch, job, proc)
                return
            problem = job_failure(job)
            if problem and self.proc is None:            # not over a job the window shows
                self.fail(problem)

        def run(self, action, title, **kw):
            self.action, self.title, self.result = action, title, {}
            try:
                self.job, self.proc = self.start(action, **kw)
            except (OSError, ValueError) as e:
                self.fail(str(e))
                return
            self.live({}, "starting ecadlib")
            self.timer.Start(250)

        def live(self, prog, default=""):
            s = self.page(self.title)
            big = {"add-part": self.mpn, "check-bom": "Checking…", "fill": "Filling…", "ai-read": "Reading…",
                   "queue": "Opening…"}.get(self.action, "")
            s.Add(self.text(self.body, big, TEXT, 20, True), 0, wx.TOP, 10)
            self.rows(s, rows_of(prog))
            s.Add(self.text(self.body, prog.get("text") or default, DIM, 9), 0, wx.TOP, 10)
            self.meter = Meter(self.body)
            try:
                self.meter.done, self.meter.total = int(prog.get("done") or 0), int(prog.get("total") or 0)
            except ValueError:
                pass
            s.Add(self.meter, 0, wx.EXPAND | wx.TOP, 6)
            self.buttons(s, "Working…", lambda: None, busy=True)
            self.done_layout()

        def tick(self, _):
            if self.proc is None:
                return
            if self.proc.poll() is None:
                prog = read_ini(os.path.join(self.job, "progress.ini"), "progress")
                key = (prog.get("text"), prog.get("rows"), prog.get("done"))
                if key != getattr(self, "_shown", None):
                    self._shown = key
                    self.live(prog)
                self.meter.set(self.meter.done, self.meter.total)
                return
            self.timer.Stop()
            self.proc = None
            self.finish()

        def finish(self):
            res = read_ini(os.path.join(self.job, "result.ini"), "result")
            self.result = res
            problem = job_failure(self.job)
            if problem:
                mark_placed(self.job, "failed")
                return self.fail(problem)
            st = res.get("status", "")
            if self.action == "queue":
                s = self.page(self.title)
                s.Add(self.text(self.body, "Opened", TEXT, 20, True), 0, wx.TOP, 10)
                self.note(s, res.get("message") or "The review queue is open in the browser.")
                self.buttons(s, "Done", self.Close)
                return self.done_layout()
            placed, extra = "", ""
            if self.action == "fill":
                extra = tables_line(res, self.project)
            if self.action in ("add-part", "ai-read") and res.get("footprint") and st in ("ready", "duplicate"):
                extra = tables_line(res, self.project, built=True)
                if res.get("tables") == "problem" or extra.startswith("Built, but"):
                    mark_placed(self.job, "not placed: " + extra)
                else:
                    placed, err = place_result(self.job, res, self.project)
                    if err:
                        extra = err
            else:
                mark_placed(self.job, "nothing to place")
            s = self.page(self.title)
            s.Add(self.text(self.body, res.get("big") or "Done", TEXT, 20, True), 0, wx.TOP, 10)
            self.rows(s, rows_of(res))
            meter = Meter(self.body)
            meter.done = meter.total = 1
            s.Add(meter, 0, wx.EXPAND | wx.TOP, 10)
            for line in (placed, extra):
                if line:
                    self.note(s, line, GREEN if line is placed else DIM)
            if self.action == "check-bom":
                if res.get("warnings"):
                    self.note(s, res["warnings"])
                if res.get("report"):
                    self.link(s, "Open the report in the browser", self.report)
                if res.get("offer_fill") == "1":
                    self.buttons(s, "Fill the library now", self.fill, "Later", self.Close)
                else:
                    self.buttons(s, "Done", self.Close)
            else:
                if res.get("ai_cost_text"):
                    self.note(s, res["ai_cost_text"], GREEN)
                if res.get("fix") and st != "ready":
                    self.note(s, "Next: " + res["fix"])
                if res.get("offer_ai") == "1":
                    self.note(s, "AI step: " + res.get("ai_text", "") + " Nothing is sent until you click.")
                    self.buttons(s, res.get("ai_button") or "Read with AI", self.read_ai, "Not now", self.Close)
                elif st in ("held", "awaiting_approval") or res.get("offer_queue") == "1":
                    self.buttons(s, "Done", self.Close, "Open review queue", self.queue)
                elif self.action == "add-part":
                    self.buttons(s, "Done", self.Close, "Add another", self.ask)
                else:
                    self.buttons(s, "Done", self.Close)
            self.done_layout()

        def placed_page(self, lines):
            """What offer_unplaced did, before anything else: the parts placed now (or why not)."""
            self.title = "Add part from MPN"
            s = self.page(self.title)
            s.Add(self.text(self.body, "Placed" if any(l.startswith("Placed") for l in lines) else "Not placed",
                            TEXT, 20, True), 0, wx.TOP, 10)
            for line in lines:
                self.note(s, line, GREEN if line.startswith("Placed") else DIM)
            self.buttons(s, "Done", self.Close, "More…", self.home)
            self.done_layout()

        def fail(self, problem):
            self.timer.Stop()
            s = self.page(getattr(self, "title", None) or "ecadlib")
            s.Add(self.text(self.body, "Not done", TEXT, 20, True), 0, wx.TOP, 10)
            self.note(s, problem, RED)
            self.buttons(s, "Close", self.Close)
            self.done_layout()

        def on_close(self, e):
            if self.proc is not None and self.proc.poll() is None:
                after = (" and adds the part to the project's library; press the ecadlib button again when it is done "
                         "to place its footprint" if self.action in ("add-part", "ai-read") else "")
                if wx.MessageBox(f"ecadlib is still working. Close the window anyway? (ecadlib keeps working in the "
                                 f"background{after}.)", "ecadlib", wx.YES_NO | wx.NO_DEFAULT, self) != wx.YES:
                    e.Veto()
                    return
            self.timer.Stop()
            _WINDOW.clear()
            self.Destroy()

    return EcadlibWindow


def open_window(mode="home"):
    """Show the ecadlib window (one per KiCad) on a page: home, add, bom or queue."""
    import wx
    board, project, board_file = board_project()
    if not project:
        wx.MessageBox("Save the board first: ecadlib puts the library next to the project.\n"
                      "Next: File > Save, then press the ecadlib button again.", "ecadlib")
        return None
    if _WINDOW:
        w = _WINDOW[0]
        w.Raise()
        if w.proc is not None:                       # still working: show it, start nothing new
            return w
    else:
        parent = wx.FindWindowByName("PcbFrame") or next((x for x in wx.GetTopLevelWindows()
                                                          if "pcb" in x.GetName().lower()), None)
        placed = offer_unplaced(project, parent)       # in Run(): KiCad records the placement for undo
        w = _window_class()(parent)
        _WINDOW.append(w)
        if placed:
            w.project, w.board_file = project, board_file
            w.placed_page(placed)
            w.CentreOnParent()
            w.Show()
            return w
    w.project, w.board_file = project, board_file
    {"home": w.home, "add": w.ask, "bom": w.check, "queue": w.queue}[mode]()
    w.CentreOnParent()
    w.Show()
    return w


def offer_unplaced(project, parent=None):
    """Add-part jobs that ended after their window was closed: ask, per part, to place its footprint now. -> the
    lines to show (placed, or why not)."""
    import wx
    lines = []
    for job, res in unplaced_jobs(project):
        what = res.get("mpn") or res.get("part") or res["footprint"]
        if wx.MessageBox(f"ecadlib finished adding {what} after its window was closed. Place its footprint "
                         f"({LIB}:{res['footprint']}) on the board now?", "ecadlib", wx.YES_NO | wx.YES_DEFAULT,
                         parent) != wx.YES:
            mark_placed(job, "declined")
            continue
        done, err = place_result(job, res, project)
        lines.append(done or err)
    return lines


class EcadlibButton(pcbnew.ActionPlugin):
    def defaults(self):
        self.name = "ecadlib"
        self.category = "Library"
        self.description = "ecadlib: add a part from its MPN, check the project BOM, open the review queue"
        self.show_toolbar_button = True
        self.icon_file_name = os.path.join(HERE, "ecadlib.png")

    def Run(self):
        open_window("home")


class EcadlibAddPart(pcbnew.ActionPlugin):
    def defaults(self):
        self.name = "ecadlib: add part"
        self.category = "Library"
        self.description = "Build a part from its manufacturer part number with ecadlib and place its footprint"
        self.show_toolbar_button = False

    def Run(self):
        open_window("add")


class EcadlibCheckBom(pcbnew.ActionPlugin):
    def defaults(self):
        self.name = "ecadlib: check project BOM"
        self.category = "Library"
        self.description = "Check the schematic's parts against the ecadlib library and fill in the missing ones"
        self.show_toolbar_button = False

    def Run(self):
        open_window("bom")


class EcadlibQueue(pcbnew.ActionPlugin):
    def defaults(self):
        self.name = "ecadlib: open review queue"
        self.category = "Library"
        self.description = "Open ecadlib's review queue in the browser"
        self.show_toolbar_button = False

    def Run(self):
        open_window("queue")


PLUGINS = (EcadlibButton, EcadlibAddPart, EcadlibCheckBom, EcadlibQueue)


def first_copy():
    """Whether this copy of the plugin is the first one KiCad loads in this loading pass. The setup page's copy
    (scripting/plugins/ecadlib_add_part) and the Plugin and Content Manager's (3rdparty/plugins/...) can both be
    installed: only the first registers, so the toolbar has one ecadlib button. A pass is told apart by the search
    path string pcbnew.LoadPlugins builds anew each time (Tools > External Plugins > Refresh Plugins loads again)."""
    this_pass = getattr(pcbnew, "PLUGIN_DIRECTORIES_SEARCH", None)
    seen = getattr(pcbnew, "_ecadlib_plugin_copy", None)
    if isinstance(seen, tuple) and len(seen) == 2 and seen[1] is this_pass and seen[0] != HERE:
        return False
    pcbnew._ecadlib_plugin_copy = (HERE, this_pass)
    return True


if first_copy():
    for _p in PLUGINS:
        _p().register()
