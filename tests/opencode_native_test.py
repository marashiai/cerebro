"""Version gate and native V2 background notification contract through the CLI."""

import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile

root = Path(__file__).resolve().parent.parent
fixture = Path(__file__).with_name('opencode_fixture.py')


with tempfile.TemporaryDirectory(prefix='cerebro-opencode-native-tests-') as temporary:
    directory = Path(temporary)
    executable = directory / 'opencode'
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
                   'CEREBRO_BACKEND': 'opencode', 'CEREBRO_OPENCODE_CMD': str(executable),
                   'CEREBRO_CLAUDE_CMD': str(guards / 'claude'), 'CEREBRO_CODEX_CMD': str(guards / 'codex'),
                   'CEREBRO_MODEL': '', 'CEREBRO_REVIEW_MODEL': '', 'CEREBRO_PAIR_IDLE': '0',
                   'CEREBRO_PLAYWRIGHT_ISOLATED': '1',
                   'CEREBRO_CHILD_IDLE_TIMEOUT': '0', 'PATH': str(guards) + ':' + os.environ['PATH']}
    for key in ('OPENCODE_CONFIG_CONTENT', 'CEREBRO_RESUME_BACKEND', 'CEREBRO_OPENCODE_CHECKED'):
        environment.pop(key, None)
    session = Path(environment['CEREBRO_HOME']) / 'sessions' / environment['CEREBRO_SESSION_ID']
    (session / 'children').mkdir(parents=True)
    (session / 'plans').mkdir()
    (session / 'transcript.jsonl').touch()
    for version, accepted in (('1.18.0', False), ('2.0.18', False), ('2.0.19', True), ('2.1.0', True)):
        configuration.write_text(json.dumps({'version': version, 'launch_log': str(directory / 'launch.jsonl')}))
        launch = subprocess.run([str(root / 'bin' / 'cerebro')], env=environment, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        assert (launch.returncode == 0) == accepted, (version, launch.stderr)
        if not accepted:
            assert 'required' in launch.stderr

    configuration.write_text(json.dumps({'version': '2.0.19', 'ready': False,
                                        'launch_log': str(directory / 'launch.jsonl')}))
    launch = subprocess.run([str(root / 'bin' / 'cerebro')], env=environment, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
    assert launch.returncode != 0 and 'role gate' in launch.stderr, 'native role activation did not fail closed'

    repo = directory / 'repo'
    repo.mkdir()
    subprocess.run(['git', 'init', '-q', '-b', 'main', str(repo)], check=True)
    subprocess.run(['git', '-C', str(repo), 'config', 'user.name', 'test'], check=True)
    subprocess.run(['git', '-C', str(repo), 'config', 'user.email', 'test@example.com'], check=True)
    subprocess.run(['git', '-C', str(repo), 'commit', '-q', '--allow-empty', '-m', 'test fixture'], check=True)
    request_log = directory / 'requests.jsonl'
    configuration.write_text(json.dumps({'mode': 'background', 'sid': 'BACKGROUND-SESSION', 'request_log': str(request_log)}))
    result = subprocess.run([str(root / 'bin' / 'cerebro'), 'execute', str(repo), '--prompt', 'join native background work'],
                            env=environment, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
    assert result.returncode == 0, result.stderr
    assert 'BACKGROUND_JOINED' in result.stdout, 'CLI returned before the native completion notification resumed the child'
    child_log = Path(result.stdout.splitlines()[-1])
    assert 'BACKGROUND_JOINED' in child_log.read_text()
    assert all(json.loads(line)['isolated'] == '1' for line in request_log.read_text().splitlines())

    original_head = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True)
    local_work = repo / 'user-local.txt'
    local_work.write_text('precious local work')
    cwd_capture = directory / 'paired-cwd.txt'
    hook = directory / 'paired-work.sh'
    hook.write_text('#!/usr/bin/env bash\nset -e\npwd -P >> ' + shlex.quote(str(cwd_capture)) + '\n'
                    "printf 'paired child work\\n' > native-paired.txt\n")
    for scenario in ('fresh', 'stall-resume'):
        sid = 'PAIRED-WORKTREE-' + scenario
        cwd_capture.write_text('')
        request_log.write_text('')
        configuration.write_text(json.dumps({'sid': sid, 'hook': str(hook), 'request_log': str(request_log)}))
        paired_environment = {**environment, 'CEREBRO_PAIR_STALL': '0.5', 'CEREBRO_PAIR_STALL_BUSY': '0.5',
                              'CEREBRO_PAIR_STALL_RETRIES': '1', 'CEREBRO_PAIR_STALL_BACKOFF': '0'}
        if scenario == 'stall-resume':
            paired_environment['FAKE_STALL_STATE'] = str(directory / 'stall-once')
        result = subprocess.run([str(root / 'bin' / 'cerebro'), 'execute', str(repo),
                                 '--prompt', 'paired native worktree ' + scenario, '--pair'],
                                env=paired_environment, text=True, capture_output=True, timeout=20)
        assert result.returncode == 0, result.stderr
        requests = [json.loads(line) for line in request_log.read_text().splitlines()]
        location = next(request['payload']['location']['directory'] for request in requests
                        if request['path'] == '/api/session')
        worktree = Path(location).resolve()
        assert worktree.parent == (Path(environment['CEREBRO_HOME']) / 'worktrees').resolve()
        native_cwds = [Path(line).resolve() for line in cwd_capture.read_text().splitlines()]
        assert len(native_cwds) == (2 if scenario == 'stall-resume' else 1)
        assert all(path == worktree for path in native_cwds), 'paired native server escaped its task worktree'
        assert (worktree / 'native-paired.txt').read_text() == 'paired child work\n'
        assert not (repo / 'native-paired.txt').exists() and local_work.read_text() == 'precious local work'
        assert subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True) == original_head
        store = json.loads((session / 'child-sessions.json').read_text())
        child = next(child for child in store.values() if child['id'] == sid)
        assert child['status'] == 'done' and Path(child['repo']).resolve() == repo.resolve()

print('all checks passed')
