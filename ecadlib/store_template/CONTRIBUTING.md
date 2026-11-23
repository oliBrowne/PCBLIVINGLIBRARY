# Sharing parts

1. In a finished ecadlib library, run `ecadlib store share`. It lists the parts that may be shared (released by the
   release check, from an allowed source, with a public datasheet link, no override) and writes their records to a
   folder. Nothing leaves your machine yet. Your GitHub name goes into each record's evidence and is public.
2. Fork this repository, check out your fork, and run `ecadlib store-repo add . FOLDER --author YOUR-GITHUB-NAME` in
   it. That adds new parts, adds your build as evidence to parts that are already here (when your pads and pins
   agree; it confirms the part only when your whole drawing is the same too, its `text_hash`, and add says so when it
   is not), refuses parts whose pads or pins differ, and updates `index/`. It applies the same rules as the `verify`
   check below, so what it writes passes: anything it refuses is a maintainer's to add.
3. Commit and open a pull request. The `verify` check rebuilds each changed record with the ecadlib version in
   `ecadlib-version.txt` and runs its release check. `ecadlib store-repo verify . --base origin/main --author
   YOUR-GITHUB-NAME` runs the same check on your machine.

## What the check refuses

- a record that does not match the format, holds a file (3D model, PDF, embedded data), a private link or an e-mail
  address anywhere, or KiCad text beyond a plain part: a footprint with a zone, a BOM / position-file / DNP /
  board-only flag, anything but pads on a copper, mask or paste layer, or text other than its reference, value and one
  `${REFERENCE}`; a symbol with hidden pins (other than ones stacked on a shown pin) or pin alternates;
- a part that the release check would hold when it is rebuilt from the record alone, or whose hashes do not match;
- different pads or pins for a part that is already here (a conflict: a maintainer looks; a corrected part comes back
  as a new revision after the old one is withdrawn);
- evidence removed or edited (evidence is only ever added), evidence in several people's names in one pull request or
  in a name that is not yours (one GitHub account counts once), more than one new evidence entry for one part (your
  one build per part);
- maintainer-only changes: `signoff` and `kicad-exact` evidence; `also` codes (other part numbers a record says share
  its land), on a new part as on one already here; any status change; a new file past rev 1; any record of a part
  listed in `withdrawn.json` (a withdrawn part comes back only through a maintainer); EasyEDA pointer records
  ("pointer records are not accepted from contributors yet": nothing can check their claim yet); a removed record;
  and every file outside `parts/` and `index/`: `store.json` (but its date), `withdrawn.json`, `withdrawn/`,
  `disputes.json`, `ecadlib-version.txt`, every top-level file (README, CONTRIBUTING, NOTICE, LICENSE) and `.github/`.
  (`kicad-exact` is not yet checked against the KiCad library by CI.)
- any file that is not `.json`, `.md`, `.yml`, `.txt` or a LICENSE in its folder, a symbolic link, a submodule, an
  executable file, and any file over 400 KB.

## Licence

By opening a pull request you offer your records under the terms in NOTICE.md: MIT for data ecadlib read from a
datasheet or drew, CC-BY-SA 4.0 with the KiCad credit for parts from the KiCad libraries, a pointer only for EasyEDA.

## Maintainers

`ecadlib store-repo withdraw . MAKER|MPN --reason "..." --issue URL` pulls a part (it stays in `withdrawn/` and
`withdrawn.json` for good); `ecadlib store-repo dispute . MAKER|MPN --reason "..."` marks it disputed;
`ecadlib store-repo add . FILE --maintainer` adds what contributors may not (also codes, sign-offs, a corrected rev of
a withdrawn part, pointer records), and with `--dispute` marks the store's record disputed when a conflicting build
comes in; `ecadlib store-repo verify . --base origin/main --maintainer` allows the maintainer-only changes. Both tell
you when a change makes a part confirmed: check who built it. A pull request that touches anything outside `parts/`
and `index/` needs your review by hand (README.md, maintainer checklist).
