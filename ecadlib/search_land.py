"""The generic KiCad land a part search's own measurements name, where its case text names none.

DigiKey files an SMD aluminium can under 'Radial, Can - SMD', a crystal under '2-SMD, No Lead', a pin header under no
case at all: no standard-table land. But it also gives the part's measurements (kept by triage as `src_dims`): the
can's diameter, base and height, the crystal's body, a through-hole part's lead spacing, a header's rows, positions
and pitch. For a few kinds of part those measurements are the whole land, and KiCad draws one generic footprint per
measurement (CP_Elec_6.3x7.7, Crystal_SMD_3215-2Pin_3.2x1.5mm, PinHeader_1x04_P2.54mm_Vertical):

  can      an SMD aluminium can: diameter, a square base 0 - 0.7 mm wider, and its height;
  radial   a through-hole radial can: diameter and lead spacing;
  crystal  a 2- or 4-pad leadless crystal by pad count and body, an SMD HC-49/US by its body;
  header   an unshrouded, straight, through-hole male pin header (or one-row socket), every position loaded;
  box      a through-hole radial box (film) capacitor: body length, width and lead spacing.

land() only names candidates. The librarian (Run.measured_land) loads them and releases the part without a person's
look only when every candidate is the same land (librarian.lands_agree): then whatever the measurements leave open
(the can's exact height, a 0.1 mm rounding of the body) cannot change the copper. Anything else names nothing, and the
part waits for its datasheet as before. Never used when the BOM names a footprint or several makers sell the MPN.
"""
import re

SEP = " | "


def dims_text(d):
    """{key: text} -> the triage cell: 'size=0.248" Dia (6.30mm) | height=0.303" (7.70mm)'."""
    return SEP.join(f"{k}={str(v).replace('|', '/').strip()}" for k, v in (d or {}).items() if str(v or "").strip())


def dims(cell):
    """The triage cell back to {key: text}."""
    out = {}
    for bit in str(cell or "").split(SEP):
        k, eq, v = bit.partition("=")
        if eq and k.strip() and v.strip():
            out.setdefault(k.strip(), v.strip())
    return out


def _mm(text):
    """The millimetre numbers of a measurement as DigiKey writes it: '0.260" L x 0.260" W (6.60mm x 6.60mm)' ->
    [6.6, 6.6]; '0.197" (5.00mm)' -> [5.0]. Only the bracket's numbers; [] when it has none."""
    inner = re.search(r"\(([^()]*mm[^()]*)\)", str(text or ""))
    return [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)\s*mm\b", inner.group(1))] if inner else []


def _num(x):
    """6.3 -> '6.3', 8.0 -> '8' (as KiCad's CP_Elec_8x10.5 writes a whole number)."""
    return f"{x:.2f}".rstrip("0").rstrip(".")


def _one(text):
    v = _mm(text)
    return v[0] if len(v) == 1 else None


def _nearest(cands):
    """cands [(name, distance)] -> names, the nearest first (ties by name)."""
    return [n for n, _ in sorted(cands, key=lambda c: (round(c[1], 3), c[0]))]


def land(cell, kind, description, names, several=False):
    """-> ({"rule", "pick": KiCad footprint name, "same": [the other candidates, which must all be the same land],
    "why"}, "") or (None, why not). cell: triage's src_dims; kind: the librarian's passive kind (R C CP L LED or None);
    description: the part search's text; names: every KiCad footprint name (a container)."""
    d = dims(cell)
    if not d:
        return None, ""
    if several:
        return None, "several makers sell this part number: the measurements may be another maker's part"
    desc = str(description or "")
    mount = d.get("mounting", "")
    case = d.get("case", "")
    dia = re.fullmatch(r'[\d.]+"\s*Dia\s*\((\d+(?:\.\d+)?)mm\)', d.get("size", ""))
    body = _mm(d.get("size")) if not dia and re.search(r"\bL x\b.*\bW\b", d.get("size", "")) else []
    h = _one(d.get("height"))
    if kind == "CP" and dia and mount == "Surface Mount" and re.fullmatch(r"Radial, Can - SMD", case):
        D, base = float(dia.group(1)), _mm(d.get("land"))
        if len(base) != 2 or base[0] != base[1] or not -0.01 <= base[0] - D <= 0.7 or h is None:
            return None, f"SMD can {_num(D)} mm: the part search gives no square base and height to go with it"
        c = [(n, abs(float(m.group(1)) - h)) for n in names
             if (m := re.fullmatch(rf"CP_Elec_{re.escape(_num(D))}x(\d+(?:\.\d+)?)", n))]
        if not c:
            return None, f"SMD can {_num(D)} x {_num(h)} mm: KiCad has no CP_Elec land of that diameter"
        order = _nearest(c)
        return {"rule": "can", "pick": order[0], "same": order[1:],
                "why": f"SMD aluminium can, diameter {_num(D)} mm, base {_num(base[0])} mm square, height {_num(h)} mm"}, ""
    if kind == "CP" and dia and mount == "Through Hole" and re.fullmatch(r"Radial, Can", case):
        D, p = float(dia.group(1)), _one(d.get("lead_spacing"))
        n = f"CP_Radial_D{D:.1f}mm_P{p:.2f}mm" if p else ""
        if n not in names:
            return None, f"radial can {_num(D)} mm: no lead spacing given, or KiCad has no CP_Radial land for it"
        return {"rule": "radial", "pick": n, "same": [],
                "why": f"radial aluminium can, diameter {_num(D)} mm, lead spacing {_num(p)} mm"}, ""
    if kind == "C" and len(body) == 2 and mount == "Through Hole" and case == "Radial" \
            and re.search(r"(?i)\bfilm\b", desc) and d.get("termination", "PC Pins") == "PC Pins":
        p = _one(d.get("lead_spacing"))
        c = []
        for n in names:
            m = re.fullmatch(r"C_Rect_L(\d+(?:\.\d+)?)mm_W(\d+(?:\.\d+)?)mm_P(\d+\.\d\d)mm(?:_[A-Z0-9]+)*", n)
            if m and p and abs(float(m.group(3)) - p) < 0.005 and abs(float(m.group(1)) - body[0]) <= 0.35 \
                    and abs(float(m.group(2)) - body[1]) <= 0.35:
                c.append((n, abs(float(m.group(1)) - body[0]) + abs(float(m.group(2)) - body[1]) + len(n) / 1e4))
        if not c:
            return None, "radial box capacitor: no lead spacing given, or KiCad has no C_Rect land of that body and spacing"
        order = _nearest(c)
        return {"rule": "box", "pick": order[0], "same": order[1:],
                "why": f"radial box capacitor, body {_num(body[0])} x {_num(body[1])} mm, lead spacing {_num(p)} mm"}, ""
    if kind is None and re.search(r"(?i)\bcrystal\b", d.get("type", "")) and re.search(r"(?i)\bcrystal\b", desc) \
            and not re.search(r"(?i)\bosc|\bXO\b|resonator|MEMS", desc + " " + d.get("type", "")) and mount == "Surface Mount":
        pads = re.fullmatch(r"([24])-SMD, No Lead", case)
        if pads and len(body) == 2:
            L, W = max(body), min(body)
            c = [n for n in names if re.fullmatch(rf"Crystal_SMD_\d{{4}}-{pads.group(1)}Pin_{L:.1f}x{W:.1f}mm", n)]
            if len(c) != 1:
                return None, f"{pads.group(1)}-pad crystal {L:.1f} x {W:.1f} mm: KiCad has no one generic land of that body"
            return {"rule": "crystal", "pick": c[0], "same": [],
                    "why": f"{pads.group(1)}-pad leadless crystal, body {L:.1f} x {W:.1f} mm"}, ""
        if re.fullmatch(r"HC-?49/US", case) and len(body) == 2 and abs(max(body) - 11.4) <= 0.3 \
                and abs(min(body) - 4.7) <= 0.3 and "Crystal_SMD_HC49-SD" in names:
            return {"rule": "crystal", "pick": "Crystal_SMD_HC49-SD", "same": [],
                    "why": f"SMD HC-49/US crystal, body {_num(max(body))} x {_num(min(body))} mm"}, ""
        return None, ""
    if kind is None and d.get("connector"):
        kindc = d["connector"]
        male = re.fullmatch(r"Header(?:, (?:Cuttable|Breakaway))?", kindc) and d.get("contact") == "Male Pin" \
            and d.get("shrouding") == "Unshrouded"
        female = kindc == "Receptacle" and d.get("contact") in ("Female Socket", "Forked")
        p, pos = _one(d.get("pitch")), d.get("positions", "")
        if not (male or female) or mount != "Through Hole" or d.get("termination") != "Solder" \
                or d.get("rows") != "1" or d.get("loaded") != "All" or d.get("features") or not pos.isdigit() or not p:
            return None, ""                         # shrouded, keyed, right-angle, SMD, two rows: its maker's own land
        n = f"{'PinHeader' if male else 'PinSocket'}_1x{int(pos):02d}_P{p:.2f}mm_Vertical"
        if n not in names:
            return None, f"plain one-row {'header' if male else 'socket'}: KiCad has no land {n}"
        return {"rule": "header", "pick": n, "same": [],
                "why": f"straight through-hole {'pin header' if male else 'socket'}, one row, {int(pos)} positions "
                       f"all loaded, pitch {_num(p)} mm, " + ("unshrouded" if male else "no features")}, ""
    return None, ""
