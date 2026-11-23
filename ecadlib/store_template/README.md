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
- **disputed**: somebody reported a concrete difference: a second opinion only, until a maintainer has looked.
- **withdrawn**: never used; a library that holds a part from a withdrawn record is told on its next run.

What this store claims is "passed the release check, and N independent builds agree". No automatic check can prove a
footprint matches its datasheet: check before you order boards.

## Layout

```
store.json                 name, format, the ecadlib it needs, maintainers
index/<ab>.json            1/256 of the parts each, by a hash of the part number
parts/<ab>/<key_id>.json   one record per part
withdrawn.json             what was pulled, when and why
withdrawn/                 the withdrawn records, kept for the record
```

A lookup fetches one index file and one record; GitHub learns which 1/256 of the store was asked for, never the part
number. A zip of the whole store is attached to the `snapshot` release every week.

## Taking part

Report a wrong part with the "Report a wrong part" issue form (or `ecadlib store report`). To share parts you built,
see CONTRIBUTING.md. Licences: NOTICE.md.
