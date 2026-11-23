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
EasyEDA conversion) has the same pads and pins as a store record somebody else built independently, the review says
"matches an independent build in the shared store", and the hold that only waited for someone to confirm that build is
lifted. Every other hold stays. If the two differ, the part is held and the review names both, so you can compare them
with the datasheet. A record made from the same KiCad or EasyEDA entry as your build is no second build and lifts
nothing.

**Withdrawn and disputed records.** ecadlib reads the store's withdrawn list on every librarian run. A part in your
library that was built from a record the store has since withdrawn or disputes is held on the next run, with the
reason, and `ecadlib upkeep` lists it. It stays held until you build it again (`ecadlib librarian BOM --refresh PART`)
or check it yourself. A store that cannot be reached holds nothing and stops nothing: the run finishes and says so
once at the end.

## Settings (`ecadlib.toml`)

```toml
[store]
url = ""              # the store: https://raw.githubusercontent.com/OWNER/REPO/main, or a folder. "" = off (the default)
accept = "confirmed"  # "confirmed": only confirmed records are used on their own (default)
                      # "checked": single-build records are used too (more parts at once, more risk)
                      # "off": never ask the store
mode = "online"       # "online": fetch the few small files a lookup needs
                      # "snapshot": download the store's whole weekly zip and look parts up on your computer
```

The librarian asks the store first whenever `store` is in `[sources] order` (it is in the default order). If you
write your own `[sources] order` without `store`, the store is not used, even with a url set. `--offline` asks the
store nothing.

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

* **Looking parts up** sends the store's host (GitHub, for a store kept there) your IP address and the names of the
  files fetched: an index file that covers about one in 256 parts (picked by a hash of the part number) and the one
  record. No BOM, no account, no counts. `mode = "snapshot"` downloads the whole store and looks parts up on your
  computer instead.
* **Sharing** happens only when you run `ecadlib store share` and then open the pull request yourself. A record holds
  the maker, part number, the maker's description, the public datasheet link, the symbol and footprint, pins,
  dimensions, the checks passed, the ecadlib version and the AI model name. It never holds your BOM, project or file
  names, your name or e-mail, internal or supplier part numbers (an EasyEDA pointer holds only the LCSC number it
  points to), stock or price fields, a private datasheet link, a 3D model or a datasheet PDF.

## Licences

Footprints and symbols ecadlib drew from a datasheet or from the standard tables are shared under MIT. Parts taken from
the KiCad libraries keep the KiCad licence (CC-BY-SA 4.0) and credit: a part built from such a record gets the same
Library Credit field and NOTICE.txt as a part taken from KiCad directly.
