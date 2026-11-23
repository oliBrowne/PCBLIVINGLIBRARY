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
| confirmed | Passed the checks, and a second independent build (another person, or another AI model reading the datasheet) has the same pads and pins; or a person signed it off; or it is the KiCad library's own part for that exact part number | Used, then checked on your computer like any part |
| checked | One build that passed the checks | Not used on its own: a **second opinion** (below) |
| disputed | Someone reported a concrete difference | Not used on its own; parts already built from it are held |
| withdrawn | Pulled from the store, with a reason | Never used; parts already built from it are held |

**Second opinion.** When your own build of a part (a datasheet read, a KiCad footprint found by a near name, an
EasyEDA conversion) has the same pads and pins as a store record with its own drawing, and the two were made
independently, the review says "matches an independent build in the shared store", and the hold that only waited for
someone to confirm that build is lifted (the datasheet read's flagged items move to the review list). Every other hold
stays. Independent means another kind of source (a KiCad or standard-table part against your datasheet read), or two
datasheet reads by **different AI models**: the same model, or a read whose model is not known, lifts nothing
("agrees, but not an independent read"), and neither does a record made from the same kind of KiCad or standard entry
as your build. If the two differ, the part is held and the review names both, so you can compare them with the
datasheet. A lift is recorded with the part; if that record is later withdrawn, disputed or no longer in the store,
the next librarian run (and `ecadlib upkeep`) puts the lifted hold back, with the reason.

A record found through another code it covers (`also`) or for a BOM line with no maker is never built from: at most it
is a second opinion as above, and it never fills in the Manufacturer field. An EasyEDA pointer record is only noted.

**Withdrawn and disputed records.** ecadlib reads the store's withdrawn list on every librarian run while a store is
set, whatever your source order or `--sources`. A part in your library built from a record the store has since
withdrawn, disputes, no longer lists, or now rates below your `accept` setting is held on the next run, with the reason,
and `ecadlib upkeep` lists it; every publisher (Altium 365 push, DbLib sync, KiCad export) checks it too. A store that
answers but has no readable withdrawn list cannot say what it took back: parts built from it are held. It stays held
until you build it again (`ecadlib librarian BOM --refresh PART`) or check it yourself. A store that cannot be reached
holds nothing new and stops nothing: the run finishes and says so once at the end. A copy of the store saved on your
computer that is more than 7 days old is never used to build a part.

**Footprints from strangers.** A store footprint was drawn on someone else's computer, so a part built from it is held
whenever the footprint has anything the pad check does not cover (pads on the bottom copper side, custom-shape pads,
paste- or mask-only openings or unusual margins, copper graphics): compare it with the datasheet first.

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
nothing). `accept = "off"` is the off switch. `--offline` asks the store nothing new: it uses the copy saved on your
computer, if any, and never one older than 7 days. While `url` is empty nothing about your runs changes: the source
order, the plan, the lock file and the rules hash are as without this feature. `ecadlib upkeep` adds its store check by
itself while a url is set.

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
  part up on your computer: the host then learns nothing about which parts you look up.
* **Sharing** happens only when you run `ecadlib store share` and then open the pull request yourself. A record holds
  the maker, part number, a description only when a part search, a library or the datasheet gave it (never your BOM's
  own Description or Notes text: otherwise it is empty), the public datasheet link, the symbol and footprint (with only
  those fields), pins, pads, the checks passed, the ecadlib version and the AI model name. It never holds your BOM, project or file
  names, your name or e-mail, internal or supplier part numbers (an EasyEDA pointer holds only the LCSC number it
  points to), stock or price fields, a private datasheet link, a 3D model or a datasheet PDF.

## Licences

Footprints and symbols ecadlib drew from a datasheet or from the standard tables are shared under MIT. Parts taken from
the KiCad libraries keep the KiCad licence (CC-BY-SA 4.0) and credit: a part built from such a record gets the same
Library Credit field and NOTICE.txt as a part taken from KiCad directly.
