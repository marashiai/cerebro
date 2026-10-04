#!/usr/bin/env python3
# cerebro PTY MCP server.
#
# A GENERIC terminal MCP server: this process owns long-lived pseudo-terminals
# and exposes them over MCP so a controller (another agent, an editor, a test)
# can spawn an interactive TTY program, send it input, and wait on real events
# (idle / pattern match / child exit) without polling. cerebro is one client;
# the surface drives any interactive program. Launched by `cerebro cerebro-mcp`
# (lib/commands/cerebro_mcp.sh) or directly: `python3 lib/python/cerebro_mcp_server.py`.
#
# stdout is the MCP JSON-RPC pipe (owned by the mcp SDK); all diagnostics go to
# stderr via _log. Mirrors lib/python/acp_server.py's idiom. The PTY allocation
# idiom (pty.fork + termios echo-off + select drain) mirrors tests/pty_run.py.
# The ANSI -> normalized-text parser (lib/python/cerebro_mcp_ansi.py) is pure stdlib
# and unit-tested separately.
#
# Every tool offloads blocking PTY work via `anyio.to_thread.run_sync` -- the loop
# stays free and concurrent tool calls run in parallel threadpool workers. Each
# session owns a dedicated daemon reader thread + a threading.Condition, so many
# agents can drive many sessions at once (independent readers + locks -> true
# parallelism), and cerebro_send/cerebro_read on a session proceed concurrently with a
# cerebro_wait that is waiting on that session (cond.wait releases the lock).
from __future__ import annotations

import anyio
import atexit
import errno
import fcntl
import json
import os
import pty
import re
import select
import signal
import struct
import sys
import termios
import threading
import time
import uuid
from typing import Any, Optional

from mcp.server.mcpserver import MCPServer

from cerebro_mcp_ansi import AnsiStreamParser

mcp = MCPServer("cerebro-mcp")


def _log(msg: str) -> None:
    """Diagnostics to stderr (stdout is the JSON-RPC pipe)."""
    print(f"cerebro cerebro-mcp: {msg}", file=sys.stderr, flush=True)


MAX_SESSIONS = 64
_MAX_WAIT_MS = 120_000          # cap cerebro_wait timeout under the harness call cap
_SELECT_TICK = 0.1              # reader select timeout + wait re-check interval


# Map a `key` enum value to the bytes a terminal expects.
_KEY_BYTES: dict[str, bytes] = {
    "enter": b"\r",
    "ctrl-c": b"\x03",
    "ctrl-d": b"\x04",
    "esc": b"\x1b",
    "tab": b"\t",
    "up": b"\x1b[A",
    "down": b"\x1b[B",
    "right": b"\x1b[C",
    "left": b"\x1b[D",
    "home": b"\x1b[H",
    "end": b"\x1b[F",
    "backspace": b"\x7f",
    "delete": b"\x1b[3~",
    "pageup": b"\x1b[5~",
    "pagedown": b"\x1b[6~",
}

_SIGS: dict[str, int] = {
    "TERM": signal.SIGTERM,
    "KILL": signal.SIGKILL,
    "INT": signal.SIGINT,
    "HUP": signal.SIGHUP,
    "QUIT": signal.SIGQUIT,
}


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


# ---- PTY session ------------------------------------------------------------

class Session:
    """One spawned program under a PTY, with a dedicated reader thread."""

    def __init__(self, session_id: str, pid: int, master_fd: int,
                 command: str, cwd: str) -> None:
        self.id = session_id
        self.pid = pid
        self.master_fd = master_fd
        self.command = command
        self.cwd = cwd
        self.parser = AnsiStreamParser()
        self.alive = True
        self.exit_code: Optional[int] = None
        self.last_byte_at = time.monotonic()
        self.cond = threading.Condition()
        self._closing = False
        self._reader = threading.Thread(
            target=self._read_loop, name=f"cerebro-reader-{session_id}", daemon=True)
        self._reader.start()

    # -- reader thread: drain master_fd -> parser, detect exit, reap, notify --

    def _read_loop(self) -> None:
        fd = self.master_fd
        while not self._closing:
            try:
                r, _, _ = select.select([fd], [], [], _SELECT_TICK)
            except (OSError, ValueError):
                break
            if r:
                try:
                    data = os.read(fd, 65536)
                except OSError as e:
                    if e.errno in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EINTR):
                        data = b""
                    else:
                        break
                if data:
                    with self.cond:
                        self.parser.feed(data)
                        self.last_byte_at = time.monotonic()
                        self.cond.notify_all()
                elif data == b"":
                    # EOF on the master: the child has (almost certainly) exited.
                    # Bounded-reap so exit_code is set before we leave the loop --
                    # a WNOHANG at this instant can race the exit by a hair and
                    # leave exit_code None, which would make a cerebro_wait that fires
                    # on `not alive` report exitCode=None.
                    self._reap_exit()
                    break
            if self._maybe_reap():
                break
        # final flush + mark dead. Try once more to reap (covers a child that
        # closed stdout but is still finishing); if it's genuinely gone, mark
        # dead so waiters don't block on a session whose reader has exited.
        if self.alive:
            self._maybe_reap()
        if self.alive:
            with self.cond:
                self.alive = False
                self.cond.notify_all()
        try:
            os.close(fd)
        except OSError:
            pass

    def _reap_exit(self) -> None:
        """Reap the child after master EOF, with a short bounded retry.

        EOF means the child is finishing; WNOHANG at that instant can miss the
        exit by a fraction of a second. Retry briefly (WNOHANG + tiny sleeps) so
        we capture exit_code without blocking the reader forever in the exotic
        case where stdout closed but the child keeps running."""
        deadline = time.monotonic() + 2.0
        while True:
            try:
                wpid, status = os.waitpid(self.pid, os.WNOHANG)
            except ChildProcessError:
                with self.cond:
                    self.alive = False
                    self.cond.notify_all()
                return
            except OSError:
                break
            if wpid != 0:
                code: Optional[int] = None
                if os.WIFEXITED(status):
                    code = os.WEXITSTATUS(status)
                elif os.WIFSIGNALED(status):
                    code = -os.WTERMSIG(status)
                with self.cond:
                    self.exit_code = code
                    self.alive = False
                    self.cond.notify_all()
                return
            if time.monotonic() >= deadline:
                break
            time.sleep(0.01)
        with self.cond:
            self.alive = False
            self.cond.notify_all()

    def _maybe_reap(self) -> bool:
        try:
            wpid, status = os.waitpid(self.pid, os.WNOHANG)
        except ChildProcessError:
            with self.cond:
                self.alive = False
                self.cond.notify_all()
            return True
        if wpid == 0:
            return False
        code: Optional[int] = None
        if os.WIFEXITED(status):
            code = os.WEXITSTATUS(status)
        elif os.WIFSIGNALED(status):
            code = -os.WTERMSIG(status)
        with self.cond:
            self.alive = False
            self.exit_code = code
            self.cond.notify_all()
        return True

    # -- teardown (called from a tool thread via cerebro_close) --

    def close(self, sig_name: str = "TERM") -> None:
        self._closing = True
        if self.alive:
            try:
                os.kill(self.pid, _SIGS.get(sig_name.upper(), signal.SIGTERM))
            except ProcessLookupError:
                pass
        self._reader.join(timeout=3.0)
        if self.alive:  # reader didn't reap in time: force-reap
            try:
                os.waitpid(self.pid, 0)
            except ChildProcessError:
                pass
            with self.cond:
                self.alive = False
        try:
            os.close(self.master_fd)
        except OSError:
            pass


# ---- session registry -------------------------------------------------------

_SESSIONS: dict[str, Session] = {}
_REGISTRY_LOCK = threading.RLock()


def _get_session(session_id: str) -> Optional[Session]:
    with _REGISTRY_LOCK:
        return _SESSIONS.get(session_id)


def _cleanup_all() -> None:
    """Close every live session on server shutdown so PTY children don't outlive us."""
    with _REGISTRY_LOCK:
        sessions = list(_SESSIONS.values())
        _SESSIONS.clear()
    for s in sessions:
        try:
            s.close("TERM")
        except Exception as e:  # noqa: BLE001
            _log(f"cleanup: session {s.id} close failed: {e}")


atexit.register(_cleanup_all)


# ---- blocking tool implementations (run via anyio.to_thread.run_sync) -------

def _spawn_impl(command: str, args: list[str], cwd: str,
                 env: Optional[dict[str, Optional[str]]],
                 cols: int, rows: int) -> dict[str, Any]:
    if not command:
        return {"error": "command is required"}
    if not cwd or not os.path.isdir(cwd):
        return {"error": f"cwd is required and must be an existing directory: {cwd!r}"}
    cols = max(1, int(cols))
    rows = max(1, int(rows))
    with _REGISTRY_LOCK:
        if len(_SESSIONS) >= MAX_SESSIONS:
            return {"error": f"session cap reached ({MAX_SESSIONS}); close one first"}
        # reserve a slot by minting the id now so a concurrent spawn can't exceed
        session_id = uuid.uuid4().hex
        _SESSIONS[session_id] = None  # type: ignore[assignment]  # placeholder

    argv = [command] + list(args or [])
    child_env = os.environ.copy()
    if env:
        for k, v in env.items():
            if v is None:
                child_env.pop(k, None)
            else:
                child_env[k] = str(v)

    try:
        pid, master_fd = pty.fork()
    except OSError as e:
        with _REGISTRY_LOCK:
            _SESSIONS.pop(session_id, None)
        return {"error": f"pty.fork failed: {e}"}

    if pid == 0:
        # child: echo off on the controlling tty, chdir, exec (no shell)
        try:
            attrs = termios.tcgetattr(0)
            attrs[3] = attrs[3] & ~termios.ECHO
            termios.tcsetattr(0, termios.TCSANOW, attrs)
        except OSError:
            pass
        try:
            os.chdir(cwd)
        except OSError as e:
            sys.stderr.write(f"cerebro cerebro-mcp: chdir failed: {e}\n")
            os._exit(126)
        try:
            os.execvpe(command, argv, child_env)
        except OSError as e:
            sys.stderr.write(f"cerebro cerebro-mcp: exec failed: {e}\n")
            os._exit(127)
        os._exit(127)

    # parent: echo off + nonblocking on master, set window size, register session
    try:
        attrs = termios.tcgetattr(master_fd)
        attrs[3] = attrs[3] & ~termios.ECHO
        termios.tcsetattr(master_fd, termios.TCSANOW, attrs)
    except OSError:
        pass
    try:
        fcntl.fcntl(master_fd, fcntl.F_SETFL,
                    fcntl.fcntl(master_fd, fcntl.F_GETFL) | os.O_NONBLOCK)
    except OSError:
        pass
    try:
        fcntl.ioctl(master_fd, termios.TIOCSWINSZ,
                    struct.pack("HHHH", rows, cols, 0, 0))
    except OSError:
        pass

    sess = Session(session_id, pid, master_fd, command, cwd)
    with _REGISTRY_LOCK:
        _SESSIONS[session_id] = sess
    _log(f"spawned session {session_id} pid={pid} cmd={command} cwd={cwd}")
    return {"sessionId": session_id, "pid": pid}


def _send_impl(session_id: str, text: Optional[str], key: Optional[str]) -> dict[str, Any]:
    sess = _get_session(session_id)
    if not sess:
        return {"error": f"session not found: {session_id}"}
    payload = b""
    if text:
        payload += text.encode("utf-8", errors="replace")
    if key:
        kb = _KEY_BYTES.get(key)
        if kb is None:
            return {"error": f"unknown key: {key!r} (one of: {sorted(_KEY_BYTES)})"}
        payload += kb  # text first, then the key (e.g. "world" then enter)
    if not payload:
        return {"ok": True, "bytesWritten": 0}
    written = 0
    while written < len(payload):
        try:
            n = os.write(sess.master_fd, payload[written:])
        except OSError as e:
            if e.errno in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EINTR):
                time.sleep(0.01)
                continue
            return {"ok": False, "bytesWritten": written, "error": f"write failed: {e}"}
        if n <= 0:
            break
        written += n
    return {"ok": True, "bytesWritten": written}


def _wait_impl(session_id: str, idle_ms: int, match: Optional[str],
               timeout_ms: int) -> dict[str, Any]:
    sess = _get_session(session_id)
    if not sess:
        return {"error": f"session not found: {session_id}"}
    # time.monotonic() is in SECONDS; the *_ms params are milliseconds. Convert
    # to seconds once so the deadline and the quiet-duration compare are in the
    # same unit as the clock (and as Session.last_byte_at, set in _read_loop).
    idle_s = max(0, int(idle_ms)) / 1000.0
    timeout_s = min(_MAX_WAIT_MS, max(0, int(timeout_ms))) / 1000.0
    compiled = re.compile(match) if match else None
    deadline = time.monotonic() + timeout_s
    result: dict[str, Any] = {}
    with sess.cond:
        start_cursor = sess.parser.cursor
        saw_output = False
        while True:
            if not sess.alive:
                result = {"event": "exit", "exited": True, "exitCode": sess.exit_code,
                          "idle": False, "matched": None, "sawOutput": saw_output,
                          "cursor": sess.parser.cursor,
                          "tail": sess.parser.text_since(start_cursor)}
                break
            if sess.parser.cursor > start_cursor:
                saw_output = True
            tail = sess.parser.text_since(start_cursor)
            if compiled:
                m = compiled.search(tail)
                if m:
                    result = {"event": "match", "exited": False, "exitCode": None,
                              "idle": False, "matched": m.group(0),
                              "sawOutput": saw_output,
                              "cursor": sess.parser.cursor, "tail": tail}
                    break
            now = time.monotonic()
            if saw_output and (now - sess.last_byte_at) >= idle_s:
                result = {"event": "idle", "exited": False, "exitCode": None,
                          "idle": True, "matched": None, "sawOutput": True,
                          "cursor": sess.parser.cursor, "tail": tail}
                break
            remaining = deadline - now
            if remaining <= 0:
                result = {"event": "timeout", "exited": not sess.alive,
                          "exitCode": sess.exit_code, "idle": False, "matched": None,
                          "sawOutput": saw_output, "cursor": sess.parser.cursor,
                          "tail": tail}
                break
            sess.cond.wait(min(remaining, _SELECT_TICK))
    return result


def _read_impl(session_id: str, since_cursor: Optional[int],
               tail_lines: Optional[int]) -> dict[str, Any]:
    sess = _get_session(session_id)
    if not sess:
        return {"error": f"session not found: {session_id}"}
    with sess.cond:
        if tail_lines is not None:
            text = sess.parser.tail_lines(int(tail_lines))
        else:
            text = sess.parser.text_since(int(since_cursor) if since_cursor else 0)
        ended = not sess.alive
        cursor = sess.parser.cursor
    return {"text": text, "cursor": cursor, "ended": ended}


def _status_impl(session_id: str) -> dict[str, Any]:
    sess = _get_session(session_id)
    if not sess:
        return {"error": f"session not found: {session_id}"}
    with sess.cond:
        return {"alive": sess.alive, "exitCode": sess.exit_code, "pid": sess.pid,
                "command": sess.command, "cwd": sess.cwd,
                "bufferedLines": sess.parser.num_lines}


def _resize_impl(session_id: str, cols: int, rows: int) -> dict[str, Any]:
    sess = _get_session(session_id)
    if not sess:
        return {"error": f"session not found: {session_id}"}
    try:
        fcntl.ioctl(sess.master_fd, termios.TIOCSWINSZ,
                    struct.pack("HHHH", max(1, int(rows)), max(1, int(cols)), 0, 0))
    except OSError as e:
        return {"ok": False, "error": f"resize failed: {e}"}
    return {"ok": True}


def _kill_impl(session_id: str, sig_name: str) -> dict[str, Any]:
    sess = _get_session(session_id)
    if not sess:
        return {"error": f"session not found: {session_id}"}
    sig = _SIGS.get(sig_name.upper())
    if sig is None:
        return {"error": f"unknown signal: {sig_name!r} (one of: {sorted(_SIGS)})"}
    try:
        os.kill(sess.pid, sig)
    except ProcessLookupError:
        return {"ok": False, "error": "no such process (already exited)"}
    except OSError as e:
        return {"ok": False, "error": f"kill failed: {e}"}
    return {"ok": True}


def _close_impl(session_id: str) -> dict[str, Any]:
    with _REGISTRY_LOCK:
        sess = _SESSIONS.pop(session_id, None)
    if not sess:
        return {"ok": True, "note": "session not found (already closed)"}
    try:
        sess.close("TERM")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"close failed: {e}"}
    _log(f"closed session {session_id}")
    return {"ok": True}


def _list_impl() -> dict[str, Any]:
    with _REGISTRY_LOCK:
        items = [
            {"sessionId": s.id, "alive": s.alive, "command": s.command, "cwd": s.cwd}
            for s in _SESSIONS.values() if s is not None
        ]
    return {"sessions": items}


# ---- MCP tools (async; offload blocking work to a threadpool worker) --------
# MCPServer keys JSON-RPC arguments off the Python parameter names, so the wire
# keys are exactly the param names below (camelCase, the documented API). The
# blocking _impl_* helpers take snake_case and are called positionally.

@mcp.tool()
async def cerebro_spawn(command: str, cwd: str,
                    args: list[str] | None = None,
                    env: dict[str, str | None] | None = None,
                    cols: int = 80, rows: int = 24) -> str:
    """Spawn a program under a persistent PTY. EXECUTES ARBITRARY CODE -- this is
    shell-equivalent. `command` is resolved via PATH (no shell); `args` is the
    argv tail; `cwd` (required) must exist; `env` optionally sets/overrides vars
    (set a value to null to unset it; otherwise inherits this server's env).
    Returns {"sessionId","pid"} or {"error"}. Close with cerebro_close when done."""
    return _json(await anyio.to_thread.run_sync(
        _spawn_impl, command, list(args or []), cwd, env, cols, rows))


@mcp.tool()
async def cerebro_send(sessionId: str, text: str | None = None,
                   key: str | None = None) -> str:
    """Send input to a session's PTY. `text` is written as raw UTF-8 bytes; `key`
    is one of: enter, ctrl-c, ctrl-d, esc, tab, up, down, left, right, home, end,
    backspace, delete, pageup, pagedown. Both may be given (text then key). Safe
    to call while a cerebro_wait is waiting on the same session. Returns
    {"ok","bytesWritten"} or {"error"}."""
    return _json(await anyio.to_thread.run_sync(_send_impl, sessionId, text, key))


@mcp.tool()
async def cerebro_wait(sessionId: str, idleMs: int = 1500,
                   match: str | None = None, timeoutMs: int = 30000) -> str:
    """Block until the next event on a session and return it. The call itself is
    the wake-up -- issue one call per event boundary, no polling loop. Returns on
    the first of: `exit` (child died; exitCode set), `match` (regex `match` found
    in text appended since this call started), `idle` (output was seen AND the
    session has been quiet for idleMs), or `timeout` (timeoutMs elapsed, capped
    at 120000). `tail` is the normalized text appended since this call started;
    `sawOutput` tells whether any arrived; `cursor` is the new read cursor. Re-
    issue after a timeout to keep waiting."""
    return _json(await anyio.to_thread.run_sync(
        _wait_impl, sessionId, idleMs, match, timeoutMs))


@mcp.tool()
async def cerebro_read(sessionId: str, sinceCursor: int | None = None,
                   tailLines: int | None = None) -> str:
    """Read normalized (ANSI-stripped) output from a session, non-blocking. With
    `sinceCursor`: returns text appended at/after that cursor (use the cursor
    from a prior wait/read to read incrementally). With `tailLines`: returns the
    last N lines joined. Returns {"text","cursor","ended"} or {"error"}."""
    return _json(await anyio.to_thread.run_sync(
        _read_impl, sessionId, sinceCursor, tailLines))


@mcp.tool()
async def cerebro_status(sessionId: str) -> str:
    """Snapshot one session: alive, exitCode, pid, command, cwd, bufferedLines."""
    return _json(await anyio.to_thread.run_sync(_status_impl, sessionId))


@mcp.tool()
async def cerebro_resize(sessionId: str, cols: int, rows: int) -> str:
    """Resize a session's PTY (sends SIGWINCH to the child). Returns {"ok"}."""
    return _json(await anyio.to_thread.run_sync(_resize_impl, sessionId, cols, rows))


@mcp.tool()
async def cerebro_kill(sessionId: str, signal: str = "TERM") -> str:
    """Send a signal to a session's child (default TERM). Does NOT remove the
    session -- the reader reaps the exit; call cerebro_close to retire it."""
    return _json(await anyio.to_thread.run_sync(_kill_impl, sessionId, signal))


@mcp.tool()
async def cerebro_close(sessionId: str) -> str:
    """Tear down a session: signal+reap the child if still alive, stop the reader,
    close the PTY, and remove it from the registry. Idempotent. Call when done so
    the server doesn't hold dead sessions."""
    return _json(await anyio.to_thread.run_sync(_close_impl, sessionId))


@mcp.tool()
async def cerebro_list() -> str:
    """List all live sessions: [{"sessionId","alive","command","cwd"}]."""
    return _json(await anyio.to_thread.run_sync(_list_impl))


# ---- entry point ------------------------------------------------------------

def main() -> None:
    try:
        mcp.run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
