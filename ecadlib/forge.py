"""Git hosting for the library agent and upkeep: GitHub pull requests or GitLab merge requests.

Which host: [agent] git_host = "auto" (default) | "github" | "gitlab". auto picks GitLab when the origin remote is on a
host named gitlab* or on the host of [agent] gitlab_api (self-hosted), else GitHub (today's behaviour).

GitLab (REST v4, standard library HTTP or a requests-like session):
    [agent]
    git_host = "gitlab"                              # or leave "auto"
    gitlab_api = "https://gitlab.example.com/api/v4" # self-hosted; default https://gitlab.com/api/v4
    gitlab_project = "group/sub/library"             # "" = from the origin remote; a numeric id works too
    gitlab_token_env = "GITLAB_TOKEN"                # NAME of the env var with a project/personal access token (api scope)

The token is sent as PRIVATE-TOKEN and never written anywhere. Merge requests are found by source branch, updated in
place (never duplicated), commented with notes and closed with state_event=close: the same steps the GitHub code takes
with pulls / issues comments / state=closed."""
import json, re
from urllib.parse import quote, urlsplit
from . import userconfig as UC

GITLAB_API = "https://gitlab.com/api/v4"


class GitLabError(Exception):
    status = None                                  # the HTTP status of a refused API call


# ------------------------------------------------------------------ which host
def _host_of(url):
    """Host name of a git remote or API URL: https://h/x, ssh://git@h:22/x, git@h:x/y."""
    url = (url or "").strip()
    if "://" in url:
        return (urlsplit(url).hostname or "").lower()
    m = re.match(r"^(?:[^@/]+@)?([^:/]+):", url)                 # scp-like git@host:group/proj.git
    return m.group(1).lower() if m else ""


def host_kind(cfg, remote):
    """'github' or 'gitlab' for the [agent] settings and the origin remote URL."""
    want = (cfg.get("git_host") or "auto").lower()
    if want in ("github", "gitlab"):
        return want
    h = _host_of(remote)
    if h and (h.startswith("gitlab") or ".gitlab." in h or h == _host_of(cfg.get("gitlab_api") or GITLAB_API)):
        return "gitlab"
    if not h and cfg.get("gitlab_project") and not cfg.get("github_repo"):
        return "gitlab"
    return "github"


def wrong_api_host(api, remote):
    """Why the token must not go to `api` for this origin remote, or None. The one case refused: the API is the default
    gitlab.com while origin is on another, fully qualified host (a self-hosted GitLab whose [agent] gitlab_api was not
    set): that host's token would be sent to gitlab.com. A configured gitlab_api is the person's choice, and a dotless
    remote host (an ssh alias such as gitlab-work) cannot be judged, so both are used as given. Callers ask only when the
    project itself came from the remote (an explicit gitlab_project names a project on the configured API)."""
    api_host, h = _host_of(api or GITLAB_API), _host_of(remote)
    if api_host != "gitlab.com" or not h or "." not in h or h == "gitlab.com" or h.endswith(".gitlab.com"):
        return None
    return (f"origin is on {h} but [agent] gitlab_api is {GITLAB_API}: set gitlab_api = \"https://{h}/api/v4\" "
            "(the token was not sent)")


def project_from_remote(remote, api=GITLAB_API):
    """group/sub/project of a GitLab remote, or None. A self-hosted GitLab under a path (https://h/gitlab/api/v4)
    has that path in front of the project path in its remotes; it is taken off."""
    remote = (remote or "").strip()
    if not remote:
        return None
    if "://" in remote:
        path = urlsplit(remote).path
    else:
        m = re.match(r"^(?:[^@/]+@)?[^:/]+:(.+)$", remote)
        if not m:
            return None
        path = m.group(1)
    path = "/" + path.strip("/")
    root = re.sub(r"/api/v4/?$", "", urlsplit(api or GITLAB_API).path or "").rstrip("/")
    if root and path.startswith(root + "/"):
        path = path[len(root):]
    path = re.sub(r"\.git$", "", path.strip("/"))
    return path if re.fullmatch(r"[\w.-]+(/[\w.-]+)+", path) else None


# ------------------------------------------------------------------ HTTP (no requests needed)
class _Resp:
    def __init__(self, status, body):
        self.status_code, self.content = status, body
        self.text = body.decode("utf-8", "replace")

    def json(self):
        return json.loads(self.text)


def no_redirect_opener():
    """urllib opener that never follows a redirect: urllib copies every request header (PRIVATE-TOKEN, Authorization)
    to wherever a 30x points, any host. A redirect comes back as its 3xx status (an HTTPError) instead."""
    import urllib.request

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    return urllib.request.build_opener(_NoRedirect)


class _UrllibSession:
    def request(self, method, url, json=None, timeout=60, headers=None):
        import json as _j, urllib.error, urllib.request
        data = _j.dumps(json).encode() if json is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=dict(headers or {}, **(
            {"Content-Type": "application/json"} if data is not None else {})))
        try:
            with no_redirect_opener().open(req, timeout=timeout) as r:
                return _Resp(r.status, r.read())
        except urllib.error.HTTPError as e:
            return _Resp(e.code, e.read() or b"")


# ------------------------------------------------------------------ GitLab REST v4
class GitLab:
    """Merge requests, notes and issues of one project. Paths are relative to /projects/<id>/."""
    kind = "gitlab"

    def __init__(self, project, token, session=None, api=GITLAB_API):
        if not project:
            raise GitLabError("the GitLab project is unknown: set [agent] gitlab_project (group/name) or use a GitLab origin remote")
        if not token:
            raise GitLabError("no GitLab token")
        self.project, self.token = str(project), token
        self.session = session or _UrllibSession()
        self.API = (api or GITLAB_API).rstrip("/")
        self._web = None

    def api(self, method, path, body=None):
        url = f"{self.API}/projects/{quote(self.project, safe='')}" + (f"/{path}" if path else "")
        r = self.session.request(method, url, json=body, timeout=60,
                                 headers={"PRIVATE-TOKEN": self.token, "Accept": "application/json"})
        if not 200 <= r.status_code < 300:
            e = GitLabError(f"GitLab {method} /projects/{self.project}/{path}: HTTP {r.status_code} "
                            f"{str(getattr(r, 'text', ''))[:200]}")
            e.status = r.status_code
            raise e
        return r.json() if r.status_code != 204 and getattr(r, "content", b"") else None

    # web links
    def web(self):
        """https://host/group/project (from the project path; a numeric id asks the API once)."""
        if self._web is None:
            if not self.project.isdigit():
                self._web = f"{re.sub(r'/api/v4$', '', self.API)}/{self.project}"
            else:
                self._web = (self.api("GET", "") or {}).get("web_url") or ""
        return self._web

    def raw_url(self, sha, path):
        return f"{self.web()}/-/raw/{sha}/{quote(path)}"

    # merge requests
    def find_open_mr(self, branch):
        mrs = self.api("GET", f"merge_requests?state=opened&source_branch={quote(branch, safe='')}&per_page=20") or []
        own = [m for m in mrs if str(m.get("source_project_id")) == str(m.get("target_project_id"))
               or m.get("source_project_id") is None]                   # never a fork's MR with the same branch name
        return min(own, key=lambda m: m["iid"]) if own else None

    def get_mr(self, iid):
        return self.api("GET", f"merge_requests/{iid}")

    def create_mr(self, source, target, title, description, labels=()):
        body = {"source_branch": source, "target_branch": target, "title": title, "description": description,
                "remove_source_branch": True}
        if labels:
            body["labels"] = ",".join(labels)
        return self.api("POST", "merge_requests", body)

    def update_mr(self, iid, title, description):
        return self.api("PUT", f"merge_requests/{iid}", {"title": title, "description": description})

    def comment_mr(self, iid, text):
        return self.api("POST", f"merge_requests/{iid}/notes", {"body": text})

    def close_mr(self, iid):
        return self.api("PUT", f"merge_requests/{iid}", {"state_event": "close"})

    # issues
    def ensure_label(self, name, color="#0e8a16", description=""):
        try:
            self.api("POST", "labels", {"name": name, "color": color, "description": description})
        except GitLabError:
            pass                                                         # 409: it exists already

    def rolling_issue(self, label):
        iss = self.api("GET", f"issues?labels={quote(label, safe='')}&state=opened&per_page=100") or []
        return min(iss, key=lambda i: i["iid"]) if iss else None

    def create_issue(self, title, description, labels=()):
        return self.api("POST", "issues", {"title": title, "description": description, "labels": ",".join(labels)})

    def update_issue(self, iid, title, description):
        return self.api("PUT", f"issues/{iid}", {"title": title, "description": description})

    def comment_issue(self, iid, text):
        return self.api("POST", f"issues/{iid}/notes", {"body": text})


def upsert_mr(gl, rec, title, body, base):
    """Open or update the merge request for rec["branch"] (the agent's batch record): a closed or merged MR is not a
    review any more, so a new one is opened; an open one is updated, never duplicated. Updates rec in place."""
    num = rec.get("pr_number")
    if num:
        try:
            cur = gl.get_mr(num)
        except GitLabError as e:                   # 404: the MR was deleted; like a closed one it is not a review
            if e.status != 404:
                raise
            cur = {"state": "closed"}
        if isinstance(cur, dict) and cur.get("state") in ("closed", "merged", "locked"):
            rec.setdefault("closed_prs", []).append(rec.get("pr_url"))
            num, rec["pr_number"], rec["pr_url"] = None, None, None
    if not num:
        mr = gl.find_open_mr(rec["branch"])
        num = mr["iid"] if mr else None
        rec["pr_url"] = mr.get("web_url") if mr else rec.get("pr_url")
    if num:
        r = gl.update_mr(num, title, body)
        rec["pr_action"] = "updated"
    else:
        r = gl.create_mr(rec["branch"], base, title, body)
        rec["pr_action"] = "opened"
    rec.update(pr_number=(r or {}).get("iid") or num, pr_url=(r or {}).get("web_url") or rec.get("pr_url"), host="gitlab")
    return rec


def upkeep_issue(gl, label, title, body, n_new, date):
    """upkeep's rolling issue on GitLab (same behaviour as upkeep.publish_issue on GitHub)."""
    iss = gl.rolling_issue(label)
    if iss:
        gl.update_issue(iss["iid"], title, body)
        gl.comment_issue(iss["iid"], f"{n_new} new change(s) on {date}: the description now has this week's report.")
        return {"kind": "issue", "action": "updated", "number": iss["iid"], "url": iss.get("web_url")}
    gl.ensure_label(label, description="ecadlib weekly library upkeep")
    new = gl.create_issue(title, body, [label])
    return {"kind": "issue", "action": "opened", "number": new["iid"], "url": new.get("web_url")}


def upkeep_mr(gl, label, branch, base, title, body):
    """upkeep's merge request for an already pushed branch (same behaviour as upkeep.publish_pr on GitHub)."""
    mr = gl.find_open_mr(branch)
    if mr:
        gl.update_mr(mr["iid"], title, body)
        return {"kind": "pr", "action": "updated", "number": mr["iid"], "url": mr.get("web_url"), "branch": branch}
    gl.ensure_label(label, description="ecadlib weekly library upkeep")
    new = gl.create_mr(branch, base, title, body, labels=[label])
    return {"kind": "pr", "action": "opened", "number": new["iid"], "url": new.get("web_url"), "branch": branch}


def client(cfg, remote, env, session=None):
    """(kind, client or None, why not) for the [agent] settings, the origin remote and the environment."""
    kind = host_kind(cfg, remote)
    if kind == "gitlab":
        proj = cfg.get("gitlab_project") or project_from_remote(remote, cfg.get("gitlab_api") or GITLAB_API)
        tok_env = cfg.get("gitlab_token_env") or ""
        token = UC.secret(tok_env, env) if tok_env else None
        if not (remote and proj and token):
            return kind, None, "no GitLab remote or token"
        bad = None if cfg.get("gitlab_project") else wrong_api_host(cfg.get("gitlab_api") or GITLAB_API, remote)
        if bad:
            return kind, None, bad
        return kind, GitLab(proj, token, session, cfg.get("gitlab_api") or GITLAB_API), None
    return kind, None, None                      # GitHub: the caller builds upkeep.GitHub as before
