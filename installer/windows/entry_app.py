"""ecadlib-app.exe in the Windows app (no console): what the Start menu entry runs.

    ecadlib-app.exe                 the desktop app (ecadlib.launcher): starts ecadlib, opens the browser
    ecadlib-app.exe --setup         the same, opening the setup page
    ecadlib-app.exe COMMAND ...     any ecadlib command, without a console (the Altium menu starts `ui` this way)

A windowed program has no stdout / stderr: they go to %APPDATA%\\ecadlib\\app.log (the last run only)."""
import multiprocessing, os, sys


def _log():
    if sys.stdout is not None and sys.stderr is not None:
        return
    from ecadlib.userconfig import config_dir
    d = config_dir()
    d.mkdir(parents=True, exist_ok=True)
    f = open(d / "app.log", "w", encoding="utf-8", buffering=1)
    sys.stdout = sys.stdout or f
    sys.stderr = sys.stderr or f


if __name__ == "__main__":
    multiprocessing.freeze_support()
    _log()
    args = sys.argv[1:]
    if not args or args[0].startswith("--"):
        from ecadlib.launcher import main
        sys.exit(main(args))
    from ecadlib.cli import main
    sys.exit(main(args))
