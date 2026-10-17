"""Regression tests for a failed Altium build in the altium_native flow (real AD26, reproduced twice): when Altium
could not build a library, ecadlib_build.pas had already deleted the old .SchLib / .PcbLib, the menu still said the
part was ready and installed a DbLib pointing at the missing files.

There is no Altium here, so the generated DelphiScript itself is run by a small interpreter (just the statements and
expressions these routines use) with Altium's servers faked: Altium either saves nothing (the build produced no
output) or raises an error. Run:
    python3 -m tests.run_all test_review_1018_build_fail"""
import os
from pathlib import Path

from ecadlib import altium_forms as AF, altium_menu as AM, altium_native as AN
from tests.fixtures_lite import lite


# ------------------------------------------------------------------ a small DelphiScript interpreter
class _Exit(Exception):
    pass


class _Break(Exception):
    pass


class Fake:
    """Any Altium object: every member is another Fake, calling one gives a Fake, assignments are kept."""

    def __init__(self, name="fake", **kw):
        self.__dict__["_name"] = name
        for k, v in kw.items():
            self.__dict__[k.lower()] = v

    def __getattr__(self, k):
        if k.startswith("__"):
            raise AttributeError(k)
        v = Fake(f"{self._name}.{k}")
        self.__dict__[k] = v
        return v

    def __call__(self, *a):
        return Fake(f"{self._name}()")


class TStringList:
    def __init__(self):
        self.items = []

    @classmethod
    def create(cls):
        return cls()

    def add(self, s):
        self.items.append(s)

    def clear(self):
        self.items = []

    def free(self):
        pass

    @property
    def count(self):
        return len(self.items)

    @property
    def strings(self):
        return self.items

    def __getitem__(self, i):
        return self.items[i]

    def loadfromfile(self, p):
        self.items = _host(p).read_text(encoding="utf-8-sig").splitlines()

    def savetofile(self, p):
        _host(p).write_text("\r\n".join(self.items) + "\r\n", encoding="utf-8", newline="")


def _host(p):
    return Path(str(p).replace("\\", "/"))


class _Files:
    """The DelphiScript file routines the scripts call, on the real temp folder."""

    def __init__(self):
        self.handles = {}

    def fileexists(self, p):
        return _host(p).is_file()

    def deletefile(self, p):
        try:
            _host(p).unlink()
            return True
        except OSError:
            return False

    def renamefile(self, a, b):
        if _host(b).exists() or not _host(a).exists():
            return False
        os.rename(_host(a), _host(b))
        return True

    def fileopen(self, p, mode):
        if not _host(p).is_file():
            return -1
        h = len(self.handles) + 3
        self.handles[h] = _host(p)
        return h

    def fileseek(self, h, off, origin):
        return self.handles[h].stat().st_size if origin == 2 else off

    def fileclose(self, h):
        self.handles.pop(h, None)


class Pas:
    def __init__(self, *texts, **builtins):
        self.routines = {}
        self.globals = {}
        for text in texts:
            self._scan(AM.pas_tokens(text))
        f = _Files()
        self.builtins = {
            "fileexists": f.fileexists, "deletefile": f.deletefile, "renamefile": f.renamefile, "fileopen": f.fileopen,
            "fileseek": f.fileseek, "fileclose": f.fileclose, "inttostr": lambda i: str(int(i)),
            "strtoint": lambda s: int(s), "trim": lambda s: s.strip(), "length": lambda s: len(s),
            "copy": lambda s, i, n: s[i - 1:i - 1 + n], "pos": lambda a, s: s.find(a) + 1, "uppercase": str.upper,
            "extractfilepath": lambda p: p[:max(p.rfind("\\"), p.rfind("/")) + 1], "tstringlist": TStringList,
            "nil": None, "true": True, "false": False}
        self.builtins.update({k.lower(): v for k, v in builtins.items()})

    # ---- routines: kept as tokens, parsed when first called
    def _scan(self, t):
        i = 0
        while i < len(t):
            if t[i][0] == "word" and t[i][1].lower() in ("function", "procedure"):
                name = t[i + 1][1].lower()
                j, params = i + 2, []
                if t[j][1] == "(":
                    names = []
                    while t[j][1] != ")":
                        j += 1
                        if t[j][0] == "word" and t[j][1].lower() not in ("var", "const"):
                            names.append(t[j][1].lower())
                        if t[j][1] == ":":
                            params += names
                            names = []
                            while t[j][1] not in (";", ")"):
                                j += 1
                    j += 1
                while t[j][1].lower() != "begin" and not (t[j][0] == "word" and t[j][1].lower() == "var"):
                    j += 1
                local, names = {}, []
                if t[j][1].lower() == "var":
                    while t[j][1].lower() != "begin":
                        j += 1
                        if t[j][1] == ":":
                            ty = t[j + 1][1].lower()
                            local.update({n: 0 if ty in ("integer", "double") else "" if ty == "string" else None for n in names})
                            names = []
                        elif t[j][0] == "word" and t[j - 1][1] in (";", ",", "Var", "var"):
                            names.append(t[j][1].lower())
                start, depth = j, 0
                while True:
                    w = t[j][1].lower() if t[j][0] == "word" else ""
                    if w in ("begin", "try", "case"):
                        depth += 1
                    elif w == "end":
                        depth -= 1
                        if depth == 0:
                            break
                    j += 1
                self.routines[name] = {"params": params, "vars": local, "toks": t[start:j + 1], "ast": None}
                i = j + 1
            else:
                i += 1

    def call(self, name, *args):
        name = name.lower()
        if name in self.builtins:
            return self.builtins[name](*args)
        r = self.routines[name]
        if r["ast"] is None:
            self.t, self.i = r["toks"], 0
            r["ast"] = self._stmt()
        env = dict(r["vars"])
        env.update(zip(r["params"], args))
        env["result"] = ""
        try:
            self._run(r["ast"], env)
        except _Exit:
            pass
        return env["result"]

    # ---- parser
    def _peek(self, k=0):
        return self.t[self.i + k] if self.i + k < len(self.t) else ("eof", "", 0)

    def _w(self, k=0):
        tok = self._peek(k)
        return tok[1].lower() if tok[0] == "word" else tok[1]

    def _take(self, want=None):
        tok = self.t[self.i]
        if want is not None and (tok[1].lower() if tok[0] == "word" else tok[1]) != want:
            raise SyntaxError(f"line {tok[2]}: expected {want}, got {tok[1]}")
        self.i += 1
        return tok

    def _block(self, *ends):
        out = []
        while self._w() not in ends:
            out.append(self._stmt())
            if self._w() == ";":
                self._take()
        return out

    def _stmt(self):
        w = self._w()
        if w == "begin":
            self._take()
            body = self._block("end")
            self._take("end")
            return ("seq", body)
        if w == "if":
            self._take()
            c = self._expr()
            self._take("then")
            a = self._stmt() if self._w() not in (";", "else") else ("seq", [])
            b = None
            if self._w() == "else":
                self._take()
                b = self._stmt()
            return ("if", c, a, b)
        if w == "while":
            self._take()
            c = self._expr()
            self._take("do")
            return ("while", c, self._stmt())
        if w == "for":
            self._take()
            v = self._take()[1].lower()
            self._take(":=")
            a = self._expr()
            self._take("to")
            b = self._expr()
            self._take("do")
            return ("for", v, a, b, self._stmt())
        if w == "try":
            self._take()
            body = self._block("except", "finally")
            kind = self._take()[1].lower()
            handler = self._block("end")
            self._take("end")
            return ("try", kind, body, handler)
        if w in ("exit", "break"):
            self._take()
            return (w,)
        d = self._designator()
        if self._w() == ":=":
            self._take()
            return ("assign", d, self._expr())
        return ("do", d)

    def _designator(self):
        node = ("name", self._take()[1].lower())
        while self._w() in (".", "[", "("):
            s = self._take()[1]
            if s == ".":
                node = ("member", node, self._take()[1].lower())
            elif s == "[":
                node = ("index", node, self._expr())
                self._take("]")
            else:
                args = []
                while self._w() != ")":
                    args.append(self._expr())
                    if self._w() == ",":
                        self._take()
                self._take(")")
                node = ("call", node, args)
        return node

    def _expr(self):
        a = self._simple()
        while self._w() in ("=", "<>", "<", ">", "<=", ">="):
            a = ("bin", self._take()[1], a, self._simple())
        return a

    def _simple(self):
        a = self._term()
        while self._w() in ("+", "-", "or"):
            a = ("bin", self._w(), a, (self._take(), self._term())[1])
        return a

    def _term(self):
        a = self._factor()
        while self._w() in ("*", "/", "div", "mod", "and"):
            a = ("bin", self._w(), a, (self._take(), self._factor())[1])
        return a

    def _factor(self):
        kind, val, _ = self._peek()
        if kind == "str":
            s = ""
            while self._peek()[0] == "str":
                s += self._take()[1]
            return ("lit", s)
        if kind == "num":
            self._take()
            return ("lit", float(val) if "." in val else int(val))
        if self._w() == "(":
            self._take()
            e = self._expr()
            self._take(")")
            return e
        if self._w() == "not":
            self._take()
            return ("not", self._factor())
        if self._w() == "-":
            self._take()
            return ("neg", self._factor())
        return self._designator()

    # ---- evaluation
    def _run(self, s, env):
        k = s[0]
        if k == "seq":
            for x in s[1]:
                self._run(x, env)
        elif k == "if":
            if self._eval(s[1], env):
                self._run(s[2], env)
            elif s[3] is not None:
                self._run(s[3], env)
        elif k == "while":
            try:
                while self._eval(s[1], env):
                    self._run(s[2], env)
            except _Break:
                pass
        elif k == "for":
            try:
                for v in range(self._eval(s[2], env), self._eval(s[3], env) + 1):
                    self._set(("name", s[1]), v, env)
                    self._run(s[4], env)
            except _Break:
                pass
        elif k == "try":
            if s[1] == "finally":
                try:
                    self._run(("seq", s[2]), env)
                finally:
                    self._run(("seq", s[3]), env)
            else:
                try:
                    self._run(("seq", s[2]), env)
                except (_Exit, _Break):
                    raise
                except RuntimeError:                 # what the faked Altium raises
                    self._run(("seq", s[3]), env)
        elif k == "exit":
            raise _Exit()
        elif k == "break":
            raise _Break()
        elif k == "assign":
            self._set(s[1], self._eval(s[2], env), env)
        elif k == "do":
            d = s[1]
            if d[0] == "call" and d[1] == ("name", "inc"):
                self._set(d[2][0], self._eval(d[2][0], env) + 1, env)
            else:
                self._eval(d, env)

    def _set(self, d, v, env):
        if d[0] == "name":
            if d[1] in env:
                env[d[1]] = v
            else:
                self.globals[d[1]] = v
        elif d[0] == "member":
            setattr(self._eval(d[1], env), d[2], v)
        else:
            raise NotImplementedError(d)

    def _eval(self, e, env):
        k = e[0]
        if k == "lit":
            return e[1]
        if k == "not":
            return not self._eval(e[1], env)
        if k == "neg":
            return -self._eval(e[1], env)
        if k == "bin":
            op, a, b = e[1], self._eval(e[2], env), self._eval(e[3], env)
            return {"=": lambda: a == b, "<>": lambda: a != b, "<": lambda: a < b, ">": lambda: a > b,
                    "<=": lambda: a <= b, ">=": lambda: a >= b, "+": lambda: a + b, "-": lambda: a - b,
                    "*": lambda: a * b, "/": lambda: a / b, "div": lambda: a // b, "mod": lambda: a % b,
                    "and": lambda: a and b, "or": lambda: a or b}[op]()
        if k == "name":
            n = e[1]
            if n in env:
                return env[n]
            if n in self.globals:
                return self.globals[n]
            if n in self.routines or (n in self.builtins and callable(self.builtins[n]) and not isinstance(self.builtins[n], (type, Fake))):
                return self.call(n)
            if n in self.builtins:
                return self.builtins[n]
            return Fake(n)
        if k == "member":
            v = getattr(self._eval(e[1], env), e[2])
            return v() if callable(v) and not isinstance(v, (type, Fake)) else v
        if k == "index":
            o, i = self._eval(e[1], env), self._eval(e[2], env)
            return o[i - 1] if isinstance(o, str) else o[i]
        if k == "call":
            args = [self._eval(a, env) for a in e[2]]
            if e[1][0] == "name":
                return self.call(e[1][1], *args)
            o = self._eval(e[1][1], env)
            return getattr(o, e[1][2])(*args)
        raise NotImplementedError(e)


# ------------------------------------------------------------------ Altium, faked
class Client:
    """Saves the library only in mode 'ok'; 'nothing' = Altium saves no file, 'raise' = Altium raises an error."""

    def __init__(self, mode):
        self.mode = mode

    def getdocumentbypath(self, p):
        return None

    def opennewdocument(self, kind, path, a, b):
        if self.mode == "raise":
            raise RuntimeError("Altium: cannot create the document")
        mode = self.mode
        doc = Fake("doc")

        def save(kind):
            if mode == "ok":
                _host(path).write_bytes(b"NEW " + kind.encode())
        doc.__dict__["dofilesave"] = save
        return doc

    def showdocument(self, d):
        pass

    def closedocument(self, d):
        pass


class Manager:
    def __init__(self):
        self.installed, self.calls = [], []

    @property
    def installedlibrarycount(self):
        return len(self.installed)

    def installedlibrarypath(self, i):
        return self.installed[i]

    def installlibrary(self, p):
        self.calls.append(("install", p))
        self.installed.append(p)

    def uninstalllibrary(self, p):
        self.calls.append(("uninstall", p))
        self.installed.remove(p)


def _library(tmp):
    """A library folder as altium-pkg writes it: a DbLib, one PcbLib and one SchLib (ecadlib's files) and the
    build recipe for both."""
    lib = tmp / "lib"
    (lib / "PcbLib").mkdir(parents=True)
    (lib / "SchLib").mkdir()
    (lib / "PcbLib" / "SOIC8.PcbLib").write_bytes(b"OLD PCBLIB")
    (lib / "SchLib" / "NE555.SchLib").write_bytes(b"OLD SCHLIB")
    (lib / "ecadlib.DbLib").write_text("[OutputDatabaseLinks]\n", encoding="utf-8")
    (lib / AN.BUILD).write_text("ECADLIB-BUILD\t1\n"
                                "PCBLIB\tPcbLib\\SOIC8.PcbLib\nFOOTPRINT\tSOIC8\tSOIC 8 pins\t15000\nEND\n"
                                "SCHLIB\tSchLib\\NE555.SchLib\nSYMBOL\tNE555\ttimer\tU?\tNE555\t1\t1\nEND\n",
                                encoding="utf-8-sig")
    return lib


def _menu(lib, mode, manager, shown):
    folder = str(lib).replace("/", "\\") + "\\"
    dblib = folder + "ecadlib.DbLib"
    answers = {"build": folder, "install": dblib, "status": "ready", "message": "Ready: NE555DR is in your library."}
    return Pas(AM.pas_text(), AN.PAS,
               Client=Client(mode), IntegratedLibraryManager=manager,
               Answer=lambda job, key: answers.get(key, ""), Setting=lambda key, default: default,
               GetRunningScriptProjectName=lambda: folder + "ecadlib_menu.PrjScr",
               ShowError=lambda s: shown.append(("error", s)), ShowMessage=lambda s: shown.append(("message", s)),
               ResetParameters=lambda: None, AddStringParameter=lambda k, v: None, RunProcess=lambda p: None), dblib


def _old_files_intact(lib):
    assert (lib / "PcbLib" / "SOIC8.PcbLib").read_bytes() == b"OLD PCBLIB"
    assert (lib / "SchLib" / "NE555.SchLib").read_bytes() == b"OLD SCHLIB"
    assert not list(lib.rglob("*.ecadlib_old")), list(lib.rglob("*.ecadlib_old"))


# ------------------------------------------------------------------ the tests
@lite
def test_altium_saving_nothing_keeps_the_old_files_and_installs_nothing(tmp_path):
    lib = _library(tmp_path)
    mgr, shown = Manager(), []
    pas, dblib = _menu(lib, "nothing", mgr, shown)
    mgr.installed.append(dblib)                         # installed by an earlier run
    extra = pas.call("Reinstall", "job")
    _old_files_intact(lib)
    report = (lib / "ecadlib_build_report.txt").read_text(encoding="utf-8").splitlines()
    assert [ln.split("\t")[0] for ln in report] == ["FAILED", "FAILED"], report
    assert all("Altium did not save the file (the previous library file was kept)" in ln for ln in report), report
    assert pas.call("BuildFailed", extra) and "Nothing was installed" in extra, extra
    assert ("install", dblib) not in mgr.calls, mgr.calls
    pas.call("ShowResult", "ecadlib: add NE555DR", "job", extra)
    assert [k for k, _ in shown] == ["error"] and "Ready" not in shown[0][1], shown


@lite
def test_altium_raising_an_error_keeps_the_old_files_and_installs_nothing(tmp_path):
    lib = _library(tmp_path)
    mgr, shown = Manager(), []
    pas, dblib = _menu(lib, "raise", mgr, shown)
    extra = pas.call("Reinstall", "job")
    _old_files_intact(lib)
    report = (lib / "ecadlib_build_report.txt").read_text(encoding="utf-8")
    assert report.count("Altium raised an error while building it (the previous library file was kept)") == 2, report
    assert pas.call("BuildFailed", extra) and not any(c[0] == "install" for c in mgr.calls), (extra, mgr.calls)


@lite
def test_a_first_build_that_saves_nothing_reports_the_missing_files(tmp_path):
    """No earlier files to keep: the build fails, the menu names the missing libraries and installs nothing."""
    lib = _library(tmp_path)
    (lib / "PcbLib" / "SOIC8.PcbLib").unlink()
    (lib / "SchLib" / "NE555.SchLib").unlink()
    mgr, shown = Manager(), []
    pas, dblib = _menu(lib, "nothing", mgr, shown)
    extra = pas.call("Reinstall", "job")
    assert "these library files are not there" in extra and "SOIC8.PcbLib" in extra and "NE555.SchLib" in extra, extra
    assert pas.call("BuildFailed", extra) and mgr.calls == [], mgr.calls


@lite
def test_no_report_from_the_build_is_a_failure(tmp_path):
    """The build script produced no output at all (no report): not ready, nothing installed, old files kept."""
    lib = _library(tmp_path)
    mgr, shown = Manager(), []
    pas, dblib = _menu(lib, "ok", mgr, shown)
    pas.builtins["buildlibraries"] = lambda folder: None
    extra = pas.call("Reinstall", "job")
    _old_files_intact(lib)
    assert "wrote no ecadlib_build_report.txt" in extra and pas.call("BuildFailed", extra), extra
    assert mgr.calls == [], mgr.calls


@lite
def test_a_build_altium_stops_part_way_puts_the_old_file_back(tmp_path):
    """A script error in Altium ends BuildLibraries after it moved a library aside and before any report (seen in
    AD26 with a bad number in a recipe line): the menu puts the old file back and installs nothing."""
    lib = _library(tmp_path)
    mgr, shown = Manager(), []
    pas, dblib = _menu(lib, "ok", mgr, shown)
    pcb = lib / "PcbLib" / "SOIC8.PcbLib"
    pas.builtins["buildlibraries"] = lambda folder: os.rename(pcb, str(pcb) + ".ecadlib_old")
    extra = pas.call("Reinstall", "job")
    _old_files_intact(lib)
    assert "previous library files were put back" in extra and pas.call("BuildFailed", extra), extra
    assert mgr.calls == [], mgr.calls


@lite
def test_a_good_build_replaces_the_files_and_installs(tmp_path):
    lib = _library(tmp_path)
    mgr, shown = Manager(), []
    pas, dblib = _menu(lib, "ok", mgr, shown)
    (lib / "PcbLib" / "SOIC8.PcbLib.ecadlib_old").write_bytes(b"STALE")       # left by an earlier run
    extra = pas.call("Reinstall", "job")
    assert (lib / "PcbLib" / "SOIC8.PcbLib").read_bytes() == b"NEW PCBLIB"
    assert (lib / "SchLib" / "NE555.SchLib").read_bytes() == b"NEW SCHLIB"
    assert not list(lib.rglob("*.ecadlib_old"))
    assert not pas.call("BuildFailed", extra) and ("install", dblib) in mgr.calls, (extra, mgr.calls)
    report = (lib / "ecadlib_build_report.txt").read_text(encoding="utf-8").splitlines()
    assert [ln.split("\t")[0] for ln in report] == ["OK", "OK"], report


@lite
def test_a_build_cut_short_last_time_gets_its_old_file_back_first(tmp_path):
    """Altium was closed mid-build: only NAME.ecadlib_old is there. The next build starts from it, and if that build
    fails too the file is back under its own name."""
    lib = _library(tmp_path)
    pcb = lib / "PcbLib" / "SOIC8.PcbLib"
    os.rename(pcb, str(pcb) + ".ecadlib_old")
    mgr, shown = Manager(), []
    pas, dblib = _menu(lib, "nothing", mgr, shown)
    pas.call("Reinstall", "job")
    _old_files_intact(lib)


def test_the_build_never_deletes_the_old_library_and_the_dialog_does_not_say_ready():
    text = AN.PAS
    for fn in ("Function BuildPcbLib", "Function BuildSchLib"):
        body = text[text.index(fn):text.index("End;\n\n", text.index("Result := EbFinish", text.index(fn)))]
        assert "DeleteFile(Path)" not in body, fn
        assert body.index("EbSetAside(Path)") < body.index("OpenNewDocument"), fn
    dialog = AF.dialog_pas_text()
    assert "If BuildFailed(Extra) Then" in dialog and "Altium could not build the library" in dialog
