"""Terminal views for the library agent (`ecadlib agent status`, `ecadlib queue list`), kept out of agent.py /
agent_queue.py so those stay about the work. Shared voice: docs/ui-voice.md; output layer: ecadlib.term."""
from . import messages as M, term as TM


def agent_status(st, workspace, t=None):
    """status.json (+ queue counts, batches) -> a few lines: state, last run, queue, batches, next step."""
    t = t or TM.Term()
    state = st.get("state") or "never run"
    look = {"idle": "ok", "running": "working", "starting": "working", "error": "failed", "stopped": "skipped",
            "disabled": "skipped", "never run": "skipped"}.get(state, "info")
    t.write(t.bold("Library agent: ") + t.status(look, state)
            + (f"  (last heartbeat {st['heartbeat']})" if st.get("heartbeat") else ""))
    if st.get("last_error"):
        t.note(M.clean(str(st["last_error"]))[:200], "see the agent log, fix the cause, then `ecadlib agent run --dry-run`")
    lr = st.get("last_run") or {}
    if lr and not t.quiet:
        t.write(f"  last run   {lr.get('finished') or lr.get('started') or '?'}: {lr.get('detected', 0)} changes seen, "
                f"{lr.get('built', 0)} built, {lr.get('review', 0)} for review, {lr.get('failed', 0)} failed, "
                f"{lr.get('pushed', 0)} released")
    q = st.get("queue") or {}
    if not t.quiet:
        t.write("  queue      " + (", ".join(f"{v} {_qword(k)}" for k, v in sorted(q.items())) if q else "empty") if "queue_error" not in st
                else f"  queue      unreadable ({M.clean(st['queue_error'])})")
        bs = st.get("batches") or []
        if bs:
            t.write(f"  batches    {len(bs)}; latest: " + ", ".join(
                f"{b.get('id')} ({b.get('status', 'open')}{', ' + b['pr_url'] if b.get('pr_url') else ''})" for b in bs[-3:]))
    nxt = []
    if q.get("review"):
        nxt.append((f"ecadlib queue --workspace {workspace} list --status review", f"{q['review']} part(s) waiting for review"))
    if state == "never run":
        nxt.append((f"ecadlib agent --workspace {workspace} run --dry-run", "see what the agent would do, change nothing"))
    if nxt and not t.quiet:
        t.write("next:")
        t.table([[c, f"# {w}"] for c, w in nxt])


def _qword(status):
    return "waiting for review" if status == "review" else M.status_word(status)


def queue_list(items, summary, where, t=None):
    """Queue items newest first: status, part, what kind of change, the prover's verdict, batch."""
    t = t or TM.Term()
    look = {"review": "signoff", "approved": "ready", "pushed": "ready", "rejected": "failed", "failed": "failed",
            "building": "working", "pending": "working"}
    rows = []
    for it in items:
        s = summary(it)
        rows.append([s["id"], t.status(look.get(s["status"], "info"), _qword(s["status"])), s["key"], s["kind"],
                     s["verdict"] or "", s["batch"] or ""])
    if rows:
        t.table(rows, headers=["id", "status", "part", "change", "check", "batch"])
    counts = {}
    for it in items:
        counts[it.get("status")] = counts.get(it.get("status"), 0) + 1
    words = {}
    for k, v in counts.items():
        words[_qword(k)] = words.get(_qword(k), 0) + v
    t.summary(f"{M.plural(len(items), 'item')} in the review queue" + (": " + ", ".join(f"{v} {k}" for k, v in words.items()) if items else ""),
              next=[("ecadlib queue approve ID --user NAME", "sign off a part"), ("ecadlib queue show ID", "everything about one item")]
              if counts.get("review") else [])
    t.detail(f"  queue folder: {where}")
