# Stream parser shared by execute / apply-review / doc-write / answer (both
# backends): `parse_stream.py <result_path> <id_path> [store] [key]`. Reads the
# child's JSON event stream on stdin (one event per line), auto-detects whether
# it is claude stream-json (`system`/`assistant`/`result` with `message.content`)
# or opencode run-json (`step_start`/`text`/`tool_use`/`step_finish`/`error` with
# `sessionID` + `part`), emits a one-line tool-call summary on stderr per tool
# use, captures the final assistant message text to <result_path> (if
# non-empty), captures the child's session id to <id_path> (if non-empty) AND --
# when a store file + key are given -- persists it the instant it is first seen
# so an interrupt stays resumable. Exits non-zero if the child produced no
# events or reported an error.
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

# Format-specific state. claude: result_text from the `result` event's `result`
# field, result_subtype from `subtype`, session id from the `system/init` event.
# opencode: result_text = text parts of the last step, snapshot at each
# step_finish, session id from the first event's `sessionID`.
cur_step_text = []
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
        result_subtype = ev.get("subtype")
        result_text = ev.get("result")
        if result_subtype and result_subtype != "success":
            saw_error = True
            error_msg = f"result subtype={result_subtype}"


def handle_opencode(ev):
    global session_id, result_text, saw_error, error_msg
    sid = ev.get("sessionID")
    if sid and session_id is None:
        session_id = sid
        record_session(sid)
    t = ev.get("type")
    part = ev.get("part") or {}
    if t == "step_start":
        cur_step_text[:] = []
    elif t == "text":
        txt = part.get("text")
        if isinstance(txt, str):
            cur_step_text.append(txt)
    elif t == "tool_use":
        name = part.get("tool", "?")
        state = part.get("state") or {}
        inp = state.get("input") or {}
        target = (
            state.get("title") or inp.get("description") or
            inp.get("filePath") or inp.get("file_path") or
            inp.get("pattern") or inp.get("path") or inp.get("query") or
            inp.get("command") or inp.get("url") or ""
        )
        if isinstance(target, list):
            target = " ".join(map(str, target))
        target = str(target).replace("\n", " ").strip()
        if len(target) > 120:
            target = target[:120] + "..."
        clr = "\r\033[2K" if sys.stderr.isatty() else ""
        emit_tool_summary(f"{clr}  {name}: {target}\n")
    elif t == "step_finish":
        snap = "".join(cur_step_text).strip()
        if snap:
            result_text = snap
    elif t == "error":
        saw_error = True
        err = ev.get("error") or {}
        data = err.get("data") or {}
        error_msg = err.get("message") or data.get("message") or err.get("name") or "unknown error"


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
        elif ev.get("type") == "step_start" or "sessionID" in ev:
            provider = "opencode"
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
    else:
        handle_opencode(ev)

# opencode: a final flush in case the run ended mid-step without a closing
# step_finish.
if provider == "opencode":
    _tail = "".join(cur_step_text).strip()
    if _tail:
        result_text = _tail

if not saw_any_event:
    sys.stderr.write("\ncerebro: child produced no stream events\n")
    sys.exit(2)
if saw_error:
    sys.stderr.write(f"\ncerebro: child reported an error: {error_msg}\n")
    sys.exit(4)
if result_path:
    if result_text is None or result_text == "":
        # claude: a missing result event is a failure; opencode: an empty run
        # with no text is also a failure (the closing message is what we need).
        sys.stderr.write("\ncerebro: child did not emit a closing message\n")
        sys.exit(3)
    with open(result_path, "w") as f:
        f.write(result_text)
