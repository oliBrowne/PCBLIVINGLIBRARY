"""One command to run the team server for a team, with HTTPS (Docker Compose: ecadlib + Caddy).

    python3 -m ecadlib team setup                     (from the ecadlib checkout; or `ecadlib team setup --dir CHECKOUT`)
    python3 -m ecadlib team setup --domain ecadlib.example.com --admin ada --name "Ada L" --email ada@example.com
    python3 -m ecadlib team setup --domain 192.168.1.20                     (LAN: Caddy's internal CA)
    python3 -m ecadlib team setup --domain ecadlib.example.com --own-cert ./certs   (ecadlib serves your cert, no Caddy)

It asks for whatever is not given, then:
  1. writes .env next to docker-compose.yml (mode 0600; domain, TLS mode, ports; no password, no keys),
  2. builds the image (or pulls --image),
  3. creates the first admin in the ecadlib-data volume, unless an admin already exists there,
  4. starts the stack with `docker compose up -d --wait`.
Running it again is safe: the .env keeps its values unless a flag changes them, an existing admin is left alone (no
password is asked), and compose only recreates what changed. The password is read without echo (or one line from
stdin with --password-stdin), handed to `ecadlib team add-user --password-stdin` on a pipe, and never written to a
file, put on a command line or printed. Standard library only, so it runs from a checkout without `pip install`."""
import argparse, getpass, ipaddress, os, re, shutil, subprocess, sys
from pathlib import Path

from . import team as TM

ENV_FILE = ".env"
BASE, OWN_CERT = "docker-compose.yml", "docker-compose.own-cert.yml"
WORKSPACE = "/data/workspace"                      # inside the ecadlib container (the ecadlib-data volume)
HOSTNAME = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$")
LOCAL_SUFFIXES = (".lan", ".local", ".internal", ".home.arpa", ".localhost", ".test", ".corp", ".intranet")
ENV_HEADER = ("# Written by `ecadlib team setup`. No secrets here: users and password hashes live in the ecadlib-data\n"
              "# volume; API keys go in api-keys.env. Edit and run `docker compose up -d` (or the setup again).\n")


class SetupError(Exception):
    pass


# ---------------------------------------------------------------- values
def clean_domain(v):
    """The host name or IP address people will type, without scheme, port or path."""
    d = str(v or "").strip().lower()
    d = re.sub(r"^https?://", "", d).split("/")[0]
    try:
        ipaddress.ip_address(d.strip("[]"))
        return d.strip("[]")
    except ValueError:
        pass
    if not HOSTNAME.match(d):
        raise SetupError(f"{v!r} is not a host name or IP address (e.g. ecadlib.example.com or 192.168.1.20)")
    return d


def is_local(domain):
    """True for names a public CA cannot certify: IP addresses, single labels and LAN-only suffixes."""
    try:
        ipaddress.ip_address(domain)
        return True
    except ValueError:
        return "." not in domain or domain == "localhost" or domain.endswith(LOCAL_SUFFIXES)


def _env_value(k, v):
    v = str(v)
    if not re.match(r"^[A-Za-z0-9._@+:/;,\-]*$", v):
        raise SetupError(f"{k}: {v!r} has characters .env cannot hold safely")
    return v


def read_env(path):
    out = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip().strip("'\"")
    return out


def write_env(path, values):
    """Write .env (0600). Returns True when the content changed; an unchanged file is left untouched."""
    order = ("ECADLIB_DOMAIN", "ECADLIB_TLS", "ECADLIB_HTTP_PORT", "ECADLIB_HTTPS_PORT", "ECADLIB_IMAGE", "COMPOSE_FILE",
             "ECADLIB_CERT_DIR")
    keys = [k for k in order if k in values] + sorted(k for k in values if k not in order)     # stable: re-runs match
    text = ENV_HEADER + "".join(f"{k}={_env_value(k, values[k])}\n" for k in keys if values[k] not in (None, ""))
    if path.exists() and path.read_text() == text:
        return False
    TM._write_private(path, text)
    return True


# ---------------------------------------------------------------- talking to the person
class Asker:
    def __init__(self, interactive):
        self.interactive = interactive

    def value(self, label, current=None, flag=None, required=True):
        if current:
            return current
        if not self.interactive:
            if required:
                raise SetupError(f"no {label}: give {flag}")
            return ""
        while True:
            try:
                v = input(f"{label}: ").strip()
            except EOFError:                            # stdin ran out (Windows calls NUL a terminal)
                raise SetupError(f"no {label}: give {flag}") from None
            if v or not required:
                return v

    def password(self, from_stdin):
        if from_stdin:
            pw = sys.stdin.readline().rstrip("\r\n")
        elif not self.interactive:
            raise SetupError("no password: run this in a terminal, or pipe one line to --password-stdin")
        else:
            pw = getpass.getpass(f"admin password (at least {TM.MIN_PASSWORD} characters, not shown): ")
            if pw != getpass.getpass("again: "):
                raise SetupError("the passwords differ")
        if len(pw) < TM.MIN_PASSWORD:
            raise SetupError(f"the password must be at least {TM.MIN_PASSWORD} characters")
        return pw


# ---------------------------------------------------------------- docker compose
class Compose:
    def __init__(self, directory, docker="docker"):
        self.dir, self.docker = Path(directory), docker

    def cmd(self, *args):
        return [self.docker, "compose", "--project-directory", str(self.dir), *args]

    def run(self, *args, input=None, capture=False):
        p = subprocess.run(self.cmd(*args), cwd=self.dir, input=input, text=True,
                           capture_output=capture, stdin=None if input is not None else subprocess.DEVNULL)
        if p.returncode:
            err = (p.stderr or "").strip()[-2000:] if capture else ""
            raise SetupError(f"`docker compose {' '.join(args[:3])} ...` failed (exit {p.returncode})" + (f":\n{err}" if err else ""))
        return p.stdout or ""

    def team(self, *args, input=None):
        """`ecadlib team --workspace /data/workspace ...` in a throwaway container on the data volume."""
        return self.run("run", "--rm", "--no-deps", "-T", "ecadlib", "team", "--workspace", WORKSPACE, *args,
                        input=input, capture=True)


def admins(compose):
    """User names with the admin role in the data volume (parses `ecadlib team list`)."""
    out = []
    for line in compose.team("list").splitlines():
        parts = line.split()
        if line.startswith("  ") and len(parts) >= 2 and parts[1] == "admin":
            out.append(parts[0])
    return out


# ---------------------------------------------------------------- main
def plan_env(a, env, ask):
    """The .env values from flags, the existing .env and answers. Pure apart from asking."""
    domain = clean_domain(a.domain or ask.value("Domain name or IP address people will use "
                                                "(e.g. ecadlib.example.com, or 192.168.1.20 on a LAN)",
                                                env.get("ECADLIB_DOMAIN"), "--domain"))
    new = {k: v for k, v in env.items() if k not in ("ECADLIB_DOMAIN", "ECADLIB_TLS", "COMPOSE_FILE", "ECADLIB_CERT_DIR")}
    new["ECADLIB_DOMAIN"] = domain
    own = a.own_cert or (env.get("ECADLIB_CERT_DIR") if a.tls == "auto" and env.get("COMPOSE_FILE") else None)
    tls = a.tls
    if tls == "auto":
        tls = "own-cert" if own else "internal" if is_local(domain) else "acme"
    if tls == "own-cert":
        if not own:
            raise SetupError("--tls own-cert needs --own-cert DIR (with cert.pem and key.pem)")
        cert_dir = Path(own) if Path(own).is_absolute() else (Path(a.dir) / own)
        for f in ("cert.pem", "key.pem"):
            if not (cert_dir / f).is_file():
                raise SetupError(f"{cert_dir / f} is missing (--own-cert DIR needs cert.pem and key.pem)")
        new["COMPOSE_FILE"] = os.pathsep.join((BASE, OWN_CERT))
        own = Path(own).as_posix()
        new["ECADLIB_CERT_DIR"] = own if Path(own).is_absolute() or own.startswith(".") else "./" + own   # not a volume name
        new["ECADLIB_TLS"] = "internal"               # unused without Caddy; keeps the file complete
    elif tls == "internal":
        new["ECADLIB_TLS"] = "internal"
    else:
        if is_local(domain):
            raise SetupError(f"{domain} cannot get a public certificate: use --tls internal (Caddy's own CA) or --own-cert")
        old = env.get("ECADLIB_TLS", "")
        email = a.acme_email or (old if "@" in old else "") or a.email
        email = ask.value("E-mail for Let's Encrypt (certificate expiry notices)", email, "--acme-email")
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            raise SetupError(f"{email!r} does not look like an e-mail address")
        new["ECADLIB_TLS"] = email
    for k, flag in (("ECADLIB_HTTP_PORT", a.http_port), ("ECADLIB_HTTPS_PORT", a.https_port), ("ECADLIB_IMAGE", a.image)):
        if flag:
            new[k] = str(flag)
    new.setdefault("ECADLIB_HTTP_PORT", "80")
    new.setdefault("ECADLIB_HTTPS_PORT", "443")
    return new, tls


def url_of(env):
    port = env.get("ECADLIB_HTTPS_PORT", "443")
    d = env["ECADLIB_DOMAIN"]
    host = f"[{d}]" if ":" in d else d
    return f"https://{host}" + ("" if port == "443" else f":{port}") + "/"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ecadlib team setup", description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__.split("\n\n", 1)[1])
    ap.add_argument("--dir", default=".", help="folder with docker-compose.yml (the ecadlib checkout); .env goes here")
    ap.add_argument("--domain", help="host name or IP address people will use")
    ap.add_argument("--tls", choices=("auto", "acme", "internal", "own-cert"), default="auto",
                    help="auto: Let's Encrypt for a public name, Caddy's internal CA for an IP or LAN name")
    ap.add_argument("--acme-email", help="e-mail for Let's Encrypt (default: the admin's)")
    ap.add_argument("--own-cert", metavar="DIR", help="ecadlib serves DIR/cert.pem + DIR/key.pem itself; no Caddy")
    ap.add_argument("--http-port", type=int, help="host port for HTTP (default 80; ACME needs 80)")
    ap.add_argument("--https-port", type=int, help="host port for HTTPS (default 443)")
    ap.add_argument("--image", help="use a published image (e.g. ghcr.io/olibrowne/pcblivinglibrary:0.3.0) instead of building")
    g = ap.add_argument_group("first admin (only used while the data volume has no admin)")
    g.add_argument("--admin", help="user name")
    g.add_argument("--name", help="full name")
    g.add_argument("--email", help="e-mail")
    g.add_argument("--password-stdin", action="store_true", help="read the password from one line of stdin")
    ap.add_argument("--no-start", action="store_true", help="write .env and create the admin, but do not start the stack")
    ap.add_argument("--docker", default="docker", help=argparse.SUPPRESS)        # tests use a stand-in
    a = ap.parse_args(argv)
    try:
        return _setup(a)
    except SetupError as e:
        print(f"ecadlib team setup: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nstopped", file=sys.stderr)
        return 130


def _setup(a):
    d = Path(a.dir).resolve()
    if not (d / BASE).is_file():
        raise SetupError(f"no {BASE} in {d}: run this from the ecadlib checkout, or pass --dir")
    if not shutil.which(a.docker):
        raise SetupError("docker is not installed or not on PATH (https://docs.docker.com/get-docker/)")
    ask = Asker(interactive=sys.stdin.isatty() and not a.password_stdin)
    env_path = d / ENV_FILE
    env, tls = plan_env(a, read_env(env_path), ask)
    print(("wrote " if write_env(env_path, env) else "unchanged: ") + str(env_path)
          + f"  (domain {env['ECADLIB_DOMAIN']}, TLS: " + {"acme": "Let's Encrypt", "internal": "Caddy's internal CA",
                                                           "own-cert": "your certificate, served by ecadlib"}[tls] + ")")
    compose = Compose(d, a.docker)
    if env.get("ECADLIB_IMAGE"):
        print(f"pulling {env['ECADLIB_IMAGE']} ...")
        compose.run("pull", "ecadlib")
    else:
        print("building the ecadlib image ...")
        compose.run("build", "ecadlib")
    have = admins(compose)
    if have:
        print(f"admin already set up ({', '.join(have)}): no password needed. Manage users on the Users page or with\n"
              f"  docker compose run --rm ecadlib team --workspace {WORKSPACE} add-user|reset-password|list ...")
    else:
        user = TM.check_username(ask.value("First admin's user name (e.g. ada)", a.admin, "--admin"))
        name = ask.value("Full name (recorded with approvals)", a.name, "--name", required=False)
        email = ask.value("E-mail", a.email or (env["ECADLIB_TLS"] if "@" in env.get("ECADLIB_TLS", "") else ""),
                          "--email", required=False)
        pw = ask.password(a.password_stdin)
        try:
            compose.team("add-user", user, "--role", "admin", "--name", name, "--email", email,
                               "--password-stdin", input=pw + "\n")
        finally:
            pw = None
        print(f"added {user} as admin (in the ecadlib-data volume)")
    if a.no_start:
        print("not started (--no-start): run `docker compose up -d` in " + str(d))
        return 0
    print("starting ...")
    if tls == "own-cert":
        compose.run("rm", "--stop", "--force", "caddy")      # left over from proxy mode (no-op when absent)
    compose.run("up", "-d", "--wait", "--wait-timeout", "180")
    print(f"\necadlib is running: {url_of(env)}")
    if tls == "internal":
        print("Browsers will warn until they trust Caddy's local CA. Export it once and install it on each computer\n"
              "(or accept the warning):  docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt ecadlib-ca.crt")
    elif tls == "acme":
        print(f"The certificate comes from Let's Encrypt: {env['ECADLIB_DOMAIN']} must resolve to this machine and ports "
              f"{env['ECADLIB_HTTP_PORT']}/{env['ECADLIB_HTTPS_PORT']} must be reachable. Logs: docker compose logs caddy")
    print("Back up: see docs/using-ecadlib.md, 'Run it for your team'. Upgrade: git pull, then run this setup again.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
