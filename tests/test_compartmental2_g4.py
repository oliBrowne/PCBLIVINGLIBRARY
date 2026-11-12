"""Compartmental testing, round 2, group 4 (2026-11-12): money safety, the model layer, the web UI, bug reports and the
command line's messages, each tested on its own with hand-made inputs. Offline: no key, no network, nothing paid."""
import contextlib, io, json, math
from pathlib import Path

from ecadlib import ds_route as DR
from ecadlib.llm.openai_compat import OpenAICompatClient
from tests.fixtures_lite import lite


# ---------------------------------------------------------------- the cost of an answer with a damaged usage block
BAD_USAGE = [{"input_tokens": 1000, "output_tokens": float("nan")}, {"input_tokens": "1e3", "output_tokens": "lots"},
             [1, 2], "12", {"input_tokens": 1000, "output_tokens": float("inf")}, {"input_tokens": {"n": 1}}]


def test_a_damaged_usage_block_is_an_unknown_cost_not_a_crash():
    """A billed answer whose usage block a gateway garbled (NaN, a string, a list) made the read fail with ValueError
    after it was paid: the answer was thrown away and cost.jsonl got no line, so a run resumed under --max-total-usd
    counted it as $0. The cost is unknown instead (usd None: the cap keeps its hold, the total refuses)."""
    for u in BAD_USAGE:
        assert DR.usage_usd("claude-opus-5-5", u) is None, u
        rec = DR.attempt_record("P1", "P1", "claude-opus-5-5", {"usage": u}, "sync", 3, 3, "ok")
        assert rec["usd"] is None and rec["how"] == "usage", (u, rec)
        assert isinstance(rec["input_tokens"], int) and isinstance(rec["output_tokens"], int), rec
    ok = DR.usage_usd("claude-opus-5-5", {"input_tokens": 1000, "output_tokens": 100})
    assert ok and ok > 0 and math.isfinite(ok)
    assert DR.usage_usd("claude-opus-5-5", {"input_tokens": 1000.0, "output_tokens": 100}) == ok   # a float count


def test_an_openai_compatible_answer_with_a_damaged_usage_block_is_kept_at_an_unknown_cost():
    """The same on the OpenAI-compatible path: to_message raised ValueError on usage {"prompt_tokens": "40k"} (or NaN),
    so the paid answer never reached the reader."""
    for u in ({"prompt_tokens": "40k", "completion_tokens": 10}, {"prompt_tokens": 100, "completion_tokens": float("nan")},
              {"prompt_tokens": 100, "completion_tokens": 10, "prompt_tokens_details": {"cached_tokens": "x"}}):
        resp = {"id": "c1", "model": "gpt-test", "usage": u,
                "choices": [{"message": {"role": "assistant", "content": "{}"}, "finish_reason": "stop"}]}
        msg = OpenAICompatClient.to_message(resp, "gpt-test")
        assert msg["content"][0]["text"] == "{}", msg
        assert msg["usage"] is not None and DR.usage_usd("claude-opus-5-5", msg["usage"]) is None, (u, msg["usage"])
    resp = {"id": "c1", "usage": {"prompt_tokens": 100, "completion_tokens": 10, "prompt_tokens_details": {"cached_tokens": 40}},
            "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]}
    assert OpenAICompatClient.to_message(resp, "m")["usage"] == {"input_tokens": 60, "cache_read_input_tokens": 40,
                                                                 "output_tokens": 10}


@lite
def test_a_garbled_usage_block_from_a_fake_server_is_booked_in_the_cost_ledger(tmp_path):
    """End to end against a local fake Anthropic server: the answer with a garbled usage block is kept (the part is
    built from it) and cost.jsonl has its line, priced unknown, so --max-total-usd refuses to guess."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from tests import e2e_support as E
    ans = json.loads((E.E2E / "anthropic" / "datasheet_DSPART16.json").read_text())
    ans["usage"] = {"input_tokens": "1e3", "output_tokens": "lots"}
    calls = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("content-length") or 0))
            calls.append(self.path)
            st, raw = (200, json.dumps(ans).encode()) if self.path.endswith("/v1/messages") else (404, b"{}")
            self.send_response(st)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        w = tmp_path / "w"
        w.mkdir()
        (w / "bom.csv").write_text(",".join(E.HEADER) + "\nU1,1,DSPART17,,ACME,DSPART17,,Buck converter VQFN-16,"
                                   + E.DS.format("dspart17") + "\n")
        E.seed_datasheet(w / "cache", "DSPART17")
        (w / "ecadlib.toml").write_text('[datasheet]\ncross_check = "off"\nmax_cost_usd = 5\n[ai]\nfeedback_retry = false\n')
        env = E.clean_env(ANTHROPIC_API_KEY="sk-test-fake", ANTHROPIC_BASE_URL=f"http://127.0.0.1:{srv.server_address[1]}")
        p = E.ecadlib(w, "librarian", "bom.csv", "-o", "lib", "--cache", "cache", "--rules", "ecadlib.toml", env=env,
                      check=False)
    finally:
        srv.shutdown()
        srv.server_close()
    assert p.returncode in (0, 2), (p.stdout[-2000:], p.stderr[-2000:])
    assert calls.count("/v1/messages") == 1, calls
    led = [json.loads(x) for x in (w / "lib" / "datasheet" / "cost.jsonl").read_text().splitlines()]
    assert len(led) == 1 and led[0]["usd"] is None, led
    tried = json.loads((w / "lib" / "plan.json").read_text())[0]["librarian"]["tried"]
    assert not any("ValueError" in t for t in tried), tried
    from ecadlib import ai_cost as AC
    assert AC.spent(w / "lib")["unpriced"] == 1


# ---------------------------------------------------------------- eval --repeat under one --max-cost-usd
@lite
def test_eval_repeats_share_the_runs_cost_cap(tmp_path, monkeypatch):
    """`eval run --repeat 3 --max-cost-usd 1` gave each live repeat its own $1, so the command could spend $3. The
    repeats now get what the earlier runs of this command left."""
    from ecadlib.eval import runner as R
    seen, real = [], R.run

    def fake_librarian(bom, libs, lib, **kw):
        led = Path(lib) / "datasheet" / "cost.jsonl"
        led.parent.mkdir(parents=True, exist_ok=True)
        with open(led, "a", encoding="utf-8") as f:            # this run "spent" $0.40
            f.write(json.dumps({"part": "X", "model": "claude-opus-5-5", "usd": 0.4, "how": "usage"}) + "\n")
        return []

    def spy(*a, **kw):
        seen.append(kw.get("max_cost_usd"))
        return real(*a, **kw)
    monkeypatch.setattr(R.L, "run", fake_librarian)
    monkeypatch.setattr(R, "run", spy)
    logs = []
    R.run("kicad-v1", str(tmp_path / "e"), limit=1, offline=True, cache=str(tmp_path / "c"), repeat=4, max_cost_usd=1.0,
          log=logs.append)
    assert seen[0] == 1.0 and seen[1] == 0.6 and seen[2] == 0.2, seen      # 1 - 0.4, then 1 - 0.8
    assert len(seen) == 3, (seen, logs)                                     # the 4th: nothing left, not run
    assert any("cap" in str(x) and "repeat" in str(x) for x in logs), logs
