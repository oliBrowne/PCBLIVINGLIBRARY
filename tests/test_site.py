"""The landing page (site/): checks a browser test would miss on Chromium alone."""
import re
from pathlib import Path

SITE = Path(__file__).resolve().parent.parent / "site"


def test_svg_stroke_widths_are_plain_numbers():
    """Oliver saw SOT-23 silk drawn as 1 mm slabs over the pads (17 Oct): stroke-width: calc(var(--sw) * 1.2) is a
    unitless calc(), which Chromium takes but Safari and Firefox drop, falling back to 1 user unit = 1 mm. Every
    stroke-width in the footprint player must be a plain number, a length or a var() of one."""
    css = (SITE / "site.css").read_text()
    bad = [m.group(0) for m in re.finditer(r"stroke-width:\s*calc\([^;]*\)", css)]
    assert not bad, bad
    js = (SITE / "site.js").read_text()
    used = set(re.findall(r"var\((--sw[\w-]*)\)", css))
    assert used and all(f'"{v}"' in js for v in used), used          # each one is set from site.js


def test_hero_data_matches_the_generators():
    """The hero shows ecadlib's own TLC5951DAP footprint and symbol: site/hero-data.js must be what the generators make
    today, so a generator change can't leave the landing page drawing an old part."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("build_site_hero", SITE.parent / "scripts" / "build_site_hero.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    have = (SITE / "hero-data.js").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert have == mod.text(), "site/hero-data.js is stale: run python3 scripts/build_site_hero.py"


def test_hero_never_meets_the_site_stylesheet():
    """The hero sits inside a page styled by site.css, which already has .panel, .layer, .pin, --ink (a registered
    number) and more. Every class, id and custom property the hero owns carries the xh- prefix, so neither side can
    restyle the other."""
    css = re.sub(r"/\*.*?\*/", "", (SITE / "hero.css").read_text(), flags=re.S)
    own = [ln for ln in css.splitlines() if not ln.lstrip().startswith(".nav.xh-over")]   # the one rule that dresses the site's nav
    sels = re.findall(r"([^{}]+)\{", "\n".join(own))
    classes = {c for s in sels if not s.strip().startswith("@") for c in re.findall(r"\.([a-zA-Z_][\w-]*)", s)}
    assert classes and all(c.startswith("xh") for c in classes), sorted(c for c in classes if not c.startswith("xh"))
    props = set(re.findall(r"(--[\w-]+)\s*:", "\n".join(own)))
    assert props and all(p.startswith("--xh-") or p == "--copper" for p in props), sorted(props)
    page = (SITE / "index.html").read_text(encoding="utf-8")
    hero = page[page.index('<section class="xh'):page.index("</section>", page.index('<section class="xh')) + 10]
    tokens = {t for v in re.findall(r'(?:class|id)="([^"]*)"', hero) for t in v.split()}
    assert tokens and all(t.startswith("xh") for t in tokens), sorted(t for t in tokens if not t.startswith("xh"))
    js = (SITE / "hero.js").read_text(encoding="utf-8")
    made = {t for v in re.findall(r'(?:class|id)="([^"$]*)"', js) for t in v.split()}
    made |= {t for v in re.findall(r"setAttribute\('(?:class|id)', '([^']*)'\)", js) for t in v.split()}
    made |= {t for v in re.findall(r"className = [`'\"]([^`'\"$]*)", js) for t in v.split()}
    asked = {c for q in re.findall(r"(?:querySelector(?:All)?|\$)\('([^']*)'\)", js) for c in re.findall(r"[.#]([a-zA-Z_][\w-]*)", q)}
    toggled = set(re.findall(r"classList\.(?:toggle|add|remove)\('([^']*)'", js))
    bad = sorted(t for t in made | asked | toggled if not t.startswith("xh") and t != "nav")    # .nav: the site's, which it darkens
    assert made and asked and toggled and not bad, bad


def test_hero_phone_layout_is_one_rule():
    """hero.js picks the phone camera with matchMedia and hero.css the phone layout with @media: they must be the same
    query, or a size between the two gets the phone camera on the wide layout (or the reverse)."""
    css = (SITE / "hero.css").read_text(encoding="utf-8")
    js = (SITE / "hero.js").read_text(encoding="utf-8")
    phone = re.search(r"narrow = matchMedia\('([^']*)'\)", js).group(1)
    assert "/* phones:" in css and re.search(r"/\* phones:[^\n]*\*/\n@media ([^{]*) \{", css).group(1) == phone, phone
