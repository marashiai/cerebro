#!/usr/bin/env python3
"""End-to-end test for the cerebro PTY MCP server (lib/python/cerebro_mcp_server.py).

This harness is the MCP CLIENT. It spawns the server over stdio, speaks
newline-delimited JSON-RPC 2.0 (initialize -> notifications/initialized ->
tools/list -> tools/call), and drives a trivial interactive program through the
real tool surface:

  cerebro_spawn(python3 -u -c 'print("hello-pty"); x=input(); print("got:"+x)')
  cerebro_wait  -> tail contains "hello-pty"
  cerebro_send  "world" + enter
  cerebro_wait  -> tail contains "got:world"
  cerebro_wait  -> event == "exit", exitCode == 0
  cerebro_close

Requires the official `mcp` Python SDK on a Python >= 3.10; skipped when absent
(Pattern B, like tests/acp/relay_test.py). Run by tests/run.sh.
Usage: cerebro_mcp_server_test.py <repo-root>"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time

REPO = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(REPO, "lib", "python", "cerebro_mcp_server.py")

PY = sys.executable  # the interpreter run.sh picked (has `mcp` installed)

# A tiny interactive program: prints a marker, reads one line, echoes it back.
CHILD_SCRIPT = 'print("hello-pty"); x=input(); print("got:"+x)'

_failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("PASS  " if cond else "FAIL  ") + msg)
    if not cond:
        _failures.append(msg)


class McpClient:
    """A minimal newline-delimited JSON-RPC 2.0 client over stdio."""

    def __init__(self, cmd: list[str], env: dict[str, str] | None = None):
        self.proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, bufsize=0, env=env,
        )
        self._next_id = 1

    def _send(self, obj: dict) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def request(self, method: str, params: dict | None = None) -> dict:
        rid = self._next_id
        self._next_id += 1
        self._send({"jsonrpc": "2.0", "id": rid, "method": method,
                    "params": params or {}})
        return self._await(rid, method)

    def request_concurrent(self, calls: list[tuple[str, dict]]) -> list[dict]:
        """Fire N tool calls at once, then collect all N responses.

        Sends every request before awaiting any -- so the server sees N
        in-flight tools/call at the same time. If the server runs tools in
        parallel (async + threadpool), the wall time is ~max(one); if it
        serialized them on the event loop, it would be ~sum(one)."""
        rids: list[int] = []
        for method, params in calls:
            rid = self._next_id
            self._next_id += 1
            rids.append(rid)
            self._send({"jsonrpc": "2.0", "id": rid, "method": method,
                        "params": params})
        results: dict[int, dict] = {}
        while len(results) < len(rids):
            line = self.proc.stdout.readline()
            if not line:
                raise EOFError("server closed stdout mid-batch")
            msg = json.loads(line.decode("utf-8"))
            mid = msg.get("id")
            if mid in rids:
                if "error" in msg:
                    raise RuntimeError(f"batch {mid} error: {msg['error']}")
                results[mid] = msg.get("result", {})
        return [results[r] for r in rids]

    def _await(self, rid: int, method: str) -> dict:
        assert self.proc.stdout is not None
        # Skip server-initiated notifications (no id) until our response arrives.
        while True:
            line = self.proc.stdout.readline()
            if not line:
                err = b""
                if self.proc.stderr is not None:
                    err = self.proc.stderr.read()
                raise EOFError(f"server closed stdout (id={rid}) stderr={err!r}")
            msg = json.loads(line.decode("utf-8"))
            if msg.get("id") == rid:
                if "error" in msg:
                    raise RuntimeError(f"{method} error: {msg['error']}")
                return msg.get("result", {})
            # else: a notification or a different response -> ignore

    def notify(self, method: str, params: dict | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def call_tool(self, name: str, arguments: dict) -> dict:
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise RuntimeError(f"tool {name} returned isError: {result}")
        # MCPServer wraps the tool's returned string in a text content block.
        content = result.get("content", [])
        text = ""
        for block in content:
            if block.get("type") == "text":
                text += block.get("text", "")
        return json.loads(text)

    def close(self) -> None:
        try:
            if self.proc.stdin is not None:
                self.proc.stdin.close()
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()
        if self.proc.stderr is not None:
            err = self.proc.stderr.read()
            if err:
                print(f"  [server stderr] {err.decode('utf-8', 'replace')}", file=sys.stderr)


def main() -> None:
    client = McpClient([PY, SERVER])
    try:
        # --- initialize handshake ---
        init = client.request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "cerebro-mcp-test", "version": "1"},
        })
        check("serverInfo" in init, "initialize returns serverInfo")
        client.notify("notifications/initialized")

        # --- tools/list: our 9 tools are registered ---
        listed = client.request("tools/list", {})
        names = {t["name"] for t in listed.get("tools", [])}
        expect = {"cerebro_spawn", "cerebro_send", "cerebro_wait", "cerebro_read", "cerebro_status",
                  "cerebro_resize", "cerebro_kill", "cerebro_close", "cerebro_list"}
        check(names == expect, f"tools/list exposes the 9 cerebro_* tools (got {sorted(names)})")

        # --- drive a trivial interactive program end-to-end ---
        with tempfile.TemporaryDirectory() as cwd:
            spawn = client.call_tool("cerebro_spawn", {
                "command": PY, "args": ["-u", "-c", CHILD_SCRIPT], "cwd": cwd,
            })
            check("sessionId" in spawn and "pid" in spawn,
                  f"cerebro_spawn returns sessionId+pid (got {spawn})")
            sid = spawn["sessionId"]

            # wait for the first marker (event-driven; no polling loop)
            w1 = client.call_tool("cerebro_wait", {
                "sessionId": sid, "idleMs": 800, "timeoutMs": 15000,
            })
            check("hello-pty" in w1.get("tail", ""),
                  f"cerebro_wait sees 'hello-pty' (event={w1.get('event')} tail={w1.get('tail')!r})")
            cursor1 = w1.get("cursor", 0)

            # send input + enter; the program reads it and prints got:<input>
            s = client.call_tool("cerebro_send", {"sessionId": sid, "text": "world", "key": "enter"})
            check(s.get("ok") and s.get("bytesWritten", 0) > 0,
                  f"cerebro_send writes text+enter (got {s})")

            # cerebro_wait is the event primitive: it captures its read cursor at
            # call time, so if the child already finished before this call, its
            # tail may be empty and the event is `exit` (not `idle`). That is
            # correct event-driven behavior -- the response text is still in the
            # buffer, so a controller reads it via cerebro_read(sinceCursor=cursor1).
            w2 = client.call_tool("cerebro_wait", {
                "sessionId": sid, "idleMs": 800, "timeoutMs": 15000,
            })
            check(w2.get("event") in ("idle", "exit", "match", "timeout"),
                  f"cerebro_wait returns a valid event after send (got {w2})")
            resp = client.call_tool("cerebro_read", {"sessionId": sid, "sinceCursor": cursor1})
            check("got:world" in resp.get("text", ""),
                  f"cerebro_read(sinceCursor) sees 'got:world' after send (got {resp})")

            # wait for child exit (input() returned -> program ends -> exit 0)
            w3 = client.call_tool("cerebro_wait", {
                "sessionId": sid, "idleMs": 0, "timeoutMs": 15000,
            })
            check(w3.get("event") == "exit" and w3.get("exitCode") == 0,
                  f"cerebro_wait reports exit 0 (got {w3})")

            # status reflects the dead session; close retires it (idempotent)
            st = client.call_tool("cerebro_status", {"sessionId": sid})
            check(st.get("alive") is False and st.get("exitCode") == 0,
                  f"cerebro_status shows dead+exitCode 0 (got {st})")
            cl = client.call_tool("cerebro_close", {"sessionId": sid})
            check(cl.get("ok") is True, f"cerebro_close retires the session (got {cl})")

            # cerebro_list no longer includes it
            lst = client.call_tool("cerebro_list", {})
            check(all(s.get("sessionId") != sid for s in lst.get("sessions", [])),
                  f"cerebro_list omits the closed session (got {lst})")

        # --- concurrency: many agents / many sessions at once is a hard
        # requirement. Spawn N sessions each sleeping S seconds, then fire N
        # cerebro_wait calls CONCURRENTLY and assert the wall time is ~S (parallel),
        # not ~N*S (which is what would happen if tools ran serialized on the
        # event loop -- the blocking-sync-tool trap the async+threadpool design
        # exists to avoid). Margin is generous so this is not flaky on slow CI. ---
        import time as _time
        N, S = 4, 2
        sleepers = []
        with tempfile.TemporaryDirectory() as ccwd:
            for _ in range(N):
                spn = client.call_tool("cerebro_spawn", {
                    "command": PY, "args": ["-u", "-c", f"import time; time.sleep({S})"],
                    "cwd": ccwd,
                })
                sleepers.append(spn["sessionId"])
            calls = [("tools/call", {"name": "cerebro_wait",
                      "arguments": {"sessionId": s, "idleMs": 0, "timeoutMs": 15000}})
                     for s in sleepers]
            t0 = _time.monotonic()
            results = client.request_concurrent(calls)
            wall = _time.monotonic() - t0
        exits = [json.loads("".join(b.get("text", "") for b in r.get("content", [])
                                    if b.get("type") == "text")).get("exitCode")
                 for r in results]
        for s in sleepers:
            client.call_tool("cerebro_close", {"sessionId": s})
        check(all(e == 0 for e in exits),
              f"all {N} concurrent cerebro_wait calls saw exit 0 (got {exits})")
        # parallel ~ S (2s); serialized would be ~ N*S (8s). 3x single-time cap.
        check(wall < S * 3,
              f"{N} concurrent cerebro_wait calls ran in parallel (wall {wall:.2f}s "
              f"< {S*3}s; serialized would be ~{N*S}s)")
    finally:
        client.close()

    if _failures:
        print(f"\n{len(_failures)} check(s) failed:")
        for f in _failures:
            print(f"  - {f}")
        sys.exit(1)
    print("all checks passed")


if __name__ == "__main__":
    main()
