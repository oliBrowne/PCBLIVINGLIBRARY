"""2026-10-26 bug hunt, web UI and the Workspace API behind it (ecadlib/ui/server.py, ecadlib/app.py, easy_setup,
userconfig). Offline. Run:  python3 -m pytest -q tests/test_hunt1026_webui.py   (or python -m tests.run_all)"""
import contextlib, io
from pathlib import Path

from tests.fixtures_lite import lite

ROOT = Path(__file__).resolve().parents[1]


@lite
def test_webui_01_altium_models_carry_a_height_and_a_3d_body(tmp_path):
    """The web UI's / MCP's "Build Altium models" converted KiCad footprints with HEIGHT 0 and no 3D body, unlike the
    librarian and `ecadlib kicad-import` (same footprint, same tables)."""
    from ecadlib import altium_lib as AL
    from ecadlib.app import Workspace
    ws = Workspace(tmp_path / "ws", [str(ROOT / "out" / "footprints")], providers=[], offline=True)
    up = ws.save_upload("bom.csv", b"Designator,Qty,Value,Footprint,MPN\n"
                                   b"R1,1,10k,ecadlib:R_0603_1608Metric,RC0603FR-0710KL\n"
                                   b"U1,1,X,ecadlib:SOIC-8_3.9x4.9mm_P1.27mm,NE555DR\n")
    with contextlib.redirect_stdout(io.StringIO()):
        r = ws.build_altium_models(up)
    assert set(r["footprints"]) == {"R_0603_1608Metric", "SOIC-8_3.9x4.9mm_P1.27mm"}, r
    for name, h in (("R_0603_1608Metric", 0.9), ("SOIC-8_3.9x4.9mm_P1.27mm", 1.75)):
        fp = AL.read_pcblib(ws.models / "PcbLib" / f"{name}.PcbLib")[0]
        assert float(str(fp["params"]["HEIGHT"]).replace("mil", "")) > 0, (name, fp["params"])
        assert any(rec["type"] == "body" for rec in fp["records"]), (name, [rec["type"] for rec in fp["records"]])


@lite
def test_webui_02_bad_json_field_types_are_400s_not_crashed_jobs(tmp_path):
    """sources / refresh of /api/librarian/run went into the job unchecked (true -> TypeError in the job, "Something
    went wrong"); a JSON Infinity (Python's json reads it) crashed int() in a job with OverflowError."""
    import json
    from ecadlib.app import Workspace
    from tests.test_ui import serve, wait_job
    ws = Workspace(tmp_path / "ws", [], providers=[], offline=True)
    bom = ws.save_upload("bom.csv", b"Designator,Qty,Value,MPN\nR1,1,10k,RC0603FR-0710KL\n")
    srv, call = serve(ws)
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            for body in ({"bom": bom, "sources": True}, {"bom": bom, "sources": "standard"}, {"bom": bom, "sources": [5]},
                         {"bom": bom, "refresh": 5}, {"bom": bom, "refresh": "R1"}, {"bom": bom, "refresh": [None]}):
                st, j = call("POST", "/api/librarian/run", json.dumps(body))
                assert st == 400 and "job" not in j, (body, st, j)
            for path, raw in (("/api/resolve/candidates", '{"bom": "%s", "part": "R1", "limit": Infinity}' % bom),
                              ("/api/ai/read", '{"bom": "%s", "approved_usd": NaN}' % bom),
                              ("/api/footprint/preview", '{"spec": {"name": "X", "body_length": Infinity}}')):
                st, j = call("POST", path, raw)
                assert st == 400 and "job" not in j and "JSON allows" in j["error"], (path, st, j)
            st, j = call("POST", "/api/librarian/run", json.dumps({"bom": bom, "sources": ["standard"], "refresh": True}))
            assert st == 200 and wait_job(call, j["job"])["state"] == "done", j
            st, j = call("POST", "/api/librarian/run", json.dumps({"bom": bom, "sources": None, "refresh": ["R1"]}))
            assert st == 200 and wait_job(call, j["job"])["state"] == "done", j
    finally:
        srv.shutdown()
        srv.server_close()


@lite
def test_webui_03_uploads_never_take_a_windows_device_name(tmp_path):
    """An upload named con.csv / NUL.xlsx / com1.csv / LPT9.tsv was stored under that name. On Windows those names are
    devices (CON = the console), so the BOM is lost on write and triage then reads the device."""
    import re
    from ecadlib.app import Workspace
    ws = Workspace(tmp_path / "ws", [], providers=[], offline=True)
    data = b"Designator,Qty,Value,MPN\nR1,1,10k,RC0603FR-0710KL\n"
    for n in ("con.csv", "CON", "nul.xlsx", "Aux.csv", "prn.tsv", "com1.csv", "LPT9.txt", "con.tar.csv", "COM¹.csv"):
        name = ws.save_upload(n, data).rsplit("/", 1)[1]
        assert not re.fullmatch(r"(?i)(con|prn|aux|nul|com[0-9¹²³]|lpt[0-9¹²³])(\..*)?", name), (n, name)
        assert Path(n).suffix.lower() == Path(name).suffix.lower(), (n, name)     # the reader still picks by the suffix
    for n in ("console.csv", "bom_con.csv", "com10.csv", "nullable.csv"):          # not devices: kept
        assert ws.save_upload(n, data).endswith("/" + n), n


@lite
def test_webui_04_settings_and_keys_written_from_two_requests_at_once_keep_both(tmp_path, monkeypatch):
    """settings.json / keys.json are read, changed and written back. The web UI serves each request in its own thread,
    so a key saved while the setup page saves (or Add to Altium and Add to KiCad at once) lost one of the changes."""
    import threading
    from ecadlib import easy_setup as ES, userconfig as UC
    monkeypatch.setenv("ECADLIB_HOME", str(tmp_path / "home"))
    for rnd in range(6):
        names = [f"K{rnd}_{i}" for i in range(6)]
        jobs = [threading.Thread(target=UC.set_key, args=(n, "v" + n)) for n in names]
        jobs += [threading.Thread(target=UC.save_settings, args=({n: 1},)) for n in names]
        jobs += [threading.Thread(target=ES._record_install, args=(n, "x")) for n in names]
        for t in jobs:
            t.start()
        for t in jobs:
            t.join()
        s = UC.load_settings()
        assert set(names) <= set(UC.stored_names()), (rnd, UC.stored_names())
        assert all(n in s and n in s["installed"] for n in names), (rnd, s)


@lite
def test_webui_05_one_damaged_triage_report_does_not_hide_the_others(tmp_path):
    """triage_history skips a damaged report (its own docstring), but a report whose JSON is valid with the wrong shape
    (a list, rows not a list) raised TypeError / AttributeError: Past BOM checks showed nothing at all."""
    from ecadlib.app import Workspace
    ws = Workspace(tmp_path / "ws", [], providers=[], offline=True)
    up = ws.save_upload("a.csv", b"Designator,Qty,Value,MPN\nR1,1,10k,X\n")
    with contextlib.redirect_stdout(io.StringIO()):
        good = ws.triage(up)["id"]
    for i, text in enumerate(("[1, 2]", '{"rows": 5}', '{"rows": [], "counts": [1]}', '"x"', '{"rows": [1], "counts": {}}')):
        (ws.root / "reports" / f"triage_bad{i}.json").write_text(text, encoding="utf-8")
        assert [t["id"] for t in ws.triage_history()["triages"]] == [good], text
        try:
            ws.triage_result(f"bad{i}")
        except ValueError as e:
            assert "damaged" in str(e), e
        else:
            raise AssertionError(f"triage_result read {text}")
    assert ws.triage_result(good)["rows"]


@lite
def test_webui_06_the_ui_sample_bom_looks_nothing_up_online(tmp_path, monkeypatch):
    """The web UI's "Try it with a sample BOM" says "Offline, no API key" and `ecadlib demo` is offline, but the UI ran
    the demo's triage and librarian with the workspace's own setting: on a normal (not --offline) workspace every
    sample part was looked up on easyeda.com."""
    import socket
    from ecadlib.app import Workspace
    from tests.test_ui import serve, wait_job
    tried = []

    def blocked(host, *a, **k):
        if host not in ("127.0.0.1", "localhost", "::1"):
            tried.append(host)
            raise socket.gaierror("network blocked in this test")
        return real(host, *a, **k)
    real = socket.getaddrinfo
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setenv("ECADLIB_HOME", str(tmp_path / "home"))
    for v in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
        monkeypatch.setenv(v, "")
    ws = Workspace(tmp_path / "ws", [])                    # what `ecadlib ui` and the desktop app make: not --offline
    srv, call = serve(ws)
    try:
        st, j = call("POST", "/api/demo", "{}")
        assert st == 200, j
        r = wait_job(call, j["job"], 120)
        assert r["state"] == "done" and r["result"]["parts"] == 7, r.get("error")
    finally:
        srv.shutdown()
        srv.server_close()
    assert tried == [], sorted(set(tried))


@lite
def test_webui_07_host_check_on_port_80_takes_the_browsers_host_without_a_port(tmp_path):
    """A browser leaves the default port out of Host (http://localhost/ sends "Host: localhost"), so `ecadlib ui
    --port 80` refused every request, the page itself included, as a DNS-rebinding attempt."""
    from types import SimpleNamespace
    from ecadlib.app import Workspace
    from ecadlib.ui.server import make_handler
    H = make_handler(Workspace(tmp_path / "ws", [], providers=[], offline=True))

    def ok(port, host):
        h = H.__new__(H)
        h.server, h.headers = SimpleNamespace(server_address=("127.0.0.1", port)), {"Host": host}
        return h._host_ok()
    assert ok(80, "localhost") and ok(80, "127.0.0.1") and ok(80, "localhost:80") and ok(8765, "127.0.0.1:8765")
    assert not ok(8765, "localhost") and not ok(80, "evil.example") and not ok(80, "evil.example:80") \
        and not ok(8765, "127.0.0.1:80") and not ok(80, None)


@lite
def test_webui_08_setup_restart_moves_only_to_a_real_workspace_folder(tmp_path, monkeypatch):
    """/api/setup/restart handed any JSON value to the desktop app's restart: {"workspace": 5} or "my ws" moved the
    server to a new folder "5" / "my ws" under whatever the current folder was (Workspace() makes it)."""
    from ecadlib import easy_setup as ES
    moved = []
    monkeypatch.setattr(ES, "RESTART", moved.append)
    good = tmp_path / "new ws"
    good.mkdir()
    for bad in (5, ["x"], {"a": 1}, "relative ws", "\0x", str(tmp_path / "missing"), "x" * 600):
        try:
            ES.POST_ROUTES["/api/setup/restart"](tmp_path, {"workspace": bad})
        except ValueError:
            pass
        else:
            raise AssertionError(f"restarted in {bad!r}")
    assert moved == []
    assert ES.POST_ROUTES["/api/setup/restart"](tmp_path, {"workspace": str(good)})["restarting"] and moved == [str(good)]
    assert ES.POST_ROUTES["/api/setup/restart"](tmp_path, {})["restarting"] and moved[-1] == str(tmp_path)


@lite
def test_webui_09_a_reject_recorded_during_a_librarian_run_is_not_undone_by_it(tmp_path, monkeypatch):
    """A librarian run (`ecadlib librarian`, the MCP tool, the web UI's Fill) read plan.json's decisions when it
    started and wrote its plan back at the end. A reject (or approval, or "release anyway") recorded meanwhile from
    another front end (the web UI's Approvals, `ecadlib approve`) took the plan lock and was written, then silently
    overwritten: the rejected part came back "ready", ready for the next Altium 365 push, while the audit log says
    rejected."""
    import json
    from ecadlib.app import Workspace
    from tests.ui_fixture import build
    monkeypatch.setenv("ECADLIB_HOME", str(tmp_path / "home"))
    with contextlib.redirect_stdout(io.StringIO()):
        ws, bom = build(tmp_path / "ws")
    plan = ws.root / "librarian" / "sample_bom" / "plan.json"
    ready = [i["name"] for i in json.loads(plan.read_text(encoding="utf-8")) if i["status"] == "ready"]
    rejected, approved = ready[0], ready[1]
    ui = Workspace(ws.root, [], providers=["digikey"], offline=True)          # another process in real life
    said = []

    def progress(n, total, label):                       # while the run looks its parts up
        if not said and str(label).startswith("["):
            said.append(ui.approve_part(bom, rejected, "reject", "Ann Lee", comment="wrong pinout")["decision"])
            said.append(ui.approve_part(bom, approved, "approve", "Ann Lee")["decision"])
    with contextlib.redirect_stdout(io.StringIO()):
        ws.librarian_run(bom, sources=["kicad", "standard"], progress=progress, ai=False)
    assert len(said) == 2, "the run never looked a part up"
    items = {i["name"]: i for i in json.loads(plan.read_text(encoding="utf-8"))}
    assert items[rejected]["status"] != "ready", items[rejected]["status"]
    assert [a["decision"] for a in items[rejected].get("approvals") or []] == ["reject"], items[rejected].get("approvals")
    assert [a["decision"] for a in items[approved].get("approvals") or []] == ["approve"], items[approved].get("approvals")


@lite
def test_webui_10_a_reject_recorded_during_the_datasheet_reads_is_not_undone(tmp_path, monkeypatch):
    """The same for the AI datasheet step (the web UI's "Read with AI", `ecadlib datasheet batch plan.json`): fill_plan
    read the plan, waited for the reads (a Message Batch can take an hour) and wrote its copy back."""
    import json
    from types import SimpleNamespace
    from ecadlib import datasheet as D
    from ecadlib.app import Workspace
    from tests.ui_fixture import build
    monkeypatch.setenv("ECADLIB_HOME", str(tmp_path / "home"))
    with contextlib.redirect_stdout(io.StringIO()):
        ws, bom = build(tmp_path / "ws")
    plan = ws.root / "librarian" / "sample_bom" / "plan.json"
    items = json.loads(plan.read_text(encoding="utf-8"))
    rejected = next(i["name"] for i in items if i["status"] == "ready")
    assert any(i["status"] == "held" for i in items)
    ui = Workspace(ws.root, [], providers=["digikey"], offline=True)
    said = []

    def log(msg):                                      # while the reads come back, part by part
        if not said:
            said.append(ui.approve_part(bom, rejected, "reject", "Ann Lee", comment="wrong pinout")["decision"])
    reader = SimpleNamespace(http=None, key=None)      # no model here: every read fails, which is fine for this
    D.fill_plan(plan, reader, ws.rules(), log=log)
    assert said, "fill_plan never got to a part"
    it = next(i for i in json.loads(plan.read_text(encoding="utf-8")) if i["name"] == rejected)
    assert it["status"] != "ready" and [a["decision"] for a in it.get("approvals") or []] == ["reject"], it
