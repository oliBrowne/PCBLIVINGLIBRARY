# Accuracy benchmark: real parts, live datasheet reads

How often does ecadlib build a part right when it has to read the datasheet itself? This page gives the measured
answer on 110 real parts, how it was measured, what counts as a miss, and how to run it again. It is the live
counterpart of the accuracy gate (`ecadlib eval gate`, 328/331 on a committed synthetic replay pack, run on every PR).
The gate is a check of the generator and the pipeline: it says the footprint is right *when the reader's answer is
right*. This page measures the whole thing, reading included: how often ecadlib reads a real datasheet correctly.

## Latest result: 87 of 110 parts right (79 %), none built wrong

| | 2026-10-25 reads, re-scored on 10-25 (PR #194 code) |
|---|---|
| Right, every part | **87/110 (79 %)**: 69 match KiCad exactly, 18 follow the maker's datasheet where KiCad differs |
| Built wrong | **0**: every one of the 87 footprints built is right; the 23 misses are parts held or not built |
| Built from a datasheet read: right | 52 of 52 built (34 match KiCad exactly, 18 by the datasheet) |
| Built from the standard-package table: right | 35 of 35 |
| Pad match over the footprints built | 89.0 % (1,504 of KiCad's 1,690 pads, 87 footprints) |
| Without the one PDF downloaded by hand | 86/109 (79 %) |

**How it was measured.** The answers are the datasheet reads of a live run on 2026-10-25 (Claude Opus 5.5, zoom on,
sources `standard,datasheet`, cap $30), which scored 77/110 and cost $25.46, plus targeted paid re-reads of AQY282GS
($0.11) and 2ED21824S06J ($0.23, from Infineon's PDF downloaded by hand). Those saved answers were then re-scored for
free (`--cached-only`, nothing sent) on Oliver's PC with the code of PR #194: 87/110. The later merges (#195 and
the #196 bug hunt) were checked after every round with a free cached re-score in the cloud, which did not change.

**What moved it from 77 to 87.** General product fixes after the run (the cross-check and land rules, the DO-34
axial table entry, fewer false holds), two paid re-reads, and fairer grading: five parts where KiCad's reference
disagrees with the datasheet are scored right, with the evidence in the errata file. Of the gains since the 2026-10-23 run (64/110), about 13 are general product fixes, 4-5 fairer grading and 2
answer-key errata.

**Read it with care.** The fixes were made while looking at these same 110 parts, so 79 % is the score on parts
the code was tuned on. A fresh set of 30-50 parts it has never seen is the real accuracy check and has not been run
yet. The 23 misses: 15 parts whose saved read has no cross-check (a live read would score them, BTS6143D has never
been read), 4 reads that left a dimension blank, 2 held because the cross-check disagreed, and 2 held for another
reason (EPM240F100 has no pin list, OPA569DWP's thermal land read as its body). None of the 23 was built.

| Family | Right | Family | Right |
|---|---|---|---|
| chip | 10/10 | soic | 6/10 |
| diode | 10/10 | sot | 6/10 |
| qfp | 9/10 | tssop | 6/10 |
| bga | 6/10 | qfn | 4/10 |
| power | 6/10 | connector | 3/10 |
| tht | 3/10 | | |

The target in #42 is a 97 % pad match on live reads. **This is still short of it.** The sections below describe
the earlier 2026-10-21 run in full; the method ("What was measured") is the same for both.

## Run of 2026-10-21

Set kicad-v1, Claude Opus 5.5 and Haiku 5.5, sources `standard,datasheet`, Message Batches, code at
main 4f78db8, pages picked automatically:

| | Opus, zoom on | Opus, default settings | Haiku 5.5, default |
|---|---|---|---|
| Right, every part | **43/110 (39 %)** | 39/110 (35 %) | 31/110 (28 %) |
| Counting the maker's land as right (below) | 46/110 (42 %) | 42/110 (38 %) | 32/110 (29 %) |
| Built from a datasheet read: right | 20 of 35 built | 16 of 35 built | 8 of 26 built |
| Built from the standard-package table: right | 23 of 24 | 23 of 24 | 23 of 24 |
| Pad match over the footprints built | 72.6 % (939/1,293 pads, 59 footprints) | 72.2 % (921/1,276, 59) | 65.0 % (606/933, 50) |
| Parts with no PDF to read | 19 | 19 | 19 |
| Real cost (API usage) | $23.19, $0.21 a part | $18.73, $0.17 a part | $0.92, under a cent a part |

"Zoom on" is `[datasheet] zoom_drawings = true`: the drawing and land-pattern pages are also sent as close-ups. It
is off by default. It needs a PDF renderer (pypdfium2, which pip installs with ecadlib, or poppler's `pdftoppm`); an eval run with zoom on and no renderer stops before it reads. The 2026-10-23 run's venv had none, so that run read every part without close-ups. Over the default it got AH1806-W, TPS70345, STULPI01A, NCP4308AMT and S10B-PH-K right and lost
TC2030 (the cross-check's second read disagreed, so it was held), for $4.46 more.

**Read the split, not just the total.** 23 of the right parts are standard packages (chip resistors and capacitors,
diodes, SOT-23 and the like) built from ecadlib's own table with no model involved; that table is right on 23 of
24. The part of the pipeline this benchmark is really about is the datasheet read: with zoom on, 20 of the 35
footprints built from a datasheet were right, and 51 parts got no footprint at all (no PDF, a dimension left blank,
or held because the second read disagreed). A held part is the intended behaviour, not a wrong part in a library:
it waits for a person.

**Against earlier runs.** On 2026-10-19 the same set scored 33/110 (Opus, default settings, $16.00), and on
2026-10-18 30/110 ($14.34). On 10-19, 10 of the 21 footprints built from a datasheet were right; now 20 of 35 are,
so the gain is in the datasheet reads, not in the table. It comes from code, not from re-tuning to the answer key:
downloads that get past maker sites that refuse a script (#145: 39 parts without a PDF became 19; 49 PDFs came
plain, 19 with Chrome's TLS fingerprint, 1 through a headless browser, 2 were sent as a link), the cross-check's
new rules, and a fix to batch reads (#147) where one part's hold was given to every part waiting with it. The zoom
run lost no part that was right on 10-19.

**Haiku 5.5** reads for under a cent a part and scores 31/110 (27 on 10-19). Most of its losses are holds: its
cross-check's second read disagreed on 29 parts, against 17 for Opus, so it builds fewer parts rather than more
wrong ones.

The target set in #42 is a 97 % pad match. **These runs are still well short of it.** The rest of this page says
where the misses are.

## What was measured

* **The set.** `ecadlib/eval/sets/kicad-v1.json`: 331 real parts, each a KiCad symbol whose footprint is in KiCad's
  official library, with the maker's datasheet link KiCad gives. The benchmark takes 10 parts from each of 11
  package families (110): bga, chip, connector, diode, power, qfn, qfp, soic, sot, tht, tssop.
  On 2026-10-25 AUIPS6011R was replaced by BTS6143D, an Infineon high-side switch on the same KiCad TO-252-4
  footprint: AUIPS6011R is discontinued and Infineon has pulled its datasheet, so no read could score it, and
  BTS6143D is in production with a KiCad link that downloads. Scores before that date count AUIPS6011R.
* **The pipeline.** The same librarian a BOM run uses, with KiCad's library switched off as a source (it is the
  answer key). A part is built from the standard-package table when its package is a standard one, else from the
  datasheet: the PDF goes to Claude, the answer is checked, and the footprint generator builds the land.
* **The answer key.** KiCad's footprint and symbol for the part. A part is right when every pad is within 0.05 mm
  of KiCad's (centre) and 0.10 mm (size), the pitch, pad numbers, pin 1 and courtyard agree, and the symbol has
  KiCad's pins with at least 80 % of the names. The origin is reported but does not fail a part (KiCad puts THT
  origins on pin 1). Thermal vias are not compared.
* **Right by the datasheet.** The maker's datasheet is the truth and KiCad's library the fallback (Oliver,
  2026-10-21). KiCad works most lands out from the package dimensions (IPC-7351B) and sizes some courtyards from a
  nominal body per pin count (every DIP-16 is 20.32 mm long). So a part that misses KiCad only where it follows the
  datasheet counts as right, and the report lists each one under "Right by the datasheet":
  - `maker_land`: every pad that differs is the datasheet's own recommended land (its example board layout), and that
    land still covers the leads read from the same drawing. In the zoom run: CD4066BM (TI D0014A, p25: 1.55 x 0.6 pads
    on 5.4 mm), BCR430UW6 (Diodes SOT26, p8: X 0.55, Y 0.80, C 2.40), both checked by hand against the datasheet page
    on 10-18, and C8051F320-GQ, not yet checked by hand. KiCad's pads are 1.95 and 1.6 mm long.
  - `datasheet_body`: only the courtyard differs, and ours is the IPC courtyard (0.1-0.5 mm excess) around our pads
    and the largest body the datasheet gives, centred on the pads: AM26LS31CN (TI body 18.92-19.69 mm) and 27C256
    (37.0 mm, longer than the 35.56 mm KiCad allows for any DIP-28).
  The report still gives the exact KiCad match count next to it. A misread land or body would be followed too; the
  checks above (leads covered, body centred on the pads, nothing else wrong) are what keeps that out.
* **KiCad errata.** One case where KiCad's reference disagrees with the datasheet is scored right, with the
  evidence in `ecadlib/eval/sets/kicad-v1.errata.json`: AH1806-W, whose SC59 pins 2 and 3 KiCad swaps (Diodes
  AH1806 datasheet p1-2: pin 2 GND, pin 3 OUTPUT).

## By family (2026-10-21 run)

| Family | Zoom on | Default | 10-19 (Opus) | Notes |
|---|---|---|---|---|
| chip | 10/10 | 10/10 | 10/10 | standard table |
| diode | 9/10 | 9/10 | 9/10 | |
| tssop | 6/10 | 5/10 | 4/10 | |
| bga | 5/10 | 4/10 | 1/10 | |
| qfp | 3/10 | 3/10 | 0/10 | 3 ATSAME70 reads still miss |
| soic | 3/10 | 3/10 | 2/10 | |
| sot | 3/10 | 2/10 | 3/10 | |
| tht | 2/10 | 2/10 | 2/10 | |
| connector | 1/10 | 1/10 | 1/10 | 5 Neutrik parts have no PCB drawing in the PDF |
| qfn | 1/10 | 0/10 | 1/10 | |
| power | 0/10 | 0/10 | 0/10 | |

## Where the misses are (2026-10-21 run)

**The 15 footprints built from a datasheet that miss KiCad (zoom run)** are mostly not misread numbers: the read
is right and the footprint is built by different rules than KiCad's.

* Courtyard only, every pad right (3): AM26LS31CN, 27C256, SM13B-GHS-TB. ecadlib draws the courtyard around the
  pads; KiCad draws it around the body (DIP body, connector housing).
* Every pad off by the same amount (6), a land-size rule: CD4066BM, BCR430UW6 and C8051F320-GQ follow the maker's
  land; ATSAME70N19A/N20A (all 100 pads 0.125 mm), ISL8117FVEZ (0.4 mm) and LTC7810 (0.175 mm).
* Pads KiCad merges (2): FDMT800100DC and BSC026N08NS5, drain leads merged into the exposed pad (3 and 5 pads).
* Thermal pad (1): TLC5951DAP got the package's exposed metal (3.04 x 5.7 mm); KiCad's copper is the PowerPAD
  land (5.2 x 12.5 mm).
* Real reading errors (about 3): S28B-PHDSS numbered from the other end; ATSAME70Q19A-AN's pin table was not on the
  pages sent (footprint right, no symbol pins); LTC7810's symbol has 49 pins to KiCad's 34 (KiCad merges repeated
  power pins).

The larger loss is the 51 parts that get no footprint at all. All 67 misses of the zoom run:

* **19 had no PDF to read.** 12 have no usable link in the set (dead links, HTML pages): ENS210, ICE40HX8K-BG121,
  MKE02Z16VLD4, GBU4A, C3D02065E, KBU4A, AUIPS6011R, AP1117-18, 2ED21824S06J, HCPL-314J, C3D08060G, 16550. The rest are links that still fail every way ecadlib tries (404 behind a 403, or no answer): EPM240F100,
  EPM1270M256, LTV-352T, DNA40U2200GU, BAT160A, BAT160C, IPS6011RPBF. A person with the PDF can give it to
  ecadlib directly; the benchmark does not. On 10-19 there were 39 of these; the new download chain got 20 more,
  including all 11 analog.com parts.
* **17 were held because the cross-check's second read disagreed**: BUK7M10-40EX, TC2030, LT3652IDD,
  PSMN5R2-60YL, IP4234CZ6, OPA569DWP, LTC2942-1, AT24CS01-MAHM, BCV61, PCA9557BS, S13B-EH, UCC256301, IR4301,
  LT8610, MAX22201, IRFS4115, AL8860MP. Holding them is the intended behaviour (nothing wrong is published); they are
  counted as misses.
* **14 reads left a dimension blank** (held, nothing built): the PDF has no PCB drawing (Neutrik NC3FAAV1-0,
  NC3FBH2-DA, NC3MBV-1, NLJ2MDXX-V), the PDF is the wrong document (M2GL090T-FG484's link gives a press release,
  LTC4060EDHC's gives the LTC4054 sheet), or one number is missing or given only as a minimum (LP38693MP-1.8's lead
  length is printed "0.9 MIN", which the reader's schema rejects; KTY83, NSJ12HF-1, TLP2361, MC33063MNTXG,
  BQ51051BYFP, UCC1895J, MKL24Z32VLK4).
* **7 scored misread_dimension**, all covered above: ISL8117FVEZ, ATSAME70N19A-AN, ATSAME70N20A-AN, LTC7810,
  AM26LS31CN, SM13B-GHS-TB, 27C256.
* **3 follow the maker's land pattern** (above), and **2 have a right footprint but a different symbol**
  (ATSAME70Q19A-AN; SM6T100A, never read, standard footprint).
* **2 are the package KiCad merges**: FDMT800100DC and BSC026N08NS5 were read as 8 leads and an exposed pad; KiCad
  merges the drain pins into the pad (3 and 5 pads).
* **The rest**: BD33FC0FP (a TO-252 with a tab, read without the tab's position, so no footprint), TLC5951DAP
  (built the 3.04 x 5.7 mm exposed pad; KiCad uses a 5.2 x 12.5 mm thermal land), S28B-PHDSS (numbered from the
  other end, mirrored against KiCad).

## Review

The benchmark builds what a BOM run builds, but no one reviews it, so `ecadlib status` has no approvals to count
(#45). What it does show: every one of the parts built from a datasheet is held for a person to compare with the
drawing before release, with the pages to check in its `datasheet/<part>.review.md`; the 17 cross-check
disagreements are held with both values side by side. The review rate on a real library (passed first time, approved, rejected, changed) comes from
`ecadlib status` on a workspace after people have reviewed it, and the trial reports quote it.

## Cost and time

From the API's own usage (cost.jsonl), all in Message Batches at half price:

* Opus, zoom on: $23.19 ($0.21 a part over the set), 7.67 M tokens in, 0.36 M out, about 80 minutes.
* Opus, default settings: $18.73 ($0.17 a part), 6.57 M tokens in, 0.36 M out, about 50 minutes, plus $6.68 of
  that for the cross-checks the #147 bug had skipped on the first pass.
* Haiku, default settings: $0.92, 7.64 M tokens in, 1.14 M out.

The reports' own totals are a little lower ($21.69 and $18.01); the figures above are from the usage the API
returned. The re-scores (`--cached-only`) cost nothing.

## Running it again

```
ecadlib eval run --set kicad-v1 --limit 110 --sources standard,datasheet --batch \
    --record bench/rec -o bench/run                      # live: reads every datasheet, about $19
ecadlib eval run --set kicad-v1 --limit 110 --sources standard,datasheet \
    --replay bench/rec -o bench/replay                   # free: the saved answers, scored by today's code
ecadlib eval run --set kicad-v1 --limit 110 --sources standard,datasheet --cached-only -o bench/run
                                                         # free: re-score the live run's folder in place
ecadlib eval compare bench/run bench/replay              # what changed, per family and per part
ecadlib eval pin bench/run                               # pin the PDFs read (URL + SHA-256) in kicad-v1.pins.json
ecadlib eval publish bench/run -o docs/benchmark-results.md   # the results page: headline, failures by type
```

The run needs an Anthropic API key (`ecadlib keys`); `[datasheet] max_cost_usd` caps what one run may spend
($10 by default; the 110-part batch runs cost $18.73 and $23.19, so pass `--max-cost-usd 35`; `--yes` does not lift it), and the estimate is shown before anything is sent. `report.md` and `report.json`
in the out folder have every part's result, the reason for each miss, and the cost. `--max-cost-usd` is per run: a
run resumed in the same out folder may spend it again. For a cap over all the folder's runs, pass `--max-total-usd N`:
what the folder's `cost.jsonl` already holds counts against it, and nothing is sent when it is used up. Any model works: `--model`, or
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
| held_failed_read | held: the last read still failed a check that could build it wrong |
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

1. Accept a dimension the drawing gives with one bound only ("0.9 MIN"), with a review note (LP38693MP-1.8,
   IRFS4115's tab; draft PR #148).
2. Check that a downloaded PDF names the part before reading it (M2GL090T-FG484, LTC4060EDHC).
3. Look at the 17 cross-check holds one by one: which of the two reads was right, and whether the rules are too
   strict.
4. Build footprints by KiCad's own rules where the read is already right: courtyard around the body as well as the
   pads, the land size worked out as KiCad's generator does, drain leads merged into the pad on power DFNs, and the
   PowerPAD land as the thermal copper (about 10 of the 15 built-but-wrong parts, an estimate; a free
   `--cached-only` re-score shows the real effect).
5. TO-252 tab parts read without the tab's position (BD33FC0FP), and ATSAME70Q19A-AN's pin table.
