"""Datasheet reads by your own assistant: no API key, no cost ([datasheet] reader = "assistant").

The datasheet source normally sends the PDF pages to a model through its API, billed to your key. With reader =
"assistant" (or `ecadlib librarian --read-with-assistant`, or ECADLIB_DATASHEET_READER=assistant) nothing is sent
anywhere. Each read the run needs is written to a folder instead, exactly as the model would get it:

    <plan folder>/datasheet/assistant/READ_ME.md          what to do, and every read still waiting
    <plan folder>/datasheet/assistant/<part>-<key>/
        request.md      the instructions (the model's system prompt) and the task for this part
        sent.pdf        the PDF pages a model would be sent; page-01.png ... the same pages as pictures
        image-01.png    close-ups of the drawing, where the run makes them
        schema.json     the shape the answer must have
        answer.json     <- written by your assistant (Claude Code, Claude Desktop, any chat that reads PDFs/images)

The part stays held ("waiting for your assistant's read") until answer.json is there. Run the same librarian command
again and the answer is read exactly as a model's answer would be: the same parser, the same checks against the
drawing, the same holds and the same release rules (a footprint from a read is released with no person's look only when
an independent source agrees, as for any read). The answer goes into the result cache like a paid one, so a later run
does not ask for it again. Each folder's name carries the request's key: a run that asks for more pages of the same
part (its first answer left something out) gets a new folder.

The cross-check's second read is asked for the same way, in its own folder (<part>-check-<key>), once the first read
is answered: its request says to read the pages afresh and never reuse the first answer (through the API the second read
is the same model reading again; 2026-11-23). Not made in this mode: the web search for a missing PDF (it is billed). Message Batches are off. Nothing here talks to the network."""
import base64
import hashlib
import json
import re
from pathlib import Path

README = "READ_ME.md"


def root(out):
    return Path(out) / "datasheet" / "assistant"


def pdf_folder(base):
    """<base>/pdfs: where your assistant saves a datasheet ecadlib could not download (<MPN>.pdf); the next run reads it
    as it reads a file in your [datasheet] folder."""
    return Path(base) / "pdfs"


def pdf_name(part):
    """The file name a downloaded datasheet is saved under: <MPN>.pdf, with what a file name cannot hold as "-"."""
    return re.sub(r'[\\/:*?"<>|]+', "-", str(part or "part")).strip(" .") + ".pdf"


def _stem(s):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(s or "part")).strip("._") or "part"


KINDS = ("check-", "pins-")                           # a second read's and a pin drawing read's keys say what they are


def _names(job):
    """(this request's folder name, the name it had before 2026-11-23)."""
    raw, key = str(job.get("name") or job.get("part") or "part"), str(job.get("key") or "nokey")
    stem = _stem(raw)
    if stem != raw:
        stem += "~" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:6]
    kind = next((k for k in KINDS if key.startswith(k)), "")
    new = f"{stem}-{kind}{key[len(kind):][:10]}-{hashlib.sha256(key.encode('utf-8')).hexdigest()[:8]}"
    old = key[:16] + key.rsplit("-", 1)[-1][:6] if kind == "check-" else key[:10]
    return new, f"{stem}-{old}"


def same_request(d, job):
    """Is the folder d this very request's: its job.json carries the request's whole key and its part? A folder
    with no job.json, or another request's, is not (review 2026-11-23: an answer.json was taken on the folder's name)."""
    try:
        had = json.loads((Path(d) / "job.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(had, dict) and bool(job.get("key")) and had.get("key") == str(job.get("key")) \
        and str(had.get("part")) == str(job.get("part"))


def folder(base, job):
    """<base>/<part>-<kind><first 10 characters of the PDF's hash>-<8 of the hash of the whole request key>: one folder
    per request. The key's first characters are only the PDF's hash, so a name made of them (as it was) gave every
    request of one PDF one folder: more pages, another package or another model was answered by the old answer.json
    (review 2026-11-23). A second read's folder keeps "-check-" in its name and a pin drawing read's "-pins-". A name
    the folder cannot hold as written (TESTPART/A and TESTPART#A both stem to TESTPART_A, FOO_ to FOO) gets 6 characters
    of its own hash too, so two parts never share a folder and each other's answers.
    A folder under the old name is still this request's when its job.json carries the request's whole key (an answer
    saved before the change is kept, for the request it answered and no other)."""
    new, old = _names(job)
    if not (Path(base) / new).exists() and same_request(Path(base) / old, job):
        return Path(base) / old
    return Path(base) / new


def _blocks(job):
    """The user turn's content blocks and the system text of the request a model would be sent."""
    body = job.get("body") or {}
    msgs = body.get("messages") or []
    content = (msgs[0].get("content") if msgs and isinstance(msgs[0], dict) else None) or []
    system = body.get("system")
    if isinstance(system, list):
        system = "\n\n".join(str(b.get("text") or "") for b in system if isinstance(b, dict))
    return content if isinstance(content, list) else [], str(system or "")


def _schema(job):
    oc = (job.get("body") or {}).get("output_config") or {}
    s = (oc.get("format") or {}).get("schema") if isinstance(oc, dict) else None
    if s:
        return s
    from .datasheet import wire_schema
    return wire_schema()


def answer(base, job, model="assistant"):
    """The assistant's answer to this request as a Messages-API-shaped message, or None when it has not answered yet.
    An answer.json that is not JSON raises DatasheetError naming the file (fix it and run again)."""
    from .datasheet import DatasheetError
    f = folder(base, job) / "answer.json"
    if not f.is_file() or not same_request(f.parent, job):   # another request's folder: no answer yet (asked afresh)
        return None
    try:                                              # (UTF-16 from PowerShell's > is not UTF-8: said, not a crash)
        text = f.read_text(encoding="utf-8-sig")
        json.loads(text)
    except (ValueError, OSError) as e:
        raise DatasheetError(f"{f} is not JSON ({e}): fix it and run again")
    return {"id": f"assistant-{_stem(job.get('part'))}", "type": "message", "role": "assistant", "model": model,
            "stop_reason": "end_turn", "content": [{"type": "text", "text": text}],
            "usage": {"input_tokens": 0, "output_tokens": 0}, "ecadlib": {"reader": "assistant", "file": f.name}}


def request(base, job):
    """Write this request's folder (the pages, the instructions, the schema) and the READ_ME index -> the folder."""
    d = folder(base, job)
    d.mkdir(parents=True, exist_ok=True)
    if (d / "answer.json").is_file() and not same_request(d, job):   # an answer to no request this folder knows (its
        (d / "answer.json").replace(d / "answer.other-request.json")   # job.json gone or another's): set aside, never
    content, system = _blocks(job)                                    # taken for this one once job.json is written
    files, texts, links, n_img = [], [], [], 0
    for b in content:
        if not isinstance(b, dict):
            continue
        src = b.get("source") or {}
        if b.get("type") == "document" and src.get("type") == "base64":
            pdf = base64.b64decode(src.get("data") or "")
            (d / "sent.pdf").write_bytes(pdf)
            files.append("sent.pdf")
            files += _pages(pdf, d)
        elif b.get("type") == "document" and src.get("type") == "url":
            links.append(str(src.get("url") or ""))
        elif b.get("type") == "image" and src.get("type") == "base64":
            n_img += 1
            ext = "jpg" if "jpeg" in str(src.get("media_type")) else "png"
            (d / f"image-{n_img:02d}.{ext}").write_bytes(base64.b64decode(src.get("data") or ""))
            files.append(f"image-{n_img:02d}.{ext}")
        elif b.get("type") == "text":
            texts.append(str(b.get("text") or ""))
    if not files and not links and job.get("data"):          # a job without a built body: its whole PDF
        (d / "sent.pdf").write_bytes(job["data"])
        files += ["sent.pdf"] + _pages(job["data"], d)
    if not texts:
        from .datasheet import task_text
        texts.append(task_text(job.get("part"), job.get("package")))
    (d / "schema.json").write_text(json.dumps(_schema(job), indent=1), encoding="utf-8")
    md = [f"# Datasheet read for {job.get('name') or job.get('part')}", "",
          f"Part number: {job.get('part')}  ", f"PDF: {job.get('url') or '(from your datasheet folder)'}", ""]
    if not files:                                            # no PDF: ecadlib's own download was refused or failed
        md += ["## First: the PDF", "",
               "ecadlib could not download this datasheet (the maker's or distributor's site refuses scripts, or the "
               "link is dead). Download it with a browser from the link below and save it as "
               f"`pdfs/{pdf_name(job.get('part'))}` in the folder above this one (datasheet/assistant/pdfs), then run the same `ecadlib librarian` command "
               "again: this read is then asked for again with the PDF's own pages, and this folder can be left "
               "unanswered. Save only this part's datasheet (or its family's) there, never a page that is not a PDF.", ""]
        tried = [a for a in ((job.get("prov") or {}).get("attempts") or ()) if isinstance(a, dict)]
        order = {}                                           # the part's usable links, as ecadlib tried them
        for u, w in [(job.get("url"), job.get("where"))] + [tuple(x) for x in job.get("more_urls") or ()]:
            if u and re.match(r"https?://", str(u)):
                order.setdefault(u, w)
        order = list(order.items())
        if len(order) > 1:                                   # the maker's first; a distributor's copy only if it can't be got
            md += ["Links to try, in order (the first that gives this part's PDF; a distributor's copy only when the "
                   "maker's own link can't be got):", ""] + [f"{i}. {u} ({w or 'link'})" for i, (u, w) in
                                                               enumerate(order[:8], 1)] + [""]
        if tried:                                            # why: refused, dead, the Archive had no copy ...
            md += ["What ecadlib tried:", ""] + [f"- {a.get('where') or ''}: {a.get('error') or ''}"[:400]
                                                  for a in tried[:8]] + [""]
    if str(job.get("key") or "").startswith("check-"):      # the cross-check's second read (ecadlib.crosscheck)
        md += ["## A second read", "",
               "This is the cross-check: a second, separate read of the same part, compared in code with the first "
               "read before a footprint built from it is released. Read it from these pages alone, as if for the "
               "first time: do not open or reuse any other folder's answer.json, and do not copy numbers from an "
               "earlier answer. If you answered this part's first read in this conversation, have a fresh session "
               "or sub-agent answer this one. A disagreement holds the part for a person, which is safe; a copied "
               "answer agrees with any mistake.", ""]
    md += ["Read the pages below as the instructions say, then write **answer.json** in this folder: one JSON object "
           "matching schema.json (no comments, no text around it). Leave a value out, or empty, when the pages do not "
           "print it: a guessed number is worse than none, because ecadlib holds a part with a missing value but may "
           "build a wrong one.", "", "## Files", ""]
    md += [f"- {f}" for f in files] or ["- (no pages: open the link below)"]
    md += [f"- link (not downloaded here): {u}" for u in links]
    md += ["", "## Task", "", *texts, "", "## Instructions (the model's system prompt)", "", system or "(none)", ""]
    (d / "request.md").write_text("\n".join(md), encoding="utf-8")
    (d / "job.json").write_text(json.dumps({k: job.get(k) for k in ("part", "name", "package", "url", "key", "n_pages")},
                                           indent=1, default=str), encoding="utf-8")
    try:                                                     # listed at once (the run's end lists exactly its own)
        had = json.loads((Path(base) / "waiting.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        had = None
    index(base, (had if isinstance(had, list) else []) + [d.name])
    return d


def _pages(pdf, d):
    try:
        from . import pdf_render as PR
        from .datasheet import pdf_pages
        n = pdf_pages(pdf)
    except Exception:
        return []
    out = []
    for p in range(1, (n or 0) + 1):
        try:
            (d / f"page-{p:02d}.png").write_bytes(PR.render_png(pdf, p, 150))
            out.append(f"page-{p:02d}.png")
        except Exception:
            break                                   # (no renderer here: sent.pdf alone)
    return out


def _has_pages(d):
    """Whether a request folder holds the pages to read: sent.pdf, or the pictures a pinout read sends instead."""
    try:
        return any(p.is_file() and (p.name == "sent.pdf" or re.match(r"(page|image)-\d+\.(png|jpg)$", p.name))
                   for p in d.iterdir())
    except OSError:
        return False


def waiting(base):
    """Folders with a request and no answer.json yet, by name: the ones the last run asked for (waiting.json), else
    every folder on disk."""
    b = Path(base)
    if not b.is_dir():
        return []
    try:
        names = json.loads((b / "waiting.json").read_text(encoding="utf-8"))
        names = [str(n) for n in names] if isinstance(names, list) else None
    except (OSError, ValueError):
        names = None
    if names is None:
        names = [p.name for p in b.iterdir() if p.is_dir()]
    return sorted(n for n in set(names) if (b / n / "request.md").is_file() and not (b / n / "answer.json").is_file())


def index(base, asked=None):
    """READ_ME.md (how to answer, and each read still waiting) and waiting.json. asked: the folder names this run asked
    for (a folder left from an earlier run, for a part since gone or changed, is then not listed); None: every one."""
    b = Path(base)
    b.mkdir(parents=True, exist_ok=True)
    if asked is not None:
        (b / "waiting.json").write_text(json.dumps(sorted(set(asked))), encoding="utf-8")
    wait = waiting(b)
    md = ["# Datasheet reads waiting for your assistant", "",
          "ecadlib sent nothing to any AI service for these parts. Each folder below holds one read: open its "
          "request.md, read the pages it lists (sent.pdf, page-NN.png, image-NN.png), and write answer.json there "
          "(one JSON object matching schema.json). Any assistant that can read PDFs or images can do it, for example "
          "Claude Code or Claude Desktop pointed at this folder. Then run the same `ecadlib librarian` command again: "
          "the answers are checked and built like any read, and parts that pass are released.", "",
          f"Waiting: {len(wait)}", ""]
    need = [w for w in wait if not _has_pages(b / w)]       # (a pinout read sends page pictures, no sent.pdf)
    md += [f"- {w}/request.md" + ("  (no PDF yet: download it first, see its request.md)" if w in need else "")
           for w in wait]
    if need:
        md += ["", f"{len(need)} of these have no PDF: ecadlib's own download was refused. Download each from the "
               "link in its request.md with a browser and save it in pdfs/ here as <MPN>.pdf (the name its request.md "
               "gives); the next run then asks for the read with the PDF's pages."]
    (b / README).write_text("\n".join(md) + "\n", encoding="utf-8")
    return wait
