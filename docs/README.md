# ecadlib docs

New here? Start with the [README](../README.md), then [START_HERE.md](START_HERE.md) if you would rather not use a
terminal.

## Using it

* [Start here](START_HERE.md): the Windows installer and setup page, no terminal needed.
* [Using ecadlib](using-ecadlib.md): the local web UI, the MCP server and the command line.
* [Using ecadlib from an AI agent (MCP)](mcp.md): setup for Claude, Codex, Grok and other clients.
* [Command line, setup and integrations](command-line.md): first run, `doctor`, `status`, `--json`, the installer,
  stored keys, notifications and plugins.

## From BOM to released library

* [BOMs and datasheets](bom-and-datasheets.md): BOM triage, the BOM formats it reads, the datasheet reader and BOMs
  from an Altium project.
* [The librarian](librarian.md): one command from BOM to library, the release check, fixing held parts, sign-off,
  library rules, safe updates and keeping the library current.
* [Change control](change-control.md): released parts never change behind your back.
* [Teach it your house style](house-style.md).

## Parts

* [Footprints, symbols and 3D models](footprints-and-symbols.md): the IPC-7351B generator, harder packages,
  standard parts, symbol layout, STEP models and the visual check.
* [Footprint rules](footprint-rules.md) and [the footprint rule book](footprint-rulebook.md).

## Tools

* [Altium and Altium 365](altium.md): Altium 365 release, database libraries, the `.SchLib`/`.PcbLib` writer, the
  install package and the ecadlib menu inside Altium.
* [Altium builds the libraries itself](altium-native.md).
* [KiCad](kicad.md): the library as KiCad files, KiCad/Altium parity and real KiCad checks.

## Models and cost

* [Bring your own model](models.md): Claude by default, any provider, guide rails for smaller models.
* [API keys and AI costs](api-keys.md) and [what it costs to run](costs.md).
* [Accuracy benchmark](benchmark.md): real parts, live datasheet reads, every miss.

## Project

* [Product spec](PRODUCT_SPEC.md) and the [roadmap](../ROADMAP.md).
* [Working on ecadlib](development.md): requirements, code layout, tests, the accuracy gate and releases.
* [How ecadlib talks to people](ui-voice.md), [brand](brand.md) and [code signing](code-signing.md).
