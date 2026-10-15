"""Crash-safe file writes for files that hold people's work (plan.json, librarian_state.json, batch state).

write_text_atomic writes UTF-8 to a temp file next to the target (unique per process and thread, so two writers never
share one), flushes it to disk and moves it over the target with os.replace: a crash or a full disk leaves the old file
whole instead of a truncated one that no later command can read."""
import os, threading
from pathlib import Path


def write_text_atomic(path, text):
    path = Path(path)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path
