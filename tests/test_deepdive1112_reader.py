"""Deep-dive testing 2026-11-12, reader: a read that names a package of the checked standard table but builds copper
far from that package's land was built with no word. The name checks compared only numbers written in the name
('2x2mm', 'P0.65mm') and a gull-wing's lead span, so a name with no numbers in it had nothing to contradict:
  * a 3.1 x 1.6 mm chip read named R_0402_1005Metric came back 'ok';
  * an SMA body named D_SOD-123 and a 0402 read 5 mm wide: both 'ok'.
A tab package's thermal pad is left out of the comparison: makers draw it in too many sizes."""
import copy
from ecadlib import datasheet as D
from tests import test_fix1026_rd as T


def held(t):
    return D.read_hold(T.read(copy.deepcopy(t)))


def test_true_reads_of_table_packages_are_not_held():
    for t in (T.R1206, T.R0402, T.SMA, T.SMB, T.SMC, T.MELF, T.SOIC8, T.TSSOP14, T.MSOP8, T.SOT89, T.SOT23, T.DPAK,
              T.SOD123):
        assert held(t) is None, (t["name"], held(t))


def test_a_chip_read_under_another_size_code_is_held():
    for name in ("R_0402_1005Metric", "R_0603_1608Metric", "R_2512_6332Metric"):
        h = held(dict(T.R1206, name=name))
        assert h and "too far apart" in h and name in h, (name, h)


def test_a_diode_body_under_another_package_name_is_held():
    h = held(dict(T.SMA, name="D_SOD-123"))
    assert h and "too far apart" in h, h
    h = held(dict(T.SMC, name="D_SMA"))
    assert h and "too far apart" in h, h


def test_a_gross_misread_of_a_named_table_package_is_held():
    for t in (dict(T.R0402, body_width=T.sc(T.R0402["body_width"], 10)),         # a 0402 5 mm wide
              dict(T.R1206, body_length=T.sc(T.R1206["body_length"], 2)),        # 6.2 mm under the name 3216Metric
              dict(T.MELF, body_width=T.sc(T.MELF["body_width"], 10))):
        h = held(t)
        assert h and "too far apart" in h, (t["name"], h)


def test_a_tab_packages_thermal_pad_is_not_compared():
    # the generator ends a DPAK / D2PAK / SOT-89 tab on a pad at the lead span's edge, KiCad's land has a thermal pad
    # under the body: true reads of these were held when the whole land was compared
    from tests import test_debugsweep_dsread as DS
    res = DS._tab_read("TO-263-5_TabPin3", "1.70", ["1", "2", "3", "4", "5"], "3", 5, size=DS.D2PAK)
    assert D.read_hold(res) is None, D.read_hold(res)
    assert held(T.DPAK) is None and held(T.SOT89) is None


def test_a_makers_land_a_little_off_the_table_land_is_not_held():
    # real reads (benchmark 2026-10-25) sit up to 0.65 mm off the table land and are right by the maker's drawing:
    # an MSOP's shorter foot, a chip a tolerance step longer
    assert held(dict(T.MSOP8, lead_span=(4.5, 4.65, 4.8))) is None
    assert held(dict(T.R1206, body_length=(3.2, 3.4, 3.6))) is None
    assert held(dict(T.SMA, body_width=(2.5, 2.9, 3.2))) is None
