"""Tidy symbols for big chips: pins grouped by function and laid out like a person would draw them.

    ecadlib symbol PINS.csv|PINS.json|PART.SchLib|LIB.kicad_sym:NAME|kicad:Lib:Name -o out.SchLib [--kicad out.kicad_sym]
                   [--preview out.svg] [--split N] [--stack stack|hide|none] [--rules ecadlib.toml]

Each pin is put in a functional group from its name (and type when the name says nothing):
  power, analog (VDDA, VREF+), power_out (VOUT, VCAP)  -> top
  ground (GND, VSS, AGND, VREF-), ep (exposed pad)     -> bottom
  reset (NRST, BOOT0, EN), clock (OSC_IN, XTAL), debug (SWDIO, TCK, U0TXD), usb, adc, other inputs -> left
  uart, i2c, spi, can, eth, ports (PA0..PE15, GPIO12, P0.1), other outputs, nc                      -> right
Alternate-function names ("PA9/USART1_TX", "PH0-OSC_IN") are grouped by their first (primary) name; overbars
('~{RESET}', 'R\\E\\S\\E\\T\\', '/RESET', 'RESET#') are ignored for grouping. Port pins are sorted by port and
number and each port is its own block; blocks are separated by one empty grid row. Ports (and unclassified pins)
move between the left and right side to keep the symbol about square, whole ports first.

Power pins sharing a name (VDD x5) are stacked (rules [symbols] stack_power): 'stack' puts them on one spot, one
pin showing its name and number and the rest drawn on top of it with name and number hidden (Altium joins pins at
one point; the KiCad export writes the rest as hidden passive pins, KLC S4.3); 'hide' makes the rest hidden pins
tied to the net by name (Altium HiddenNetName, KiCad hidden power_in); 'none' draws every pin.

Above [symbols] split_pins pins the part becomes a multi-part symbol: part A holds the power and ground pins, the
other parts the signal blocks in order, at most unit_pins pins each (a port is never split across parts unless it
alone is bigger). Everything is on the [symbols] grid (100 mil): body edges, pin ends, pin pitch.

altium_lib.ic_symbol uses this for parts with at least [symbols] layout_pins pins (smaller parts keep the plain
'side per pin' layout, byte for byte). datasheet.py can call arrange_pins() to show the grouping in its review."""
import argparse, csv, json, re, sys
from pathlib import Path

from . import altium_lib as AL

GROUPS = ("power", "analog", "power_out", "ground", "ep", "reset", "clock", "debug", "usb", "adc", "other_in",
          "uart", "i2c", "spi", "can", "eth", "ports", "other_out", "nc")
ORDER = ["power", "analog", "power_out", "reset", "clock", "debug", "usb", "adc", "other_in", "uart", "i2c", "spi",
         "can", "eth", "ports", "other_out", "nc", "ground", "ep"]
SIDES = {"power": "top", "analog": "top", "power_out": "top", "ground": "bottom", "ep": "bottom",
         "reset": "left", "clock": "left", "debug": "left", "usb": "left", "adc": "left", "other_in": "left",
         "uart": "right", "i2c": "right", "spi": "right", "can": "right", "eth": "right", "ports": "right",
         "other_out": "right", "nc": "right"}
POWER_GROUPS = {"power", "analog", "power_out", "ground", "ep"}
FLEX = ("ports", "other_in", "other_out")      # may move between left and right to balance the symbol
STACKS = ("stack", "hide", "none")
SPLIT_PENALTY = 6                              # rows a port split across sides must save before it is used
CHAR = 7                                       # name width per character, DXP units (as altium_lib.ic_symbol)

_R = lambda s: re.compile(r"^(?:" + s + r")$")
RX = [  # (group, regex on the primary name, upper case, overbar / active-low markers removed); first match wins
    ("nc", _R(r"N\.?C\.?\d*|DNC|DNU|NU|RESERVED\d*|RSVD\d*|NOT ?CONNECTED")),
    ("ep", _R(r"EP|E?PAD|EXPOSED[ _]?PAD|THERMAL[ _]?PAD|TAB|SLUG")),
    ("ground", _R(r"[ADPSE]?GND[A-Z0-9_]*|GND[ADPS]?|VSS[A-Z0-9_]*|V?REF-|VREFN|VREF_?N|VEE|V-|0V|AVSS|DVSS|PVSS")),
    ("analog", _R(r"VDDA[A-Z0-9_]*|AVDD[A-Z0-9_]*|AVCC|VCCA|V?REF\+?|VREFP|VREF_?P|VREF_?OUT|VREFBUF")),
    ("power_out", _R(r"V?OUT|VO|VREG[A-Z0-9_]*|VCAP[A-Z0-9_]*|VDDCORE_?OUT|\d+V\d*_?OUT|V\d+V?\d*_?OUT|LDO_?OUT|VCORE|DECOUPLE")),
    ("usb", _R(r"USB[A-Z0-9_+\-]*|D[PM]\d?|D[+\-]|UD[+\-PM]|VBUS|USB_?ID|OTG[A-Z0-9_]*|CC[12]")),
    ("power", _R(r"VDD[A-Z0-9_]*|VCC[A-Z0-9_]*|VBAT[A-Z0-9_]*|VIN\d*|VS|V\+|VPP|VIO|VM|VSYS|DVDD|IOVDD|DVCC|PVDD|PVIN|"
                 r"V\d+V\d*|\d+V\d*|VBACKUP|VDDIO\d*|VBATT|BAT|VLOGIC|VL")),
    ("reset", _R(r"N?RST[A-Z0-9_]*|N?RESET[A-Z0-9_]*|MCLR|N?MCLR|EN|CHIP_?EN|CHIP_?PU|N?CE|BOOT\d*|BOOT_?SEL\d*|PDR_?ON|"
                 r"MODE\d*|TEST\d*|TST|N?WAKE[A-Z0-9_]*|PWR_?EN|N?SLEEP|N?SHDN|SHUTDOWN|N?STBY|N?PD|PWRDN|N?PWRDWN|N?SUSPEND|NMI")),
    ("clock", _R(r"OSC[A-Z0-9_]*|XTAL[A-Z0-9_]*|XI|XO|XIN|XOUT|X[12]|CLK_?IN|CLK_?OUT|CLKO?|MCO\d*|EXT_?CLK|XT[IO]|"
                 r"RTC_?X[A-Z0-9_]*|32K_?X[PN]|OSC32[A-Z0-9_]*|LSE[A-Z_]*|HSE[A-Z_]*")),
    ("debug", _R(r"SWDIO|SWCLK|SWO|SWD[A-Z0-9_]*|TMS|TCK|TDI|TDO|N?TRST|JT[A-Z0-9_]*|DBG[A-Z0-9_]*|TRACE[A-Z0-9_]*|"
                 r"U0TXD|U0RXD|UART0_?TXD?|UART0_?RXD?|TXD0|RXD0|SWIM|PDI|UPDI|ICSP[A-Z]*|PGE?[CD]\d?")),
    ("uart", _R(r"L?P?US?ART[A-Z0-9_]*|TXD?\d*|RXD?\d*|N?RTS\d*|N?CTS\d*|N?DTR|N?DSR|N?DCD|N?RI|TXEN|TXDEN|RS485[A-Z_]*|"
                r"TXLED|RXLED|N?TXLED|N?RXLED")),
    ("i2c", _R(r"I2C[A-Z0-9_]*|SDA\d*|SCL\d*|TWI[A-Z0-9_]*|SMB[A-Z0-9_]*|IIC[A-Z0-9_]*")),
    ("spi", _R(r"Q?SPI[A-Z0-9_]*|MOSI\d*|MISO\d*|SCK\d*|SCLK\d*|N?CS\d*|N?SS\d*|N?SEL|SDI|SDO|SIO\d|SD[0-3]|CMD|"
               r"FLASH[A-Z0-9_]*|N?HOLD|N?WP")),
    ("can", _R(r"F?D?CAN[A-Z0-9_]*|TXCAN|RXCAN")),
    ("eth", _R(r"ETH[A-Z0-9_]*|R?G?MII[A-Z0-9_]*|MDIO|MDC|T[XD][PN+\-]|R[XD][PN+\-]|TXP|TXN|RXP|RXN|PHY[A-Z0-9_]*")),
    ("adc", _R(r"ADC[A-Z0-9_]*|AIN\d*|AN\d+|DAC[A-Z0-9_]*|AOUT\d*|SENSOR_?V[PN]|SENSOR[A-Z0-9_]*|VP|VN|AIN[PN]\d*")),
]
PORT_RX = [re.compile(r"^P([A-Z])(\d{1,2})$"), re.compile(r"^P(\d{1,2})[._](\d{1,2})$"), re.compile(r"^PIO(\d)_(\d{1,2})$"),
           re.compile(r"^(GPIO|IO|GP)(\d{1,3})$"), re.compile(r"^R(\d)(\d)$")]


# ------------------------------------------------------------------ names
def plain(name):
    """Pin name without overbar markup: '~{RESET}' / 'R\\E\\S\\E\\T\\' -> 'RESET'."""
    s = re.sub(r"~\{([^}]*)\}", r"\1", str(name or ""))
    return s.replace("\\", "").replace("~", "")


def primary(name):
    """Primary function of an alternate-function name: 'PA9/USART1_TX' -> 'PA9', 'PH0-OSC_IN' -> 'PH0'."""
    s = plain(name).strip()
    if re.fullmatch(r"(?i)n/?c|not connected", s):
        return "NC"
    if s.startswith("/") and len(s) > 1:          # '/RESET' active low
        s = s[1:]
    tok = re.split(r"[/,;(\s]", s, maxsplit=1)[0] or s
    m = re.match(r"^(P[A-Z]\d{1,2}|GPIO\d+|IO\d+)[-_](?=[A-Z])", tok, re.I)
    return m.group(1) if m else tok


def _key(name):
    """Primary name for matching: upper case, active-low suffixes removed ('RESET#', 'RST_N', 'RESET_B')."""
    s = primary(name).upper()
    s = re.sub(r"(#|\*|_N|_B|_L)$", "", s) if len(s) > 2 else s
    return s


def port_of(name):
    """(port, number) for a port pin name ('PA9' -> ('A', 9), 'GPIO12' -> ('IO', 12), 'P0.3' -> ('0', 3)); else None."""
    s = _key(name)
    for rx in PORT_RX:
        m = rx.match(s)
        if m:
            port, num = m.groups()
            return ("IO" if port in ("GPIO", "IO", "GP") else port), int(num)
    return None


def classify(pin):
    """-> functional group (see GROUPS). pin: {'name', 'type', 'num', optional 'group'} (an explicit 'group' wins)."""
    if pin.get("group") in GROUPS:
        return pin["group"]
    k, t = _key(pin.get("name", "")), str(pin.get("type") or "").lower()
    num = str(pin.get("num", "")).upper()
    if t == "nc":
        return "nc"
    if port_of(pin.get("name", "")):
        return "ports"
    for g, rx in RX:
        if rx.match(k):
            if g == "usb" and k == "VBUS" and t in ("power", "power_in"):
                return "power"
            return g
    if num in ("EP", "PAD", "EPAD", "TAB"):
        return "ep"
    if t == "ground":
        return "ground"
    if t in ("power", "power_in"):
        return "power"
    if t == "power_out":
        return "power_out"
    return "other_out" if t in ("output", "open_collector", "open_emitter", "tristate", "hiz") else "other_in"


def _natural(s):
    return [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", str(s))]


def stack_key(name):
    """Pins whose names give the same key may be one net: 'VDD_1', 'VDD_2', 'VDD' -> 'VDD'. Only an index after a
    separator is dropped: 'VDD1' / 'VDD2' (an isolator's two sides) and 'VDD18' / 'VDD33' are different supplies."""
    return re.sub(r"[_\-]\d+$", "", _key(name)) or _key(name)


def stack_keys(names):
    """name -> stacking key for the power pins of one group. 'VDD_1'..'VDD_5' (instance numbers 1..n or 0..n-1) share
    the key 'VDD'; 'VDD_18' + 'VDD_33' are not instance numbers and keep their own names (never stacked together)."""
    idx = {}
    for n in names:
        m = re.fullmatch(r"(.+?)[_\-](\d+)", _key(n))
        if m:
            idx.setdefault(m.group(1), set()).add(int(m.group(2)))
    ok = {st for st, ix in idx.items() if ix in (set(range(1, len(ix) + 1)), set(range(len(ix))))}
    out = {}
    for n in names:
        m = re.fullmatch(r"(.+?)[_\-](\d+)", _key(n))
        out[n] = m.group(1) if m and m.group(1) in ok else _key(n)
    return out


# ------------------------------------------------------------------ settings
def settings(rules=None):
    """[symbols] settings: rules may be a loaded rules dict, a dict of [symbols] overrides, or None (defaults)."""
    from . import rules as RU
    s = dict(RU.DEFAULTS["symbols"])
    if isinstance(rules, dict):
        over = rules.get("symbols") if isinstance(rules.get("symbols"), dict) else {k: v for k, v in rules.items() if k in s}
        s.update(over or {})
    return s


def wants_layout(pins, rules=None):
    """True when a part with these pins (or this many) gets the grouped layout."""
    n = pins if isinstance(pins, int) else len(pins)
    t = settings(rules)["layout_pins"]
    return bool(t) and n >= t


# ------------------------------------------------------------------ arrangement
def _blocks(pins, s):
    """pins -> ordered blocks {group, label, side, slots: [[lead pin, stacked...], ...]}."""
    order = {g: i for i, g in enumerate(list(s["order"]) + [g for g in ORDER if g not in s["order"]])}
    sides = dict(SIDES, **(s.get("sides") or {}))
    by = {}
    for i, p in enumerate(pins):
        g = classify(p)
        port = port_of(p.get("name", "")) if g == "ports" else None
        sub = port[0] if port else ""
        by.setdefault((g, sub), []).append((port[1] if port else i, i, p))
    blocks = []
    for (g, sub), items in sorted(by.items(), key=lambda kv: (order[kv[0][0]], _natural(kv[0][1]))):
        if g == "ports":
            items.sort(key=lambda t: (t[0], _natural(t[2].get("num", ""))))
        else:                                             # numbered names in number order (CBUS0..4, SD0..3, VSS_1..5),
            stem = (lambda n: stack_key(n)) if g in POWER_GROUPS else (lambda n: re.sub(r"\d+", "#", _key(n)))
            stems = {}                                    # otherwise the order given (TXD, RXD, RTS, CTS ...)
            for t in items:
                stems.setdefault(stem(t[2].get("name", "")), t[1])
            items.sort(key=lambda t: (stems[stem(t[2].get("name", ""))], _natural(_key(t[2].get("name", ""))), t[1]))
        slots, at, keys = [], {}, []
        sk = stack_keys([t[2].get("name", "") for t in items])
        for _, _, p in items:
            k = sk[p.get("name", "")]
            if g in POWER_GROUPS and s["stack_power"] != "none" and k in at:
                slots[at[k]].append(p)
            else:
                at[k] = len(slots)
                slots.append([p])
                keys.append(k)
        blocks.append({"group": g, "label": f"P{sub}" if sub and sub.isalpha() and len(sub) == 1 else sub or g,
                       "side": sides[g], "slots": slots, "nets": {id(sl): k for sl, k in zip(slots, keys)}})
    return blocks


def _npins(blocks):
    return sum(len(sl) for b in blocks for sl in b["slots"])


def _height(blocks):
    """Rows a side takes: slots plus one gap between blocks (pieces of one block that follow each other: no gap)."""
    rows = sum(len(b["slots"]) for b in blocks)
    return rows + sum(1 for a, b in zip(blocks, blocks[1:]) if not (b.get("piece") and b["piece"][0] == id(a.get("src", a))))


def _pieces(b, step=8):
    """A flexible block cut where it may be split between the sides (every `step` slots, blocks of 16+ slots)."""
    n = len(b["slots"])
    if n < 16:
        return [b]
    src = b.get("src", b)
    return [dict(b, slots=b["slots"][i:i + step], piece=(id(src), i), src=src) for i in range(0, n, step)]


def _balance(blocks):
    """Assign flexible blocks to left/right: left = fixed left + flex[:k], right = flex[k:] + fixed right."""
    fixed_l = [b for b in blocks if b["side"] == "left" and b["group"] not in FLEX]
    fixed_r = [b for b in blocks if b["side"] == "right" and b["group"] not in FLEX]
    flex = [b for b in blocks if b["group"] in FLEX and b["side"] in ("left", "right")]
    pieces = [pc for b in flex for pc in _pieces(b)]
    best = None
    for k in range(len(pieces) + 1):
        left, right = fixed_l + pieces[:k], pieces[k:] + fixed_r
        split = 0 < k < len(pieces) and pieces[k].get("piece") and pieces[k]["piece"][1] > 0
        hl, hr = _height(left), _height(right)
        cost = (max(hl, hr) + (SPLIT_PENALTY if split else 0), abs(hl - hr), k)
        if best is None or cost < best[0]:
            best = (cost, left, right)
    _, left, right = best
    return {"left": left, "right": right, "top": [b for b in blocks if b["side"] == "top"],
            "bottom": [b for b in blocks if b["side"] == "bottom"]}


def _chunk(block, size):
    return [dict(block, slots=block["slots"][i:i + size]) for i in range(0, len(block["slots"]), size)] if size else [block]


def arrange(pins, rules=None, split=None):
    """pins -> [unit {'unit': n, 'sides': {side: [block, ...]}}]. split: True/False forces multi-part on/off."""
    s = settings(rules)
    blocks = _blocks(pins, s)
    big = s["split_pins"] and len(pins) > s["split_pins"]
    if not (big if split is None else split):
        return [{"unit": 1, "sides": _balance(blocks)}]
    pw = [b for b in blocks if b["side"] in ("top", "bottom") or b["group"] == "nc"]     # part A: power, ground, NC
    sig = [b for b in blocks if b not in pw]
    cap = max(8, s["unit_pins"])
    units, cur = [], []
    for b in sig:
        for c in (_chunk(b, cap) if _npins([b]) > cap else [b]):
            if cur and _npins(cur) + _npins([c]) > cap:
                units.append(cur); cur = []
            cur.append(c)
    if cur:
        units.append(cur)
    out = [{"unit": 1, "sides": _balance(pw)}] if pw else []
    return out + [{"unit": len(out) + i + 1, "sides": _balance(u)} for i, u in enumerate(units)]


def arrange_pins(pins, rules=None, split=None):
    """For datasheet.py and reviews: the pins in layout order, each a copy with 'group', 'side', 'unit' (1 = part A)
    and 'stacked_on' (the pin number it is stacked on, for stacked power pins)."""
    out = []
    for u in arrange(pins, rules, split):
        for side in ("left", "right", "top", "bottom"):
            for b in u["sides"][side]:
                for sl in b["slots"]:
                    for j, p in enumerate(sl):
                        out.append(dict(p, group=b["group"], side=side, unit=u["unit"], **({"stacked_on": sl[0].get("num")} if j else {})))
    return out


# ------------------------------------------------------------------ geometry
def _up(v, g):
    return g * -(-int(v) // g)


def _rows(blocks):
    """[slot or None (gap)] down one side."""
    out = []
    for i, b in enumerate(blocks):
        if i and not (b.get("piece") and b["piece"][0] == id(blocks[i - 1].get("src", blocks[i - 1]))):
            out.append(None)
        out += b["slots"]
    return out


def _nlen(p):
    return len(plain(p.get("name", "")))


def build(name, pins, description="", designator="U?", footprint=None, comment="*", rules=None, split=None):
    """Grouped multi-block (and optionally multi-part) IC symbol, as an altium_lib symbol dict."""
    bad = [p.get("num") for p in pins if not str(p.get("num", "")).strip()]
    if bad:
        raise ValueError(f"{name}: {len(bad)} pins have no number")
    s = settings(rules)
    g, L = s["grid"] // 10, s["pin_length"] // 10
    units = arrange(pins, rules, split)
    prims, parts = [], []
    tallest = (0, 0)
    for u in units:
        sd = u["sides"]
        nets = {k: v for bl in sd.values() for b in bl for k, v in b.get("nets", {}).items()}
        left, right, top, bot = _rows(sd["left"]), _rows(sd["right"]), _rows(sd["top"]), _rows(sd["bottom"])
        names = lambda rows: max([_nlen(sl[0]) for sl in rows if sl] or [0])
        lch, rch = names(left), names(right)
        t_need = _up(names(top) * CHAR + 10, g) if top else 0          # vertical names of top / bottom pins
        b_need = _up(names(bot) * CHAR + 10, g) if bot else 0
        nrows = max(len(left), len(right), 1)
        cols = max(len(top), len(bot))
        def shape(w):                                                  # -> (w + h, w, h, top room, bottom room, x0s)
            W = w // g
            fits = lambda rows, x0: (x0 - g // 2 > lch * CHAR + g // 2                       # clear of the side names
                                     and x0 + g * (len(rows) - 1) + g // 2 < w - rch * CHAR - g // 2)
            x0s = {}
            for rows in (top, bot):                                     # centred, else just right of the left names
                c = [g * max(1, (W - len(rows) + 1) // 2), _up(lch * CHAR + g + 1, g)]
                x0s[id(rows)] = next((x for x in c if fits(rows, x)), c[0])
            clear = lambda rows: not rows or fits(rows, x0s[id(rows)])
            tr, br = (0 if clear(top) else t_need), (0 if clear(bot) else b_need)   # side pins start below them only if they meet
            h = max(tr + g * (nrows + 1) + br, t_need + b_need + 2 * g)
            return w + h, w, h, tr, br, x0s
        w0 = max(4 * g, _up(lch * CHAR + rch * CHAR + 2 * g, g), g * (cols + 1))
        wide = _up(lch * CHAR + rch * CHAR + 2 * g, g) + g * (cols + 2)       # wide enough for the top/bottom pins between names
        _, w, h, t_room, b_room, x0s = min(shape(w0), shape(max(w0, wide)), key=lambda t: t[:3])
        prims.append(("rect", 0, 0, w, -h)); parts.append(u["unit"])
        tallest = max(tallest, (h, w))

        def put(sl, side, x, y):
            for j, p in enumerate(sl):
                q = dict(p)
                if j:
                    if s["stack_power"] == "hide":
                        q.update(hidden=True, net=q.get("net") or nets.get(id(sl)) or stack_key(p.get("name", "")))
                    else:
                        q.update(show_name=False, show_num=False)
                prims.append(("pin", q, side, x, y, L)); parts.append(u["unit"])
        for i, sl in enumerate(left):
            if sl:
                put(sl, "left", 0, -t_room - g * (i + 1))
        for i, sl in enumerate(right):
            if sl:
                put(sl, "right", w, -t_room - g * (i + 1))
        for rows, side, y in ((top, "top", 0), (bot, "bottom", -h)):
            x0 = x0s[id(rows)]
            for j, sl in enumerate(rows):
                if sl:
                    put(sl, side, x0 + g * j, y)
    h, _ = tallest
    top_pins = any(u["sides"]["top"] for u in units)
    bot_pins = any(u["sides"]["bottom"] for u in units)
    sym = {"name": name, "description": description, "designator": designator, "comment": comment, "footprint": footprint,
           "prims": prims, "label_at": ((0, (L + 2) if top_pins else 2), (0, -h - (L + 8 if bot_pins else 8)))}
    if len(units) > 1:
        sym.update(parts=len(units), prim_parts=parts)
    return sym


def relayout(sym, rules=None, split=None):
    """An altium_lib symbol dict (e.g. converted from EasyEDA) -> the grouped layout, keeping its name, labels,
    footprint and parameters. Pins keep number, name, type, shape. Only single-part symbols are re-laid out."""
    if sym.get("parts", 1) > 1 or any(sym.get("prim_modes") or []):
        return sym
    pins, seen = [], set()
    for pr in sym["prims"]:
        if pr[0] == "pin" and pr[1]["num"] not in seen:
            seen.add(pr[1]["num"])
            pins.append({k: v for k, v in pr[1].items() if k in ("num", "name", "type", "shape", "description")})
    new = build(sym["name"], pins, sym.get("description", ""), sym.get("designator", "U?"), sym.get("footprint"),
                sym.get("comment", "*"), rules, split)
    if sym.get("params"):
        new["params"] = sym["params"]
    return new


# ------------------------------------------------------------------ checks
def check(sym, grid=100):
    """Problems in a laid-out symbol: pins off the grid, two different pins at one spot (other than stacked power
    pins with the same name), pins inside another pin's span. -> [text]."""
    g = grid // 10
    probs, at = [], {}
    parts = sym.get("prim_parts") or [1] * len(sym["prims"])
    for pr, u in zip(sym["prims"], parts):
        if pr[0] != "pin":
            continue
        _, p, side, x, y, L, *_ = pr
        dx, dy = {"left": (-1, 0), "right": (1, 0), "top": (0, 1), "bottom": (0, -1)}[side]
        ex, ey = x + dx * L, y + dy * L
        if any(v % g for v in (x, y, ex, ey)):
            probs.append(f"pin {p['num']} off the {grid} mil grid at ({x}, {y})")
        key = (u, x, y)
        if key in at and stack_key(at[key]["name"]) != stack_key(p["name"]):
            probs.append(f"pins {at[key]['num']} and {p['num']} overlap at ({x}, {y}) in part {u}")
        at.setdefault(key, p)
    return probs


# ------------------------------------------------------------------ SVG preview
def svg(sym, scale=2.0):
    """SVG drawing of an altium_lib symbol dict (every part side by side): body, pins, names, numbers."""
    parts = sym.get("prim_parts") or [1] * len(sym["prims"])
    units = sorted(set(parts))
    S, out, xoff = scale, [], 0.0
    fs = 7 * S
    boxes = []
    for u in units:
        prs = [pr for pr, pu in zip(sym["prims"], parts) if pu == u]
        xs, ys = [], []
        for pr in prs:
            if pr[0] == "rect":
                xs += [pr[1], pr[3]]; ys += [pr[2], pr[4]]
            elif pr[0] == "pin":
                _, p, side, x, y, L, *_ = pr
                dx, dy = {"left": (-1, 0), "right": (1, 0), "top": (0, 1), "bottom": (0, -1)}[side]
                xs += [x, x + dx * L]; ys += [y, y + dy * L]
        x0, x1, y0, y1 = min(xs or [0]) - 10, max(xs or [0]) + 10, min(ys or [0]) - 10, max(ys or [0]) + 20
        boxes.append((u, prs, x0, x1, y0, y1, xoff))
        xoff += (x1 - x0) + 30
    W, H = xoff * S, max(b[5] - b[4] for b in boxes) * S + 20
    esc = lambda t: str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    out.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W:.0f}" height="{H:.0f}" viewBox="0 0 {W:.0f} {H:.0f}" '
               f'font-family="Arial, sans-serif" font-size="{fs:.0f}">')
    out.append(f'<rect width="{W:.0f}" height="{H:.0f}" fill="#ffffff"/>')
    for u, prs, x0, x1, y0, y1, xo in boxes:
        X = lambda x: (x - x0 + xo) * S
        Y = lambda y: (y1 - y) * S + 10
        if len(units) > 1:
            out.append(f'<text x="{X(x0 + 2):.1f}" y="{Y(y1 - 8):.1f}" fill="#555">{esc(sym["name"])} part {chr(64 + u)}</text>')
        for pr in prs:
            if pr[0] == "rect":
                out.append(f'<rect x="{X(min(pr[1], pr[3])):.1f}" y="{Y(max(pr[2], pr[4])):.1f}" width="{abs(pr[3] - pr[1]) * S:.1f}" '
                           f'height="{abs(pr[4] - pr[2]) * S:.1f}" fill="#ffffb0" stroke="#800000" stroke-width="1.5"/>')
            elif pr[0] == "line":
                out.append(f'<line x1="{X(pr[1]):.1f}" y1="{Y(pr[2]):.1f}" x2="{X(pr[3]):.1f}" y2="{Y(pr[4]):.1f}" stroke="#000080"/>')
            elif pr[0] in ("polyline", "polygon"):
                pts = " ".join(f"{X(a):.1f},{Y(b):.1f}" for a, b in pr[1])
                out.append(f'<{pr[0]} points="{pts}" fill="none" stroke="#000080"/>')
            elif pr[0] == "circle":
                out.append(f'<circle cx="{X(pr[1]):.1f}" cy="{Y(pr[2]):.1f}" r="{pr[3] * S:.1f}" fill="none" stroke="#000080"/>')
            elif pr[0] == "text":
                out.append(f'<text x="{X(pr[1]):.1f}" y="{Y(pr[2]):.1f}">{esc(pr[3])}</text>')
        for pr in prs:
            if pr[0] != "pin":
                continue
            _, p, side, x, y, L, *show = pr
            if p.get("hidden"):
                continue
            dx, dy = {"left": (-1, 0), "right": (1, 0), "top": (0, 1), "bottom": (0, -1)}[side]
            ex, ey = x + dx * L, y + dy * L
            out.append(f'<line x1="{X(x):.1f}" y1="{Y(y):.1f}" x2="{X(ex):.1f}" y2="{Y(ey):.1f}" stroke="#000080" stroke-width="1.2"/>')
            out.append(f'<circle cx="{X(ex):.1f}" cy="{Y(ey):.1f}" r="1.2" fill="#000080"/>')
            sn = p.get("show_name", show[0] if show else True) and p.get("name") not in ("", "~")
            sd = p.get("show_num", show[0] if show else True)
            nm, num = esc(plain(p.get("name", ""))), esc(p.get("num", ""))
            over = ' text-decoration="overline"' if re.search(r"~\{|\\", str(p.get("name", ""))) else ""
            if dy == 0:
                tx = X(x) + (6 if dx < 0 else -6)
                anchor = "start" if dx < 0 else "end"
                if sn:
                    out.append(f'<text x="{tx:.1f}" y="{Y(y) + fs / 3:.1f}" text-anchor="{anchor}"{over}>{nm}</text>')
                if sd:
                    mx = (X(x) + X(ex)) / 2
                    out.append(f'<text x="{mx:.1f}" y="{Y(y) - 2:.1f}" text-anchor="middle" fill="#800000" font-size="{fs * 0.85:.0f}">{num}</text>')
            else:
                tx, ty = X(x) + fs / 3, Y(y) + (6 if dy > 0 else -6)
                anchor = "end" if dy > 0 else "start"
                if sn:
                    out.append(f'<text x="{tx:.1f}" y="{ty:.1f}" text-anchor="{anchor}" transform="rotate(-90 {tx:.1f} {ty:.1f})"'
                               f'{over}>{nm}</text>')
                if sd:
                    my = (Y(y) + Y(ey)) / 2
                    out.append(f'<text x="{X(x) - 2:.1f}" y="{my:.1f}" text-anchor="middle" fill="#800000" font-size="{fs * 0.85:.0f}" '
                               f'transform="rotate(-90 {X(x) - 2:.1f} {my:.1f})">{num}</text>')
    out.append("</svg>")
    return "\n".join(out) + "\n"


# ------------------------------------------------------------------ inputs
ETYPE_NAME = {0: "input", 1: "io", 2: "output", 3: "open_collector", 4: "passive", 5: "tristate", 6: "open_emitter", 7: "power"}
TYPE_ALIASES = {"i": "input", "in": "input", "o": "output", "out": "output", "bidirectional": "io", "i/o": "io", "inout": "io",
                "pwr": "power", "power_input": "power_in", "power_output": "power_out", "gnd": "ground", "tri_state": "tristate",
                "unspecified": "passive", "free": "passive", "no_connect": "nc", "": "passive", "p": "power", "analog": "passive"}


def _ptype(t):
    t = str(t or "").strip().lower().replace(" ", "_")
    t = TYPE_ALIASES.get(t, t)
    return t if t in AL.ETYPE else "passive"


def pins_from_csv(path):
    """CSV/TSV with a header: number (num/pin/#), name, optional type, group. -> pins."""
    text = Path(path).read_text(encoding="utf-8-sig")
    dialect = "excel-tab" if "\t" in text.splitlines()[0] else "excel"
    rows = list(csv.DictReader(text.splitlines(), dialect=dialect))
    pick = lambda r, *ks: next((str(r[k]).strip() for k in r if k and k.strip().lower() in ks and r[k] is not None), "")
    out = []
    for r in rows:
        num, name = pick(r, "num", "number", "pin", "#", "pin number", "pin_number"), pick(r, "name", "pin name", "signal", "pin_name")
        if not num:
            continue
        p = {"num": num, "name": name or num, "type": _ptype(pick(r, "type", "electrical", "electrical type", "etype", "dir"))}
        grp = pick(r, "group")
        if grp:
            p["group"] = grp
        out.append(p)
    return out


def pins_from_schlib(path, index=0):
    _, syms = AL.read_schlib(path)
    sy = syms[index]
    slot_names = {v: k for k, v in AL.PIN_SYMBOL.items()}
    pins, seen = [], set()
    for r in sy["records"]:
        if r.get("RECORD") == "2" and r["num"] not in seen and not r["mode"]:
            seen.add(r["num"])
            d = {sl: slot_names.get(v, v) for sl, v in zip(AL.PIN_SLOTS, r["symbols"]) if v}
            shape = next((k for k, v in AL.PIN_SHAPE.items() if v == d), None)
            p = {"num": r["num"], "name": _kicad_name(r["name"]), "type": ETYPE_NAME.get(r["etype"], "passive")}
            if shape and shape != "line":
                p["shape"] = shape
            pins.append(p)
    return sy["name"], pins


def _kicad_name(s):
    """Altium 'R\\E\\S\\E\\T\\' -> '~{RESET}' so altium_lib.overline writes it back the same."""
    from .kicad_export import _kicad_pin_name
    return _kicad_pin_name(s)


def load_pins(src, http=None):
    """CSV / JSON (pin list, datasheet extract or symbol JSON) / .SchLib / LIB.kicad_sym:NAME / kicad:Lib:Name ->
    (name, pins, extra symbol fields)."""
    s = str(src)
    low = s.lower()
    if low.endswith((".csv", ".tsv", ".txt")):
        return Path(s).stem, pins_from_csv(s), {}
    if low.endswith(".json"):
        d = json.loads(Path(s).read_text(encoding="utf-8"))
        if isinstance(d, list):
            return Path(s).stem, d, {}
        d = d.get("symbol", d) if isinstance(d.get("symbol"), dict) else d
        extra = {k: d[k] for k in ("description", "designator", "footprint") if d.get(k)}
        return d.get("name") or d.get("mpn") or Path(s).stem, d["pins"], extra
    if low.endswith(".schlib"):
        name, pins = pins_from_schlib(s)
        return name, pins, {}
    from . import kicad_import as K
    ref = s[6:] if low.startswith("kicad:") else s
    chain, _ = K.load_symbol(ref, http if low.startswith("kicad:") else None)
    sym = K.convert_symbol(chain, None, None, [], None, [])
    pins, seen = [], set()
    for pr in sym["prims"]:
        if pr[0] == "pin" and pr[1]["num"] not in seen:
            seen.add(pr[1]["num"])
            pins.append({k: v for k, v in pr[1].items() if k in ("num", "name", "type", "shape")})
    return str(sym["name"]).strip('"'), pins, {k: sym[k] for k in ("description", "designator") if sym.get(k)}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ecadlib symbol", description="group a pin list by function and write a tidy symbol "
                                 "(Altium .SchLib, optional KiCad .kicad_sym and SVG preview)")
    ap.add_argument("source", help="pins: .csv/.tsv (num,name[,type][,group]), .json (pin list / datasheet extract / symbol JSON), "
                                   ".SchLib (re-layout its first symbol), LIB.kicad_sym:NAME or kicad:Lib:Name")
    ap.add_argument("-o", "--out", help="output .SchLib (default: <name>.SchLib)")
    ap.add_argument("--name", help="symbol name (default: from the source)")
    ap.add_argument("--kicad", help="also write this .kicad_sym")
    ap.add_argument("--preview", help="write an SVG preview here")
    ap.add_argument("--split", type=int, help="multi-part above this many pins (0 = never; default: rules [symbols] split_pins)")
    ap.add_argument("--stack", choices=STACKS, help="same-name power pins (default: rules [symbols] stack_power)")
    ap.add_argument("--footprint", help="footprint name to link")
    ap.add_argument("--rules", help="library rules file (default: ecadlib.toml found from here)")
    ap.add_argument("--list", action="store_true", help="print the grouping")
    a = ap.parse_args(argv)
    from . import rules as RU
    try:
        rules = RU.load(a.rules, start=Path(a.out).parent if a.out else ".")
    except RU.RulesError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    sec = dict(rules["symbols"])
    if a.split is not None:
        sec["split_pins"] = a.split
    if a.stack:
        sec["stack_power"] = a.stack
    name, pins, extra = load_pins(a.source)
    name = a.name or name
    sym = build(name, pins, extra.get("description", ""), extra.get("designator", "U?"), a.footprint or extra.get("footprint"),
                rules={"symbols": sec})
    out = Path(a.out or f"{AL.file_stem(name)}.SchLib")
    out.parent.mkdir(parents=True, exist_ok=True)
    AL.write_schlib(out, [sym])
    probs = check(sym, sec["grid"])
    nparts = sym.get("parts", 1)
    groups = {}
    for p in arrange_pins(pins, {"symbols": sec}):
        groups.setdefault((p["unit"], p["side"], p["group"]), []).append(p)
    print(f"{name}: {len(pins)} pins in {len({k[2] for k in groups})} groups, {nparts} part{'s' if nparts > 1 else ''} -> {out}")
    if a.list:
        for (u, side, g), ps in groups.items():
            print(f"  part {chr(64 + u)} {side:<6} {g:<9} " + ", ".join(f"{p['num']}:{plain(p['name'])}" + ("*" if p.get("stacked_on") else "") for p in ps))
    if a.kicad:
        from . import kicad_export as X, sexpr
        notes = []
        ks = X.symbol_from_schlib(out, X.kname(name), notes)
        tree = ["kicad_symbol_lib", ["version", X.SYM_VERSION], ["generator", sexpr.Q("ecadlib")], ["generator_version", sexpr.Q("8.0")], ks]
        Path(a.kicad).write_text(sexpr.dumps(tree), encoding="utf-8")
        print(f"  KiCad symbol -> {a.kicad}")
    if a.preview:
        Path(a.preview).write_text(svg(sym), encoding="utf-8")
        print(f"  preview -> {a.preview}")
    for pr in probs:
        print(f"  problem: {pr}")
    return 1 if probs else 0


if __name__ == "__main__":
    sys.exit(main())
