# Change control

**No library update can silently change a released part.** A part is released once it passes the release check (or
comes from your Altium 365 workspace). From then on, every ecadlib command that could rewrite it compares what it is
about to leave with the released revision. It classifies every difference, and a change to pins, pads or the part's
identity waits for a person. The released part stays exactly as it was until someone approves the change.

This covers every writer: librarian re-runs, `upstream check-kicad --update`, `upkeep --apply`, `ecadlib resolve` /
review picks, `dedupe --merge`, `sim-models --attach`, datasheet and sourcing passes, `style apply`, and the
publishers (`kicad-export`, `altium-dblib-sync`, `altium365 push`). `tests/test_change_control.py` fails the build when a
module that writes library files does not go through the gate.

## What counts as a change

Each part gets a fingerprint read from its files (`ecadlib part fingerprint FILE...`). It is the same for KiCad and
Altium files of the same part, so an export does not look like a change.

| Class | What it means | Default |
|---|---|---|
| identity | MPN, manufacturer, or the value as a number (100nF = 0.1uF) | hold |
| electrical | a pin's signal name or type, pins added / removed / renumbered / swapped, pin to pad map, hidden pins, units | hold |
| geometry-major | a pad moved, rotated, resized beyond tolerance, changed side, drill, copper offset, paste/mask on/off; footprint origin; 3D origin or rotation | hold |
| geometry-minor | pad size within tolerance, rect to rounded rect, mask/paste margins, courtyard, 3D model added / removed / height | warn |
| cosmetic | silkscreen, fab drawing, texts, symbol graphics, pin length or position, a pin name written another way (RESET# = ~{RESET}), other fields | pass |

Messages are plain sentences: `pad 3 moved 0.25 mm in x`, `pin 5 (CONT) type open_collector -> passive`,
`pad S1 removed; pad SH added`.

## Policy

```toml
[change]                    # ecadlib.toml
identity = "hold"           # hold | warn
electrical = "hold"         # hold | warn
geometry_major = "hold"     # hold | warn | pass
geometry_minor = "warn"     # hold | warn | pass
cosmetic = "pass"           # hold | warn | pass
approvers = 1               # 2 = the two-person rule: two different people approve a held change
a365_transition = ""        # optional Altium 365 life-cycle transition for an approved new revision, e.g. "Release"
```

`pass` and `warn` record a new revision (`warn` also adds the changes to the part's review notes). `hold` keeps the
released files and fields, stores the proposed version in `.ecadlib/proposed/<part>.json`, and lists the part in the
review queue with the diff. A part that shares a file with a held part (two resistors in one `.SchLib`) is held with
it. A change someone rejected is held again on the next run without asking.

`ecadlib style apply` may only make cosmetic changes. If a restyle would change anything else, that is an ecadlib bug,
and the part is held with that message.

## Working with held changes

```text
ecadlib part status ws/library                 # held changes waiting for a person
ecadlib part show ws/library AO3401A           # released revision, the held change, its diff
ecadlib part approve ws/library --part AO3401A -m "new datasheet drawing" --user "Ann <ann@x.com>"
ecadlib part reject ws/library --part AO3401A -m "the old pinout is right"
ecadlib part history ws/library AO3401A        # every release, hold, approval and publish
ecadlib part revert ws/library AO3401A@2       # release revision 2 again (as a new revision)
ecadlib part diff OLD.kicad_sym NEW.kicad_sym  # classify any two versions of a part
```

An approved change goes through the release check before it is released. If it fails (for example silkscreen now too
close to a moved pad), the released part stays, the approval is not counted, and `part status` shows "approval refused:
release check" with the reason.

The review list (`review.md`), the web UI and the librarian's summary show held changes with what changed. Picking a
symbol or footprint yourself in `ecadlib resolve` counts as one approval.

## The ledger

`library.ledger.jsonl` sits next to `plan.json`. It is append-only, one JSON line per event (release, hold, approve,
reject, revert, publish), sorted keys, so it diffs well in git. The exact bytes of every released file are kept in
`.ecadlib/blobs`, which is what `part revert` restores. `ecadlib migrate` stamps the ledger's schema version.

## Publishing

- **Altium 365**: an approved change becomes a new revision of the same component, never a new component. If
  `a365_transition` is set, ecadlib applies that life-cycle transition to the new revision.
- **KiCad export and DbLib sync**: they publish only released revisions. A hand edit found in the library folder is
  checked against the policy first, the same as any other writer's change.

## Projects: drift, freeze and usage

```text
ecadlib project check board.kicad_pro -o out/drift        # what a library update would change in this project
ecadlib project freeze board.kicad_pro                     # copy the parts it uses into project libraries
ecadlib project scan board.kicad_pro --library ws/library  # record which library parts (and revisions) it uses
ecadlib project check board.PrjPcb --workspace https://acme.365.altium.com --library ws/library
```

- **KiCad check.** A schematic keeps a copy of each symbol it uses, and the board keeps a copy of each footprint.
  `project check` compares each copy with the library today, through the project's library tables, KiCad's global
  tables and any `--lib` folder. Board footprints are read back in their library frame first, so rotation and the
  bottom side do not count as changes. Each difference is classified the same way as a library update, with the
  references that use the part. Parts that differ only in texts or graphics are listed on one line. The exit code is
  1 when a change is one the policy holds.
- **KiCad freeze.** `project freeze` copies the exact symbols and footprints the project uses into
  `<project>-frozen.kicad_sym` and `<project>-frozen.pretty`. It adds them to the project's library tables and
  points the schematic and board at them. The files it changes are backed up in `.ecadlib/freeze-<time>/` first, and
  `--dry-run` shows the plan without changing anything. 3D models keep their paths. The project pack (#109) uses
  the same code.
- **Altium out-of-date list.** Components placed from Altium 365 record the revision they were placed at.
  `project check` asks the workspace for each component's current revision and lists the out-of-date ones. With
  `--library`, it also lists the classified changes between the two revisions, when ecadlib pushed both.
- **Usage index.** `project scan` records which parts of an ecadlib library a project or BOM uses, and at which
  revision, in `library.usage.json`. The librarian also records its own BOM there. A held change then says which
  projects use the part.
- **Upkeep.** The weekly `ecadlib upkeep` run has a `changes` check. It reports held changes waiting for a person,
  projects in the usage index that use an older revision than the released one, and KiCad projects whose parts
  differ from the library in a way the policy holds. It never applies a change.

## Proof: KiCad 9.0.8 to 10.0.0

`scripts/change_corpus.py` classifies every symbol and footprint that exists in both KiCad library releases. It then
reads every part a second, independent way (raw pin numbers, names, types and visibility; raw pad numbers, positions,
sizes and rotations) to find anything the classifier missed.

```text
python scripts/change_corpus.py kicad-footprints-9.0.8 kicad-symbols-9.0.8 kicad-footprints-10.0.0 kicad-symbols-10.0.0 -o out/corpus
```

| | Symbols | Footprints |
|---|---|---|
| In both releases | 22,537 | 15,381 |
| Changed | 223 | 733 |
| identity | 0 | 0 |
| electrical | 66 | 158 |
| geometry-major | 0 | 10 |
| geometry-minor | 0 | 536 |
| cosmetic | 157 | 29 |
| **Held** under the default policy | **66** | **168** |
| Unreadable | 0 | 0 |
| Missed by the classifier (independent re-read) | **0** | **0** |

**Missed electrical changes: 0.** The independent re-read found pin or pad differences in exactly the parts the
classifier held, and in no others.

**False holds: 0 of 234.** Every held part was checked by hand against the two releases. All of them are real
changes a librarian would want to see before a board uses them:

- 158 footprints and 32 symbols renumber the shield pad to `SH` (USB A/B/C, micro-USB, D-sub, SD and microSD; for
  example `pad 5 removed; pad SH added`). A board that used the old number loses its shield connection.
- 29 timer symbols (555, 556, 7555, TLC555...) change the CONT pin type to passive. This changes ERC results.
- TSC2007 SCL changes from input to bidirectional; GAL16V8 inputs are renamed `I1` to `I_{0}`; 74LS242/243 gain NC
  pins.
- 10 footprints change the land pattern: SC-70 / SOT-323 / SOT-353 / SOT-363 / SOT-343 / SC-70-8 pads shrink by
  about 0.3 mm; SSOP-16 and TSSOP-10 pads grow; DFN-8 and MSOP-10 pads move 12.5 um; U.FL moves its origin 0.475 mm.

Two false holds the first run found were fixed in the reader: KiCad 9 writes an empty pin name, number or field as
`~` and KiCad 10 writes `""`. Before the fix this showed 11,671 cosmetic pin-name lines and two false electrical holds
(`Valve:ECH81`, `Valve:EM84`).

A seeded sample of 50 changed parts (seed 105) was also checked by hand; all 50 were classified correctly. The rest
of the changes are courtyard rounding (537 footprints, warn), rect pads changed to rounded rects (59, warn),
datasheet URLs and descriptions (cosmetic) and silkscreen / fab redraws (cosmetic).

The issue that started this work cites a KiCad update that reversed `Device:LED_RGB`'s pin order. Comparing that
symbol across KiCad 8.0, 9.0, 9.0.8 and 10.0 shows no pin change, so the fixtures use two other real kicad-symbols
commits instead. The test suite adds fixtures from real KiCad history (a dual diode whose pin order changed, an LED whose `Sim.Pins`
changed), a SOT-23 pin remap, 0603 to 0402 held / approved / reverted, the two-person rule, 2,200 seeded property
tests (cosmetic changes never hold; any pin, pad or identity change always holds), and a check that KiCad and Altium
files of the same part have the same fingerprint.
