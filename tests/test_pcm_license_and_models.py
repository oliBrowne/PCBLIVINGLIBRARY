"""kicad-pcm: the package licence is CC-BY-SA 4.0 only when the library holds KiCad library content (the house rule:
the CC-BY-SA label only on KiCad content), a neutral licence from KiCad's PCM list otherwise; --license overrides.
footprint_text points a model path at the installed 3D models whatever folder the export wrote it from."""
import json, tempfile
from pathlib import Path

from ecadlib import kicad_export as X, kicad_pcm as KP
from tests import test_kicad_house_rules as HR, test_librarian as TL


def _export(d):
    http = TL.FakeHTTP({"C2975360": HR._xtal_ee()})
    TL.run(d, http, bom=HR.BOM)
    rep = X.export(Path(d) / "out" / "plan.json", Path(d) / "kicad", "lib", include_held=True, http=http,
                   log=lambda *a: None)
    assert not rep["kicad_content"]                        # one EasyEDA part: nothing from the KiCad libraries
    return Path(d) / "kicad"


def test_license_default_follows_kicad_content():
    assert KP.NEUTRAL_LICENSE in KP.LICENSES and KP.NEUTRAL_LICENSE != "CC-BY-SA-4.0"
    with tempfile.TemporaryDirectory() as d:
        exp = _export(d)
        KP.build(exp, Path(d) / "pcm", name="lib", include_held=True, log=lambda *a: None)
        assert json.loads((Path(d) / "pcm" / "metadata.json").read_text())["license"] == KP.NEUTRAL_LICENSE
        KP.build(exp, Path(d) / "pcm2", name="lib", include_held=True, license_="MIT", log=lambda *a: None)
        assert json.loads((Path(d) / "pcm2" / "metadata.json").read_text())["license"] == "MIT"   # explicit wins


def test_license_default_cc_by_sa_with_kicad_content():
    from tests import test_kicad_export as T
    with tempfile.TemporaryDirectory() as d:
        plan, _, http = T._plan(Path(d))
        rep = X.export(plan, Path(d) / "kicad", "ecadlib", http=http, log=lambda *a: None)
        assert rep["kicad_content"]
        KP.build(Path(d) / "kicad", Path(d) / "pcm", name="ecadlib", log=lambda *a: None)
        assert json.loads((Path(d) / "pcm" / "metadata.json").read_text())["license"] == "CC-BY-SA-4.0"


def test_cli_license_default_is_not_cc_by_sa():
    import unittest.mock as m
    seen = []

    def fake_build(*a, **k):
        seen.append(a[7])
        raise ValueError("stop")
    with m.patch.object(KP, "build", fake_build):
        KP.main(["plan.json", "--out", "x"])
        KP.main(["plan.json", "--out", "x", "--license", "MIT"])
    assert seen == [None, "MIT"], seen                      # no --license: build picks it from the library's content


def test_absolute_model_paths_rewritten():
    for path in ("D:/libs/out/lib.3dshapes/", "D:\\libs\\out\\lib.3dshapes\\", "D:\\\\libs\\\\lib.3dshapes\\\\",
                 "/home/me/x/lib.3dshapes/",
                 "${KIPRJMOD}/lib.3dshapes/", "${KIPRJMOD}/ecadlib/lib.3dshapes/"):
        text = f'(footprint "F"\n\t(model "{path}F.step"\n\t)\n)\n'
        got = KP.footprint_text(text, "lib", "KICAD9_3RD_PARTY", "com.x", 9)
        assert '(model "${KICAD9_3RD_PARTY}/3dmodels/com.x/lib.3dshapes/F.step"' in got, (path, got)
    other = '(footprint "F"\n\t(model "D:/libs/other.3dshapes/F.step"\n\t)\n)\n'
    assert KP.footprint_text(other, "lib", "KICAD9_3RD_PARTY", "com.x", 9) == other     # another library's: kept
