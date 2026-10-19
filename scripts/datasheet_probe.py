"""EXPERIMENT: how many benchmark datasheet links download as a usable PDF with ecadlib's own User-Agent vs a desktop
browser's headers ([datasheet] browser_headers), each with and without retries. No model is called (no API spend).

    python scripts/datasheet_probe.py -o probe-out [--limit 110] [--modes honest,browser,browser+retry,honest+retry]

Every link goes through PdfStore.fetch, the same path a BOM run takes (PDF check, size limit, one hop from an HTML
product page to its PDF), each mode with its own empty cache and its own session (no shared cookies). Modes run
back to back per link, in a rotating order, under ecadlib's per-host rate limit (one request per second per site).
Writes probe.csv (one row per link per mode) and summary.md (per site and overall)."""
import argparse, csv, sys, tempfile, time
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ecadlib import datasheet as D                                 # noqa: E402
from ecadlib.httpcache import CachedHTTP                           # noqa: E402
from ecadlib.eval import runner as R                               # noqa: E402

MODES = {"honest": (False, 0), "honest+retry": (False, 2), "browser": (True, 0), "browser+retry": (True, 2)}
# docs/benchmark.md (run of 2026-10-18): parts whose PDF never reached the model
REFUSED = set("LTM4671 LT3652IDD LTC4060EDHC LTC2942-1 LTC7810 ADAU1702 AD5274BRM AD7606 LT8610 MAX22201 AD7616 "
              "BUK7M10-40EX PSMN5R2-60YL IP4234CZ6 BCV61 BAT160A BAT160C M2GL090T-FG484 DNA40U2200GU C8051F320-GQ "
              "LTV-352T IPS6011RPBF".split())
NO_LINK = set("ENS210 ICE40HX8K-BG121 AQY282GS MKE02Z16VLD4 GBU4A C3D02065E KBU4A AUIPS6011R AP1117-18 2ED21824S06J "
              "HCPL-314J C3D08060G 16550".split())


def site(url):
    h = urlparse(url).netloc.lower().split(":")[0]
    parts = h.split(".")
    return ".".join(parts[-3:]) if len(parts) > 2 and parts[-2] in ("co", "com") and len(parts[-1]) == 2 else ".".join(parts[-2:])


def probe(url, mode, root, last):
    browser, retries = MODES[mode]
    h = CachedHTTP(cache_dir=root / mode / "http", min_interval=1.0)
    h._last = last                                                  # one rate limit per site across the modes
    st = D.PdfStore(root / mode, h, browser=browser, retries=retries)
    t = time.time()
    try:
        data, meta = st.fetch(url, "probe")
        ok, err, pages, size = bool(meta.get("pages")), "" if meta.get("pages") else "no pages", meta.get("pages"), len(data)
    except D.DatasheetError as e:
        ok, err, pages, size = False, str(e).replace(url, "").lstrip(": ")[:200], "", ""
    return {"ok": ok, "error": err, "pages": pages, "bytes": size, "attempts": st.attempts, "seconds": round(time.time() - t, 1)}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--limit", type=int, default=110)
    ap.add_argument("--modes", default="honest,browser,browser+retry,honest+retry")
    a = ap.parse_args(argv)
    modes = a.modes.split(",")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    cases = R.select(R.load_set("kicad-v1")["cases"], limit=a.limit)
    rows, last = [], {}
    with tempfile.TemporaryDirectory(prefix="dsprobe") as tmp:
        for i, c in enumerate(cases):
            url = (c.get("datasheet") or "").strip()
            base = {"mpn": c["mpn"], "family": c["family"], "url": url, "site": site(url) if url.startswith("http") else "(none)",
                    "benchmark": "refused" if c["mpn"] in REFUSED else "no link" if c["mpn"] in NO_LINK else ""}
            if not url.startswith("http"):
                rows += [dict(base, mode=m, ok=False, error="no link", pages="", bytes="", attempts=0, seconds=0) for m in modes]
                continue
            order = modes[i % len(modes):] + modes[:i % len(modes)]
            for m in order:
                r = dict(base, mode=m, **probe(url, m, Path(tmp) / f"{i}", last))
                rows.append(r)
                print(f"{i + 1:3}/{len(cases)} {m:14} {'PDF ' if r['ok'] else 'FAIL'} {c['mpn']:18} {base['site']:22} {r['error'][:70]}", flush=True)
    with open(out / "probe.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    (out / "summary.md").write_text(summary(rows, modes), encoding="utf-8")
    print(summary(rows, modes))


def summary(rows, modes):
    by = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for r in rows:
        for key in (r["site"], "ALL"):
            by[key][r["mode"]][1] += 1
            by[key][r["mode"]][0] += bool(r["ok"])
        if r["benchmark"]:
            by["benchmark misses"][r["mode"]][1] += 1
            by["benchmark misses"][r["mode"]][0] += bool(r["ok"])
    keys = sorted((k for k in by if k not in ("ALL", "benchmark misses")), key=lambda k: -by[k][modes[0]][1])
    lines = ["| Site | Links | " + " | ".join(modes) + " |", "|---|---|" + "---|" * len(modes)]
    for k in ["ALL", "benchmark misses"] + keys:
        n = by[k][modes[0]][1]
        lines.append(f"| {k} | {n} | " + " | ".join(str(by[k][m][0]) for m in modes) + " |")
    flips = defaultdict(list)
    for r in rows:
        flips[(r["mpn"], r["url"])].append(r)
    gained = [k[0] for k, rs in flips.items() if any(x["ok"] for x in rs if x["mode"].startswith("browser"))
              and not any(x["ok"] for x in rs if x["mode"].startswith("honest"))]
    lost = [k[0] for k, rs in flips.items() if any(x["ok"] for x in rs if x["mode"].startswith("honest"))
            and not any(x["ok"] for x in rs if x["mode"].startswith("browser"))]
    return "\n".join(lines + ["", f"Only with browser headers ({len(gained)}): {', '.join(gained) or '-'}",
                              f"Only with ecadlib's own UA ({len(lost)}): {', '.join(lost) or '-'}", ""])


if __name__ == "__main__":
    main()
