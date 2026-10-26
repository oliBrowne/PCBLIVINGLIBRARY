"""Tidy symbols for big chips: pins grouped by function and laid out like a person would draw them.

    ecadlib symbol PINS.csv|PINS.json|PART.SchLib|LIB.kicad_sym:NAME|kicad:Lib:Name -o out.SchLib [--kicad out.kicad_sym]
                   [--preview out.svg] [--split N] [--stack stack|hide|none] [--rules ecadlib.toml]

The house rule: inputs left, outputs right, power on top, ground on the bottom ([symbols] power_side = "top"), or
the top and bottom edges left empty with the power pins at the top of the left side and the ground pins at its
bottom (power_side = "left"). Each pin is put in a functional group from its name (and type when the name says nothing):
  power, analog (VDDA, VREF+)                          -> top (power_side "left": top of the left side)
  ground (GND, VSS, AGND, VREF-), ep (exposed pad)     -> bottom (power_side "left": bottom of the left side)
  reset (NRST, BOOT0, EN), clock (OSC_IN, XTAL), debug (SWDIO, TCK, U0TXD), usb, adc, inputs (IN+, TRIG, FB, type
  input) -> left
  power_out (VOUT, VCAP), uart, i2c, spi, can, eth, ports (PA0..PE15, GPIO12, P0.1), outputs (OUT, Q, PG, SW, type
  output), nc -> right
  (a uart / i2c / spi / can / eth group whose pins are all type input is inputs named like a bus: left)
  other (no name rule and no direction: passive, bidirectional) -> left, moved right when that keeps the symbol square
Alternate-function names ("PA9/USART1_TX", "PH0-OSC_IN") are grouped by their first (primary) name; overbars
('~{RESET}', 'R\\E\\S\\E\\T\\', '/RESET', 'RESET#') are ignored for grouping. Port pins are sorted by port and
number and each port is its own block; blocks are separated by one empty grid row. Ports (and unclassified pins)
and 'other' pins move between the left and right side to keep the symbol about square, whole ports first; inputs and
outputs never move.

Power pins sharing a name (VDD x5) may be stacked (rules [symbols] stack_power, default 'none'): 'stack' puts them on one spot, one
pin showing its name and number and the rest drawn on top of it with name and number hidden (Altium joins pins at
one point; the KiCad export writes the rest as hidden passive pins, KLC S4.3); 'hide' makes the rest hidden pins
tied to the net by name (Altium HiddenNetName, KiCad hidden power_in), for supply inputs and grounds only (an output
or exposed pad hidden on a net named VOUT / EP would join every part's on the board: those are stacked as 'stack'
does); 'none' draws every pin.

Above [symbols] split_pins pins the part becomes a multi-part symbol: part A holds the power and ground pins, the
other parts the signal blocks in order, at most unit_pins pins each (a port is never split across parts unless it
alone is bigger). Everything is on the [symbols] grid (100 mil): body edges, pin ends, pin pitch.

altium_lib.ic_symbol uses this for parts with at least [symbols] layout_pins pins (default 1: every IC; above that the
smaller parts keep the plain 'side per pin' layout). librarian re-lays out EasyEDA IC symbols the same way, and a KiCad
library symbol whose pin text would overlap in Altium (text_problems). datasheet.py can call arrange_pins() to show the
grouping in its review."""
import argparse, csv, json, re, sys
from pathlib import Path

from . import altium_lib as AL

GROUPS = ("power", "analog", "power_out", "ground", "ep", "reset", "clock", "debug", "usb", "adc", "other_in",
          "other", "uart", "i2c", "spi", "can", "eth", "ports", "other_out", "nc")
ORDER = ["power", "analog", "reset", "clock", "debug", "usb", "adc", "other_in", "other", "power_out", "uart", "i2c",
         "spi", "can", "eth", "ports", "other_out", "nc", "ground", "ep"]
SIDES = {"power": "top", "analog": "top", "power_out": "right", "ground": "bottom", "ep": "bottom",
         "reset": "left", "clock": "left", "debug": "left", "usb": "left", "adc": "left", "other_in": "left",
         "other": "left", "uart": "right", "i2c": "right", "spi": "right", "can": "right", "eth": "right",
         "ports": "right", "other_out": "right", "nc": "right"}
POWER_GROUPS = {"power", "analog", "power_out", "ground", "ep"}
BUSES = ("uart", "i2c", "spi", "can", "eth")
FLEX = ("ports", "other")                      # may move between left and right to balance the symbol
STACKS = ("stack", "hide", "none")
POWER_SIDES = ("top", "left")
DIRECTED = {"input": "other_in", "output": "other_out", "open_collector": "other_out", "open_emitter": "other_out",
            "tristate": "other_out", "hiz": "other_out"}                  # [symbols] power_side: the house rule's two variants
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
    # direction from the name, for the small analog / power / logic parts whose pins have no better group
    ("other_out", _R(r"OUT[A-Z0-9_+\-]*|[A-Z0-9]+_?OUT[A-Z0-9]*|N?Q\d*'?|Q_?N|Q[A-H]'?|\d+Q'?|\d+Y|Y\d+|DIS|DISCH[A-Z]*|N?PG|N?PGOOD|P_?GOOD|PWR_?(GD|GOOD|OK)|"
                    r"N?CO|N?BO|N?TC|N?RCO|N?FLT|N?FAULT|N?FLAG|N?ALERT|N?INT\d*|N?IRQ\d*|RDY|N?READY|BUSY|DONE|STAT\d*|LED[A-Z0-9_]*|SW\d*|LX\d*|"
                    r"BST|BOOST|PH|DRV[A-Z0-9_]*|GATE[A-Z0-9_]*|[HL]O|[HL]G|ALARM|TRIP")),
    ("other_in", _R(r"IN\d*|IN[+\-PNAB]\d*|[+\-]IN[A-Z0-9]*|IN_?[PN]\d*|[A-Z0-9]+_IN|\d+IN[+\-]?|IN[A-Z]?\d*[+\-]|TRIG[A-Z]*|TRG|TR|THR[A-Z]*|CV|CONT[A-Z]*|"
                   r"CTRL[A-Z0-9_]*|CTL|FB\d*|VFB|ADJ|SET|SENSE[A-Z0-9_+\-]*|SNS[A-Z0-9_+\-]*|CS[+\-]|SS|SS_?TR|ILIM|ISET|RT|"
                   r"RT_?CLK|COMP|DIR|STEP|PWM[A-Z0-9_]*|[HL]I|D|J|K|S|R|C?LR|PRE|IREF|RSET|RLIM|SEL\d*|A\d?|B\d?")),
]
# signed supplies (+VS, VCC+, +5V, +3V3 on top; -VS, VS-, -5V, VNEG at the bottom), only for a pin whose type is a
# supply's or says nothing: a typed signal named so keeps its type (ZXCT1009F Vs+ / Vs-, sense inputs; MAX232 VS+ /
# VS-, the charge pump's power_out)
SIGNED = {"ground": _R(r"-V(?:S|CC|DD|EE|SS)?\d*|V(?:S|CC|DD|EE|SS)\d*-|-\d+V\d*|VNEG\d*"),
          "power": _R(r"\+V(?:S|CC|DD|IN|POS)?\d*|V(?:S|CC|DD)\d*\+|\+\d+V\d*|VPOS\d*")}
SIGNED_TYPES = ("", "unspecified", "passive", "power", "power_in")
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
    if len(s) > 2 and not re.fullmatch(r".*(OUT|IN)\d*_[AB]", s):     # OUT_B / IN_B: channel B of a dual part, not active low
        s = re.sub(r"(#|\*|_N|_B|_L)$", "", s)
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
    full = primary(pin.get("name", "")).upper()   # the name before an active-low '_N' / '_B' / '_L' came off: a rule
    hit = next((g for g, rx in RX if g not in ("other_in", "other_out") and rx.match(full) and not rx.match(k)),
               None) if full != k else None       # written for that very spelling wins (VREF_N, the negative
    signed = t in SIGNED_TYPES
    for g, rx in RX:                              # reference: ground, not VREF)
        if (g == hit) if hit else (rx.match(k) or (signed and g in SIGNED and SIGNED[g].match(k))):
            if g == "usb" and k == "VBUS" and t in ("power", "power_in"):
                return "power"
            if g in ("other_in", "other_out") and t in DIRECTED:     # a name only hints the direction: the type says it
                return DIRECTED[t]
            return g
    if num in ("EP", "PAD", "EPAD", "TAB"):
        return "ep"
    if t == "ground":
        return "ground"
    if t in ("power", "power_in"):
        return "power"
    if t == "power_out":
        return "power_out"
    return DIRECTED.get(t, "other")


def _natural(s):
    return [int(x) if x.isdecimal() else x for x in re.split(r"(\d+)", str(s))]


# an FPGA's per-bank I/O supply: VCCIO_0..3 / VCCO_0..3 are one rail per bank (3.3 V on one, 1.8 V on another), so
# the number after the stem names the bank, never an instance of one supply (R-SY-4)
BANK_SUPPLY = re.compile(r"VCCIO|VCCO|VDDIO|VDDO|VIO")


def stack_key(name):
    """Pins whose names give the same key may be one net: 'VDD_1', 'VDD_2', 'VDD' -> 'VDD'. Only an index after a
    separator is dropped: 'VDD1' / 'VDD2' (an isolator's two sides) and 'VDD18' / 'VDD33' are different supplies, and
    a bank supply's (VCCIO_0, VCCO_2) never."""
    k = _key(name)
    m = re.fullmatch(r"(.+?)[_\-]\d+", k)
    return k if m and BANK_SUPPLY.fullmatch(m.group(1)) else (re.sub(r"[_\-]\d+$", "", k) or k)


def stack_keys(names):
    """name -> stacking key for the power pins of one group. 'VDD_1'..'VDD_5' (instance numbers 1..n or 0..n-1, each
    name once) share the key 'VDD'; 'VDD_18' + 'VDD_33' are not instance numbers and keep their own names (never
    stacked together), nor are a bank supply's (VCCIO_0 x2, VCCIO_1 x2: two banks, two rails) (R-SY-4)."""
    idx, seen, twice = {}, set(), set()
    for n in names:
        k = _key(n)
        (twice if k in seen else seen).add(k)
        m = re.fullmatch(r"(.+?)[_\-](\d+)", k)
        if m:
            idx.setdefault(m.group(1), []).append((int(m.group(2)), k))
    ok = set()
    for st, got in idx.items():
        ix = {i for i, _ in got}
        if BANK_SUPPLY.fullmatch(st) or any(k in twice for _, k in got):
            continue
        if ix in (set(range(1, len(ix) + 1)), set(range(len(ix)))):
            ok.add(st)
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
def _by_channel(items):
    """A dual / quad part's pins one channel at a time: 1IN-, 1IN+, 2IN-, 2IN+ (not 1IN-, 2IN-, 1IN+, 2IN+). Only
    when two or more names differ just by a channel number that is not at the end of the name (D0..D7 / A0..A7 are
    buses and keep their order) and every name has the same channels. items: [(_, index, pin)] sorted by stem."""
    split = [re.fullmatch(r"(\D*)(\d+)(\D+)", _key(t[2].get("name", ""))) for t in items]
    if len(items) < 4 or not all(split):
        return items
    stems = {}
    for m in split:
        stems.setdefault(m.group(1) + "#" + m.group(3), set()).add(int(m.group(2)))
    chans = list(stems.values())
    if len(stems) < 2 or len(chans[0]) < 2 or any(c != chans[0] for c in chans):
        return items
    order = {st: i for i, st in enumerate(stems)}
    key = {id(t): (int(m.group(2)), order[m.group(1) + "#" + m.group(3)]) for t, m in zip(items, split)}
    return sorted(items, key=lambda t: key[id(t)])


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
    for g in BUSES:                       # a bus group on the right whose pins are all type input is no bus but inputs
        items = by.get((g, ""))           # named like one (SS/TR, a soft-start input): they go left with the inputs
        if items and sides.get(g) == "right" and all(str(t[2].get("type") or "").lower() == "input" for t in items):
            by.setdefault(("other_in", ""), []).extend(by.pop((g, "")))
            by[("other_in", "")].sort(key=lambda t: t[1])
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
            if g not in POWER_GROUPS:
                items = _by_channel(items)
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
                       "side": sides[g], "slots": slots, "nets": {id(sl): k for sl, k in zip(slots, keys)},
                       "hide": {id(sl) for sl in slots if _hideable(g, sl)}})
    return blocks


DRIVES = ("power_out", "output", "open_collector", "open_emitter", "tristate", "hiz")


def _hideable(group, slot):
    """stack_power = "hide" ties hidden pins to a net by their name, design-wide: only a supply input or a ground may
    be hidden so. Two regulators' VOUT tabs (power_out) or two parts' EP pins on a net "VOUT" / "EP" would join
    rails; they stay stacked and shown (R-SY-5)."""
    ts = [str(p.get("type") or "").lower() for p in slot]
    if any(t in DRIVES for t in ts):
        return False
    if group == "analog":                     # AVDD / VREF+ in, but VREF_OUT / VREFBUF drive
        return all(t in ("power", "power_in") for t in ts)
    return group in ("power", "ground")


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


def _balance(blocks, power_side="top"):
    """Assign flexible blocks to left/right: left = fixed left + flex[:k], right = flex[k:] + fixed right. Ties keep
    'other' pins (no direction known) on the left with the inputs. power_side "left": the top blocks go first and the
    bottom blocks last on the left side, and the top and bottom edges stay empty."""
    top = [b for b in blocks if b["side"] == "top"]
    bot = [b for b in blocks if b["side"] == "bottom"]
    fixed_l = [b for b in blocks if b["side"] == "left" and b["group"] not in FLEX]
    fixed_r = [b for b in blocks if b["side"] == "right" and b["group"] not in FLEX]
    flex = [b for b in blocks if b["group"] in FLEX and b["side"] in ("left", "right")]
    head, tail = (top, bot) if power_side == "left" else ([], [])
    pieces = [pc for b in flex for pc in _pieces(b)]
    best = None
    for k in range(len(pieces) + 1):
        left, right = head + fixed_l + pieces[:k] + tail, pieces[k:] + fixed_r
        split = 0 < k < len(pieces) and pieces[k].get("piece") and pieces[k]["piece"][1] > 0
        hl, hr = _height(left), _height(right)
        moved = sum(1 for pc in pieces[k:] if pc["group"] == "other")
        cost = (max(hl, hr) + (SPLIT_PENALTY if split else 0), abs(hl - hr), moved, k)
        if best is None or cost < best[0]:
            best = (cost, left, right)
    _, left, right = best
    if power_side == "left":
        return {"left": left, "right": right, "top": [], "bottom": []}
    return {"left": left, "right": right, "top": top, "bottom": bot}


def _chunk(block, size):
    return [dict(block, slots=block["slots"][i:i + size]) for i in range(0, len(block["slots"]), size)] if size else [block]


def arrange(pins, rules=None, split=None):
    """pins -> [unit {'unit': n, 'sides': {side: [block, ...]}}]. split: True/False forces multi-part on/off."""
    s = settings(rules)
    blocks = _blocks(pins, s)
    big = s["split_pins"] and len(pins) > s["split_pins"]
    ps = s.get("power_side", "top")
    if not (big if split is None else split):
        return [{"unit": 1, "sides": _balance(blocks, ps)}]
    pw = [b for b in blocks if b["side"] in ("top", "bottom") or b["group"] in POWER_GROUPS or b["group"] == "nc"]   # part A
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
    out = [{"unit": 1, "sides": _balance(pw, ps)}] if pw else []
    return out + [{"unit": len(out) + i + 1, "sides": _balance(u, ps)} for i, u in enumerate(units)]


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
    pins = [dict(p, type=_ptype(p.get("type"))) for p in pins]      # 'Open Drain', 'Input' (a JSON pin list): ETYPE keys
    s = settings(rules)
    g, L = s["grid"] // 10, s["pin_length"] // 10
    units = arrange(pins, rules, split)
    prims, parts = [], []
    tallest = (0, 0)
    for u in units:
        sd = u["sides"]
        nets = {k: v for bl in sd.values() for b in bl for k, v in b.get("nets", {}).items()}
        hide = {k for bl in sd.values() for b in bl for k in b.get("hide", ())}
        left, right, top, bot = _rows(sd["left"]), _rows(sd["right"]), _rows(sd["top"]), _rows(sd["bottom"])
        tail = [b for b in sd["left"] if b["side"] == "bottom"]          # power_side "left": ground at the bottom
        if tail and len(right) > len(left):                                  # of the left side, not mid-way down (also
                                                                              # when ground is all the left side holds)
            head, foot = _rows([b for b in sd["left"] if b["side"] != "bottom"]), _rows(tail)
            left = head + [None] * (len(right) - len(head) - len(foot)) + foot
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
                    if s["stack_power"] == "hide" and id(sl) in hide:
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
def shared_numbers(pins):
    """One number is one pad: two signals on it short on the board. pins: [{num, name}] -> ['pin number 7 is used by 2
    different signals: WP, SDA']; pins of one stack (VSS, VSS_2) on a number are one signal. Also the release check's
    (R-SY-3)."""
    names = {}
    for p in pins:
        ns = names.setdefault(str(p.get("num")), [])
        if all(stack_key(n) != stack_key(p.get("name") or "") for n in ns):
            ns.append(p.get("name") or "")
    return [f"pin number {n} is used by {len(ns)} different signals: {', '.join(map(plain, ns))}"
            for n, ns in names.items() if len(ns) > 1]


def check(sym, grid=100):
    """Problems in a laid-out symbol: pins off the grid, two different pins at one spot (other than stacked power
    pins with the same name), a pin whose end or line lies on another pin's line, pin lines crossing, pin text that
    overlaps (text_problems). -> [text]."""
    g = grid // 10
    probs, at, segs, names = [], {}, [], {}
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
        segs.append((u, p, (x, y), (ex, ey)))
        names.setdefault(p["num"], []).append(p["name"])
    probs += shared_numbers([{"num": n, "name": nm} for n, ns in names.items() for nm in ns])
    # a pin whose end or body lies on another pin's line, or two pin lines crossing (stacked pins share one line: fine)
    on = lambda q, a, b: min(a[0], b[0]) <= q[0] <= max(a[0], b[0]) and min(a[1], b[1]) <= q[1] <= max(a[1], b[1]) and \
        (b[0] - a[0]) * (q[1] - a[1]) == (b[1] - a[1]) * (q[0] - a[0])
    for i, (u, p, a, b) in enumerate(segs):
        for v, q, c, d in segs[i + 1:]:
            if u != v or (a, b) == (c, d):
                continue
            if on(c, a, b) or on(d, a, b) or on(a, c, d) or on(b, c, d) or _cross(a, b, c, d):
                probs.append(f"pin {q['num']} lies on or crosses pin {p['num']}'s line in part {u}")
    return probs + text_problems(sym)


# pin text as Altium draws it (default 10 pt pin font), DXP units (10 mil): a little wide, so a symbol that passes looks
# clear in Altium and KiCad. The layout itself sizes bodies with CHAR (wider still).
TXT_W, TXT_H, TXT_GAP = 6, 8, 3
CONNECTOR_PREFIX = ("J", "P", "CN", "CON", "X", "XS", "XP", "JP", "SW", "S", "K", "T")   # pin order is the drawing


def _boxes(sym):
    """[(part, what, (x0, y0, x1, y1))] for every shown pin name and number, and {part: body rect}."""
    parts = sym.get("prim_parts") or [1] * len(sym["prims"])
    body, out = {}, []
    for pr, u in zip(sym["prims"], parts):
        if pr[0] == "rect":
            r = (min(pr[1], pr[3]), min(pr[2], pr[4]), max(pr[1], pr[3]), max(pr[2], pr[4]))
            if u not in body or (r[2] - r[0]) * (r[3] - r[1]) > (body[u][2] - body[u][0]) * (body[u][3] - body[u][1]):
                body[u] = r
    pos = {}
    for pr, u in zip(sym["prims"], parts):
        if pr[0] == "pin":
            pos.setdefault((u, pr[2], pr[3] if pr[2] in ("left", "right") else pr[4]), set()).add(pr[4] if pr[2] in ("left", "right") else pr[3])
    gaps = [b - a for v in pos.values() for a, b in zip(sorted(v), sorted(v)[1:])]
    f = min(1.0, min(gaps or [10]) / 10)          # a 50 mil pitch symbol is drawn with a font half as big
    tw, th, tg = TXT_W * f, TXT_H * f, TXT_GAP * f
    for pr, u in zip(sym["prims"], parts):
        if pr[0] != "pin":
            continue
        _, p, side, x, y, L, *show = pr
        if p.get("hidden"):
            continue
        sn = p.get("show_name", show[0] if show else True) and plain(p.get("name", "")) not in ("", "~")
        sd = p.get("show_num", show[0] if show else True) and str(p.get("num", ""))
        n, m, h = len(plain(p.get("name", ""))) * tw, len(str(p.get("num", ""))) * tw, th / 2
        if side in ("left", "right"):
            s = 1 if side == "left" else -1                       # +1: the body is to the right of the pin's end
            if sn:
                a = x + s * tg
                out.append((u, f"pin {p['num']} name", (min(a, a + s * n), y - h, max(a, a + s * n), y + h)))
            if sd:
                mx = x - s * L / 2
                out.append((u, f"pin {p['num']} number", (mx - m / 2, y + f, mx + m / 2, y + f + th)))
        else:
            s = -1 if side == "top" else 1                        # names run down from a top pin, up from a bottom one
            if sn:
                a = y + s * tg
                out.append((u, f"pin {p['num']} name", (x - h, min(a, a + s * n), x + h, max(a, a + s * n))))
            if sd:
                my = y - s * L / 2
                out.append((u, f"pin {p['num']} number", (x - f - th, my - m / 2, x - f, my + m / 2)))
    return out, body


def text_problems(sym):
    """Pin text that would overlap in Altium: two pin names or numbers on top of each other, or a pin name running
    out of the body (the estimate: TXT_W per character, TXT_H high). -> [text]."""
    out, body = _boxes(sym)
    probs = []
    hit = lambda a, b: a[0] < b[2] - 0.5 and b[0] < a[2] - 0.5 and a[1] < b[3] - 0.5 and b[1] < a[3] - 0.5
    for i, (u, w, a) in enumerate(out):
        r = body.get(u)
        if r and w.endswith("name") and (a[0] < r[0] - 0.5 or a[2] > r[2] + 0.5 or a[1] < r[1] - 0.5 or a[3] > r[3] + 0.5):
            probs.append(f"{w} runs out of the body" + (f" in part {u}" if len(body) > 1 else ""))
        for v, w2, b in out[i + 1:]:
            if u == v and hit(a, b):
                probs.append(f"{w} overlaps {w2}" + (f" in part {u}" if len(body) > 1 else ""))
    return probs


def is_box_ic(sym):
    """True for a plain IC symbol: designator U / IC (or 8+ pins and not a connector), one part, a rectangle body with every pin on its edge, and nothing
    else drawn but small marks (a pin 1 dot, text). Those can be re-laid out by the house rule without losing a drawing;
    connectors, transistors, op-amp triangles and logic gates cannot."""
    if sym.get("parts", 1) > 1 or any(sym.get("prim_modes") or []):
        return False
    pre = re.sub(r"[^A-Z]", "", str(sym.get("designator", "")).upper())
    rects = [pr for pr in sym["prims"] if pr[0] == "rect"]
    pins = [pr for pr in sym["prims"] if pr[0] == "pin"]
    if len(rects) != 1 or len(pins) < 3 or pre in CONNECTOR_PREFIX or (pre not in ("U", "IC") and len(pins) < 8):
        return False
    x0, x1 = sorted((rects[0][1], rects[0][3]))
    y0, y1 = sorted((rects[0][2], rects[0][4]))
    if any(pr[0] in ("line", "polyline", "polygon", "arc", "bezier") or pr[0] == "circle" and pr[3] > 10
           for pr in sym["prims"]):
        return False
    edge = {"left": lambda x, y: abs(x - x0) < 1 and y0 <= y <= y1, "right": lambda x, y: abs(x - x1) < 1 and y0 <= y <= y1,
            "top": lambda x, y: abs(y - y1) < 1 and x0 <= x <= x1, "bottom": lambda x, y: abs(y - y0) < 1 and x0 <= x <= x1}
    return all(edge[pr[2]](pr[3], pr[4]) for pr in pins)


def _cross(a, b, c, d):
    """Axis-aligned segments a-b and c-d (one horizontal, one vertical) cross in their interiors."""
    if (a[0] == b[0]) == (c[0] == d[0]):
        return False
    (h1, h2), (v1, v2) = ((a, b), (c, d)) if a[1] == b[1] else ((c, d), (a, b))
    return min(h1[0], h2[0]) < v1[0] < max(h1[0], h2[0]) and min(v1[1], v2[1]) < h1[1] < max(v1[1], v2[1])


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
                "unspecified": "passive", "free": "passive", "no_connect": "nc", "": "passive", "p": "power", "analog": "passive",
                "open_drain": "open_collector", "opendrain": "open_collector", "od": "open_collector", "oc": "open_collector",
                "hi_z": "hiz", "high_z": "hiz"}


def _ptype(t):
    """A pin type as a CSV, a JSON pin list, a datasheet or KiCad spells it ('Open Drain', 'tri-state', 'Input',
    'bidirectional') -> an altium_lib.ETYPE key; one it does not know is passive."""
    t = re.sub(r"[\s-]+", "_", str(t or "").strip().lower())
    t = TYPE_ALIASES.get(t, t)
    return t if t in AL.ETYPE else "passive"


def unknown_types(pins):
    """[(pin number, type as given)] for the pins whose type is no pin type _ptype knows ('ouptut'): they are drawn
    passive, which is never silent (the CLI prints them, the librarian lists them for review)."""
    norm = lambda t: TYPE_ALIASES.get(re.sub(r"[\s-]+", "_", str(t or "").strip().lower()),
                                      re.sub(r"[\s-]+", "_", str(t or "").strip().lower()))
    given = lambda p: p.get("type_given") or p.get("type")       # a CSV row's own spelling (pins_from_csv)
    return [(str(p.get("num")), str(given(p))) for p in pins or []
            if given(p) not in (None, "") and norm(given(p)) not in AL.ETYPE]


def unknown_types_line(pins):
    """unknown_types as one line, or None."""
    bad = unknown_types(pins)
    if not bad:
        return None
    return (f"pin type not known, drawn passive: " + ", ".join(f"pin {n} {t!r}" for n, t in bad[:8])
            + (" ..." if len(bad) > 8 else "") + " (input, output, io, passive, power_in, power_out, open_collector, "
            "tristate or nc); check the type")


class PinListError(ValueError):
    """A pin list that cannot make a symbol (no pins, a pin without a number): one plain line for the CLI."""
    plain_error = True
    hint = "a pin CSV needs a header (num,name[,type][,group]) and one row per pin, each with its number"


def pins_from_csv(path):
    """CSV/TSV with a header: number (num/pin/#), name, optional type, group. -> pins. Comma, tab or semicolon
    (Excel's CSV in many locales). A row with a name but no number is refused, never dropped."""
    text = Path(path).read_text(encoding="utf-8-sig")
    lines = text.splitlines()
    if not lines:
        raise PinListError(f"{Path(path).name} is empty: no pins")
    head = lines[0]
    sep = "\t" if "\t" in head else ";" if head.count(";") > head.count(",") else ","
    rows = list(csv.DictReader(lines, dialect="excel-tab" if sep == "\t" else "excel", delimiter=sep))
    pick = lambda r, *ks: next((str(r[k]).strip() for k in r if k and k.strip().lower() in ks and r[k] is not None), "")
    out = []
    for i, r in enumerate(rows, 2):
        num, name = pick(r, "num", "number", "pin", "#", "pin number", "pin_number"), pick(r, "name", "pin name", "signal", "pin_name")
        if not num:
            if name or any(str(v or "").strip() for k, v in r.items() if k):
                raise PinListError(f"{Path(path).name} line {i}: pin {name or '(no name)'} has no pin number")
            continue
        raw = pick(r, "type", "electrical", "electrical type", "etype", "dir")
        p = {"num": num, "name": name or num, "type": _ptype(raw)}
        if unknown_types([{"num": num, "type": raw}]):
            p["type_given"] = raw                       # drawn passive: said by unknown_types_line
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
    if not pins:                                         # never replace a symbol with a pinless one
        raise PinListError(f"{a.source}: no pins (is the header num,name? is it one row per pin?)")
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
        ks = X.symbol_from_schlib(out, X.ksym(name), notes)
        own = {str(p["num"]): {"power_out": "power_out", "nc": "no_connect"}.get(_ptype(p.get("type"))) for p in pins}
        X.restore_types(ks, {k: v for k, v in own.items() if v}, guess=False)   # Altium has no power_out / nc: the
        #                                                  source's types back (never guessed from names: the list says)
        tree = ["kicad_symbol_lib", ["version", X.SYM_VERSION], ["generator", sexpr.Q("ecadlib")], ["generator_version", sexpr.Q("8.0")], ks]
        Path(a.kicad).write_text(sexpr.dumps(tree), encoding="utf-8", newline="\n")
        print(f"  KiCad symbol -> {a.kicad}")
    if a.preview:
        Path(a.preview).write_text(svg(sym), encoding="utf-8")
        print(f"  preview -> {a.preview}")
    for pr in probs:
        print(f"  problem: {pr}")
    odd = unknown_types_line(pins)
    if odd:                                              # never silent (it was a crash before symexp-01)
        print(f"  warning: {odd}")
    return 1 if probs else 0


if __name__ == "__main__":
    sys.exit(main())
