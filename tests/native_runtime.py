"""Exercise installed native CLIs against isolated, deterministic local providers.

Run with optional --parents, or --pair and --background, followed by backend
names. Artifacts are retained under /tmp; no model account is required.
"""
import json
import os
from pathlib import Path
import shlex
import fcntl
import pty
import queue
import re
import select
import signal
import struct
import termios
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SOURCE = Path(__file__).resolve().parent.parent
ROOT = Path(tempfile.mkdtemp(prefix='cerebro-real-backends-', dir='/tmp'))
REQUESTS = []
COUNTS = {}
LOCK = threading.Lock()
PAIR_READY = threading.Event()


def action(body, backend):
    raw = json.dumps(body)
    tools = body.get('tools', [])
    names = [t.get('name') or t.get('function', {}).get('name') for t in tools]
    # A parent keeps its original task marker in its conversation after tools.
    parent = 'NATIVE_PARENT' in raw and ('cerebro_command' in names or 'mcp__cerebro__command' in names or 'mcp__cerebro' in names or backend == 'opencode' and 'skill' in names)
    reviewer = 'NATIVE_REVIEW' in raw
    marker = 'parent' if parent else 'review' if reviewer else 'worker' if 'NATIVE_WORKER' in raw else 'other'
    key = (backend, marker)
    with LOCK:
        COUNTS[key] = COUNTS.get(key, 0) + 1
        call = COUNTS[key]
        REQUESTS.append({'backend': backend, 'scenario': marker, 'call': call, 'tools': names, 'tool_schemas': [tool for tool in tools if 'cerebro' in (tool.get('name') or '')]})
        (ROOT / 'requests.json').write_text(json.dumps(REQUESTS, indent=2))
    if parent:
        assert not any(n in names for n in ('shell', 'Bash', 'Write', 'Edit', 'Agent', 'subagent', 'execute')), names
    if reviewer and backend != 'codex':
        assert not any(n in names for n in ('shell', 'Bash', 'Write', 'Edit', 'Agent', 'subagent', 'execute')), names
    if parent and backend == 'opencode' and call == 1:
        return 'skill', {'name': 'cerebro-commands'}, None
    if parent and call == (2 if backend == 'opencode' else 1):
        name = 'cerebro_command' if backend == 'opencode' else 'mcp__cerebro__command'
        assert name in names, names
        return name, {'argv': ['execute', str(ROOT / backend / 'repo'), '--prompt', 'NATIVE_WORKER: write the proof file and report completion.', '--branch', 'feat/native-proof']}, None
    if reviewer and backend == 'codex' and call == 1:
        return 'exec_command', {'cmd': "printf BAD > review-escape.txt", 'yield_time_ms': 1000}, None
    if marker == 'worker' and call == 1:
        name = {'opencode': 'shell', 'claude': 'Bash', 'codex': 'exec_command'}[backend]
        assert name in names, names
        command = ("sleep 2; " if '--background' in sys.argv else '') + "printf '%s' NATIVE_PROOF > native-proof.txt"
        args = {'command': command, 'description': 'Write isolated native proof'} if backend == 'claude' else {'command': command} if backend == 'opencode' else {'cmd': command, 'yield_time_ms': 1000}
        if '--background' in sys.argv and backend == 'opencode':
            args['background'] = True
        if backend == 'claude':
            assert 'TaskStop' in names, names
        return name, args, None
    if marker == 'worker' and call == 2 and '--pair' in sys.argv:
        PAIR_READY.set()
    if marker == 'worker' and 'NATIVE_SCOPE_CORRECTION' in raw:
        return None, None, 'NATIVE_WORKER_STEERED'
    return None, None, 'NATIVE_' + marker.upper() + '_DONE'


class Provider(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass
    def do_GET(self):
        self.reply('application/json', json.dumps({'data': [{'id': 'test', 'type': 'model'}]}).encode())
    def reply(self, mime, data):
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        self.wfile.flush()
    def do_POST(self):
        try:
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
            if self.path.endswith('count_tokens'):
                return self.reply('application/json', b'{"input_tokens":100}')
            backend = 'claude' if '/messages' in self.path else 'codex' if '/responses' in self.path else 'opencode'
            name, args, value = action(body, backend)
            if backend == 'opencode':
                delta = {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': 'call_local', 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}]} if name else {'role': 'assistant', 'content': value}
                chunks = [{'id': 'chatcmpl-local', 'object': 'chat.completion.chunk', 'created': int(time.time()), 'model': 'test', 'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]}, {'id': 'chatcmpl-local', 'object': 'chat.completion.chunk', 'created': int(time.time()), 'model': 'test', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'tool_calls' if name else 'stop'}], 'usage': {'prompt_tokens': 100, 'completion_tokens': 10, 'total_tokens': 110}}]
                data = ''.join('data: ' + json.dumps(c) + '\n\n' for c in chunks) + 'data: [DONE]\n\n'
            elif backend == 'claude':
                message = {'id': 'msg_local', 'type': 'message', 'role': 'assistant', 'model': body.get('model', 'test'), 'content': [], 'stop_reason': None, 'stop_sequence': None, 'usage': {'input_tokens': 100, 'output_tokens': 0}}
                block = {'type': 'tool_use', 'id': 'toolu_local', 'name': name, 'input': {}} if name else {'type': 'text', 'text': ''}
                delta = {'type': 'input_json_delta', 'partial_json': json.dumps(args)} if name else {'type': 'text_delta', 'text': value}
                events = [('message_start', {'type': 'message_start', 'message': message}), ('content_block_start', {'type': 'content_block_start', 'index': 0, 'content_block': block}), ('content_block_delta', {'type': 'content_block_delta', 'index': 0, 'delta': delta}), ('content_block_stop', {'type': 'content_block_stop', 'index': 0}), ('message_delta', {'type': 'message_delta', 'delta': {'stop_reason': 'tool_use' if name else 'end_turn', 'stop_sequence': None}, 'usage': {'output_tokens': 10}}), ('message_stop', {'type': 'message_stop'})]
                data = ''.join('event: ' + k + '\ndata: ' + json.dumps(v) + '\n\n' for k, v in events)
            else:
                response = {'id': 'resp_local', 'object': 'response', 'created_at': int(time.time()), 'model': 'test', 'status': 'in_progress', 'output': []}
                item = {'type': 'function_call', 'id': 'fc_local', 'call_id': 'call_local', 'name': name, 'arguments': '', 'status': 'in_progress'} if name else {'type': 'message', 'id': 'msg_local', 'role': 'assistant', 'status': 'in_progress', 'content': []}
                if name == 'mcp__cerebro__command':
                    item.update(name='command', namespace='mcp__cerebro')
                events = [{'type': 'response.created', 'response': response}, {'type': 'response.output_item.added', 'output_index': 0, 'item': item}]
                if name:
                    events += [{'type': 'response.function_call_arguments.delta', 'item_id': 'fc_local', 'output_index': 0, 'delta': json.dumps(args)}, {'type': 'response.function_call_arguments.done', 'item_id': 'fc_local', 'output_index': 0, 'arguments': json.dumps(args)}]
                    item = {**item, 'arguments': json.dumps(args), 'status': 'completed'}
                else:
                    part = {'type': 'output_text', 'text': '', 'annotations': []}
                    events += [{'type': 'response.content_part.added', 'item_id': 'msg_local', 'output_index': 0, 'content_index': 0, 'part': part}, {'type': 'response.output_text.delta', 'item_id': 'msg_local', 'output_index': 0, 'content_index': 0, 'delta': value}, {'type': 'response.output_text.done', 'item_id': 'msg_local', 'output_index': 0, 'content_index': 0, 'text': value}, {'type': 'response.content_part.done', 'item_id': 'msg_local', 'output_index': 0, 'content_index': 0, 'part': {**part, 'text': value}}]
                    item = {**item, 'status': 'completed', 'content': [{**part, 'text': value}]}
                events += [{'type': 'response.output_item.done', 'output_index': 0, 'item': item}, {'type': 'response.completed', 'response': {**response, 'status': 'completed', 'output': [item], 'usage': {'input_tokens': 100, 'output_tokens': 10, 'total_tokens': 110}}}]
                data = ''.join('event: ' + e['type'] + '\ndata: ' + json.dumps(e) + '\n\n' for e in events)
            self.reply('text/event-stream', data.encode())
        except Exception as exc:
            (ROOT / 'provider-error.txt').write_text(str(exc))
            self.send_error(500)


server = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
threading.Thread(target=server.serve_forever, daemon=True).start()
base = 'http://127.0.0.1:' + str(server.server_port)


def prepare(backend):
    if backend not in ('opencode', 'codex', 'claude'):
        raise ValueError('unknown backend: ' + backend)
    actual = shutil.which(backend)
    if not actual:
        raise RuntimeError('native CLI is not installed: ' + backend)
    version = subprocess.check_output([actual, '--version'], text=True).strip()
    print(backend + ' version: ' + version, flush=True)
    directory = ROOT / backend
    repo = directory / 'repo'
    session = directory / 'home' / 'sessions' / 'native-session'
    (session / 'children').mkdir(parents=True)
    (session / 'plans').mkdir()
    repo.mkdir()
    (repo / 'AGENTS.md').write_text('Work only in the announced isolated worktree.\n')
    subprocess.run(['git', 'init', '-q', '-b', 'main', str(repo)], check=True)
    subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(repo), '-c', 'user.name=Native Verification', '-c', 'user.email=native@localhost', 'commit', '-qm', 'test fixture'], check=True)
    env = dict(os.environ)
    for key in list(env):
        if key.startswith(('CEREBRO_', 'OPENCODE', 'ANTHROPIC', 'OPENAI', 'GEMINI', 'GOOGLE', 'AWS', 'AZURE', 'CLAUDE')):
            env.pop(key)
    env.update(CEREBRO_HOME=str(directory / 'home'), CEREBRO_SESSION_ID='native-session', CEREBRO_SESSION_DIR=str(session), CEREBRO_BACKEND=backend, CEREBRO_TIMEOUT='45', CEREBRO_PAIR_IDLE='0.2', CEREBRO_PAIR_STALL='30', CEREBRO_PAIR_STALL_BUSY='30', CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1')
    guards = directory / 'guards'
    guards.mkdir()
    for native in ('opencode', 'codex', 'claude'):
        guard = guards / native
        guard.write_text('#!/usr/bin/env bash\nprintf "unconfigured native backend launch\\n" >&2\nexit 97\n')
        guard.chmod(0o755)
    env['PATH'] = str(guards) + ':' + env['PATH']
    (session / 'metadata.json').write_text(json.dumps({'id': 'native-session', 'backend': backend, 'role': 'supervisor', 'created_at': '2026-10-01T00:00:00Z'}))
    if backend == 'opencode':
        env['CEREBRO_OPENCODE_CMD'] = shutil.which('opencode')
        for kind in ('CONFIG', 'DATA', 'CACHE', 'STATE'):
            path = directory / kind.lower(); path.mkdir()
            env['XDG_' + kind + '_HOME'] = str(path)
        env['OPENCODE_TEST_HOME'] = str(directory / 'native-home')
        env['OPENCODE_CONFIG_DIR'] = str(directory / 'config')
        # ACP can snapshot the stock catalog before configured providers load.
        # Disable its seed catalog so this fixture can never select a remote model.
        env['OPENCODE_CONFIG_CONTENT'] = json.dumps({'plugins': ['-opencode.models.dev'], 'providers': {'cerebro-local': {'package': 'aisdk:@ai-sdk/openai-compatible', 'settings': {'baseURL': base + '/v1', 'apiKey': 'local-test'}, 'models': {'test': {'name': 'Local Test', 'limit': {'context': 32768, 'output': 2048}, 'capabilities': {'tools': True, 'input': ['text'], 'output': ['text']}}}}}, 'snapshots': False, 'agents': {'general': {'permissions': [{'action': '*', 'resource': '*', 'effect': 'allow'}]}, 'build': {'permissions': [{'action': '*', 'resource': '*', 'effect': 'allow'}]}}})
        env['CEREBRO_MODEL'] = 'cerebro-local/test'
    elif backend == 'claude':
        env.update(CEREBRO_CLAUDE_CMD=shutil.which('claude'), CLAUDE_CONFIG_DIR=str(directory / 'native-home'), CEREBRO_CLAUDE_BASE_URL=base, CEREBRO_CLAUDE_AUTH_TOKEN='local-test', CEREBRO_MODEL='test')
        native_home = directory / 'native-home'
        native_home.mkdir()
        (native_home / '.claude.json').write_text(json.dumps({'hasCompletedOnboarding': True, 'theme': 'dark', 'projects': {str((directory / 'home').resolve()): {'hasTrustDialogAccepted': True}}}))
    else:
        wrapper = directory / 'codex-local'
        provider = '{name="Local Verification",base_url="' + base + '/v1",wire_api="responses",requires_openai_auth=false}'
        trust = 'projects={' + json.dumps(str((directory / 'home').resolve())) + '={trust_level="trusted"}}'
        wrapper.write_text('#!/usr/bin/env bash\nexec ' + shlex.quote(actual) + ' -c model_provider=\"cerebro-local-native\" -c ' + shlex.quote('model_providers.cerebro-local-native=' + provider) + ' -c ' + shlex.quote(trust) + ' "$@"\n')
        wrapper.chmod(0o755)
        env.update(CEREBRO_CODEX_CMD=str(wrapper), CEREBRO_MODEL='test')
    if '--parents' in sys.argv:
        prompt = 'NATIVE_PARENT: delegate the isolated task through Cerebro and report completion.'
        if backend != 'codex':
            actual = shutil.which(backend)
            wrapper = directory / (backend + '-local')
            if backend == 'opencode':
                wrapper.write_text('#!/usr/bin/env bash\nif [[ "${1:-}" == --standalone ]]; then exec ' + shlex.quote(actual) + ' "$@" --prompt ' + shlex.quote(prompt) + '; fi\nexec ' + shlex.quote(actual) + ' "$@"\n')
            else:
                wrapper.write_text('#!/usr/bin/env bash\nfor arg in "$@"; do if [[ "$arg" == -p ]]; then exec ' + shlex.quote(actual) + ' "$@"; fi; done\nexec ' + shlex.quote(actual) + ' "$@" ' + shlex.quote(prompt) + '\n')
            wrapper.chmod(0o755)
            env['CEREBRO_' + backend.upper() + '_CMD'] = str(wrapper)
        else:
            body = wrapper.read_text()
            command = body.splitlines()[1].replace(' "$@"', '')
            wrapper.write_text('#!/usr/bin/env bash\nfor arg in "$@"; do case "$arg" in app-server|mcp) ' + command + ' "$@";; esac; done\n' + command + ' "$@" ' + shlex.quote(prompt) + '\n')
    return env, repo, session


def call(env, *args):
    result = subprocess.run([str(SOURCE / 'bin' / 'cerebro'), *args], env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    name = env['CEREBRO_BACKEND'] + '-' + args[0]
    (ROOT / (name + '.stdout')).write_text(result.stdout)
    (ROOT / (name + '.stderr')).write_text(result.stderr)
    assert_provider_ok()
    assert result.returncode == 0, name + ' failed rc=' + str(result.returncode) + ' see ' + str(ROOT / (name + '.stderr'))
    return result.stdout


def assert_provider_ok():
    error = ROOT / 'provider-error.txt'
    if error.exists():
        raise AssertionError('local provider rejected a native request: ' + error.read_text())


def paired(env, repo, session):
    PAIR_READY.clear()
    env['CEREBRO_PAIR_IDLE'] = '2'
    error_path = ROOT / (env['CEREBRO_BACKEND'] + '-pair.stderr')
    with error_path.open('w') as errors:
        proc = subprocess.Popen([str(SOURCE / 'bin' / 'cerebro'), 'execute', str(repo), '--pair',
                                 '--prompt', 'NATIVE_WORKER: write the proof file and report completion.',
                                 '--branch', 'feat/native-proof'], env=env, text=True, stdout=subprocess.PIPE,
                                stderr=errors, start_new_session=True)
        try:
            assert PAIR_READY.wait(30), 'native first turn did not finish; see ' + str(error_path)
            fifos = list((session / 'children').glob('*.steer.fifo'))
            assert len(fifos) == 1, fifos
            call(env, 'steer', str(fifos[0]), 'NATIVE_SCOPE_CORRECTION: continue within the approved contract.')
            stdout, _ = proc.communicate(timeout=45)
            (ROOT / (env['CEREBRO_BACKEND'] + '-pair.stdout')).write_text(stdout)
            assert proc.returncode == 0, error_path.read_text()
            assert 'NATIVE_WORKER_STEERED' in stdout, stdout
            return stdout
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(timeout=8)


def parent(env):
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 40, 140, 0, 0))
    def terminal():
        os.setsid()
        fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    proc = subprocess.Popen([str(SOURCE / 'bin' / 'cerebro')], env={**env, 'TERM': 'xterm-256color'},
                            cwd=SOURCE, stdin=slave, stdout=slave, stderr=slave, preexec_fn=terminal)
    os.close(slave)
    buffer = b''
    started = time.monotonic()
    try:
        while time.monotonic() - started < 45:
            ready, _, _ = select.select([master], [], [], 0.2)
            if ready:
                try:
                    data = os.read(master, 65536)
                except OSError:
                    break
                buffer += data
                (ROOT / (env['CEREBRO_BACKEND'] + '-parent.tty')).write_bytes(buffer)
                if b'\x1b[6n' in data:
                    os.write(master, b'\x1b[1;1R')
                if b'\x1b[c' in data:
                    os.write(master, b'\x1b[?1;2c')
            text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', buffer.decode(errors='replace'))
            if 'NATIVE_PARENT_DONE' in text:
                sessions = Path(env['CEREBRO_HOME']) / 'sessions'
                bound = [json.loads(path.read_text()).get('foreign_session_id') for path in sessions.glob('*/metadata.json') if path.parent.name != 'native-session']
                if any(bound):
                    break
            if proc.poll() is not None:
                break
        (ROOT / (env['CEREBRO_BACKEND'] + '-parent.tty')).write_bytes(buffer)
        assert 'NATIVE_PARENT_DONE' in text, 'parent terminal did not complete; see retained tty'
        os.close(master)
        master = None
        try:
            proc.terminate()
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.wait(timeout=4)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except (ProcessLookupError, PermissionError):
                pass
            proc.wait(timeout=4)
        sessions = Path(env['CEREBRO_HOME']) / 'sessions'
        meta = [json.loads(path.read_text()) for path in sessions.glob('*/metadata.json') if path.parent.name != 'native-session']
        assert len(meta) == 1 and meta[0]['foreign_session_id'], 'native parent session binding missing'
        child_file = sessions / meta[0]['cerebro_session_id'] / 'child-sessions.json'
        children = json.loads(child_file.read_text())
        assert any(child['provider'] == env['CEREBRO_BACKEND'] and child['status'] == 'done' for child in children.values())
    finally:
        if master is not None:
            os.close(master)
        if proc.poll() is None:
            try:
                proc.kill()
            except (ProcessLookupError, PermissionError):
                pass
            proc.wait(timeout=4)


def acp_parent(env):
    sid = call(env, 'acp', 'mint').strip()
    session = Path(env['CEREBRO_HOME']) / 'sessions' / sid
    env = {**env, 'CEREBRO_SESSION_ID': sid, 'CEREBRO_SESSION_DIR': str(session)}
    log = ROOT / 'opencode-native-acp.jsonl'
    with (ROOT / 'opencode-native-acp.stderr').open('w') as errors:
        proc = subprocess.Popen([str(SOURCE / 'bin' / 'cerebro'), 'acp', 'opencode-child'],
                                env=env, cwd=Path(env['CEREBRO_HOME']) / 'acp' / sid,
                                text=True, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=errors, start_new_session=True)
        messages = queue.Queue()

        def receive():
            for line in proc.stdout:
                with log.open('a') as output:
                    output.write(line)
                messages.put(json.loads(line))

        threading.Thread(target=receive, daemon=True).start()

        def request(identifier, method, params):
            proc.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': identifier,
                                        'method': method, 'params': params}) + '\n')
            proc.stdin.flush()
            while True:
                message = messages.get(timeout=45)
                if message.get('id') == identifier and 'method' not in message:
                    assert 'error' not in message, message
                    return message['result']
                if 'id' in message and 'method' in message:
                    raise AssertionError('unexpected native client request: ' + repr(message))

        try:
            request(1, 'initialize', {'protocolVersion': 1, 'clientCapabilities': {},
                                    'clientInfo': {'name': 'cerebro-native-test', 'version': '1'}})
            created = request(2, 'session/new', {'cwd': str(Path(env['CEREBRO_HOME']) / 'acp' / sid),
                                                'mcpServers': []})
            foreign = created['sessionId']
            selected = next(option for option in created['configOptions'] if option['id'] == 'model')
            assert selected['currentValue'] == env['CEREBRO_MODEL'], selected
            request(3, 'session/prompt', {'sessionId': foreign, 'prompt': [{'type': 'text',
                    'text': 'NATIVE_PARENT: delegate the isolated task through Cerebro and report completion.'}]})
            assert_provider_ok()
            assert 'NATIVE_PARENT_DONE' in log.read_text(), log.read_text()[-4000:]
            assert json.loads((session / 'metadata.json').read_text())['foreign_session_id'] == foreign
            children = json.loads((session / 'child-sessions.json').read_text())
            assert any(child['provider'] == 'opencode' and child['status'] == 'done'
                       for child in children.values()), children
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=4)


try:
    print('native verification artifacts:', ROOT, flush=True)
    parent_mode = '--parents' in sys.argv
    acp_mode = '--acp' in sys.argv
    assert sum(flag in sys.argv for flag in ('--parents', '--pair', '--acp')) <= 1, 'select one native mode'
    backends = [arg for arg in sys.argv[1:] if not arg.startswith('--')] or (['opencode'] if acp_mode else ['opencode', 'codex', 'claude'])
    for backend in backends:
        if acp_mode and backend != 'opencode':
            raise ValueError('--acp exercises the native OpenCode launcher only')
        env, repo, session = prepare(backend)
        if acp_mode:
            acp_parent(env)
            print('opencode: native ACP session delegated through guarded MCP and bound its exact native ID', flush=True)
            continue
        if parent_mode:
            parent(env)
            assert_provider_ok()
            print(backend + ': real parent terminal delegated through guarded MCP, native child completed, exact native parent ID bound', flush=True)
            continue
        stdout = paired(env, repo, session) if '--pair' in sys.argv else call(env, 'execute', str(repo), '--prompt', 'NATIVE_WORKER: write the proof file and report completion.', '--branch', 'feat/native-proof')
        assert ('NATIVE_WORKER_STEERED' if '--pair' in sys.argv else 'NATIVE_WORKER_DONE') in stdout, stdout
        children = json.loads((session / 'child-sessions.json').read_text())
        worker = next(v for v in children.values() if v['role'] == 'execute')
        trees = subprocess.check_output(['git', '-C', str(repo), 'worktree', 'list', '--porcelain'], text=True)
        path = next(Path(line[9:]) for line in trees.splitlines() if line.startswith('worktree ') and Path(line[9:]).resolve() != repo.resolve())
        assert (path / 'native-proof.txt').read_text() == 'NATIVE_PROOF'
        assert not (repo / 'native-proof.txt').exists()
        call(env, 'answer', worker['id'], 'Continue within the approved contract.')
        children2 = json.loads((session / 'child-sessions.json').read_text())
        assert next(v for v in children2.values() if v['role'] == 'execute')['id'] == worker['id']
        criteria = session / 'plans' / 'native-review.md'
        criteria.write_text('NATIVE_REVIEW: inspect the isolated proof file.')
        report = Path(call(env, 'review', str(path), '--criteria-file', str(criteria)).strip())
        assert 'NATIVE_REVIEW_DONE' in report.read_text(), report.read_text()
        assert not (path / 'review-escape.txt').exists(), 'native read-only review wrote a repository file'
        assert_provider_ok()
        print(backend + ': real CLI child wrote only worktree, same native session resumed, guarded same-backend review completed', flush=True)
    print('native backend checks passed', flush=True)
finally:
    server.shutdown()
