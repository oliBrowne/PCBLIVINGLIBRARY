# Shared store of confirmed parts

Parts somebody already built with [ecadlib](https://github.com/oliBrowne/PCBLIVINGLIBRARY) and that passed its release
check. ecadlib looks here first: a part that is here and trusted is downloaded, drawn in your own style and checked on
your machine like any part it builds itself.

## Trust

- **checked**: one build passed the full release check.
- **confirmed**: checked, and a second independent build gave the same pads and pins (another contributor, or two AI
  models in a maintainer's seed run), or a maintainer signed it off, or it is the KiCad library's own entry for that
  exact part number. By default ecadlib uses only confirmed parts without a person looking; a checked part is a
  second opinion.
- **disputed**: a maintainer marked it after a concrete report of a difference: a second opinion only, until a
  maintainer has settled it.
- **withdrawn**: never used; a library that holds a part from a withdrawn record is told on its next run.

EasyEDA pointer records (an LCSC number and hashes, no drawing) are informational only: nothing checks their claim
yet, so they are never confirmed, never used and never a second opinion, and only a maintainer adds them.

What this store claims is "passed the release check, and N independent builds agree". No automatic check can prove a
footprint matches its datasheet: check before you order boards.

## Layout

```
store.json                 name, format, the ecadlib it needs, maintainers
index/<ab>.json            1/256 of the parts each, by a hash of the part number
parts/<ab>/<key_id>.json   one record per part
withdrawn.json             what was pulled, when and why
withdrawn/                 the withdrawn records, kept for the record
ecadlib-version.txt        the ecadlib version the pull-request check installs
.github/                   the checks (workflows), the issue and pull-request templates, CODEOWNERS
```

Privacy: a lookup first fetches one index file, which is one 1/256 slice of the store and does not name the part. When
the part IS in the store, the lookup then fetches that part's record, and the record's file name identifies the part:
GitHub then learns which part was looked up. A part that is not in the store is not revealed. `[store] mode =
"snapshot"` downloads the whole store as one zip instead (attached to the `snapshot` release every week) and reveals
nothing about single parts.

## Maintainer checklist

The checks only bind once the repository is set up like this (Settings of the repository on GitHub):

1. `.github/CODEOWNERS` names the maintainers' GitHub accounts (`ecadlib store-repo init --maintainer NAME` writes
   them; replace `@MAINTAINERS` otherwise). It makes every file outside `parts/` and `index/` theirs to review.
2. Settings -> Branches (or Rules -> Rulesets) -> a rule for the default branch with:
   - Require a pull request before merging, with at least 1 approval;
   - Require review from Code Owners;
   - Require status checks to pass before merging: `verify`;
   - Do not allow bypassing the above settings (include administrators); block force pushes and deletions.
3. Settings -> Actions -> General: "Require approval for all outside collaborators" for fork pull request workflows,
   and "Read repository contents" as the default workflow permissions.
4. Review every change outside `parts/` and `index/` by hand, above all `.github/`: a pull request's `verify` check runs
   that pull request's OWN copy of the workflows, so a green check proves nothing about a change to them. `verify`
   prints a WARNING line when a pull request touches `.github/`.
5. `ecadlib-version.txt` pins the ecadlib the pull-request check installs (read from the base branch, never from the
   pull request). Bump it after a new ecadlib release has passed the nightly re-check, which always runs the newest
   release.

## Taking part

Report a wrong part with the "Report a wrong part" issue form (or `ecadlib store report`). To share parts you built,
see CONTRIBUTING.md. Licences: NOTICE.md.
