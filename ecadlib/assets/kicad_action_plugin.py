"""ecadlib action plugin for KiCad's PCB editor: "ecadlib: add part".

Installed by KiCad's Plugin and Content Manager from the zip `ecadlib kicad-pcm --plugin` writes (or copied by hand
into KiCad's scripting/plugins folder). It runs in KiCad's own Python, so it does not import ecadlib: it calls the
`ecadlib` command (ECADLIB_CMD if set, else `ecadlib` on PATH, else a python3 that can import ecadlib).

What it does, for the board that is open (it must be saved, so there is a project folder):
  1. asks for a manufacturer part number;
  2. adds it to <project>/ecadlib-work/parts.csv and runs `ecadlib librarian` on that list (parts already built are
     kept), then `ecadlib kicad-export` into <project>/ecadlib/ (ecadlib.kicad_sym, ecadlib.pretty, 3D models);
  3. adds the "ecadlib" rows (${KIPRJMOD}/ecadlib/...) to the project's fp-lib-table and sym-lib-table if missing;
  4. places the part's footprint at the middle of the board, linked as ecadlib:<footprint>.
The symbol is in the ecadlib symbol library for the schematic. Held parts (failed checks) are not placed: the
message says why and `ecadlib resolve` fixes them."""
import csv, json, os, re, shlex, shutil, subprocess, sys

import pcbnew

LIB = "ecadlib"
HEADER = ["Designator", "Qty", "Value", "Footprint", "Manufacturer", "Mfr. Part #", "LCSC Part Number", "Description", "Datasheet"]


def ecadlib_command(environ=None, which=shutil.which, run=subprocess.run):
    """The command line that runs ecadlib, or None."""
    environ = os.environ if environ is None else environ
    if environ.get("ECADLIB_CMD"):
        return shlex.split(environ["ECADLIB_CMD"])
    exe = which("ecadlib")
    if exe:
        return [exe]
    for py in ("python3", "python"):
        p = which(py)
        if p and run([p, "-c", "import ecadlib"], capture_output=True).returncode == 0:
            return [p, "-m", "ecadlib"]
    return None


def add_to_list(project_dir, mpn):
    """Append `mpn` to <project>/ecadlib-work/parts.csv (once). Returns the file."""
    mpn = mpn.strip()
    if not re.match(r"^[A-Za-z0-9][-A-Za-z0-9_.+/#,() ]{0,79}$", mpn):
        raise ValueError(f"not a part number: {mpn!r}")
    work = os.path.join(project_dir, "ecadlib-work")
    os.makedirs(work, exist_ok=True)
    bom = os.path.join(work, "parts.csv")
    rows = []
    if os.path.isfile(bom):
        with open(bom, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    if not any(r.get("Mfr. Part #") == mpn for r in rows):
        rows.append({"Designator": f"P{len(rows) + 1}", "Qty": "1", "Value": mpn, "Mfr. Part #": mpn})
        with open(bom, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, HEADER)
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in HEADER})
    return bom


def commands(project_dir, cmd):
    """The ecadlib runs that build the project's ecadlib library from the part list."""
    work = os.path.join(project_dir, "ecadlib-work")
    out = os.path.join(work, "out")
    return [cmd + ["librarian", os.path.join(work, "parts.csv"), "-o", out, "--cache", os.path.join(work, "http_cache")],
            cmd + ["kicad-export", os.path.join(out, "plan.json"), "-o", os.path.join(project_dir, LIB), "--name", LIB,
                   "--cache", os.path.join(work, "http_cache")]]


def ensure_tables(project_dir):
    """Add the ecadlib rows to the project's fp-lib-table / sym-lib-table (created when missing). Returns changed files."""
    changed = []
    for kind, table, uri in (("fp", "fp_lib_table", f"{LIB}/{LIB}.pretty"), ("sym", "sym_lib_table", f"{LIB}/{LIB}.kicad_sym")):
        path = os.path.join(project_dir, f"{kind}-lib-table")
        row = f'  (lib (name "{LIB}")(type "KiCad")(uri "${{KIPRJMOD}}/{uri}")(options "")(descr "ecadlib parts"))\n'
        text = open(path, encoding="utf-8").read() if os.path.isfile(path) else f"({table}\n  (version 7)\n)\n"
        if re.search(r'\(lib \(name "?' + LIB + r'"?\)', text):
            continue
        body = text.rstrip()
        if not body.endswith(")"):
            raise ValueError(f"{path}: unexpected layout; add this row by hand: {row.strip()}")
        open(path, "w", encoding="utf-8").write(body[:-1].rstrip() + "\n" + row + ")\n")
        changed.append(path)
    return changed


def footprint_for(project_dir, mpn):
    """(footprint name, None) for the part from kicad_export.json, or (None, why)."""
    rep = os.path.join(project_dir, LIB, "kicad_export.json")
    if not os.path.isfile(rep):
        return None, "ecadlib wrote no KiCad library (see ecadlib-work/out/review.html for what was tried)"
    data = json.load(open(rep, encoding="utf-8"))
    names = {mpn}                             # the plan names parts by the library rules (RES-0603-10k, upper case ...),
    plan = os.path.join(project_dir, "ecadlib-work", "out", "plan.json")      # not always by the MPN typed here
    if os.path.isfile(plan):
        for it in json.load(open(plan, encoding="utf-8")):
            if isinstance(it, dict) and any(p.get("name") == "Manufacturer Part Number" and str(p.get("value", "")).strip().upper()
                                            == mpn.strip().upper() for p in it.get("parameters") or [] if isinstance(p, dict)):
                names.add(it.get("name"))
    for p in data.get("parts", []):
        if p.get("part") in names:
            return (p["footprint"], None) if p.get("footprint") else (None, "the part has no footprint yet")
    for s in data.get("skipped", []):
        if s.get("part") in names:
            says = s.get("says") or []
            if says:
                return None, f"it is held. {says[0]['text']}\nNext: {says[0]['fix']}"
            return None, f"held: {s.get('why')} (fix it with `ecadlib resolve`)"
    return None, "ecadlib could not add it (see ecadlib-work/out/review.html for what was tried)"


class EcadlibAddPart(pcbnew.ActionPlugin):
    def defaults(self):
        self.name = "ecadlib: add part"
        self.category = "Library"
        self.description = "Build a part from its manufacturer part number with ecadlib and place its footprint"
        self.show_toolbar_button = False

    def Run(self):
        import wx
        board = pcbnew.GetBoard()
        path = board.GetFileName() if board else ""
        if not path:
            wx.MessageBox("Save the board first: ecadlib puts the library next to the project.\n"
                          "Next: File > Save, then run ecadlib: add part again.", "ecadlib")
            return
        project = os.path.dirname(os.path.abspath(path))
        cmd = ecadlib_command()
        if not cmd:
            wx.MessageBox("ecadlib is not installed where KiCad can find it.\n"
                          "Next: pip install ecadlib, or set ECADLIB_CMD to its command.", "ecadlib")
            return
        dlg = wx.TextEntryDialog(None, "Manufacturer part number:", "ecadlib: add part")
        if dlg.ShowModal() != wx.ID_OK:
            return
        mpn = dlg.GetValue().strip()
        try:
            add_to_list(project, mpn)
        except ValueError as e:
            wx.MessageBox(str(e), "ecadlib")
            return
        busy = wx.BusyInfo(f"ecadlib is building {mpn} ...")
        try:
            for c in commands(project, cmd):
                r = subprocess.run(c, capture_output=True, text=True, cwd=project)
                if r.returncode not in (0, 1):            # 1 = finished with held parts / check failures
                    wx.MessageBox(f"ecadlib could not build {mpn}.\nNext: check the part number; ecadlib's last words "
                                  f"are below.\n\n{(r.stderr or r.stdout)[-1500:]}", "ecadlib")
                    return
        finally:
            del busy
        changed = ensure_tables(project)
        fp_name, why = footprint_for(project, mpn)
        if not fp_name:
            wx.MessageBox(f"{mpn} was not placed: {why}", "ecadlib")
            return
        fp = pcbnew.FootprintLoad(os.path.join(project, LIB, f"{LIB}.pretty"), fp_name)
        fp.SetFPID(pcbnew.LIB_ID(LIB, fp_name))
        box = board.GetBoardEdgesBoundingBox()
        fp.SetPosition(box.GetCenter() if box.GetWidth() else pcbnew.VECTOR2I(0, 0))
        board.Add(fp)
        pcbnew.Refresh()
        note = ("\nThe project's library tables were updated: reopen the project so the editors list the ecadlib "
                "library." if changed else "")
        wx.MessageBox(f"Placed {mpn} ({LIB}:{fp_name}) at the middle of the board. Its symbol is in the {LIB} "
                      f"symbol library.{note}", "ecadlib")


EcadlibAddPart().register()
