"""2026-10-26 superpowers bug hunt (bommcp): BOM import / triage and MCP-vs-CLI parity.
Plain functions: python -m tests.run_all test_hunt1026_bommcp"""
import contextlib, io, json

from ecadlib import bom_triage as T
from tests.fixtures_lite import lite


def _read(f, text):
    f.write_bytes(text.encode("utf-8"))
    with contextlib.redirect_stderr(io.StringIO()):
        return T.read_bom(f)


# ---------------------------------------------------------------- bommcp-01: a millimetre case-code column
@lite
def test_metric_case_code_column_is_not_read_as_an_imperial_size(tmp_path):
    # 'Case Code (mm)' 0603 is the metric code of an imperial 0201 part: read as the footprint it was an 0603, and the
    # librarian built a ready 0603 land for a 0201 capacitor
    lines, m = _read(tmp_path / "mm.csv", "Designator,Qty,Value,Manufacturer,MPN,Case Code (mm)\n"
                                          "C1,1,100nF,Acme,XYZCAP100N,0603\nR1,1,10k,Acme,XYZRES10K,1608\n"
                                          "R2,1,1k,Acme,XYZRES1K,1206\nU1,1,LDO,Acme,XYZLDO,SOT-23-5\n")
    from ecadlib.kicad_index import package_of
    by = {l["designator"]: l for l in lines}
    assert package_of(by["C1"].get("footprint")) == "0201", by["C1"]
    assert package_of(by["R1"].get("footprint")) == "0603", by["R1"]
    # 1206 is no metric chip code: which size it is cannot be told, so it is not used (the part is held, not guessed)
    assert not by["R2"].get("footprint") and "mm" in by["R2"].get("bom_note", ""), by["R2"]
    assert by["U1"]["footprint"] == "SOT-23-5", by["U1"]                  # a package name is not a size code


@lite
def test_imperial_case_column_wins_over_the_metric_one_in_either_order(tmp_path):
    for hdr, row in (("Case Code (mm),Case Code (in)", "0603,0201"), ("Case Code (in),Case Code (mm)", "0201,0603")):
        lines, m = _read(tmp_path / "both.csv", f"Designator,Qty,Value,MPN,{hdr}\nC1,1,100nF,XYZCAP100N,{row}\n")
        assert m["footprint"] == "Case Code (in)", (hdr, m)
        assert lines[0]["footprint"] == "0201", (hdr, lines)


# ---------------------------------------------------------------- bommcp-02: designators on two lines
def test_ref_keys_reads_ranges_as_count_refs_does():
    # shared_designators (R2 = 10k on one line and R2 = 1k on another) expands with ref_keys, which knew only 'R1-R4':
    # 'R1..R4', 'R1 to R4', 'R1:R4', a reversed 'R5-R1' and 'R1/R2' were missed (or kept whole as 'R1..R4')
    for s, want in (("R1..R4", {"R1", "R2", "R3", "R4"}), ("R1 to R3", {"R1", "R2", "R3"}), ("R1:R3", {"R1", "R2", "R3"}),
                    ("R5-R3", {"R3", "R4", "R5"}), ("R1/R2", {"R1", "R2"}), ("R1&R2+R3", {"R1", "R2", "R3"}),
                    ("U1A, U1B", {"U1"}), ("R1_CH1, R1_CH2", {"R1_CH1", "R1_CH2"}), ("C1~C3", {"C1", "C2", "C3"})):
        assert T.ref_keys(s) == want, (s, T.ref_keys(s))
        assert T.count_refs(s) == len(want), (s, T.count_refs(s))
    lines = [{"designator": "R1..R4", "mpn": "A"}, {"designator": "R2", "mpn": "B"}, {"designator": "R9-R5", "mpn": "C"},
             {"designator": "R7", "mpn": "D"}]
    assert set(T.shared_designators(lines)) == {1, 2, 3, 4}, T.shared_designators(lines)


@lite
def test_workspace_triage_says_a_designator_is_on_two_lines_as_the_cli_does(tmp_path, monkeypatch):
    # the web UI / MCP triage_bom ran triage() one line at a time, so no line ever saw the others and the CLI's
    # 'designator on two lines' note never reached them
    from ecadlib.app import Workspace
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    ws = Workspace(str(tmp_path / "ws"), [], [], True)
    (ws.root / "b.csv").write_text("Designator,Qty,Value,MPN\nR1,1,10k,RC0603FR-0710KL\n\"R2,R3\",2,1k,RC0603FR-071KL\n"
                                   "R3,1,100R,RC0603FR-07100RL\n", encoding="utf-8")
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        r = ws.triage("b.csv")
    notes = {x["line"]: x["notes"] for x in r["rows"]}
    assert "R3 is also on line 3" in notes[2] and "R3 is also on line 2" in notes[3], notes
    assert "designator on two lines" not in notes[1], notes


# ---------------------------------------------------------------- bommcp-03: merged cells in an .xlsx BOM
def _merged_book(path, rows, merges):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    for m in merges:
        ws.merge_cells(m)
    wb.save(path)
    return path


@lite
def test_xlsx_cells_merged_down_over_one_part_fill_every_row(tmp_path, monkeypatch):
    # a hand-made BOM: one row per designator, the part's value / maker / MPN merged down over its rows. Only the top
    # cell of a merge holds the text, so R2 and R3 came out with no part at all
    f = _merged_book(tmp_path / "m.xlsx", [["Designator", "Qty", "Value", "Manufacturer", "MPN", "Description"],
                                          ["R1", 1, "10k", "Yageo", "RC0603FR-0710KL", "thick film"],
                                          ["R2", 1, None, None, None, None], ["R3", 1, None, None, None, None],
                                          ["C1", 1, "100n", "Murata", "GRM188R71H104KA93D", "X7R"]],
                     ["C2:C4", "D2:D4", "E2:E4"])
    for plain in (False, True):                       # pandas + openpyxl, and the standard-library reader
        if plain:
            def no_openpyxl(*a, **k):
                raise ImportError("Missing optional dependency 'openpyxl'")
            monkeypatch.setattr(T.pd, "read_excel", no_openpyxl)
        lines, _ = _read_xlsx(f)
        got = [(l["designator"], l.get("value"), l.get("manufacturer"), l.get("mpn"), l.get("description")) for l in lines]
        assert got == [("R1", "10k", "Yageo", "RC0603FR-0710KL", "thick film"), ("R2", "10k", "Yageo", "RC0603FR-0710KL", ""),
                       ("R3", "10k", "Yageo", "RC0603FR-0710KL", ""), ("C1", "100n", "Murata", "GRM188R71H104KA93D", "X7R")], \
            (plain, got)


@lite
def test_xlsx_merged_qty_and_merges_across_columns_are_not_copied(tmp_path):
    # a Qty merged over the rows is the group's count (the rows below continue its designator list, as before), and a
    # cell merged across columns (a Description over the MPN column) must never become a part number
    f = _merged_book(tmp_path / "q.xlsx", [["Designator", "Qty", "MPN", "Description"],
                                          ["R1", 3, "RC0603FR-0710KL", "10k"], ["R2", None, None, None], ["R3", None, None, None],
                                          ["U1", 1, "see note", None], ["U2", 1, None, None]],
                     ["B2:B4", "C2:C4", "C5:D6"])
    lines, _ = _read_xlsx(f)
    got = [(l["designator"], l.get("qty"), l.get("mpn")) for l in lines]
    assert got[0] == ("R1, R2, R3", "3", "RC0603FR-0710KL"), got
    assert ("U2", "1", "") in got, got


def _read_xlsx(f):
    with contextlib.redirect_stderr(io.StringIO()):
        return T.read_bom(f)


# ---------------------------------------------------------------- bommcp-04..06: JSON-RPC over stdio and HTTP
def _ws(tmp_path):
    from ecadlib.app import Workspace
    from ecadlib import mcp_server as M
    ws = Workspace(str(tmp_path / "ws"))
    ws.mcp_options = M.Options(wait=30)
    return ws


@lite
def test_an_infinite_integer_argument_is_a_tool_error_not_an_internal_error(tmp_path):
    # json.loads reads 1e999 as inf: int(inf) raised OverflowError inside handle(), so the client got a JSON-RPC
    # 'internal error' (and a traceback on stderr) instead of the usual argument message it can act on
    from ecadlib import mcp_server as M
    ws = _ws(tmp_path)
    for tool, args in (("job_status", '{"id": "x", "wait_seconds": 1e999}'),
                       ("part_candidates", '{"bom": "b.csv", "part": "P", "limit": -1e999}')):
        line = '{"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "%s", "arguments": %s}}' % (tool, args)
        with contextlib.redirect_stderr(io.StringIO()):
            r = M.handle(ws, json.loads(line))
        assert r.get("result", {}).get("isError") is True, r
        assert "invalid argument" in r["result"]["content"][0]["text"], r


@lite
def test_http_body_with_a_byte_order_mark_is_read_as_stdio_reads_it(tmp_path):
    import http.client
    from ecadlib import mcp_http as H
    srv, url = H.serve_in_thread(_ws(tmp_path), token="t0k")
    try:
        c = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=30)
        c.request("POST", "/mcp", body="﻿".encode() + b'{"jsonrpc": "2.0", "id": 1, "method": "ping"}',
                  headers={"Authorization": "Bearer t0k", "Content-Type": "application/json"})
        r = c.getresponse()
        assert r.status == 200 and json.loads(r.read()) == {"jsonrpc": "2.0", "id": 1, "result": {}}
    finally:
        srv.shutdown()
        srv.server_close()


@lite
def test_http_chunked_body_is_read_not_taken_for_the_next_request(tmp_path):
    # a body sent with Transfer-Encoding: chunked (no Content-Length) was read as empty: the answer was 'invalid
    # request' and the chunk lines were then parsed as a second HTTP request ('400 Bad request syntax')
    import socket
    from ecadlib import mcp_http as H
    srv, url = H.serve_in_thread(_ws(tmp_path), token="t0k")
    try:
        body = b'{"jsonrpc": "2.0", "id": 5, "method": "ping"}'
        s = socket.create_connection(("127.0.0.1", srv.server_address[1]), timeout=30)
        s.sendall(b"POST /mcp HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer t0k\r\nContent-Type: application/json\r\n"
                  b"Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
                  + b"%x\r\n" % 10 + body[:10] + b"\r\n" + b"%x\r\n" % (len(body) - 10) + body[10:] + b"\r\n0\r\n\r\n")
        out = b""
        while True:
            b = s.recv(65536)
            if not b:
                break
            out += b
        s.close()
        text = out.decode("latin-1")
        assert text.startswith("HTTP/1.1 200") and text.count("HTTP/1.1") == 1, text
        assert json.loads(text.split("\r\n\r\n", 1)[1]) == {"jsonrpc": "2.0", "id": 5, "result": {}}, text
        for chunks, status in ((b"zz\r\nabc\r\n0\r\n\r\n", "400"), (b"%x\r\n" % (H.MAX_BODY + 1), "413")):
            s = socket.create_connection(("127.0.0.1", srv.server_address[1]), timeout=30)
            s.sendall(b"POST /mcp HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer t0k\r\n"
                      b"Transfer-Encoding: chunked\r\n\r\n" + chunks)
            got = s.recv(65536).decode("latin-1")
            s.close()
            assert got.startswith(f"HTTP/1.1 {status}"), got
    finally:
        srv.shutdown()
        srv.server_close()
