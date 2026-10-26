"""The setup page behind `ecadlib setup` and the desktop app's first run: no terminal, no ecadlib.toml editing.

    ecadlib setup [--workspace W] [--port 8765]     opens the web UI at its setup page

What the page does (each step also works on its own, later):
  1. AI model: pick a provider and a model, paste the API key. The key is stored for this user (ecadlib.userconfig:
     encrypted for the Windows account on Windows) and checked with a free request (the provider's model list); the
     workspace's ecadlib.toml gets [ai] provider / model, never the key. "No AI" leaves the datasheet reader out of
     [sources]: KiCad-library and standard parts need no key.
  2. Tools: which of Altium Designer / KiCad is installed (ecadlib.detect), and which one you make libraries for.
  3. Folders: the workspace (default Documents/ecadlib) and your existing library folders, if any.
  4. Add to Altium: writes the "ecadlib" menu script project (ecadlib.altium_menu) into <workspace>/altium-menu, set to
     run this ecadlib, and shows the one step left inside Altium (Install from file...). Add to KiCad: copies the
     "ecadlib: add part" PCB editor plugin into KiCad's plugin folder for each KiCad found, with this ecadlib's command
     next to it, so it works without PATH changes.
Saving writes <workspace>/ecadlib.toml through `ecadlib init`'s own code (rules_infer.propose: it also learns naming
conventions from the library folders given) and the answers to the user's settings.json.

The routes are served by the local web UI (ecadlib/ui/server.py), which checks Host and Origin first."""
import argparse, json, os, re, shutil, sys
from pathlib import Path

from . import __version__, detect, userconfig as UC

RESTART = None          # set by the desktop app (ecadlib.launcher): fn(new workspace) that restarts the server there

# (id, label, ecadlib.toml provider, key variable, models (first = default), where to get a key, note)
PROVIDERS = [
    ("anthropic", "Claude (Anthropic)", "anthropic", "ANTHROPIC_API_KEY",
     [("claude-opus-5-5", "most accurate (default)"), ("claude-sonnet-5-5", "balanced"), ("claude-haiku-5-5", "cheapest")],
     "https://console.anthropic.com/settings/keys", "Reads datasheet PDFs directly. The default and the best tested."),
    ("openai", "OpenAI", "openai", "OPENAI_API_KEY",
     [("gpt-5", "most accurate"), ("gpt-5-mini", "cheaper")],
     "https://platform.openai.com/api-keys", ""),
    ("gemini", "Google Gemini", "gemini", "GEMINI_API_KEY",
     [("gemini-2.5-pro", "most accurate"), ("gemini-2.5-flash", "cheaper")],
     "https://aistudio.google.com/apikey", "Gemini gets datasheet pages as pictures and text, not the PDF."),
    ("openrouter", "OpenRouter (many models, one key)", "openrouter", "OPENROUTER_API_KEY",
     [("anthropic/claude-sonnet-4.5", "type any OpenRouter model id")],
     "https://openrouter.ai/settings/keys", ""),
    ("ollama", "A model on this computer (Ollama)", "ollama", "",
     [("qwen2.5vl:7b", "type the model name you pulled")],
     "https://ollama.com/download", "Free and private; small local models get extra guide rails and are less accurate."),
    ("none", "No AI for now", "", "", [], "",
     "Parts from KiCad's library, EasyEDA/LCSC and ecadlib's standard packages still work. Add a model any time."),
]
EXTRA_KEYS = [("NEXAR_TOKEN", "Altium 365 (Nexar) token", "send parts to an Altium 365 workspace",
               "https://portal.nexar.com/applications")]
TOOLS = ("altium", "kicad", "both")


def _provider(pid):
    p = next((p for p in PROVIDERS if p[0] == pid), None)
    if p is None:
        raise ValueError(f"unknown AI provider {pid!r}")
    return p


def _ws_rules_raw(ws_root):
    from . import rules as RU
    f = Path(ws_root) / "ecadlib.toml"
    try:
        return RU.parse_text(f.read_text(encoding="utf-8"), f.name) if f.is_file() else {}
    except (OSError, RU.RulesError):
        return {}


def ws_libs(ws_root):
    """The library folders saved on the setup page: [setup] libs of <workspace>/ecadlib.toml (a relative one starts at
    the workspace, as the default --lib does). The desktop app and `ecadlib setup` start the web UI with them."""
    libs = (_ws_rules_raw(ws_root).get("setup") or {}).get("libs")
    out = []
    for x in libs if isinstance(libs, list) else []:
        if isinstance(x, str) and x.strip():
            p = Path(x.strip()).expanduser()
            out.append(str(p if p.is_absolute() else Path(ws_root).resolve() / p))
    return out


def current_ai(raw):
    """(provider id, model) the workspace's ecadlib.toml names; ("anthropic", default) when it names none."""
    ai = raw.get("ai") or {}
    prov = str(ai.get("provider") or "anthropic")
    model = str(ai.get("model") or (raw.get("datasheet") or {}).get("model") or "")
    order = (raw.get("sources") or {}).get("order")
    if isinstance(order, list) and "datasheet" not in order:
        return "none", ""
    pid = next((p[0] for p in PROVIDERS if p[2] == prov), "anthropic" if prov == "anthropic" else "openrouter")
    return pid, model or (_provider(pid)[4][0][0] if _provider(pid)[4] else "")


def _key_row(name, label, why, url, environ):
    src = UC.key_source(name, environ)
    return {"name": name, "label": label, "why": why, "url": url, "stored": src == "stored",
            "set": bool(src), "from_env": src == "environment"}


def state(ws_root, environ=None):
    """Everything the setup page shows. Never contains a key value."""
    environ = os.environ if environ is None else environ
    s = UC.load_settings()
    raw = _ws_rules_raw(ws_root)
    st = raw.get("setup") or {}
    pid, model = current_ai(raw)
    found = detect.summary()
    tool = st.get("tool") or s.get("tool") or ("both" if found["altium"] and found["kicad"] else "kicad"
                                                 if found["kicad"] and not found["altium"] else "altium" if found["altium"] else "both")
    keys = [_key_row(p[3], f"{p[1]} API key", "the datasheet reader and visual check", p[5], environ)
            for p in PROVIDERS if p[3]]
    keys += [_key_row(n, label, why, url, environ) for n, label, why, url in EXTRA_KEYS]
    return {"version": __version__, "workspace": str(Path(ws_root).resolve()),
            "default_workspace": str(UC.default_workspace()), "done": bool(s.get("setup_done")),
            "frozen": bool(getattr(sys, "frozen", False)), "platform": sys.platform,
            "tool": tool, "libs": [str(x) for x in (st.get("libs") or s.get("libs") or [])],
            "ai": {"provider": pid, "model": model},
            "providers": [{"id": p[0], "label": p[1], "key": p[3], "models": [{"id": m, "note": n} for m, n in p[4]],
                           "key_url": p[5], "note": p[6]} for p in PROVIDERS],
            "keys": keys, "detected": found, "installed": s.get("installed") or {},
            "can_restart": RESTART is not None, "config_dir": str(UC.config_dir())}


# ------------------------------------------------------------------ saving
def save(ws_root, b):
    """Apply the page's answers: {workspace, tool, libs, provider, model}. Returns {saved, workspace, restart, toml}.
    A different workspace than the running one is created and gets the ecadlib.toml; restart says the server must
    move there (the desktop app does it; `ecadlib ui` users restart it with --workspace)."""
    from . import rules_infer as RI, rules as RU
    tool = b.get("tool") or "both"
    if tool not in TOOLS:
        raise ValueError(f"tool must be one of {', '.join(TOOLS)}")
    libs = b.get("libs") or []
    if not isinstance(libs, list) or not all(isinstance(x, str) for x in libs) or len(libs) > 50:
        raise ValueError("libs must be a list of folders")
    libs = [str(Path(x.strip()).expanduser().resolve()) for x in libs if x.strip()]   # the folder checked below,
                                                                                       # not one under the workspace
    missing = [x for x in libs if not Path(x).is_dir()]
    if missing:
        raise ValueError(f"library folder not found: {missing[0]}")
    ws = b.get("workspace") or str(ws_root)
    if not isinstance(ws, str) or len(ws) > 500:
        raise ValueError("bad workspace folder")
    ws = Path(ws.strip()).expanduser()
    if not ws.is_absolute():
        raise ValueError("give the workspace as a full path, e.g. " + str(UC.default_workspace()))
    if ws.exists() and not ws.is_dir():
        raise ValueError(f"{ws} is a file, not a folder")
    p = _provider(b.get("provider") or "anthropic")
    model = str(b.get("model") or (p[4][0][0] if p[4] else "")).strip()
    from .llm import MODEL_RE
    if p[0] != "none" and not MODEL_RE.fullmatch(model):
        raise ValueError("pick or type a model name")
    cfg = UC.config_dir()                       # settings.json is written last: a save that cannot finish changes nothing
    try:
        cfg.mkdir(parents=True, exist_ok=True)
        ok = os.access(cfg, os.W_OK)
    except OSError:
        ok = False
    if not ok:
        raise PermissionError(f"ecadlib's settings folder {cfg} cannot be written (permission denied), so nothing was "
                              "changed; fix its permissions, or set ECADLIB_HOME to a folder you can write")
    ws.mkdir(parents=True, exist_ok=True)
    order = (_ws_rules_raw(ws).get("sources") or {}).get("order")   # the person's order: the page only sets the
    order = list(order if isinstance(order, list) else RU.DEFAULTS["sources"]["order"])   # datasheet reader
    if p[0] == "none":
        order = [x for x in order if x != "datasheet"]
    elif "datasheet" not in order:
        order.append("datasheet")
    extra = {"setup": {"tool": tool, "libs": libs, "workspace": "."}, "sources": {"order": order}}
    if p[0] != "none":
        extra["ai"] = {"provider": p[2], "model": model}
        old_ai = _ws_rules_raw(ws).get("ai") or {}
        extra["ai"].update({k: "" for k in ("base_url", "api_key_env") if old_ai.get(k)})   # a preset brings its own
    out = ws / "ecadlib.toml"
    prop = RI.propose([x for x in libs] or [], existing=out, extra=extra)
    if prop["errors"]:
        raise ValueError("; ".join(prop["errors"]))
    res = RI.save(prop["toml"], out, overwrite=True)
    UC.save_settings({"workspace": str(ws), "tool": tool, "libs": libs, "setup_done": True})
    moved = ws.resolve() != Path(ws_root).resolve()
    return {"saved": True, "workspace": str(ws.resolve()), "restart": moved, "can_restart": RESTART is not None,
            "toml": str(out), "backup": res.get("backup"), "learned": len(prop["settings"])}


def restart(ws):
    if RESTART is None:
        raise ValueError(f"restart ecadlib with: ecadlib ui --workspace \"{ws}\"")
    RESTART(str(ws))
    return {"restarting": True, "workspace": str(ws)}


# ------------------------------------------------------------------ keys
def store_key(b):
    name, value = b.get("name"), b.get("value")
    allowed = {p[3] for p in PROVIDERS if p[3]} | {k[0] for k in EXTRA_KEYS}
    if name not in allowed:
        raise ValueError(f"{name!r} is not a key this page stores; use `ecadlib keys set NAME` for others")
    if not isinstance(value, str):
        raise ValueError("no key given")
    UC.set_key(name, value)                     # read from the store when needed: never put into the environment
    return {"stored": name}


def forget_key(b):
    name = b.get("name")
    UC.check_name(name if isinstance(name, str) else "")
    gone = UC.remove_key(name)
    return {"removed": gone, "still_in_env": bool(os.environ.get(name))}


def check_key(b, get=None):
    """A free request that proves the key works: the provider's model list. -> {ok, message}. Sends the key only to
    that provider's own address. b["key"]: a key pasted but not saved yet (checked instead of the stored one)."""
    p = _provider(b.get("provider") or "anthropic")
    if p[0] == "none":
        return {"ok": True, "message": "no AI chosen: nothing to check"}
    from . import llm as LL
    base = "https://api.anthropic.com/v1" if p[2] == "anthropic" else LL.PRESETS[p[2]][0]
    pasted = b.get("key").strip() if isinstance(b.get("key"), str) else ""
    if len(pasted) > 1000 or any(c.isspace() for c in pasted):
        return {"ok": False, "message": f"that is not a {p[1]} key: copy it again from {p[5]}"}
    key = (pasted or UC.secret(p[3])) if p[3] else None
    if p[3] and not key:
        return {"ok": False, "message": f"no key yet: paste your {p[1]} key above"}
    if get is None:
        import requests
        get = requests.get
    headers = ({"x-api-key": key, "anthropic-version": "2023-06-01"} if p[2] == "anthropic"
               else {"Authorization": f"Bearer {key}"} if key else {})
    try:
        r = get(base.rstrip("/") + "/models", headers=headers, timeout=15)
    except Exception as e:
        where = "is Ollama running? Start it, then try again" if p[0] == "ollama" else "check the internet connection or proxy"
        return {"ok": False, "message": f"could not reach {base} ({type(e).__name__}): {where}"}
    code = getattr(r, "status_code", 0)
    if code in (401, 403):
        return {"ok": False, "message": f"{p[1]} refused the key ({code}): copy it again from {p[5]}"}
    if code != 200:
        return {"ok": False, "message": f"{p[1]} answered {code}; the key may still be fine, try again later"}
    model = str(b.get("model") or "")
    try:
        ids = {m.get("id") or m.get("name") for m in (r.json().get("data") or r.json().get("models") or [])}
    except Exception:
        ids = set()
    if model and ids and model not in ids and p[0] in ("ollama",):
        return {"ok": False, "message": f"Ollama has no model {model}: run `ollama pull {model}` first"}
    return {"ok": True, "message": (f"{p[1]} accepted the key" + (": press Save to keep it" if pasted else "")) if key
            else f"{p[1]} answered"}


# ------------------------------------------------------------------ add to Altium / KiCad
def _record_install(kind, value):
    with UC._WRITE:                             # Add to Altium and Add to KiCad at once: both are kept
        inst = dict(UC.load_settings().get("installed") or {})
        inst[kind] = value
        UC.save_settings({"installed": inst})


def install_altium_menu(ws_root):
    """Write the Altium menu script project into <workspace>/altium-menu, run by this ecadlib. -> {folder, project,
    steps}. Altium keeps its list of installed script projects to itself, so the last step is one click inside Altium."""
    from . import altium_menu as AM
    ws = Path(ws_root).resolve()
    out = ws / "altium-menu"
    cmd = UC.self_command()
    command = " ".join(f'"{c}"' if " " in c else c for c in cmd)
    # build() names this folder in the menu file on Windows, or leaves that file out when the folder can't go in one
    # (an accented user name): the menu bar below needs no menu file
    rep = AM.build(out, workspace=str(ws), library=str(ws / "altium-library"), command=command)
    ini = out / AM.INI
    _set_ini_command(ini, command)                           # an older build's .ini is kept: point it at this ecadlib
    project = out / AM.PROJECT
    _record_install("altium_menu", str(project))
    bar = {}
    if os.name == "nt":                     # the "ecadlib" menu straight into Altium's top menu bar (Altium closed)
        try:
            bar = AM.install_menu_bar(out)
        except (OSError, ValueError) as e:
            bar = {"error": str(e)}
    res = {"folder": str(out), "project": str(project), "menu_file": str(out / AM.PREFS) if rep.get("menu_file") else None,
           "problems": rep.get("problems") or [], "menu_bar": bar}
    if bar.get("folders") and not bar.get("error"):
        res["steps"] = ["Start Altium Designer and open a schematic or PCB",
                        "The ecadlib menu is in the menu bar, before Window: Add part from MPN, Check this project BOM, "
                        "Open review queue",
                        f"To take it out again: ecadlib altium-menu install-menu --out \"{out}\" --remove"]
    else:
        why = f" ({bar['error']})" if bar.get("error") else ""
        res["steps"] = [f"Close Altium Designer{why}",
                        f"Run: ecadlib altium-menu install-menu --out \"{out}\" (or press Add to Altium here again)",
                        "Start Altium: the ecadlib menu is in the menu bar, before Window."
                        + (f" Without closing Altium: Preferences > Load... with {out / AM.PREFS} (see "
                           "ALTIUM_MENU_README.txt)" if res["menu_file"] else "")]
    return res


def _set_ini_command(ini, command):
    try:
        lines = ini.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    out, done = [], False
    for ln in lines:
        if ln.strip().lower().startswith("command") and "=" in ln and not done:
            out.append(f"command={command}")
            done = True
        else:
            out.append(ln)
    if done:
        ini.write_text("\n".join(out) + "\n", encoding="utf-8")


def install_kicad_plugin(series=None):
    """Copy the action plugin into KiCad's per-user plugin folder (every KiCad found, or one series). -> {installed:
    [folder], steps}."""
    if series is not None and not (isinstance(series, str) and re.fullmatch(r"\d{1,3}\.\d{1,3}", series)):
        raise ValueError("series must be a KiCad version series such as 8.0")    # it names a folder: no ../ paths
    kicads = [k for k in detect.kicad() if k["series"]]
    if series:
        kicads = [k for k in kicads if k["series"] == series] or [{"series": series}]
    if not kicads:
        raise ValueError("no KiCad found on this computer; install KiCad 7 or newer, then press this again")
    from .kicad_menu import icon_png
    src = Path(__file__).with_name("assets") / "kicad_action_plugin.py"
    done = []
    for k in kicads:
        d = detect.kicad_plugin_dir(k["series"]) / "ecadlib_add_part"
        d.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, d / "__init__.py")
        (d / "ecadlib.png").write_bytes(icon_png(24))
        (d / "ecadlib_cmd.json").write_text(json.dumps({"command": UC.self_command(), "ecadlib": __version__}, indent=2) + "\n",
                                            encoding="utf-8")
        done.append(str(d))
    _record_install("kicad_plugin", done)
    return {"installed": done,
            "steps": ["Restart KiCad (or in the PCB editor: Tools > External Plugins > Refresh Plugins)",
                      "Open a saved board, then press the ecadlib button in the top toolbar (also under Tools > "
                      "External Plugins)"]}


# ------------------------------------------------------------------ web routes
# Keys and installs are per person, on their own computer. The web UI checks Host and Origin before any of them runs.
GET_ROUTES = {"/api/setup": lambda ws, b: state(ws)}
POST_ROUTES = {"/api/setup": save, "/api/setup/key": lambda ws, b: store_key(b),
               "/api/setup/key/forget": lambda ws, b: forget_key(b), "/api/setup/key/check": lambda ws, b: check_key(b),
               "/api/setup/altium": lambda ws, b: install_altium_menu(ws),
               "/api/setup/kicad": lambda ws, b: install_kicad_plugin(b.get("series")),
               "/api/setup/restart": lambda ws, b: restart(b.get("workspace") or ws)}
WRITES = {"/api/setup", "/api/setup/altium"}             # these write into the workspace: under the server's write lock


# ------------------------------------------------------------------ ecadlib setup
def main(argv=None):
    ap = argparse.ArgumentParser(prog="ecadlib setup", description="open the setup page: AI model and key, Altium / "
                                 "KiCad, folders, add ecadlib to Altium and KiCad (no ecadlib.toml editing)")
    ap.add_argument("--workspace", help="workspace folder (default: the one chosen before, else Documents/ecadlib)")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args(argv)
    from .ui import server
    ws = a.workspace or UC.load_settings().get("workspace") or str(UC.default_workspace())
    Path(ws).mkdir(parents=True, exist_ok=True)
    return server.main(["--workspace", ws, "--port", str(a.port), "--setup"] + (["--no-browser"] if a.no_browser else [])
                       + [x for lib in ws_libs(ws) for x in ("--lib", lib)])


if __name__ == "__main__":
    sys.exit(main())
