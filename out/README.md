# out/: sample outputs

These files were made once by ecadlib from `examples/sample_bom.csv` and the specs in `ecadlib/package_specs/`
([how](../docs/development.md#how-the-sample-outputs-in-out-were-made)). Tests and docs read them, so they stay as
committed: do not regenerate them. Your own runs write to `ecadlib_workspace/` (or the folder you pick), not here.

| Folder | What it is |
|---|---|
| `footprints/ecadlib.pretty/` | KiCad footprints from the IPC-7351B generator, with `compare_vs_kicad_official.txt` |
| `altium_models/` | `.PcbLib` / `.SchLib` files from the pure-Python Altium writer |
| `altium/` | an Altium database library (`.DbLib` over SQLite and Excel) |
| `altium365/` | an Altium 365 release plan and its dry run |
| `triage_sample_bom*` | BOM triage reports, made with every part source unreachable |
