"""Launch the native Codex supervisor with its normal configuration and tools."""

import json
import os
from pathlib import Path
import queue
import shlex
import subprocess
import sys
import threading


def toml(value):
    if isinstance(value, dict):
        return '{' + ','.join(json.dumps(k) + '=' + toml(v) for k, v in value.items()) + '}'
    if isinstance(value, list):
        return '[' + ','.join(toml(item) for item in value) + ']'
    return json.dumps(value)


def native_hook_options(executable, cwd, session_dir):
    command = shlex.join([sys.executable, str(Path(__file__).with_name('native_input.py')),
                          'codex', str(session_dir)])
    options = ['-c', 'hooks.UserPromptSubmit=' + toml([
        {'hooks': [{'type': 'command', 'command': command, 'async': False, 'timeout': 10}]}])]
    process = subprocess.Popen([executable, 'app-server', *options], cwd=cwd,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, text=True)
    events = queue.Queue()

    def reader():
        try:
            for line in process.stdout:
                events.put(json.loads(line))
        finally:
            events.put(None)

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()

    def request(number, method, params):
        process.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': number,
                                        'method': method, 'params': params}) + '\n')
        process.stdin.flush()
        while True:
            event = events.get(timeout=10)
            if event is None:
                raise RuntimeError('Codex hook metadata process exited before replying')
            if event.get('id') == number:
                if 'error' in event:
                    raise RuntimeError('Codex hook metadata request failed: ' + json.dumps(event['error']))
                return event['result']

    try:
        request(1, 'initialize', {'clientInfo': {'name': 'cerebro-hook-binding', 'version': '1'},
                                  'capabilities': {'experimentalApi': True}})
        process.stdin.write('{"jsonrpc":"2.0","method":"initialized","params":{}}\n')
        process.stdin.flush()
        result = request(2, 'hooks/list', {'cwds': [str(Path(cwd).resolve())]})
        matches = [hook for entry in result['data'] for hook in entry['hooks']
                   if hook.get('source') == 'sessionFlags' and hook.get('eventName') == 'userPromptSubmit'
                   and hook.get('command') == command]
        if len(matches) != 1 or not matches[0]['enabled']:
            raise RuntimeError('Codex did not register the synchronous Cerebro input hook')
        hook = matches[0]
        options += ['-c', 'hooks.state=' + toml({hook['key']: {'trusted_hash': hook['currentHash']}})]
        return options
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=5)
        thread.join(timeout=1)
        process.stdout.close()


def supervisor_options(session_dir):
    config = json.loads((Path(session_dir) / 'tools-supervisor.json').read_text())
    server = config['mcpServers']['cerebro']
    server.update(enabled=True, required=True, tool_timeout_sec=86400, env_vars=list(os.environ))
    return ['-c', 'mcp_servers.cerebro=' + toml(server),
            '-c', 'features.code_mode.direct_only_tool_namespaces=' + toml(['mcp__cerebro'])]


def main():
    executable, role, cwd, session_dir, native_id, model = sys.argv[1:]
    if role != 'supervisor':
        raise ValueError('interactive Codex launch requires supervisor role')
    payloads = Path(__file__).resolve().parent.parent / 'payloads'
    instructions = (payloads / 'skills' / 'cerebro-supervisor' / 'SKILL.md').read_text()
    options = supervisor_options(session_dir)
    options += ['-c', 'developer_instructions=' + toml(instructions)]
    if os.environ.get('CEREBRO_INPUT_OWNER') != 'external':
        options += native_hook_options(executable, cwd, session_dir)
    if model:
        options += ['--model', model]
    effort = os.environ.get('CEREBRO_SUPERVISOR_EFFORT')
    if effort:
        options += ['-c', 'model_reasoning_effort=' + toml(effort)]
    argv = [executable, *options]
    if native_id:
        argv += ['resume', native_id]
    os.chdir(cwd)
    os.execvp(executable, argv)


if __name__ == '__main__':
    main()
