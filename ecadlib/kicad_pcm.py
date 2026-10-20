"""The library as a KiCad Plugin and Content Manager (PCM) package: one zip KiCad installs with "Install from File...".

    ecadlib kicad-pcm out/librarian/plan.json --out out/pcm [--name mylib] [--kicad 8] [--pkg-version 1.2.0]
    ecadlib kicad-pcm out/kicad --out out/pcm --name mylib       # an existing `ecadlib kicad-export` folder
        [--identifier com.example.mylib] [--prefix PCM_] [--license CC-BY-SA-4.0] [--author NAME]
        [--base-url https://example.com/kicad]   # also write repository.json + packages.json for a custom repository
        [--schema pcm.v1.schema.json] [--json]

Writes into --out:
  <identifier>-<version>.zip   the package: metadata.json at the root, then KiCad's package folders
                               symbols/<lib>.kicad_sym, footprints/<lib>.pretty/, 3dmodels/<lib>.3dshapes/
  metadata.json                the same metadata, for reading without unzipping
  repository.json, packages.json   only with --base-url (KiCad fetches repositories over http/https only)
  kicad_pcm.json               what was written, the checks, the content hash that decides the version

How KiCad installs it (read from KiCad's source, kicad/pcm/pcm_task_manager.cpp, 7.0 and master): each archive
entry <dir>/<rest> with <dir> one of symbols, footprints, 3dmodels, plugins, resources, colors is extracted to
<3rd party>/<dir>/<identifier with '.' -> '_'>/<rest>; anything else (metadata.json, NOTICE.txt) is skipped. With
"automatically add installed libraries" on (the default), KiCad adds every .kicad_sym / .pretty under those folders
to the global tables as "<prefix><lib>" (prefix default "PCM_"), with a ${KICADn_3RD_PARTY}/... URI. So:
  * symbols' Footprint fields say "<prefix><lib>:<footprint>" (--prefix "" if your KiCad prefix is empty);
  * footprints' 3D models point at ${KICADn_3RD_PARTY}/3dmodels/<identifier_>/<lib>.3dshapes/<file>, n = --kicad;
  * SPICE models attached by sim-models go to symbols/<lib>.sim/ and Sim.Library points there the same way.
--kicad 7 writes KiCad 7 files (symbol library 20220914, footprints 20221018) so KiCad 7 can install it; 8 (the
default) keeps kicad_export's KiCad 8 files, which KiCad 8, 9 and 10 open. metadata.json says the minimum version.

The version (major.minor.patch, as the PCM schema wants) is --pkg-version, else the previous build's version when the
content is unchanged, else today's date (2026.10.16), so re-running on the same library changes nothing. KiCad offers
an update only for a newer version, so a change built when the previous build's version is today's date or later takes
the version after it (a second change on 2026.10.16 is 2026.10.17); repository.json's update_timestamp likewise only
moves forward.
The metadata is checked against KiCad's PCM JSON schema when one is found (--schema, or the pcm.v1.schema.json a KiCad
install ships in share/kicad/schemas) and the jsonschema package is installed, and always against the schema's
required fields and patterns written out below."""
import argparse, datetime, hashlib, io, json, re, shutil, sys, tempfile, zipfile
from pathlib import Path

SCHEMA_URL = "https://go.kicad.org/pcm/schemas/v1"
PKG_DIRS = ("symbols", "footprints", "3dmodels", "plugins", "resources", "colors")     # KiCad's PCM_PACKAGE_DIRECTORIES
ID_RE = re.compile(r"^[a-zA-Z][-a-zA-Z0-9.]{0,98}[a-zA-Z0-9]$")
VER_RE = re.compile(r"^\d{1,4}(\.\d{1,4}(\.\d{1,6})?)?$")
KVER_RE = re.compile(r"^\d{1,2}(\.\d{1,2}(\.\d{1,2})?)?$")
TAG_RE = re.compile(r"^[a-z][-a-z0-9]{0,48}[a-z0-9]$")
CONTACT_RE = re.compile(r"^[a-z][-a-z0-9 ]{0,48}[a-z0-9]$")
RESOURCE_RE = re.compile(r"^[a-zA-Z][-a-zA-Z0-9 ]{0,48}[a-zA-Z0-9]$")
LICENSES = {"public-domain", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "CC-BY-4.0", "CC-BY-SA-4.0",
            "CC0-1.0", "MIT", "GPL-2.0", "GPL-3.0", "LGPL-2.1", "LGPL-3.0", "MPL-1.1", "Unlicense", "CERN-OHL",
            "open-source", "unrestricted"}                         # the common ones of the schema's enum
SCHEMA_PATHS = ("/usr/share/kicad/schemas/pcm.v1.schema.json", "/usr/local/share/kicad/schemas/pcm.v1.schema.json",
                "/Applications/KiCad/KiCad.app/Contents/SharedSupport/schemas/pcm.v1.schema.json",
                "C:/Program Files/KiCad/9.0/share/kicad/schemas/pcm.v1.schema.json",
                "C:/Program Files/KiCad/8.0/share/kicad/schemas/pcm.v1.schema.json")
ZIP_TIME = (1980, 1, 1, 0, 0, 0)                                    # fixed: same input -> byte-identical zip


def clean_id(identifier):
    """The folder name KiCad extracts a package into ('.' -> '_', pcm_task_manager.cpp)."""
    return identifier.replace(".", "_")


def third_party_var(kicad):
    return f"KICAD{int(kicad)}_3RD_PARTY"


def default_identifier(lib):
    return "local.ecadlib." + (re.sub(r"[^-a-zA-Z0-9]+", "-", lib).strip("-") or "library")


# ---------------------------------------------------------------- file transforms
def footprint_text(text, lib, var, cid, kicad):
    """kicad_export's footprint -> PCM install paths (and KiCad 7 syntax for --kicad 7)."""
    text = re.sub(r'\(model "\$\{KIPRJMOD\}/' + re.escape(lib) + r'\.3dshapes/',
                  f'(model "${{{var}}}/3dmodels/{cid}/{lib}.3dshapes/', text)
    return footprint_kicad7(text) if int(kicad) < 8 else text


def symbol_text(text, lib, prefix, var, cid, kicad):
    """Footprint fields "<lib>:X" -> "<prefix><lib>:X"; Sim.Library "<lib>.sim/f" -> the installed path."""
    if prefix:
        text = re.sub(r'(\(property "Footprint" ")' + re.escape(lib) + ":", lambda m: m.group(1) + prefix + lib + ":", text)
    text = re.sub(r'(\(property "Sim\.Library" ")' + re.escape(lib) + r"\.sim/",
                  lambda m: f"{m.group(1)}${{{var}}}/symbols/{cid}/{lib}.sim/", text)
    return symbol_kicad7(text) if int(kicad) < 8 else text


def symbol_kicad7(text):
    """KiCad 8 symbol library -> KiCad 7 (20220914): the only KiCad 8 tokens kicad_export writes are dropped, and the
    Description field goes back to KiCad 7's ki_description (KiCad 7 reads a symbol's description only from that)."""
    text = re.sub(r"\(version 2023\d+\)|\(version 2024\d+\)", "(version 20220914)", text, count=1)
    text = text.replace('(property "Description" ', '(property "ki_description" ')
    return re.sub(r"\n?[ \t]*\((?:generator_version|exclude_from_sim) [^()]*\)", "", text)


def footprint_kicad7(text):
    """KiCad 8 footprint (version > 20221018) -> KiCad 7: Reference / Value properties become fp_text (the (at ...)
    first, as KiCad 7 requires), other properties lose their position (KiCad 7 footprint properties are name/value
    only), generator_version goes. KiCad 8 syntax KiCad 7's parser rejects: (uuid ...) and (unlocked yes) are dropped,
    (hide yes|no) -> hide or nothing, (fill yes|no) -> (fill solid|none), (remove_unused_layers / keep_end_layers
    yes|no) -> the bare flag or nothing."""
    from . import sexpr
    t = sexpr.parse(text, keep_quotes=True)
    v = sexpr.first(t, "version")
    if not v or int(v[1]) <= 20221018:
        return text
    v[1] = "20221018"

    def walk(node):
        res = [node[0]]
        for c in node[1:]:
            if isinstance(c, list) and c:
                if c[0] in ("uuid", "unlocked", "embedded_fonts"):
                    continue
                if c[0] == "hide" and node[0] in ("fp_text", "property", "model", "effects"):
                    if len(c) < 2 or c[1] == "yes":
                        res.append("hide")
                    continue
                if c[0] in ("remove_unused_layers", "keep_end_layers") and len(c) == 2:
                    if c[1] == "yes":
                        res.append([c[0]])
                    continue
                if c[0] == "fill" and node[0] != "zone" and len(c) == 2 and c[1] in ("yes", "no"):
                    c = ["fill", "solid" if c[1] == "yes" else "none"]
                c = walk(c)
            res.append(c)
        return res
    t = walk(t)
    out = []
    for c in t:
        if isinstance(c, list) and c and c[0] == "generator_version":
            continue
        if isinstance(c, list) and c and c[0] == "property" and len(c) >= 3:
            if c[1] in ("Reference", "Value"):
                rest = [x for x in c[3:] if x != "hide"]
                at = [x for x in rest if isinstance(x, list) and x and x[0] == "at"]
                c = ["fp_text", c[1].lower(), c[2]] + at + (["hide"] if "hide" in c[3:] else []) + [x for x in rest if x not in at]
            else:
                c = ["property", c[1], c[2]]
        out.append(c)
    return sexpr.dumps(out) + "\n"


# ---------------------------------------------------------------- metadata
def metadata(name, identifier, version, kicad, license_, author, description=None, contact=None, homepage=None,
             install_size=None):
    ver = {"version": version, "status": "stable", "kicad_version": f"{int(kicad)}.0"}
    if install_size is not None:
        ver["install_size"] = int(install_size)
    m = {"$schema": SCHEMA_URL, "name": name,
         "description": description or f"{name}: parts built by ecadlib (symbols, footprints, 3D models)",
         "description_full": (description or f"{name}: parts built by ecadlib") + ".\n\nSymbols, footprints and 3D models "
                             "built and checked by ecadlib from a parts list. Installed libraries "
                             "appear as <prefix>" + name + " (KiCad's default prefix is PCM_).",
         "identifier": identifier, "type": "library",
         "author": {"name": author, "contact": dict(contact or {})},
         "license": license_, "resources": {"homepage": homepage} if homepage else {},
         "tags": ["symbols", "footprints"], "versions": [ver]}
    return m


def check_metadata(m, schema_path=None, definition="Package"):
    """Problems with `m` (a Package, or with definition 'PackageArray'/'Repository' those), [] when fine.
    Built-in checks follow pcm.v1.schema.json; with jsonschema installed and a schema file found, that schema too
    (schema_path: a file, None = the one a KiCad install ships, False = built-in checks only).
    Returns (problems, how) where how says which checks ran."""
    errs, how = [], ["built-in"]
    if definition == "Package":
        for k in ("name", "description", "description_full", "identifier", "type", "author", "license", "resources", "versions"):
            if k not in m:
                errs.append(f"missing {k}")
        if not ID_RE.match(m.get("identifier", "")):
            errs.append(f"identifier {m.get('identifier')!r} must match {ID_RE.pattern}")
        if m.get("type") not in ("plugin", "library", "colortheme"):
            errs.append(f"type {m.get('type')!r}")
        if len(m.get("name", "")) > 200 or len(m.get("description", "")) > 500 or len(m.get("description_full", "")) > 5000:
            errs.append("name / description too long")
        a = m.get("author") or {}
        if "name" not in a or not isinstance(a.get("contact"), dict) or any(not CONTACT_RE.match(k) for k in a.get("contact", {})):
            errs.append("author needs name and contact {key: text} (keys lower case)")
        if any(not RESOURCE_RE.match(k) for k in m.get("resources", {})):
            errs.append("resources keys must match " + RESOURCE_RE.pattern)
        if any(not TAG_RE.match(t) for t in m.get("tags", [])):
            errs.append("tags must be lower case words")
        for v in m.get("versions", []):
            if not VER_RE.match(str(v.get("version", ""))):
                errs.append(f"version {v.get('version')!r} must be major[.minor[.patch]] (4/4/6 digits)")
            if not KVER_RE.match(str(v.get("kicad_version", ""))):
                errs.append(f"kicad_version {v.get('kicad_version')!r}")
            if v.get("status") not in ("stable", "testing", "development", "deprecated"):
                errs.append(f"status {v.get('status')!r}")
            if "download_sha256" in v and not re.match(r"^[a-f0-9]{64}$", v["download_sha256"]):
                errs.append("download_sha256 must be lower case hex")
    if schema_path is False:                                        # built-in checks only
        return errs, how
    if schema_path and not Path(schema_path).is_file():
        return errs + [f"schema file {schema_path} not found"], how
    path = schema_path or next((p for p in SCHEMA_PATHS if Path(p).is_file()), None)
    if path:
        try:
            import jsonschema
        except ImportError:
            jsonschema = None
        if jsonschema:
            schema = json.loads(Path(path).read_text(encoding="utf-8"))
            if definition != "Package":
                schema = dict(schema, **{"$ref": f"#/definitions/{definition}"})
            v = jsonschema.Draft7Validator(schema)
            errs += [f"schema: {'/'.join(map(str, e.path)) or '(root)'}: {e.message}" for e in v.iter_errors(m)]
            how.append(f"schema {path}")
    return errs, how


# ---------------------------------------------------------------- package
def check_export_folder(src, lib, rep):
    """Change control (#105): a kicad-export folder is packaged only as kicad-export wrote it. kicad_export.json lists
    every file the export wrote with its sha256 (taken from the released revisions); a symbol library or footprint
    edited, replaced or added by hand since is refused, so a package never carries a change no one approved. A 3D
    model the export did not write stays the user's file (kicad-export keeps those too). CRLF line ends (a git checkout
    with core.autocrlf) are not an edit, as for kicad-export. Raises ValueError."""
    from .kicad_export import _same
    src = Path(src)
    how = f"run `ecadlib kicad-export PLAN -o {src}` again (or package the plan.json itself)"
    if not rep or "written" not in rep:
        raise ValueError(f"{src}: no kicad_export.json with the files kicad-export wrote, so nothing can be checked "
                         f"against the released library: {how}; for a KiCad folder ecadlib did not export, pass "
                         f"--unchecked (the package then says it was not checked)")
    written = rep.get("written") or {}
    files = ([src / f"{lib}.kicad_sym"] if (src / f"{lib}.kicad_sym").is_file() else []) + \
        sorted((src / f"{lib}.pretty").glob("*.kicad_mod")) + \
        sorted(f for f in (src / f"{lib}.3dshapes").glob("*") if f.is_file() and f"{lib}.3dshapes/{f.name}" in written)
    bad = []
    for f in files:
        rel = f.relative_to(src).as_posix()
        if rel not in written:
            bad.append(f"{rel} (not written by kicad-export)")
        elif not _same(f.read_bytes(), written[rel]):
            parts = [p["part"] for p in rep.get("parts") or [] if rel.endswith(f"/{p.get('footprint')}.kicad_mod")]
            bad.append(f"{rel} (changed since kicad-export wrote it" + (f"; part {', '.join(parts)}" if parts else "") + ")")
    gone = [r for r in written if r.startswith((f"{lib}.kicad_sym", f"{lib}.pretty/")) and not (src / r).is_file()]
    bad += [f"{r} (removed since kicad-export wrote it)" for r in gone]
    if bad:
        raise ValueError(f"{src}: not packaged: files differ from what kicad-export wrote from the released library: "
                         f"{'; '.join(bad[:8])}" + (f" and {len(bad) - 8} more" if len(bad) > 8 else "") +
                         f". A change goes through the library (ecadlib part status / approve); then {how}")


UNCHECKED_NOTE = "NOT CHECKED: packaged with --unchecked from a KiCad folder ecadlib did not export. Its symbols and footprints were not compared with any released revision of an ecadlib library."


def _source(src, name, include_held, http, offline, cache, log, unchecked=False):
    """(export folder, library name, that library's kicad_export record or None, temp dir to clean up or None). unchecked: a folder
    with no kicad_export.json (not written by ecadlib) is packaged without the change-control check; a folder
    kicad-export wrote is always checked. name: the library to package (None: a folder's only library; a plan's is
    exported as 'ecadlib')."""
    from . import kicad_export as X
    src = Path(src)
    if src.is_dir():
        exp = src / "kicad_export.json"
        rep = json.loads(exp.read_text(encoding="utf-8")) if exp.is_file() else None
        # one kicad_export.json, several libraries (EXP-27): each library's own record is under "libraries"; the top
        # level is only the library exported last (a report from before then: that one library)
        recs = {k: v for k, v in rep["libraries"].items() if isinstance(v, dict)} \
            if isinstance((rep or {}).get("libraries"), dict) else {}
        if rep and rep.get("library") and rep["library"] not in recs and "written" in rep:
            recs[rep["library"]] = rep
        if name is None and len(recs) > 1:
            raise ValueError(f"{src} holds {len(recs)} libraries kicad-export wrote ({', '.join(sorted(recs))}): "
                             f"pass --name with the one to package")
        lib = X.libname(name) if name is not None else next(iter(recs), None) or "ecadlib"
        if recs and lib not in recs:
            raise ValueError(f"{src}: kicad-export wrote no library {lib} here (it holds {', '.join(sorted(recs))}): "
                             f"pass --name with one of them")
        if not recs and (name is None or not (src / f"{lib}.kicad_sym").is_file() and not (src / f"{lib}.pretty").is_dir()):
            libs = sorted({p.stem for p in src.glob("*.kicad_sym")} | {p.stem for p in src.glob("*.pretty") if p.is_dir()})
            if not libs:
                raise FileNotFoundError(f"{src} has no <lib>.kicad_sym or <lib>.pretty: run `ecadlib kicad-export PLAN -o {src}` first")
            if name is not None:                  # never another library than the one named
                raise FileNotFoundError(f"{src} has no {lib}.kicad_sym or {lib}.pretty (it holds {', '.join(libs)}): "
                                        f"pass --name with one of them")
            if len(libs) > 1:
                raise ValueError(f"{src} holds {len(libs)} libraries ({', '.join(libs)}): pass --name with the one to package")
            lib = libs[0]
        own = rep if (rep or {}).get("library") == lib else recs.get(lib, rep)     # the top level is the last one only
        if not (unchecked and rep is None):
            check_export_folder(src, lib, own)
        return src, lib, own, None
    if not src.is_file():
        raise FileNotFoundError(f"{src}: no such plan.json or kicad-export folder")
    from .httpcache import CachedHTTP
    tmp = Path(tempfile.mkdtemp(prefix="ecadlib-pcm-"))
    rep = X.export(src, tmp, name or "ecadlib", include_held, http=http or CachedHTTP(cache, offline=offline, timeout=60), log=log)
    return tmp, rep["library"], rep, tmp


def stage(exp, lib, identifier, prefix, kicad):
    """{archive path: bytes} for the package folders, from a kicad-export folder."""
    var, cid = third_party_var(kicad), clean_id(identifier)
    files = {}
    sym = exp / f"{lib}.kicad_sym"
    if sym.is_file():
        files[f"symbols/{lib}.kicad_sym"] = symbol_text(sym.read_text(encoding="utf-8"), lib, prefix, var, cid, kicad).encode()
    for f in sorted((exp / f"{lib}.sim").glob("*")) if (exp / f"{lib}.sim").is_dir() else []:
        if f.is_file():
            files[f"symbols/{lib}.sim/{f.name}"] = f.read_bytes()
    for f in sorted((exp / f"{lib}.pretty").glob("*.kicad_mod")) if (exp / f"{lib}.pretty").is_dir() else []:
        files[f"footprints/{lib}.pretty/{f.name}"] = footprint_text(f.read_text(encoding="utf-8"), lib, var, cid, kicad).encode()
    for f in sorted((exp / f"{lib}.3dshapes").glob("*")) if (exp / f"{lib}.3dshapes").is_dir() else []:
        if f.is_file():
            files[f"3dmodels/{lib}.3dshapes/{f.name}"] = f.read_bytes()
    if not any(k.startswith(("symbols/", "footprints/")) for k in files):
        raise ValueError(f"nothing to package: {exp} has no {lib}.kicad_sym and no footprints in {lib}.pretty")
    return files


def zip_bytes(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(files):
            zi = zipfile.ZipInfo(name, ZIP_TIME)
            zi.external_attr = 0o644 << 16
            zi.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(zi, files[name])
    return buf.getvalue()


def content_hash(files):
    h = hashlib.sha256()
    for k in sorted(files):
        h.update(k.encode() + b"\0" + hashlib.sha256(files[k]).digest())
    return h.hexdigest()


def build(src, out, name=None, identifier=None, pkg_version=None, kicad=8, prefix="PCM_", license_="CC-BY-SA-4.0",
          author="ecadlib user", description=None, homepage=None, base_url=None, include_held=False, http=None,
          offline=False, cache="out/http_cache", schema=None, today=None, log=print, unchecked=False):
    """Build the PCM package from a plan.json or a kicad-export folder into `out`; returns the report dict.
    name: with a plan, the library name to export it as (None: 'ecadlib'); with a folder, the library to package
    (None: the folder's only one). unchecked: package a KiCad folder ecadlib did not export (no kicad_export.json) as it is; the package's NOTICE.txt
    and the report say it was not checked."""
    if int(kicad) < 7:
        raise ValueError("--kicad must be 7 or newer (the Plugin and Content Manager came with KiCad 6, libraries with 7)")
    if base_url and not re.match(r"^https?://", base_url):
        raise ValueError("--base-url must be an http:// or https:// URL: KiCad only fetches repositories over http(s) "
                         "(kicad_curl_easy.cpp); for a local file use the PCM's 'Install from File...'")
    out = Path(out)
    exp, lib, xrep, tmp = _source(src, name, include_held, http, offline, cache, log, unchecked)
    unchecked = bool(unchecked and Path(src).is_dir() and xrep is None)
    try:
        identifier = identifier or default_identifier(lib)
        if not ID_RE.match(identifier):
            raise ValueError(f"identifier {identifier!r}: letters, digits, '-' and '.', starting with a letter (KiCad's PCM rule)")
        files = stage(exp, lib, identifier, prefix, kicad)
        notice = exp / "NOTICE.txt"
        if notice.is_file():
            files["NOTICE.txt"] = notice.read_bytes()               # root files are not installed; kept in the zip
        if unchecked:
            files["NOTICE.txt"] = files.get("NOTICE.txt", b"") + (UNCHECKED_NOTE + "\n").encode("utf-8")
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)
    if (xrep or {}).get("kicad_content") and license_ not in ("CC-BY-SA-4.0",):
        log(f"  note: the library holds KiCad library content (CC-BY-SA 4.0); --license {license_} may not be allowed")
    chash = content_hash(files | {"#settings": json.dumps([name or "ecadlib", identifier, kicad, prefix, license_, author, description, homepage]).encode()})
    prev = out / "kicad_pcm.json"
    old = json.loads(prev.read_text(encoding="utf-8")) if prev.is_file() else {}
    if pkg_version:
        version = pkg_version
    elif old.get("content_sha256") == chash and old.get("version"):
        version = old["version"]
    else:
        d = today or datetime.date.today()
        prior = _ver_tuple(old.get("version"))
        version = ".".join(map(str, max((d.year, d.month, d.day), prior[:2] + (prior[2] + 1,) if prior else ())))
    if not VER_RE.match(version):
        raise ValueError(f"version {version!r} must be major[.minor[.patch]], e.g. 1.2.0")
    install_size = sum(len(v) for k, v in files.items() if k.split("/", 1)[0] in PKG_DIRS)
    meta = metadata(lib, identifier, version, kicad, license_, author, description, homepage=homepage, install_size=install_size)
    problems, how = check_metadata(meta, schema)
    files["metadata.json"] = (json.dumps(meta, indent=2) + "\n").encode()
    data = zip_bytes(files)
    out.mkdir(parents=True, exist_ok=True)
    zname = f"{identifier}-{version}.zip"
    zpath = out / zname
    if not zpath.is_file() or zpath.read_bytes() != data:
        zpath.write_bytes(data)
    _write(out / "metadata.json", files["metadata.json"])
    rep = {"zip": str(zpath), "identifier": identifier, "install_dir": clean_id(identifier), "library": lib,
           "nickname": prefix + lib, "version": version, "kicad_version": f"{int(kicad)}.0", "env_var": third_party_var(kicad),
           "content_sha256": chash, "sha256": hashlib.sha256(data).hexdigest(), "download_size": len(data),
           "install_size": install_size, "files": sorted(k for k in files if k.split("/", 1)[0] in PKG_DIRS),
           "symbols": sum(1 for k in files if k.endswith(".kicad_sym")),
           "footprints": sum(1 for k in files if k.endswith(".kicad_mod")),
           "models": sum(1 for k in files if k.startswith("3dmodels/")), "checks": how, "problems": problems,
           "unchecked": unchecked}
    if base_url:
        pkg = json.loads(json.dumps(meta))
        pkg["versions"][0].update({"download_url": f"{base_url.rstrip('/')}/{zname}", "download_sha256": rep["sha256"],
                                   "download_size": len(data)})
        pk = {"packages": [pkg]}
        pk_bytes = (json.dumps(pk, indent=2) + "\n").encode()
        ts = int(datetime.datetime.combine(today or datetime.date.today(), datetime.time()).replace(tzinfo=datetime.timezone.utc).timestamp())
        if old.get("packages_sha256") == hashlib.sha256(pk_bytes).hexdigest() and old.get("update_timestamp"):
            ts = old["update_timestamp"]                              # unchanged: same timestamp, no "update" in KiCad
        elif isinstance(old.get("update_timestamp"), int):
            ts = max(ts, old["update_timestamp"] + 1)                 # changed: later than the last one, even the same day
        repo = {"$schema": SCHEMA_URL, "name": f"{lib} (ecadlib)",
                "packages": {"url": f"{base_url.rstrip('/')}/packages.json", "sha256": hashlib.sha256(pk_bytes).hexdigest(),
                             "update_timestamp": ts,
                             "update_time_utc": datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")}}
        p2, _ = check_metadata(pk, schema, "PackageArray")
        p3, _ = check_metadata(repo, schema, "Repository")
        rep["problems"] += p2 + p3
        _write(out / "packages.json", pk_bytes)
        _write(out / "repository.json", (json.dumps(repo, indent=2) + "\n").encode())
        rep.update(repository=str(out / "repository.json"), repository_url=f"{base_url.rstrip('/')}/repository.json",
                   packages_sha256=repo["packages"]["sha256"], update_timestamp=ts)
    _write(prev, (json.dumps(rep, indent=2) + "\n").encode())
    return rep


def _ver_tuple(version):
    """'2026.10.16' -> (2026, 10, 16), '3.1' -> (3, 1, 0) (KiCad compares versions part by part); None when it is not
    major[.minor[.patch]]."""
    if not isinstance(version, str) or not VER_RE.match(version):
        return None
    t = tuple(int(x) for x in version.split("."))
    return t + (0,) * (3 - len(t))


def _write(path, data):
    if not path.is_file() or path.read_bytes() != data:
        path.write_bytes(data)


def install(zip_path, third_party, repository_id=""):
    """Do what KiCad's PCM "Install from File..." does with the archive, outside KiCad (for tests and headless setups):
    extract the package folders to <third_party>/<dir>/<identifier_>/..., refusing entries that would land outside.
    Returns {"identifier", "symbols": [paths], "footprints": [paths]} - the libraries KiCad would add to its tables."""
    third_party = Path(third_party).resolve()
    with zipfile.ZipFile(zip_path) as z:
        meta = json.loads(z.read("metadata.json"))
        if len(meta.get("versions", [])) != 1:
            raise ValueError("Archive metadata must have a single version defined")     # KiCad's own words
        cid = clean_id(meta["identifier"])
        for e in z.infolist():
            parts = [p for p in e.filename.split("/") if p]
            if e.is_dir() or len(parts) < 2 or parts[0] not in PKG_DIRS:
                continue
            target = (third_party / parts[0] / cid / Path(*parts[1:])).resolve()
            if not target.is_relative_to(third_party):
                raise ValueError(f"Package archive entry '{e.filename}' would be extracted outside of the package directory.")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(e))
    syms = sorted(str(p) for p in (third_party / "symbols" / cid).rglob("*.kicad_sym"))
    fps = sorted(str(p) for p in (third_party / "footprints" / cid).rglob("*.pretty") if p.is_dir())
    return {"identifier": meta["identifier"], "install_dir": cid, "symbols": syms, "footprints": fps}


def global_table_rows(installed, third_party, kicad, prefix="PCM_"):
    """The rows KiCad's PCM_SYM_LIB_TRAVERSER / PCM_FP_LIB_TRAVERSER add for an installed package:
    {"sym": [(nickname, uri)], "fp": [(nickname, uri)]}, URIs as ${KICADn_3RD_PARTY}/<dir>/<id_>/<lib>."""
    var, root = third_party_var(kicad), Path(third_party).resolve()
    rows = {"sym": [], "fp": []}
    for kind, paths, ext in (("sym", installed["symbols"], ".kicad_sym"), ("fp", installed["footprints"], ".pretty")):
        for p in paths:
            rel = Path(p).resolve().relative_to(root).as_posix()
            rows[kind].append((prefix + Path(p).name[: -len(ext)], f"${{{var}}}/{rel}"))
    return rows


def plugin_build(out, identifier="local.ecadlib.add-part", kicad=7, author="ecadlib user", schema=None):
    """The ecadlib pcbnew action plugins (the toolbar button and the ecadlib window) as a PCM plugin package:
    plugins/__init__.py, plugins/ecadlib.png (the toolbar icon) and resources/icon.png (the PCM's list icon)."""
    from . import __version__
    from .kicad_menu import icon_png
    src = Path(__file__).with_name("assets") / "kicad_action_plugin.py"
    version = re.match(r"\d+(\.\d+){0,2}", __version__).group(0)
    files = {"plugins/__init__.py": src.read_bytes(), "plugins/ecadlib.png": icon_png(24), "resources/icon.png": icon_png(64)}
    meta = metadata("ecadlib", identifier, version, kicad, "MIT", author,
                    description="ecadlib in the PCB editor: add parts by MPN, check the project BOM, review queue")
    meta["type"], meta["tags"] = "plugin", ["library", "parts"]
    meta["description_full"] = (src.read_text(encoding="utf-8").split('"""')[1].strip())[:5000]
    meta["versions"][0]["install_size"] = sum(len(v) for v in files.values())
    problems, how = check_metadata(meta, schema)
    files["metadata.json"] = (json.dumps(meta, indent=2) + "\n").encode()
    data = zip_bytes(files)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    zpath = out / f"{identifier}-{version}.zip"
    _write(zpath, data)
    return {"zip": str(zpath), "identifier": identifier, "version": version, "checks": how, "problems": problems,
            "sha256": hashlib.sha256(data).hexdigest()}


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="ecadlib kicad-pcm",
        description="Package the library for KiCad's Plugin and Content Manager (a zip for 'Install from File...')",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="examples:\n  ecadlib kicad-pcm out/librarian/plan.json --out out/pcm --name mylib\n"
               "  ecadlib kicad-pcm out/kicad --out out/pcm --kicad 7\n"
               "  ecadlib kicad-pcm out/kicad --out site/kicad --base-url https://example.com/kicad   # custom repository\n"
               "then in KiCad: Plugin and Content Manager > Install from File... > the .zip")
    ap.add_argument("src", nargs="?", help="librarian plan.json, or a folder `ecadlib kicad-export` wrote")
    ap.add_argument("--plugin", action="store_true",
                    help="instead: package the ecadlib PCB editor plugin: toolbar button and window (KiCad 7+)")
    ap.add_argument("-o", "--out", default="out/pcm", help="output folder (default %(default)s)")
    ap.add_argument("--name", help="library name: with a plan, the name to export it as (default ecadlib); with a "
                    "kicad-export folder, the library to package (needed when the folder holds several)")
    ap.add_argument("--identifier", help="package id, e.g. com.example.mylib (default local.ecadlib.<lib>)")
    ap.add_argument("--pkg-version", help="package version major.minor.patch (default: kept while unchanged, else today's "
                    "date, or the version after the previous build's when that is today's date or later)")
    ap.add_argument("--kicad", type=int, default=8, choices=(7, 8, 9, 10), help="oldest KiCad to install it (default 8)")
    ap.add_argument("--prefix", default="PCM_", help="KiCad's library nickname prefix for PCM libraries (default %(default)s)")
    ap.add_argument("--license", default="CC-BY-SA-4.0", help="license from KiCad's PCM list (default %(default)s)")
    ap.add_argument("--author", default="ecadlib user")
    ap.add_argument("--description")
    ap.add_argument("--homepage")
    ap.add_argument("--base-url", help="https URL the folder will be served from: also write repository.json + packages.json")
    ap.add_argument("--include-held", action="store_true", help="with a plan: also package held parts")
    ap.add_argument("--cache", default="out/http_cache")
    ap.add_argument("--offline", action="store_true", help="with a plan: use the HTTP cache only")
    ap.add_argument("--schema", help="KiCad's pcm.v1.schema.json to validate against (default: the one KiCad installs)")
    ap.add_argument("--unchecked", action="store_true", help="package a KiCad folder ecadlib did not export (no "
                    "kicad_export.json) without checking it against an ecadlib library; the package says so")
    ap.add_argument("--json", action="store_true", help="print the report as JSON")
    a = ap.parse_args(argv)
    if a.plugin:
        rep = plugin_build(a.out, schema=a.schema)
        print(json.dumps(rep, indent=2) if a.json else
              "\n".join([f"  PROBLEM {p}" for p in rep["problems"]] +
                        [f"  in KiCad: Plugin and Content Manager > Install from File... > {Path(rep['zip']).name}; "
                         "then the ecadlib button in the PCB editor's top toolbar", f"wrote {rep['zip']}"]))
        return 1 if rep["problems"] else 0
    if not a.src:
        ap.error("give a plan.json or a kicad-export folder (or --plugin)")
    log = (lambda *x: print(*x, file=sys.stderr)) if a.json else print
    try:
        rep = build(a.src, a.out, a.name, a.identifier, a.pkg_version, a.kicad, a.prefix, a.license, a.author,
                    a.description, a.homepage, a.base_url, a.include_held, offline=a.offline, cache=a.cache,
                    schema=a.schema, log=log, unchecked=a.unchecked)
    except (OSError, ValueError) as e:
        print(f"ecadlib kicad-pcm: {e}", file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(rep, indent=2))
        return 1 if rep["problems"] else 0
    for p in rep["problems"]:
        log(f"  PROBLEM {p}")
    if rep.get("unchecked"):
        log("  NOT CHECKED (--unchecked): this folder was not written by kicad-export, so its parts were not compared "
            "with any released revision; the package's NOTICE.txt says so too")
    log(f"  {rep['symbols']} symbol library, {rep['footprints']} footprints, {rep['models']} 3D models; "
        f"checked: {', '.join(rep['checks'])}")
    log(f"  in KiCad: Plugin and Content Manager > Install from File... > {Path(rep['zip']).name}; "
        f"the library shows up as {rep['nickname']}")
    if rep.get("repository"):
        log(f"  repository: upload {a.out} to {a.base_url}, then add {rep['repository_url']} under Manage Repositories")
    log(f"wrote {rep['zip']} (version {rep['version']}, KiCad {rep['kicad_version']}+)")
    return 1 if rep["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
