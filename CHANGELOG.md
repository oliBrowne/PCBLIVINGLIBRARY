# Changelog

All notable changes to ecadlib are recorded here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Until 1.0.0,
a minor release may change command-line options and file formats.

## [Unreleased]

### Added

- Resolve held parts: work through the release check's held items and record how each was fixed.
- KiCad export: write the library's symbols, footprints and 3D models as KiCad libraries.
- Altium DbLib/SVNDbLib sync: keep a database library in step with the living library.
- STEP generation: build simple 3D bodies for generated footprints.
- Sourcing and alternates: look up stock and suggest drop-in alternates per BOM line.
- Approvals and dedupe: sign off parts before release and merge duplicate library entries.

## [0.2.0] - 2026-10-14

### Added

- Packaging for PyPI: complete project metadata, `pdf`, `altium` and `all` extras,
  version single-sourced from `ecadlib.__version__`, `ecadlib --version`, this changelog,
  a CI job that builds and smoke-tests the sdist and wheel, and a tag-triggered release
  workflow (GitHub release plus PyPI trusted publishing).
- Altium 365 workspace exporter through the Nexar Design API (`ecadlib altium365 plan|workspaces|push`),
  with every GraphQL operation validated against Nexar's published schema and a dry-run mode.
- Pure-Python Altium `.PcbLib` / `.SchLib` writer (`ecadlib altium-lib`): SMD and through-hole pads,
  arcs, keep-outs, extruded and embedded-STEP 3D bodies, multi-part symbols, hidden pins,
  designator and comment text, pin graphics (clock, inverted, active-low) and De Morgan display modes.
- Local web UI (`ecadlib ui`) and MCP server (`ecadlib mcp`) on a shared Workspace layer, plus the
  single `ecadlib` command for all tools.
- KiCad library import (`kicad_import`): official KiCad footprints, symbols and STEP models converted
  to Altium files, with provenance recorded in `manifest.json`.
- Datasheet reader (`datasheet`): Claude reads a PDF and fills the package spec and the symbol.
- Lifecycle watcher (`lifecycle`): checks part status with Nexar, DigiKey and Mouser and can write the
  lifecycle back to Altium 365.
- Librarian (`ecadlib librarian`): one command from a BOM to a filled library, sourcing missing parts
  from KiCad, then LCSC/EasyEDA, then the datasheet reader; available in the UI and the MCP server.
- Release check (`ecadlib release-check`): holds back parts that fail pin/pad, footprint, 3D-height,
  required-field and verification checks before an Altium 365 push; `--allow` overrides per part.
- Upstream change alerts (`ecadlib upstream check-kicad|check-a365`): diffs KiCad sources against
  imported parts and reports Altium 365 drift from the release plan.
- Footprint checker (`ecadlib check`) for KLC/IPC rules; SOT-223 tabs, polarity marks, QFP and
  DFN families; IPC-7351B numbers taken from the tables KiCad builds its library with.
- GitHub Actions workflow running the offline test suite (`python -m tests.run_all`) on every PR.

### Changed

- `jsonschema` is a runtime dependency; the example package specs ship as package data
  (`ecadlib/package_specs`).
- Altium 365 push skips parts already up to date, sends the merged parameter set, and never
  pushes parts the release check held.

### Fixed

- Real-world BOM shapes: Altium ActiveBOM, UTF-16 files, designator ranges, binary and
  headerless BOMs, and generic LibRefs no longer promoted to MPNs.
- Independent review findings in the core modules, the Altium writer (P1-P11) and the UI,
  MCP server and footprint code (U1-U14).

### Security

- Query-string secrets (API keys, tokens) are redacted from the HTTP cache.
- The UI checks the Host header, listens on loopback only, and reads BOMs only from the workspace.

## [0.1.0] - 2026-10-13

### Added

- Initial prototypes: IPC-7351B footprint generator driven by `package_schema.json`, BOM triage
  against local KiCad libraries and public part sources, and Altium Database Library (DbLib) export.

[Unreleased]: https://github.com/oliBrowne/PCBLIVINGLIBRARY/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/oliBrowne/PCBLIVINGLIBRARY/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/oliBrowne/PCBLIVINGLIBRARY/releases/tag/v0.1.0
