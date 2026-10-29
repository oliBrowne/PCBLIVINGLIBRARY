"""Round-two review (2026-10-29), state area: change_diff, dedupe, ledger, approvals and friends."""
from ecadlib import change_diff as CD, fingerprint as FP
from ecadlib.dedupe import mpn_key


def _fp(value, desc):
    return FP.build("P1", params={"Value": value, "Manufacturer Part Number": "RC0603FR-0710KL", "Description": desc})


def _classes(a, b, desc):
    return [(c["class"], c["text"]) for c in CD.diff(_fp(a, desc), _fp(b, desc))]


def test_value_tolerance_plus_minus_is_not_an_identity_change():
    """change_diff: '10k 1%' -> '10k ±1%' was held as an identity change (a tolerance is ± by nature)."""
    got = _classes("10k 1%", "10k ±1%", "Resistor")
    assert got and all(c == "cosmetic" for c, _ in got), got
    got = _classes("10k 1%", "10k ± 1%", "Resistor")
    assert got and all(c == "cosmetic" for c, _ in got), got
    assert CD.worst(CD.diff(_fp("10k 1%", "Resistor"), _fp("10k ±5%", "Resistor"))) == "identity"   # still a change


def test_value_np0_is_c0g():
    """change_diff: '22pF NP0' -> '22pF C0G' was held as an identity change (NP0 and C0G name one EIA class 1 dielectric)."""
    for a in ("22pF NP0", "22pF NPO", "22pF COG"):
        got = _classes(a, "22pF C0G", "Capacitor")
        assert got and all(c == "cosmetic" for c, _ in got), (a, got)
    assert CD.worst(CD.diff(_fp("22pF C0G", "Capacitor"), _fp("22pF X7R", "Capacitor"))) == "identity"


def test_value_vdc_is_v():
    """change_diff: '100nF 50V' -> '100nF 50 VDC' was held as an identity change (electrical_facts: 16 VDC == 16V)."""
    for b in ("100nF 50 VDC", "100nF 50VDC", "100nF 50Vdc"):
        got = _classes("100nF 50V", b, "Capacitor")
        assert got and all(c == "cosmetic" for c, _ in got), (b, got)
    assert CD.worst(CD.diff(_fp("100nF 50V", "Capacitor"), _fp("100nF 16VDC", "Capacitor"))) == "identity"


def test_mpn_key_strips_adi_lt_reel_suffixes():
    """dedupe.mpn_key: ADI/LT packaging suffixes #TRMPBF (mini reel) and -RL / -RL7 were kept, so one part was not grouped."""
    assert mpn_key("LT1761ES5-5#TRMPBF") == mpn_key("LT1761ES5-5#PBF") == mpn_key("LT1761ES5-5")
    assert mpn_key("LT1761ES5-5#TRM") == mpn_key("LT1761ES5-5")
    assert mpn_key("ADP3338AKCZ-3.3-RL7") == mpn_key("ADP3338AKCZ-3.3-REEL7") == mpn_key("ADP3338AKCZ-3.3")
    assert mpn_key("AD8605ARTZ-RL") == mpn_key("AD8605ARTZ")
    assert mpn_key("ADP3338AKCZ-3.3") != mpn_key("ADP3338AKCZ-5.0")
