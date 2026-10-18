"""Draw ecadlib.ico (the installer, the Start menu entry and both .exe files): the Sprout Trace mark on a dark tile
(ecadlib/brand.py), drawn separately at every size Windows asks for so small sizes stay crisp. Needs Pillow.
Run: python installer/windows/make_icon.py OUT.ico"""
import sys
from pathlib import Path
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ecadlib.brand import mark_rgba  # noqa: E402


def draw(n):
    return Image.frombytes("RGBA", (n, n), b"".join(mark_rgba(n, tile=True)))


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "ecadlib.ico"
    sizes = [16, 24, 32, 48, 64, 128, 256]
    big = draw(256)
    big.save(out, sizes=[(n, n) for n in sizes], append_images=[draw(n) for n in sizes[:-1]])
    print(f"wrote {out}")
