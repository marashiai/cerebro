#!/usr/bin/env python3
"""Minimal ACP agent stub used by tests/acp/relay_test.py.

It speaks ACP over stdio (via the SDK's run_agent) so `cerebro acp`'s proxy can
connect to it as its upstream child. It records every call it receives to the
JSON file named by STUB_RECORD so the test harness can assert:
  * new_session got the cerebro project dir as cwd and the user repo in
    additional_directories (the cwd remap),
  * the forced set_config_option pin (config_id + value + the foreign sid),
  * prompt got the foreign sid (sessionId remap editor->child).
It also emits one session/update on prompt so the harness can assert the
notification relays child->editor with the cerebro sid (sessionId remap
child->editor)."""

from __future__ import annotations

import json
import os
import sys

import acp
from acp.core import run_agent
from acp.schema import (
    AgentCapabilities,
    AgentMessageChunk,
    Implementation,
    InitializeResponse,
    NewSessionResponse,
    PromptResponse,
    SetSessionConfigOptionResponse,
    TextContentBlock,
)

_REC_PATH = os.environ["STUB_RECORD"]
_record: dict = {}


def _dump() -> None:
    with open(_REC_PATH, "w") as f:
        json.dump(_record, f)


class StubAgent:
    def __init__(self) -> None:
        self.conn = None  # AgentSideConnection -> the cerebro proxy (client)

    def on_connect(self, conn) -> None:
        self.conn = conn

    async def initialize(self, protocol_version, client_capabilities=None,
                         client_info=None, **kw):
        return InitializeResponse(
            protocol_version=acp.PROTOCOL_VERSION,
            agent_capabilities=AgentCapabilities(load_session=True),
            agent_info=Implementation(name="stub", title="stub", version="1.0"),
        )

    async def new_session(self, cwd, additional_directories=None,
                          mcp_servers=None, **kw):
        _record["new_session"] = {
            "cwd": cwd,
            "additional_directories": list(additional_directories or []),
            "mcp_servers": list(mcp_servers or []),
        }
        _dump()
        # A fixed foreign session id the proxy will remap to the cerebro sid.
        return NewSessionResponse(session_id="foreign-1", modes=None, config_options=None)

    async def set_config_option(self, config_id, session_id, value, **kw):
        _record.setdefault("set_config_option", []).append({
            "config_id": config_id, "session_id": session_id, "value": value,
        })
        _dump()
        return SetSessionConfigOptionResponse(config_options=[])

    async def set_session_mode(self, session_id, mode_id, **kw):
        _record.setdefault("set_session_mode", []).append(
            {"session_id": session_id, "mode_id": mode_id}
        )
        _dump()
        return None

    async def prompt(self, session_id, prompt, **kw):
        _record["prompt"] = {"session_id": session_id}
        _dump()
        # Emit one agent message chunk -> relays as session/update to the editor.
        await self.conn.session_update(
            session_id=session_id,
            update=AgentMessageChunk(
                session_update="agent_message_chunk",
                content=TextContentBlock(type="text", text="hello from stub"),
            ),
        )
        return PromptResponse(stop_reason="end_turn")

    async def cancel(self, session_id, **kw):
        _record["cancel"] = {"session_id": session_id}
        _dump()

    async def close_session(self, session_id, **kw):
        _record["close_session"] = {"session_id": session_id}
        _dump()
        return None

    async def list_sessions(self, cwd=None, cursor=None, **kw):
        return {"sessions": [], "next_cursor": None}

    async def ext_method(self, method, params):
        return {}

    async def ext_notification(self, method, params):
        pass


def main() -> None:
    try:
        asyncio_run = __import__("asyncio").run
        asyncio_run(run_agent(StubAgent()))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()