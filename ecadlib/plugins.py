"""Plugins: third-party part sources and exporters, installed as ordinary Python packages and found through entry points.

    ecadlib plugins list                              built-in and installed providers and exporters (and load errors)
    ecadlib plugins export NAME PLAN.json -o DIR      run an exporter plugin on a librarian plan

Part sources (entry point group `ecadlib.providers`): the object is a class like the built-in ones in
ecadlib/providers.py:

    class MyDistributor(ecadlib.providers.Provider):
        name = "mydist"                  # what [providers] order and --providers call it
        auth = "API key in MYDIST_KEY"   # shown in reports and the UI
        env = ("MYDIST_KEY",)            # configured() is True when these are set
        def lookup(self, line):          # line: the BOM line dict (mpn, manufacturer, lcsc, value, footprint, ...)
            r = self.http.get(...)       # self.http: ecadlib's cached, rate-limited HTTP client
            return SourceResult(self.name, found=True, mpn=..., has_symbol=True, has_footprint=True, url=...)

raise NotConfigured when credentials are missing; any other exception from a plugin becomes a note on that BOM line
(a broken plugin never stops a triage). Triage asks the providers in order and stops at the first one that has a
footprint.

Exporters (entry point group `ecadlib.exporters`): the object is a callable, or an object with an `export` method,
called as export(items, out_dir, rules) -> list of written paths. items: the plan items (plan.json), out_dir: a Path
that exists, rules: the loaded library rules. Optional attributes: name, description.

[providers] order = ["mydist", "jlcpcb_search", "easyeda"] chooses and orders the sources for triage, the librarian,
the web UI / MCP workspace and the library agent; [] (the default) keeps each command's own default. An explicit
--providers flag still wins. See examples/ecadlib_example_plugin/ for a complete, installable example."""
import argparse, json, sys
from pathlib import Path

from . import providers as P

GROUPS = ("ecadlib.providers", "ecadlib.exporters")
_cache = {}


def _entry_points(group):
    from importlib.metadata import entry_points
    try:
        return list(entry_points(group=group))
    except TypeError:                                   # very old importlib.metadata: a dict of groups
        return list(entry_points().get(group, []))


def load(group, eps=None):
    """{name: object} for one group, and [(name, error)] for plugins that failed to load. eps: entry points (tests)."""
    if eps is None and group in _cache:
        return _cache[group]
    found, errors = {}, []
    for ep in (_entry_points(group) if eps is None else eps):
        try:
            obj = ep.load()
        except Exception as e:                          # a broken plugin is reported, never fatal
            errors.append((ep.name, f"{type(e).__name__}: {e}"))
            continue
        found[ep.name] = obj
    res = (found, errors)
    if eps is None:
        _cache[group] = res
    return res


def reset():
    _cache.clear()


def provider_classes(eps=None):
    """{name: class}: the built-in providers, then plugins (a plugin cannot replace a built-in name). -> (dict, errors)"""
    out = {c.name: c for c in P.ALL}
    plugins, errors = load("ecadlib.providers", eps)
    errors = list(errors)
    for ep_name, cls in plugins.items():
        name = getattr(cls, "name", None) or ep_name
        if not isinstance(cls, type) or not callable(getattr(cls, "lookup", None)):
            errors.append((ep_name, "not a provider class (needs name, lookup(line))"))
            continue
        if name in out:
            errors.append((ep_name, f"provider name {name!r} is taken by a built-in or another plugin"))
            continue
        out[name] = _guarded(cls, name)
    return out, errors


def _guarded(cls, name):
    """A subclass of plugin class cls whose construction and configured() never raise: a plugin that fails to start
    (missing config file, bad key ...) answers every lookup with an error note on the line instead of stopping triage,
    and reports itself as not configured. lookup() errors are turned into notes by bom_triage (_plugin = True)."""
    def __init__(self, *a, **k):
        self._plugin_error = None
        try:
            cls.__init__(self, *a, **k)
        except Exception as e:
            self._plugin_error = f"{type(e).__name__}: {str(e)[:120]}"
            self.http = a[0] if a else k.get("http")

    def lookup(self, line):
        if self._plugin_error:
            raise RuntimeError(f"plugin failed to start: {self._plugin_error}")
        return cls.lookup(self, line)

    def configured(self):
        if self._plugin_error:
            return False
        try:
            return bool(cls.configured(self)) if callable(getattr(cls, "configured", None)) else True
        except Exception:
            return False
    return type(cls.__name__, (cls,), {"name": name, "_plugin": True, "auth": str(getattr(cls, "auth", "") or ""),
                                       "__module__": cls.__module__, "__init__": __init__, "lookup": lookup,
                                       "configured": configured})


def select(names=None, rules=None, default=None, eps=None):
    """Provider classes in order. names: an explicit choice (list or comma text, e.g. --providers) wins; else the
    rules' [providers] order when not empty; else default (names; None = every built-in). Unknown names raise
    ValueError listing what is available; a plugin named in [providers] order that fails to load is skipped with a
    warning on stderr (an explicit --providers choice of it raises)."""
    if isinstance(names, str):
        names = [n.strip() for n in names.split(",") if n.strip()]
    order = ((rules or {}).get("providers") or {}).get("order") or []
    want = list(names) if names else list(order) if order else (
        [n.strip() for n in default.split(",") if n.strip()] if isinstance(default, str) else
        list(default) if default is not None else [c.name for c in P.ALL])
    avail, errors = provider_classes(eps)
    if not names and order:                             # a plugin in the rules' order that fails to load is skipped
        broken = {n: e for n, e in errors if n in want and n not in avail}      # (with a warning): triage goes on
        for n, e in broken.items():
            print(f"ecadlib: [providers] order: plugin {n} skipped, it failed to load: {e}", file=sys.stderr)
        want = [n for n in want if n not in broken]
    bad = [n for n in want if n not in avail]
    if bad:
        why = "".join(f"; plugin {n} failed to load: {e}" for n, e in errors if n in bad)
        raise ValueError(f"unknown provider {bad[0]!r}; available: {', '.join(avail)}{why}")
    if "jlcpcb_search" in want and "easyeda" in want and want.index("jlcpcb_search") > want.index("easyeda"):
        want.remove("jlcpcb_search")                    # it finds the LCSC number EasyEDA is keyed by: always before it
        want.insert(want.index("easyeda"), "jlcpcb_search")
    seen, out = set(), []
    for n in want:
        if n not in seen:
            seen.add(n)
            out.append(avail[n])
    if not P.unofficial_allowed(rules):                 # [providers] unofficial = false: undocumented endpoints are out,
        dropped = [c.name for c in out if not getattr(c, "official", True)]      # even when named
        if dropped:
            print(f"ecadlib: [providers] unofficial = false: skipping {', '.join(dropped)}", file=sys.stderr)
        out = [c for c in out if getattr(c, "official", True)]
    return out


def order_set(rules):
    return bool(((rules or {}).get("providers") or {}).get("order"))


def exporters(eps=None):
    """{name: callable(items, out_dir, rules)} and load errors."""
    found, errors = load("ecadlib.exporters", eps)
    out, errors = {}, list(errors)
    for name, obj in found.items():
        try:
            target = obj() if isinstance(obj, type) else obj          # a class: one instance with an export method
        except Exception as e:
            errors.append((name, f"{type(e).__name__}: {e}"))
            continue
        fn = getattr(target, "export", None) or (target if callable(target) else None)
        if not callable(fn):
            errors.append((name, "not an exporter (needs export(items, out_dir, rules))"))
            continue
        desc = getattr(target, "description", "") or (fn.__doc__ or "").strip().split("\n")[0]
        out[getattr(target, "name", None) or name] = (fn, desc)
    return out, errors


def run_exporter(name, plan, out_dir, rules=None, eps=None):
    """Run exporter `name` on a plan (path or list of items). -> list of written paths (as text)."""
    from . import rules as RU
    ex, errors = exporters(eps)
    if name not in ex:
        why = "".join(f"; {n} failed to load: {e}" for n, e in errors if n == name)
        raise ValueError(f"unknown exporter {name!r}; installed: {', '.join(ex) or 'none'}{why}")
    items = json.loads(Path(plan).read_text(encoding="utf-8")) if not isinstance(plan, list) else plan
    if not isinstance(items, list):
        raise ValueError(f"{plan}: not a plan (a JSON list of parts)")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rules = RU.resolve(rules, Path(plan).parent if not isinstance(plan, list) else out_dir)
    fn = ex[name][0]
    if isinstance(plan, list):
        return _call(name, fn, items, out_dir, rules)
    # change control (#105): the plugin gets the released revisions (a hand edit since the last writer is held and the
    # released files and fields put back), and reads the part files under the library lock; a part waiting for
    # `ecadlib part approve` with no released revision is left out
    from . import change_gate as CG, release_check as RC
    with CG.publishing(Path(plan).parent):
        items = CG.settled_plan(plan, f"plugin:{name}", rules=rules)
        items = [it for it in items if not RC.change_hold(it, Path(plan).parent)]
        for it in items:                       # the release check again, on the files as they are: `ready` is not enough
            why = RC.publish_problems(it, Path(plan).parent, rules) if it.get("status", "ready") == "ready" else []
            if why:
                it["status"], it["hold_reasons"] = "held", why
        return _call(name, fn, items, out_dir, rules)


def _call(name, fn, items, out_dir, rules):
    try:
        paths = fn(items, out_dir, rules) or []
        return [str(p) for p in paths]
    except Exception as e:                     # the plugin's bug, not ecadlib's: name it (never fatal, never "report it")
        raise PluginError(f"exporter {name} (plugin {getattr(fn, '__module__', '?')}) failed: {type(e).__name__}: {e}; "
                          "this is a problem in that plugin, tell its author") from e


class PluginError(ValueError):
    """An installed plugin raised: reported as that plugin's failure."""


def describe(eps_providers=None, eps_exporters=None):
    provs, perr = provider_classes(eps_providers)
    ex, eerr = exporters(eps_exporters)
    return {"providers": [{"name": n, "plugin": bool(getattr(c, "_plugin", False)), "auth": getattr(c, "auth", ""),
                           "module": c.__module__} for n, c in provs.items()],
            "exporters": [{"name": n, "description": d} for n, (_, d) in ex.items()],
            "errors": [{"plugin": n, "error": e} for n, e in perr + eerr]}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ecadlib plugins", description="installed part-source and exporter plugins")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list", help="providers and exporters, built-in and installed")
    ls.add_argument("--json", action="store_true")
    ex = sub.add_parser("export", help="run an exporter plugin on a librarian plan")
    ex.add_argument("name")
    ex.add_argument("plan")
    ex.add_argument("-o", "--out", required=True, help="output folder")
    ex.add_argument("--rules", help="ecadlib.toml (default: found from the plan's folder)")
    a = ap.parse_args(argv)
    if a.cmd == "list":
        d = describe()
        if a.json:
            print(json.dumps(d, indent=2))
            return 0
        print("providers (triage part sources; [providers] order picks and orders them):")
        for p in d["providers"]:
            print(f"  {p['name']:<16} {'plugin ' + p['module'] if p['plugin'] else 'built-in':<34} {p['auth'][:60]}")
        print("exporters (ecadlib plugins export NAME PLAN -o DIR):")
        for e in d["exporters"] or [{"name": "(none installed)", "description": ""}]:
            print(f"  {e['name']:<16} {e['description']}")
        for e in d["errors"]:
            print(f"  plugin {e['plugin']} failed to load: {e['error']}", file=sys.stderr)
        return 0
    try:
        paths = run_exporter(a.name, a.plan, a.out, a.rules)
    except (ValueError, OSError) as e:
        print(f"ecadlib plugins export: {e}", file=sys.stderr)
        return 2
    for p in paths:
        print(p)
    print(f"{a.name}: {len(paths)} file(s) -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
