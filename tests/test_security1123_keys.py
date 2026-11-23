"""Security fixes of the 2026-11-23 review, group S-C (keys and tokens): 2.2 a settings file in the current folder
could send a stored key to any server; 2.8 the Altium 365 download resent the sign-in token to whatever host the URL
named, and the headless browser followed redirects unchecked; the smaller redaction items. Every key here is made up;
nothing is sent (requests are stubbed or go to a server of the test's own on 127.0.0.1)."""
import io, json, os, sys, time
from pathlib import Path

from ecadlib import ai_cost as AC, keyhosts as KH, llm as LL, rules as RU, userconfig as UC
from tests.fixtures_lite import lite

STORED = "sk-ant-STORED-SECRET-0123456789"
HOSTILE = ('[ai]\nprovider = "openai-compatible"\nbase_url = "https://collector.attacker.example/v1"\n'
           'model = "claude-opus-5-5"\napi_key_env = "ANTHROPIC_API_KEY"\n')


def _rules(tmp_path, text):
    (tmp_path / "ecadlib.toml").write_text(text, encoding="utf-8")
    return RU.load(tmp_path / "ecadlib.toml")


# ------------------------------------------------------------------ 2.2 a stored key goes only to its own service
@lite
def test_a_folder_settings_file_cannot_send_the_stored_claude_key_to_another_server(tmp_path, monkeypatch):
    """The review's repro (a2/key_to_any_server.py): the key stored for Claude went out as a Bearer header."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    UC.set_key("ANTHROPIC_API_KEY", STORED)
    ep = LL.endpoint(_rules(tmp_path, HOSTILE))
    assert ep["base_url"] == "https://collector.attacker.example/v1" and ep["key_env"] == "ANTHROPIC_API_KEY"
    assert LL.key_for(ep) is None                              # the run stops here: no key, nothing sent
    msg = LL.needs_key_message(ep)
    assert "collector.attacker.example" in msg and "ANTHROPIC_API_KEY" in msg and "Nothing was sent" in msg, msg
    assert "ecadlib keys allow ANTHROPIC_API_KEY collector.attacker.example" in msg, msg
    assert STORED not in msg


@lite
def test_a_key_from_the_environment_is_held_to_the_same_rule(tmp_path, monkeypatch):
    ep = LL.endpoint(_rules(tmp_path, HOSTILE.replace("ANTHROPIC_API_KEY", "OPENAI_API_KEY")))
    assert LL.key_for(ep, environ={"OPENAI_API_KEY": "sk-proj-ENVKEY-000000000000"}) is None
    lan = LL.endpoint(_rules(tmp_path, HOSTILE.replace("https://collector.attacker.example", "http://192.168.1.66")))
    assert lan["local"] and LL.key_for(lan, environ={"ANTHROPIC_API_KEY": STORED}) is None    # not NO_KEY either


@lite
def test_the_presets_and_claude_work_as_before(tmp_path, monkeypatch):
    env = {"ANTHROPIC_API_KEY": "k-claude", "OPENAI_API_KEY": "k-openai", "OPENROUTER_API_KEY": "k-or",
           "GEMINI_API_KEY": "k-gem", "MY_CLAUDE_KEY": "k-mine"}
    assert LL.key_for(LL.endpoint(None), environ=env) == "k-claude"
    for preset, want in (("openai", "k-openai"), ("openrouter", "k-or"), ("gemini", "k-gem")):
        ep = LL.endpoint({"ai": {"provider": preset, "model": "m"}})
        assert LL.key_for(ep, environ=env) == want, preset
    # a Claude key under a name of your own ([datasheet] api_key_env), and a key you named for a known service
    assert LL.key_for(LL.endpoint({"datasheet": {"api_key_env": "MY_CLAUDE_KEY"}}), environ=env) == "k-mine"
    assert LL.key_for(LL.endpoint({"ai": {"provider": "openai", "model": "m", "api_key_env": "MY_CLAUDE_KEY"}}),
                      environ=env) == "k-mine"
    # a local server: no key, no question; one on this computer started with a key of your own takes it
    assert LL.key_for(LL.endpoint({"ai": {"provider": "ollama", "model": "qwen2.5:7b"}}), environ=env) == LL.NO_KEY
    ep = LL.endpoint({"ai": {"provider": "openai-compatible", "base_url": "http://127.0.0.1:1234/v1", "model": "m",
                             "api_key_env": "LMSTUDIO_KEY"}})
    assert LL.key_for(ep, environ={"LMSTUDIO_KEY": "k-lm"}) == "k-lm"
    assert LL.key_for(ep, environ={}) == LL.NO_KEY


@lite
def test_another_services_key_is_never_sent_to_claude_or_a_preset(tmp_path, monkeypatch):
    env = {"ANTHROPIC_API_KEY": "k-claude", "OPENAI_API_KEY": "k-openai", "GITLAB_TOKEN": "glpat-x", "MOUSER_API_KEY": "m"}
    for name in ("OPENAI_API_KEY", "GITLAB_TOKEN", "MOUSER_API_KEY"):
        ep = LL.endpoint({"datasheet": {"api_key_env": name}})
        assert ep["provider"] == "anthropic" and LL.key_for(ep, environ=env) == "k-claude", name    # Claude's own key
        ep = LL.endpoint({"ai": {"provider": "openrouter", "model": "m", "api_key_env": name}})
        assert LL.key_for(ep, environ=env) is None and "openrouter.ai" in LL.needs_key_message(ep), name
    from ecadlib import datasheet as D
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k-claude")
    monkeypatch.setenv("OPENAI_API_KEY", "k-openai")
    monkeypatch.setenv("ECADLIB_NO_STORED_KEYS", "1")
    assert D.require_key(None, {"datasheet": {"api_key_env": "OPENAI_API_KEY"}}) == "k-claude"
    assert D.require_key(None, {"ai": {"api_key_env": "OPENAI_API_KEY"}}) == "k-claude"


@lite
def test_a_host_you_allowed_once_gets_the_key_and_only_that_key(tmp_path, monkeypatch):
    rules = _rules(tmp_path, HOSTILE.replace("collector.attacker.example", "llm.corp.example").replace(
        "ANTHROPIC_API_KEY", "CORP_LLM_KEY"))
    ep, env = LL.endpoint(rules), {"CORP_LLM_KEY": "k-corp", "ANTHROPIC_API_KEY": STORED}
    assert LL.key_for(ep, environ=env) is None                 # a name of your own, a server ecadlib does not know
    assert "you have not allowed CORP_LLM_KEY" in LL.needs_key_message(ep)
    assert KH.allow("CORP_LLM_KEY", "https://LLM.corp.example:8443/v1") == ["llm.corp.example"]
    assert LL.key_for(ep, environ=env) == "k-corp"
    assert json.loads((UC.config_dir() / "settings.json").read_text())["key_hosts"] == {"CORP_LLM_KEY": ["llm.corp.example"]}
    other = LL.endpoint(dict(rules, ai=dict(rules["ai"], api_key_env="ANTHROPIC_API_KEY")))
    assert LL.key_for(other, environ=env) is None              # the host is allowed for that key, not for every key
    assert KH.forget("CORP_LLM_KEY", "llm.corp.example") and LL.key_for(ep, environ=env) is None
    # CI and scripts: the environment says so (a rules file cannot set it)
    assert LL.key_for(ep, environ=dict(env, ECADLIB_KEY_HOSTS="X=y.example, CORP_LLM_KEY=llm.corp.example")) == "k-corp"
    assert LL.key_for(ep, environ=dict(env, ECADLIB_KEY_HOSTS="CORP_LLM_KEY=corp.example")) is None   # the whole host


@lite
def test_a_rules_file_cannot_allow_a_host_itself(tmp_path, monkeypatch):
    for extra in ('\n[key_hosts]\nANTHROPIC_API_KEY = ["collector.attacker.example"]\n',
                  '\n[setup]\nkey_hosts = "x"\n'):
        try:
            rules = _rules(tmp_path, HOSTILE + extra)
        except RU.RulesError:
            continue                                           # not a known key: the file is refused as a whole
        assert LL.key_for(LL.endpoint(rules), environ={"ANTHROPIC_API_KEY": STORED}) is None


@lite
def test_keys_allow_asks_on_a_terminal_and_refuses_without_one(tmp_path, monkeypatch):
    said = []
    asked = []

    def yes(q):
        asked.append(q)
        return "y"
    assert UC.main(["allow", "CORP_LLM_KEY", "llm.corp.example"], out=said.append, confirm=lambda q: "n") == 1
    assert KH.allowed_hosts("CORP_LLM_KEY", {}) == []
    assert UC.main(["allow", "CORP_LLM_KEY", "llm.corp.example"], out=said.append, confirm=yes) == 0
    assert "llm.corp.example" in asked[0] and "CORP_LLM_KEY" in asked[0]
    assert KH.allowed_hosts("CORP_LLM_KEY", {}) == ["llm.corp.example"]
    monkeypatch.setattr(sys, "stdin", io.StringIO("y\n"))      # no terminal (a script, an assistant's shell): not a yes
    err = io.StringIO()
    monkeypatch.setattr(sys, "stderr", err)
    assert UC.main(["allow", "OPENAI_API_KEY", "collector.attacker.example"], out=said.append) == 2
    assert KH.allowed_hosts("OPENAI_API_KEY", {}) == [] and "ECADLIB_KEY_HOSTS" in err.getvalue()
    said.clear()
    assert UC.main(["list"], out=said.append) == 0
    assert any("CORP_LLM_KEY" in s and "llm.corp.example" in s for s in said), said
    assert UC.main(["allow", "CORP_LLM_KEY", "llm.corp.example", "--remove"], out=said.append) == 0
    assert KH.allowed_hosts("CORP_LLM_KEY", {}) == []


def test_the_cost_dialog_names_the_server_the_key_and_the_pages_go_to():
    est = {"rows": [{"part": "P1", "input_tokens": 1000, "model": "claude-opus-5-5"}], "usd": 0.2, "usd_max": 0.5,
           "model": "claude-opus-5-5"}
    q = AC.quote(est, None)
    assert q["host"] == "api.anthropic.com" and "(claude-opus-5-5 at api.anthropic.com)" in q["text"], q["text"]
    rules = {"ai": {"provider": "openai-compatible", "base_url": "https://collector.attacker.example/v1",
                    "model": "claude-opus-5-5", "api_key_env": "ANTHROPIC_API_KEY"}}
    q = AC.quote(est, rules)
    assert "collector.attacker.example" in q["host"] and "at collector.attacker.example" in q["text"], q["text"]
    q = AC.quote(est, rules, key_set=False)            # the key is stored, but not for this server: say that, not "no key"
    assert "ANTHROPIC_API_KEY" in q["text"] and "Nothing was sent" in q["text"] and "No API key" not in q["text"], q["text"]
    assert "No API key is stored" in AC.quote(est, None, key_set=False)["text"]
    q = AC.quote(dict(est, model="qwen2.5:7b"), {"ai": {"provider": "ollama", "model": "qwen2.5:7b"}})
    assert "localhost" in q["text"] and "this computer" in q["text"], q["text"]
    q = AC.request_quote("style-apply", "claude-opus-5-5", 0.01, 0.02, rules)
    assert "at collector.attacker.example" in q["text"], q["text"]
    q = AC.style_quote("s", "u", {}, "claude-opus-5-5", None)
    assert "at api.anthropic.com" in q["text"], q["text"]


# ------------------------------------------------------------------ 2.2 second route: a *_env setting naming another key
@lite
def test_the_gitlab_token_setting_cannot_name_another_services_key(tmp_path, monkeypatch):
    from ecadlib import forge
    remote = "https://gitlab.attacker.example/group/lib.git"
    cfg = {"git_host": "gitlab", "gitlab_api": "https://gitlab.attacker.example/api/v4",
           "gitlab_token_env": "ANTHROPIC_API_KEY"}
    kind, cl, why = forge.client(cfg, remote, {"ANTHROPIC_API_KEY": STORED})
    assert kind == "gitlab" and cl is None and "gitlab.attacker.example" in why and STORED not in why, why
    # your GitLab token goes to gitlab.com; to a GitLab of your own once you allowed it (or CI names its own server)
    kind, cl, why = forge.client({"git_host": "gitlab", "gitlab_token_env": "GITLAB_TOKEN"},
                                  "https://gitlab.com/group/lib.git", {"GITLAB_TOKEN": "glpat-x"})
    assert cl is not None and why is None
    own = dict(cfg, gitlab_token_env="GITLAB_TOKEN")
    assert forge.client(own, remote, {"GITLAB_TOKEN": "glpat-x"})[1] is None
    assert forge.client(own, remote, {"GITLAB_TOKEN": "glpat-x", "CI_SERVER_HOST": "gitlab.attacker.example",
                                      "GITLAB_CI": "true"})[1] is not None
    assert forge.client(own, remote, {"GITLAB_TOKEN": "glpat-x",
                                      "ECADLIB_KEY_HOSTS": "GITLAB_TOKEN=gitlab.attacker.example"})[1] is not None


@lite
def test_a_notify_target_cannot_send_another_services_key_as_its_token(tmp_path, monkeypatch):
    from ecadlib import notify as N
    sent = []

    def sender(url, body, headers, timeout):
        sent.append((url, dict(headers)))
        return 200
    rules = {"notify": {"targets": [{"name": "erp", "type": "json", "url_env": "ERP_HOOK_URL",
                                     "token_env": "ANTHROPIC_API_KEY", "events": ["*"]}]}}
    env = {"ERP_HOOK_URL": "https://erp.attacker.example/hook", "ANTHROPIC_API_KEY": STORED, "ERP_HOOK_TOKEN": "t-erp"}
    ev = [N.event("upkeep_changes", "t", "x", ["a"])]
    out = N.send(rules, ev, env=env, sender=sender)
    assert not sent and "ANTHROPIC_API_KEY" in json.dumps(out) and STORED not in json.dumps(out), out
    assert "erp.attacker.example" in out[0]["result"], out
    rules["notify"]["targets"][0]["token_env"] = "ERP_HOOK_TOKEN"            # a token of your own, as documented
    out = N.send(rules, ev, env=env, sender=sender)
    assert sent and sent[0][1].get("Authorization") == "Bearer t-erp", (sent, out)


# ------------------------------------------------------------------ 2.8 the Altium 365 sign-in token
def test_the_a365_download_sends_the_token_only_to_altiums_own_servers():
    """The review's repro (a7/a74_a365_token.py): any host that answered 401 got "Authorization: Bearer <token>"."""
    from ecadlib import altium365 as A

    class Resp:
        content = b"x"

        def __init__(self, code):
            self.status_code = code

        def raise_for_status(self):
            pass

    class Sess:
        def __init__(self):
            self.calls = []

        def get(self, url, headers=None, timeout=None):
            self.calls.append((url, dict(headers or {})))
            return Resp(401 if not headers else 200)
    for url in ("http://attacker.example/steal", "https://storage.other-cloud.example/blob?sig=abc",
                "http://169.254.169.254/latest/meta-data/", "http://files.nexar.com/x", "https://nexar.com.attacker.example/x",
                "https://notnexar.com/x", "file:///etc/passwd"):
        s = Sess()
        try:
            A.Nexar("SECRET-NEXAR-TOKEN", session=s).download(url)
            raise AssertionError(f"{url}: downloaded")
        except A.NexarError as e:
            assert "SECRET-NEXAR-TOKEN" not in str(e) and "not sent" in str(e), str(e)
        assert len(s.calls) == 1 and not s.calls[0][1], (url, s.calls)       # the plain try only: no token anywhere
    for url in ("https://files.nexar.com/File/1", "https://acme.365.altium.com/dl/1", "https://api.nexar.com/x"):
        s = Sess()
        assert A.Nexar("SECRET-NEXAR-TOKEN", session=s).download(url) == b"x"
        assert s.calls[1] == (url, {"Authorization": "Bearer SECRET-NEXAR-TOKEN"}), s.calls
    s = Sess()                                                       # a pre-signed address needs no token: as before
    s.get = lambda url, headers=None, timeout=None: (s.calls.append((url, headers)), Resp(200))[1]
    assert A.Nexar("SECRET-NEXAR-TOKEN", session=s).download("https://storage.other-cloud.example/blob?sig=abc") == b"x"
    assert s.calls == [("https://storage.other-cloud.example/blob?sig=abc", None)]


def test_requests_drops_the_token_when_a_redirect_changes_host():
    """What download() relies on for the redirect half: the token never follows a redirect to another host."""
    import requests
    s = requests.Session()
    assert s.should_strip_auth("https://files.nexar.com/a", "https://attacker.example/b")
    assert s.should_strip_auth("https://files.nexar.com/a", "http://files.nexar.com/a")
    assert not s.should_strip_auth("https://files.nexar.com/a", "https://files.nexar.com/b")


# ------------------------------------------------------------------ 2.8 the headless browser's redirect hops
def test_the_headless_browser_checks_every_redirect_hop():
    """A site answers 302 to another address (this machine, a router, cloud metadata): Playwright's route handler is
    not called for the hop, so the browser used to make the request. Run with a real browser when there is one."""
    import http.server, threading
    from ecadlib import datasheet as D, pdf_fetch as PF
    if not PF.playwright_available():
        print("  (playwright not installed: skipped)")
        return
    hits = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append((self.server.server_address[1], self.path))
            if self.path.startswith("/go"):
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{inner}/secret")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = {"/page": b"<img src='/go-img'><script>fetch('/go-fetch').catch(()=>0)</script>",
                    # second review (I3): a popup, and a frame of another site (its own process where the browser
                    # isolates sites) whose image and own navigation redirect
                    "/popup": f"<script>window.open('http://localhost:{outer}/go-popup')</script>ok".encode(),
                    "/frames": f"<iframe src='http://127.0.0.2:{outer}/frame'></iframe>ok".encode(),
                    "/frame": b"<img src='/go-frame-img'><script>setTimeout(function(){location.href='/go-frame-nav'}, 300)</script>",
                    }.get(self.path, b"ok")
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    site, target = (http.server.ThreadingHTTPServer((host, 0), H) for host in ("0.0.0.0", "127.0.0.1"))
    outer, inner = site.server_address[1], target.server_address[1]
    for srv in (site, target):
        threading.Thread(target=srv.serve_forever, daemon=True).start()
    real = D.unsafe_download
    # the test's stand-in for "a public site redirects to a local address": localhost is the site, 127.0.0.1 is refused
    D.unsafe_download = lambda url, public=False, resolve=True: "refused for the test" if "//127.0.0.1" in url else None
    b = PF.Browser(timeout=20, settle=0.3, challenge_s=3)
    try:
        if b._worker.call(lambda: (b._start(b._s), b._s.why)[1], 120):
            print(f"  (no browser: {b._s.why}: skipped)")
            return
        r = b.get(f"http://localhost:{outer}/go/ds.pdf")              # the link itself redirects
        assert r[1] is None and r[4], r
        r = b.get(f"http://localhost:{outer}/page")                   # an image and a fetch of the page redirect
        assert r[0] == 200 and b"go-img" in r[1], r
        assert (outer, "/go/ds.pdf") in hits and (outer, "/go-img") in hits and (outer, "/go-fetch") in hits, hits
        assert not [h for h in hits if h[0] == inner], hits           # no hop reached the refused address
        for path in ("/popup", "/frames"):
            r = b.get(f"http://localhost:{outer}{path}")
            assert r[0] == 200 and b"ok" in r[1], r
            time.sleep(1.5)                                           # what the page started keeps running a moment
        assert (outer, "/frame") in hits and (outer, "/go-frame-img") in hits, hits   # the frame did load and ask
        assert not [h for h in hits if h[0] == inner], hits
        assert not [h for h in hits if h[1] == "/go-popup"], hits     # a page nobody asked for requests nothing
        assert b.get(f"http://localhost:{outer}/plain")[0] == 200
        assert len(b._s.ctx.pages) == 0, b._s.ctx.pages               # and is not left open past the next call
    finally:
        D.unsafe_download = real
        b.close()
        site.shutdown()
        target.shutdown()


# ------------------------------------------------------------------ smaller: what the saved crash report keeps
@lite
def test_the_saved_crash_report_blanks_webhooks_other_folders_and_named_short_keys(tmp_path, monkeypatch):
    """The review's repros (a9/old_r3_scrub.py, c2_crash_client.py)."""
    from ecadlib import bugreport as B
    # (put together here: written out, the made-up address looks like a real one to a secret scanner)
    hook = "https://hooks.slack.com/" + "/".join(["services", "T00000000", "B11111111", "abcdEFGHijklMNOPqrstUVWX"])
    got = B.scrub(f"POST {hook} failed")
    assert "T00000000" not in got and "abcdEFGH" not in got and "hooks.slack.com" in got, got
    monkeypatch.setenv("TEAM_WEBHOOK", "https://chat.corp.example/in/9f3a77")      # a hook named as one: by its value
    assert "9f3a77" not in B.scrub("POST https://chat.corp.example/in/9f3a77 failed")
    for url in ("https://acme.webhook.office.com/webhookb2/aaaa-bbbb/IncomingWebhook/cccc/dddd",
                "https://discord.com/api/webhooks/123456789/AbCdEf-ghij", "https://erp.corp.example/hooks/9f3a/xyz"):
        got = B.scrub(f"sent to {url}: 500")
        assert url.rsplit("/", 1)[1] not in got and "[secret]" in got, got
    got = B.scrub(r"FileExistsError: D:\Projects\AcmeCorp\SecretBoard-X1\bom\BOM_rev3.csv")
    assert "AcmeCorp" not in got and "SecretBoard" not in got and "BOM_rev3" not in got and got.endswith(".csv"), got
    got = B.scrub("/srv/projects/acme/secret-board/plan.json: bad")
    assert "acme" not in got and "secret-board" not in got and got.endswith("plan.json: bad"), got
    home = str(Path.home())
    got = B.scrub(f"could not read {home}/Projects/AcmeCorp/RadarBoard-X1/BOM_rev3.csv line 7")
    assert "AcmeCorp" not in got and "RadarBoard" not in got and "~" in got and "line 7" in got, got
    got = B.scrub(r"also \\fileserver\eng\AcmeCorp\lib and more")
    assert "AcmeCorp" not in got and "fileserver" not in got and "and more" in got, got
    assert "Zx81Lm20PqRs" not in B.scrub("key 'DK_CLIENT' value Zx81Lm20PqRs")
    assert "Zx81Lm20PqRs" not in B.scrub("mouser key Zx81Lm20PqRs; next")
    # what a report needs to stay useful is kept: ecadlib's own files and routes, part numbers, plain words
    keep = ('File "ecadlib/librarian.py", line 12, in run', "web UI: /api/status", "KeyError: 'STM32H743ZIT6'",
            "POST https://api.anthropic.com/v1/messages: 529", "the key is not set", "3 / 4 parts and/or pins",
            "token request failed: HTTP 401")
    for text in keep:
        assert B.scrub(text) == text, (text, B.scrub(text))
    ws = B.scrub(f"{home}/Documents/ecadlib/librarian/plan.json")
    assert ws == "~/Documents/ecadlib/librarian/plan.json", ws


@lite
def test_the_key_check_hides_the_key_in_an_error_it_did_not_expect(tmp_path, monkeypatch):
    from ecadlib import lookup_keys as LK
    UC.set_key("MOUSER_API_KEY", "Zx81Lm20PqRs")

    class Http:
        def request(self, method, url, **kw):
            raise RuntimeError(f"cannot open https://api.mouser.com/api/v1/search/partnumber?apiKey=Zx81Lm20PqRs&v=1 "
                               "(apiKey=another-Key-99)")
        get = post = lambda self, url, *a, **kw: self.request("X", url, **kw)
    r = LK.check("mouser", http=Http())
    assert not r["ok"] and "Zx81Lm20PqRs" not in r["message"] and "another-Key-99" not in r["message"], r
    assert "RuntimeError" in r["message"] and "api.mouser.com" in r["message"], r
    said = []
    assert LK.main(["mouser"], out=said.append, http=Http()) == 1 and "Zx81Lm20PqRs" not in "\n".join(said), said


# ------------------------------------------------------------------ smaller: NEXAR_TOKEN_URL from the environment
def test_a_nexar_address_from_the_environment_must_be_https_on_nexars_own_host():
    said = []
    tok = "https://identity.nexar.com/connect/token"

    def url(value, **kw):
        return KH.service_url("NEXAR_TOKEN_URL", tok, {"NEXAR_TOKEN_URL": value, **kw.pop("env", {})}, warn=said.append, **kw)
    for bad in ("http://identity.nexar.com/connect/token", "https://attacker.example/connect/token",
                "http://attacker.example/t", "https://identity.nexar.com.attacker.example/t", "ftp://identity.nexar.com/t",
                "http://127.0.0.1:9/t"):
        said.clear()
        assert url(bad, same_host=True) == tok, bad
        assert said and "NEXAR_TOKEN_URL" in said[0] and "/t" not in said[0].replace("https://", ""), said
    assert url("https://identity.nexar.com/connect/token2", same_host=True).endswith("token2")
    assert url("http://127.0.0.1:9/t", same_host=True, env={"ECADLIB_TEST_ENDPOINTS": "1"}) == "http://127.0.0.1:9/t"
    # the API addresses (the token goes there): https anywhere the person names, or a test server on this computer
    assert url("https://gateway.corp.example/graphql") == "https://gateway.corp.example/graphql"
    assert url("http://127.0.0.1:8123/graphql") == "http://127.0.0.1:8123/graphql"
    assert url("http://gateway.corp.example/graphql") == tok
    # as the part search and the Altium 365 client read them (at import: a fresh Python with the variables set)
    import subprocess
    env = dict(os.environ, NEXAR_TOKEN_URL="http://attacker.example/t", NEXAR_API_URL="http://attacker.example/g",
               NEXAR_FILES_URL="ftp://attacker.example/f")
    env.pop("ECADLIB_TEST_ENDPOINTS", None)
    r = subprocess.run([sys.executable, "-c", "from ecadlib import providers as P, altium365 as A\n"
                                              "print(P.Nexar.TOKEN_URL, P.Nexar.API_URL, A.API_URL, A.FILES_URL)"],
                       env=env, capture_output=True, text=True, cwd=str(Path(__file__).resolve().parent.parent))
    assert r.stdout.split() == [tok, "https://api.nexar.com/graphql", "https://api.nexar.com/graphql",
                                "https://files.nexar.com"], (r.stdout, r.stderr)
    assert "NEXAR_TOKEN_URL" in r.stderr and "attacker.example/t" not in r.stderr, r.stderr


# ------------------------------------------------------------------ second review: the host the client really uses
TRICKS = ("https://collector.attacker.example\\@api.anthropic.com/v1", "https://collector.attacker.example\\@api.openai.com/v1",
          "https://collector.attacker.example\\.api.anthropic.com/v1", "https://user@api.anthropic.com/v1",
          "https://api.anthropic.com\t.attacker.example/v1", "https://api.anthropic.com\x00.attacker.example/v1",
          "https://api.anthropic.com\\.attacker.example/v1", "https://attacker.example /@api.anthropic.com",
          "https://attacker.example‮@api.anthropic.com/v1")


@lite
def test_a_backslash_or_userinfo_cannot_make_one_host_read_as_another(tmp_path, monkeypatch):
    """Second review (C1): urlsplit read "https://evil\\@api.anthropic.com" as api.anthropic.com, the HTTP client
    sent it to evil. An address that two parsers could read differently has no host at all: no key goes to it."""
    import requests
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    UC.set_key("ANTHROPIC_API_KEY", STORED)
    for url in TRICKS:
        assert KH.host_of(url) == "", (url, KH.host_of(url))
        for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "MY_OWN_KEY"):
            assert not KH.ok(name, url), (name, url)
    for url, host in (("https://api.anthropic.com/v1", "api.anthropic.com"), ("http://localhost:11434/v1", "localhost"),
                      ("http://[::1]:8000/v1", "::1"), ("API.OpenAI.com", "api.openai.com"), ("gateway.example:8443", "gateway.example")):
        assert KH.host_of(url) == host, (url, KH.host_of(url))
        if "://" in url:                                   # and it is the host the client connects to
            sent = requests.Request("POST", url.rstrip("/") + "/chat/completions").prepare().url
            assert KH.host_of(sent) == host, sent
    text = HOSTILE.replace('"https://collector.attacker.example/v1"', "'https://collector.attacker.example\\@api.anthropic.com/v1'")
    try:
        ep = LL.endpoint(_rules(tmp_path, text))
    except Exception as e:                                 # the settings file is refused outright
        assert "base_url" in str(e), str(e)
    else:
        assert LL.key_for(ep) is None and LL.key_refused(ep), ep


def test_the_a365_token_is_not_sent_to_an_address_two_parsers_read_differently():
    from ecadlib import altium365 as A
    for url in ("https://evil.example\\@files.nexar.com/f.SchLib", "https://evil.example\\.nexar.com/f", "https://u:p@files.nexar.com/f",
                "https://files.nexar.com\t.evil.example/f", "https://evil.example\\@acme.365.altium.com/f"):
        assert not A.token_host_ok(url), url
    assert A.token_host_ok("https://files.nexar.com/File/1") and A.token_host_ok("https://acme.365.altium.com/dl/1")
