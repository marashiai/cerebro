# Capture closing messages and resumable identities from native child streams.
# Session identities are stored as soon as the native conversation is durable.
import json, os, select, sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from child_store_lib import _now_iso, store_upsert

result_path = sys.argv[1] if len(sys.argv) > 1 else ""
id_path = sys.argv[2] if len(sys.argv) > 2 else ""
store_file = sys.argv[3] if len(sys.argv) > 3 else ""
child_key = sys.argv[4] if len(sys.argv) > 4 else ""

saw_any_event = False
saw_error = False
error_msg = ""
session_id = None
provider = sys.argv[5] if len(sys.argv) > 5 else None
tool_summary_open = True

result_text = ""
result_subtype = None


def emit_tool_summary(line):
    global tool_summary_open
    if not tool_summary_open:
        return
    try:
        sys.stderr.write(line)
        sys.stderr.flush()
    except (BrokenPipeError, OSError):
        # The orchestrator sometimes previews `cerebro ... 2>&1 | head -6`.
        # A closed preview pipe must not kill this parser, because that would
        # also make tee stop draining the child's stdout and freeze the child log.
        tool_summary_open = False
        try:
            sys.stderr = open(os.devnull, "w")
        except OSError:
            pass


def record_session(sid):
    if not sid:
        return
    if id_path:
        try:
            with open(id_path, "w") as f:
                f.write(sid)
        except OSError:
            pass
    if store_file and child_key:
        store_upsert(store_file, child_key,
                     {"id": sid, "provider": provider, "updated_at": _now_iso()})


def handle_claude(ev):
    global session_id, result_text, result_subtype, saw_error, error_msg
    t = ev.get("type")
    if t == "system" and ev.get("subtype") == "init":
        sid = ev.get("session_id")
        if sid and session_id is None:
            session_id = sid
            record_session(sid)
        return
    if t == "assistant":
        for item in ev.get("message", {}).get("content", []) or []:
            if item.get("type") == "tool_use":
                name = item.get("name", "?")
                inp = item.get("input", {}) or {}
                target = (
                    inp.get("description") or inp.get("file_path") or
                    inp.get("pattern") or inp.get("path") or
                    inp.get("query") or inp.get("command") or ""
                )
                if isinstance(target, list):
                    target = " ".join(map(str, target))
                target = str(target).replace("\n", " ").strip()
                if len(target) > 120:
                    target = target[:120] + "..."
                clr = "\r\033[2K" if sys.stderr.isatty() else ""
                emit_tool_summary(f"{clr}  {name}: {target}\n")
        return
    if t == "result":
        # Only the last turn's result decides the stage; an interrupted turn
        # reports an error result before the turn that replaces it.
        result_subtype = ev.get("subtype")
        result_text = ev.get("result")
        saw_error = bool(result_subtype and result_subtype != "success")
        error_msg = f"result subtype={result_subtype}"


def handle_pi(ev):
    global session_id, result_text, saw_error, error_msg
    t = ev.get("type")
    if t == "session.started":
        session_id = ev["session_id"]
        record_session(session_id)
    elif t == "message_end":
        message = ev.get("message") or {}
        if message.get("role") == "assistant":
            result_text = "\n".join(part["text"] for part in message.get("content", [])
                                    if part.get("type") == "text")
            saw_error = message.get("stopReason") in ("error", "aborted")
            error_msg = message.get("errorMessage") or message.get("stopReason")
    elif t == "tool_execution_start":
        inp = ev.get("args") or {}
        target = inp.get("description") or inp.get("path") or inp.get("pattern") or inp.get("command") or inp.get("argv") or ""
        emit_tool_summary(f"  {ev.get('toolName', '?')}: {str(target).replace(chr(10), ' ')[:120]}\n")
    elif t == "error":
        saw_error = True
        error_msg = ev.get("message") or "Pi child failed"


def handle_codex(ev):
    global session_id, result_text, saw_error, error_msg
    kind = ev.get("type")
    if kind == "thread.started":
        session_id = ev["thread_id"]
        record_session(session_id)
    elif kind == "item.completed":
        item = ev.get("item") or {}
        if item.get("type") == "agent_message":
            result_text = item.get("text", "")
        elif item.get("type") in ("command_execution", "mcp_tool_call", "file_change"):
            target = item.get("command") or item.get("tool") or item.get("changes") or ""
            emit_tool_summary(f"  {item['type']}: {str(target)[:120]}\n")
    elif kind in ("turn.failed", "error"):
        saw_error = True
        error_msg = (ev.get("error") or {}).get("message") or ev.get("message") or "Codex turn failed"


# Inactivity timeout: if no new complete event line arrives for this many
# seconds, treat the child as stalled and exit non-zero (dedicated code 5).
# A slow-but-progressing child (periodic events) is NOT killed -- the timer
# resets on every received line. 0 disables the timeout (blocking read).
try:
    IDLE_TIMEOUT = float(os.environ.get("CEREBRO_CHILD_IDLE_TIMEOUT", "0") or 0)
except (TypeError, ValueError):
    sys.exit("cerebro: child_idle_timeout must be a number")


def _read_bounded():
    """Yield stdin lines one at a time, with an inactivity deadline.

    Replaces the blocking ``for line in sys.stdin`` loop so a stalled child
    (one event then silence) becomes a detectable failure instead of an
    infinite block. Uses select() on stdin; on Unix this is reliable. The
    deadline resets on every received line, so a slow-but-progressing child
    is never killed. Yields lines (stripped upstream) until EOF or stall.
    """
    if IDLE_TIMEOUT <= 0:
        # No timeout: fall back to the plain blocking read. The stdin
        # iterator already yields the final partial line without a trailing
        # newline, so no manual EOF flush is needed (unlike the select() path
        # below, which uses os.read + manual buffering and does need it).
        for line in sys.stdin:
            yield line
        return
    stdin_fd = sys.stdin.fileno()
    while True:
        ready, _, _ = select.select([stdin_fd], [], [], IDLE_TIMEOUT)
        if not ready:
            # No data for the full window: stalled.
            sys.stderr.write(
                f"\ncerebro: child stalled -- no stream events for "
                f"{int(IDLE_TIMEOUT)}s\n"
            )
            sys.exit(5)
        chunk = os.read(stdin_fd, 65536)
        if not chunk:
            # EOF: stdin closed. Flush any trailing partial line without a
            # trailing newline so a final event is not lost. (The stall
            # path above exits via sys.exit(5) with _buf unchanged, which is
            # correct -- a stall means no new data arrived.)
            if getattr(_read_bounded, "_buf", b"") and _read_bounded._buf.strip():
                yield _read_bounded._buf.decode("utf-8", "replace")
                _read_bounded._buf = b""
            return
        # Buffer and split into lines; os.read may return multiple lines
        # at once, so we yield each complete line and keep the tail.
        if not hasattr(_read_bounded, "_buf"):
            _read_bounded._buf = b""
        _read_bounded._buf += chunk
        while b"\n" in _read_bounded._buf:
            line, _read_bounded._buf = _read_bounded._buf.split(b"\n", 1)
            yield line.decode("utf-8", "replace")


for line in _read_bounded():
    line = line.strip()
    if not line:
        continue
    try:
        ev = json.loads(line)
    except json.JSONDecodeError:
        continue
    if not saw_any_event:
        saw_any_event = True
        # Auto-detect format from the first event.
        if provider:
            pass
        elif ev.get("type") in ("session.started", "agent_start", "message_start", "message_update", "message_end"):
            provider = "pi"
        elif ev.get("type") in ("system", "assistant", "result"):
            provider = "claude"
        elif ev.get("type") in ("thread.started", "turn.started", "item.started", "item.completed"):
            provider = "codex"
        else:
            sys.exit("cerebro: unrecognized child stream; select its backend explicitly")
    if provider == "claude":
        handle_claude(ev)
    elif provider == "codex":
        handle_codex(ev)
    elif provider == "pi":
        handle_pi(ev)
    else:
        sys.exit("cerebro: unsupported child stream backend: " + str(provider))

if not saw_any_event:
    sys.stderr.write("\ncerebro: child produced no stream events\n")
    sys.exit(2)
if saw_error:
    sys.stderr.write(f"\ncerebro: child reported an error: {error_msg}\n")
    sys.exit(4)
if result_path:
    if result_text is None or result_text == "":
        sys.stderr.write("\ncerebro: child did not emit a closing message\n")
        sys.exit(3)
    with open(result_path, "w") as f:
        f.write(result_text)
