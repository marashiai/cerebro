"""Claude ACP launch binding and persisted parent-role guards."""

import ast
import asyncio
import json
import os
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parent.parent
binary = root / 'bin' / 'cerebro'


with tempfile.TemporaryDirectory(prefix='cerebro-acp-launch-tests-') as temporary:
    directory = Path(temporary)
    home = directory / 'home'
    guards = directory / 'guards'
    guards.mkdir()
    for backend in ('pi', 'claude', 'codex'):
        guard = guards / backend
        guard.write_text('#!/usr/bin/env bash\nprintf "unexpected backend fixture\\n" >&2\nexit 97\n')
        guard.chmod(0o755)
    context_log = directory / 'native-launches.jsonl'
    wrapper = guards / 'claude-agent-acp'
    wrapper.write_text('''#!/usr/bin/env python3
import json, os
from pathlib import Path
with open(os.environ['TEST_ACP_LOG'], 'a') as log:
    log.write(json.dumps({'pid': os.getpid(), 'cwd': os.getcwd(),
        'session_id': os.environ['CEREBRO_SESSION_ID'],
        'session_dir': os.environ['CEREBRO_SESSION_DIR'],
        'model': os.environ.get('ANTHROPIC_MODEL'), 'base_url': os.environ.get('ANTHROPIC_BASE_URL'),
        'mcp': json.loads(Path('.mcp.json').read_text()),
        'agent': Path('.claude/agents/cerebro-orchestrator.md').read_text()}) + '\\n')
''')
    wrapper.chmod(0o755)
    environment = {
        'HOME': os.environ['HOME'], 'PATH': str(guards) + ':' + os.environ['PATH'],
        'CEREBRO_HOME': str(home), 'CEREBRO_BACKEND': 'claude',
        'CEREBRO_PI_CMD': str(guards / 'pi'), 'CEREBRO_CLAUDE_CMD': str(guards / 'claude'),
        'CEREBRO_CODEX_CMD': str(guards / 'codex'), 'CEREBRO_MODEL': 'implementation-native',
        'CEREBRO_SUPERVISOR_MODEL': 'supervisor-native', 'CEREBRO_REVIEW_MODEL': 'review-native',
        'CEREBRO_JEV_ENABLED': '0', 'CEREBRO_JEV_API_KEY': '',
        'CEREBRO_CLAUDE_BASE_URL': 'http://127.0.0.1:9/fixture',
        'CEREBRO_CLAUDE_AUTH_TOKEN': 'fixture-token', 'TEST_ACP_LOG': str(context_log),
    }
    shell = ('CEREBRO_LIB_DIR="$1"; . "$1/config.sh"; . "$1/backend.sh"; '
             '. "$1/backend-claude.sh"; . "$1/helpers.sh"; . "$1/commands/models.sh"; backend_acp_child_spec')
    result = subprocess.run(['bash', '-c', shell, '_', str(root / 'lib')], env=environment,
                            text=True, capture_output=True, timeout=5)
    assert result.returncode == 0, result.stderr
    spec = json.loads(result.stdout)
    assert spec['argv'] == ['claude-agent-acp']
    assert spec['pin'] == {'config_id': 'agent', 'value': 'cerebro-orchestrator'}
    assert not context_log.exists(), 'building the child spec launched a native process prematurely'

    # Exercise the actual dependency-free proxy environment helper without
    # importing its optional ACP SDK. Launching below uses the returned argv.
    source = ast.parse((root / 'lib' / 'python' / 'acp_server.py').read_text())
    helper = next(node for node in source.body if isinstance(node, ast.FunctionDef)
                  and node.name == '_build_child_env')
    namespace = {'os': os, '_SPEC_ENV': spec.get('env') or {}, 'CEREBRO_HOME': str(home)}
    exec(compile(ast.Module(body=[helper], type_ignores=[]), 'acp_server.py', 'exec'), namespace)
    # Run the production resume guards without importing the optional ACP SDK.
    agent = next(node for node in source.body if isinstance(node, ast.ClassDef)
                 and node.name == 'CerebroAgent')
    methods = [node for node in agent.body
               if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
               and node.name in ('_read_foreign', 'load_session', 'resume_session')]
    guarded = ast.Module(body=[
        ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0),
        ast.ClassDef(name='SessionGuards', bases=[], keywords=[], body=methods, decorator_list=[]),
    ], type_ignores=[])
    guard_namespace = {'os': os, 'json': json, 'CEREBRO_HOME': str(home)}
    exec(compile(ast.fix_missing_locations(guarded), 'acp_server.py', 'exec'), guard_namespace)
    proxy = guard_namespace['SessionGuards']()
    spawned = []

    class SpawnReached(Exception):
        pass

    async def spawn(sid):
        spawned.append(sid)
        raise SpawnReached

    proxy._spawn_child = spawn
    resumed = home / 'sessions' / 'persisted-parent'
    resumed.mkdir(parents=True)
    metadata = resumed / 'metadata.json'
    recorded = {'backend': 'claude', 'foreign_session_id': 'native-parent', 'last_touched': '2020-01-01T00:00:00Z'}
    for role in ('observer', 'reviewer', 'execute', 'unknown-role', None):
        metadata.write_text(json.dumps({**recorded, 'role': role}))
        before = metadata.read_bytes()
        for action in ('load_session', 'resume_session'):
            try:
                asyncio.run(getattr(proxy, action)(cwd=str(directory), session_id=resumed.name))
            except RuntimeError as error:
                assert 'unsupported parent role' in str(error), str(error)
            else:
                raise AssertionError('ACP accepted a non-supervisor parent role: ' + repr(role))
            assert not spawned, 'refused ACP parent opened a native process'
            assert metadata.read_bytes() == before
    metadata.write_text('{malformed')
    for action in ('load_session', 'resume_session'):
        try:
            asyncio.run(getattr(proxy, action)(cwd=str(directory), session_id=resumed.name))
        except RuntimeError as error:
            assert 'no upstream session recorded' in str(error), str(error)
        else:
            raise AssertionError('ACP accepted unreadable parent metadata')
        assert not spawned
    for backend in ('pi', 'codex'):
        metadata.write_text(json.dumps({**recorded, 'backend': backend, 'role': 'supervisor'}))
        before = metadata.read_bytes()
        for action in ('load_session', 'resume_session'):
            try:
                asyncio.run(getattr(proxy, action)(cwd=str(directory), session_id=resumed.name))
            except RuntimeError as error:
                assert 'ACP is unavailable for recorded backend: ' + backend in str(error), str(error)
            else:
                raise AssertionError('ACP converted an unsupported recorded backend')
            assert not spawned and metadata.read_bytes() == before
    for parent in ({}, {'role': 'supervisor'}):
        metadata.write_text(json.dumps({**recorded, **parent}))
        assert proxy._read_foreign(resumed.name) == recorded['foreign_session_id']
        for action in ('load_session', 'resume_session'):
            try:
                asyncio.run(getattr(proxy, action)(cwd=str(directory), session_id=resumed.name))
            except SpawnReached:
                assert spawned == [resumed.name], spawned
            else:
                raise AssertionError('ACP supervisor never reached its native spawn')
            spawned.clear()

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
        assert Path(launch['cwd']).resolve() == (home / 'acp' / sid).resolve()
        assert Path(launch['session_dir']).resolve() == session.resolve()
        assert launch['model'] == 'supervisor-native'
        assert launch['base_url'] == environment['CEREBRO_CLAUDE_BASE_URL']
        binding = launch['mcp']['mcpServers']['cerebro']['env']
        assert binding['CEREBRO_SESSION_ID'] == sid
        assert Path(binding['CEREBRO_SESSION_DIR']).resolve() == session.resolve()
        assert binding['CEREBRO_ROLE'] == 'supervisor'
        assert binding['CEREBRO_BACKEND'] == 'claude'
        assert binding['CEREBRO_MODEL'] == 'implementation-native'
        assert binding['CEREBRO_SUPERVISOR_MODEL'] == 'supervisor-native'
        assert binding['CEREBRO_REVIEW_MODEL'] == 'review-native'
        stored = json.loads((session / 'tools-supervisor.json').read_text())
        assert stored == launch['mcp']
        assert 'Cerebro command tool directly' in launch['agent'] and 'Delegate coding and tests' in launch['agent']
    for backend in ('pi', 'codex'):
        before = context_log.read_bytes()
        session_count = len(list((home / 'sessions').iterdir()))
        result = subprocess.run([str(binary), 'acp'], env={**environment, 'CEREBRO_BACKEND': backend},
                                text=True, capture_output=True, timeout=5)
        assert result.returncode != 0 and 'no native ACP endpoint' in result.stderr, result.stderr
        assert context_log.read_bytes() == before
        assert len(list((home / 'sessions').iterdir())) == session_count, 'unsupported ACP created a parent session'

print('all checks passed')
