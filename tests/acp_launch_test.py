"""Native OpenCode ACP launch config is constructed after each session is minted."""

import ast
import json
import os
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parent.parent
binary = root / 'bin' / 'cerebro'
fixture = Path(__file__).with_name('opencode_fixture.py')


with tempfile.TemporaryDirectory(prefix='cerebro-acp-launch-tests-') as temporary:
    directory = Path(temporary)
    home = directory / 'home'
    guards = directory / 'guards'
    guards.mkdir()
    for backend in ('opencode', 'claude', 'codex'):
        guard = guards / backend
        guard.write_text('#!/usr/bin/env bash\nprintf "unexpected backend fixture\\n" >&2\nexit 97\n')
        guard.chmod(0o755)
    wrapper = directory / 'selected-opencode'
    wrapper.write_text('#!/usr/bin/env bash\nexec python3 "' + str(fixture) + '" "' + str(directory) + '" "$@"\n')
    wrapper.chmod(0o755)
    context_log = directory / 'native-launches.jsonl'
    (directory / 'fixture.json').write_text(json.dumps({
        'launch_log': str(directory / 'argv.jsonl'), 'launch_context_log': str(context_log)}))
    provider_config = {'providers': {'acp-local': {
        'package': 'aisdk:@ai-sdk/openai-compatible',
        'settings': {'baseURL': 'http://127.0.0.1:9/v1', 'apiKey': 'local-fixture'},
        'models': {'test': {'name': 'ACP local fixture'}}}}}
    environment = {
        'HOME': os.environ['HOME'], 'PATH': str(guards) + ':' + os.environ['PATH'],
        'CEREBRO_HOME': str(home), 'CEREBRO_BACKEND': 'opencode',
        'CEREBRO_OPENCODE_CMD': str(wrapper), 'CEREBRO_CLAUDE_CMD': str(guards / 'claude'),
        'CEREBRO_CODEX_CMD': str(guards / 'codex'), 'CEREBRO_MODEL': 'acp-local/test',
        'OPENCODE_CONFIG_DIR': str(directory / 'native-config'),
        'OPENCODE_CONFIG_CONTENT': json.dumps(provider_config),
    }
    # This runs before the ACP proxy's first session/new, with no session binding.
    shell = ('CEREBRO_LIB_DIR="$1"; . "$1/config.sh"; . "$1/backend.sh"; '
             '. "$1/backend-opencode.sh"; backend_acp_child_spec')
    result = subprocess.run(['bash', '-c', shell, '_', str(root / 'lib')], env=environment,
                            text=True, capture_output=True, timeout=5)
    assert result.returncode == 0, result.stderr
    spec = json.loads(result.stdout)
    assert Path(spec['argv'][0]).resolve() == binary.resolve()
    assert spec['argv'][1:] == ['acp', 'opencode-child']
    assert spec['pin'] == {'config_id': 'mode', 'value': 'build'}
    assert not context_log.exists(), 'building the child spec launched a native process prematurely'

    # Exercise the actual dependency-free proxy environment helper without
    # importing its optional ACP SDK. Launching below uses the returned argv.
    source = ast.parse((root / 'lib' / 'python' / 'acp_server.py').read_text())
    helper = next(node for node in source.body if isinstance(node, ast.FunctionDef)
                  and node.name == '_build_child_env')
    namespace = {'os': os, '_SPEC_ENV': spec.get('env') or {}, 'CEREBRO_HOME': str(home)}
    exec(compile(ast.Module(body=[helper], type_ignores=[]), 'acp_server.py', 'exec'), namespace)
    original_environment = dict(os.environ)
    processes = []
    try:
        os.environ.clear()
        os.environ.update(environment)
        session_ids = []
        for _ in range(2):
            minted = subprocess.run([str(binary), 'acp', 'mint'], env=environment, text=True,
                                    capture_output=True, timeout=5)
            assert minted.returncode == 0, minted.stderr
            session_ids.append(minted.stdout.strip())
        assert len(set(session_ids)) == 2, 'native ACP sessions reused a Cerebro identity'
        for sid in session_ids:
            child_env = namespace['_build_child_env'](sid)
            processes.append(subprocess.Popen(spec['argv'], env=child_env,
                                              cwd=home / 'acp' / sid, text=True,
                                              stdout=subprocess.PIPE, stderr=subprocess.PIPE))
        for process in processes:
            stdout, stderr = process.communicate(timeout=10)
            assert process.returncode == 0, (stdout, stderr)
    finally:
        os.environ.clear()
        os.environ.update(original_environment)
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate()

    launches = [json.loads(line) for line in context_log.read_text().splitlines()]
    assert len(launches) == 2 and len({launch['pid'] for launch in launches}) == 2
    assert {launch['session_id'] for launch in launches} == set(session_ids)
    for launch in launches:
        sid = launch['session_id']
        session = home / 'sessions' / sid
        assert launch['argv'] == ['acp']
        assert Path(launch['cwd']).resolve() == (home / 'acp' / sid).resolve()
        assert Path(launch['session_dir']).resolve() == session.resolve()
        configuration = launch['config']
        assert configuration['providers'] == provider_config['providers']
        assert configuration['model'] == 'acp-local/test'
        assert configuration['default_agent'] == 'build'
        server = configuration['mcp']['servers']['cerebro']
        binding = server['environment']
        assert binding['CEREBRO_SESSION_ID'] == sid
        assert Path(binding['CEREBRO_SESSION_DIR']).resolve() == session.resolve()
        assert binding['CEREBRO_ROLE'] == 'supervisor'
        assert binding['CEREBRO_BACKEND'] == 'opencode'
        assert binding['CEREBRO_OPENCODE_CMD'] == str(wrapper)
        assert binding['CEREBRO_MODEL'] == 'acp-local/test'
        assert json.loads(binding['OPENCODE_CONFIG_CONTENT'])['providers'] == provider_config['providers']
        stored = json.loads((session / 'tools-supervisor.json').read_text())
        assert stored['mcpServers']['cerebro']['env'] == binding

print('all checks passed')
