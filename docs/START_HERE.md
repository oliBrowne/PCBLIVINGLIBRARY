# Start here

ecadlib builds the parts your BOM is missing (schematic symbol, footprint, 3D model) and puts them in your Altium or
KiCad library. It is free and open source. If you would rather not use a terminal, this page is all you need.

## 1. Install (Windows)

1. Download **ecadlib-setup-<version>.exe** from the
   [Releases page](https://github.com/oliBrowne/PCBLIVINGLIBRARY/releases).
2. Run it. It installs for your Windows account only, so it does not ask for an administrator password.
   If Windows shows "Windows protected your PC" (builds made before code signing was set up), click
   **More info**, then **Run anyway**.
3. At the end, leave **Start ecadlib now** ticked.

ecadlib comes with everything it needs (its own Python), so there is nothing else to install. It shows up in the
Start menu as **ecadlib** and **ecadlib setup**.

On macOS or Linux, or if you like the command line: `pip install ecadlib`, then `ecadlib start`.

## 2. Set it up (the setup page)

The first start opens the setup page in your web browser. It has four steps:

1. **AI model.** Pick a provider: Claude (the default), OpenAI, Gemini, OpenRouter, a model on your own computer
   (Ollama), or no AI for now. Paste your API key and press **Check key**. You pay the provider directly. The key is
   stored only on this computer, encrypted with your Windows account. Without AI, parts still come from KiCad's
   library, EasyEDA/LCSC and ecadlib's built-in standard packages; only parts that need a datasheet read are held.
2. **Your tools.** ecadlib shows which of Altium Designer and KiCad it found. Pick the one you make libraries for,
   or both.
3. **Folders.** The workspace, where ecadlib keeps your BOMs and the libraries it builds (default:
   `Documents\ecadlib`). If you already have library folders, paste their paths, one per line. ecadlib reuses those
   parts and learns your naming from them.
4. **Add ecadlib to Altium and KiCad** (optional). This adds an "add part from MPN" command inside your tool.
   - **Altium:** press **Add to Altium**, then do one step in Altium: Preferences > Scripting System > Global Projects
     > Install from file..., and pick the file the page shows.
   - **KiCad:** press **Add to KiCad** and restart KiCad. The command is in the PCB editor under Tools > External
     Plugins > ecadlib: add part.

Press **Save setup**. You can come back to this page any time (Start menu > **ecadlib setup**).

## 3. Your first BOM

Open ecadlib (the **Open ecadlib** button, or the Start menu). Drop your BOM on the first card. CSV or Excel works,
as Altium or KiCad export it. Then:

1. **Check the BOM.** Every line gets a status: already in your library, found in a public library, or missing.
2. **Fill the library.** ecadlib finds or builds every missing part and checks each one before release. Parts that
   fail a check are **held**, with the reason in plain words and what to do next.
3. **Send to your tool.** For Altium, use a database library (DbLib), `.SchLib`/`.PcbLib` files, or an Altium 365
   workspace. For KiCad, use `.kicad_sym`/`.pretty` files.

## Trying ecadlib for us? Send two numbers

After your first BOM, run `ecadlib status` in a terminal (or `ecadlib status --json`) and send us the **last run**
line and the **review** line:

- **last run:** how long the BOM took to become a released library, step by step (triage, build, release check,
  your review, the Altium 365 push), and how much of that was ecadlib's own work.
- **review:** how many parts went through as ecadlib built them, how many were held (and the top reasons), and how
  many you approved, rejected or changed.

## While it runs

A small "ecadlib is running" window stays open. It has buttons for Open, Setup, Workspace folder and Quit. Closing
the window stops ecadlib. Nothing runs in the background after that.

## If something is wrong

- **The browser page does not open:** go to <http://127.0.0.1:8765/> yourself.
- **"API key refused":** copy the key again from your provider's page. Then on the setup page, press **Save key** and
  **Check key**.
- **The setup check:** the setup page shows what ecadlib found. In a terminal, `ecadlib doctor` lists every check,
  with a fix for each one.
- **Uninstall:** Windows Settings > Apps > ecadlib. Your workspace and your stored keys (`%APPDATA%\ecadlib`) are kept.
  Delete those folders yourself if you want them gone.
- Anything else: [open an issue](https://github.com/oliBrowne/PCBLIVINGLIBRARY/issues) and include the message you
  saw.
