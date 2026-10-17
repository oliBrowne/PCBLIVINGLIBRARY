# Teach ecadlib your house style

Point ecadlib at the library you already have, tell it in plain words what you want, and every new part comes out
looking like one of yours. You can use any model for the parts that need one: Claude, another API, or a model running
on your own computer. You can also train a model of your own on your library.

```
ecadlib style learn --from D:\Altium\Libraries            # learn your conventions -> house_style.json (offline, no model)
ecadlib style direct "IC descriptions start with the function in capitals"
ecadlib style direct "Write tolerances as ±1%"
ecadlib style learn --ai                                  # the model writes a style guide from your direction
ecadlib style check out/librarian/plan.json               # how far a plan is from your style
ecadlib style apply out/librarian/plan.json --ai          # adapt it (plan.json.style.bak is kept)
```

In the web UI this is the **Settings: your house style** card. The MCP server has `learn_house_style`, `house_style`,
`check_house_style` and `apply_house_style`.

## What it learns

`learn` reads the same files as `ecadlib init`: Altium `.SchLib` / `.PcbLib` / `.DbLib` and their parts tables, Altium
365 component lists, KiCad `.kicad_sym` / `.pretty`, and BOMs. It runs offline and calls no model.

| What | Example | Used for |
|---|---|---|
| Everything `ecadlib init` infers | `RES_{package}_{value}_{tolerance}`, `MPN` for Manufacturer Part Number, Mechanical 15 courtyard | `--write-rules` puts it in `ecadlib.toml` |
| Description pattern per category | `RES {value} {tolerance} {package}` (passives, when one pattern covers at least half the parts) | new passives are described that way |
| How values are written | `±1%` not `1%`, `16 V` not `16V` | new parameter values are rewritten that way |
| Parameters most parts carry | 90% of your capacitors have `Dielectric` | `check` flags a new part without one |
| Active-low pin notation | an overbar (`~{RESET}` in KiCad, `R\E\S\E\T\` in Altium), `RESET#`, `/RESET`, `nRESET`, `RESET_N` | datasheet symbols use your notation |
| Example parts | up to 8 real parts per category | the few-shot examples a model sees |

Everything goes in `house_style.json` next to your `ecadlib.toml`. Commit it with the library so the whole team uses
the same style. `ecadlib style show` prints what was learned.

## Your direction

Direction is what you write. It is the only instruction a model gets; your library is evidence. Where the two
disagree, your direction wins, and the guide lists every place it overrides what the library does today. Lines are
kept until you clear them (`ecadlib style direct --clear`), and `learn` keeps them when it re-scans.

Some direction can be applied exactly. With `--ai` the model turns it into settings that ecadlib applies by code:
naming templates, name case, value style, parameter names and defaults, folders, footprint density, the pin notation
and description patterns. Each setting is checked against the rules file's own validation; a setting that fails is
listed under "not used" and never applied. `learn --ai --write-rules` writes the learned settings and your
direction's settings into `ecadlib.toml`. An existing file is only replaced with `--force` (a `.bak` is kept);
otherwise you get a diff and `ecadlib.toml.proposed`.

Direction that cannot be a setting ("descriptions read like Digi-Key's") goes into the written guide. The model then
follows that guide when it rewords parts in `style apply --ai`.

## Who changes what

* **Code, every time a part is made.** When `house_style.json` exists and `[style] apply = "auto"` (the default), the
  librarian adapts every new part: description patterns, value formats, and active-low pin names on symbols read from
  a datasheet. Each changed part records what changed and why under `house_style` in `plan.json`, and the review
  notes say which pins were renamed. `[style] apply = "off"` turns this off.
* **The model, only when you ask (`--ai`).** It writes the guide (`learn --ai`) and rewords free-text descriptions,
  comments and parameter values to read like your examples (`apply --ai`).
* **Never changed.** Part names are left alone, because they come from `ecadlib.toml`, so a rename always goes
  through your rules file. Manufacturer, manufacturer and supplier part numbers, datasheet links and footprint
  geometry are never shown to a model and never restyled.

Every model's answer goes through the same checks. A rewritten field may not contain a number that is not already in
the part's record (so "100mW" cannot appear on a resistor that never said so). It may not add parameters, and it may
not turn an Altium `=Value` comment into plain text. Anything that fails is listed as "not applied" and the part
keeps its old text. An answer that is not valid JSON gets one retry with the exact error; after that nothing is
changed.

## Which model

The model is set in `ecadlib.toml`, the same way as every other AI feature (see the README's "Bring your own model"
section):

```toml
[style]
model = "claude-haiku-5-5"      # "" = [ai] model (Claude Opus 5.5 by default)
# provider = "ollama"           # or a local / other model just for this
# model = "qwen2.5:14b"
```

How much help the model gets depends on what it can do. A frontier model gets more examples, whole batches of parts
in one request, and the JSON schema as its response format. A small or unknown model gets 2 examples, one part per
request, and a worked example of the answer. If it has no JSON-schema mode, it gets the schema in the prompt and its
answer is repaired (code fences, trailing commas). `[style] assist = "full"` gives any model the small-model help;
`"off"` gives none.

| Tier | Examples per category | Parts per request | Guide rules | Worked example |
|---|---|---|---|---|
| frontier | 8 | 20 | up to 40 | no |
| mid | 4 | 8 | up to 20 | yes |
| small | 2 | 1 | up to 10 | yes |

`learn` without `--ai` and the code pass need no model and no key.

## Running on your own hardware, and training your own model

Everything runs on your machine. The library is read locally, `house_style.json` is a local file, and a model is
only called if you pass `--ai`. If you point `[style]` at a local server (Ollama, LM Studio, vLLM, llama.cpp), nothing
leaves your network.

To train a model on your library:

```
ecadlib style export-dataset -o house_train.jsonl --eval-share 0.1
```

This writes one chat-format JSON line per part, plus a held-out `house_train.eval.jsonl`:

* **system:** "Write this component the way our library does: name, description and parameters, as JSON."
* **user:** neutral facts: category, MPN, manufacturer, value in plain SI form, package, footprint, and the values
  stripped of house formatting.
* **assistant:** your record: name, description and parameters exactly as your library has them.

OpenAI fine-tuning, Unsloth, axolotl and mlx-lm all take this format. The split between training and held-out lines
depends only on each record, so re-running the export gives the same split. A LoRA on a small open model (for
example Qwen 2.5 7B with Unsloth) is a reasonable start. Export the result to GGUF, load it in Ollama
(`ollama create house-style -f Modelfile`), and name it in `[style] model`. ecadlib treats it like any other model:
an unknown model gets the small-model help until you set `[ai.profiles."house-style"] tier = "mid"`, and the same
checks apply.

The fine-tuning step itself is not part of ecadlib (it needs a GPU and a training stack). The dataset is what
ecadlib provides. Your library's data stays in files you control.

## Limits

* Description patterns are learned for resistors, capacitors and inductors, whose descriptions are built from
  fields. ICs and other free-text descriptions are learned only as examples (and their case); rewording those needs
  `--ai`.
* Value-format learning covers units without an SI prefix that could be confused (`V`, `W`, `A`, `%`, `ohm`). `10K`
  and `100nF` belong to the value style in `ecadlib.toml`.
* Pin notation applies to symbols read from a datasheet. Symbols taken from a KiCad library or EasyEDA keep their
  own names.
* Tested on synthetic libraries written by ecadlib's own Altium and KiCad writers, with a fake model standing in for a
  real one. No real customer library has been scanned and no real model has been run against it yet.
