<p align="center">
  <a href="https://pcblivinglibrary.vercel.app">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="docs/readme/header-dark.svg">
      <img alt="ecadlib: your component library, kept alive" src="docs/readme/header-light.svg" width="880">
    </picture>
  </a>
</p>

<p align="center">
  <a href="https://pypi.org/project/ecadlib/"><img alt="PyPI" src="https://img.shields.io/pypi/v/ecadlib?color=c2622d&label=pypi"></a>
  <a href="https://github.com/oliBrowne/PCBLIVINGLIBRARY/actions/workflows/tests.yml"><img alt="Tests" src="https://github.com/oliBrowne/PCBLIVINGLIBRARY/actions/workflows/tests.yml/badge.svg"></a>
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-3776ab">
  <a href="LICENSE"><img alt="MIT license" src="https://img.shields.io/badge/license-MIT-555"></a>
</p>

<p align="center">
  <a href="https://pcblivinglibrary.vercel.app"><b>Website</b></a> ·
  <a href="#try-the-demo"><b>Try the demo</b></a> ·
  <a href="https://github.com/oliBrowne/PCBLIVINGLIBRARY/releases/latest"><b>Windows installer</b></a> ·
  <a href="docs/README.md"><b>Docs</b></a>
</p>

ecadlib is a librarian for your component library, from the first BOM to the day a part goes obsolete. Give it a BOM
and it finds every symbol, footprint and 3D model you are missing, draws the ones nobody has from the datasheet, and
makes them match the rest of your library. **Every part is verified before it is released**: anything that fails a
check, or still needs a person, is held until someone signs it off. The rest goes to **Altium 365**, a database
library or **KiCad**. After that it keeps the library alive: released parts never change behind your back, and a
weekly check reports parts going end of life, upstream KiCad changes and drift in your Altium 365 workspace. Free and
open source (MIT); you bring your own API key and pick the model.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/readme/flow-dark.svg">
  <img alt="Read the BOM, find the part, draw the rest, check every part, release. Parts in doubt are held for a person, then signed off into the release." src="docs/readme/flow-light.svg" width="100%">
</picture>

## The whole life of a part

**1. Bring in the BOM.** Altium BOMs and ActiveBOMs, an Altium project (`.PrjPcb` / `.SchDoc`), KiCad BOMs and
schematics, or a hand-made CSV/XLSX, with whatever column names it has. Each line comes back as already in your
library, available from a source, needs a footprint, or DNP. `ecadlib where-used` shows which projects use which part.

**2. Fill the library.** Your own libraries first, then the official KiCad libraries and ecadlib's checked table of
standard packages, then EasyEDA/LCSC (an unofficial source, used last; `[providers] unofficial = false` turns it off).
Nexar, DigiKey and Mouser identify the part. When nobody has it, a datasheet reader (Claude by default, or any model
you choose, local ones included) reads the package drawing and a deterministic IPC-7351B generator draws the land:
chips, gull-wing and no-lead ICs, QFPs, BGAs, connectors and pad tables. Symbols follow one pin rule (inputs left,
outputs right, power top, ground bottom), and parts get a 3D model (KiCad's, EasyEDA's, or a package-shaped STEP that
ecadlib makes).

**3. Check every part.** Every pin has a pad, the footprint passes the [rule book](docs/footprint-rulebook.md), the 3D
height is plausible, the fields are filled in and the files read back. Datasheet reads are cross-checked against a
second read or the standard package table. A part that fails is held with the reason and the next step in plain words.
Parts from a source that needs a second look (a datasheet read, an EasyEDA conversion, a pinout from a family table)
are held until a person releases them. Optional sign-off names who must approve before anything ships.

**4. Release it where you work.** Managed components in your **Altium 365** workspace, a `.DbLib`/`.SVNDbLib` database
library, plain `.SchLib`/`.PcbLib` files (written in pure Python, or built by Altium itself), an Altium install
package, or **KiCad** symbol, footprint and database libraries. One plan feeds all of them, and the KiCad and Altium
versions of every part are checked to match.

**5. Make it yours.** `ecadlib style learn` reads your existing libraries, and new parts follow the same naming,
descriptions, value formats and parameters. `ecadlib dedupe` finds the same part, footprint or symbol twice.

**6. Keep it alive.** Once a part is released, no library update can change it behind your back: a change to pins,
pads or the part's identity waits for a person, who sees it in plain words ("pad 5 removed; pad SH added"). `ecadlib
upkeep` runs as a weekly job in your library's repo and reports only what is new, as one GitHub issue or a pull
request: parts going NRND, last-time-buy or obsolete, stock running out, KiCad sources that changed upstream, and
drift between your plan and the Altium 365 workspace. `library.lock.json` lets a later version of ecadlib rebuild the
same library and report any drift.

<p align="center">
  <img alt="A held part in the web UI: the datasheet read and the standard package table disagree on pin pitch, so nothing was written; the panel says why and what to do next" src="docs/ui-held-part.png" width="100%">
  <br><sub>A held part says why and what to do next. Here the datasheet read and the standard package table disagree on
  pin pitch, so nothing was written to the library.</sub>
</p>

## Where you use it

* **Inside Altium Designer:** an **ecadlib** menu adds a part from its MPN, checks the project's BOM, builds the
  libraries and opens the parts that need a person, with Altium's own dialogs.
* **Inside KiCad:** a plugin (**Tools > External Plugins > ecadlib** in the PCB editor) opens an ecadlib window that
  does the same for KiCad projects.
* **In your browser:** a local web UI (`ecadlib ui`) for the whole flow, from dropping a BOM to releasing to Altium 365.
* **From an AI agent:** an MCP server (`ecadlib mcp`) lets Claude, Codex, Grok or another agent run the same steps in
  conversation, with the same holds and cost checks.
* **On the command line and in CI:** every step is a command (`ecadlib -h` lists them), most with `--json` output.

All of them run on your own machine: your libraries and API keys stay there.

## Install

* **Windows, no terminal:** download **ecadlib-setup-&lt;version&gt;.exe** from the
  [Releases page](https://github.com/oliBrowne/PCBLIVINGLIBRARY/releases) and run it. A setup page opens where you
  paste an API key, pick a model and add ecadlib to Altium or KiCad. [docs/START_HERE.md](docs/START_HERE.md) walks
  through it.
* **Command line:** `pip install ecadlib` (Python 3.10+). These docs follow the main branch: what
  [CHANGELOG.md](CHANGELOG.md) lists under *Unreleased* (change control and `ecadlib part`, `mcp --http`, the run and
  review lines of `ecadlib status`) is not in the PyPI release yet. To get it:
  `pip install "ecadlib @ git+https://github.com/oliBrowne/PCBLIVINGLIBRARY"`.

## Try the demo

```
pip install ecadlib
ecadlib demo
```

About a second, offline, no API keys. It builds a 7-part library (0603 resistors, a 0402 capacitor, an LED, a SOT-23
MOSFET, a SOT-223 regulator and a SOIC-16 chip) from a BOM bundled with ecadlib:

```
7 parts: 4 ready, 3 held in 0.7 s (held: AMS1117-3.3, CH340C, AO3401A; that is expected, see the review)
written:
  ecadlib-demo/librarian/demo_bom_demo/plan.json     the library plan
  ecadlib-demo/librarian/demo_bom_demo/review.md     review: what to check, part by part
  ecadlib-demo/librarian/demo_bom_demo/models/       11 Altium files (.SchLib, .PcbLib)
  ecadlib-demo/kicad/demo_bom_demo/                  KiCad: .kicad_dbl, .kicad_sym, .pretty/
```

CH340C is held on purpose (the demo has no symbol for it), so you see what a held part looks like. The regulator and
the MOSFET are held too: their pinouts come from a family table, and makers differ, so a person checks them against
the datasheet and releases them. `ecadlib ui --workspace ecadlib-demo` opens the result in your browser.

<p align="center">
  <img alt="Fill the library: 4 of 7 parts ready; AMS1117-3.3, CH340C and AO3401A held, each with the reason" src="docs/ui-librarian.png" width="100%">
</p>

## From BOM to Altium 365

The whole flow is five commands. The web UI (`ecadlib ui`), the **ecadlib** menu inside Altium and the MCP server run
the same steps.

**1. Set up once.** `ecadlib init` asks which tool you use, where your libraries are and which part sources to try,
learns your conventions from those libraries, writes `ecadlib.toml` and runs a setup check (`ecadlib doctor`).

```
ecadlib init
```

**2. Fill the library.** The librarian triages the BOM, reuses what you already have, and for each missing part tries
the official KiCad libraries, EasyEDA (if allowed), ecadlib's standard packages and finally the datasheet reader. It
writes the Altium files, a `plan.json` and a review, then runs the release check.

```
ecadlib datasheet estimate my_bom.csv            # optional: what the AI reads would cost, before anything is sent
ecadlib librarian my_bom.csv                     # writes ecadlib_workspace/librarian/my_bom (the init workspace)
ecadlib status                                   # ready, held, waiting for sign-off
```

**3. Deal with held parts.** `ecadlib review` goes through them one at a time: why each is held, ranked symbol or
footprint candidates to pick from, or **release anyway** after you have looked (recorded in the plan).

```
ecadlib review ecadlib_workspace/librarian/my_bom/plan.json
```

**4. Sign off** (optional policy). With an `approvals.json` next to the plan, parts wait until the named people
approve them. A rejected part is held either way, policy or not, until it is approved again.

```
ecadlib approve ecadlib_workspace/librarian/my_bom/plan.json approve --part CH340C -m "pinout checked"
```

**5. Release to Altium 365.** Sign in once with `ecadlib nexar signin` (your own free Nexar app; ecadlib stays signed
in, see [docs/altium.md](docs/altium.md#signing-in-to-altium-365-ecadlib-nexar-signin)), check the plan with a dry
run, then push. Only parts that pass the release check are pushed: push checks each part's files again before it
uploads them, and held parts are never pushed. A part already in the workspace gets a new revision, and only after
change control lets it through.

```
ecadlib altium365 workspaces
ecadlib altium365 push ecadlib_workspace/librarian/my_bom/plan.json --workspace https://acme.365.altium.com --dry-run
ecadlib altium365 push ecadlib_workspace/librarian/my_bom/plan.json --workspace https://acme.365.altium.com
```

No Altium 365? `ecadlib dblib-sync` writes the same plan into a `.DbLib`/`.SVNDbLib` folder, `ecadlib altium-pkg`
builds an install package, and `ecadlib kicad-export` writes KiCad libraries.

## After the release: keeping the library current

```
ecadlib part status ecadlib_workspace/librarian/my_bom/plan.json   # released parts with a change waiting for a person
ecadlib upkeep ecadlib_workspace/librarian/my_bom/plan.json --github issue   # lifecycle, KiCad upstream, A365 drift
ecadlib style check ecadlib_workspace/librarian/my_bom/plan.json             # how far new parts are from your style
```

[`examples/upkeep.yml`](examples/upkeep.yml) is a ready-to-copy weekly GitHub Actions job for your library repo. See
[change control](docs/change-control.md) and
[keeping the library current](docs/librarian.md#upkeeppy-weekly-upkeep-one-issue-or-pr).

## How far to trust it

The release check and the review hold are there because drawing parts is the easy half; checking them is the work.
What has been measured so far:

| What | Result | What it tells you |
|---|---|---|
| **Reading real datasheets** (the number that matters) | 87 of 110 real parts built fully right (79 %), 0 built wrong | Claude Opus 5.5's live datasheet reads of 2026-10-25 plus two targeted re-reads, re-scored free on the code of PR #194. The 23 misses were held or left unread, never built. The code was tuned on these same parts, so a fresh set is the real test. Method, every miss and earlier runs: [docs/benchmark.md](docs/benchmark.md). |
| Footprint generator (replay check) | 328 / 331 on every pull request | Given a correct package description, the generator draws the right pads. It does not test datasheet reading. |
| Altium Designer 26 | Tried | The generated libraries, the ecadlib menu and parts released to Altium 365. Older Altium versions are untested. |
| Keeping it current (lifecycle, KiCad upstream, Altium 365 drift, upkeep) | Offline tests; the KiCad upstream check run live | The distributor, Nexar and GitHub calls are tested against their documented responses, not yet on a real library over months. |

## Where it's going

| When | What |
|---|---|
| Now: v0.4.0, due 2026-11-30 | A proven BOM-to-Altium 365 flow: a fresh benchmark on parts the code was not tuned on, a full BOM released to a real workspace and placed in Altium Designer, house style tried on a real company library |
| v0.5.0, due 2027-01-15 | Easy to adopt: a signed Windows installer and the cost shown before every AI step in Altium, KiCad and the browser |
| Parked | A library agent that opens review pull requests, and two-way Altium 365 sync (kept on a branch until the core flow is proven) |
| Out of scope | Assembly output for a fab house, SPICE models, pricing and stock, a hosted service |

The details are in [ROADMAP.md](ROADMAP.md) and [docs/PRODUCT_SPEC.md](docs/PRODUCT_SPEC.md).

## Documentation

Everything else lives in [docs/](docs/README.md):

* [Using ecadlib](docs/using-ecadlib.md): the web UI, the MCP server and the command line
* [The librarian](docs/librarian.md): the release check, held parts, sign-off, library rules and safe updates
* [Change control](docs/change-control.md) and [house style](docs/house-style.md)
* [Altium and Altium 365](docs/altium.md) and [KiCad](docs/kicad.md)
* [BOMs and datasheets](docs/bom-and-datasheets.md) and
  [footprints, symbols and 3D models](docs/footprints-and-symbols.md)
* [Bring your own model](docs/models.md), [API keys](docs/api-keys.md) and [costs](docs/costs.md)
* [Using it from an AI agent (MCP)](docs/mcp.md)
* [Command line, setup and integrations](docs/command-line.md)
* [Working on ecadlib](docs/development.md): tests, the accuracy gate and releases

Changes are in [CHANGELOG.md](CHANGELOG.md). Bug reports and pull requests are welcome: see
[CONTRIBUTING.md](CONTRIBUTING.md) (and [SECURITY.md](SECURITY.md) for anything private).

## Credits

Parts converted from EasyEDA come from the **JLCEDA/EasyEDA Official Library** ([easyeda.com](https://easyeda.com),
[lceda.cn](https://lceda.cn)), whose data asks anyone reusing it to credit it. Parts from the **KiCad libraries**
([gitlab.com/kicad/libraries](https://gitlab.com/kicad/libraries), CC-BY-SA 4.0 with a design-use exception) are
labelled `(CC-BY-SA 4.0)` in the footprint description. ecadlib keeps the credit on every such part (a `Credit`
property in KiCad, a `Library Credit` parameter in Altium) and writes a `NOTICE.txt` listing the third-party terms of
each librarian run, and copies it into the KiCad export, the dblib-sync folder and the altium-pkg package. Leave them in place if you share or publish the library.

## License

MIT. See [LICENSE](LICENSE).
