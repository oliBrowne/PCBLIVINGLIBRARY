"""`ecadlib ai`: which model, provider and guide rails the AI features use.

    ecadlib ai show [--model M] [--section datasheet|proof] [--detect] [--rules FILE] [--json]
    ecadlib ai rails                      every rail: layer (support / capability / guidance) and what it does

show prints the endpoint (provider, base_url, key variable: set or not, never the value), the capability profile
and where it came from, and the rails a read would run, by layer. --detect asks an openai-compatible server about
the model (GET /models, Ollama /api/show); without it nothing is sent anywhere."""
import argparse, json, os, sys

from . import AIConfigError, endpoint, key_for, NO_KEY, profiles as PR, rails as R


def show(rules, model=None, section="datasheet", detect=False):
    ep = endpoint(rules, section, model)
    if detect and ep["provider"] == "openai-compatible":
        prof = PR.resolve(ep, rules, key_for(ep))
    else:
        prof = PR.profile(ep["model"], ep["provider"], rules)
    plan = R.plan(prof, ep["assist"], ep["feedback_retry"])
    k = key_for(ep)
    key = "none needed" if k == NO_KEY else (f"{ep['key_env'] or 'ANTHROPIC_API_KEY'} set" if k else f"{ep['key_env']} NOT set")
    return {"endpoint": {**ep, "key": key}, "profile": prof, "plan": plan, "layers": R.layers(plan)}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ecadlib ai", description="the model, provider and guide rails the AI features use",
                                 epilog="examples:\n  ecadlib ai show\n  ecadlib ai show --model qwen2.5:7b --detect\n"
                                        "  ecadlib ai rails", formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("show", help="endpoint, profile and rails for the configured (or a given) model")
    s.add_argument("--model", help="a model id (or endpoint:model); default: [ai] / [datasheet] model")
    s.add_argument("--section", choices=["datasheet", "proof"], default="datasheet")
    s.add_argument("--detect", action="store_true", help="ask an openai-compatible server what the model can do")
    s.add_argument("--rules", help="rules file (default: ecadlib.toml found from here)")
    s.add_argument("--json", action="store_true")
    sub.add_parser("rails", help="list every rail and its layer")
    a = ap.parse_args(argv)
    if a.cmd == "rails":
        for layer in ("support", "capability", "guidance"):
            print(f"{layer}:")
            for k, (lv, what) in R.RAILS.items():
                if lv == layer:
                    tiers = [t for t, rs in R.TIER_RAILS.items() if k in rs]
                    print(f"  {k:<17} {what}" + (f"  [tiers: {', '.join(tiers)}]" if tiers else ""))
        return 0
    from .. import rules as RU
    rules = RU.load(a.rules)
    try:
        rep = show(rules, a.model, a.section, a.detect)
    except AIConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(rep, indent=2, default=str))
        return 0
    ep, p = rep["endpoint"], rep["profile"]
    print(f"model     {ep['model']}  via {ep['name']} ({'Anthropic API' if ep['provider'] == 'anthropic' else ep['base_url']})")
    print(f"key       {ep['key']}")
    print(f"profile   {PR.describe(p)}")
    if p.get("detect"):
        print(f"detected  {p['detect']}")
    print(f"assist    {ep['assist']} -> {rep['plan']['tier']} tier")
    for layer, names in rep["layers"].items():
        print(f"{layer:<11}{', '.join(names) or '-'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
