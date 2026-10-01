# The engine of `cerebro observe`. argv is <sessions-root> <target-id>
# <self-id> <state-dir> <window-secs> <quiet-secs>. It lets ANY cerebro
# session look over the shoulder of ANOTHER cerebro session's live --pair
# children (the <target-id> names the orchestrator session, NOT a child).
# It resolves the target -- the named session, else the most recently
# touched OTHER session that currently has live paired children -- then
# tails, concurrently, that session's OWN transcript (transcript.jsonl)
# ALONGSIDE every one of its live paired children (children/*.steer.fifo
# whose pipe a child is still reading -> the .jsonl beside it). It returns
# ONE batch of new activity and exits, so the caller can narrate and call
# again. A per-target cursor under the observer's own session dir makes
# successive calls advance: a child that is live when first seen is caught
# up from the top; one already finished when first seen is skipped. Each
# call blocks up to <window> seconds, returning early once it has a batch
# and things go quiet for <quiet> seconds; STATUS is "active" while any
# child is live (call again) or "done" when none are. Read-only: it never
# writes to a child, so observing never disturbs the agents.
#
# Native adapter events are rendered without issuing backend state queries.

import glob, json, os, sys, time

sessions_root = sys.argv[1]
target_id     = sys.argv[2]
self_id       = sys.argv[3]
state_dir     = sys.argv[4]
window        = float(sys.argv[5])
quiet         = float(sys.argv[6])
mode          = sys.argv[7] if len(sys.argv) > 7 else ""
if target_id and (os.path.basename(target_id) != target_id or target_id in (".", "..")):
    sys.exit("observe: target must be a Cerebro session ID")

def fifo_live(fifo):
    try:
        fd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
    except OSError:
        return False
    os.close(fd)
    return True

def log_of(fifo):
    return fifo[:-len(".steer.fifo")] + ".jsonl"

def fifo_of(log):
    return log[:-len(".jsonl")] + ".steer.fifo"

def live_children(sess_dir):
    out = []
    for fifo in sorted(glob.glob(os.path.join(sess_dir, "children", "*.steer.fifo"))):
        if fifo_live(fifo):
            out.append(log_of(fifo))
    return out

def last_touched(sess_dir):
    try:
        with open(os.path.join(sess_dir, "metadata.json")) as fh:
            data = json.load(fh)
        return data.get("last_touched") or data.get("created_at") or ""
    except Exception:
        try:
            return "%020.6f" % os.path.getmtime(sess_dir)
        except OSError:
            return ""

def emit_empty(msg, status="done", tid=""):
    print("=== OBSERVE%s ===" % ((" session " + tid) if tid else ""))
    print(msg)
    print("=== OBSERVE STATUS: %s ===" % status)

# --- resolve the target cerebro session ---------------------------------------
if target_id:
    target = os.path.join(sessions_root, target_id)
    if not os.path.isdir(target):
        if mode == "probe":
            sys.exit(3)
        emit_empty("cerebro: no such session: %s" % target_id)
        sys.exit(0)
else:
    cands = []
    try:
        names = os.listdir(sessions_root)
    except OSError:
        names = []
    for name in names:
        if name == self_id:
            continue
        d = os.path.join(sessions_root, name)
        if os.path.isdir(d) and live_children(d):
            cands.append((last_touched(d), name))
    if not cands:
        if mode == "probe":
            sys.exit(3)
        emit_empty("cerebro: no other cerebro session has live paired children to "
                   "observe. Start one with --pair (e.g. \"cerebro execute <repo> "
                   "... --pair\") in another session, then observe again.")
        sys.exit(0)
    cands.sort(reverse=True)
    target_id = cands[0][1]
    target = os.path.join(sessions_root, target_id)

# Startup probes bind the same target that observation would select, without
# draining its activity or minting an observer session before work exists.
if mode == "probe":
    if not live_children(target):
        sys.exit(3)
    print(target_id)
    sys.exit(0)

target_children = os.path.abspath(os.path.join(target, "children"))
# The orchestrator session itself, tailed alongside its children so the
# observer sees the whole picture: the conductor and the orchestra.
session_log = os.path.abspath(os.path.join(target, "transcript.jsonl"))

# --- cursor state, persisted per target under the observer session ------------
try:
    os.makedirs(state_dir, exist_ok=True)
except OSError:
    pass
state_path = os.path.join(state_dir, target_id + ".json")
try:
    with open(state_path) as fh:
        cursor = json.load(fh)
except Exception:
    cursor = {}

def label_of(log):
    base = os.path.basename(log)
    return base[:-len(".jsonl")] if base.endswith(".jsonl") else base

def track_label(log):
    return ("orchestrator session" if os.path.abspath(log) == session_log
            else label_of(log))

def under_target(log):
    return os.path.dirname(os.path.abspath(log)) == target_children

def preview(s, n):
    s = " ".join((s or "").split())
    return s[:n] + ("..." if len(s) > n else "")

def render_claude(ev, out):
    t = ev.get("type")
    if t == "assistant":
        for item in ev.get("message", {}).get("content", []) or []:
            if item.get("type") == "text":
                txt = (item.get("text") or "").strip()
                if txt:
                    out.append("says: " + preview(txt, 2000))
            elif item.get("type") == "tool_use":
                name = item.get("name", "?")
                if name in ("Read", "Grep", "Glob", "LS", "NotebookRead"):
                    continue
                inp = item.get("input", {}) or {}
                if name == "TodoWrite" and isinstance(inp.get("todos"), list):
                    items = []
                    for td in inp["todos"]:
                        mark = {"completed": "x", "in_progress": ">"}.get(
                            td.get("status"), " ")
                        items.append("[%s] %s" % (mark, td.get("content") or ""))
                    if items:
                        out.append("plan: " + preview(" | ".join(items), 1200))
                    continue
                tgt = (inp.get("description") or inp.get("file_path") or
                       inp.get("pattern") or inp.get("path") or
                       inp.get("query") or inp.get("command") or "")
                if isinstance(tgt, list):
                    tgt = " ".join(map(str, tgt))
                tgt = preview(str(tgt), 200)
                body = inp.get("new_string")
                if body is None:
                    body = inp.get("content")
                if isinstance(body, str) and body.strip():
                    out.append(("%s %s :: %s" % (name, tgt, preview(body, 1600))).strip())
                else:
                    out.append(("%s %s" % (name, tgt)).strip())
    elif t == "result":
        sub = ev.get("subtype")
        out.append("(turn complete)" if not sub or sub == "success" else "(ended: %s)" % sub)

def render_opencode(ev, out):
    t = ev.get("type")
    part = ev.get("part") or {}
    if t == "text":
        txt = (part.get("text") or "").strip()
        if txt:
            out.append("says: " + preview(txt, 2000))
    elif t == "tool_use":
        name = part.get("tool", "?")
        if name in ("read", "grep", "glob", "list"):
            return
        state = part.get("state") or {}
        inp = state.get("input") or {}
        if name == "todowrite" and isinstance(inp.get("todos"), list):
            items = []
            for td in inp["todos"]:
                mark = {"completed": "x", "in_progress": ">"}.get(td.get("status"), " ")
                items.append("[%s] %s" % (mark, td.get("content") or ""))
            if items:
                out.append("plan: " + preview(" | ".join(items), 1200))
            return
        tgt = (state.get("title") or inp.get("description") or
               inp.get("filePath") or inp.get("file_path") or
               inp.get("pattern") or inp.get("path") or
               inp.get("query") or inp.get("command") or inp.get("url") or "")
        if isinstance(tgt, list):
            tgt = " ".join(map(str, tgt))
        tgt = preview(str(tgt), 200)
        body = inp.get("newString")
        if body is None:
            body = inp.get("new_string")
        if body is None:
            body = inp.get("content")
        if isinstance(body, str) and body.strip():
            out.append(("%s %s :: %s" % (name, tgt, preview(body, 1600))).strip())
        else:
            out.append(("%s %s" % (name, tgt)).strip())
    elif t == "step_finish":
        reason = part.get("reason")
        if reason in (None, "stop", "tool-calls"):
            out.append("(turn complete)")
        else:
            out.append("(ended: %s)" % reason)
    elif t == "error":
        err = ev.get("error") or {}
        data = err.get("data") or {}
        out.append("(error: %s)" % preview(data.get("message") or err.get("name") or "error", 200))

def render_codex(ev, out):
    kind = ev.get("type")
    item = ev.get("item") or {}
    if kind in ("item.started", "item.completed"):
        if item.get("type") == "agent_message" and kind == "item.completed":
            text = (item.get("text") or "").strip()
            if text:
                out.append("says: " + preview(text, 2000))
        elif item.get("type") == "command_execution":
            out.append("command: " + preview(item.get("command"), 400))
            if item.get("aggregatedOutput"):
                out.append("output: " + preview(item["aggregatedOutput"], 2000))
        elif item.get("type") == "file_change":
            for change in item.get("changes", []):
                out.append("file: " + str(change["path"]) + " :: " + preview(change.get("diff"), 2000))
        elif item.get("type") == "mcp_tool_call":
            out.append("tool: " + item["server"] + "/" + item["tool"] + " "
                       + preview(json.dumps(item.get("arguments")), 400))
    elif kind == "turn.completed":
        out.append("(turn complete)")
    elif kind == "turn.failed":
        out.append("(error: " + preview(json.dumps(ev.get("error")), 2000) + ")")

def render(ev, out):
    # The orchestrator session's own transcript.jsonl speaks a different
    # dialect than a child's log: {kind:"user"} for prompts it received and
    # {kind:"event"} for the cerebro actions it took.
    kind = ev.get("kind")
    if kind == "user":
        txt = (ev.get("text") or "").strip()
        if txt:
            out.append("user: " + preview(txt, 2000))
        return
    if kind == "event":
        what = ev.get("what") or "?"
        detail = (ev.get("detail") or "").strip()
        out.append(("%s: %s" % (what, preview(detail, 300))) if detail else what)
        return
    if "sessionID" in ev or ev.get("type") in ("step_start", "step_finish"):
        render_opencode(ev, out)
    elif ev.get("type") in ("thread.started", "item.started", "item.completed", "turn.started", "turn.completed", "turn.failed", "progress"):
        render_codex(ev, out)
    else:
        render_claude(ev, out)

def drain(log):
    out = []
    try:
        size = os.path.getsize(log)
    except OSError:
        return out
    if log not in cursor:
        # First sight: catch a live child up from the top; skip one already done.
        cursor[log] = 0 if fifo_live(fifo_of(log)) else size
    pos = cursor[log]
    if pos > size:        # log was truncated / rotated
        pos = 0
    try:
        with open(log, "rb") as fh:
            fh.seek(pos)
            data = fh.read()
    except OSError:
        return out
    nl = data.rfind(b"\n")
    if nl < 0:            # no complete line yet
        return out
    complete = data[:nl + 1]
    cursor[log] = pos + len(complete)
    for raw in complete.split(b"\n"):
        raw = raw.strip()
        if not raw:
            continue
        try:
            ev = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            continue
        render(ev, out)
    return out

# --- collect one batch --------------------------------------------------------
acc = []                 # ordered (label, line)
last_new = None
ever_live = set()
finished_noted = set()
deadline = time.monotonic() + window
live = live_children(target)
while True:
    live = live_children(target)
    live_labels = set(label_of(l) for l in live)
    ever_live |= live_labels
    # The session's own transcript first, THEN every live child PLUS any
    # child we already track (to flush the final lines of one that just
    # finished), de-duplicated, order-stable.
    cand = list(dict.fromkeys([session_log] + live + [l for l in cursor if under_target(l)]))
    for log in cand:
        for ln in drain(log):
            acc.append((track_label(log), ln))
            last_new = time.monotonic()
    for lab in sorted(ever_live - live_labels):
        if lab not in finished_noted:
            finished_noted.add(lab)
            acc.append((lab, "(this agent has finished)"))
            last_new = time.monotonic()
    now = time.monotonic()
    if not live:
        status = "done"; break
    if acc and last_new is not None and (now - last_new) >= quiet:
        status = "active"; break
    if now >= deadline:
        status = "active"; break
    time.sleep(0.5)

try:
    with open(state_path, "w") as fh:
        json.dump(cursor, fh)
except OSError:
    pass

# --- emit the batch -----------------------------------------------------------
print("=== OBSERVE session %s ===" % target_id)
if acc:
    cur = None
    for lab, ln in acc:
        if lab != cur:
            cur = lab
            fifo = os.path.join(target_children, lab + ".steer.fifo")
            hdr = "[%s" % lab
            if os.path.exists(fifo):
                hdr += "  steer-pipe: %s" % fifo
            print(hdr + "]")
        print(ln)
else:
    print("(no new activity in the last %gs)" % window)
print("=== OBSERVE STATUS: %s ===" % status)
live_now = sorted(label_of(l) for l in live_children(target))
if live_now:
    print("live: " + ", ".join(live_now))
