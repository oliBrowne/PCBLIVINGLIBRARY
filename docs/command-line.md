# Command line, setup and integrations

First run, CLI conveniences, the Windows installer and setup page, notifications and plugins, and the shared voice.

On this page:

* [First run: `ecadlib init` setup, `ecadlib demo`, and a consistent CLI (`firstrun.py`, `clihelp.py`)](#first-run-ecadlib-init-setup-ecadlib-demo-and-a-consistent-cli-firstrunpy-clihelppy)
* [Command-line conveniences: `doctor`, `status`, `completion`, `--json`, environment defaults](#command-line-conveniences-doctor-status-completion---json-environment-defaults)
* [Easy setup: the Windows installer, the setup page and stored keys](#easy-setup-the-windows-installer-the-setup-page-and-stored-keys)
* [Connections to other tools: notifications, GitLab, plugins](#connections-to-other-tools-notifications-gitlab-plugins)
* [One voice: `messages.py`, `reports.py` and `docs/ui-voice.md`](#one-voice-messagespy-reportspy-and-docsui-voicemd)

## First run: `ecadlib init` setup, `ecadlib demo`, and a consistent CLI (`firstrun.py`, `clihelp.py`)

```
ecadlib demo [-o DIR] [--tool altium|kicad|both] [--bom BOM] [--json]     a small library, offline, no keys
ecadlib init [--yes] [--tool T] [--lib DIR ...] [--workspace W] [--sources LIST] [--from DIR ...] [--no-doctor]
```

* **`init` setup.** Besides the conventions of [library rules](librarian.md#rulespy-and-ecadlib-init-one-file-for-your-library-conventions), `init` asks (or takes from the flags, or with `--yes` uses
  the defaults): the tool (`both`), your library folders (none), the workspace (`ecadlib_workspace`), whether to look
  parts up online (KiCad library on gitlab.com, EasyEDA/LCSC) and whether to use the Claude datasheet reader (default
  yes only when its key is set). The answers go in `[setup]` (`tool`, `libs`, `workspace`; relative paths start at the
  file's folder) and, when you changed them, `[sources] order`. Then it creates the workspace, prints which API keys are
  set (names only), runs the doctor and prints the next two or three commands for your tool. Re-running `init` keeps
  the answers as the new defaults.
* **`[setup]` as defaults.** `libs` and `workspace` fill in `--lib` / `--workspace` for the same commands as
  `ECADLIB_LIB` / `ECADLIB_WORKSPACE` ([command-line conveniences](#command-line-conveniences-doctor-status-completion---json-environment-defaults)), after the command line and the environment. A rules file without
  `[setup]` changes nothing.
* **`demo`** writes a real workspace (default `./ecadlib-demo`) with its own `ecadlib.toml` (`[sources] order =
  ["standard"]`, so a rules file in a parent folder cannot change it), the bundled BOM in `uploads/demo/`, the librarian
  plan and Altium models in `librarian/`, and for KiCad the `.kicad_sym`, `.pretty`, lib tables and database library in
  `kicad/`. Re-running reuses the workspace (finished parts are skipped). Measured here: 1.0 s, 6 ready + 1 held.
* **Help.** Every command and subcommand has a one-line description and every command's `-h` ends with examples that
  parse (`tests/test_firstrun.py` walks the whole argparse tree and parses every example). Same names for the same
  things: `--lib`, `--workspace`, `-o/--out`, `--json`, `--dry-run`, `--offline`. `librarian` and `kicad-export` gained
  `--json`; `librarian` prints progress lines (BOM read, KiCad index build, `[n/N] part`) and `kicad-export` ends with a
  line saying what was written where.
* **Failure paths** (one line plus `hint:`, exit 2, before anything is written): a `--lib` folder that does not exist
  (also from `ECADLIB_LIB` or `[setup]`), an output that cannot be written (`-o` under a file, or a folder you cannot
  write to), a BOM with no recognised columns (names the columns to use and the `[bom.columns]` fix), an empty BOM or
  one with a header and no rows. After a librarian run, held parts caused by something outside the part get a `note:` and
  a `fix:` line: a source that could not be reached (proxy refused, timeout, HTTP 429) and the datasheet reader without
  its API key.
* **Not done:** `--workspace` still means an Altium 365 URL for `upkeep` and `upstream` (renaming it would break
  scripts); `dblib-sync` keeps `--to` for the library folder (its `-o` is the report); `--json` is not on every command.

## Command-line conveniences: `doctor`, `status`, `completion`, `--json`, environment defaults

```
ecadlib doctor [--workspace W] [--lib DIR] [--rules F] [--json]   is this machine ready? (exit 1 on any FAIL)
ecadlib status [--workspace W] [--json]                           one screen about a workspace (read-only)
ecadlib completion bash|zsh|fish                                  shell completion script
```

* **doctor** prints one line per check, each OK / WARN / FAIL with a fix: Python version (3.10 warns when neither tomllib nor tomli can read TOML), the core packages (FAIL when missing), pillow / pypdf, `pdftoppm`, `kicad-cli` and `git` (WARN), which API keys are set (variable **names** only, never values; the names follow `[datasheet] api_key_env` and `[agent] github_token_env`), the rules file in use and whether it is valid (an error names `file:line`), the workspace (exists, writable: a temporary probe file is created and removed) and the `--lib` folders. Also `Workspace.doctor()` and the MCP tool `doctor`.
* **status** summarises a workspace without creating or changing anything: parts per librarian plan (ready, held, waiting for sign-off, duplicates), uploaded BOMs, the last triage, the last upkeep run, how parts fared in review, and what to run next. A damaged file is named under WARN and skipped. Exit 1 when the workspace does not exist. Also `Workspace.status()` and the MCP tool `workspace_status`.
* **completion** builds the script from each command's own argparse parser (commands, subcommands, options, fixed choices), so it always matches the installed version: `ecadlib completion bash > ~/.local/share/bash-completion/completions/ecadlib`, `source <(ecadlib completion zsh)` (uses zsh's `bashcompinit`), `ecadlib completion fish > ~/.config/fish/completions/ecadlib.fish`.
* **Errors:** a missing file, bad JSON, an unreadable table, a missing package, no permission or a bad `ecadlib.toml` print one line and a hint (exit 2) instead of a traceback; anything else prints `internal error (...)` (exit 1). `--debug` anywhere on the line, or `ECADLIB_DEBUG=1`, shows the full traceback.
* **Environment defaults** (a flag on the command line always wins): `ECADLIB_LIB` = `--lib` (several folders separated by `:`, `;` on Windows) for `triage`, `librarian`, `resolve`, `kicad-export`, `mcp`, `ui` and `doctor`; `ECADLIB_WORKSPACE` = `--workspace` for `mcp`, `ui`, `visual-check`, `doctor`, `status` and `review` (with neither set nor [setup] workspace, the workspace chosen on the setup page is the default when there is no `./ecadlib_workspace`) (not for `upkeep` / `upstream`, where `--workspace` is an Altium 365 URL); `ECADLIB_CONFIG` = the rules file wherever ecadlib searches for `ecadlib.toml` (an explicit `--rules` and a workspace's own `ecadlib.toml` still come first). Without it `ecadlib.toml` is found by walking up from the working folder (unchanged).
* **Machine-readable output (`--json`)**, one JSON object on stdout, nothing else; the keys below are stable (new keys may be added):
  * `triage --json`: `{"bom", "lines", "mapping", "counts": {status: n}, "rows": [{"line", "designator", "qty", "mpn", "manufacturer", "lcsc", "value", "package", "status", "local_symbol", "local_footprint", "src", "src_symbol", "src_footprint", "src_3d", "src_mpn", "notes"}], "reports": [md, csv]}`
  * `release-check --json`: `{"plan", "report", "counts": {"ready", "held", "awaiting_approval", "duplicate"}, "rows": [one per part, as release_check.csv]}`; exit 2 when a part is held, as before.
  * `approve PLAN status --json`: `{"plan", "policy_file", "enforced", "awaiting_approval", "rows": [{"name", "status", "approved_by", "missing", "stale", ...}]}`
  * `doctor --json`: `{"ok", "checks": [{"check", "level", "detail", "hint"}]}`; `status --json`: `{"workspace", "exists", "rules", "boms", "plans", "totals", "last_triage", "last_upkeep", "last_run", "review", "problems"}`. `last_run` is the newest BOM run: `{"plan", "started", "finished", "steps": {"triage", "build", "release_check", "review", "push": seconds}, "work_seconds", "total_seconds"}` (`work_seconds`: ecadlib's own time; `total_seconds`: first step to last, people's review included; from `<plan dir>/timing.jsonl`). `review` is `{"built", "held", "passed_first_time", "approved", "rejected", "changed", "held_reasons": [{"reason", "parts", "example"}]}` over every plan; each plan row carries its own `timing` and `review`
  * Already there before: `resolve --json`, `project --json`, `init --json`, `footprint-bench --json FILE`.

### The terminal, human-friendly: `term.py` and `ecadlib review`

Every command people run most (`demo`, `init`, `doctor`, `librarian`, `triage`, `release-check`, `review`, `status`,
`kicad-export`, `dblib-sync`, `altium-pkg`, `parity`, `eval gate`, `migrate`, `rebuild`)
prints through one small layer, `ecadlib/term.py` (stdlib only), in the shared voice (`docs/ui-voice.md`):

* **One vocabulary:** a part is `✓ ready`, `! held` (with the reason in plain words, from `ecadlib/messages.py`) or
  `○ waiting for sign-off`; `… working`, `✗ failed`, `- skipped`. Plain output (piped, logs) prints the word only.
* **Colour and symbols only on a terminal**, never with `NO_COLOR`, `--json`, `TERM=dumb` or in CI logs (`CI`,
  `GITHUB_ACTIONS`, `GITLAB_CI`, ...). `ECADLIB_COLOR=always|never` overrides.
* **Tables fit the terminal** (`COLUMNS`, else its width; no limit when piped): the last column wraps under itself.
* **A final summary block:** the outcome (`6 ready, 1 held`), what was written where, and the next 1-3 commands.
* **Live progress** on a terminal: `librarian` shows one line `[3/14] SOT-23  ✓ ready` that updates in place, then a
  row per part; piped, the plain log lines as before. A slow network source or model call says what it waits on and
  for how long (`… waiting on the KiCad library (gitlab.com)  12 s`); piped: `still waiting on ... (30 s)` after 10 s,
  30 s, 60 s and then every minute. Live lines go to stderr.
* **`-q` / `--quiet`** (only the outcome and errors) and **`-v` / `--verbose`** (every reason with its fix, notes,
  sources) work for every command, anywhere on the line.
* **`--json` is unchanged**, byte for byte (`tests/test_term.py`).

```
ecadlib review [PLAN] [--workspace W] [--part P] [--yes] [--offline] [--lib DIR] [--limit N]
```

`ecadlib review` goes through the held parts one at a time: the reason and the fix in plain words, the datasheet link,
and for a missing symbol / footprint (or pins that do not match the pads) the best candidates from `resolve`. Then it
asks: `1-N` use a candidate (converted, written into the plan, re-checked), `a` release anyway (recorded with your name,
like `release-check --allow`; not offered when a file is missing), `d` open the datasheet (at the page a reason names),
`s` skip, `q` quit. With `--yes`, or when not on a terminal, it never asks: it prints each part with the commands that
fix it. Without PLAN it reviews every plan in the workspace. Exit 2 while anything is still held.

## Easy setup: the Windows installer, the setup page and stored keys

For people who are used to installers, not terminals. The guide for them is `docs/START_HERE.md`.

* **The installer** (`installer/windows/`, built by `.github/workflows/windows.yml` on every PR and attached to each
  release by `release.yml`): PyInstaller bundles Python and every dependency into `ecadlib-app.exe` (no console: the
  desktop app) and `ecadlib.exe` (the command line). Inno Setup wraps them into `ecadlib-setup-<version>.exe`. It
  installs per user by default (`%LOCALAPPDATA%\Programs\ecadlib`, no administrator) and adds Start menu entries for
  **ecadlib** and **ecadlib setup**. A desktop icon and adding `ecadlib` to PATH are optional. CI installs the result
  silently, runs it, checks the Start menu entries and PATH, and uninstalls it.
* **`ecadlib start`** (`launcher.py`, what the Start menu runs) starts the web UI on 127.0.0.1:8765 and opens the
  browser. The first run opens the setup page. A small window has Open, Setup, Workspace folder and Quit, and closing
  it stops ecadlib. A second start reuses the running copy.
* **`ecadlib setup`** (`easy_setup.py`, `ui/static/setup.html`): pick the AI provider and model, then paste and check
  the key (a free request for the provider's model list). It finds Altium Designer and KiCad (`detect.py`) and asks
  for the tool, the workspace and your library folders. Saving writes `<workspace>/ecadlib.toml` through `ecadlib
  init`'s own code, so it learns your naming from your libraries. **Add to Altium** writes the menu script project
  ([Altium and KiCad plugins](altium.md#plugging-into-the-tools-kicad_pcmpy-and-altium_pkgpy)) and sets it to run this ecadlib. One step inside Altium is left (Install from file...), because Altium
  keeps its list of script projects to itself. **Add to KiCad** copies the action plugin into each KiCad's
  `scripting/plugins` folder, with this ecadlib's command next to it, so no PATH change is needed.
* **`ecadlib nexar signin|status|signout`** (`nexar_signin.py`): sign in to Altium 365 once with your own free Nexar
  app; ecadlib refreshes the token by itself, so there is no daily portal token to paste. See
  [Signing in to Altium 365](altium.md#signing-in-to-altium-365-ecadlib-nexar-signin).
* **`ecadlib keys list|set NAME|remove NAME`** (`userconfig.py`): API keys stored per user, the way to give ecadlib a
  key. On Windows they are encrypted with DPAPI for your account. Elsewhere they are in a file only you can read
  (mode 600). The folder is `%APPDATA%\ecadlib`, `~/Library/Application Support/ecadlib` or `~/.config/ecadlib`, and
  `ECADLIB_HOME` overrides it. ecadlib reads a stored key when it needs it and never puts it into an environment
  variable, so other programs (a coding assistant that bills an `ANTHROPIC_API_KEY` it finds, or anything ecadlib
  starts) cannot use it. An environment variable of the same name is used only when nothing is stored, and `ecadlib
  doctor` warns about it. A key value is never printed, logged, shown by the web UI or written to `ecadlib.toml`.
  See [docs/api-keys.md](api-keys.md).
* **The doctor on a fresh install** now shows only what matters. A key or tool for a feature you have not turned on
  is OK ("only needed for ..."): pdftoppm for visual-check, git and GITHUB_TOKEN without an `[agent] github_repo`
  (upkeep pull requests), NEXAR_TOKEN without an `[altium365]` workspace, kicad-cli when you only make Altium
  libraries. The optional part-lookup services share one line. kicad-cli is also found in KiCad's install folder when it is not on PATH. The hints say
  `ecadlib keys set NAME` or the setup page instead of a Unix `export`.

Code signing: the build signs the app and the installer with Azure Artifact Signing once the repository has the
secrets and variables in `docs/code-signing.md`; until then it is unsigned. Still to do: macOS (a `.dmg` or `.pkg`).

## Connections to other tools: notifications, GitLab, plugins

**Notifications** (`ecadlib/notify.py`). With `[notify]` targets in `ecadlib.toml`, `ecadlib upkeep` posts when it finds new
changes (`upkeep_changes`) and when a part goes EOL, NRND or last time buy (`lifecycle`), after a run that saved its
state. Nothing is sent without targets.

```toml
[notify]
retries = 3          # retried on 429, 5xx and network errors: backoff_s, then 2x, 4x; 60 s budget for all retries
[[notify.targets]]
name = "library-channel"
type = "slack"                      # slack (incoming webhook) | teams (Teams workflow webhook) | json (generic POST)
url_env = "SLACK_WEBHOOK_URL"       # the NAME of the environment variable; the URL itself never goes in the file
events = ["upkeep_changes"]         # default: both
[[notify.targets]]
type = "json"
url_env = "ERP_HOOK_URL"
token_env = "ERP_HOOK_TOKEN"        # json only: Authorization: Bearer <value>
events = ["lifecycle"]
```

Slack gets a `text` message, Teams an Adaptive Card message, and `json` gets
`{source, event, title, text, lines, more, url, data, time}`. A failed post goes into the upkeep report
(`rep["notify"]`) and the log, and the run carries on. Error messages name the target and host, never the URL.
`ecadlib notify show` lists the targets and whether their variables are set. `ecadlib notify test [--event E]
[--target NAME]` sends a test message.

**GitLab** (`ecadlib/forge.py`). `ecadlib upkeep --gitlab issue|pr` opens the rolling issue or a merge request. It
follows the GitHub path step for step: it finds the open MR by source branch (MRs from forks are ignored), updates it
in place, opens a new MR when the old one was merged or closed, adds outcome notes and closes with `state_event=close`. The host is chosen by `[agent] git_host = "auto"`:
GitLab when origin is on a `gitlab*` host or on the host of `gitlab_api`, otherwise GitHub, so nothing changes for
GitHub users. Settings: `gitlab_api` (default `https://gitlab.com/api/v4`; for a self-hosted server, e.g.
`https://git.example.com/api/v4`, including servers under a path), `gitlab_project` (`group/sub/name` or a numeric id;
empty = taken from the origin remote) and `gitlab_token_env` (default `GITLAB_TOKEN`, needs api scope). In GitLab CI,
`upkeep` reads `CI_PROJECT_PATH` and `CI_API_V4_URL` by default. Checked live against gitlab.com, read-only: the merge
request list and get, issue list and project lookup, with the field names used (`iid`, `web_url`, `state`,
`source_project_id`, `target_project_id`). Opening, editing and closing MRs and issues is only tested against a fake
server, because there is no token in this sandbox.

**Plugins** (`ecadlib/plugins.py`). Part sources and exporters can be shipped as separate Python packages:

```toml
# the plugin's pyproject.toml
[project.entry-points."ecadlib.providers"]
stockroom = "ecadlib_example_plugin:StockroomCSV"      # a Provider class: name, auth, env, lookup(line) -> SourceResult
[project.entry-points."ecadlib.exporters"]
markdown-catalog = "ecadlib_example_plugin:markdown_catalog"   # export(items, out_dir, rules) -> [paths]
```

`[providers] order = ["stockroom", "jlcpcb_search", "easyeda"]` chooses and orders the part sources for triage, the
librarian and the web UI / MCP workspace. `[]` keeps each command's default, and `--providers` still
overrides it. `jlcpcb_search` is always placed before `easyeda`, because it finds the LCSC number that EasyEDA looks
parts up by. If a plugin raises an exception, or returns something other than a `SourceResult`, the BOM line gets a
note and triage carries on. A plugin that fails to import is listed by `ecadlib plugins list` with its error. Run an
exporter with `ecadlib plugins export NAME plan.json -o DIR` (MCP `plugin_export`, `Workspace.plugin_export`).
`examples/ecadlib_example_plugin/` holds a complete example: a stockroom-CSV part source and a Markdown catalog
exporter. Install it with `pip install ./examples/ecadlib_example_plugin`.

## One voice: `messages.py`, `reports.py` and `docs/ui-voice.md`

Every message and report a person reads follows [docs/ui-voice.md](ui-voice.md): what happened first, then what
to do; a part is **ready**, **held** or **waiting for sign-off**; numbers with units; errors in two lines; no internal
names or server paths.

* `ecadlib/messages.py` is the one table of held reasons: `held(code, **values) -> (text, fix)`,
  `explain(raw_reason) -> {code, text, fix, note}` for any reason already in a plan, `error()` / `friendly_error()`,
  `status_word()`, `counts()` ("6 ready, 2 held"), `source_word()` and `clean()`. Plans, CSVs and `--json` keep their
  machine strings; the sentence is shown next to the code.
* `ecadlib/reports.py` builds the library review once and renders it as `review.md` and `review.html` (the web UI's
  colours, light and dark, readable at phone width): held parts first with each reason and its next step, then parts
  waiting for sign-off, ready parts a person should double-check, duplicates, the ready table and the details.
* Rewritten in the same voice: `release_check.md`, the datasheet reader's `<part>.review.md`, the parity report, the
  eval `report.md`, the test pack's `CHECKLIST.md`, the upkeep notifications, `ecadlib doctor` (outcome,
  then what to fix, what is optional, what is fine), the Altium menu and KiCad plugin dialogs, the `init` questions and
  MCP errors. Workspace `librarian_run` rows carry `word` and `says`, `held_parts` carries `reasons`.
* Checked by `tests/test_messages.py` (every code has a sentence; a new release-check rule, footprint-check error,
  KiCad/Altium field or review note without one fails) and `tests/test_ui_voice.py` (snapshots in
  `tests/fixtures/voice/`, Markdown and HTML say the same, demo and test pack reports free of internal names and
  absolute paths). After a deliberate wording change: `UPDATE_VOICE=1 python -m tests.run_all test_ui_voice`.
