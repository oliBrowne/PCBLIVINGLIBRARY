# API keys and AI costs

ecadlib is free and runs on your computer. The AI steps (reading a datasheet, writing a house style guide, the
optional visual check) use **your own** API key and bill **your** account. This page says where that key lives, who
can read it, and how ecadlib shows the cost before it spends anything.

## Where your key lives

Store the key with ecadlib, not in an environment variable:

```
ecadlib keys set ANTHROPIC_API_KEY     # asks for the key, hidden; or paste it on the setup page (ecadlib setup)
ecadlib keys list                      # names only, never the values
ecadlib keys remove ANTHROPIC_API_KEY
```

The key goes into a file of your own user account:

| System  | File | Protection |
|---------|------|------------|
| Windows | `%APPDATA%\ecadlib\keys.json` | each value encrypted for your Windows account (DPAPI) |
| macOS   | `~/Library/Application Support/ecadlib/keys.json` | readable by you only (mode 600) |
| Linux   | `~/.config/ecadlib/keys.json` (`$XDG_CONFIG_HOME`) | readable by you only (mode 600) |

`ECADLIB_HOME` moves the folder. `ecadlib.toml` holds only the *name* of a key (`api_key_env`), never the key.

## Only ecadlib reads it

- ecadlib reads the stored key at the moment it needs it. It never copies the key into an environment variable,
  never runs `setx`, never edits a shell profile, and never asks you to set one.
- So other programs cannot pick it up: not a coding assistant, not an SDK, not a build tool, and not any program
  ecadlib itself starts (KiCad, git, Altium).
- The key is never printed, logged, cached, written to a report, or returned by the web UI.

**Why this matters.** Several tools look for `ANTHROPIC_API_KEY` (or `OPENAI_API_KEY`) in the environment on their
own and bill whatever key they find. Claude Code is one: with `ANTHROPIC_API_KEY` set, it uses that key, not your
Claude subscription. A key you set with `setx` or in your shell profile is visible to every program you start.

If an environment variable of the same name exists, ecadlib still uses the stored key first and the variable only
when nothing is stored (for CI). `ecadlib doctor` warns when an AI key comes only from an environment
variable. To move it: run `ecadlib keys set ANTHROPIC_API_KEY`, then delete the variable (Windows: Settings > System >
About > Advanced system settings > Environment Variables; macOS / Linux: the line in `~/.zshrc`, `~/.bashrc` or
`~/.profile`), and open a new terminal.

The accuracy gate (`ecadlib eval gate`) and the golden builds never read the key store
(`ECADLIB_NO_STORED_KEYS=1`), so they can never make a paid call.

## The cost, before and after every AI step

The Altium dialog, the KiCad window and the web UI never start a paid model call on their own.

1. **Fill the library** and **Add part from MPN** run every free source first (your libraries, KiCad, EasyEDA,
   standard parts). A part only a datasheet read can finish is priced, not sent.
2. You see the AI step: how many parts, **which model**, **about** how much and **at most** how much (every answer
   at its longest, every retry on the part's route, and the cross-check's second read). Nothing is sent until you
   click **Read with AI**.
3. When the most it can cost is above your limit, ecadlib asks a second time. The limit is `[datasheet]
   max_cost_usd` when you set one, else `[datasheet] ask_above_usd` (default $1.00). A model whose price ecadlib
   does not know always gets that second question.
4. The reads are held to the amount you approved: if a retry or a longer answer would go over it, that part is not
   sent and stays held.
5. Afterwards the dialog shows what the reads **really** cost (from the usage the API returned, recorded in
   `<plan folder>/datasheet/cost.jsonl`) next to the estimate.

The house style guide (web UI, *Let my AI model write a style guide*) works the same way: the scan is free, the
guide is priced, and the model is asked only after you click.

```toml
[datasheet]
ask_above_usd = 1.0     # ask twice above this (the dialogs always show the cost and wait for a click)
max_cost_usd = 0        # a hard cap: the command line refuses a run above it unless --yes; the dialogs ask twice
```

On the command line, `ecadlib datasheet estimate PLAN` prints the same estimate and sends nothing, and
`ecadlib costs` shows what each job costs on each model. The MCP server keeps its `datasheet_estimate` and
`datasheet_batch` tools: `datasheet_batch` needs the amount you approved of `datasheet_estimate`'s quote
(`approved_usd`) and is held to it. `--offline` sends nothing to the model: no datasheet is read until you run without it.
