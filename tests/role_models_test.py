"""Independent role defaults reach every native parent launch and its children."""

import json
import os
from pathlib import Path
import subprocess
import tempfile

from pi_fixture import seed_session
from task_lifecycle_test import LifecycleTests

root = Path(__file__).resolve().parent.parent


with tempfile.TemporaryDirectory(prefix='cerebro-role-model-tests-') as temporary:
    home = Path(temporary)
    session = home / 'sessions' / 'model-session'
    session.mkdir(parents=True)
    native = home / 'native'
    native.write_text('''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ['TEST_NATIVE_LOG'], 'a') as log:
    log.write(json.dumps(args) + '\\n')
if args == ['--version']:
    print('0.99.2')
elif 'mcp' in args and 'list' in args:
    print('[]')
else:
    print(json.dumps({'argv': args,
        'anthropic_model': os.environ.get('ANTHROPIC_MODEL'),
        'haiku_model': os.environ.get('ANTHROPIC_DEFAULT_HAIKU_MODEL'),
        'context_window': os.environ.get('CLAUDE_CODE_AUTO_COMPACT_WINDOW'),
        'backend': os.environ.get('CEREBRO_RESUME_BACKEND') or os.environ.get('CEREBRO_BACKEND')}))
''')
    native.chmod(0o755)
    native_session = home / 'native-parent.jsonl'
    seed_session(native_session, home)
    settings = {'model': 'fixture/implementation', 'supervisor_model': 'fixture/supervisor',
                'review_model': 'fixture/review', 'supervisor_effort': 'opaque-parent-effort',
                'implementor_effort': 'opaque-worker-effort', 'review_effort': 'opaque-review-effort'}
    (home / 'config.json').write_text(json.dumps(settings))
    (home / 'models-config.json').write_text(json.dumps({'models': [
        {'id': settings['supervisor_model'], 'contextTokens': 1000000}]}))
    environment = {'HOME': os.environ['HOME'], 'PATH': os.environ['PATH'],
                   'CEREBRO_HOME': str(home), 'CEREBRO_SESSION_ID': session.name,
                   'CEREBRO_SESSION_DIR': str(session),
                   'CEREBRO_JEV_ENABLED': '0', 'CEREBRO_JEV_API_KEY': '',
                   'CEREBRO_INPUT_OWNER': 'external',
                   'TEST_NATIVE_LOG': str(home / 'native-argv.jsonl'),
                   'CEREBRO_PI_CMD': str(native), 'CEREBRO_CODEX_CMD': str(native),
                   'CEREBRO_CLAUDE_CMD': str(native)}
    config_shell = ('. "$1/config.sh"; printf "%s %s %s" '
                    '"$CEREBRO_SUPERVISOR_MODEL" "$CEREBRO_MODEL" "$CEREBRO_REVIEW_MODEL"')
    for overrides, expected in (
        ({}, 'fixture/supervisor fixture/implementation fixture/review'),
        ({'CEREBRO_SUPERVISOR_MODEL': 'env-supervisor'}, 'env-supervisor fixture/implementation fixture/review'),
        ({'CEREBRO_MODEL': 'env-implementation'}, 'fixture/supervisor env-implementation fixture/review'),
        ({'CEREBRO_REVIEW_MODEL': 'env-review'}, 'fixture/supervisor fixture/implementation env-review'),
    ):
        result = subprocess.run(['bash', '-c', config_shell, '_', str(root / 'lib')],
                                env={**environment, **overrides}, text=True, capture_output=True, timeout=5)
        assert result.returncode == 0 and result.stdout == expected, (result.stderr, result.stdout)

    load = ('CEREBRO_LIB_DIR="$1"; . "$1/config.sh"; '
            'for f in "$1"/*.sh; do [[ "$f" == "$1/config.sh" ]] || . "$f"; done; '
            'for f in "$1"/commands/*.sh; do . "$f"; done; ')
    shell = load + 'materialise_home; "$2" "$CEREBRO_SESSION_DIR" "$3" "$4"'
    for backend in ('pi', 'codex', 'claude'):
        selected = {**environment, 'CEREBRO_BACKEND': backend,
                    'CEREBRO_CLAUDE_BASE_URL': 'http://127.0.0.1:9/fixture'}
        for action, role, native_id in (
            ('launch_orchestrator', 'supervisor', ''),
            ('resume_orchestrator', 'supervisor', str(native_session) if backend == 'pi' else 'native-supervisor'),
        ):
            (session / 'metadata.json').write_text(json.dumps({'role': role, 'backend': backend}))
            result = subprocess.run(['bash', '-c', shell, '_', str(root / 'lib'),
                                     'backend_' + backend + '_' + action, native_id, 'target'],
                                    env=selected, cwd=home, text=True, capture_output=True, timeout=10)
            assert result.returncode == 0 and not result.stderr, result.stderr
            launched = json.loads(result.stdout)
            argv = launched['argv']
            assert argv[argv.index('--model') + 1] == settings['supervisor_model'], argv
            if backend == 'codex':
                assert 'model_reasoning_effort=' + json.dumps(settings['supervisor_effort']) in argv, argv
            else:
                flag = '--thinking' if backend == 'pi' else '--effort'
                assert argv[argv.index(flag) + 1] == settings['supervisor_effort'], argv
            if backend == 'claude':
                assert launched['anthropic_model'] == settings['supervisor_model'], launched
                assert launched['haiku_model'] == settings['supervisor_model'], launched
                assert launched['context_window'] == '1000000', launched
            binding = json.loads((session / ('tools-' + role + '.json')).read_text())['mcpServers']['cerebro']['env']
            for key, value in settings.items():
                assert binding['CEREBRO_' + key.upper()] == value, binding

    empty_roles = {'CEREBRO_' + key.upper(): '' for key in settings}
    for backend in ('pi', 'codex', 'claude'):
        (session / 'metadata.json').write_text(json.dumps({'role': 'supervisor', 'backend': backend}))
        result = subprocess.run(['bash', '-c', shell, '_', str(root / 'lib'),
                                 'backend_' + backend + '_launch_orchestrator', '', ''],
                                env={**environment, **empty_roles, 'CEREBRO_BACKEND': backend},
                                cwd=home, text=True, capture_output=True, timeout=10)
        assert result.returncode == 0, result.stderr
        argv = json.loads(result.stdout)['argv']
        assert '--model' not in argv and '--effort' not in argv and '--thinking' not in argv, argv
        assert not any(item.startswith('model_reasoning_effort=') for item in argv), argv
        binding = json.loads((session / 'tools-supervisor.json').read_text())['mcpServers']['cerebro']['env']
        assert all(binding[key] == '' for key in empty_roles), binding

    fixture = LifecycleTests()
    fixture.setUp()
    try:
        (fixture.home / 'config.json').write_text(json.dumps(settings))
        fixture.env.update(empty_roles)
        for backend in ('pi', 'codex', 'claude'):
            fixture.env['CEREBRO_BACKEND'] = backend
            (fixture.session / 'metadata.json').write_text(json.dumps({'backend': backend}))
            fixture.packet['task'] = 'Explicit empty environment selects native defaults: ' + backend
            fixture.log.write_text('')
            result = fixture.cli('execute', packet=fixture.packet)
            assert result['review_model_relation'] == 'native defaults unresolved', result
            assert fixture.stages() == ['execute', 'review']
            if backend == 'codex':
                starts = [item['params'] for item in fixture.records() if item.get('method') == 'thread/start']
                turns = [item['params'] for item in fixture.records() if item.get('method') == 'turn/start']
                assert all('model' not in item for item in starts), starts
                assert all('effort' not in item for item in turns), turns
            else:
                argvs = [item['argv'] for item in fixture.records() if 'argv' in item]
                assert all('--model' not in argv and '--effort' not in argv and '--thinking' not in argv
                           for argv in argvs), argvs
    finally:
        fixture.doCleanups()

    native_log = Path(environment['TEST_NATIVE_LOG'])
    for backend in ('pi', 'codex', 'claude'):
        stored = home / 'sessions' / ('resume-' + backend)
        stored.mkdir()
        metadata = stored / 'metadata.json'
        recorded = {'backend': backend, 'foreign_session_id': str(native_session) if backend == 'pi' else 'native-supervisor',
                    'last_touched': '2020-01-01T00:00:00Z'}
        selected = {**environment,
                    'CEREBRO_BACKEND': 'claude' if backend == 'pi' else 'pi',
                    'CEREBRO_CLAUDE_BASE_URL': 'http://127.0.0.1:9/fixture'}
        for role in ('observer', 'reviewer', 'execute', 'unknown-role', None):
            metadata.write_text(json.dumps({**recorded, 'role': role}))
            before = metadata.read_bytes()
            native_log.unlink(missing_ok=True)
            result = subprocess.run([str(root / 'bin' / 'cerebro'), '--resume', stored.name],
                                    env=selected, text=True, capture_output=True, timeout=5)
            error = 'cannot read the recorded parent role' if role is None else 'unsupported parent role: ' + role
            assert result.returncode != 0 and error in result.stderr, (backend, role, result.stderr)
            assert not native_log.exists(), 'refused parent resume reached a native backend'
            assert metadata.read_bytes() == before, 'refused parent resume changed recorded metadata'
        metadata.write_text('{malformed')
        before = metadata.read_bytes()
        result = subprocess.run([str(root / 'bin' / 'cerebro'), '--resume', stored.name],
                                env=selected, text=True, capture_output=True, timeout=5)
        assert result.returncode != 0 and 'cannot read the recorded parent role' in result.stderr, result.stderr
        assert not native_log.exists() and metadata.read_bytes() == before
        for parent in ({}, {'role': 'supervisor'}):
            metadata.write_text(json.dumps({**recorded, **parent}))
            result = subprocess.run([str(root / 'bin' / 'cerebro'), '--resume', stored.name],
                                    env=selected, text=True, capture_output=True, timeout=10)
            assert result.returncode == 0, result.stderr
            launched = json.loads(result.stdout)
            assert launched['backend'] == backend and native_log.exists(), launched
            assert launched['argv'][launched['argv'].index('--model') + 1] == settings['supervisor_model'], launched

    invalid_backend_session = home / 'sessions' / 'unsupported-recorded-backend'
    invalid_backend_session.mkdir()
    metadata = invalid_backend_session / 'metadata.json'
    for backend in (None, '', 'unsupported-backend', 7):
        recorded = {'role': 'supervisor', 'foreign_session_id': str(native_session),
                    'last_touched': '2020-01-01T00:00:00Z'}
        if backend is not None:
            recorded['backend'] = backend
        for argv in (['--resume', invalid_backend_session.name],
                     ['execute', str(home), '--prompt', 'must not reach a native backend']):
            metadata.write_text(json.dumps(recorded))
            before = metadata.read_bytes()
            native_log.unlink(missing_ok=True)
            result = subprocess.run([str(root / 'bin' / 'cerebro'), *argv],
                                    env={**environment, 'CEREBRO_BACKEND': 'pi',
                                         'CEREBRO_SESSION_ID': invalid_backend_session.name},
                                    text=True, capture_output=True, timeout=5)
            assert result.returncode != 0 and 'session metadata has no supported backend' in result.stderr, result.stderr
            assert not native_log.exists(), 'incomplete backend metadata selected a native backend'
            assert metadata.read_bytes() == before, 'refused backend metadata was mutated'

    gateway = {**environment, 'CEREBRO_BACKEND': 'claude',
               'CEREBRO_CLAUDE_BASE_URL': 'http://127.0.0.1:9/fixture'}
    transition = (load + 'backend_claude_endpoint_env "$2"; backend_claude_endpoint_env "$3"; '
                  'printf "%s" "${CLAUDE_CODE_AUTO_COMPACT_WINDOW:-}"')
    for initial, selected, expected in (
        (settings['supervisor_model'], settings['model'], ''),
        (settings['supervisor_model'], '', ''),
    ):
        result = subprocess.run(['bash', '-c', transition, '_', str(root / 'lib'), initial, selected],
                                env=gateway, text=True, capture_output=True, timeout=5)
        assert result.returncode == 0 and result.stdout == expected, (result.stderr, result.stdout)
    acp = load + 'backend_claude_acp_child_spec'
    result = subprocess.run(['bash', '-c', acp, '_', str(root / 'lib')],
                            env={**gateway, 'CEREBRO_SUPERVISOR_MODEL': settings['model'],
                                 'ANTHROPIC_MODEL': settings['supervisor_model'],
                                 'CLAUDE_CODE_AUTO_COMPACT_WINDOW': '1000000'},
                            text=True, capture_output=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['env']['CLAUDE_CODE_AUTO_COMPACT_WINDOW'] is None

    (home / 'config.json').write_text(json.dumps({'model': settings['model']}))
    result = subprocess.run(['bash', '-c', config_shell, '_', str(root / 'lib')],
                            env=environment, text=True, capture_output=True, timeout=5)
    assert result.returncode == 0 and result.stdout == ' fixture/implementation ', result.stderr
    for backend in ('pi', 'codex', 'claude'):
        result = subprocess.run(['bash', '-c', shell, '_', str(root / 'lib'),
                                 'backend_' + backend + '_launch_orchestrator', '', ''],
                                env={**environment, 'CEREBRO_BACKEND': backend,
                                     'CEREBRO_CLAUDE_BASE_URL': 'http://127.0.0.1:9/fixture',
                                     'ANTHROPIC_MODEL': 'native-default',
                                     'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'native-housekeeping',
                                     'CLAUDE_CODE_AUTO_COMPACT_WINDOW': '200000'},
                                cwd=home, text=True, capture_output=True, timeout=10)
        assert result.returncode == 0, result.stderr
        launched = json.loads(result.stdout)
        assert '--model' not in launched['argv'], launched
        if backend == 'claude':
            assert launched['anthropic_model'] == 'native-default', launched
            assert launched['haiku_model'] == 'native-housekeeping' and launched['context_window'] == '200000', launched
    result = subprocess.run(['bash', '-c', acp, '_', str(root / 'lib')],
                            env=gateway, text=True, capture_output=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert not any(key in json.loads(result.stdout)['env']
                   for key in ('ANTHROPIC_MODEL', 'ANTHROPIC_DEFAULT_HAIKU_MODEL', 'CLAUDE_CODE_AUTO_COMPACT_WINDOW'))

print('all checks passed')
