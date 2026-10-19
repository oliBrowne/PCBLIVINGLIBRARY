# Accuracy benchmark: real parts, live datasheet reads

How often does ecadlib build a part right when it has to read the datasheet itself? This page gives the measured
answer on 110 real parts, how it was measured, what counts as a miss, and how to run it again. It is the live
counterpart of the accuracy gate (`ecadlib eval gate`, 328/331 on a committed synthetic replay pack, run on every PR).
The gate is a check of the generator and the pipeline: it says the footprint is right *when the reader's answer is
right*. This page measures the whole thing, reading included: how often ecadlib reads a real datasheet correctly.

**Run of 2026-10-18** (set kicad-v1, Claude Opus 5.5, sources `standard,datasheet`, code at PR #102):

| | Parts | Right | Rate |
|---|---|---|---|
| Every part | 110 | 30 | 27 % |
| Parts with a datasheet link | 97 | 30 | 31 % |
| Parts not stopped by a missing or unfetchable PDF | 71 | 30 | 42 % |
| Counting the maker's land as right (below) | 110 | 33 | 30 % |

**Pad match**, over the 46 footprints built: every pad within tolerance on 33 (72 %), 36 (78 %) counting the
maker's land as right. Pitch 44/46, pad count 45/46, pin 1 43/46, courtyard 40/46. Pin names 30/32.

The target set in #42 is a 97 % pad match. **This run is well short of it.** The rest of this page says where the
misses are, so the next steps are the ones that move the number.

## What was measured

* **The set.** `ecadlib/eval/sets/kicad-v1.json`: 331 real parts, each a KiCad symbol whose footprint is in KiCad's
  official library, with the maker's datasheet link KiCad gives. The benchmark takes 10 parts from each of 11
  package families (110): bga, chip, connector, diode, power, qfn, qfp, soic, sot, tht, tssop.
* **The pipeline.** The same librarian a BOM run uses, with KiCad's library switched off as a source (it is the
  answer key). A part is built from the standard-package table when its package is a standard one, else from the
  datasheet: the PDF goes to Claude, the answer is checked, and the footprint generator builds the land.
* **The answer key.** KiCad's footprint and symbol for the part. A part is right when every pad is within 0.05 mm
  of KiCad's (centre) and 0.10 mm (size), the pitch, pad numbers, pin 1 and courtyard agree, and the symbol has
  KiCad's pins with at least 80 % of the names. The origin is reported but does not fail a part (KiCad puts THT
  origins on pin 1). Thermal vias are not compared.
* **Against the maker's land pattern.** KiCad works most lands out from the package dimensions (IPC-7351B). When a
  datasheet gives its own example board layout, ecadlib uses that instead, so a part can miss KiCad while being
  exactly what the maker asks for. The report marks those (`kicad_differs_maker_land`) and gives the rate counting
  them as right. Three did, each checked by hand against the datasheet page:
  CD4066BM (TI D0014A, p25: 1.55 x 0.6 pads on 5.4 mm), UCC256301 (TI DDB0014A, p70: the same) and BCR430UW6
  (Diodes SOT26, p8: X 0.55, Y 0.80, C 2.40). KiCad's pads are 1.95 and 1.6 mm long. 13 of the parts read from a
  datasheet came with a land pattern, and ecadlib built each of those footprints from it (a misread land would be
  followed too, so only the three above are counted, after a hand check).
* **KiCad errata.** One case where KiCad's reference disagrees with the datasheet is scored right, with the
  evidence in `ecadlib/eval/sets/kicad-v1.errata.json`: AH1806-W, whose SC59 pins 2 and 3 KiCad swaps (Diodes
  AH1806 datasheet p1-2: pin 2 GND, pin 3 OUTPUT).

## By family

| Family | Right | Notes |
|---|---|---|
| chip | 10/10 | standard table |
| diode | 9/10 | |
| sot | 3/10 | +1 counting the maker's land |
| bga | 2/10 | |
| soic | 2/10 | +2 counting the maker's land |
| tht | 2/10 | |
| connector | 1/10 | 5 Neutrik parts have no PCB drawing in the PDF |
| qfn | 1/10 | |
| power | 0/10 | |
| qfp | 0/10 | 3 ATSAME70 reads left every dimension blank |
| tssop | 0/10 | PowerPAD thermal lands (below) |

## Where the misses are

Of the 80 parts that were not right:

* **13 have no usable datasheet link** (dead links, HTML pages with no PDF): ENS210, ICE40HX8K-BG121, AQY282GS,
  MKE02Z16VLD4, GBU4A, C3D02065E, KBU4A, AUIPS6011R, AP1117-18, 2ED21824S06J, HCPL-314J, C3D08060G, 16550.
* **26 never reached the model**: the maker's site refused the download here and the API's own fetcher could not
  get it either. analog.com x11 (LTM4671, LT3652IDD, LTC4060EDHC, LTC2942-1, LTC7810, ADAU1702, AD5274BRM, AD7606,
  LT8610, MAX22201, AD7616), Nexperia x6 (BUK7M10-40EX, PSMN5R2-60YL, IP4234CZ6, BCV61, BAT160A, BAT160C), ST x2,
  Altera x2, and M2GL090T-FG484, DNA40U2200GU, C8051F320-GQ, LTV-352T, IPS6011RPBF. A person with the PDF can give
  it to ecadlib directly; the benchmark does not.
* **20 reads left a dimension blank** (the part is held, nothing is built): the PDF has no PCB drawing (5 Neutrik
  connectors), every dimension came back blank (ATSAME70 x3), several did (MKL24Z32VLK4, MC33063MNTXG, UCC1895J),
  or one number is missing (TLP2361 height, LP38693MP-1.8 tab length, S28B-PHDSS annular ring and others).
* **6 were held because the cross-check's second read disagreed**: OPA569DWP, BSC026N08NS5, AT24CS01-MAHM,
  BD33FC0FP, IR4301 and one other. Holding them is the intended behaviour; they are counted as misses.
* **7 thermal pads**: TI's PowerPAD parts (BQ24004, TPS26633PWP, TPS70345, TPS26600PWP, UCC25800, TLC5951DAP, and
  ISL8117FVEZ, whose Renesas PDF was not checked) get the package's exposed pad as their copper, while the datasheet's land page gives a larger thermal
  land (for example 3.4 x 5.0 mm around a 3.3 x 3.3 pad, TPS2660 p53), which KiCad uses. Their signal pads are
  right. The fix is to read the thermal land too; it needs a new field in the reader's answer and a re-read of
  these parts.
* **The rest** are single misses: PCA9557BS read as 4 x 4 mm, 0.65 mm pitch (SOT629-1) against KiCad's
  3 x 3 mm, 0.5 mm footprint (not yet checked which is right); S10B-PH-K numbered right to left ("No. 1 circuit at
  right" on JST's side-entry drawing), mirrored against KiCad; S13B-EH's courtyard 0.55 mm short behind the pins
  (the drawing does not give the body depth); 27C256 named O0-O7 as the datasheet does (KiCad: D0-D7) and a
  courtyard 0.73 mm longer than KiCad's, for the real 37 mm body; FDMT800100DC, whose same-net pads KiCad merges
  into one.

## Review

The benchmark builds what a BOM run builds, but no one reviews it, so `ecadlib status` has no approvals to count
(#45). What it does show: every one of the parts built from a datasheet is held for a person to compare with the
drawing before release, with the pages to check in its `datasheet/<part>.review.md`; the 6 cross-check
disagreements are held with both values side by side. The review rate on a real library (passed first time, approved, rejected, changed) comes from
`ecadlib status` on a workspace after people have reviewed it, and the trial reports quote it.

## Cost and time

$14.34 in all, from the API's own usage: 139 answers for the 54 parts that reached Claude (113 in Message Batches
at half price, 26 sent one at a time), including 50 escalations where the reader asked again with more pages or a
stronger setting. That is $0.13 per part over the whole set, $0.27 per part read. The re-scores after it
(`--cached-only`, below) cost nothing.

## Running it again

```
ecadlib eval run --set kicad-v1 --limit 110 --sources standard,datasheet --batch \
    --record bench/rec -o bench/run                      # live: reads every datasheet, about $15
ecadlib eval run --set kicad-v1 --limit 110 --sources standard,datasheet \
    --replay bench/rec -o bench/replay                   # free: the saved answers, scored by today's code
ecadlib eval run --set kicad-v1 --limit 110 --sources standard,datasheet --cached-only -o bench/run
                                                         # free: re-score the live run's folder in place
ecadlib eval compare bench/run bench/replay              # what changed, per family and per part
ecadlib eval pin bench/run                               # pin the PDFs read (URL + SHA-256) in kicad-v1.pins.json
ecadlib eval publish bench/run -o docs/benchmark-results.md   # the results page: headline, failures by type
```

The run needs an Anthropic API key (`ecadlib keys`); `[datasheet] max_cost_usd` caps what one run may spend
($10 by default; the 110-part batch run cost $14.34, so pass `--max-cost-usd 20`; `--yes` does not lift it), and the estimate is shown before anything is sent. `report.md` and `report.json`
in the out folder have every part's result, the reason for each miss, and the cost. Any model works: `--model`, or
`--provider` and `--base-url` for another provider.

Each run records, per part: the datasheet it read (URL, SHA-256, pages sent), the reader's prompt version (a hash of
its schema and instructions, so two runs are comparable only when it matches), the model, tokens, cost and time.
With a pins file (`kicad-v1.pins.json`, or `--pins`), a part whose maker has since changed the PDF is flagged
`CHANGED since pinned`.

Every failed part gets one failure type:

| Type | What it means |
|---|---|
| no_pdf | no datasheet PDF (no link, a dead link, or the maker's site refused the download) |
| not_read | not read in this run (`--cached-only`, or waiting for the cost OK) |
| blank_dimension | the read left a needed dimension blank: held, nothing built |
| held_cross_check | held: the cross-check's second read disagreed |
| no_footprint | no footprint for another reason |
| wrong_unit | a unit mistake (mil or inch read as mm, or the reverse) |
| wrong_variant | the wrong package or variant (pin count or pitch differs) |
| mirrored | pad 1 in the wrong place: mirrored or rotated |
| misread_table | a pad table misread (pads in the wrong place or size) |
| thermal_pad | only the exposed or thermal pad differs |
| misread_dimension | a dimension misread or made up (pads or courtyard off); the part's review page tells which |
| maker_land | follows the datasheet's own land pattern, which KiCad's differs from |
| symbol | the footprint is right; the symbol's pins or names differ |

The **Accuracy benchmark (live)** workflow in GitHub Actions runs the same thing on demand (Actions tab, choose the
model, the part count and a cost cap; it needs the `ANTHROPIC_API_KEY` repository secret). It has no schedule: a
weekly run costs money each week, so it is switched on only when wanted. The results page is in the run summary
and its artifact.

## Next steps toward 97 %

1. Read the thermal-pad land from the datasheet's land page (7 parts, about $2 to re-read in a batch, estimated).
2. Fetch the 26 PDFs another way (a person's download, or the maker's API), then read them.
3. Find out why the ATSAME70 reads came back with every dimension blank.
4. Check PCA9557BS and S10B-PH-K against the parts themselves, and add what is right to the errata or the fixes.
