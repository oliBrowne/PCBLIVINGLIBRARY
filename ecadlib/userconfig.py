"""Per-user settings and API keys, for people who set ecadlib up from the setup page instead of a terminal.

    ecadlib keys list                  which keys are stored (names only) and which only the environment has
    ecadlib keys set NAME              asks for the value (hidden), stores it for this user
    ecadlib keys remove NAME

Where: a folder of this user's own, never the workspace or a repository:
  Windows  %APPDATA%\\ecadlib\\          macOS  ~/Library/Application Support/ecadlib/
  Linux    $XDG_CONFIG_HOME/ecadlib/ (~/.config/ecadlib/)     ECADLIB_HOME overrides all three (tests use it)
  settings.json   the setup page's answers: workspace folder, tool, library folders, set-up done, what was installed
  keys.json       stored API keys: on Windows each value is encrypted for this Windows account (DPAPI, the same store
                  browsers use), elsewhere the file is readable by this user only (mode 600), like ~/.netrc

How keys are used: ecadlib reads a key from this file when it needs it (secret(name)), and from an environment
variable of the same name only when nothing is stored. It never copies a key into the environment, never sets one for
the user (no setx, no shell profile) and never needs one, so other programs (a coding assistant, a build tool, any
child process ecadlib starts) cannot pick a stored key up. A key value is never printed, logged, returned by the web
UI or written anywhere else.

apply_env() puts KiCad's bin folder on PATH when kicad-cli is not found there but a KiCad install is (the
Windows installer of KiCad does not add it to PATH)."""
import argparse, base64, getpass, json, os, re, sys, tempfile, threading
from pathlib import Path

SETTINGS, KEYS = "settings.json", "keys.json"
NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
MAX_KEY = 4096
NO_STORED = "ECADLIB_NO_STORED_KEYS"          # set (the eval gate, golden builds): stored keys are not read
_WRITE = threading.RLock()                    # read-modify-write of settings.json / keys.json: the web UI's request
                                              # threads (a key saved while setup is saved) never drop each other's change


def config_dir(environ=None):
    environ = os.environ if environ is None else environ
    if environ.get("ECADLIB_HOME"):
        return Path(environ["ECADLIB_HOME"])
    if os.name == "nt":
        return Path(environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "ecadlib"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "ecadlib"
    return Path(environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "ecadlib"


def documents_dir():
    """The user's Documents folder (Windows: the real one, also when it was moved to OneDrive)."""
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes
            buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0 and buf.value:   # CSIDL_PERSONAL
                return Path(buf.value)
        except Exception:
            pass
    d = Path.home() / "Documents"
    return d if d.is_dir() or os.name == "nt" or sys.platform == "darwin" else Path.home()


def default_workspace():
    return documents_dir() / "ecadlib"


def _write_private(path, text):
    """Write a file only this user can read (atomic replace; mode 600 where the OS has modes)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix="." + path.name + ".")
    try:
        if os.name != "nt":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_json(path):
    try:
        v = json.loads(path.read_text(encoding="utf-8"))
        return v if isinstance(v, dict) else {}
    except (OSError, ValueError):
        return {}


def damaged(path):
    """Why an existing keys.json / settings.json can't be read ("not valid JSON (line 3)"), or None (fine or absent)."""
    try:
        v = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except OSError as e:
        return f"unreadable ({e.strerror or e})"
    except ValueError as e:
        return f"not valid JSON (line {getattr(e, 'lineno', '?')})"
    return None if isinstance(v, dict) else "not a JSON object"


def _read_for_write(path):
    """The file's dict before a change is written back. A damaged file is copied aside first (<name>.damaged-<time>),
    with a warning, so a one-character slip never silently loses every stored key."""
    why = damaged(path)
    if why:
        import shutil, time
        keep = path.with_name(f"{path.name}.damaged-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(path, keep)
        if os.name != "nt":
            os.chmod(keep, 0o600)
        print(f"warning: {path} was {why}; it is kept as {keep.name} and a new file is written", file=sys.stderr)
        return {}
    return _read_json(path)


# ------------------------------------------------------------------ settings
def load_settings(environ=None):
    return _read_json(config_dir(environ) / SETTINGS)


def save_settings(changes, environ=None):
    """Merge `changes` into settings.json; returns the new settings."""
    with _WRITE:
        s = _read_for_write(config_dir(environ) / SETTINGS)
        s.update(changes)
        _write_private(config_dir(environ) / SETTINGS, json.dumps(s, indent=2) + "\n")
    return s


# ------------------------------------------------------------------ keys
def _dpapi(data, protect):
    """CryptProtectData / CryptUnprotectData for this Windows account (no prompt, no entropy)."""
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
    buf = ctypes.create_string_buffer(data, len(data))
    src, dst = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), Blob()
    fn = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    if not fn(ctypes.byref(src), None, None, None, None, 0x1, ctypes.byref(dst)):     # CRYPTPROTECT_UI_FORBIDDEN
        raise OSError(f"Windows could not {'encrypt' if protect else 'decrypt'} the key (error {ctypes.GetLastError()})")
    try:
        return ctypes.string_at(dst.pbData, dst.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(dst.pbData)


def _encode(value):
    raw = value.encode("utf-8")
    if os.name == "nt":
        return "dpapi:" + base64.b64encode(_dpapi(raw, True)).decode("ascii")
    return "plain:" + base64.b64encode(raw).decode("ascii")


def _decode(stored):
    kind, _, b64 = str(stored).partition(":")
    raw = base64.b64decode(b64)
    if kind == "dpapi":
        if os.name != "nt":
            raise OSError("this key was stored on Windows and can only be read there")
        raw = _dpapi(raw, False)
    elif kind != "plain":
        raise ValueError(f"unknown key format {kind!r}")
    return raw.decode("utf-8")


def check_name(name):
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise ValueError(f"{name!r} is not an environment variable name (letters, digits and _)")
    return name


def stored_names(environ=None):
    return sorted(_read_json(config_dir(environ) / KEYS))


def set_key(name, value, environ=None):
    check_name(name)
    value = (value or "").strip()
    if not value:
        raise ValueError("the key is empty")
    if len(value) > MAX_KEY or any(c in value for c in "\r\n\0"):
        raise ValueError("that does not look like an API key (too long, or more than one line)")
    f = config_dir(environ) / KEYS
    with _WRITE:
        keys = _read_for_write(f)
        keys[name] = _encode(value)
        _write_private(f, json.dumps(keys, indent=2) + "\n")
    _CACHE.clear()                                    # a same-size rewrite inside one clock tick keeps the old stamp


def remove_key(name, environ=None):
    f = config_dir(environ) / KEYS
    with _WRITE:
        keys = _read_json(f)
        if keys.pop(check_name(name), None) is None:
            return False
        _write_private(f, json.dumps(keys, indent=2) + "\n")
    _CACHE.clear()
    return True


_CACHE = {}


def get_key(name, environ=None):
    """The stored value, or None (unreadable entries are skipped)."""
    f = config_dir(environ) / KEYS
    try:
        st = f.stat()
        stamp = (str(f), st.st_mtime_ns, st.st_size)
    except OSError:
        return None
    vals = _CACHE.get("values") if _CACHE.get("stamp") == stamp else None
    if vals is None:                                  # decrypt once per change of keys.json, not on every request
        vals = {}                                     # (a local: a set_key clearing _CACHE meanwhile can't break it)
        _CACHE.update(stamp=stamp, values=vals)
    if name not in vals:
        stored = _read_json(f).get(name)
        try:
            vals[name] = None if stored is None else _decode(stored)
        except (OSError, ValueError):
            vals[name] = None
    return vals[name]


def secret(name, environ=None):
    """A key or token ecadlib needs: the one stored for this user (`ecadlib keys set`, the setup page), else an
    environment variable of that name (environ: that environment, for tests), else None. Read when needed; never
    copied into the environment."""
    env = os.environ if environ is None else environ
    if not name:
        return None
    if name == "NEXAR_TOKEN" and not os.environ.get(NO_STORED):
        try:                                          # signed in with the user's own Nexar app: a fresh token
            from . import nexar_signin
            v = nexar_signin.access_token()
        except Exception:
            v = None
        if v:
            return v
    try:
        v = None if os.environ.get(NO_STORED) else get_key(name)
    except Exception:                                 # a broken config folder must not stop any command
        v = None
    return v or env.get(name) or None


def key_source(name, environ=None):
    """Where secret(name) comes from: "stored", "environment" or None (names only, never the value)."""
    env = os.environ if environ is None else environ
    try:
        if not os.environ.get(NO_STORED) and (get_key(name) or name == "NEXAR_TOKEN" and get_key("NEXAR_SIGNIN")):
            return "stored"
    except Exception:
        pass
    return "environment" if env.get(name) else None


def apply_env(environ=None, config_environ=None):
    """Puts KiCad's bin folder on PATH when kicad-cli is missing there but KiCad is installed. Stored keys are NOT put
    into the environment (read them with secret()); returns [] (the names of keys set, kept for old callers).
    Never raises: a broken config folder must not stop any command."""
    environ = os.environ if environ is None else environ
    try:
        import shutil
        if not shutil.which("kicad-cli", path=environ.get("PATH")):
            from . import detect
            cli = detect.kicad_cli()
            if cli:
                environ["PATH"] = environ.get("PATH", "") + os.pathsep + str(Path(cli).parent)
    except Exception:
        pass
    return []


# ------------------------------------------------------------------ how to run ecadlib from another program
def self_command():
    """The command line that runs this ecadlib: the bundled ecadlib.exe in the Windows app, else this Python."""
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable)
        cli = exe.with_name("ecadlib.exe" if os.name == "nt" else "ecadlib")
        return [str(cli if cli.is_file() else exe)]
    return [sys.executable, "-m", "ecadlib"]


def key_hint(name):
    """How to set a key, the same on every OS."""
    return f"add it on the setup page (`ecadlib setup`), or run: ecadlib keys set {name}"


# ------------------------------------------------------------------ ecadlib keys
def main(argv=None, inp=None, out=print):
    ap = argparse.ArgumentParser(prog="ecadlib keys", description="API keys stored for this user (names only are shown)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="stored keys (names only)")
    s = sub.add_parser("set", help="store a key (asked for, hidden); ecadlib uses it before any environment variable")
    s.add_argument("name", help="the variable name, e.g. ANTHROPIC_API_KEY")
    s.add_argument("--stdin", action="store_true", help="read the value from standard input (scripts)")
    r = sub.add_parser("remove", help="forget a stored key")
    r.add_argument("name")
    a = ap.parse_args(argv)
    try:
        return _keys(a, inp, out)
    except (ValueError, EOFError) as e:          # an empty key, a bad name, no input: the user's to fix, not a bug
        print(f"ecadlib keys {a.cmd}: {str(e) or 'no input (nothing was typed)'}", file=sys.stderr)
        return 2


def _keys(a, inp, out):
    if a.cmd == "list":
        names = stored_names()
        out(f"stored in {config_dir() / KEYS}" + (" (encrypted for this Windows account)" if os.name == "nt" else ""))
        why = damaged(config_dir() / KEYS)
        if why:
            out(f"  {KEYS} is {why}: fix it by hand, or `ecadlib keys set` writes a new one (the old one is kept)")
            return 1
        if not names:
            out("  none stored")
        for n in names:
            env = "also set in the environment; ecadlib uses the stored one" if os.environ.get(n) and os.environ.get(n) != get_key(n) else ""
            out(f"  {n}" + (f"   ({env})" if env else ""))
        out("ecadlib reads these itself and never puts them in the environment, so other programs cannot use them.")
        return 0
    if a.cmd == "set":
        check_name(a.name)
        if a.stdin:
            value = sys.stdin.readline()
        else:
            value = (inp or getpass.getpass)(f"{a.name} (input hidden): ")
        set_key(a.name, value)
        out(f"stored {a.name} for this user; every ecadlib command uses it from now on (only ecadlib reads it)")
        return 0
    if remove_key(a.name):
        out(f"removed {a.name}")
        return 0
    out(f"{a.name} was not stored")
    return 1


if __name__ == "__main__":
    sys.exit(main())
