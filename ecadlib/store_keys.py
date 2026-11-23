"""Shared store of confirmed parts: how a part is named there (docs/shared-store-plan.md).

A part's key is its maker and its exact orderable part number, both reduced the way the rest of ecadlib compares
them: the maker by providers.maker_key ("TI", "Texas Instruments Inc." -> TEXASINSTRUMENTS), the MPN by
kicad_index.norm_mpn (case and punctuation dropped, a decimal point between digits kept: SMAJ5.0A is not SMAJ50A).
Packing suffixes are NOT stripped here: a suffix can change the package, so a record lists the other codes its land
covers itself ("also"), and nothing is guessed by prefix.

    key("Texas Instruments", "NE555DR")  -> "TEXASINSTRUMENTS|NE555DR"
    key_id(key)                          -> 32 hex digits, the record's file name: parts/<shard>/<key_id>.json
    shard("NE555DR")                     -> 2 hex digits from the MPN alone (index/<shard>.json), so a BOM line that
                                            names no maker still finds the shard its part is in
    dumps(obj) / digest(obj)             -> canonical JSON (sorted keys, no spaces, UTF-8) and its sha256
"""
import hashlib, json

SCHEMA = 1
SHARDS = 256


def norm_maker(name):
    """'' for no maker (None, 'N/A')."""
    from .providers import maker_key
    from .release_check import _filled
    return maker_key(str(name)) if _filled(name) else ""


def norm_mpn(mpn):
    """'' for an empty MPN or a placeholder ('N/A', 'TBD')."""
    from .bom_triage import placeholder_mpn
    from .kicad_index import norm_mpn as _norm
    return "" if placeholder_mpn(mpn) else _norm(mpn)


def key(maker, mpn):
    """'MAKER|MPN'; '' when there is no MPN. The maker part may be empty ('|NE555DR'): such a key names no record,
    it is only what a lookup without a maker searches by (mpn_of)."""
    m = norm_mpn(mpn)
    return f"{norm_maker(maker)}|{m}" if m else ""


def maker_of(k):
    return str(k).split("|", 1)[0]


def mpn_of(k):
    return str(k).split("|", 1)[-1]


def key_id(k):
    return hashlib.sha256(str(k).encode("utf-8")).hexdigest()[:32]


def shard(mpn_or_key):
    """The index shard of a part: by its normalised MPN only. Takes a key ('MAKER|MPN') or a raw MPN."""
    s = str(mpn_or_key)
    m = mpn_of(s) if "|" in s else norm_mpn(s)
    return hashlib.sha256(m.encode("utf-8")).hexdigest()[:2]


def dumps(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest(obj):
    return hashlib.sha256(dumps(obj).encode("utf-8")).hexdigest()
