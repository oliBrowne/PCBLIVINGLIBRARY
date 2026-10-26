# Change control

**No library update can silently change a released part.** A part is released once it passes the release check (or
comes from your Altium 365 workspace). From then on, every ecadlib command that could rewrite it compares what it is
about to leave with the released revision. It classifies every difference, and a change to pins, pads or the part's
identity waits for a person. The released part stays exactly as it was until someone approves the change.

This covers every writer: librarian re-runs, `upstream check-kicad --update`, `upkeep --apply`, `ecadlib resolve` /
review picks, `dedupe --merge`, datasheet passes, `style apply`, and the publishers (`kicad-export`,
`kicad-dblib`, `export-parts`, exporter plugins, `kicad-pcm`, `altium-pkg`, `altium-dblib-sync`, `altium365 push`),
from the command line, the web UI and MCP alike. `tests/test_change_control.py` fails the build when a module that
writes library files does not go through the gate, and `tests/test_change_control_round4.py` when a publisher does
not settle the library before it reads it. A writer that builds released parts again (`librarian --refresh`, the
web UI's Redo all, `rebuild LIB` in place) first settles what is on disk, so a hand edit is held (its bytes kept in
the blob store, a `hold` ledger line) before the rebuild overwrites it.

## What counts as a change

Each part gets a fingerprint read from its files (`ecadlib part fingerprint FILE...`). It is the same for KiCad and
Altium files of the same part, so an export does not look like a change. A part's fingerprint covers its .SchLib, its
.PcbLib and the .kicad_mod the KiCad publishers ship (`footprint.kicad_mod`, or `models/KiCad/<footprint>.kicad_mod`
beside the .PcbLib). A change to the .kicad_mod is classified like a footprint change ("KiCad footprint (.kicad_mod):
pad 1 moved 1 mm in x"), and so is a .kicad_mod whose pads no longer match the .PcbLib's. A .kicad_mod that cannot
be read (cut short, garbled, not text, or not a footprint at all) is an electrical change, "KiCad footprint
(.kicad_mod) file unreadable", and is held. Its pads are never taken from the .PcbLib instead, because KiCad would load
the broken file, not the .PcbLib.

| Class | What it means | Default |
|---|---|---|
| identity | MPN, manufacturer, or the value: its number, its unit, and a rating or tolerance written after it (100nF = 0.1uF = 100n, but 100nF is not 100nH, 3.3V is not 3.9V, 100nF 50V is not 100nF 6.3V; adding a tolerance, a voltage, power or current rating, a temperature coefficient, a dielectric (X7R, C0G) or a package size to `10k` says more and is cosmetic, dropping one is a change unless the MPN stays the same; any other text after the value (`10k DNF`, `10k NC`, `4.7k||1k`, `10k 0R`) is a change; `25 V` and `25V` are the same; a unit added to a value that had none must be the part's own (`100n` -> `100nF` on a capacitor), so `10` -> `10V` on a resistor, or on a part ecadlib cannot tell the kind of, is a change; an ohm sign (`10k` -> `10k Ohm` or `10kΩ`) is cosmetic unless the part is known to be something other than a resistor); a second MPN or manufacturer field that names another part than the first (a house `Part Number` field next to the MPN is the library's own number, so it is cosmetic); a numbered second source (`Manufacturer Part Number 2`, `Manufacturer 2`) added or changed | hold |
| electrical | a pin's signal name or type, the exact name of a hidden or power pin (it is the net the pin joins, so `VCC` -> `Vcc` or `~{CS}` -> `CS#` on a power pin is a change), pins added / removed / renumbered / swapped, pin to pad map, hidden pins and their nets, units (split, merge, a pin moved to another unit), alternate pin functions; every copy of a pin number counts (stacked pins, power pins repeated in each unit); pins that now share a pin end (they connect: `pin 3 (D) now shares a pin end with pin 2 (S): they connect`), and pins of different names or types that no longer share one | hold |
| geometry-major | a pad moved, rotated, resized beyond tolerance, changed side, drill, copper offset, copper layers, paste/mask on/off (also an exposed pad whose paste windows were all removed), custom pad copper shape, zone connection; footprint origin; footprint copper and copper zones; board edge (Edge.Cuts) in the footprint; KiCad assembly flags (exclude from BOM or position files, DNP, board only); 3D origin or rotation | hold |
| geometry-minor | pad size within tolerance, rect to rounded rect, mask/paste margins (also footprint-wide), courtyard, through-hole copper on the inner layers (`*.Cu` / `F&B.Cu`), keepout zones, footprint type (SMD / through hole), pad fabrication property, pad pin function, 3D model added / removed / height; a pin end moved (where a wire meets the pin: wires in placed schematics may need moving); power pins of one net and type that no longer share a pin end (each now needs its own wire); the number of display modes (body styles, such as a De Morgan view) added or removed; the last two are a warn whatever the policy says | warn |
| cosmetic | silkscreen, fab drawing, texts, symbol graphics, pin length or position that leaves the pin end where it was, a visible signal pin's name written another way (RESET# = ~{RESET}), the value written another way (only spacing and case when it is not read as a number), other fields | pass |

Some values ecadlib cannot read as a number, such as `47kR`, `1E4`, `1uFd`, `C 1uF` or a colour. When either side is
one of these, the text is all there is to compare. Any other text is then an identity change, even when the MPN stays
the same: `10k` -> `47kR` is held. Only spacing and case are ignored (`10 k` = `10K`). A comma before exactly three
digits reads two ways: `100,000nF` is 100 nF with a decimal comma and 100 uF with a thousands separator. A change to
or from such a value is an identity change when the two values differ under either reading.

An NPTH hole has no copper, so its copper layers are not compared (`F&B.Cu` and `*.Cu` are the same hole). A pad pin
function or type is compared only when both versions have one: a board fills them in from the schematic, a library
footprint usually leaves them out.

Pins that end on one spot are connected: KiCad and Altium join them there. The fingerprint (schema 4) records each
pin's end and, for each unit and body style, the pins that share one. A pin moved onto another pin's end is a new
connection and is held, even when the pin keeps its name, so a D-S short drawn by moving D onto S is never a
cosmetic pin move. Pins pulled apart lose a connection: that is held too, except for power pins of one net and type.
A hidden pin's net is its hidden net (Altium's HiddenNetName), so hidden pins both named GND but tied to AGND and DGND
are two nets. A body style or unit the released symbol did not have is compared with body style 1 of the same unit:
pins that share an end there but not in body style 1 are a new connection, and a pin that is another signal there
than in body style 1 is an electrical change, so adding a De Morgan view that puts D on S's end is held. kicad-export
treats a KiCad source that gained or lost a body style as not the released revision.
The house rules now give each stacked power pin its own end (`[symbols] stack_power = "none"`), and a released symbol
redrawn that way says which pins now need their own wires in placed schematics. It is a warn, never a pass, whatever
the policy says. A pin end that moves without joining or leaving another pin is a warn as well, because wires in
placed schematics may need moving; a new pin length or graphics that keep the end in place stay cosmetic. KiCad's pin
position is its end; an Altium pin's location is its body end, so its end is the location plus the length. `project
check` uses the same classifier, so a placed symbol and a library update give the same answer.

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
unlisted = "hold"           # hold | warn | pass: Altium project check, an out-of-date component whose changes are unknown
```

`pass` and `warn` record a new revision (`warn` also adds the changes to the part's review notes). `hold` keeps the
released files and fields, stores the proposed version in `.ecadlib/proposed/<part>.json`, and lists the part in the
review queue with the diff. A part that shares a file with a held part (two resistors in one `.SchLib`) is held with
it. A change someone rejected is held again on the next run without asking.

A new part that carries the MPN of a released part that left the plan is compared with that part before its first
release. This happens when a BOM change renames a part: a footprint change from 0603 to 0402 turns `RES-0603-10k` into
`RES-0402-10k` with the same MPN, and a value change turns `CAP-0402-100nF` into `CAP-0402-1uF`. If the policy holds
the difference, the new part is held (status held, not exported) until a person approves it, and the approval records
its revision 1. The released part that left the plan gets a `retired` ledger line.

`ecadlib style apply` may only make cosmetic changes. If a restyle would change anything else, that is an ecadlib bug,
and the part is held with that message.

## Working with held changes

```text
ecadlib part status ws/library                 # held changes waiting for a person
ecadlib part show ws/library AO3401A           # released revision, the held change, its diff and its digest
ecadlib part approve ws/library --part AO3401A --digest 3f2a9c41d0e8 -m "new datasheet drawing" --user "Ann <ann@x.com>"
ecadlib part reject ws/library --part AO3401A -m "the old pinout is right"
ecadlib part history ws/library AO3401A        # every release, hold, approval and publish
ecadlib part revert ws/library AO3401A@2       # release revision 2 again (as a new revision)
ecadlib part diff OLD.kicad_sym NEW.kicad_sym  # classify any two versions of a part
ecadlib part adopt ws/library --part AO3401A -m "ledger lost; files checked against the datasheet"
```

An approval names the change you reviewed. `part show` and `part status` print the held change's digest, and
`part approve` needs it (`--digest`, 6 or more of its first characters are enough). If a writer proposed a different
change after you looked, the approval is refused and nothing is released: look again and approve the new digest. The
web UI sends the digest of the change on the page, so a stale page cannot approve a newer change. Parts that share a
file are decided together, and one digest covers all of them.

With `approvers = 2`, one person counts once however they write their name: `Ann Lee <ann@x.com>`, `Ann Lee`,
`ann  lee` and `ann+2@x.com` are all Ann. Names are compared without case or extra spaces, and e-mails without a
`+tag`. Look-alike letters from other scripts (a Cyrillic `А`, a Greek `ο`), zero-width and other invisible
characters, accents (`Zoë` = `Zoe`), trailing punctuation (`Ann Lee.`, `ann@x.com.`) and a `mailto:` in front are
folded too. When both identities give an e-mail, the e-mail decides: `Ann Lee <ann@x.com>` and `Ann Lee
<ann.lee@y.com>` are two people who share a name. When one of them gives no e-mail, the names decide. The refusal names the
approval already on record, so `"Аnn Lee" approved this change already` shows who it was. A revert is a release too.
With `approvers = 2` it becomes a held change that carries the reverting person's approval, and a second person
approves it with `part approve`. A revert to a revision that fails the release check today is refused.

If an approval replaces files someone edited by hand after the change was held, the approval still applies the
reviewed change. It also warns, keeps the edited bytes in the blob store and writes an `overwritten` ledger line.

The release check never clears a change hold. A part waiting for `part approve` with no released revision to fall
back on (a renamed part held against its MPN twin, adopted files waiting for approval) stays held through
`release-check`, `release-check --allow`, review's "release anyway" and the web UI and MCP release check. Its hold
reasons say "change control:" and name `ecadlib part show`. Removing the part's change block from plan.json by hand
does not help either, because the held proposal in the library still decides. Only an approval releases it.

When `release-check --allow`, "release anyway" or the web UI and MCP release check release a part that has no
revision yet, the release is recorded in the ledger at once as revision 1, with the files as they are at that moment.

An approved change goes through the release check before it is released. If it fails (for example silkscreen now too
close to a moved pad), the released part stays, the approval is not counted, and `part status` shows "approval refused:
release check" with the reason.

The review list (`review.md`), the web UI and the librarian's summary show held changes with what changed. Picking a
symbol or footprint yourself in `ecadlib resolve` counts as one approval. Like any approval, that choice goes through
the release check first. If it fails, the choice waits as a held change marked "not applied", and every released file
is put back, 3D models included. `resolve choose` then says the choice is held and exits with code 2, as it also
does while the choice waits for a second person.

A refused approval (no digest, a digest that is too short, or the digest of an older proposal) never prints the
current digest. Run `ecadlib part show`, which prints it together with the diff. A digest that matches no held change
of the part at all (a typo) says "no held change has that digest". An approval refused by the parity check (for
example only the .kicad_mod was edited, so it no longer matches the .PcbLib) says what to do: edit the .PcbLib too (or
use `ecadlib fp-edit`, which writes both) and run the writer again, reject the change, or set
`[release] parity = "warn"`.

Messages show characters you cannot see: a zero-width space in a pin name prints as `<U+200B>`, and a name that starts
or ends with a space is quoted. An approval that was killed while it put the files in place leaves a mix of released
and approved files. The next approval does not report that as a newer hand edit and writes no `overwritten` line.

## The ledger

`library.ledger.jsonl` sits next to `plan.json`. It is append-only, one JSON line per event (release, hold, approve,
reject, revert, publish, retired, overwritten), sorted keys, so it diffs well in git. The exact bytes of every released
file, and of the 3D models its recipe names, are kept in `.ecadlib/blobs`, which is what `part revert` and a hold
restore. `ecadlib migrate` stamps the ledger's schema version.

Writers take a lock on the library (`.ecadlib/lock`) while they compare and record. Two exports or a librarian run
and an export at the same time therefore never record the same revision twice. A publisher (kicad-export,
kicad-pcm, kicad-dblib, dblib-sync, the Altium 365 push) holds the lock for its whole run, from settling the hand
edits through reading the files to recording the publish. Another writer's change therefore cannot be shipped before it
is settled. The lock is an operating-system lock,
so a crashed run never leaves it behind. A run that waits longer than 10 minutes (`ECADLIB_LOCK_WAIT`, in seconds)
stops with a message.

### When the ledger is damaged

A broken ledger line stops every writer with the line number. Put the line back from git and run again.

**A ledger merged from two branches.** If two branches each released the same revision of a part (both made r2 of
KT-0603R) and git merged them, the ledger holds both r2 lines, or only one branch's while `.ecadlib/marks/` (one empty
file per release, `<part>@r<N>@<digest>`, merged by git like any file) still holds both marks. Which r2 is released is
then not known, so that part alone is refused: publishers (kicad-export, DbLib sync, Altium 365 push, export-parts)
leave it out and say why ("change refuse PART ..."; kicad-export lists it as skipped and exits 1), approving and
reverting it are refused, and `ecadlib part status` and `ecadlib part history` name it. The rest of the library
publishes as usual. To mend it, decide which r2 is the one you want, then:

1. keep that one release line of r2 in `library.ledger.jsonl` and delete the other branch's (or keep the file as it is
   when it already holds only the one you want), and
2. delete the other branch's mark, `.ecadlib/marks/<part>@r2@<digest>`: the message names the files, and the one to
   delete is the one whose digest is not on the line you kept.

Then run again (`ecadlib part history ws/library PART` shows no warning once it is mended). Restoring
`library.ledger.jsonl` and `.ecadlib/` from one branch with git does the same.

If the ledger was deleted, emptied or lost a part's lines, while something else shows the part was released before,
ecadlib does not take the files on disk as revision 1. These signs count: `.ecadlib/blobs`, `library.usage.json`, other
ledger lines, `ecadlib_schema.json` saying the library keeps a ledger, files that differ from what `library.lock.json`
records the librarian built, and a `kicad_export.json` (in the workspace's `kicad/<library>/`) written by an ecadlib
that keeps a ledger and listing the part. The lock counts only for a part it records as released when the librarian
built it. A part that was held then and fixed by hand, by `resolve` or in review before its first release is no sign
of a lost release, and neither is a lock written before the lock recorded statuses (a library edited after its
build, upgraded from an older ecadlib). Taking the files on disk as revision 1 when there was a release would hide
whatever changed since. The writer stops and changes nothing. Restore the ledger from git
(`git checkout -- library.ledger.jsonl` in the library folder). If that is not possible, check the files by hand and
record them yourself: `ecadlib part adopt ws/library --part P -m "what you checked"`, or `--all` for every such part.
With `--all`, the parts that compare clean are adopted and the others are listed as refused, with what differs (exit
code 1).
The new revision 1 is recorded with decision `adopted`, your name and your reason.
Adopt prints the name it signs as (`--user`, else `$ECADLIB_USER`, else git config). It compares the files on disk
with the part's last fingerprint still kept in `.ecadlib/blobs`. That is the one a surviving publish line names, else
the newest one that is not a held change. When the blob store is gone too, files that are byte for byte what
`library.lock.json` records are adopted directly ("compared with library.lock.json"). A library from before change
control (no stamp, no ledger) is recorded as revision 1 as before. Adopt lists every difference. A difference that is more than cosmetic (a pin
swap, a moved pad) is refused. When nothing is left to compare with (`.ecadlib/blobs` is gone too), adopt says "not
compared" and refuses as well; restore `.ecadlib/` from git if you can. `--accept-changes` never releases such files
on one person's word. If the blob store still holds the files of the last stored version, that version is adopted, and
the files on disk become a held change against it. Otherwise the files on disk become a held change with no released
version, and publishers leave the part out. Either way the change needs `ecadlib part approve`, as many people as
`[change] approvers` says. An adopted part never gets a revision number it had before. It gets the next one after
every revision the ledger lines, the usage index and the blob store still show, so at least r2.

When a newer ecadlib reads more of a part (the fingerprint has a schema number), a release recorded by an older one is
fingerprinted again from its stored files with the new code before it is compared. An upgrade therefore neither shows
every released part as changed nor hides a real change in what the old fingerprint did not cover. The same holds
wherever a stored digest meets one computed now: approving (the check for a newer edit on disk), reverting (which
parts actually change) and a held change proposed again. A reject line keeps the rejected change's files, so a newer
ecadlib still knows the change was rejected before. For a reject line written by an older ecadlib, which has no files,
the change is matched on what its stored fingerprint recorded.

## Publishing

- **Altium 365**: an approved change becomes a new revision of the same component, never a new component. If
  `a365_transition` is set, ecadlib applies that life-cycle transition to the new revision.
- **KiCad export and DbLib sync**: they publish only released revisions. A hand edit found in the library folder is
  checked against the policy first, the same as any other writer's change, and every publisher says what it held
  ("change hold PART what changed", on stderr for those without a progress log). A symbol that `kicad-export` (and so
  `kicad-pcm` and `kicad-dblib --export`) takes again from the part's source (`kicad:`, `local:` from `--lib`,
  `easyeda:`) is compared with the released .SchLib first. When the source changed more than cosmetics since the
  release (an upstream pin swap), the released .SchLib is exported instead and the export lists a warning; run
  `upstream check-kicad --update` or the librarian to bring that change through change control. Some pin details
  exist only in KiCad: the types that Altium merges (passive, free, unspecified and no_connect; power_in and
  power_out) and alternate pin functions. The first KiCad export of a revision records them on its publish line. A
  later export of that revision whose source changed one of them also exports the released .SchLib, with a warning.
  The first export of a revision cannot check them, because the release does not record them.
- **kicad-dblib, export-parts and exporter plugins**: they read plan.json, so they settle it first, as kicad-export
  does. A hand edit of a released part's fields or files is held and the released version goes out. A plugin is
  given the settled parts and never a part waiting for approval with no released revision. This holds for
  `kicad-dblib` without `--export` and for the web UI and MCP `export_parts` and `plugin_export`.
- **kicad-pcm from a folder**: the folder must be what `kicad-export` wrote. `kicad_export.json` lists every file it
  wrote with its sha256, and a symbol library or footprint edited, added or removed since is refused with the file
  names. A folder without `kicad_export.json` is refused too. Package the plan.json, or export again. A KiCad folder
  ecadlib did not export is packaged with `--unchecked`. The output and the package's NOTICE.txt then say that it was
  not checked against any released revision. A folder kicad-export wrote is always checked.
- **altium-pkg from a folder**: when the folder belongs to an ecadlib library (a `plan.json` in it or up to two
  folders above, or an earlier `altium_pkg.json` naming it), the library is settled first. Every .SchLib or .PcbLib
  in the folder that is a copy of a released part's file must still be that part (the same bytes, or nothing more
  than cosmetic changes); otherwise it is refused, naming the part and the change. A folder of ecadlib-made files
  (a DbLib sync record, `.ecadbuild` or `.ecadstate` files) whose library is not known, such as a plain copy of a
  library's `models/`, is refused. Name the library with `--plan PLAN`, or pass `--unchecked` to package the files as
  they are; the output and ALTIUM_README.txt then say that they were not checked.
- **kicad-export exit code**: 1 while any change is held (the library on disk then differs from what was exported),
  with `ecadlib part status` as the next step. With `--include-held`, a held part with no released revision says
  "--include-held exported the held version anyway".
- **Altium 365 push without a library ledger** (the web UI or MCP push for a BOM the librarian has not run on): it
  never changes the MPN, manufacturer or value of a component that is already in the workspace. That part is skipped
  with the reason; other parameters are still updated. Use another part name for a different part, or run the
  librarian and approve the change, which releases it as a new component revision.
- **Previews**: the web UI and MCP Altium 365 plan and `altium365 push --dry-run` write nothing, so they do not settle
  a hand edit. A released part whose files or fields were edited in a way the policy holds is shown held, with "an
  edit not yet settled" as its reason; the released revision is what a real push sends.
- **Targets shared by several plan folders** (one DbLib folder, one KiCad output folder, one Altium 365 workspace):
  change control is per plan folder, so one plan never changes a part another plan released there until that plan
  has released the same drawing. Each plan folder has an id in `.ecadlib/plan_id.json`: a renamed or moved plan
  folder is still the same plan, and a copy of one is another plan. A target keeps where each plan was last seen both
  relative to it and as a full path, so moving the target or the plan folders does not make a plan look gone. A plan
  is named "not found from here" only when neither path finds it; run that plan once from where it is now, and use
  `--forget-missing-plans` (or `--take-over`) only for a plan folder that no longer exists.
- **Altium 365 workspaces shared across projects**: a workspace has no record of its own of which plan folder pushed
  each component (ecadlib writes no parameter for it, as parameters reach schematics). The record is kept in the
  project (`ecadlib_publishers.json` beside `ecadlib.toml`) and on this computer (`ecadlib_publishers.json` in the
  ecadlib settings folder), so plan folders of one project, or of several projects pushed from one computer, wait for
  each other. A plan folder of another project that is not at its recorded path any more (the project moved or
  renamed, a drive not mounted) is still on record: its components are held "not found from here" until it is pushed
  once from where it is now, or dropped with `--forget-missing-plans`. Plan folders of different projects pushed from different computers do not: a push that adopts a
  component no record names says so ("Neither this project nor this computer has a record ..."). Push the plan
  folders that share a workspace from one project or one computer.
- **Upgrading ecadlib**: a revision fingerprinted by an older ecadlib is fingerprinted again from its saved files, so
  an upgrade that reads more detail does not show every released part as changed.

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
  1 when a change is one the policy holds. The symbol's Footprint field is compared too: another footprint there is
  what "Update symbols from library" would place, so it is a geometry-major change. A Footprint field naming a
  footprint the library no longer has is held, and so is a board footprint that is gone from a library that is
  still there. Each placed symbol's own Footprint property and the board footprint's library id are compared with
  the library part's footprint too: a part placed with another footprint, or with an empty Footprint property (it
  places no footprint), is held. Nicknames are resolved to their
  library folder first, so a project that names the same `.pretty` with another nickname shows no change. A
  nickname the project does not define is looked for as `<nickname>.pretty` beside the symbol library, where
  kicad-export writes it.
- **Freeze and the Footprint field.** `project freeze` also points the frozen symbols' Footprint field at the frozen
  footprints, so a frozen project checks clean.
- **KiCad freeze.** `project freeze` copies the exact symbols and footprints the project uses into
  `<project>-frozen.kicad_sym` and `<project>-frozen.pretty`. It adds them to the project's library tables and
  points the schematic and board at them. The files it changes are backed up in `.ecadlib/freeze-<time>/` first, and
  `--dry-run` shows the plan without changing anything. 3D models keep their paths. The project pack (#109) uses
  the same code.
- **Altium out-of-date list.** Components placed from Altium 365 record the revision they were placed at.
  `project check` asks the workspace for each component's current revision and lists the out-of-date ones. With
  `--library`, it also lists the classified changes between the two revisions when the ledger records both, whether
  ecadlib pushed them (`altium365 push`) or an older ecadlib pulled them (`a365 pull`, now parked), and the `[change]` policy decides. An
  out-of-date component whose changes cannot be listed gets the `unlisted` action: by default it is held and the
  exit code is 1. A name such as `TPS7A02-33` is looked up as it is. Only when the workspace has no component of that
  name is a trailing `-<number>` read as an Altium revision number (`CMP-003-000000-1`, `CAP-1`), and the row says so.
  A component placed from a DbLib carries no workspace revision. It is listed as `not checked`,
  with the library it came from, and it does not change the exit code.
- **Usage index.** `project scan` records which parts of an ecadlib library a project or BOM uses, and at which
  revision, in `library.usage.json`. The librarian also records its own BOM there. A held change then says which
  projects use the part. A KiCad project without a schematic is read from its board: KiCad copies each symbol's MPN
  field onto its footprint.
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
| Changed | 223 | 761 |
| identity | 0 | 0 |
| electrical | 66 | 158 |
| geometry-major | 0 | 10 |
| geometry-minor | 9 | 566 |
| cosmetic | 148 | 27 |
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

The run was repeated after the fingerprint learned every copy of a pin, alternate pin functions, units, pad copper
layers, custom pad shapes, zone connections, pad properties, footprint attributes, keepouts and Edge.Cuts. The held
numbers did not change, and there is no new hold: the one symbol with new alternate pin functions (GAL16V8) and the
one footprint with a new zone connection (DFN-8-1EP 3x2 mm) were already held for other changes. 30 more footprints
now warn, all real changes: 29 pad fabrication properties (heatsink to mechanical on 22 Micro-Fit mounting pads,
mechanical added on 4 HDMI shield, 2 SMA and 1 SAS footprints) and one keepout redrawn (Hamamatsu C11204-1). The 89 NPTH
holes whose layers changed from `F&B.Cu` to `*.Cu` are not changes.

The run was repeated after hidden and power pin names became electrical (round 4). The numbers did not change:
22,537 / 15,381 in both releases, 223 / 761 changed, 66 / 168 held, no per-part difference and no miss. KiCad 9.0.8
to 10.0.0 renames no hidden or power pin, so the stricter rule adds no hold there. An Altium hidden pin's
`HiddenNetName` written with an overbar (`V\C\C\`) is exported to KiCad as `~{VCC}`, as a pin name is.

The run was repeated after pins that share a pin end became connected and a moved pin end became a warn (fingerprint
schema 4). The held numbers did not change: 66 / 168, with no new hold and no new electrical line about pin ends, so
KiCad 9.0.8 to 10.0.0 stacks no pin onto another. 9 symbols moved from cosmetic to warn, all real pin-end moves: 4
jumpers (`Jumper_3_Bridged12`, `Jumper_3_Open`, `SolderJumper_2_Bridged`, `SolderJumper_2_Open`), 2 SiPM sensors, one
op-amp pin (TSV524xIQ4T pin 17), and two 1-Wire EEPROMs (DS2431Q, DS28E07Q) whose four hidden NC pins, stacked on one
end in 9.0.8, each have their own end in 10.0.0.

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
