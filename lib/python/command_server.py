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
from wait_detached import job_response, wait_for_update

OPERATIONS = {"execute", "answer", "steer", "restart", "wait", "cancel", "status", "jobs", "worktrees"}
LONG = {"execute", "answer"}


def run_command(role, executable, argv, stdin=""):
    if not isinstance(argv, list) or not argv or any(not isinstance(a, str) or "\0" in a for a in argv):
        raise ValueError("argv must be a nonempty array of literal strings")
    if not isinstance(stdin, str):
        raise ValueError("stdin must be text")
    if role != "supervisor" or argv[0] not in OPERATIONS:
        raise ValueError("unknown supervisor operation: " + argv[0])
    env = {**os.environ, "CEREBRO_ROLE": role}
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
        return job_response(job, wait_for_update(job["status"]))
    proc = subprocess.run([executable, *argv], input=stdin, text=True, env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if argv[0] == 'wait':
        try:
            return json.loads(proc.stdout)
        except ValueError:
            pass
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
                    "description": "Run a Cerebro orchestration operation. Literal argv; large bodies go in stdin. Child commands wait for completion or a Jev scope notice and survive parent disconnects. After handling a notice, wait <job-id> --after <sequence> --disposition continue|correct|stop --note <reason>. Native inspection tools remain available.",
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
