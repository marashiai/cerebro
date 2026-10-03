#!/usr/bin/env python3
# cerebro ACP (Agent Client Protocol) proxy server.
#
# `cerebro acp` execs this module on the official `agent-client-protocol` Python
# SDK. cerebro is a THIN PROXY between an ACP editor (Zed, ...) and a per-session
# UPSTREAM claude-agent-acp child. The upstream child
# owns the entire ACP capability surface -- images, @-mentions / embedded
# context, thinking, structured user questions (elicitation), terminals, MCP,
# permissions, edit review, model / mode / effort / agent pickers, session
# load / resume / fork / list, usage -- so the editor gets the fully-featured
# agent experience. cerebro owns four things:
#
#   1. session minting -- per session/new it mints a cerebro session
#      (`cerebro acp mint`) so spawned `cerebro <subcommand>` children bind to it
#      via CEREBRO_SESSION_ID (the same binding the interactive TUI uses);
#   2. env injection -- CEREBRO_SESSION_ID / CEREBRO_SESSION_DIR / CEREBRO_HOME
#      plus the backend's child env (CLAUDE_CONFIG_DIR / Anthropic gateway env
#      for claude) are exported into the upstream child;
#   3. agent pinning -- the cerebro-orchestrator agent is forced via
#      session/set_config_option after new_session (`agent`); the agent file
#      lives in a cerebro-owned per-session project
#      dir that is the session cwd (the user's repo is an additional_directory,
#      never written to);
#   4. sessionId remap -- the editor sees cerebro session ids; the upstream sees
#      its own. Every method relays one direction, remapping the id.
#
# No stream translation, no per-feature reimplementation: JSON-RPC flows through
# unchanged. See lib/commands/acp.sh and docs/USAGE.md (ACP) for the contract.
#
# stdout is the JSON-RPC pipe to the editor; all diagnostics go to stderr.

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from dataclasses import dataclass
from typing import Any, Optional

import acp
from acp.core import run_agent
from acp.schema import (
    AgentCapabilities,
    AuthenticateResponse,
    CloseSessionResponse,
    ForkSessionResponse,
    Implementation,
    InitializeResponse,
    ListSessionsResponse,
    LoadSessionResponse,
    McpCapabilities,
    NewSessionResponse,
    PromptCapabilities,
    ResumeSessionResponse,
    SessionAdditionalDirectoriesCapabilities,
    SessionCapabilities,
    SessionCloseCapabilities,
    SessionConfigOptionSelect,
    SessionConfigSelectOption,
    SessionInfo,
    SessionListCapabilities,
    SessionResumeCapabilities,
    SetSessionConfigOptionResponse,
    SetSessionModeResponse,
)
from acp.stdio import spawn_agent_process

CEREBRO_HOME = os.environ["CEREBRO_HOME"]
CEREBRO_BIN = shutil.which("cerebro") or "cerebro"

# The backend-fixed upstream child spec, built once by `cerebro acp`:
#   {"argv": [...], "pin": {"config_id": "...", "value": "..."}, "env": {...}}
# env values may be null -> unset on the child.
_SPEC = json.loads(os.environ["CEREBRO_ACP_CHILD_SPEC"])
_SPEC_ARGV = list(_SPEC["argv"])
_SPEC_PIN = _SPEC["pin"]
_SPEC_ENV = _SPEC.get("env") or {}


def _log(msg: str) -> None:
    """Diagnostics to stderr (stdout is the JSON-RPC pipe)."""
    print(f"cerebro acp: {msg}", file=sys.stderr, flush=True)


def _build_child_env(cerebro_sid: str) -> dict[str, str]:
    """Build the env for an upstream child process: inherit this process's env
    (PATH / HOME / user auth env), apply the backend spec env (null -> unset),
    then inject the cerebro session binding."""
    env = os.environ.copy()
    for k, v in _SPEC_ENV.items():
        if v is None:
            env.pop(k, None)
        else:
            env[k] = str(v)
    env["CEREBRO_SESSION_ID"] = cerebro_sid
    env["CEREBRO_SESSION_DIR"] = os.path.join(CEREBRO_HOME, "sessions", cerebro_sid)
    env["CEREBRO_HOME"] = CEREBRO_HOME
    return env


# ---- catalog-driven model picker rewrite ----
#
# The upstream `claude-agent-acp` child populates its /model picker from the
# Claude SDK's stock list + the ANTHROPIC_DEFAULT_*_MODEL env vars (which carry
# Anthropic tier labels like "Opus"/"Sonnet"). cerebro's catalog
# ($CEREBRO_HOME/models-config.json) is the user's real source of truth, so the
# proxy rewrites the `model` config option on every response/notification that
# carries config_options: the editor sees a clean "Model" picker whose entries
# are the catalog's ids / names / descriptions -- no Anthropic tier labels. The
# env vars (set by backend_claude_acp_child_spec) still register the ids with
# the SDK so `set_config_option("model", <catalog id>)` is accepted; the proxy
# just owns the editor-facing presentation.
#
# The rewrite is gated on CEREBRO_CLAUDE_BASE_URL being set (a custom endpoint):
# on the Anthropic subscription path the catalog models aren't available at the
# API, so the editor must see the SDK's stock Anthropic picker unchanged. A
# missing/unparseable/empty catalog also leaves the upstream's model option
# untouched.
_CATALOG_PATH = os.path.join(CEREBRO_HOME, "models-config.json")
_SUPERVISOR_MODEL = os.environ.get("CEREBRO_SUPERVISOR_MODEL") or ""
_HAS_CUSTOM_ENDPOINT = bool(os.environ.get("CEREBRO_CLAUDE_BASE_URL"))


def _load_catalog() -> list[dict]:
    """Read $CEREBRO_HOME/models-config.json and return the `.models` array.
    Returns [] on a missing/unreadable/invalid file, or when the proxy is on
    the Anthropic subscription path (no CEREBRO_CLAUDE_BASE_URL) -- in both
    cases the caller treats the result as "no catalog -- pass the upstream
    picker through unchanged"."""
    if not _HAS_CUSTOM_ENDPOINT:
        return []
    try:
        with open(_CATALOG_PATH) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    models = data.get("models") if isinstance(data, dict) else None
    return [m for m in models if isinstance(m, dict) and m.get("id")] if isinstance(models, list) else []


def _catalog_model_option(current_value: str) -> SessionConfigOptionSelect:
    """Build a `model` SessionConfigOptionSelect from the catalog."""
    options: list[SessionConfigSelectOption] = []
    for m in _load_catalog():
        options.append(SessionConfigSelectOption(
            value=str(m["id"]),
            name=str(m.get("name") or m["id"]),
            description=str(m["description"]) if m.get("description") else None,
        ))
    return SessionConfigOptionSelect(
        id="model",
        name="Model",
        description="AI model to use",
        category="model",
        type="select",
        current_value=current_value,
        options=options,
    )


def _rewrite_model_option(config_options: Optional[list]) -> Optional[list]:
    """Replace the `model` entry in a config_options list with the catalog-
    sourced option, preserving the upstream's currentValue (what the SDK is
    actually running) and leaving all other options untouched. Returns the
    list unchanged when the catalog is empty (the subscription path / no
    catalog case keeps the stock anthropic picker) or when the upstream
    didn't include a `model` option (nothing to replace)."""
    if not config_options:
        return config_options
    if not _load_catalog():
        return config_options
    result: list = []
    replaced = False
    for opt in config_options:
        if getattr(opt, "id", None) == "model":
            result.append(_catalog_model_option(
                current_value=str(getattr(opt, "current_value", _SUPERVISOR_MODEL) or _SUPERVISOR_MODEL)
            ))
            replaced = True
        else:
            result.append(opt)
    if not replaced:
        # Upstream had no `model` option (e.g. a backend that doesn't expose
        # one). Inject ours at the end so the editor still gets a picker.
        result.append(_catalog_model_option(current_value=_SUPERVISOR_MODEL))
    return result



@dataclass
class SessionState:
    """One open ACP session: the cerebro id (shown to the editor), the upstream
    child's foreign id, and the live child connection + process."""
    cerebro_sid: str
    foreign_sid: Optional[str] = None
    relay: Optional["RelayClient"] = None
    child: Any = None          # ClientSideConnection to the upstream child
    proc: Any = None           # asyncio subprocess
    _cm: Any = None            # spawn_agent_process context manager (to exit)
    project_dir: str = ""


class RelayClient:
    """The CLIENT side of the upstream child (implements acp.Client). The child
    drives this: its session/update notifications and agent->client requests
    (request_permission, elicitation, fs/*, terminal/*) land here, and we
    forward each to the editor via the agent-side connection (self.agent.zed),
    remapping the child's foreign sessionId -> the cerebro sessionId the editor
    knows. Responses flow back the same way."""

    def __init__(self, agent: "CerebroAgent", cerebro_sid: str) -> None:
        self.agent = agent
        self.cerebro_sid = cerebro_sid
        self.foreign_sid: Optional[str] = None
        self.child: Any = None  # ClientSideConnection, set by on_connect

    def set_foreign(self, foreign_sid: str) -> None:
        self.foreign_sid = foreign_sid

    def _csid(self, session_id: str) -> str:
        """Child sessionId -> cerebro sessionId."""
        return self.cerebro_sid if session_id == self.foreign_sid else session_id

    def on_connect(self, conn: Any) -> None:
        self.child = conn

    # ----- notifications / requests the child sends to us (-> editor) -----

    async def session_update(self, session_id: str, update: Any, **kw: Any) -> None:
        # config_option_update notifications carry the upstream's config_options
        # (with the Anthropic-tier-labeled `model` row). Rewrite it so the
        # editor's picker stays catalog-driven. The update object is a pydantic
        # model with a `configOptions` field (alias config_options); we mutate it
        # in place before forwarding.
        if getattr(update, "session_update", None) == "config_option_update":
            co = getattr(update, "config_options", None)
            rewritten = _rewrite_model_option(co)
            if rewritten is not None and rewritten is not co:
                try:
                    update.config_options = rewritten
                except Exception:
                    pass
        await self.agent.zed.session_update(
            session_id=self._csid(session_id), update=update, **kw
        )

    async def request_permission(
        self, session_id: str, tool_call: Any, options: list, **kw: Any
    ) -> Any:
        return await self.agent.zed.request_permission(
            session_id=self._csid(session_id), tool_call=tool_call, options=options, **kw
        )

    async def read_text_file(
        self, session_id: str, path: str, line: Optional[int] = None,
        limit: Optional[int] = None, **kw: Any
    ) -> Any:
        return await self.agent.zed.read_text_file(
            session_id=self._csid(session_id), path=path, line=line, limit=limit, **kw
        )

    async def write_text_file(
        self, session_id: str, path: str, content: str, **kw: Any
    ) -> Any:
        return await self.agent.zed.write_text_file(
            session_id=self._csid(session_id), path=path, content=content, **kw
        )

    async def create_terminal(
        self, session_id: str, command: str, args: Optional[list] = None,
        env: Optional[list] = None, cwd: Optional[str] = None,
        output_byte_limit: Optional[int] = None, **kw: Any
    ) -> Any:
        return await self.agent.zed.create_terminal(
            session_id=self._csid(session_id), command=command, args=args, env=env,
            cwd=cwd, output_byte_limit=output_byte_limit, **kw
        )

    async def terminal_output(self, session_id: str, terminal_id: str, **kw: Any) -> Any:
        return await self.agent.zed.terminal_output(
            session_id=self._csid(session_id), terminal_id=terminal_id, **kw
        )

    async def release_terminal(self, session_id: str, terminal_id: str, **kw: Any) -> Any:
        return await self.agent.zed.release_terminal(
            session_id=self._csid(session_id), terminal_id=terminal_id, **kw
        )

    async def wait_for_terminal_exit(
        self, session_id: str, terminal_id: str, **kw: Any
    ) -> Any:
        return await self.agent.zed.wait_for_terminal_exit(
            session_id=self._csid(session_id), terminal_id=terminal_id, **kw
        )

    async def kill_terminal(self, session_id: str, terminal_id: str, **kw: Any) -> Any:
        return await self.agent.zed.kill_terminal(
            session_id=self._csid(session_id), terminal_id=terminal_id, **kw
        )

    async def create_elicitation(self, message: str, mode: Any, **kw: Any) -> Any:
        # Elicitation is not sessionId-scoped in the protocol; pass straight through.
        return await self.agent.zed.create_elicitation(message=message, mode=mode, **kw)

    async def complete_elicitation(self, elicitation_id: str, **kw: Any) -> None:
        await self.agent.zed.complete_elicitation(elicitation_id=elicitation_id, **kw)

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        return await self.agent.zed.ext_method(method=method, params=self.agent._remap_to_client(params))

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        await self.agent.zed.ext_notification(
            method=method, params=self.agent._remap_to_client(params)
        )


class CerebroAgent:
    """The AGENT side facing the editor (implements acp.Agent). The editor's
    requests flow through here; we forward each to the matching session's
    upstream child, remapping cerebro sessionId -> the child's foreign sessionId,
    and return the child's response (with the session_id swapped back where the
    response carries one)."""

    def __init__(self) -> None:
        self.zed: Any = None  # AgentSideConnection to the editor (set in on_connect)
        self.sessions: dict[str, SessionState] = {}

    def on_connect(self, conn: Any) -> None:
        self.zed = conn

    # ----- sessionId remap helpers -----

    def _to_foreign(self, cerebro_sid: str) -> Optional[str]:
        st = self.sessions.get(cerebro_sid)
        return st.foreign_sid if st else None

    def _remap_to_child(self, params: Any) -> Any:
        """For ext forwarding editor->child: cerebro sessionId in params -> foreign."""
        if isinstance(params, dict) and params.get("session_id") in self.sessions:
            params = dict(params)
            params["session_id"] = self.sessions[params["session_id"]].foreign_sid
        return params

    def _remap_to_client(self, params: Any) -> Any:
        """For ext forwarding child->editor: foreign sessionId in params -> cerebro."""
        if isinstance(params, dict):
            for st in self.sessions.values():
                if params.get("session_id") == st.foreign_sid:
                    params = dict(params)
                    params["session_id"] = st.cerebro_sid
                    break
        return params

    # ----- cerebro shell-out helpers -----

    async def _mint(self) -> str:
        """Mint a cerebro session + its ACP project dir; returns the cerebro sid."""
        p = await asyncio.create_subprocess_exec(
            CEREBRO_BIN, "acp", "mint",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await p.communicate()
        if p.returncode != 0:
            raise RuntimeError(f"cerebro acp mint failed: {err.decode(errors='replace').strip()}")
        return out.decode().strip()

    async def _record_foreign(self, cerebro_sid: str, foreign_sid: str) -> None:
        p = await asyncio.create_subprocess_exec(
            CEREBRO_BIN, "acp", "set-foreign", cerebro_sid, foreign_sid,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        await p.wait()

    def _read_foreign(self, cerebro_sid: str) -> Optional[str]:
        md = os.path.join(CEREBRO_HOME, "sessions", cerebro_sid, "metadata.json")
        if not os.path.isfile(md):
            return None
        try:
            with open(md) as f:
                metadata = json.load(f)
        except Exception:
            return None
        role = metadata.get("role", "supervisor")
        if role != "supervisor":
            raise RuntimeError(f"unsupported parent role for cerebro session {cerebro_sid}: {role}")
        if metadata.get("backend") != "claude":
            raise RuntimeError(f"ACP is unavailable for recorded backend: {metadata.get('backend')}")
        return metadata.get("foreign_session_id") or None

    async def _pin(self, child: Any, foreign_sid: str) -> None:
        """Force the cerebro-orchestrator via set_config_option."""
        try:
            await child.set_config_option(
                config_id=_SPEC_PIN["config_id"],
                session_id=foreign_sid,
                value=_SPEC_PIN["value"],
            )
        except Exception as e:  # pin failure is surfaced but non-fatal
            _log(f"pin set_config_option({_SPEC_PIN['config_id']}={_SPEC_PIN['value']}) failed: {e}")

    async def _spawn_child(self, cerebro_sid: str) -> SessionState:
        """Spawn the upstream child for a session, rooted at the cerebro-owned
        ACP project dir (the session cwd). Returns the live SessionState."""
        project_dir = os.path.join(CEREBRO_HOME, "acp", cerebro_sid)
        relay = RelayClient(self, cerebro_sid)
        env = _build_child_env(cerebro_sid)
        # Inherit stderr (stderr=None) so the upstream child's diagnostics flow
        # through to this process's stderr instead of an unread PIPE that would
        # deadlock the child once its OS pipe buffer fills.
        cm = spawn_agent_process(
            relay, *_SPEC_ARGV, env=env, cwd=project_dir,
            transport_kwargs={"stderr": None},
        )
        child, proc = await cm.__aenter__()
        st = SessionState(
            cerebro_sid=cerebro_sid, relay=relay, child=child, proc=proc,
            _cm=cm, project_dir=project_dir,
        )
        self.sessions[cerebro_sid] = st
        return st

    async def _teardown(self, cerebro_sid: str) -> None:
        st = self.sessions.pop(cerebro_sid, None)
        if not st:
            return
        try:
            if st._cm is not None:
                await st._cm.__aexit__(None, None, None)
        except Exception:
            pass
        try:
            if st.proc is not None and st.proc.returncode is None:
                st.proc.terminate()
        except Exception:
            pass

    # ----- acp.Agent protocol -----

    async def initialize(
        self, protocol_version: int, client_capabilities: Any = None,
        client_info: Any = None, **kw: Any
    ) -> InitializeResponse:
        # No child exists at init (children are per-session). Advertise the union
        # of what the upstreams provide so the editor enables the full surface;
        # the upstream child enforces the real per-session behavior.
        return InitializeResponse(
            protocol_version=acp.PROTOCOL_VERSION,
            agent_capabilities=AgentCapabilities(
                load_session=True,
                prompt_capabilities=PromptCapabilities(
                    image=True, audio=True, embedded_context=True
                ),
                mcp_capabilities=McpCapabilities(http=True, sse=True, acp=True),
                session_capabilities=SessionCapabilities(
                    list=SessionListCapabilities(),
                    additional_directories=SessionAdditionalDirectoriesCapabilities(),
                    resume=SessionResumeCapabilities(),
                    close=SessionCloseCapabilities(),
                ),
            ),
            agent_info=Implementation(name="cerebro", title="cerebro", version="1.0"),
        )

    async def new_session(
        self, cwd: str, additional_directories: Optional[list] = None,
        mcp_servers: Optional[list] = None, **kw: Any
    ) -> NewSessionResponse:
        cerebro_sid = await self._mint()
        st = await self._spawn_child(cerebro_sid)
        # The user's repo (the editor's cwd) becomes an additional_directory;
        # the cerebro project dir is the session cwd (where the orchestrator
        # agent file lives, so the upstream surfaces it as a selectable option).
        adds = [cwd] + (additional_directories or [])
        try:
            ns = await st.child.new_session(
                cwd=st.project_dir, additional_directories=adds,
                mcp_servers=mcp_servers, **kw
            )
        except Exception:
            await self._teardown(cerebro_sid)
            raise
        foreign_sid = ns.session_id
        st.foreign_sid = foreign_sid
        st.relay.set_foreign(foreign_sid)
        await self._pin(st.child, foreign_sid)
        await self._record_foreign(cerebro_sid, foreign_sid)
        return NewSessionResponse(
            session_id=cerebro_sid,
            modes=ns.modes,
            config_options=_rewrite_model_option(ns.config_options),
        )

    async def prompt(self, session_id: str, prompt: list, **kw: Any):
        st = self.sessions.get(session_id)
        if not st:
            raise RuntimeError(f"unknown session: {session_id}")
        return await st.child.prompt(session_id=st.foreign_sid, prompt=prompt, **kw)

    async def cancel(self, session_id: str, **kw: Any) -> None:
        st = self.sessions.get(session_id)
        if st:
            await st.child.cancel(session_id=st.foreign_sid, **kw)

    async def set_config_option(
        self, config_id: str, session_id: str, value: Any, **kw: Any
    ) -> Optional[SetSessionConfigOptionResponse]:
        st = self.sessions.get(session_id)
        if not st:
            return None
        resp = await st.child.set_config_option(
            config_id=config_id, session_id=st.foreign_sid, value=value, **kw
        )
        # The SDK's response carries the updated config_options (with the
        # Anthropic-tier-labeled `model` row). Rewrite it so the editor's
        # picker stays catalog-driven after a model switch.
        if resp is not None:
            resp.config_options = _rewrite_model_option(resp.config_options)
        return resp

    async def set_session_mode(
        self, session_id: str, mode_id: str, **kw: Any
    ) -> Optional[SetSessionModeResponse]:
        st = self.sessions.get(session_id)
        if not st:
            return None
        return await st.child.set_session_mode(
            session_id=st.foreign_sid, mode_id=mode_id, **kw
        )

    async def authenticate(self, method_id: str, **kw: Any) -> Optional[AuthenticateResponse]:
        # Auth is pre-session; cerebro advertises no auth_methods, so this is
        # not expected to be called. No-op.
        return None

    async def load_session(
        self, cwd: str, session_id: str, mcp_servers: Optional[list] = None,
        additional_directories: Optional[list] = None, **kw: Any
    ) -> Optional[LoadSessionResponse]:
        cerebro_sid = session_id
        foreign_sid = self._read_foreign(cerebro_sid)
        if not foreign_sid:
            raise RuntimeError(f"no upstream session recorded for cerebro session {cerebro_sid}")
        st = await self._spawn_child(cerebro_sid)
        adds = [cwd] + (additional_directories or [])
        resp = await st.child.load_session(
            cwd=st.project_dir, session_id=foreign_sid,
            mcp_servers=mcp_servers, additional_directories=adds, **kw
        )
        st.foreign_sid = foreign_sid
        st.relay.set_foreign(foreign_sid)
        await self._pin(st.child, foreign_sid)  # retain the supervisor role
        return LoadSessionResponse(modes=resp.modes, config_options=_rewrite_model_option(resp.config_options))

    async def resume_session(
        self, session_id: str, cwd: str, additional_directories: Optional[list] = None,
        mcp_servers: Optional[list] = None, **kw: Any
    ) -> Optional[ResumeSessionResponse]:
        cerebro_sid = session_id
        foreign_sid = self._read_foreign(cerebro_sid)
        if not foreign_sid:
            raise RuntimeError(f"no upstream session recorded for cerebro session {cerebro_sid}")
        st = await self._spawn_child(cerebro_sid)
        adds = [cwd] + (additional_directories or [])
        resp = await st.child.resume_session(
            session_id=foreign_sid, cwd=st.project_dir,
            additional_directories=adds, mcp_servers=mcp_servers, **kw
        )
        st.foreign_sid = foreign_sid
        st.relay.set_foreign(foreign_sid)
        await self._pin(st.child, foreign_sid)
        return ResumeSessionResponse(modes=resp.modes, config_options=_rewrite_model_option(resp.config_options))

    async def list_sessions(
        self, cwd: Optional[str] = None, cursor: Optional[str] = None, **kw: Any
    ) -> ListSessionsResponse:
        # Answer from cerebro's own session store (no upstream child needed).
        sessions: list[SessionInfo] = []
        sdir = os.path.join(CEREBRO_HOME, "sessions")
        if os.path.isdir(sdir):
            for name in sorted(os.listdir(sdir), reverse=True):
                md = os.path.join(sdir, name, "metadata.json")
                if not os.path.isfile(md):
                    continue
                try:
                    with open(md) as f:
                        m = json.load(f)
                except Exception:
                    continue
                if m.get("role", "supervisor") != "supervisor" or m.get("backend") != "claude":
                    continue
                sessions.append(SessionInfo(
                    session_id=m.get("cerebro_session_id", name),
                    cwd="",
                    additional_directories=None,
                    title=None,
                    updated_at=m.get("last_touched"),
                ))
        return ListSessionsResponse(sessions=sessions, next_cursor=None)

    async def close_session(self, session_id: str, **kw: Any) -> Optional[CloseSessionResponse]:
        st = self.sessions.get(session_id)
        resp: Optional[CloseSessionResponse] = None
        if st:
            try:
                resp = await st.child.close_session(session_id=st.foreign_sid, **kw)
            except Exception:
                pass
            await self._teardown(session_id)
        return resp

    async def fork_session(self, *a: Any, **kw: Any) -> ForkSessionResponse:
        # Not advertised (session_capabilities.fork = False); reject if called.
        raise RuntimeError("cerebro acp does not support session/fork in v1")

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        sid = params.get("session_id") if isinstance(params, dict) else None
        st = self.sessions.get(sid) if sid else None
        if not st:
            return {}
        return await st.child.ext_method(method=method, params=self._remap_to_child(params))

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        sid = params.get("session_id") if isinstance(params, dict) else None
        st = self.sessions.get(sid) if sid else None
        if st:
            await st.child.ext_notification(method=method, params=self._remap_to_child(params))


def main() -> None:
    try:
        asyncio.run(run_agent(CerebroAgent()))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
