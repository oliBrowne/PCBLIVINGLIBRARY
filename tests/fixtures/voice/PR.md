# Library agent batch b1

**1 part to review** (1 held), built 2026-10-16.

**Next:** compare each part's pictures with its datasheet, then **Approve** or **Reject** it in the review queue (`ecadlib ui`, Review queue, or `ecadlib agent approve ID`). Approved parts are released to Altium 365 by the agent; merging this PR only keeps the library files in git.

## X1 (ACME)

Queue item `q1`, watch: **new** in bom\_file b.csv

Library part X1: **held**; symbol from KiCad library \(R\), footprint from datasheet reader.

Datasheet: <https://example.com/x1.pdf>

**Independent check:** doubt: pin 1 mark on the other side (m)

**Check before you approve:**

- The independent check is not sure about this part: pin 1 mark on the other side. Next: Compare the part with the datasheet before you approve it.
- The datasheet read and the standard package table disagree on pin count \(5 vs 3\); nothing was written to the library. Next: Check the datasheet and pick one, or release anyway.
- The footprint came from a model reading the datasheet, so a person has to check before it is released. Next: Compare it with the datasheet, then approve it or release anyway.

<sub>Opened by ecadlib agent. It never force-pushes; re-runs update this PR.</sub>
