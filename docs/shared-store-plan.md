# Shared store of confirmed parts: plan and architecture

Status: plan, 2026-11-23. Build-out is tracked at the end. Nothing here is live until the store repository exists and
Oliver approves the first publish.

## In plain words

Today every ecadlib user builds every part from scratch: look it up, maybe pay an AI to read the datasheet, run the
checks. The shared store is a public list of parts that somebody already built and that passed the checks. ecadlib
looks there first. If the part is there and trusted, it is downloaded and drawn in the user's own style, and it lands
in their Altium or KiCad library exactly as a locally built part does. If it is not there, ecadlib builds it as it does
today.

Three things the store must never do:

1. **Spread a wrong part.** A store part goes through the same release check on the user's machine as a locally built
   part, and by default only parts that were confirmed twice are used without a person looking.
2. **Share what we have no right to share.** No maker 3D models, no datasheet PDFs, nothing from sources whose terms
   forbid it.
3. **Leak what a user is working on.** Looking a part up tells GitHub no more than "somebody asked for one of a few
   hundred parts". Sharing a part is always a deliberate act, never automatic.

## What the store is honestly worth

Not every part gains the same.

| How the part was built | What a new user saves | Worth storing |
|---|---|---|
| AI datasheet read (the costly path: key, money, minutes, and most of the holds) | The whole read, and the hold | Yes, this is the main value |
| Standard table (chip resistors, SOIC ...) or KiCad library by exact name | A few seconds; these are free and repeatable already | Yes, but mainly as confirmation that this MPN really is this land |
| EasyEDA / LCSC conversion | The conversion and its "check this" hold | Only as a pointer (see licences) |
| A user's own or company library, SnapMagic, SamacSys, Ultra Librarian | - | Never: not ours to share |

The second effect: a store record is a **second opinion**. When a user's own build differs from a record, the part is
held and both answers are named. When it agrees with a record, that is evidence only when the record itself is
**confirmed** (two builders drew the same whole part, or a person signed it off): one contributor's build cannot be
verified by anyone (not even which AI model read the datasheet: that is their own word), so agreeing with a `checked`
record releases nothing; the review only notes it. Since a confirmed record with an exact maker + MPN match is used
directly anyway, in practice a lift happens only for a confirmed record reached through another code it covers
(`also`) or for a BOM line with no maker.

What it cannot do: prove a footprint matches the datasheet. No automatic check can. The store's claim is "passed the
release check, and N independent builds agree", and the docs must say so in those words. (The 11-10 review rule stands:
no public "none built wrong" claim.)

## Hosting: options looked at

| Option | Cost | Reads | Writes / review | Problems |
|---|---|---|---|---|
| **Public GitHub repo of JSON records** (recommended) | Free | Plain HTTPS file fetches, no account, no API limit that matters | Pull requests, with GitHub Actions re-checking each one; full history is the audit trail | Contributors need a GitHub account. Oliver is the only maintainer, so the checks must do most of the reviewing. Past roughly 100,000 parts the repo gets heavy (see scale) |
| GitHub Release bundles only | Free | One big download | No per-part review | Slow to update, no per-part pull |
| Hugging Face dataset | Free | Fine | Weaker review tools, second account for everyone | Splits the project across two homes |
| Own server or bucket (Vercel Blob, Cloudflare R2) | Free tier, then paid | Fast | Needs a write service we run | Oliver becomes the operator of a service holding user uploads: cost, abuse, privacy duties. Against "free, no paid tier" |
| PyPI data package | Free | Offline after install | Release per update | Size caps, days-old data, a bad part cannot be pulled quickly |
| KiCad PCM package | Free | KiCad only | - | Does nothing for Altium |

Recommendation: the public GitHub repo is the source of truth, plus a zipped snapshot attached to a GitHub Release each
week for offline and locked-down networks. Nothing else is needed for the first years.

Scale: a record is about 5 to 15 KB. 10,000 parts is about 100 MB of text, comfortable. Clients never clone the repo;
they fetch one small index file and one record per hit. If the store passes about 100,000 parts, split it by maker into
several repos behind the same index format; the client only knows URLs, so that is not a redesign.

The store URL is a setting. A folder on disk or any other git host works the same way, so a company can keep a private
store of its own without us building anything extra. (This is not the cut "team server": there is no server.)

## Licences

| Content | Rule |
|---|---|
| Data read from a datasheet by ecadlib (pin names, pad sizes, dimensions), and the footprint ecadlib drew from it | Shared under MIT. Dimensions and pinouts are facts; the drawing is ecadlib's output |
| Standard-table lands (IPC) | Shared under MIT |
| Symbols and footprints taken from the KiCad libraries | Shared, marked CC-BY-SA 4.0 with the KiCad credit on the record (as #54 does today); the user's exports get the same NOTICE they get now |
| EasyEDA / LCSC content | **Pointer only** in version 1: the record holds the LCSC number, the hash of the pads we saw, and the check result. In version 1 a pointer is only recorded for later: no client builds from it, compares with it or lifts a hold because of it (fetching the part from EasyEDA and comparing the hash is not built). Reason: EasyEDA asks for credit on reuse, and whether a public copy of their library data is allowed is unclear. Not worth the risk |
| Maker 3D models (STEP) | Never stored. The record holds the height and where the model came from; ecadlib fetches or generates the body on each machine |
| Datasheet PDFs | Never stored. The record holds the public link and the PDF's hash |
| Anything from SnapMagic, SamacSys, Ultra Librarian, a `--lib` library, `ecadlib resolve` picks | Never shareable. Their terms forbid redistribution, or we cannot know who owns it |

This is enforced in code by an allow-list of sources, not by trust in the contributor: a record whose symbol or
footprint source is not `datasheet`, `standard` or `kicad` (or an `easyeda` pointer) cannot be exported, and the store's
checks reject it again.

Contributors agree, in the pull request template, that their record is offered under these terms.

## Trust

### Levels

- **checked**: one build, passed the full release check (#141), no `--allow` override, no open "verify" note.
- **confirmed**: checked, plus at least one of:
  - a second, independent build of the same whole drawing: the same pads (geometry hash), the same pins (number, name,
    electrical type, pad) and the same `text_hash` (store_record.text_hash: the normalised footprint and symbol text,
    so silk, fab, courtyard, text, attributes and symbol graphics all count). Independent means another contributor,
    or a maintainer's own builds with another AI model reading the datasheet. Two runs of the same code on the same
    input do not count. A build with the same pads and pins but another drawing is kept as evidence and confirms
    nothing;
  - a person's sign-off of that drawing, recorded with the record (who, when, against which datasheet revision);
  - the part is the KiCad library's own entry for this exact MPN (a curated source).
- **withdrawn**: pulled. Never used; local copies are flagged.

### What a client does with each

| Record | Default behaviour |
|---|---|
| confirmed | Used. Still goes through the local release check like any part |
| confirmed, reached through an `also` code or without a maker | Not used on its own. A second opinion that can release: if the user's own build agrees and was made independently (another kind of source, or two datasheet reads by different AI models), the hold that only waited for a confirmation is lifted; if it disagrees, the part is held |
| checked | Not used on its own, and never releases anything. Compared with the user's own build: agreement is noted ("agrees with one unconfirmed build in the shared store; not enough to release"); disagreement holds the part and names both answers |
| withdrawn | Ignored |

`[store] accept = "confirmed"` is the default; `"checked"` takes single-build records too (more parts at once, more
risk); `"off"` never downloads.

Whatever the level, the download is only another source. The record's footprint and symbol are rebuilt locally and the
release check runs on the result, and every publisher re-runs it (`publish_problems`), so a store part can never skip a
check a local part gets. A record that fails today's release check on the user's machine is held and counted as a
problem with the record.

### How parts get in

1. A user runs `ecadlib store share` on a finished library. It lists the parts that may be shared (ready, allowed
   source, public datasheet link, no override) and shows exactly what would be sent. Nothing leaves the machine yet.
2. It writes the record files and opens the pull request page on the store repo (through the user's own GitHub
   account; ecadlib holds no credentials for it).
3. The store repo's checks run on every pull request (`ecadlib store-repo verify`), with no AI and no secrets:
   - the record matches the schema, holds only allowed fields and allowed sources, no embedded models or PDFs;
   - the part is rebuilt from the record alone and passes the release check with the current ecadlib;
   - the hashes in the record match what was rebuilt;
   - if the store already has this part: same pads and pins adds evidence (a new, independent builder promotes it to
     confirmed); different pads or pins is a **conflict**, which blocks the pull request and marks the existing record
     disputed until a maintainer looks;
   - a contributor cannot confirm their own record (one GitHub account counts once).
4. No pull request is safe to merge unseen: a new `checked` record is neither used on its own nor able to lift a hold,
   but a second contributor's matching build makes it `confirmed`, and a confirmed record is used without a person
   looking. Every pull request is merged by a maintainer who has looked at it; auto-merge is not planned.

The checks cannot read the datasheet, so they cannot tell a wrong-but-plausible footprint from a right one. That gap is
exactly what the `confirmed` rule covers.

### Reporting and pulling a bad part

- `ecadlib store report PART` opens a pre-filled issue on the store repo (same pattern as `report-bug`: nothing is sent
  until the user presses Submit on GitHub).
- A maintainer, or the nightly job, withdraws it: the record moves to the withdrawn list with the reason and the issue
  link. Withdrawals are never deleted.
- A report with a concrete difference (a pad size, a pin) marks the record **disputed** at once: it drops to
  second-opinion use until settled. Disputing never deletes anything, so abuse costs little.
- Every client that can read the store fetches the small withdrawn list on each run. A part in the user's library that
  came from a withdrawn record (or was released by its agreement) is held on the next run that can read the list, and
  shown in `ecadlib upkeep` and refused by every publisher, with the reason. A part that depends on the store is held
  too when the store cannot vouch for it: switched off (`[store] url` empty or accept `"off"`: "release it yourself or
  switch the store on"), or its list unreadable and no copy younger than 30 days (`HOLD_MAX_AGE_S`) saved on the
  computer; a stale or missing list is never taken for "not withdrawn". A person's `--allow` keeps such a part
  released, and those holds go again once the store can be read. The part's source line says which record and revision
  it came from.
- A nightly job on the store repo re-runs the release check on every record with the newest ecadlib. A record that a
  new rule catches is withdrawn automatically. So every general fix we make to the checks also cleans the store.

### Tampering

Records are addressed by the hash of their content and the index lists that hash; a record that does not match its
hash is refused. Transport is HTTPS to GitHub. Signing the index (sigstore) is possible later and not needed while one
repo with protected `main` is the only source.

## Privacy

- **Lookups.** The client fetches store.json, withdrawn.json and the index file of the part number's shard (1/256 of
  all parts, chosen by a hash of the part number): that request narrows the part down to a 1/256 slice. Only on a hit
  does it fetch the record, and that request does identify the exact part: the record's file name is the key id,
  which the public index lists beside the maker and part number. GitHub sees an IP address and those file names;
  ecadlib sends no account and no BOM. For stricter needs `[store] mode = "snapshot"` downloads the whole weekly bundle
  and looks parts up locally (the host then learns nothing about which parts), and `--offline` or
  `[store] accept = "off"` contacts nothing (parts that came from the store are then checked against the saved
  withdrawn list, or held). Publishing commands (`kicad-export`, `altium365 push`, `dblib sync`, `export`, the
  plugins) and `upkeep` contact the store too, only for the parts that depend on it, with the same requests.
- **Sharing.** Only by the explicit command. A record holds only: maker, part number, a description only when a source
  gave it (a part search, a library, the datasheet; never the BOM's own Description or Notes text), the public
  datasheet link and hash, pins, pads, dimensions, the checks passed, the ecadlib version and the AI model name. It never holds the BOM, project or file names, the user's name or e-mail, internal part numbers, stock or
  price fields, or a private or signed link (the same link test the Internet Archive fallback uses). The contributor's
  GitHub name is public through the pull request itself; the command says so before opening the page.
- PRIVACY.md and the /privacy page get one new table row for lookups and one paragraph for sharing.

## Architecture

### Store repository layout

```
store.json                  {"schema": 1, "name": ..., "shards": 256, "min_ecadlib": "0.5.0", "updated": ...}
index/<ab>.json             one of 256 shards, by the first byte of sha256(normalised MPN):
                            {"<key_id>": {"key": "TEXASINSTRUMENTS|NE555DR", "rev": 2, "sha256": <record hash>,
                                          "level": "confirmed", "status": "active"}}
parts/<ab>/<key_id>.json    the record
withdrawn.json              [{"key_id", "key", "rev", "reason", "issue", "at"}]
LICENSE, NOTICE.md, CONTRIBUTING.md, .github/ (verify on pull request, nightly re-check, weekly snapshot, templates)
```

Sharding by part number (not maker) lets a BOM line with no maker still find its part: the shard is scanned for that
MPN, and a hit is taken only when exactly one maker has it, with a note.

### The record (schema 1)

```
{"schema": 1, "key": {"maker", "mpn"}, "also": [other orderable codes with the same land: reel / tray suffixes],
 "rev": 2, "status": "active" | "disputed",
 "part": {"description", "datasheet": {"url", "sha256", "revision"}, "package"},
 "symbol": {"kicad_sym": <KiCad symbol text>, "pins": [{"num", "name", "type"}], "source", "license", "credit"},
 "footprint": {"kicad_mod": <KiCad footprint text> | null, "pointer": {"lcsc": ...} | null, "hash", "hash_v",
               "spec": null (version 1; reserved for the package dimensions as read), "source", "license", "credit",
               "mount_pads"},
 "body": {"height_mm", "height_src", "model": null | {"source", "url"}},
 "pin_map": {pin number: pad number},
 "evidence": [{"kind": "build" | "signoff" | "kicad-exact", "by": <GitHub name>, "at", "ecadlib", "reader":
               {"provider", "model"} | null, "footprint_hash", "pins_hash", "checks": [...rules passed]}]}
```

KiCad text is the canonical form for the drawing, as it already is inside ecadlib (decision of 10-13). The local
converter turns it into the user's Altium or KiCad files and applies their house style there (pin sides, layers, text
sizes), which is how "share the data, draw it locally" is met without a new drawing format. A record carries no
package dimensions in version 1 (`spec` is null; a client accepts one, a later version may fill it).

### Code in this repo

| Module | Job |
|---|---|
| `ecadlib/store_keys.py` | Normalise maker and MPN, the key, its id, the shard, canonical JSON and hash |
| `ecadlib/store_record.py` | Build a record from a released plan item (`from_item`), the share allow-list (`shareable`), `validate`, `level`, `agree`, and `build` (record -> files in a folder, used by the checks) |
| `ecadlib/store_client.py` | Fetch and cache the index and records (URL or folder), verify hashes, withdrawn list, snapshot mode, the accept policy. Never raises into a run: a store that cannot be reached is a miss with one note |
| `ecadlib/librarian.py` | New source `store`, tried after the user's own library and before `kicad`, only while `[store] url` is set (never in the default `[sources] order`: it runs by itself). Writes through the existing `put_fp` / `put_kicad_sym`, so footprint sharing, house style and notes behave as for any source. Second-opinion compare after the other sources ran |
| `ecadlib/store_repo.py` | Maintainer and CI side: `index`, `verify`, `add`, `withdraw`, `snapshot`; and `store_template/` with the store repo's workflows, templates, README and NOTICE |
| `ecadlib/store_cli.py` | `ecadlib store status / get / share / report`, `ecadlib store-repo ...` |
| `ecadlib/upkeep.py`, `release_check.py` | A part from a withdrawn, disputed, gone or down-rated record, or released by such a record's agreement, is held, with the reason; so is one the store cannot vouch for (switched off, or its withdrawn list unreadable with no saved copy younger than 30 days); the upkeep check runs by itself while `[store] url` is set or the plan has a part that depends on the store |
| `ecadlib/rules.py` | `[store] url, accept, mode`; not in the default source order, and left out of the rules hash while the url is empty (nothing changes for anyone who has not set one) |
| docs, PRIVACY.md, site/privacy | User guide `docs/shared-store.md`; privacy rows |

The Altium menu, the KiCad plugin, the web UI and the MCP server need no change to work, since they all run the
librarian. Showing "from the shared store, confirmed by 3" in their part views is a later, small step.

### Flow of one BOM line

1. Local library (unchanged).
2. Store lookup by maker + MPN. Confirmed hit: build from the record, source `store:<key_id>@r<rev>`. Checked hit: keep
   it aside.
3. Other sources as today, for whatever is still missing.
4. If a record was kept aside and the local build finished, they are compared: same pads and pins (both drawings hashed
   at this ecadlib's digest version). Different -> hold with both answers named. The same, and the record is
   **confirmed** (the lower of the index's level and the level recomputed from the record with the store's trusted
   list and maintainers), active, with a drawing, and the two builds independent -> the agreement is recorded and the
   hold that waited for a confirmation is lifted (the read's flagged items go to the review list); the lift is taken
   back if the record is later withdrawn, disputed or gone, and held again while the store cannot vouch for it (off,
   or its withdrawn list unreadable with no saved copy younger than 30 days). The same, but only `checked` -> noted,
   nothing lifted.
5. Release check, as today.

## Seeding

The store starts empty of trust unless we seed it. Candidates are the parts from our own runs that have two independent
reads (the Opus read simulation of 11-19 and the Haiku Bedrock run of 11-20 on the same parts) and the 110 benchmark
parts whose reference lands were checked against the maker's drawing. Parts with one read go in as `checked`. The seed
is produced by a script from those run folders and reviewed as one pull request. Publishing it is outward-facing, so it
waits for Oliver's go.

## Risks and open points

- **One maintainer.** Mitigated by making unconfirmed records harmless and by the nightly re-check; still a queue if
  the project grows. (No pull request is auto-merged: see "How parts get in".)
- **Poisoning.** Someone could submit a wrong part from two accounts to get it confirmed. Mitigations: new GitHub
  accounts' evidence does not count toward confirmed until a maintainer has merged something of theirs before; the
  local release check; fast withdrawal. Not fully closed; no open store can close it.
- **Same model, same mistake.** Two users with the same model can misread a drawing the same way. Agreement between
  two different models counts for more; the evidence records the model so the rule can be tightened later with data.
- **Part number variants.** One land for many ordering codes is common, but a suffix sometimes changes the package.
  Version 1 matches the exact code or an `also` code that a build explicitly listed; no guessing by prefix.
- **Datasheet revisions.** The record stores the PDF hash; a new revision does not invalidate it, but a report can.
- **EasyEDA pointers** give confirmation but not a download; revisit if their terms are clarified.

## Build-out

Tests first, then code, then a separate code review, per the repo's workflow. Sub-agents build; the thread reviews.

| Step | Contents | Depends on |
|---|---|---|
| 1 | `store_keys.py` | - |
| 2a | `store_record.py` (+ tests: round trip build -> record -> rebuild gives the same hashes and passes the release check; every forbidden source refused) | 1 |
| 2b | `store_client.py` (+ tests against a folder store and a mocked HTTP store: hash mismatch, withdrawn, offline, unreachable) | 1 |
| 3a | Librarian source, second opinion, rules, withdrawn holds, CLI `store status/get/share/report` | 2a, 2b |
| 3b | `store_repo.py`, `store_template/`, CLI `store-repo` | 2a |
| 4 | Docs, PRIVACY.md, /privacy; code review of everything; fixes | 3 |
| 5 | Pull request 1 (all of the above; nothing contacts a store until a URL is set) | 4 |
| 6 | Needs Oliver: create the public store repo; then seed script and first publish, default URL switched on | 5 |

Until step 6 the default store URL is empty, so merging step 5 changes nothing for users.
