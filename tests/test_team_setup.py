"""`ecadlib team setup` (ecadlib/team_setup.py) and the Docker files around it. Offline: a stand-in `docker`
script maps `docker compose run ... ecadlib team --workspace /data/workspace ...` onto a temp folder (the "volume")
and records every command line, so the real code path runs without Docker.
Run:  python3 -m tests.test_team_setup"""
import json, os, re, stat, subprocess, sys, tempfile
from pathlib import Path
from types import SimpleNamespace

from ecadlib import team as TM
from ecadlib import team_setup as TS

ROOT = Path(__file__).resolve().parent.parent
PW = "a-long-secret-pw-42"

FAKE_DOCKER = r'''#!{python}
import json, os, subprocess, sys
log, vol = os.environ["FAKE_DOCKER_LOG"], os.environ["FAKE_DOCKER_VOLUME"]
args = sys.argv[1:]
data = sys.stdin.read() if "run" in args else ""
with open(log, "a") as f:
    f.write(json.dumps({{"argv": args, "stdin_bytes": len(data)}}) + "\n")
if "run" in args and "team" in args:
    rest = args[args.index("team") + 1:]
    assert rest[:2] == ["--workspace", "/data/workspace"], rest
    p = subprocess.run([sys.executable, "-m", "ecadlib", "team", "--workspace", os.path.join(vol, "workspace")] + rest[2:],
                       input=data, text=True, capture_output=True)
    sys.stdout.write(p.stdout); sys.stderr.write(p.stderr)
    sys.exit(p.returncode)
'''


def _sandbox():
    t = Path(tempfile.mkdtemp(prefix="ecadlib-setup-"))
    (t / "app").mkdir()
    (t / "app" / "docker-compose.yml").write_text((ROOT / "docker-compose.yml").read_text())
    fake = t / "docker"
    fake.write_text(FAKE_DOCKER.format(python=sys.executable))
    fake.chmod(0o755)
    env = {**os.environ, "PYTHONPATH": str(ROOT), "FAKE_DOCKER_LOG": str(t / "docker.log"),
           "FAKE_DOCKER_VOLUME": str(t / "volume")}
    return t, fake, env


def _setup(t, fake, env, *args, stdin=None):
    return subprocess.run([sys.executable, "-m", "ecadlib", "team", "setup", "--dir", str(t / "app"), "--docker", str(fake),
                           *args], input=stdin, stdin=None if stdin is not None else subprocess.DEVNULL,
                          text=True, capture_output=True, env=env, cwd=str(t))


def _calls(t):
    f = t / "docker.log"
    return [json.loads(l) for l in f.read_text().splitlines()] if f.exists() else []


def test_domain_rules():
    assert TS.clean_domain("https://Ecadlib.Example.com/x") == "ecadlib.example.com"
    assert TS.clean_domain("192.168.1.20") == "192.168.1.20"
    for bad in ("", "ecad lib.com", "-x.com", "a..b", "x.com:443"):
        try:
            TS.clean_domain(bad)
            raise AssertionError(f"accepted {bad!r}")
        except TS.SetupError:
            pass
    assert TS.is_local("192.168.1.20") and TS.is_local("ecadlib") and TS.is_local("ecadlib.lan")
    assert TS.is_local("localhost") and not TS.is_local("ecadlib.example.com")
    assert TS.url_of({"ECADLIB_DOMAIN": "x.example.com", "ECADLIB_HTTPS_PORT": "443"}) == "https://x.example.com/"
    assert TS.url_of({"ECADLIB_DOMAIN": "10.0.0.5", "ECADLIB_HTTPS_PORT": "8443"}) == "https://10.0.0.5:8443/"


def _args(**kw):
    base = dict(domain=None, tls="auto", acme_email=None, own_cert=None, http_port=None, https_port=None, image=None,
                email=None, dir=".")
    return SimpleNamespace(**{**base, **kw})


def test_tls_mode_choice():
    ask = TS.Asker(interactive=False)
    env, tls = TS.plan_env(_args(domain="ecadlib.example.com", email="ops@example.com"), {}, ask)
    assert tls == "acme" and env["ECADLIB_TLS"] == "ops@example.com" and env["ECADLIB_HTTPS_PORT"] == "443"
    env, tls = TS.plan_env(_args(domain="192.168.1.20"), {}, ask)
    assert tls == "internal" and env["ECADLIB_TLS"] == "internal"
    try:                                                        # no public certificate for an IP address
        TS.plan_env(_args(domain="192.168.1.20", tls="acme", acme_email="a@b.co"), {}, ask)
        raise AssertionError("acme for an IP")
    except TS.SetupError:
        pass
    try:                                                        # public name, no e-mail, nobody to ask
        TS.plan_env(_args(domain="ecadlib.example.com"), {}, ask)
        raise AssertionError("acme without e-mail")
    except TS.SetupError as e:
        assert "--acme-email" in str(e)
    # existing .env values are kept; a flag overrides only what it names
    env, _ = TS.plan_env(_args(https_port=8443), {"ECADLIB_DOMAIN": "ecadlib.lan", "ECADLIB_TLS": "internal",
                                                   "NEXAR_EXTRA": "x"}, ask)
    assert env["ECADLIB_DOMAIN"] == "ecadlib.lan" and env["ECADLIB_HTTPS_PORT"] == "8443" and env["NEXAR_EXTRA"] == "x"


def test_own_cert_mode_sets_compose_files():
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "certs").mkdir()
        ask = TS.Asker(interactive=False)
        try:
            TS.plan_env(_args(domain="x.example.com", own_cert="certs", dir=d), {}, ask)
            raise AssertionError("missing cert accepted")
        except TS.SetupError as e:
            assert "cert.pem" in str(e)
        for f in ("cert.pem", "key.pem"):
            (Path(d) / "certs" / f).write_text("x")
        env, tls = TS.plan_env(_args(domain="x.example.com", own_cert="certs", dir=d), {}, ask)
        assert tls == "own-cert" and env["ECADLIB_CERT_DIR"] == "./certs"          # "certs" alone is a volume name
        assert env["COMPOSE_FILE"] == os.pathsep.join(("docker-compose.yml", "docker-compose.own-cert.yml"))
        env2, tls2 = TS.plan_env(_args(dir=d), env, ask)                             # a re-run stays in that mode
        assert tls2 == "own-cert" and env2 == env
        env3, tls3 = TS.plan_env(_args(dir=d, tls="internal"), env, ask)             # and can leave it
        assert tls3 == "internal" and "COMPOSE_FILE" not in env3 and "ECADLIB_CERT_DIR" not in env3


def test_env_file_refuses_unsafe_values():
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / ".env"
        for bad in ("a b", "x$y", "q'uote", "new\nline"):
            try:
                TS.write_env(p, {"ECADLIB_DOMAIN": bad})
                raise AssertionError(bad)
            except TS.SetupError:
                pass
        assert not p.exists()


def test_setup_end_to_end_with_stand_in_docker():
    t, fake, env = _sandbox()
    p = _setup(t, fake, env, "--domain", "ecadlib.example.com", "--admin", "Ada", "--name", "Ada Lovelace",
               "--email", "ada@example.com", "--password-stdin", stdin=PW + "\n")
    assert p.returncode == 0, p.stdout + p.stderr
    envf = t / "app" / ".env"
    assert stat.S_IMODE(envf.stat().st_mode) == 0o600
    vals = TS.read_env(envf)
    assert vals["ECADLIB_DOMAIN"] == "ecadlib.example.com" and vals["ECADLIB_TLS"] == "ada@example.com"   # ACME e-mail
    assert "https://ecadlib.example.com/" in p.stdout
    # the admin is in the "volume", with a working password hash
    u = TM.UserStore(t / "volume" / "workspace").get("ada")
    assert u and u["role"] == "admin" and u["name"] == "Ada Lovelace" and TM.verify_password(PW, u["password"])
    calls = _calls(t)
    verbs = [c["argv"][c["argv"].index("compose") + 3] for c in calls]          # after compose --project-directory D
    assert verbs == ["build", "run", "run", "up"], verbs
    add = next(c for c in calls if "add-user" in c["argv"])
    assert "--password-stdin" in add["argv"] and add["stdin_bytes"] == len(PW) + 1
    assert "-T" in add["argv"] and "--rm" in add["argv"]
    # the password is on no command line and in no file (only its scrypt hash is stored)
    assert all(PW not in " ".join(c["argv"]) for c in calls)
    for f in t.rglob("*"):
        if f.is_file():
            assert PW.encode() not in f.read_bytes(), f
    assert PW not in p.stdout + p.stderr

    # second run: nothing to type, no password asked, .env untouched, no second add-user
    before = (envf.read_text(), envf.stat().st_mtime_ns)
    p2 = _setup(t, fake, env)
    assert p2.returncode == 0, p2.stdout + p2.stderr
    assert "admin already set up (ada)" in p2.stdout and "unchanged" in p2.stdout
    assert (envf.read_text(), envf.stat().st_mtime_ns) == before
    assert sum("add-user" in c["argv"] for c in _calls(t)) == 1
    assert len(TM.UserStore(t / "volume" / "workspace").users()) == 1

    # changing one setting rewrites .env, keeps the rest
    n_before = len(_calls(t))
    p3 = _setup(t, fake, env, "--https-port", "8443", "--no-start")
    assert p3.returncode == 0 and "wrote" in p3.stdout and "not started" in p3.stdout
    vals = TS.read_env(envf)
    assert vals["ECADLIB_HTTPS_PORT"] == "8443" and vals["ECADLIB_DOMAIN"] == "ecadlib.example.com"
    assert not any("up" in c["argv"] for c in _calls(t)[n_before:])


def test_setup_refuses_without_answers_or_short_password():
    t, fake, env = _sandbox()
    p = _setup(t, fake, env)                                   # no terminal, no --domain
    assert p.returncode == 1 and "--domain" in p.stderr and not (t / "app" / ".env").exists()
    p = _setup(t, fake, env, "--domain", "10.0.0.5", "--admin", "ada", "--password-stdin", stdin="short\n")
    assert p.returncode == 1 and "at least" in p.stderr
    assert not (t / "volume" / "workspace" / "team_users.json").exists()
    p = _setup(t, fake, env, "--dir", str(t), "--domain", "10.0.0.5")     # no docker-compose.yml there
    assert p.returncode == 1 and "docker-compose.yml" in p.stderr


def test_docker_files_wire_the_proxy_trust_correctly():
    compose = (ROOT / "docker-compose.yml").read_text()
    caddy = (ROOT / "docker" / "Caddyfile").read_text()
    df = (ROOT / "Dockerfile").read_text()
    ecad = compose.split("\n  caddy:")[0]
    # ecadlib trusts exactly Caddy's fixed address and publishes no port of its own
    trusted = re.search(r"--trusted-proxy\n\s+- (\S+)", ecad).group(1)
    assert trusted == "${ECADLIB_PROXY_IP:-172.30.87.2}"
    assert f"ipv4_address: {trusted}" in compose.split("\n  caddy:")[1]
    assert "ports:" not in ecad and "ipv4_address" not in ecad and "ip_range:" in compose
    assert "172.30.87.2" not in "172.30.87.128/25"           # Caddy's address lies outside the dynamic range
    # the SSO user header is never passed through from clients
    assert "header_up -X-Forwarded-User" in caddy
    # image: non-root, health check, no secrets, ecadlib refuses plain HTTP unless compose says otherwise
    assert re.search(r"^USER ecadlib$", df, re.M) and "HEALTHCHECK" in df and "VOLUME" in df
    assert not re.search(r"(TOKEN|KEY|PASSWORD|SECRET)\s*=", df)
    assert "--allow-http" not in df.split("CMD", 1)[1]
    ignore = [l for l in (ROOT / ".dockerignore").read_text().splitlines() if l and not l.startswith("#")]
    assert ignore[0] == "*" and "!ecadlib" in ignore                 # allow-list: nothing else reaches the build
    for f in ("api-keys.env", ".env"):
        assert f in (ROOT / ".gitignore").read_text().split()


def test_image_publishing_only_on_tags():
    wf = (ROOT / ".github" / "workflows" / "docker.yml").read_text()
    push_job = wf.split("\n  publish:")[1]
    assert "startsWith(github.ref, 'refs/tags/v')" in push_job and "packages: write" in push_job
    assert "packages: write" not in wf.split("\n  publish:")[0]
    assert "push: true" not in wf.split("\n  publish:")[0]


if __name__ == "__main__":
    from tests.run_all import main
    sys.exit(main(["test_team_setup"]))
