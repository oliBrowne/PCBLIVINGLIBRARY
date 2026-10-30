"""Crash-safe file writes for files that hold people's work (plan.json, librarian_state.json, batch state).

write_text_atomic writes UTF-8 to a temp file next to the target (unique per process and thread, so two writers never
share one), flushes it to disk and moves it over the target with os.replace: a crash or a full disk leaves the old file
whole instead of a truncated one that no later command can read.

On Windows a rename over a file that another process has open (the web UI reading the plan, a virus scanner, a
search indexer) fails with PermissionError for as long as it is open, where the old in-place write worked: replace()
retries for a few seconds before giving up."""
import contextlib, os, re, threading, time
from pathlib import Path


def report_file(stem, ext):
    """<stem><ext>: a report stem keeps its dots ('triage_Board_rev1.2_BOM' -> 'triage_Board_rev1.2_BOM.md');
    Path.with_suffix would cut '.2_BOM' off, so rev1.1 and rev1.2 would write one report."""
    stem = Path(stem)
    return stem.with_name(stem.name + ext)


# Windows device names: CON.SchLib (any case, any extension, trailing spaces before the dot) opens the console, not a
# file. Superscript 1-3 count too (COM¹).
DEVICE_NAMES = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
                *(f"{p}{d}" for p in ("COM", "LPT") for d in "0123456789¹²³")}


def portable_stem(stem):
    """A generated file name stem that is no Windows device name: 'AUX' -> 'AUX_', 'nul.x' -> 'nul_.x'; any other
    stem as it is. (Illegal characters and trailing dots are the caller's naming rule.)"""
    stem = str(stem)
    i = stem.find(".")
    i = len(stem) if i < 0 else i
    return stem[:i] + "_" + stem[i:] if stem[:i].rstrip(" ").upper() in DEVICE_NAMES else stem


def plain_stem(name):
    """A part / package name -> the stem of the datasheet answer files (datasheet.write, read back by fp-edit and the
    web UI): letters, digits and _.+- only, never a Windows device name ('COM1' -> 'COM1_')."""
    return portable_stem(re.sub(r"[^A-Za-z0-9_.+-]", "_", str(name)))


RETRY = os.name == "nt"             # only Windows refuses a rename over an open file; elsewhere it is a real error
DELAYS = (0.05, 0.1, 0.2, 0.4, 0.8, 1.6)
TMP = re.compile(r"\..+\.\d+\.\d+\.tmp")   # the temp file name below: .NAME.<pid>.<thread>.tmp


def atomic_tmp(path):
    """Is `path` a temp file of write_text_atomic / write_bytes_atomic (one a killed process left behind, or one being
    written right now)? Never a person's file: a sweep of a folder ecadlib writes leaves it out."""
    return bool(TMP.fullmatch(Path(path).name))


def replace(src, dst):
    """os.replace(src, dst), retried on Windows while dst (or src) is held open by another process."""
    for delay in DELAYS + (None,):
        try:
            return os.replace(src, dst)
        except PermissionError:
            if not RETRY or delay is None:
                raise
            time.sleep(delay)


def _tmp_for(path):
    """The temp file next to `path`: .NAME.<pid>.<thread>.tmp, with a long NAME cut (and a hash of the whole name
    added) so the temp name stays within the 255 characters a file system allows when the target's own name does."""
    path = Path(path)
    name = path.name
    if len(name) > 150:
        import hashlib
        name = f"{name[:100]}~{hashlib.sha1(name.encode('utf-8', 'surrogatepass')).hexdigest()[:8]}"
    return path.with_name(f".{name}.{os.getpid()}.{threading.get_ident()}.tmp")


def write_text_atomic(path, text):
    path = Path(path)
    tmp = _tmp_for(path)
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def write_bytes_atomic(path, data):
    """write_text_atomic for binary files (.PcbLib / .SchLib): a crash mid-write leaves the old file whole."""
    path = Path(path)
    tmp = _tmp_for(path)
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def read_ini_text(path):
    """An INI file Altium saved (.PrjPcb, .DbLib) -> (text, encoding to write it back in): Altium saves them as ANSI
    (cp1252), UTF-8 (with or without BOM) or UTF-16; reading every one as UTF-8 would turn "Müller" into replacement
    characters."""
    data = Path(path).read_bytes()
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16"), "utf-16"
    if data[:3] == b"\xef\xbb\xbf":
        return data[3:].decode("utf-8", "replace"), "utf-8-sig"
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("cp1252"), "cp1252"
    except UnicodeDecodeError:
        return data.decode("latin-1"), "latin-1"      # any byte, written back unchanged


_HELD = threading.local()                # plan.lock folders this thread holds (plan_lock is re-entrant per thread)


# Lock order (two locks guard a librarian plan): the library lock (ledger.locked(<plan folder>), held by change-control
# writers from their begin() to their end, and by publishers) is taken FIRST, <workspace>/plan.lock SECOND, and only
# for the short read-merge-write of plan.json. A plan.lock holder never waits for the library lock: a command that
# needs both takes the library lock before plan.lock (approvals.cli_plan_lock, Workspace._plan_lock with a folder),
# so a librarian run (library lock, then plan.lock) and a web UI / CLI decision can never wait for each other.
@contextlib.contextmanager
def plan_lock(workspace_root, wait=True, timeout=None, name="plan.lock"):
    """<workspace>/plan.lock: every path that records into a plan (the web UI's Approvals page / approve_part, change
    decisions, `ecadlib approve`) takes it while it rewrites plan.json, so two of them never overwrite each other.
    wait=False yields False at once when another thread or process holds it; timeout (seconds, with wait) yields False
    once that long has passed (plan.lock is held only for a short read-merge-write, so a few seconds is plenty).
    Re-entrant in one thread (an inner plan write under an outer lock does not wait for itself); other threads and
    processes are kept out. Released by the OS if the holder dies. See the lock order above. name: another lock file in
    that folder (userconfig's keys.json / settings.json writes)."""
    d = Path(workspace_root).resolve()
    held = _HELD.__dict__.setdefault("dirs", set())
    if d / name in held:
        yield True
        return
    d.mkdir(parents=True, exist_ok=True)
    f = open(d / name, "a+b")
    msvcrt = None
    deadline = None if not wait or timeout is None else time.monotonic() + max(0.0, float(timeout))
    try:
        try:
            while True:
                try:
                    try:
                        import fcntl
                        fcntl.flock(f, fcntl.LOCK_EX | (0 if wait and deadline is None else fcntl.LOCK_NB))
                    except ImportError:                         # Windows
                        import msvcrt
                        _win_lock(msvcrt, f, wait and deadline is None)
                    break
                except OSError:
                    if deadline is None or time.monotonic() >= deadline:
                        raise
                    time.sleep(0.05)
        except OSError:
            if wait and deadline is None:
                raise
            got = False
        else:
            got = True
        if got:
            held.add(d / name)
        try:
            yield got
        finally:
            if got:
                held.discard(d / name)
            if msvcrt is not None and got:                      # Windows frees a lock left on a closed file late
                try:
                    f.seek(0)
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
    finally:
        f.close()


@contextlib.contextmanager
def computer_lock(name):
    """One writer at a time on this computer for a file several plan folders share (this computer's and the project's
    ecadlib_publishers.json): a file lock .<name>.lock in the ecadlib settings folder. A settings folder that cannot
    be written: no lock."""
    from .userconfig import config_dir
    cm = plan_lock(config_dir(), name=f".{re.sub(r'[^A-Za-z0-9_.-]', '_', name)}.lock")
    try:
        cm.__enter__()
    except OSError:
        cm = None
    try:
        yield
    finally:
        if cm is not None:
            cm.__exit__(None, None, None)


def _win_lock(msvcrt, f, wait, poll=0.5):
    """msvcrt's blocking LK_LOCK gives up with OSError after 10 one-second tries: wait by polling the non-blocking lock
    instead."""
    while True:
        f.seek(0)
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            return
        except OSError:
            if not wait:
                raise
        time.sleep(poll)
