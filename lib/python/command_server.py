"""A stdio MCP tool exposing argv-only Cerebro commands, not a shell.

The server deliberately needs only the same Python runtime as the Bash CLI.
Long commands belong to persistent detached monitors; cancelling a tool call
or closing the native parent cannot reap the work it delegated.
"""

import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import uuid

from detach_process import launch
from wait_detached import wait_for_completion

READ = {"guide", "read", "grep", "ls", "status", "list", "recall", "models",
        "learnings", "jobs", "observe"}
SUPERVISOR = READ | {"spec", "plan", "execute", "audit", "review", "apply-review",
                     "verify", "doc-write", "improve", "answer", "steer", "restart",
                     "wait", "cancel", "detach", "git", "gh", "learn-note",
                     "learn-set", "overlay", "plans", "worktrees"}
OBSERVER_READ_ACTIONS = {"spec": {(), ("show",), ("history",)},
                         "plans": {()}, "worktrees": {(), ("list",)}}
OBSERVER = READ | set(OBSERVER_READ_ACTIONS) | {"steer", "restart"}
REVIEWER = {"guide", "read", "grep", "ls", "git"}
LONG = {"execute", "audit", "review", "apply-review", "verify", "doc-write", "improve", "answer"}


def run_command(role, executable, argv, stdin=""):
    if not isinstance(argv, list) or not argv or any(not isinstance(a, str) or "\0" in a for a in argv):
        raise ValueError("argv must be a nonempty array of literal strings")
    if not isinstance(stdin, str):
        raise ValueError("stdin must be text")
    allowed = {"supervisor": SUPERVISOR, "observer": OBSERVER, "reviewer": REVIEWER}[role]
    if argv[0] not in allowed:
        raise ValueError(f"{argv[0]} is unavailable to the {role}")
    if role == "observer" and argv[0] in OBSERVER_READ_ACTIONS \
            and tuple(argv[1:]) not in OBSERVER_READ_ACTIONS[argv[0]]:
        raise ValueError(f"observers may only read {argv[0]}")
    env = {**os.environ, "CEREBRO_ROLE": role}
    if role == "observer" and argv[0] in ("spec", "plans", "observe"):
        metadata = json.loads((Path(env["CEREBRO_SESSION_DIR"]) / "metadata.json").read_text())
        target = metadata.get("observe_target")
        if target:
            if argv[0] == "observe":
                if argv[1:] not in ([], [target]):
                    raise ValueError("observer is bound to session " + target)
                argv = ["observe", target]
            else:
                env["CEREBRO_SESSION_ID"] = target
                env["CEREBRO_SESSION_DIR"] = str(Path(env["CEREBRO_HOME"]) / "sessions" / target)
    if argv[0] in LONG:
        job_id = str(uuid.uuid4())
        session = Path(env["CEREBRO_SESSION_DIR"]).resolve()
        directory = (session / "detached-jobs").resolve()
        directory.relative_to(session)
        directory.mkdir(exist_ok=True)
        output = str(directory / (job_id + ".out"))
        job_file = str(directory / (job_id + ".json"))
        input_path = ""
        if stdin:
            input_path = str(directory / (job_id + ".stdin"))
            Path(input_path).write_text(stdin)
        job = launch(output, output + ".status", output + ".pid", job_file,
                     job_id, argv[0], [executable, *argv], input_path,
                     output + ".result", announce=False)
        rc = wait_for_completion(job["status"])
        text = Path(job["result"]).read_text(errors="replace")
        if rc:
            text += "\n" + Path(output).read_text(errors="replace")[-4000:]
        return {"exit_code": rc, "job_id": job_id, "output": output,
                "text": text}
    proc = subprocess.run([executable, *argv], input=stdin, text=True, env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return {"exit_code": proc.returncode, "text": proc.stdout}


def main():
    role, executable = sys.argv[1:]
    output_lock = threading.Lock()

    def reply(request_id, result=None, error=None):
        response = {"jsonrpc": "2.0", "id": request_id}
        response["error" if error else "result"] = error or result
        with output_lock:
            print(json.dumps(response), flush=True)

    def call(message):
        try:
            params = message.get("params", {})
            if params.get("name") != "command":
                raise ValueError("unknown tool")
            args = params.get("arguments", {})
            result = run_command(role, executable, args.get("argv"), args.get("stdin", ""))
            reply(message["id"], {"content": [{"type": "text", "text": json.dumps(result)}],
                                  "isError": result["exit_code"] != 0})
        except Exception as exc:
            reply(message["id"], {"content": [{"type": "text", "text": str(exc)}], "isError": True})

    with concurrent.futures.ThreadPoolExecutor() as pool:
        for line in sys.stdin:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if "id" not in message:
                continue
            method = message.get("method")
            if method == "initialize":
                reply(message["id"], {"protocolVersion": message["params"]["protocolVersion"],
                                      "capabilities": {"tools": {}},
                                      "serverInfo": {"name": "cerebro", "version": "2.0.0"}})
            elif method == "ping":
                reply(message["id"], {})
            elif method == "tools/list":
                reply(message["id"], {"tools": [{"name": "command",
                    "description": "Run a guarded Cerebro command. Literal argv; large bodies go in stdin. Child commands block until completion and survive parent disconnects.",
                    "inputSchema": {"type": "object", "properties": {
                        "argv": {"type": "array", "items": {"type": "string"}},
                        "stdin": {"type": "string", "default": ""}},
                        "required": ["argv"], "additionalProperties": False}}]})
            elif method == "tools/call":
                pool.submit(call, message)
            else:
                reply(message["id"], error={"code": -32601, "message": "Method not found"})


if __name__ == "__main__":
    main()
