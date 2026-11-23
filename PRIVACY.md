# Privacy

ecadlib runs on your own computer. It has no telemetry and no account. It sends data only to the services listed
below, and only when you use the feature that needs them. The one server this project runs receives automatic crash
reports, and only from people who switched them on (below).

| Where | What is sent | When | How to turn it off |
|---|---|---|---|
| Your AI provider (Anthropic by default; Claude through Amazon Bedrock, OpenAI, Gemini, OpenRouter, or a model on your own machine) | Datasheet pages and part numbers for parts no library has, and pictures of a generated footprint for the visual check. With a Claude key, the part number, maker and package of a part that has no datasheet go to Claude's web search (`[datasheet] web_search`, on by default; `web_search = false` turns it off). Your API key goes with each request (with Bedrock, the request is signed with your AWS credentials instead). With `--read-with-assistant` ecadlib sends nothing to any AI service; what you hand your own assistant follows that assistant's terms. | Only when a part needs the datasheet reader or the visual check, and only to the provider you picked | Pick "No AI" on the setup page, or remove `datasheet` from `[sources] order` in `ecadlib.toml` |
| gitlab.com (KiCad's official libraries) | Requests for library index and footprint/symbol files | When looking parts up online | Run with `--offline`, or remove `kicad` from `[sources] order` |
| EasyEDA / LCSC | Part numbers | When looking parts up online | Remove `easyeda` from `[sources] order` |
| The Internet Archive (archive.org) | A part's datasheet link, when it is a public web link | Only when none of the part's datasheet links gave a PDF (a dead link, or a site that refused the download). Never a link to your own network, an intranet name, or a link holding a user name, key or signature | Set `archive = false` under `[datasheet]` |
| DigiKey, Mouser, Nexar, SnapMagic, SamacSys | Part numbers, with your key | Only if you set that service's key | Do not set the key |
| Altium 365 (Nexar) | The parts you push, with your token | Only when you push to Altium 365 | Do not set `NEXAR_TOKEN` |
| The shared store of checked parts (GitHub, or the address in `[store] url`) | Your IP address and the files a lookup fetches: the store's description and withdrawn list, and the index file of the part number's slice (one of 256, picked by a hash of the part number: it narrows the part down to about 1/256 of all parts). Only when the store has the part, that part's record: that request does identify the exact part (its file name comes from the maker and part number, and the public index lists it). No BOM, no account. With `[store] mode = "snapshot"` only the whole store is downloaded, and the host learns nothing about which parts you look up | Only if you set `[store] url` (empty by default: no store is asked), when the librarian looks parts up, for `ecadlib store status`/`get` and the upkeep store check, and when a publishing command (`ecadlib kicad-export`, `altium365 push`, `dblib sync`, `export`, the KiCad / Altium plugins) sends a part that came from the store or was released by its agreement: it checks that part's record was not withdrawn (the same description, withdrawn list and index-slice requests). Store requests follow no redirect to another address (a snapshot download only GitHub's own, over https) | Leave `[store] url` empty, set `[store] accept = "off"`, run with `--offline`, or use `[store] mode = "snapshot"` to download the whole store and look parts up on your computer. To keep publishing commands from asking, build the parts that came from the store again without it (`ecadlib librarian BOM --refresh PART`); with the store switched off those parts are held until you release them yourself |
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

**Sharing parts with the shared store** happens only when you ask for it. `ecadlib store share` writes record files on
your computer and sends nothing; you offer them to the store yourself, in a pull request from your own GitHub account
(your GitHub name is public on it). A record holds the maker, part number, a description only when a part search, a
library or the datasheet gave it (never your BOM's own Description or Notes text), the public datasheet link, the
symbol and footprint, pins, pads, the checks passed, the ecadlib version and the AI model name. It never holds your BOM, project or file names, your name or e-mail, internal part numbers, stock or price
fields, a private datasheet link, a 3D model or a datasheet PDF. What a store accepts is checked by the same test
ecadlib runs before it writes the file: no e-mail address and no link that is not a public web link anywhere in a
record, its KiCad text included. `ecadlib store report` builds a GitHub issue link like `ecadlib report-bug`: nothing
is sent until you press Submit. See [docs/shared-store.md](docs/shared-store.md).

API keys you add on the setup page or with `ecadlib keys set` are stored only on your computer: on Windows they are
encrypted for your Windows account, elsewhere they are in a file only you can read. They are never written to your
library, your project files or `ecadlib.toml`.

The Windows installer changes only its own folder, the Start menu (and, if you tick them, a desktop icon and your
PATH). Uninstalling removes those. Your workspace and stored keys (`%APPDATA%\ecadlib`) are kept until you delete
them.
