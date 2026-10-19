"""Small deterministic fuzzers (stdlib random, fixed seeds, bounded time) for the parsers and front ends that take
outside input. Any exception other than the target's documented error type fails the test, with the input that
caused it. Offline, the whole file runs in well under 30 s. Run:  python3 -m tests.test_fuzz [iterations-scale]

Documented error types:
  bom_triage.read_bom                 ValueError (and OSError for a file that cannot be opened)
  sexpr.parse                         ValueError
  kicad_import.convert_footprint/_symbol   ValueError
  altium_project.read_schdoc, altium_lib.read_pcblib   ValueError, OSError (olefile: not an OLE file)
  footprint_gen.validate              jsonschema.ValidationError; a valid spec: sanity() returns problems, build() raises ValueError
  rules.load / parse_text             rules.RulesError (a ValueError)
  web UI JSON endpoints               an HTTP status below 500 and no traceback from the handler
  MCP tools/call                      a result (isError for bad input), no traceback for user errors"""
import contextlib, copy, inspect, io, json, os, random, re, sys, tempfile, time, traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCALE = float(os.environ.get("ECADLIB_FUZZ_SCALE", "1"))
BUDGET = 3.5 * SCALE                            # seconds per target at most (the iteration count usually ends first)


class Found(AssertionError):
    pass


def _fuzz(name, n, seed, make, run, ok):
    """make(rnd) -> input; run(input) must return or raise one of `ok`."""
    rnd, t0 = random.Random(seed), time.time()
    for i in range(int(n * SCALE)):
        x = make(rnd)
        try:
            run(x)
        except ok:
            pass
        except Exception as e:
            raise Found(f"{name} seed {seed} case {i}: {type(e).__name__}: {e}\ninput: {str(x)[:600]!r}\n"
                        + "".join(traceback.format_exception(e)[-6:])) from None
        if time.time() - t0 > BUDGET:
            break


def _mutate_bytes(rnd, b, extra=(b",", b";", b"\t", b'"', b"\n", b"\r", b"\x00", b"\xe9", b"\xff\xfe", b"Total")):
    b = bytearray(b)
    for _ in range(rnd.randint(1, 6)):
        if not b:
            break
        i, op = rnd.randrange(len(b)), rnd.randrange(4)
        if op == 0:
            del b[i:i + rnd.randrange(1, 40)]
        elif op == 1:
            b[i:i] = rnd.choice(extra)
        elif op == 2:
            b[i] = rnd.randrange(256)
        else:
            b[i:i] = bytes(b[rnd.randrange(len(b)):][:30])
    return bytes(b)


def _mutate_tree(rnd, t):
    t = copy.deepcopy(t)
    for _ in range(rnd.randint(1, 3)):
        stack, nodes = [t], []
        while stack:
            x = stack.pop()
            if isinstance(x, list):
                nodes.append(x)
                stack.extend(x)
        nd = rnd.choice(nodes)
        if len(nd) < 2:
            continue
        i, op = rnd.randrange(1, len(nd)), rnd.randrange(5)
        if op == 0:
            del nd[i]
        elif op == 1:
            nd[i] = rnd.choice(["0", "-1", "1e9", "nan", "x", "", "inf", "0.0"])
        elif op == 2:
            nd.insert(i, copy.deepcopy(nd[rnd.randrange(1, len(nd))]))
        elif op == 3:
            nd[i] = []
        else:
            del nd[i:]
    return t


BOMS = [
    (".csv", (ROOT / "examples/sample_bom.csv").read_bytes()),
    (".csv", b'"Bill of Materials";;;\n;;;\nComment;Designator;Quantity;Manufacturer Part Number 1\n'
             b'10k;"R1, R2";2;RC0603FR-0710KL\n;Total;2;\n'),
    (".txt", "Designator\tComment\tQuantity\r\nU1\tSTM32\t1\r\n".encode("utf-16")),
    (".csv", "Designator;Value;MPN\nC1;2,2µF;GRM21\n".encode("latin-1")),
]


def test_fuzz_bom_reader():
    from ecadlib import bom_triage as T
    d = Path(tempfile.mkdtemp())
    import openpyxl
    wb = openpyxl.Workbook()
    wb.active.append(["Designator", "Qty", "MPN"])
    wb.active.append(["R1-R4", 4, "RC0603FR-0710KL"])
    wb.save(d / "base.xlsx")
    boms = BOMS + [(".xlsx", (d / "base.xlsx").read_bytes())]
    count = iter(range(10 ** 6))

    def make(rnd):
        suf, b = rnd.choice(boms)
        f = d / f"b{next(count)}{rnd.choice([suf, suf, '.csv', '.xlsx', '.xls'])}"
        f.write_bytes(_mutate_bytes(rnd, b))
        return f
    _fuzz("read_bom", 250, 11, make, T.read_bom, (ValueError, OSError))


KICAD = [ROOT / "tests/fixtures/kicad/USB_C_Receptacle_HRO_TYPE-C-31-M-12.kicad_mod",
         ROOT / "tests/fixtures/kicad/Regulator_Linear_AMS1117.kicad_sym",
         ROOT / "tests/fixtures/kicad/ecadlib_demorgan.kicad_sym"]


def test_fuzz_sexpr():
    from ecadlib import sexpr
    texts = [p.read_text() for p in KICAD]

    def make(rnd):
        s = rnd.choice(texts)
        i = rnd.randrange(len(s))
        j = i + rnd.randrange(50)
        return s[:i] + rnd.choice(["", "(", ")", '"', "\\", "\x00", "((((", s[j:j + 20]]) + s[j:]
    _fuzz("sexpr.parse", 300, 12, make, lambda m: (sexpr.parse(m), sexpr.parse(m, True)), (ValueError,))


def test_fuzz_kicad_importer():
    from ecadlib import kicad_import as KI, sexpr
    fp = sexpr.parse(KICAD[0].read_text())
    _fuzz("convert_footprint", 300, 13, lambda rnd: sexpr.dumps(_mutate_tree(rnd, fp)),
          lambda m: KI.convert_footprint(m, "X"), (ValueError,))
    syms = [s for p in KICAD[1:] for s in sexpr.find(sexpr.parse(p.read_text()), "symbol")]
    _fuzz("convert_symbol", 300, 14, lambda rnd: _mutate_tree(rnd, rnd.choice(syms)),
          lambda s: KI.convert_symbol([s], "X"), (ValueError,))


def test_fuzz_altium_ole_readers():
    from ecadlib import altium_lib as AL, altium_project as AP, footprint_gen as FG, kicad_import as KI
    from tests.test_altium_project import comp, write_schdoc
    d = Path(tempfile.mkdtemp())
    sch = write_schdoc(d / "a.SchDoc", [comp("R1", "RES", "AAAA", [("Manufacturer Part Number", "RC0603FR-0710KL")], ["R0603"]),
                                       comp("U1", "MCU", "BBBB", [("MPN", "STM32")], ["LQFP48", "QFP"], current=1)])
    spec = json.loads((ROOT / "ecadlib/package_specs/R_0603_1608Metric.json").read_text())
    AL.write_pcblib(d / "a.PcbLib", [KI.convert_footprint(FG.build(spec)[0], "R0603")[0]])
    base = [("SchDoc", sch.read_bytes(), AP.read_schdoc), ("PcbLib", (d / "a.PcbLib").read_bytes(), AL.read_pcblib)]
    count = iter(range(10 ** 6))

    def make(rnd):
        ext, b, reader = rnd.choice(base)
        b = bytearray(b)
        for _ in range(rnd.randint(1, 8)):
            i = rnd.randrange(512, len(b)) if rnd.random() < .8 else rnd.randrange(len(b))
            op = rnd.randrange(3)
            if op == 0:
                b[i] = rnd.randrange(256)
            elif op == 1:
                b[i:i + 4] = rnd.choice([b"\xff\xff\xff\xff", b"\x00\x00\x00\x00", b"\xfe\xff\xff\xff", b"\x10\x00\x00\x00"])
            else:
                b[i:i] = b"|RECORD=1|OWNERINDEX=-5|"
        if rnd.random() < .1:
            b = b[:rnd.randrange(len(b))]
        f = d / f"m{next(count)}.{ext}"
        f.write_bytes(bytes(b))
        return reader, f
    _fuzz("altium OLE readers", 600, 15, make, lambda x: x[0](x[1]), (ValueError, OSError))


def test_fuzz_package_specs():
    import jsonschema
    from ecadlib import footprint_gen as FG
    specs = [json.loads(p.read_text()) for p in sorted((ROOT / "ecadlib/package_specs").rglob("*.json"))]
    vals = [0, -1, 0.0001, 1e6, None, "x", [], {}, True, [1, 2], [2, 1, 0.5], {"min": 1, "max": 0.5}, 3, 0.5]

    def make(rnd):
        sp = copy.deepcopy(rnd.choice(specs))
        for k in rnd.sample(list(sp), min(len(sp), rnd.randint(1, 2))):
            sp[k] = rnd.choice(vals)
        return sp

    def run(sp):
        try:
            FG.validate(sp)
        except jsonschema.ValidationError:
            return
        if not FG.sanity(sp):
            FG.build(sp)
    _fuzz("package spec", 300, 16, make, run, (ValueError,))


def test_fuzz_rules():
    from ecadlib import rules as RU
    secs = [k for k in RU.DEFAULTS if isinstance(RU.DEFAULTS[k], dict)]
    vals = [0, -1, "", "x", [], {}, True, ["a"], {"a": 1}, 1e9, "{mpn}", "{nope}", [1, "a"], {"a": {"b": 1}}]

    def toml(v):
        if isinstance(v, dict):
            return "{ " + ", ".join(f'"{a}" = {toml(b)}' for a, b in v.items()) + " }"
        if isinstance(v, list):
            return "[" + ", ".join(toml(x) for x in v) + "]"
        return json.dumps(v)

    def make(rnd):
        sec = rnd.choice(secs)
        k = rnd.choice(list(RU.DEFAULTS[sec]) + ["bogus"])
        v = rnd.choice(vals)
        text = f'[{sec}]\n"{k}" = {toml(v)}\n'
        if rnd.random() < .2:
            text = text[:rnd.randrange(len(text))] + rnd.choice(["", "[", "=", '"', "\x00"])
        return text

    def run(text):
        RU.load(RU.parse_text(text))
    _fuzz("rules", 400, 17, make, run, (RU.RulesError,))


def test_fuzz_web_json_endpoints():
    from ecadlib.app import Workspace
    from ecadlib.ui import server as SV
    from tests.test_ui import serve, wait_job
    tracebacks = []
    real = SV.traceback.print_exc
    SV.traceback.print_exc = lambda *a, **k: tracebacks.append(traceback.format_exc())
    with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        srv, call = serve(Workspace(d, [str(ROOT / "examples/kicad_lib")], providers=["digikey"], offline=True))
        try:
            rels = []
            for suf, b in BOMS:
                st, j = call("POST", "/api/upload", b, {"X-Filename": "bom" + suf})
                assert st == 200, j
                rels.append((wait_job(call, j["job"]).get("result") or {}).get("bom") or "x")
            src = inspect.getsource(SV)
            post_src = src[src.index("def do_POST"):]
            posts = sorted({p for p in re.findall(r'"(/api/[a-z0-9/_-]+)"', post_src)}
                           - {"/api/upload", "/api/librarian/run", "/api/rules/propose", "/api/altium365/workspaces"})
            assert len(posts) > 20, posts
            keys = ["bom", "spec", "density", "overwrite", "part", "kind", "id", "pin_map", "allow", "unallow", "user", "role",
                    "comment", "decision", "merge", "text", "dry_run", "workspace", "note", "refresh", "limit"]

            def val(rnd, depth=0):
                c = rnd.randrange(8 if depth < 2 else 6)
                if c == 6:
                    return [val(rnd, depth + 1) for _ in range(rnd.randrange(3))]
                if c == 7:
                    return {rnd.choice(["spec", "bom", "name", "package_type"]): val(rnd, depth + 1) for _ in range(rnd.randrange(3))}
                return [None, True, rnd.randint(-5, 10 ** 12), rnd.random(), "",
                        rnd.choice(["x", "../../etc/passwd", "uploads", "R1", "a" * 300, "\x00", "plan.json"])][c]

            def make(rnd):
                body = {k: val(rnd) for k in rnd.sample(keys, rnd.randrange(1, 6))}
                if rnd.random() < .6:
                    body["bom"] = rnd.choice(rels)
                return rnd.choice(posts), json.dumps(body)

            def run(x):
                st, j = call("POST", x[0], x[1])
                if st >= 500 or tracebacks:
                    raise Found(f"{x[0]} -> {st} {str(j)[:200]}\n" + "".join(tracebacks[-1:]))
                if isinstance(j, dict) and j.get("job"):           # a background job (the demo BOM ...) must not
                    for _ in range(1200):                          # still be writing when the folder is removed
                        if call("GET", f"/api/job?id={j['job']}")[1].get("state") != "running":
                            break
                        time.sleep(0.05)
            _fuzz("web JSON endpoints", 300, 18, make, run, ())
        finally:
            srv.shutdown()
            SV.traceback.print_exc = real


def test_fuzz_mcp_tools():
    from ecadlib import mcp_server as M
    from ecadlib.app import Workspace
    skip = {"datasheet_batch", "a365_pull", "a365_status", "altium365_push", "visual_check", "librarian_run",
            "part_candidates", "upkeep_run", "agent_run", "propose_rules"}          # network or minutes-long: test_ui covers
    tools = [t for t in M.TOOLS if t["name"] not in skip]
    strs = ["", "x", "R1", "STM32F103C8T6", "../x", "/etc/passwd", "uploads", "symbol", "footprint", "approve", "a" * 200]
    tracebacks = []
    real = M.traceback.print_exc
    M.traceback.print_exc = lambda *a, **k: tracebacks.append(traceback.format_exc())
    with tempfile.TemporaryDirectory() as d:
        ws = Workspace(d, [str(ROOT / "examples/kicad_lib")], providers=["digikey"], offline=True)
        rels = [ws.save_upload("bom" + suf, b) for suf, b in BOMS]
        specs = sorted((ROOT / "ecadlib/package_specs").glob("*.json"))

        def gen(rnd, sch, name):
            t = sch.get("type")
            if "enum" in sch:
                return rnd.choice(sch["enum"])
            if name == "bom":
                return rnd.choice(rels + [rnd.choice(strs)])
            if t == "boolean":
                return rnd.random() < .5
            if t in ("integer", "number"):
                return rnd.choice([0, 1, -1, 3, 10 ** 9])
            if t == "array":
                return [gen(rnd, sch.get("items", {"type": "string"}), name) for _ in range(rnd.randrange(3))]
            if t == "object":
                return {rnd.choice(strs): rnd.choice(strs) for _ in range(rnd.randrange(3))}
            return rnd.choice(strs)

        def make(rnd):
            tool = rnd.choice(tools)
            sch = tool["inputSchema"]
            args = {p: gen(rnd, s, p) for p, s in sch.get("properties", {}).items()
                    if p in sch.get("required", []) or rnd.random() < .5}
            if tool["name"] in ("preview_footprint", "approve_footprint"):
                sp = json.loads(rnd.choice(specs).read_text())
                for k in rnd.sample(list(sp), 2):
                    sp[k] = rnd.choice([None, 0, -1, "x", [], 1e9, 0.0001])
                args["spec"] = sp
            return tool["name"], args

        def run(x):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                r = M.handle(ws, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": x[0], "arguments": x[1]}})
            if tracebacks or "result" not in r:
                raise Found(f"{x[0]}: {str(r)[:200]}\n" + "".join(tracebacks[-1:]))
        try:
            _fuzz("MCP tools", 300, 19, make, run, ())
        finally:
            M.traceback.print_exc = real


if __name__ == "__main__":
    if len(sys.argv) > 1:
        os.environ["ECADLIB_FUZZ_SCALE"] = sys.argv[1]
        SCALE = float(sys.argv[1])
        BUDGET = 3.5 * SCALE
    for n, f in list(globals().items()):
        if n.startswith("test_"):
            t = time.time()
            f()
            print(f"ok {n} {time.time() - t:.1f}s")
