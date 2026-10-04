#!/usr/bin/env python3
"""End-to-end relay test for `cerebro acp`'s proxy (acp_server.py).

This harness is the ACP EDITER (client). It spawns acp_server.py as the ACP
agent, with CEREBRO_ACP_CHILD_SPEC pointing at tests/acp/stub_upstream.py as the
upstream child. It then drives initialize / new_session / prompt and asserts:
  * the editor sees cerebro session ids (not the stub's "foreign-1");
  * acp-mint created the cerebro session dir + ACP project dir (agent file);
  * the proxy remapped cwd: the stub got the cerebro project dir as cwd and the
    user's repo in additional_directories (no repo pollution);
  * the forced set_config_option pin reached the stub (config_id + value + the
    foreign sid);
  * a session/update notification relayed child->editor with the cerebro sid;
  * session/prompt returned the stub's stop_reason;
  * the foreign id was recorded into cerebro metadata (for resume/load).

Run by tests/run.sh via the ACP python (the one with agent-client-protocol).
Usage: relay_test.py <repo-root>"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Any

from acp.schema import PromptResponse, TextContentBlock
from acp.stdio import spawn_agent_process

REPO = sys.argv[1]
ACP_SERVER = os.path.join(REPO, "lib", "python", "acp_server.py")
STUB = os.path.join(REPO, "tests", "acp", "stub_upstream.py")
CEREBRO_BIN_DIR = os.path.join(REPO, "bin")

_failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("PASS  " if cond else "FAIL  ") + msg)
    if not cond:
        _failures.append(msg)


class TestClient:
    """The editor side: records session/update notifications relayed to it."""

    def __init__(self) -> None:
        self.updates: list[tuple[str, Any]] = []

    def on_connect(self, conn) -> None:
        pass

    async def session_update(self, session_id, update, **kw):
        self.updates.append((session_id, update))

    async def request_permission(self, *a, **k):
        raise RuntimeError("unexpected request_permission")

    async def write_text_file(self, *a, **k):
        raise RuntimeError("unexpected write_text_file")

    async def read_text_file(self, *a, **k):
        raise RuntimeError("unexpected read_text_file")

    async def create_terminal(self, *a, **k):
        raise RuntimeError("unexpected create_terminal")

    async def terminal_output(self, *a, **k):
        raise RuntimeError("unexpected terminal_output")

    async def release_terminal(self, *a, **k):
        raise RuntimeError("unexpected release_terminal")

    async def wait_for_terminal_exit(self, *a, **k):
        raise RuntimeError("unexpected wait_for_terminal_exit")

    async def kill_terminal(self, *a, **k):
        raise RuntimeError("unexpected kill_terminal")

    async def create_elicitation(self, *a, **k):
        raise RuntimeError("unexpected create_elicitation")

    async def complete_elicitation(self, *a, **k):
        pass

    async def ext_method(self, method, params):
        return {}

    async def ext_notification(self, method, params):
        pass


def main() -> int:
    import asyncio

    tmp = tempfile.mkdtemp(prefix="cerebro-acp-test-")
    home = os.path.join(tmp, "home")
    os.makedirs(home)
    user_repo = os.path.join(tmp, "userrepo")
    os.makedirs(user_repo)
    rec_path = os.path.join(tmp, "stub_record.json")

    spec = {
        "argv": [sys.executable, STUB],
        "pin": {"config_id": "mode", "value": "cerebro-orchestrator"},
        "env": {},
    }
    env = os.environ.copy()
    env["CEREBRO_HOME"] = home
    env["CEREBRO_BACKEND"] = "claude"
    env["CEREBRO_JEV_ENABLED"] = "0"
    env["CEREBRO_ACP_CHILD_SPEC"] = json.dumps(spec)
    env["STUB_RECORD"] = rec_path
    env["PATH"] = CEREBRO_BIN_DIR + os.pathsep + env.get("PATH", "")

    async def run() -> None:
        client = TestClient()
        async with spawn_agent_process(client, sys.executable, ACP_SERVER, env=env) as (conn, proc):
            init = await conn.initialize(1)
            check(init.agent_info.name == "cerebro", "initialize.agent_info.name == cerebro")

            ns = await conn.new_session(cwd=user_repo, mcp_servers=[])
            cerebro_sid = ns.session_id
            check(bool(cerebro_sid) and cerebro_sid != "foreign-1",
                  f"new_session returned a cerebro sid (got {cerebro_sid!r})")

            project_dir = os.path.join(home, "acp", cerebro_sid)
            check(os.path.isfile(os.path.join(project_dir, ".mcp.json")),
                  "acp-mint wrote the private supervisor MCP configuration")
            check(os.path.isfile(os.path.join(project_dir, ".claude", "agents", "cerebro-orchestrator.md")),
                  "acp-mint wrote the claude orchestrator agent into the project dir")
            check(os.path.isdir(os.path.join(home, "sessions", cerebro_sid)),
                  "acp-mint created the cerebro session dir")

            resp = await conn.prompt(session_id=cerebro_sid,
                                     prompt=[TextContentBlock(type="text", text="hi")])
            check(isinstance(resp, PromptResponse) and resp.stop_reason == "end_turn",
                  f"prompt returned the stub's stop_reason (got {getattr(resp,'stop_reason',None)!r})")

            # session/update should have relayed with the cerebro sid (remapped).
            check(any(sid == cerebro_sid for sid, _ in client.updates),
                  "session/update relayed to editor with the cerebro sid")

            try:
                await conn.close_session(session_id=cerebro_sid)
            except Exception:
                pass

        # Read the stub's record of what it received.
        with open(rec_path) as f:
            rec = json.load(f)

        ns_rec = rec.get("new_session", {})
        check(ns_rec.get("cwd") == project_dir,
              f"stub new_session.cwd == cerebro project dir (got {ns_rec.get('cwd')!r})")
        check(user_repo in (ns_rec.get("additional_directories") or []),
              "stub new_session.additional_directories includes the user repo")

        pins = rec.get("set_config_option", [])
        pin_ok = any(p.get("config_id") == "mode"
                     and p.get("value") == "cerebro-orchestrator"
                     and p.get("session_id") == "foreign-1" for p in pins)
        check(pin_ok, f"forced pin set_config_option(mode=cerebro-orchestrator, foreign-1) reached stub (pins={pins})")

        check(rec.get("prompt", {}).get("session_id") == "foreign-1",
              "stub prompt got the foreign sid (editor->child remap)")

        md_path = os.path.join(home, "sessions", cerebro_sid, "metadata.json")
        with open(md_path) as f:
            md = json.load(f)
        check(md.get("foreign_session_id") == "foreign-1",
              f"foreign id recorded in cerebro metadata (got {md.get('foreign_session_id')!r})")
        check(md.get("backend") == "claude",
              f"metadata backend recorded (got {md.get('backend')!r})")

    asyncio.run(run())
    shutil.rmtree(tmp, ignore_errors=True)
    if _failures:
        print(f"\n{len(_failures)} ACP relay check(s) failed")
        return 1
    print("\nACP relay: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())