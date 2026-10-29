# Roadmap

Last updated 2026-10-29. What ecadlib is for, who it is for and how success is measured are in
[docs/PRODUCT_SPEC.md](docs/PRODUCT_SPEC.md). Every planned item below has an issue, and every milestone is a
[GitHub milestone](https://github.com/oliBrowne/PCBLIVINGLIBRARY/milestones) with the same due date.

Milestones follow the package version (v0.4.0, v0.5.0, v0.6.0), not v1 / v1.1: ecadlib is at 0.4.0 on PyPI, and
the changelog keeps 0.x minor releases free to change command-line options and file formats until 1.0.0.

**Focus until v0.5.0:** a proven BOM-to-Altium 365 flow with a published accuracy benchmark, and real-BOM trials
with 3 to 5 Altium users (from the 2026-10-17 market-fit review). Altium (Altium 365 first) and KiCad are the
supported targets. The Fusion Electronics / EAGLE and EasyEDA Pro exports were removed on 2026-10-18 (P13, below).
On 2026-10-19 five more features were cut and three parked for the same reason
([Cut on 2026-10-19](#cut-on-2026-10-19), [Parked](#parked)).

## Shipped

Dates are the day each pull request was merged to `main`. PRs #1-#12 were opened on 2026-10-13 and 2026-10-14 and
merged together on 2026-10-15. GitHub shows some PRs as closed rather than merged because the history was rewritten
on 2026-10-17; their commits are on `main`. Some of what shipped was cut or parked on 2026-10-19 (see below); the
table is kept as the record of what was merged.

| Merged | PRs | What shipped |
|---|---|---|
| 2026-10-13 | (first commits) | **v0.1.0** prototypes: IPC-7351B footprint generator, BOM triage, Altium DbLib export |
| 2026-10-14 | #4 | KiCad import, datasheet reader, lifecycle watcher, BOM-to-library librarian, release check, upstream alerts, CI |
| 2026-10-15 | #1, #2, #3, #5, #6, #7 | **v0.2.0**: Altium 365 exporter (Nexar), pure-Python `.PcbLib` / `.SchLib` writer, local web UI, MCP server and CLI, held-part picker, KiCad output, DbLib sync, 3D models, sourcing, approvals, library rules, two-way Altium 365 sync, upkeep, sim models, Altium projects |
| 2026-10-15 | #8, #9, #10, #11, #12 | Datasheet batch reader, big-chip symbols, BGA / connector footprints, team server and Docker, library agent with review queue, 78 review fixes with soak / fuzz / real-world BOM tests, real Altium / KiCad checks |
| 2026-10-16 | #13, #14, #15, #16 | 331-part accuracy set, standard parts without a model, cheaper datasheet reads, any model with your own key, end-to-end tests, cross-checked reads, KiCad / Altium parity, accuracy gate in CI, safe upgrades, first Altium menu |
| 2026-10-16 and 17 | #17, #18, #19, #20, #21, #22, #23, #24, #25, #26 | Hairline web UI and landing page, one plain voice in every interface, 42 bugs from the full technical review, cost per job for every model, library hub, footprint rule book in the release gate, Fusion / EAGLE and EasyEDA Pro exports, Windows installer and setup page |
| 2026-10-17 | #27, #28, #29, #30, #33 | Altium menu fixes from a real AD26 PC, house style (`ecadlib style`), one pin rule for every IC symbol, tidier converted footprints, the ecadlib dialog in Altium and window in KiCad, EasyEDA credit on converted parts |
| 2026-10-17 | #31, #32, #34 | **v0.3.0** released: on PyPI (`pip install ecadlib`) and as a Windows installer on the GitHub release |
| 2026-10-17 | #35 | Official part APIs first with unofficial endpoints fenced off, weekly sources canary, and Altium rebuilding every library itself (8 of 8 test libraries, 0 differences on AD26) |
| 2026-10-17 | #61-#80 (issues #36-#40) | **v0.3.1** released: Build libraries in the Altium menu, fab-layer designator in Altium, multi-part symbols, keep-outs and bottom-side pads checked in AD26, and the fixes from the overnight system test |
| 2026-10-17 to 19 | #81-#125 | Released in v0.4.0: change control ([#105](https://github.com/oliBrowne/PCBLIVINGLIBRARY/issues/105), `ecadlib part`), time from BOM to library and the review rate in `ecadlib status` (P7, P8), the cost shown before every AI step (P12), docs/START_HERE.md (P14), the first live benchmark run (docs/benchmark.md), `ecadlib mcp --http`, and the 2026-10-19 cut and park |
| 2026-10-19 to 28 | #126-#204 | **v0.4.0** released on 2026-10-29: the release gate, Nexar sign-in, the live benchmark at 87 of 110 parts fully right with none built wrong, and the scope cut to Altium and KiCad. The milestones below were renumbered: the BOM-to-Altium 365 flow is now v0.5.0 |

## Next milestones

### v0.5.0: Proven BOM-to-Altium 365 flow (due 2026-11-30)

**Goal.** Show, with numbers, that a real BOM becomes a correct, house-style library in Altium 365 faster than by
hand.

| Item | Issue | Label |
|---|---|---|
| P5. Publish an accuracy benchmark on real parts (shipped in v0.4.0: 87 of 110 fully right, none built wrong; the 97% pad-match exit criterion is not met yet) | [#42](https://github.com/oliBrowne/PCBLIVINGLIBRARY/issues/42) | docs, feature |
| P6. Real-BOM trials with 3 to 5 Altium users (**skipped for now**: no outside users to ask yet) | [#43](https://github.com/oliBrowne/PCBLIVINGLIBRARY/issues/43) | docs |
| P7. Measure time from BOM to released library (shipped in v0.4.0) | [#44](https://github.com/oliBrowne/PCBLIVINGLIBRARY/issues/44) | feature |
| P8. Report the review and approval rate (shipped in v0.4.0) | [#45](https://github.com/oliBrowne/PCBLIVINGLIBRARY/issues/45) | feature |
| P9. Try house style on a real company library (waits for a real company library) | [#46](https://github.com/oliBrowne/PCBLIVINGLIBRARY/issues/46) | feature |
| P10. Release a full BOM into a real Altium 365 workspace | [#47](https://github.com/oliBrowne/PCBLIVINGLIBRARY/issues/47) | feature |

**Exit criteria.** A live benchmark of 100+ parts is published with a pad-match rate of 97% or better against
KiCad's library; at least 3 outside Altium users have run their own BOMs, with time, held parts and review errors
recorded for each; a 20+ line BOM has been released to a real Altium 365 workspace and places correctly in Altium
Designer.

### v0.6.0: Easy to adopt (due 2027-01-15)

**Goal.** An Altium user can go from download to a released BOM without a warning dialog, a surprise bill or help
from us.

| Item | Issue | Label |
|---|---|---|
| P11. Sign the Windows installer (**on hold** until the maintainer picks it up again) | [#48](https://github.com/oliBrowne/PCBLIVINGLIBRARY/issues/48) | feature |
| P12. Show the cost before every AI step in Altium, KiCad and the web UI (shipped in v0.4.0; not yet tried in Altium or KiCad) | [#49](https://github.com/oliBrowne/PCBLIVINGLIBRARY/issues/49) | feature |
| P13. Decide on the experimental Fusion / EAGLE and EasyEDA Pro exports: **removed** (2026-10-18) | [#50](https://github.com/oliBrowne/PCBLIVINGLIBRARY/issues/50) | docs |
| P14. One-page guide from installer to a BOM in Altium 365 (shipped in v0.4.0: docs/START_HERE.md) | [#51](https://github.com/oliBrowne/PCBLIVINGLIBRARY/issues/51) | docs |

**Exit criteria.** The installer is signed and installs without a SmartScreen warning; every place that starts a
model call shows its estimated cost first; the experimental exports are kept or removed with the reason recorded;
a trial user follows docs/START_HERE.md to a released BOM without help.

**P13 decision (2026-10-18): remove.** No user asked for either export, neither library was ever opened in Fusion or
EasyEDA Pro, and the two writers with their read-back were about 3,000 lines that one maintainer had to keep working
next to the Altium and KiCad paths (risk R6). ecadlib's time goes to the BOM-to-Altium 365 flow instead. The last
version with them is 0.3.1 (`pip install "ecadlib==0.3.1"`); `ecadlib eagle-export` and `ecadlib easyeda-export`
now say so.

## Cut on 2026-10-19

These are deleted from `main`. The last version with them is on the branch
[`archive/before-trim`](https://github.com/oliBrowne/PCBLIVINGLIBRARY/tree/archive/before-trim).

| Cut | Why |
|---|---|
| JLCPCB assembly output and check (`ecadlib assembly`, assembly profiles, the LCSC Part and Assembly Rotation fields, the *Assembly check (JLCPCB)* entry in the Altium and KiCad menus) | The fab hand-off is a different product. Issue [#104](https://github.com/oliBrowne/PCBLIVINGLIBRARY/issues/104) is out of scope. |
| SPICE / IBIS sim models (`ecadlib sim-models`, `[sim]` rules) | A wrong SPICE model fails silently, and checking them is a project of its own. |
| Pricing, stock and alternates (`ecadlib sourcing`, the upkeep price and stock check) | Octopart and DigiKey do it better. Finding the part through Nexar, DigiKey and Mouser stays. |
| Library hub (`ecadlib hub`, the "Your library" page across tools, copying parts between tools) | Sharing across tools needs a community, and a stranger's footprint is what the review hold exists to stop. |
| Team server (`ecadlib team`, `ecadlib ui --team`, sign-in, roles, users, audit log, the Docker and Caddy HTTPS setup) | A login system with no users is pure liability. Trials run as one local user, and the web UI is for one local user. |

## Parked

Removed from `main`, kept on the branch
[`parked/agent-and-a365-pull`](https://github.com/oliBrowne/PCBLIVINGLIBRARY/tree/parked/agent-and-a365-pull):

- **The library agent:** `ecadlib agent`, `ecadlib agent-watch`, its review queue (`ecadlib queue`), the visual check
  as the agent used it, and its branch / pull request batches.
- **Two-way Altium 365 sync:** `ecadlib a365 pull`, `status` and `reconcile`. Pushing to Altium 365
  (`ecadlib altium365 push`) stays: it is the v0.5.0 goal.

Frozen: **KiCad PCM packaging** (`ecadlib kicad-pcm`) stays in the code, but gets no new work.

## Risks

Seven risks are tracked in the [product spec](docs/PRODUCT_SPEC.md#risks), each with an issue labelled
[`risk`](https://github.com/oliBrowne/PCBLIVINGLIBRARY/issues?q=label%3Arisk) (#52-#58).

## Timeline

Past bars run from the day a PR was opened to the day it was merged. Future bars are plans.

```mermaid
gantt
    title ecadlib roadmap
    dateFormat YYYY-MM-DD
    axisFormat %d %b

    section Shipped
    v0.1.0 prototypes                                  :done, s0, 2026-10-13, 1d
    Altium writer, Altium 365 exporter, web UI, MCP     :done, s1, 2026-10-13, 3d
    Librarian, datasheet reader, release check, CI      :done, s2, 2026-10-14, 1d
    Rules, sync, upkeep, team server, library agent     :done, s3, 2026-10-14, 2d
    v0.2.0 on main                                     :milestone, done, m2, 2026-10-15, 0d
    Accuracy set, standard parts, any model, parity     :done, s4, 2026-10-15, 2d
    UI, landing page, review fixes, hub, installer      :done, s5, 2026-10-16, 2d
    Altium dialog, KiCad window, house style            :done, s6, 2026-10-17, 1d
    v0.3.0 on PyPI                                     :milestone, done, m3, 2026-10-17, 0d
    Official APIs first, Altium builds the libraries    :done, s7, 2026-10-17, 1d

    section v0.3.1 Altium loop closed
    Build libraries in the Altium menu                  :done, p1, 2026-10-17, 1d
    Fab-layer designator in Altium                      :done, p2, 2026-10-17, 1d
    Check multi-part, keep-outs, bottom pads in AD26    :done, p3, 2026-10-17, 1d
    v0.3.1 release                                     :milestone, done, r1, 2026-10-17, 0d

    section v0.5.0 Proven BOM to Altium 365
    Real Altium 365 release of a full BOM               :p10, 2026-11-01, 10d
    Time and approval-rate metrics                      :p7, 2026-11-01, 10d
    Live accuracy benchmark                             :p5, 2026-11-01, 22d
    Real-BOM trials with 3 to 5 Altium users            :p6, 2026-11-09, 21d
    House style on a real company library               :p9, 2026-11-12, 15d
    v0.5.0 release                                     :milestone, r2, 2026-11-30, 0d

    section v0.6.0 Easy to adopt
    Signed Windows installer                            :p11, 2026-12-01, 21d
    Cost shown before every AI step                     :p12, 2026-12-01, 21d
    Decide on experimental exports                      :p13, 2026-12-14, 7d
    Installer to Altium 365 guide                       :p14, 2027-01-04, 11d
    v0.6.0 release                                     :milestone, r3, 2027-01-15, 0d
```
