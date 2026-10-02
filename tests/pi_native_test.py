"""Pi version, native settled completion and paired worktree contracts."""

import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile

root = Path(__file__).resolve().parent.parent
fixture = Path(__file__).with_name('pi_fixture.py')


with tempfile.TemporaryDirectory(prefix='cerebro-pi-native-tests-') as temporary:
    directory = Path(temporary)
    executable = directory / 'pi'
    executable.write_text('#!/usr/bin/env bash\nexec python3 "' + str(fixture) + '" "' + str(directory) + '" "$@"\n')
    executable.chmod(0o755)
    guards = directory / 'guards'
    guards.mkdir()
    for backend in ('claude', 'codex'):
        guard = guards / backend
        guard.write_text('#!/usr/bin/env bash\nprintf "unexpected backend fixture\\n" >&2\nexit 97\n')
        guard.chmod(0o755)
    configuration = directory / 'fixture.json'
    environment = {**os.environ, 'CEREBRO_HOME': str(directory / 'home'), 'CEREBRO_SESSION_ID': 'native-test',
                   'CEREBRO_BACKEND': 'pi', 'CEREBRO_PI_CMD': str(executable),
                   'CEREBRO_CLAUDE_CMD': str(guards / 'claude'), 'CEREBRO_CODEX_CMD': str(guards / 'codex'),
                   'CEREBRO_MODEL': '', 'CEREBRO_REVIEW_MODEL': '', 'CEREBRO_SUPERVISOR_MODEL': '',
                   'CEREBRO_JEV_ENABLED': '0', 'CEREBRO_JEV_API_KEY': '', 'CEREBRO_PAIR_IDLE': '0',
                   'CEREBRO_PLAYWRIGHT_ISOLATED': '1',
                   'CEREBRO_CHILD_IDLE_TIMEOUT': '0', 'PATH': str(guards) + ':' + os.environ['PATH']}
    environment.pop('CEREBRO_RESUME_BACKEND', None)
    session = Path(environment['CEREBRO_HOME']) / 'sessions' / environment['CEREBRO_SESSION_ID']
    (session / 'children').mkdir(parents=True)
    (session / 'plans').mkdir()
    (session / 'transcript.jsonl').touch()
    (session / 'metadata.json').write_text(json.dumps({'backend': 'pi', 'role': 'supervisor'}))
    for version, accepted in (('0.98.10', False), ('0.99.1', False), ('0.99.2', True), ('0.100.0', True)):
        configuration.write_text(json.dumps({'version': version, 'launch_log': str(directory / 'launch.jsonl')}))
        launch = subprocess.run([str(root / 'bin' / 'cerebro')], env=environment, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        assert (launch.returncode == 0) == accepted, (version, launch.stderr)
        if not accepted:
            assert 'requires version 0.99.2' in launch.stderr
        else:
            arguments = json.loads((directory / 'launch.jsonl').read_text().splitlines()[-1])
            assert arguments[arguments.index('--tools') + 1] == 'mcp__cerebro__command'
            assert arguments[arguments.index('--cerebro-role') + 1] == 'supervisor'
            assert '--no-context-files' in arguments
            assert Path(arguments[arguments.index('-e') + 1]).is_file()

    repo = directory / 'repo'
    repo.mkdir()
    subprocess.run(['git', 'init', '-q', '-b', 'main', str(repo)], check=True)
    subprocess.run(['git', '-C', str(repo), 'config', 'user.name', 'test'], check=True)
    subprocess.run(['git', '-C', str(repo), 'config', 'user.email', 'test@example.com'], check=True)
    subprocess.run(['git', '-C', str(repo), 'commit', '-q', '--allow-empty', '-m', 'test fixture'], check=True)
    request_log = directory / 'requests.jsonl'
    configuration.write_text(json.dumps({'mode': 'background', 'request_log': str(request_log)}))
    result = subprocess.run([str(root / 'bin' / 'cerebro'), 'execute', str(repo), '--prompt', 'join native automatic work'],
                            env=environment, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
    assert result.returncode == 0, result.stderr
    assert 'AUTOMATIC_WORK_JOINED' in result.stdout, 'CLI completed at low-level agent_end before Pi settled'
    child_log = Path(result.stdout.splitlines()[-1])
    events = [json.loads(line) for line in child_log.read_text().splitlines()]
    assert any(event['type'] == 'agent_settled' for event in events)
    assert all(json.loads(line)['isolated'] == '1' for line in request_log.read_text().splitlines())

    configuration.write_text(json.dumps({'delay': 0.4, 'request_log': str(request_log)}))
    quiet = {**environment, 'CEREBRO_PAIR_STALL': '0.1', 'CEREBRO_PAIR_STALL_BUSY': '0.1'}
    result = subprocess.run([str(root / 'bin' / 'cerebro'), 'apply-review', str(repo),
                             '--prompt', 'quiet native task', '--no-watch'],
                            env=quiet, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    events = [json.loads(line) for line in Path(result.stdout.splitlines()[-1]).read_text().splitlines()]
    assert any(event['type'] == 'agent_settled' for event in events)

    original_head = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True)
    local_work = repo / 'user-local.txt'
    local_work.write_text('precious local work')
    cwd_capture = directory / 'paired-cwd.txt'
    hook = directory / 'paired-work.sh'
    hook.write_text('#!/usr/bin/env bash\nset -e\npwd -P >> ' + shlex.quote(str(cwd_capture)) + '\n'
                    "printf 'paired child work\\n' > native-paired.txt\n")
    for scenario in ('fresh', 'stall-resume'):
        cwd_capture.write_text('')
        request_log.write_text('')
        configuration.write_text(json.dumps({'hook': str(hook), 'request_log': str(request_log)}))
        paired_environment = {**environment, 'CEREBRO_PAIR_STALL': '0.5', 'CEREBRO_PAIR_STALL_BUSY': '0.5',
                              'CEREBRO_PAIR_STALL_RETRIES': '1', 'CEREBRO_PAIR_STALL_BACKOFF': '0'}
        if scenario == 'stall-resume':
            paired_environment['FAKE_STALL_STATE'] = str(directory / 'stall-once')
        result = subprocess.run([str(root / 'bin' / 'cerebro'), 'execute', str(repo),
                                 '--prompt', 'paired native worktree ' + scenario, '--pair', '--worktree'],
                                env=paired_environment, text=True, capture_output=True, timeout=20)
        assert result.returncode == 0, result.stderr
        launches = [json.loads(line) for line in request_log.read_text().splitlines() if '"argv"' in line]
        worktree = Path(launches[0]['cwd']).resolve()
        assert worktree.parent == (Path(environment['CEREBRO_HOME']) / 'worktrees').resolve()
        native_cwds = [Path(line).resolve() for line in cwd_capture.read_text().splitlines()]
        assert len(native_cwds) == (2 if scenario == 'stall-resume' else 1)
        assert all(path == worktree for path in native_cwds), 'paired native process escaped its task worktree'
        assert all(Path(launch['cwd']).resolve() == worktree for launch in launches)
        assert (worktree / 'native-paired.txt').read_text() == 'paired child work\n'
        assert not (repo / 'native-paired.txt').exists() and local_work.read_text() == 'precious local work'
        assert subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True) == original_head
        events = [json.loads(line) for line in Path(result.stdout.splitlines()[-1]).read_text().splitlines()]
        native_file = next(event['session_id'] for event in events if event['type'] == 'session.started')
        assert Path(native_file).is_file()
        if scenario == 'stall-resume':
            assert len(launches) == 2
            resumed_file = launches[1]['argv'][launches[1]['argv'].index('--session') + 1]
            assert Path(resumed_file).resolve() == Path(native_file).resolve(), (resumed_file, native_file)
        store = json.loads((session / 'child-sessions.json').read_text())
        child = next(child for child in store.values() if child['id'] == native_file)
        assert child['status'] == 'done' and Path(child['repo']).resolve() == worktree

print('all checks passed')
