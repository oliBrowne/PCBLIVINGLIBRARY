"""kicad-export --project: the lib-table rows and the footprints' 3D model paths start from the same project-relative
folder (${KIPRJMOD}/ecadlib/ for the KiCad window's <project>/ecadlib/), so a project using the rows finds both."""
import tempfile
from pathlib import Path

from ecadlib import kicad_export as X


def test_project_lib_table_and_model_paths_share_one_base():
    from tests import test_kicad_export as T
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        plan, _, http = T._plan(d)
        proj = d / "board"
        proj.mkdir()
        out = proj / "ecadlib"
        rep = X.export(plan, out, "ecadlib", http=http, log=lambda *a: None, project=proj)
        assert rep["models_at"] == "${KIPRJMOD}/ecadlib/ecadlib.3dshapes"
        assert '(uri "${KIPRJMOD}/ecadlib/ecadlib.pretty")' in (out / "fp-lib-table").read_text()
        assert '(uri "${KIPRJMOD}/ecadlib/ecadlib.kicad_sym")' in (out / "sym-lib-table").read_text()
        X.export(plan, d / "flat", "ecadlib", http=http, log=lambda *a: None)          # no project: as before
        assert '(uri "${KIPRJMOD}/ecadlib.pretty")' in (d / "flat" / "fp-lib-table").read_text()
