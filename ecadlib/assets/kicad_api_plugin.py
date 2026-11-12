"""ecadlib's button in KiCad 9 and newer, through KiCad's plugin system (the KiCad API: Preferences > Plugins >
Enable KiCad API). KiCad starts this file when the ecadlib button in the schematic editor's top toolbar is pressed
(and in the PCB editor's from KiCad 11, which no longer loads the older PCB-editor plugins). It opens the same
ecadlib window as the PCB editor's button (ecadlib_window.py, a copy of kicad_action_plugin.py) for the project KiCad
has open: KiCad passes that project's folder as KIPRJMOD. Pressing the button again while a window is open starts
another window (a process of its own); finish one job before starting another on the same project.

Written into KiCad's plugins folder (<KiCad documents folder>/N.0/plugins/ecadlib/) with plugin.json next to it by
the setup page's "Add to KiCad" or `ecadlib kicad-menu add-to-schematic`. On Windows and macOS KiCad runs it with
its own Python in a virtual environment made with --system-site-packages, so KiCad's wxPython is there (requirements.txt
asks pip for nothing); on Linux KiCad runs the launcher ecadlib_kicad.sh, which starts it with the Python KiCad is set
to use. It does not import ecadlib: the window starts the ecadlib command saved in ecadlib_cmd.json, as the PCB editor's button does."""
import os, sys, traceback

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    os.environ["ECADLIB_KICAD_API_PLUGIN"] = "1"         # the window: no pcbnew, no board to place on
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    import wx
    app = wx.App(False)
    try:
        import ecadlib_window
        w = ecadlib_window.open_window(sys.argv[1] if len(sys.argv) > 1 else "home")
    except Exception:                                    # shown, never lost in a log nobody reads
        wx.MessageBox("ecadlib could not open its window:\n\n" + traceback.format_exc()[-1500:], "ecadlib")
        return 1
    if w is not None:
        app.MainLoop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
