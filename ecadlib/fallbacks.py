"""Fallbacks that change what a run does, said out loud instead of carried on silently.

Where a setting or helper fails and ecadlib keeps going with a default (a model setting that does not resolve, a
house-style parameter name that does not load), the handler calls `note()`. The warning goes to stderr at once and
is kept until the next review: the librarian's review.md / review.html lead with it ("Settings that fell back to a
default"). Handlers that only affect how something is shown, or that record the failure in the part's own report,
do not need this."""
import sys
import threading

_LOCK = threading.Lock()
_NOTES = []


def note(what, exc=None, instead=""):
    """Record one fallback: what failed, the exception (type and text) and what is used instead. Returns the line.
    The same line is printed and kept once, however often the handler runs."""
    msg = what + (f" ({type(exc).__name__}: {exc})" if exc is not None else "") + (f"; using {instead} instead" if instead else "")
    with _LOCK:
        if msg in _NOTES:
            return msg
        _NOTES.append(msg)
    print(f"ecadlib: warning: {msg}", file=sys.stderr)
    return msg


def peek():
    with _LOCK:
        return list(_NOTES)


def take():
    """The fallbacks noted since the last take(), and forget them (each review reports what happened since the last)."""
    with _LOCK:
        out = list(_NOTES)
        _NOTES.clear()
    return out
