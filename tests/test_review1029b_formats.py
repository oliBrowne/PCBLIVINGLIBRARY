"""Round two review (formats): KiCad / Altium file round trips."""
from tests.fixtures_lite import lite

from ecadlib import sexpr


def test_courtyard_bbox_counts_arc_segments_of_a_polygon():
    """sexpr.courtyard_bbox ignored the (arc ...) segments KiCad 7+ writes inside an fp_poly's (pts ...)."""
    t = sexpr.parse('(footprint "X" (fp_poly (pts (xy -3 -3) (xy 3 -3) (xy 3 3) (xy -3 3) '
                    '(arc (start -3 3) (mid -3.5 0) (end -3 -3))) (stroke (width 0.05) (type solid)) (fill none) '
                    '(layer "F.CrtYd")))')
    bb = sexpr.courtyard_bbox(t)
    assert bb is not None and abs(bb[0] - -3.5) < 1e-9 and bb[1:] == (-3.0, 3.0, 3.0), bb
