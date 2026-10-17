"""Draw ecadlib.ico (the installer, the Start menu entry and both .exe files): a QFN footprint in hairline style,
ink on white, at every size Windows asks for. Needs Pillow. Run: python installer/windows/make_icon.py OUT.ico"""
import sys
from PIL import Image, ImageDraw


def draw(n):
    s = 4 * n                                      # draw large, scale down: smooth 1px lines
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    r = s // 6
    d.rounded_rectangle((0, 0, s - 1, s - 1), r, fill=(35, 35, 39, 255))
    ink, w = (255, 255, 255, 255), max(4, s // 40)
    body = (s * 0.3, s * 0.3, s * 0.7, s * 0.7)
    d.rectangle(body, outline=ink, width=w)
    d.rectangle((s * 0.4, s * 0.4, s * 0.6, s * 0.6), fill=ink)              # exposed pad
    for i in range(4):
        t = s * (0.34 + i * 0.1)
        pw, pl = s * 0.045, s * 0.12
        for box in ((t - pw / 2, s * 0.3 - pl - s * 0.03, t + pw / 2, s * 0.3 - s * 0.03),
                    (t - pw / 2, s * 0.7 + s * 0.03, t + pw / 2, s * 0.7 + pl + s * 0.03),
                    (s * 0.3 - pl - s * 0.03, t - pw / 2, s * 0.3 - s * 0.03, t + pw / 2),
                    (s * 0.7 + s * 0.03, t - pw / 2, s * 0.7 + pl + s * 0.03, t + pw / 2)):
            d.rectangle(box, fill=ink)
    d.ellipse((s * 0.33, s * 0.33, s * 0.33 + s * 0.05, s * 0.33 + s * 0.05), fill=(35, 35, 39, 255))   # pin 1
    return im.resize((n, n), Image.LANCZOS)


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "ecadlib.ico"
    sizes = [16, 24, 32, 48, 64, 128, 256]
    draw(256).save(out, sizes=[(n, n) for n in sizes])
    print(f"wrote {out}")
