"""Crash-safe file writes for files that hold people's work (plan.json, librarian_state.json, batch state).

write_text_atomic writes UTF-8 to a temp file next to the target (unique per process and thread, so two writers never
share one), flushes it to disk and moves it over the target with os.replace: a crash or a full disk leaves the old file
whole instead of a truncated one that no later command can read.

On Windows a rename over a file that another process has open (the web UI reading the plan, a virus scanner, a
search indexer) fails with PermissionError for as long as it is open, where the old in-place write worked: replace()
retries for a few seconds before giving up."""
import os, threading, time
from pathlib import Path

RETRY = os.name == "nt"              # only Windows refuses a rename over an open file; elsewhere it is a real error
DELAYS = (0.05, 0.1, 0.2, 0.4, 0.8, 1.6)


def replace(src, dst):
    """os.replace(src, dst), retried on Windows while dst (or src) is held open by another process."""
    for delay in DELAYS + (None,):
        try:
            return os.replace(src, dst)
        except PermissionError:
            if not RETRY or delay is None:
                raise
            time.sleep(delay)


def write_text_atomic(path, text):
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
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
