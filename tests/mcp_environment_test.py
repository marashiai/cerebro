"""Native supervisor MCP carries role config/auth context into the task controller."""
import json
import os
from pathlib import Path
import subprocess
import sys

from task_lifecycle_test import LifecycleTests, ROOT
sys.path.insert(0, str(ROOT / 'lib/python'))
from codex_launch import supervisor_options
from pi_launch import run_argv

fixture = LifecycleTests()
fixture.setUp()
try:
    settings = {**fixture.env, 'CEREBRO_SESSION_DIR': str(fixture.session), 'CEREBRO_ROLE': 'supervisor',
                'CEREBRO_SUPERVISOR_MODEL': 'configured-supervisor', 'CEREBRO_SUPERVISOR_EFFORT': 'parent-effort',
                'CEREBRO_IMPLEMENTOR_EFFORT': 'worker-effort', 'CEREBRO_REVIEW_EFFORT': 'review-effort',
                'CLAUDE_CONFIG_DIR': str(fixture.root / 'claude-config'),
                'PI_CODING_AGENT_DIR': str(fixture.root / 'pi-config'),
                'OPENAI_API_KEY': 'fixture-provider-token'}
    shell = 'CEREBRO_LIB_DIR="$1"; . "$1/config.sh"; . "$1/backend.sh"; backend_supervisor_config supervisor'
    previous = dict(os.environ)
    for backend in ('claude', 'pi', 'codex'):
        selected = {**settings, 'CEREBRO_BACKEND': backend}
        prepared = subprocess.run(['bash', '-c', shell, '_', str(ROOT / 'lib')], env=selected,
                                  text=True, capture_output=True, timeout=5)
        assert prepared.returncode == 0, prepared.stderr
        path = Path(prepared.stdout.strip())
        assert path.stat().st_mode & 0o777 == 0o600
        server = json.loads(path.read_text())['mcpServers']['cerebro']
        for key in ('CEREBRO_MODEL', 'CEREBRO_REVIEW_MODEL', 'CEREBRO_SUPERVISOR_MODEL',
                    'CEREBRO_SUPERVISOR_EFFORT', 'CEREBRO_IMPLEMENTOR_EFFORT', 'CEREBRO_REVIEW_EFFORT',
                    'CLAUDE_CONFIG_DIR', 'PI_CODING_AGENT_DIR'):
            assert server['env'][key] == selected[key], key
        os.environ.clear()
        os.environ.update(selected)
        if backend == 'codex':
            options = supervisor_options(str(fixture.session))
            codex = next(item for item in options if item.startswith('mcp_servers.cerebro='))
            assert '"OPENAI_API_KEY"' in codex and 'fixture-provider-token' not in codex
        elif backend == 'pi':
            argv = run_argv('pi', 'supervisor', str(fixture.home), str(fixture.session), '', '', 'context')
            native = Path(argv[argv.index('--cerebro-mcp-config') + 1])
            assert native == path
        else:
            assert server['alwaysLoad'] is True
    os.environ.clear()
    os.environ.update(previous)

    # Recreate native MCP environment filtering: launch only with emitted config.
    (fixture.session / 'metadata.json').write_text('{"backend":"codex"}')
    proc = subprocess.Popen([server['command'], *server['args']], env=server['env'],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        for message in (
            {'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2024-11-05'}},
            {'id': 2, 'method': 'tools/call', 'params': {'name': 'command', 'arguments': {
                'argv': ['execute'], 'stdin': json.dumps(fixture.packet)}}},
        ):
            proc.stdin.write(json.dumps({'jsonrpc': '2.0', **message}) + '\n')
            proc.stdin.flush()
            response = json.loads(proc.stdout.readline())
            assert response['id'] == message['id'] and 'error' not in response, response
        output = json.loads(response['result']['content'][0]['text'])
        assert output['exit_code'] == 0, output
        task = json.loads(output['text'])
        assert task['stage'] == 'done' and task['packet']['models']['reviewer']['effort'] == 'review-effort'
        assert fixture.stages() == ['execute', 'review']
    finally:
        proc.stdin.close()
        proc.wait(timeout=5)
        proc.stdout.close()
        proc.stderr.close()
finally:
    os.environ.clear()
    os.environ.update(previous)
    fixture.doCleanups()
print('all checks passed')
