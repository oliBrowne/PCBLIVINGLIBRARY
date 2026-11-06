# Privacy

ecadlib runs on your own computer. It has no telemetry and no account. It sends data only to the services listed
below, and only when you use the feature that needs them. The one server this project runs receives automatic crash
reports, and only from people who switched them on (below).

| Where | What is sent | When | How to turn it off |
|---|---|---|---|
| Your AI provider (Anthropic by default; OpenAI, Gemini, OpenRouter, or a model on your own machine) | Datasheet pages and part numbers for parts no library has, and pictures of a generated footprint for the visual check. Your API key goes with each request. | Only when a part needs the datasheet reader or the visual check, and only to the provider you picked | Pick "No AI" on the setup page, or remove `datasheet` from `[sources] order` in `ecadlib.toml` |
| gitlab.com (KiCad's official libraries) | Requests for library index and footprint/symbol files | When looking parts up online | Run with `--offline`, or remove `kicad` from `[sources] order` |
| EasyEDA / LCSC | Part numbers | When looking parts up online | Remove `easyeda` from `[sources] order` |
| DigiKey, Mouser, Nexar, SnapMagic, SamacSys | Part numbers, with your key | Only if you set that service's key | Do not set the key |
| Altium 365 (Nexar) | The parts you push, with your token | Only when you push to Altium 365 | Do not set `NEXAR_TOKEN` |
| GitHub | Upkeep issues or pull requests | Only if you run `ecadlib upkeep --github` | Leave out `--github` |
| A webhook you configure | Short notifications | Only if you set `[notify]` | Leave `[notify]` empty |
| This project's crash-report endpoint (pcblivinglibrary.vercel.app), which files a public GitHub issue | A crash's error type (such as `KeyError`), the lines of ecadlib's own code it went through, where in ecadlib it happened, and the ecadlib, OS and Python versions. Never the error message, file names of yours, parts, keys or anything you typed | Only if you switched automatic crash reports on (asked once after a crash, or on the setup page); off by default | `ecadlib report-bug --auto off`, the setup page, or the environment variable `ECADLIB_NO_CRASH_REPORTS=1` (or `DO_NOT_TRACK=1`) |

Unless you switch automatic crash reports on, bug reports are never sent by ecadlib. When ecadlib hits an error it did not expect (a bug, not a wrong path or a bad
setting), it saves the error on your computer, in the `crashes` folder next to your stored keys (`%APPDATA%\ecadlib`
on Windows), keeping the newest 20. Before saving, it blanks out API keys, tokens, e-mail addresses, your user name, your computer's name,
your company's name in an Altium 365 address and your home folder. `ecadlib report-bug` (or **Report a bug** in the web UI) shows you that text and opens a GitHub issue
filled in with it, the ecadlib version, your OS and your Python version. Nothing leaves your computer until you choose
to open that link: the text then travels to GitHub inside the link so GitHub can show it to you, and it becomes a
public issue only when you press Submit. You can edit or delete any of it first. `ecadlib report-bug --clear`
deletes the saved errors.

**Automatic crash reports** are off until you say yes: ecadlib asks once, in the terminal, after the first bug it hits,
and the setup page has a switch. With them on, every bug ecadlib hits sends only the short report in the table above (at
most 10 a day for each user account on a computer; the same bug twice in a row goes once). The endpoint accepts only
those fields, and only values it knows: file and function names that exist in ecadlib's code, a command name, a Python
or ecadlib error type, version numbers. So free text cannot get through. It turns the report into a public GitHub
issue (one issue per distinct crash; a repeat raises its count and adds a released version or a system to its lists,
and a closed issue seen again on a newer release gets one comment), which anyone can read and help fix. Issues stay
public after you switch reports off. Vercel, which hosts the endpoint, sees your IP address when the report arrives, as
it does for any web request. To stop floods, the endpoint counts reports per address for one hour, under a one-way
hash of the address (never the address itself), and then forgets it; the address is not put in the issue or passed on.
`ecadlib report-bug --auto status` shows the setting.

API keys you add on the setup page or with `ecadlib keys set` are stored only on your computer: on Windows they are
encrypted for your Windows account, elsewhere they are in a file only you can read. They are never written to your
library, your project files or `ecadlib.toml`.

The Windows installer changes only its own folder, the Start menu (and, if you tick them, a desktop icon and your
PATH). Uninstalling removes those. Your workspace and stored keys (`%APPDATA%\ecadlib`) are kept until you delete
them.
