"""Write the ecadlib mark's files from ecadlib/brand.py: the site favicon and the brand kit in site/brand/.
Run after changing ecadlib/brand.py: python scripts/build_brand.py (tests/test_brand.py checks the files match)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ecadlib import brand as B  # noqa: E402


def files():
    """Published path (under site/) -> bytes."""
    return {
        "favicon.svg": B.mark_svg(adaptive=True).encode(),
        "brand/ecadlib-mark.svg": B.mark_svg().encode(),
        "brand/ecadlib-mark-on-dark.svg": B.mark_svg(B.INK_DARK, B.COPPER_DARK).encode(),
        "brand/ecadlib-mark-one-colour.svg": B.mark_svg(B.INK, B.INK).encode(),
        "brand/ecadlib-tile-64.png": B.mark_png(64),
        "brand/ecadlib-tile-256.png": B.mark_png(256),
    }


if __name__ == "__main__":
    for rel, data in files().items():
        p = ROOT / "site" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        print(f"wrote site/{rel}")
