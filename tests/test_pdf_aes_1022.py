"""An AES-encrypted datasheet with no open password (AQY282GS in the kicad-v1 benchmark) is read like any other: pypdf
needs the cryptography package for AES, so ecadlib depends on pypdf[crypto]. Without it page_texts gave None and the
part's pages were never picked (seen on a fresh venv, 2026-10-22).   python3 -m tests.test_pdf_aes_1022"""
import io
from pathlib import Path

from ecadlib import ds_route as R
from tests import test_ds_route as TR


def aes(data, algorithm="AES-128"):
    import pypdf
    w = pypdf.PdfWriter(clone_from=pypdf.PdfReader(io.BytesIO(data)))
    w.encrypt(user_password="", owner_password="maker-only", algorithm=algorithm)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def test_an_aes_pdf_with_no_open_password_is_read():
    plain = TR.pdf_lines([["AQY282GS PhotoMOS"], ["SOP4 package dimensions"]])
    for alg in ("AES-128", "AES-256"):
        data = aes(plain, alg)
        assert b"/Encrypt" in data
        texts = R.page_texts(data)
        assert texts is not None and "SOP4" in texts[1], (alg, texts)


def test_pypdf_is_installed_with_its_crypto_extra():
    try:
        import tomllib
    except ImportError:                              # Python 3.10
        import tomli as tomllib
    deps = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())["project"]["dependencies"]
    assert any(d.replace(" ", "").startswith("pypdf[crypto]") for d in deps), deps


if __name__ == "__main__":
    for k, v in list(globals().items()):
        if k.startswith("test_"):
            v()
            print("ok", k)
