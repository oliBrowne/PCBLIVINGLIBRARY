# The shared store of checked parts

**Not switched on yet.** There is no public store yet, and ecadlib has no default store address: until you set
`[store] url`, ecadlib never asks a store anything and builds every part as before. This page says how it works once
a store exists, or if your company keeps its own. (The design and the reasons behind it: [shared-store-plan.md](shared-store-plan.md).)

## What it is

A public list of parts that somebody already built with ecadlib and that passed the release check. The librarian looks
there first. A part the store trusts is drawn from its record into your library, in your style, and then goes through
the same release check as a part built on your computer. A part that is not there is built as before.

What the store can say about a part, and no more: **it passed the checks, and N independent builds agree**. No check
can prove a footprint matches the datasheet. Compare a part with its datasheet before you order boards, as you would
with any library part.

## Levels

| Level | What it means | What ecadlib does with it (default) |
|---|---|---|
| confirmed | Passed the checks, and a second independent build (another person, or another AI model reading the datasheet) drew exactly the same part: the same pads and pins **and** the same whole drawing (silk, fab and courtyard lines, text, attributes, symbol graphics); or a person signed that drawing off; or it is the KiCad library's own part for that exact part number | Used, then checked on your computer like any part |
| checked | One build that passed the checks (or several that agree on pads and pins but not on the whole drawing) | Not used on its own, and never releases anything: it is only compared with your own build (**second opinion**, below) |
| disputed | Someone reported a concrete difference | Not used on its own; parts already built from it are held |
| withdrawn | Pulled from the store, with a reason | Never used; parts already built from it are held |

**Second opinion.** A record the store does not let you use directly is compared with your own build of the part. If
the two differ, the part is held and the review names both, so you can compare them with the datasheet. If they agree
and the record is only **checked** (one person's build: nothing anyone else can verify, not even which AI model read
the datasheet), the review notes "agrees with one unconfirmed build in the shared store; not enough to release" and
**nothing is released**. Only a **confirmed**, active record that the store did not hand you directly (found through
another code it covers, or for a BOM line with no maker) can release your own build: when your build (a datasheet
read, a KiCad footprint found by a near name, an EasyEDA conversion) has the same pads and pins and the two were made
independently, the review says "matches an independent build in the shared store", and the hold that only waited for
someone to confirm that build is lifted (the datasheet read's flagged items move to the review list). Every other hold
stays. Independent means another kind of source (a KiCad or standard-table part against your datasheet read), or two
datasheet reads by **different AI models**: the same model, or a read whose model is not known, lifts nothing
("agrees, but not an independent read"), and neither does a record made from the same kind of KiCad or standard entry
as your build. A lift is recorded with the part, and the part then depends on the store like a part built from it
(below): if that record is later withdrawn, disputed or no longer in the store, or the store can no longer vouch for
it, the next librarian run (and `ecadlib upkeep`, and every publisher) puts the lifted hold back, with the reason.

A record found through another code it covers (`also`) or for a BOM line with no maker is never built from: at most it
is a second opinion as above, and it never fills in the Manufacturer field. An EasyEDA pointer record is only noted.

**Withdrawn and disputed records.** A part that depends on the store (built from a record, or released by a record's
agreement) is checked again by every librarian run, by `ecadlib upkeep` and by every publisher (Altium 365 push,
DbLib sync, KiCad export, `ecadlib export`, the plugins): the store must vouch for it, or it is held with the reason.

| The store... | A part that depends on it |
|---|---|
| has withdrawn the record, disputes it, no longer lists it, or rates it below your `accept` setting | held, with the reason; it stays held until you build it again (`ecadlib librarian BOM --refresh PART`) or check it and release it yourself |
| answers, but its withdrawn list is missing or unreadable | held ("cannot tell whether the store withdrew it") |
| cannot be reached (or `--offline`), and a copy of its withdrawn list saved on your computer is less than 30 days old | checked against that copy (one note says so) |
| cannot be reached, and no such copy is saved, or it is 30 days old or older | held ("withdrawals could not be checked"): never taken for "not withdrawn" |
| is switched off (`url` empty, or `accept = "off"`) | held ("this part came from the shared store, which is switched off, so withdrawals cannot be checked: release it yourself or switch the store on") |

The last three holds last only as long as the cause: once the store can be read again, the next run takes them out.
Releasing such a part yourself (`--allow`, or Release in the review) keeps it released on later runs: the hold's
text does not change from run to run. Parts that do not depend on the store are never affected, whatever these
settings. A copy of the store saved on your computer that is more than 7 days old is never used to build a part.

**Footprints from strangers.** A store footprint was drawn on someone else's computer. The store takes only a plain
footprint: pads, outlines on silk, fab and courtyard, its reference and value and one `${REFERENCE}` text, `smd` or
`through_hole`. A record with a zone, a BOM / position-file / do-not-populate / board-only flag, any graphic or text on
a copper, mask or paste layer, other text, a hidden pin (other than one stacked on a shown pin) or pin alternates is
refused, by the store's check and again on your computer. A part built from a record is still held whenever its
footprint has anything the pad check does not cover (pads on the bottom copper side, custom-shape pads, paste- or
mask-only openings or unusual margins): compare it with the datasheet first. And "confirmed" means two builders drew
the same whole part, not only the same pads.

## Settings (`ecadlib.toml`)

```toml
[store]
url = ""              # the store: https://raw.githubusercontent.com/OWNER/REPO/main, a file:// URL or a folder.
                      # "" = off (the default). A plain http:// address is refused
accept = "confirmed"  # "confirmed": only confirmed records are used on their own (default)
                      # "checked": single-build records are used too (more parts at once, more risk)
                      # "off": never ask the store
mode = "online"       # "online": fetch the few small files a lookup needs
                      # "snapshot": download the store's whole weekly zip and look parts up on your computer
```

While `url` is set (and `accept` is not `"off"`), the librarian asks the store first for every part, whatever your
`[sources] order` or `--sources` says: you do not list it there (naming `store` in an order is allowed and changes
nothing). `accept = "off"` is the off switch for looking parts up; parts already built from the store are then held
until you release them yourself (above). `--offline` asks the store nothing new: it uses the copy saved on your
computer, if any, and never one older than 7 days to build a part (30 days to check withdrawals). While `url` is empty
and no part in your library came from the store, nothing about your runs changes: the source order, the plan, the
lock file and the rules hash are as without this feature. `ecadlib upkeep` adds its store check by itself while a url
is set, or while the plan has a part that depends on the store.

## Commands

* `ecadlib store status [PLAN]`: the settings, whether the store answers, and with a plan how many parts came from the
  store and which of them it has withdrawn or disputes.
* `ecadlib store get MAKER MPN [-o FILE]`: one part's record: level, where it was made from, who built it.
* `ecadlib store share PLAN [-o DIR] [--by NAME] [PART ...]`: writes a record file for each ready part that may be
  shared, to `DIR/parts/<shard>/<id>.json`, and prints what each file holds. **Nothing is sent.** For a store on
  GitHub it then prints the steps to offer the files in a pull request from your own GitHub account (your GitHub name
  is public on the pull request). Parts that may not be shared are listed with the reason: held parts, parts released
  with `--allow`, parts you picked by hand, parts from your own libraries or from SnapMagic, SamacSys or Ultra
  Librarian, and datasheet reads without a public datasheet link. EasyEDA parts are shared as a pointer only.
* `ecadlib store report PART --plan PLAN [--reason TEXT]` (or `ecadlib store report MAKER MPN`): a pre-filled issue on
  the store's GitHub page about a wrong part. Nothing is sent until you press Submit there.

## Privacy

* **Looking parts up** (`mode = "online"`) sends the store's host (GitHub, for a store kept there) your IP address and
  requests for the store's description, its withdrawn list, and the index file of the part number's slice: one of 256
  files, picked by a hash of the part number, so that request narrows your part down to about 1/256 of all parts.
  Only when the store has the part does ecadlib fetch its record, and that request does identify the exact part: the
  record's file name is derived from the maker and part number, and the public index lists which part each name is.
  No BOM is sent and no account is used. `mode = "snapshot"` downloads the whole store as one file and looks every
  part up on your computer: the host then learns nothing about which parts you look up. No store request follows a
  redirect to another address (the snapshot download only GitHub's own, over https).
* **Publishing and upkeep** (`ecadlib kicad-export`, `ecadlib altium365 push`, `ecadlib dblib sync`,
  `ecadlib export`, the KiCad / Altium plugins, `ecadlib upkeep`) also contact the store, for the parts that depend on
  it only, to check that their record was not withdrawn: the same requests as a lookup (store description, withdrawn
  list, that part's index slice). To avoid it, use `--offline` where the command has it (the saved copy is used), or
  build those parts again without the store (`--refresh`); with the store switched off they are held until you
  release them yourself.
* **Sharing** happens only when you run `ecadlib store share` and then open the pull request yourself. A record holds
  the maker, part number, a description only when a part search, a library or the datasheet gave it (never your BOM's
  own Description or Notes text: otherwise it is empty), the public datasheet link, the symbol and footprint (with only
  those fields), pins, pads, the checks passed, the ecadlib version and the AI model name. It never holds your BOM, project or file
  names, your name or e-mail, internal or supplier part numbers (an EasyEDA pointer holds only the LCSC number it
  points to), stock or price fields, a private datasheet link, a 3D model or a datasheet PDF. What the store accepts
  is checked by the same test ecadlib runs before writing the file (`store_record.validate`): no e-mail address and
  no link that is not a public web link anywhere in a record, its KiCad text included.

## Licences

Footprints and symbols ecadlib drew from a datasheet or from the standard tables are shared under MIT. Parts taken from
the KiCad libraries keep the KiCad licence (CC-BY-SA 4.0) and credit: a part built from such a record gets the same
Library Credit field and NOTICE.txt as a part taken from KiCad directly.
