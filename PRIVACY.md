# Privacy

ecadlib runs on your own computer. It has no telemetry, no account, and no server run by this project. It sends data
only to the services listed below, and only when you use the feature that needs them.

| Where | What is sent | When | How to turn it off |
|---|---|---|---|
| Your AI provider (Anthropic by default; OpenAI, Gemini, OpenRouter, or a model on your own machine) | Datasheet pages and part numbers for parts no library has, and pictures of a generated footprint for the visual check. Your API key goes with each request. | Only when a part needs the datasheet reader or the visual check, and only to the provider you picked | Pick "No AI" on the setup page, or remove `datasheet` from `[sources] order` in `ecadlib.toml` |
| gitlab.com (KiCad's official libraries) | Requests for library index and footprint/symbol files | When looking parts up online | Run with `--offline`, or remove `kicad` from `[sources] order` |
| EasyEDA / LCSC | Part numbers | When looking parts up online | Remove `easyeda` from `[sources] order` |
| DigiKey, Mouser, Nexar, SnapMagic, SamacSys | Part numbers, with your key | Only if you set that service's key | Do not set the key |
| Altium 365 (Nexar) | The parts you push, with your token | Only when you push to Altium 365 | Do not set `NEXAR_TOKEN` |
| GitHub / GitLab | Library changes as pull requests, upkeep issues | Only if you turn on the library agent | Leave `[agent] enabled = false` (the default) |
| A webhook you configure | Short notifications | Only if you set `[notify]` | Leave `[notify]` empty |

API keys you add on the setup page or with `ecadlib keys set` are stored only on your computer: on Windows they are
encrypted for your Windows account, elsewhere they are in a file only you can read. They are never written to your
library, your project files or `ecadlib.toml`.

The Windows installer changes only its own folder, the Start menu (and, if you tick them, a desktop icon and your
PATH). Uninstalling removes those. Your workspace and stored keys (`%APPDATA%\ecadlib`) are kept until you delete
them.
