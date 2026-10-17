"""The desktop app: what the Windows Start menu entry (and `ecadlib start`) runs.

    ecadlib start [--workspace W] [--port 8765] [--setup] [--no-window] [--no-browser]

Starts the web UI in the background on 127.0.0.1 (local mode, no sign-in) and opens it in the default browser: the
setup page the first time (ecadlib.easy_setup), the main page after. A small window says ecadlib is running, with
Open / Setup / Workspace folder / Quit; closing it stops ecadlib. Without a display (or --no-window) it runs until
Ctrl+C instead.

The workspace is --workspace, else the one picked on the setup page (settings.json), else Documents/ecadlib. When an
ecadlib is already answering on the port, the browser is pointed at it and nothing new starts, so a second click on
the Start menu entry just brings the page back. When the setup page moves the workspace, the server restarts there on
the same port (easy_setup.RESTART)."""
import argparse, os, sys, threading, webbrowser
from pathlib import Path

from . import __version__, easy_setup as ES, userconfig as UC


class App:
    def __init__(self, workspace, port, host="127.0.0.1"):
        self.host, self.port, self.workspace = host, port, str(workspace)
        self.srv, self.thread, self.lock = None, None, threading.Lock()
        self.on_change = lambda: None

    @property
    def url(self):
        return f"http://{self.host}:{self.port}/"

    def start(self):
        from .app import Workspace
        from .ui import server as S
        Path(self.workspace).mkdir(parents=True, exist_ok=True)
        ws = Workspace(self.workspace)
        self.srv = S.make_server(ws, self.host, self.port)
        self.port = self.srv.server_address[1]
        self.thread = threading.Thread(target=self.srv.serve_forever, name="ecadlib-ui", daemon=True)
        self.thread.start()

    def stop(self):
        if self.srv:
            self.srv.shutdown()
            self.srv.server_close()
            self.srv = None

    def restart(self, workspace):
        """Called from a request: answer first, then move the server (another thread, after a moment)."""
        def go():
            with self.lock:
                old = self.workspace
                self.stop()
                self.workspace = str(workspace)
                try:
                    self.start()
                except Exception as e:                  # the new folder failed: keep ecadlib running where it was
                    print(f"could not move to {workspace}: {e}", file=sys.stderr)
                    self.workspace = old
                    self.start()
            self.on_change()
        threading.Timer(0.4, go).start()


def free_port(port, host="127.0.0.1", tries=20):
    """(port, already running ecadlib's workspace or None): the first port that is ours or free."""
    import socket
    from .altium_menu import probe_ui
    for p in range(port, port + tries):
        what, ws = probe_ui(p, host, timeout=1.0)
        if what == "ecadlib":
            return p, ws
        if what is None:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                try:
                    s.bind((host, p))
                    return p, None
                except OSError:
                    continue
    raise OSError(f"no free port between {port} and {port + tries - 1}")


def window(app, open_page):
    """The small 'ecadlib is running' window. Returns False when there is no display (no tkinter)."""
    try:
        import tkinter as tk
        from tkinter import ttk
        root = tk.Tk()
    except Exception:
        return False
    root.title("ecadlib")
    root.resizable(False, False)
    frm = ttk.Frame(root, padding=16)
    frm.grid()
    ttk.Label(frm, text=f"ecadlib {__version__} is running", font=("Segoe UI", 11, "bold")).grid(column=0, row=0, columnspan=4, sticky="w")
    where = tk.StringVar(value=f"{app.url}\nworkspace: {app.workspace}")
    ttk.Label(frm, textvariable=where, foreground="#555").grid(column=0, row=1, columnspan=4, sticky="w", pady=(4, 12))
    app.on_change = lambda: root.after(0, lambda: where.set(f"{app.url}\nworkspace: {app.workspace}"))

    def open_folder():
        p = app.workspace
        if os.name == "nt":
            os.startfile(p)                                         # noqa: S606 (the user's own folder)
        else:
            webbrowser.open(Path(p).as_uri())

    ttk.Button(frm, text="Open ecadlib", command=lambda: open_page("")).grid(column=0, row=2, padx=(0, 6))
    ttk.Button(frm, text="Setup", command=lambda: open_page("setup")).grid(column=1, row=2, padx=(0, 6))
    ttk.Button(frm, text="Workspace folder", command=open_folder).grid(column=2, row=2, padx=(0, 6))
    ttk.Button(frm, text="Quit", command=root.destroy).grid(column=3, row=2)
    ttk.Label(frm, text="Closing this window stops ecadlib.", foreground="#777").grid(column=0, row=3, columnspan=4, sticky="w", pady=(10, 0))
    root.protocol("WM_DELETE_WINDOW", root.destroy)
    root.mainloop()
    return True


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ecadlib start", description="the desktop app: run ecadlib in the background and "
                                 "open it in your browser (the setup page the first time)")
    ap.add_argument("--workspace", help="workspace folder (default: the one picked on the setup page, else Documents/ecadlib)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-window", action="store_true", help="no 'ecadlib is running' window: run until Ctrl+C")
    ap.add_argument("--no-browser", action="store_true", help="do not open the browser")
    ap.add_argument("--setup", action="store_true", help="open the setup page (the 'ecadlib setup' Start menu entry)")
    a = ap.parse_args(argv)
    UC.apply_env()
    settings = UC.load_settings()
    ws = Path(a.workspace or settings.get("workspace") or UC.default_workspace()).expanduser()
    first = "" if settings.get("setup_done") and not a.setup else "setup"
    port, running = free_port(a.port)
    if running:
        print(f"ecadlib is already running on http://127.0.0.1:{port}/ (workspace {running})")
        if not a.no_browser:
            webbrowser.open(f"http://127.0.0.1:{port}/{first}")
        return 0
    app = App(ws, port)
    app.start()
    ES.RESTART = app.restart
    print(f"ecadlib {__version__} on {app.url} (workspace {app.workspace})")

    def open_page(path):
        webbrowser.open(app.url + path)
    if not a.no_browser:
        open_page(first)
    try:
        if a.no_window or not window(app, open_page):
            print("Ctrl+C stops it")
            threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        ES.RESTART = None
        app.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
