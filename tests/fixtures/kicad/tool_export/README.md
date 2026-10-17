Footprints from KiCad's official library (https://gitlab.com/kicad/libraries/kicad-footprints, CC-BY-SA 4.0 with
the library exception), unchanged. tests/test_tool_exports.py writes each one as a Fusion Electronics / EAGLE package
and as an EasyEDA Pro footprint and reads it back. They were picked from a survey of the whole KiCad library for what
is hard to carry into those formats: custom pads (with anchors, outline widths, rings, several shapes), paste windows
drawn as pads or graphics, a through-hole pad whose copper is off the hole, NPTH holes with copper and a bigger mask
opening, thermal via arrays and rounded through-hole pads.
