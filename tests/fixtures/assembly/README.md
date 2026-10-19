# Assembly test fixtures (#104, docs/assembly.md)

Everything the assembly tests read. No test touches the network.

| Path | What | Real or synthetic | Licence / credit |
| --- | --- | --- | --- |
| `easyeda/<LCSC>.json` | EasyEDA's answer for 79 LCSC parts: JLCPCB's own footprint, pin names, stock | real, recorded 2026-10-18 and 2026-10-19 (fetched once each, with an honest User-Agent, from a PC outside the build sandbox) | JLCEDA / EasyEDA Official Library ([lceda.cn](https://lceda.cn/), [easyeda.com](https://easyeda.com)) |
| `kicad7.pretty/` | 50 KiCad library footprints | real, KiCad 7.0.11 kicad-footprints | CC-BY-SA 4.0 with the KiCad library exception |
| `golden.json` | the golden set: each part's expected pad verdict and CPL correction, decided by hand | real parts, hand-decided answers | ecadlib (MIT) |
| `boards/two_sided.*`, `boards/page_origin.*` | test boards built by KiCad's own pcbnew (`make_boards.py`), KiCad's position file of each (`kicad-cli pcb export pos`) and pcbnew's pad positions (`*.truth.json`) | real KiCad output | ecadlib (MIT); footprints as above |
| `boards/two_sided_altium_pnp.csv` | the two-sided board as an Altium pick-and-place file | **synthetic**: written in Altium's format, not by Altium | ecadlib (MIT) |
| `review_1019/` | the 2026-10-19 tester / critic repro inputs (`tests/test_assembly_review_1019.py`): `LED_0603_A1` (anode on pad 1, symmetric silk), `LED_0603_TRI` / `_SYM` (a diode arrow, with and without its bar), `D_0805_2012Metric` (KiCad's LED_0805 lands under a diode name), Altium pick-and-place files (`led_a1_pnp.csv`, `param_pnp.csv`, `short_row.csv`, `tant_pnp.csv`) | **synthetic**, written by the reviewers | ecadlib (MIT); lands from KiCad's library as above |

To rebuild the boards: `/usr/bin/python3 tests/fixtures/assembly/make_boards.py` (a Python that has KiCad's pcbnew).
