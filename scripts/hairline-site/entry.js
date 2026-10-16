// Bundle entry for site/hairline.js (build_site_figures.sh puts it in hairline's src/ and runs esbuild).
// The figures the landing page uses from @lucasmarkes/hairline, plus board.js: our own figure, written with the hairline-create skill.
import { create } from "./mount";
import { TABLE } from "./intensity";
import * as iso from "./core/iso";
import * as motion from "./core/motion";
import * as stage from "./core/stage";
export { sieve, slow, loupe, dish, plug, rebuild, terminal } from "./index";

const HL = { ...iso, ...motion, ...stage };
let BOARD;
const hairline = (def) => { BOARD = def; };
/*BOARD*/
TABLE.board = BOARD.range;

/** A small PCB whose parts hover over their pads; the pointer seats them. */
export function board(el, options) {
  return create({ id: "board", label: BOARD.means, rest: "rest", engine: BOARD.mount, tour: BOARD.tour }, el, options);
}

/** Turns the board's camera to an azimuth in degrees; every mounted board follows on its spring. */
export function turn(deg) {
  TURN.deg = deg;
  TURN.wake.forEach((wake) => wake());
}
