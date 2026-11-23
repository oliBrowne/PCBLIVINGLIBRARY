# Sharing parts

1. In a finished ecadlib library, run `ecadlib store share`. It lists the parts that may be shared (released by the
   release check, from an allowed source, with a public datasheet link, no override) and writes their records to a
   folder. Nothing leaves your machine yet. Your GitHub name goes into each record's evidence and is public.
2. Fork this repository, check out your fork, and run `ecadlib store-repo add . FOLDER` in it. That adds new parts,
   adds your build as evidence to parts that are already here (when your pads and pins agree), refuses parts whose
   pads or pins differ, and updates `index/`.
3. Commit and open a pull request. The `verify` check rebuilds each changed record with the newest ecadlib and runs
   its release check. `ecadlib store-repo verify . --base origin/main` runs the same check on your machine.

## What the check refuses

- a record that does not match the format, holds a file (3D model, PDF, embedded data) or a private link;
- a part that the release check would hold when it is rebuilt from the record alone, or whose hashes do not match;
- different pads or pins for a part that is already here (a conflict: a maintainer looks; a corrected part comes back
  as a new revision after the old one is withdrawn);
- evidence removed or edited (evidence is only ever added), evidence in several people's names in one pull request or
  in a name that is not yours (one GitHub account counts once);
- `signoff` and `kicad-exact` evidence, new `also` codes for a part that is already here, status changes away from
  disputed, and any change to `withdrawn.json`, `withdrawn/`, `disputes.json`, `store.json`, the licence files or
  `.github/`: these are the maintainers'. (`kicad-exact` is not yet checked against the KiCad library by CI.)
- any file that is not `.json`, `.md`, `.yml`, `.txt` or a LICENSE in its folder, and any file over 400 KB.

## Licence

By opening a pull request you offer your records under the terms in NOTICE.md: MIT for data ecadlib read from a
datasheet or drew, CC-BY-SA 4.0 with the KiCad credit for parts from the KiCad libraries, a pointer only for EasyEDA.

## Maintainers

`ecadlib store-repo withdraw . MAKER|MPN --reason "..." --issue URL` pulls a part (it stays in `withdrawn/` and
`withdrawn.json` for good); `ecadlib store-repo dispute . MAKER|MPN --reason "..."` marks it disputed;
`ecadlib store-repo add . FILE --dispute` marks the store's record disputed when a conflicting build comes in;
`ecadlib store-repo verify . --base origin/main --maintainer` allows the maintainer-only changes.
