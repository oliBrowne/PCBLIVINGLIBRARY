# PyInstaller spec for the Windows app (built by .github/workflows/windows.yml; run from the repository root):
#   python installer/windows/make_icon.py build/ecadlib.ico
#   pyinstaller --noconfirm installer/windows/ecadlib.spec
# -> dist/ecadlib/ with ecadlib-app.exe (no console: the desktop app) and ecadlib.exe (the command line), sharing one
# Python and one set of libraries. Every ecadlib submodule is included because cli.py imports commands by name.
import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

root = os.path.abspath(os.path.join(SPECPATH, "..", ".."))
icon = os.path.join(root, "build", "ecadlib.ico")
icon = icon if os.path.isfile(icon) else None
hidden = collect_submodules("ecadlib") + ["PIL.Image", "pypdf"]
datas = collect_data_files("ecadlib", include_py_files=True, excludes=["**/__pycache__"])
excludes = ["pytest", "IPython", "matplotlib", "notebook", "jupyter_client", "scipy"]


def analysis(script):
    return Analysis([os.path.join(SPECPATH, script)], pathex=[root], datas=datas, hiddenimports=hidden,
                    excludes=excludes, noarchive=False)


a_app, a_cli = analysis("entry_app.py"), analysis("entry_cli.py")
exe_app = EXE(PYZ(a_app.pure), a_app.scripts, [], exclude_binaries=True, name="ecadlib-app", console=False,
              icon=icon, upx=False)
exe_cli = EXE(PYZ(a_cli.pure), a_cli.scripts, [], exclude_binaries=True, name="ecadlib", console=True,
              icon=icon, upx=False)
coll = COLLECT(exe_app, a_app.binaries, a_app.datas, exe_cli, a_cli.binaries, a_cli.datas, name="ecadlib", upx=False)
