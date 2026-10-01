"""Native MCP configurations preserve the settings used by delegated children."""

import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parent.parent
libraries = root / 'lib' / 'python'
sys.path.insert(0, str(libraries))
from codex_launch import guarded_options
from opencode_config import config as opencode_config


with tempfile.TemporaryDirectory(prefix='cerebro-mcp-environment-tests-') as temporary:
    home = Path(temporary)
    session = home / 'sessions' / 'configured-session'
    (session / 'children').mkdir(parents=True)
    (session / 'transcript.jsonl').touch()
    guards = home / 'guards'
    guards.mkdir()
    for backend in ('opencode', 'claude', 'codex'):
        guard = guards / backend
        guard.write_text('#!/usr/bin/env bash\nprintf "unexpected backend fixture\\n" >&2\nexit 97\n')
        guard.chmod(0o755)
    log = home / 'native.jsonl'
    wrapper = home / 'selected-codex'
    wrapper.write_text('#!/usr/bin/env bash\nexport NATIVE_FIXTURE_LOG="' + str(log) + '"\n'
                       'exec python3 "' + str(Path(__file__).with_name('native_child_fixture.py')) + '" codex "$@"\n')
    wrapper.chmod(0o755)
    settings = {
        'CEREBRO_HOME': str(home), 'CEREBRO_SESSION_ID': session.name,
        'CEREBRO_SESSION_DIR': str(session), 'CEREBRO_ROLE': 'supervisor',
        'CEREBRO_BACKEND': 'codex', 'CEREBRO_CODEX_CMD': str(wrapper),
        'CEREBRO_OPENCODE_CMD': str(guards / 'opencode'), 'CEREBRO_CLAUDE_CMD': str(guards / 'claude'),
        'CEREBRO_MODEL': 'configured/native-model', 'CEREBRO_REVIEW_MODEL': 'configured/review-model',
        'CEREBRO_TIMEOUT': '10', 'CEREBRO_CHILD_IDLE_TIMEOUT': '0',
        'CEREBRO_PAIR_IDLE': '0.15', 'CEREBRO_PAIR_STALL': '5', 'CEREBRO_PAIR_STALL_BUSY': '5',
        'CEREBRO_PAIR_STALL_RETRIES': '1', 'CEREBRO_PAIR_STALL_BACKOFF': '0',
        'CEREBRO_PLAYWRIGHT_ISOLATED': '1',
        'CEREBRO_CLAUDE_BASE_URL': 'http://127.0.0.1:9/fixture', 'CEREBRO_CLAUDE_AUTH_TOKEN': 'fixture-token',
        'CLAUDE_CONFIG_DIR': str(home / 'claude-config'),
        'OPENCODE_CONFIG_DIR': str(home / 'opencode-config'), 'XDG_CONFIG_HOME': str(home / 'xdg-config'),
    }
    if 'CODEX_HOME' in os.environ:
        settings['CODEX_HOME'] = os.environ['CODEX_HOME']
    host = {'HOME': os.environ['HOME'], 'PATH': str(guards) + ':' + os.environ['PATH']}
    shell = 'CEREBRO_LIB_DIR="$1"; . "$1/config.sh"; . "$1/backend.sh"; backend_supervisor_config supervisor'
    previous = dict(os.environ)
    try:
        for backend in ('claude', 'opencode', 'codex'):
            selected = {**settings, 'CEREBRO_BACKEND': backend}
            environment = {**host, **selected, 'OPENAI_API_KEY': 'fixture-provider-token'}
            prepared = subprocess.run(['bash', '-c', shell, '_', str(root / 'lib')], env=environment,
                                      text=True, capture_output=True, timeout=5)
            assert prepared.returncode == 0, prepared.stderr
            config_path = Path(prepared.stdout.strip())
            assert config_path.stat().st_mode & 0o777 == 0o600, 'MCP configuration must remain private'
            server = json.loads(config_path.read_text())['mcpServers']['cerebro']
            os.environ.clear()
            os.environ.update(environment)
            if backend == 'claude':
                for key, value in selected.items():
                    assert server['env'].get(key) == value, 'Claude MCP lost ' + key
            elif backend == 'opencode':
                opencode = opencode_config(home, 'supervisor')['mcp']['servers']['cerebro']
                for key, value in selected.items():
                    assert opencode['environment'].get(key) == value, 'OpenCode MCP lost ' + key
            else:
                options = guarded_options(str(wrapper), 'supervisor', str(home), str(session))
                codex = next(option for option in options if option.startswith('mcp_servers='))
                for key, value in selected.items():
                    assert json.dumps(key) + '=' + json.dumps(value) in codex, 'Codex MCP lost ' + key
                assert '"OPENAI_API_KEY"' in codex, 'Codex MCP dropped native auth inheritance'
                assert 'fixture-provider-token' not in codex, 'Codex MCP serialized a provider credential'
    finally:
        os.environ.clear()
        os.environ.update(previous)

    repo = home / 'repo'
    subprocess.run(['git', 'init', '-q', '-b', 'main', str(repo)], check=True)
    subprocess.run(['git', '-C', str(repo), 'config', 'user.name', 'test'], check=True)
    subprocess.run(['git', '-C', str(repo), 'config', 'user.email', 'test@example.com'], check=True)
    subprocess.run(['git', '-C', str(repo), 'commit', '-q', '--allow-empty', '-m', 'test fixture'], check=True)
    log.write_text('')
    # Native hosts whitelist environment variables. Recreate that boundary:
    # no original CEREBRO_* setting is inherited outside the emitted config.
    proc = subprocess.Popen([server['command'], *server['args']], env={**host, **server['env']},
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        messages = [
            {'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2024-11-05',
             'capabilities': {}, 'clientInfo': {'name': 'cerebro-tests', 'version': '1'}}},
            {'id': 2, 'method': 'tools/call', 'params': {'name': 'command', 'arguments': {
             'argv': ['execute', str(repo), '--prompt', 'retain configured native settings']}}},
        ]
        for message in messages:
            proc.stdin.write((json.dumps({'jsonrpc': '2.0', **message}) + '\n').encode())
            proc.stdin.flush()
            ready, _, _ = select.select([proc.stdout], [], [], 20)
            assert ready, 'configured MCP request timed out'
            response = json.loads(proc.stdout.readline())
            assert response['id'] == message['id'] and 'error' not in response, response
            if message['method'] == 'initialize':
                proc.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
                proc.stdin.flush()
        result = response['result']
        assert not result.get('isError'), result
        assert json.loads(result['content'][0]['text'])['exit_code'] == 0
    finally:
        proc.stdin.close()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        proc.stdout.close()
        proc.stderr.close()
    native = [json.loads(line) for line in log.read_text().splitlines()]
    thread = next(event['params'] for event in native if event.get('method') == 'thread/start')
    assert thread['model'] == settings['CEREBRO_MODEL'], 'delegated child switched its configured model'
    store = json.loads((session / 'child-sessions.json').read_text())
    assert any(child['provider'] == 'codex' and child['status'] == 'done' for child in store.values())

print('all checks passed')
