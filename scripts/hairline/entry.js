// Bundle entry for ecadlib/ui/static/hairline.js (build_ui_figures.sh puts it in hairline's src/ and runs esbuild).
// The figures the UI uses from @lucasmarkes/hairline, plus seat.js: our own figure, written with the hairline-create skill.
import { create } from "./mount";
import { TABLE } from "./intensity";
import * as iso from "./core/iso";
import * as motion from "./core/motion";
import * as stage from "./core/stage";
export { slow, drawer, sieve } from "./index";

const HL = { ...iso, ...motion, ...stage };
let SEAT;
const hairline = (def) => { SEAT = def; };
/*SEAT*/
TABLE.seat = SEAT.range;

/** A chip over its footprint; the pointer seats it on the pads. */
export function seat(el, options) {
  return create({ id: "seat", label: SEAT.means, rest: "rest", engine: SEAT.mount, tour: SEAT.tour }, el, options);
}
