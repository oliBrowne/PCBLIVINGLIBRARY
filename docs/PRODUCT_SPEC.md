# ecadlib product spec

Last updated 2026-10-17, for v0.3.1. The plan for what comes next is in [ROADMAP.md](../ROADMAP.md).

## Problem

Every new board needs its parts in the library first: a schematic symbol, a footprint, a 3D model and the
parameters (manufacturer, part number, supplier, lifecycle). Building them by hand is slow, and it is easy to get
wrong. A swapped pin or a land pattern read off the wrong drawing is found at bring-up and costs a respin. Downloaded
parts help one part at a time, but each vendor draws them differently, so librarians still redraw them to match their
own library, and nobody keeps them current once they are in.

## Users and jobs to be done

| Who | Main jobs |
|---|---|
| Hardware engineers in small and mid-size teams on Altium 365, with no dedicated librarian (primary) | Turn a project BOM into released, placeable parts without leaving Altium. Trust that pins and pads are right. Keep parts current (lifecycle, stock). |
| Design consultancies with a library per client | Build parts that match each client's house style, quickly, and prove they were checked. |
| Small teams sharing one library | Review and approve parts before release, see who changed what, and keep Altium and KiCad versions in step. |
| Students, makers and KiCad / JLC users (secondary) | Get correct KiCad parts for a BOM for free, including LCSC parts, with a check that they are right. |

## Goals

1. **BOM in, released library out.** From a BOM or an Altium project, find what the library already has, build only
   what is missing, check it, and release it into Altium 365 (or a KiCad / DbLib library).
2. **Correct before it is fast.** No part reaches a library without passing the checks; anything doubtful is held
   for a person.
3. **Matches your library.** New parts follow the user's house style, not a vendor's.
4. **Stays current.** Lifecycle, upstream and Altium 365 drift are watched after release.
5. **Free and local.** Open source (MIT), runs on the user's machine, with their own API key and their choice of
   model, including small and local models.

## Non-goals

- A hosted service or a paid tier.
- Circuit design, part selection advice or schematic generation.
- Replacing a PLM system or a large company's library process.
- Fusion Electronics / EAGLE and EasyEDA Pro exports (removed on 2026-10-18; the last version with them is 0.3.1).
- Scraping sources whose terms forbid it (for example SnapMagic), or spoofing a browser to reach an endpoint.

## Requirements

Status: **shipped** is in v0.3.0; **planned** names the roadmap item in [ROADMAP.md](../ROADMAP.md).

### Must

| Requirement | Feature | Status |
|---|---|---|
| Read BOMs from CSV / Excel, Altium projects and ActiveBOM, KiCad schematics and netlists | `bom_triage`, `bom_inputs`, `altium_project` | shipped |
| Use the user's existing library first and build only what is missing | `librarian`, `dedupe`, `hub` | shipped |
| Get parts from official sources first (KiCad library, Nexar, DigiKey, Mouser), unofficial ones last and optional | `providers`, `kicad_import` (PR #35) | shipped |
| Build footprints to IPC-7351B and the footprint rule book | `footprint_gen`, `footprint_adv`, `footprint_rules` | shipped |
| Read a datasheet only when nothing else covers the part, and cross-check every read | `datasheet`, `ds_route`, `crosscheck` | shipped |
| Hold any part that fails a check, and require sign-off before release | `release_check`, `approvals`, `resolve` | shipped |
| Write Altium `.SchLib` / `.PcbLib` that Altium opens unchanged | `altium_lib`, `altium_native` (8/8 libraries, 0 diffs on AD26) | shipped; multi-part symbols, De Morgan display modes, keep-outs and bottom-side pads checked in AD26 |
| Release parts into Altium 365 with their parameters, never the held ones | `altium365`, `a365_sync` | shipped (dry-run tested); real-workspace run planned (P10) |
| Work from inside Altium and KiCad | `altium_menu`, `altium_forms`, KiCad window (PR #30) | shipped; *Build libraries* in the menu planned (P1) |
| Measured accuracy that can't silently drop | `ecadlib eval`, `eval gate` in CI | shipped (synthetic replay); live benchmark planned (P5) |

### Should

| Requirement | Feature | Status |
|---|---|---|
| New parts follow the user's house style | `ecadlib style learn/check/apply` | shipped; real company library planned (P9) |
| Same library as KiCad files, kept in step with Altium | `kicad_export`, `parity` | shipped |
| One symbol rule for every IC (inputs left, outputs right, power top, ground bottom) | `symbol_layout` | shipped |
| Show what a run will cost before it runs | `datasheet estimate`, `max_cost_usd`, docs/costs.md | shipped on the CLI; in Altium / KiCad / web UI planned (P12) |
| Any model provider, with guide rails for small models | `ecadlib/llm/` | shipped |
| Watch lifecycle, upstream KiCad changes and Altium 365 drift | `lifecycle`, `upstream`, `upkeep`, library agent | shipped |
| Install on Windows without Python | Windows installer, setup page | shipped unsigned; signing planned (P11) |

### Could

| Requirement | Feature | Status |
|---|---|---|
| Team server with roles and sign-in | `ecadlib ui --team`, Docker | shipped |
| Library agent that opens review PRs | `agent` | shipped |
| SPICE / IBIS models | `sim_models` | shipped |
| Fusion Electronics / EAGLE and EasyEDA Pro exports | (removed) | removed 2026-10-18 (P13); last in 0.3.1 |

## Success metrics

| Metric | How it is measured | Today (2026-10-17) | Target |
|---|---|---|---|
| Test count | `grep -rho "def test_" tests \| wc -l`; full suite with `python -m tests.run_all` in CI | 1,130 test functions | Grows with every fix; suite green on every merge |
| Pad-match rate against KiCad's library | `ecadlib eval gate` on the 331-part kicad-v1 set | 327 / 331 (98.8%), on a **synthetic** replay pack: a check of the generator and pipeline given correct answers, not live reads (docs/benchmark.md has the live figure) | 97% or better on **live** reads of 100+ parts, published (P5) |
| Review and approval rate | Parts approved first time / held / rejected, from the release check, approvals and the review queue | Not reported yet | Reported per workspace (P8); 80%+ approved first time in trials |
| Time from BOM to released library | Wall time per step, from BOM to Altium 365 release | Not measured on a real BOM (`ecadlib demo`: 1 s offline) | Measured in every trial (P7); faster than the user's usual process |
| Outside use | Users who ran their own BOM end to end | 0 | 3 to 5 Altium users by v0.4.0 (P6) |

## Risks

Each risk has its own issue, labelled `risk`. The owner of all of them is the maintainer, Oliver Browne
([@oliBrowne](https://github.com/oliBrowne)).

| # | Risk | Mitigation | Status | Issue |
|---|---|---|---|---|
| R1 | AI misreads a datasheet (wrong pinout or land pattern) | Model-free sources first; every read cross-checked; visual proof; release check holds and human sign-off; eval gate in CI. Live accuracy benchmark next. | Mitigated in code, not yet measured live | [#52](https://github.com/oliBrowne/PCBLIVINGLIBRARY/issues/52) |
| R2 | Altium output not fully proven in Altium Designer | Interop tests on 553 Altium-saved files; Altium rebuilds every library itself (`altium_native`, 8/8 on AD26); parity checks. Multi-part symbols ([#38](https://github.com/oliBrowne/PCBLIVINGLIBRARY/issues/38)), keep-outs ([#39](https://github.com/oliBrowne/PCBLIVINGLIBRARY/issues/39)), bottom-side pads ([#40](https://github.com/oliBrowne/PCBLIVINGLIBRARY/issues/40)) and De Morgan display modes checked in AD26. Only AD26 has been run; older Altium versions are untested. | Verified on AD26 | [#53](https://github.com/oliBrowne/PCBLIVINGLIBRARY/issues/53) |
| R3 | Licensing of third-party part data (KiCad CC-BY-SA, EasyEDA credit) | NOTICE files (KiCad exports and librarian output), KiCad exports kept out of git, EasyEDA and KiCad credit on every converted part (Altium `Library Credit`), CC-BY-SA label only on KiCad content, no SnapMagic; own code MIT. Rechecked 2026-10-18: Nexar/DigiKey/Mouser give part facts and datasheet links only. | Handled | [#54](https://github.com/oliBrowne/PCBLIVINGLIBRARY/issues/54) |
| R4 | Model API cost surprises users | Most parts use no model; page routing and model ladder; cost estimate and `max_cost_usd`; docs/costs.md. The estimate is shown before every AI step on the command line, in the Altium dialog, the KiCad window and the web UI, and the key is never put in the global environment ([#49](https://github.com/oliBrowne/PCBLIVINGLIBRARY/issues/49), docs/api-keys.md). The Altium and KiCad dialogs are not yet tried in the real tools. | Mitigated | [#55](https://github.com/oliBrowne/PCBLIVINGLIBRARY/issues/55) |
| R5 | External part APIs change or block us | Official APIs first; unofficial ones last and switchable off; own User-Agent; weekly `sources-canary`. | Watched weekly | [#56](https://github.com/oliBrowne/PCBLIVINGLIBRARY/issues/56) |
| R6 | Scope too wide for one maintainer | Altium + KiCad supported, other exports removed; v0.4.0 only proves the core flow. | Mitigating | [#57](https://github.com/oliBrowne/PCBLIVINGLIBRARY/issues/57) |
| R7 | No outside users yet; competitors cover single-part generation | Lead with the BOM-level librarian workflow; real-BOM trials and a published benchmark. | Open | [#58](https://github.com/oliBrowne/PCBLIVINGLIBRARY/issues/58) |
