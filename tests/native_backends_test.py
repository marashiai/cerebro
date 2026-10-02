"""CLI-level Claude/Codex session, review, resume and live-steering contracts."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

root = Path(__file__).resolve().parent.parent
cli = str(root / 'bin' / 'cerebro')
fixture = Path(__file__).with_name('native_child_fixture.py')


def records(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def check(result):
    assert result.returncode == 0, result.stderr + '\n' + result.stdout
    return result.stdout


def command(environment, *arguments):
    return subprocess.run([cli, *arguments], env=environment, text=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)


def execute_pair(environment, repo, log, role):
    environment = {**environment, 'NATIVE_FIXTURE_MODE': 'steer'}
    log.write_text('')
    proc = subprocess.Popen([cli, 'execute', str(repo), '--prompt', 'native paired task', '--pair'],
                            env=environment, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        directory = Path(environment['CEREBRO_HOME']) / 'sessions' / environment['CEREBRO_SESSION_ID'] / 'children'
        deadline = time.monotonic() + 10
        fifo = None
        while time.monotonic() < deadline:
            entries = records(log)
            native_started = (any(entry.get('method') == 'turn/start' for entry in entries)
                              if environment['CEREBRO_BACKEND'] == 'codex'
                              else any(entry.get('type') == 'user' for entry in entries))
            live = list(directory.glob('*.steer.fifo'))
            if native_started and live:
                fifo = live[0]
                break
            assert proc.poll() is None, proc.communicate()
            time.sleep(0.02)
        assert fifo, 'paired native child never started'
        steering_environment = {**environment, 'CEREBRO_ROLE': role}
        check(command(steering_environment, 'steer', str(fifo), 'enforce the approved contract'))
        stdout, stderr = proc.communicate(timeout=10)
        assert proc.returncode == 0, stderr
        assert 'PAIR STEERING' in stdout, stdout
        assert '[' + role + ']' in stdout, stdout
        return records(log)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()


with tempfile.TemporaryDirectory(prefix='cerebro-native-backend-tests-') as temporary:
    directory = Path(temporary)
    guards = directory / 'guards'
    guards.mkdir()
    for backend in ('pi', 'codex', 'claude'):
        guard = guards / backend
        guard.write_text('#!/usr/bin/env bash\nprintf "unexpected native backend fixture\\n" >&2\nexit 97\n')
        guard.chmod(0o755)
    for backend in ('codex', 'claude'):
        home = directory / backend
        session = home / 'sessions' / 'native-session'
        (session / 'children').mkdir(parents=True)
        (session / 'plans').mkdir()
        (session / 'transcript.jsonl').touch()
        (session / 'metadata.json').write_text(json.dumps({'backend': backend, 'role': 'supervisor'}))
        repo = home / 'repo'
        repo.mkdir()
        subprocess.run(['git', 'init', '-q', '-b', 'main', str(repo)], check=True)
        subprocess.run(['git', '-C', str(repo), 'config', 'user.name', 'test'], check=True)
        subprocess.run(['git', '-C', str(repo), 'config', 'user.email', 'test@example.com'], check=True)
        (repo / 'requirements.txt').write_text('approved contract')
        subprocess.run(['git', '-C', str(repo), 'add', 'requirements.txt'], check=True)
        subprocess.run(['git', '-C', str(repo), 'commit', '-q', '-m', 'test fixture'], check=True)
        log = home / 'native.jsonl'
        executable = home / backend
        executable.write_text('#!/usr/bin/env bash\nexec python3 "' + str(fixture) + '" ' + backend + ' "$@"\n')
        executable.chmod(0o755)
        environment = {**os.environ, 'CEREBRO_HOME': str(home), 'CEREBRO_SESSION_ID': 'native-session',
                       'CEREBRO_BACKEND': backend, 'CEREBRO_MODEL': '', 'CEREBRO_REVIEW_MODEL': '',
                       'CEREBRO_SUPERVISOR_MODEL': '',
                       'CEREBRO_JEV_ENABLED': '0', 'CEREBRO_JEV_API_KEY': '',
                       'CEREBRO_CODEX_CMD': str(guards / 'codex'), 'CEREBRO_CLAUDE_CMD': str(guards / 'claude'),
                       'CEREBRO_PI_CMD': str(guards / 'pi'), 'NATIVE_FIXTURE_LOG': str(log),
                       'CEREBRO_PAIR_IDLE': '0.2', 'CEREBRO_PAIR_STALL': '5', 'CEREBRO_PAIR_STALL_BUSY': '5',
                       'CEREBRO_PLAYWRIGHT_ISOLATED': '1',
                       'CEREBRO_CHILD_IDLE_TIMEOUT': '0', 'PATH': str(guards) + ':' + os.environ['PATH']}
        environment['CEREBRO_' + backend.upper() + '_CMD'] = str(executable)

        # Native default-model launch captures an ID before completion and
        # records one backend for both development and independent review.
        stdout = check(command(environment, 'execute', str(repo), '--prompt', 'native task', '--branch', 'feat/native'))
        assert 'NATIVE_DONE' in stdout
        store = json.loads((session / 'child-sessions.json').read_text())
        execute = [value for value in store.values() if value['role'] == 'execute'][0]
        assert execute['id'] == 'NATIVE-CHILD-1' and execute['provider'] == backend and execute['status'] == 'done'
        native = records(log)
        assert all(entry.get('isolated') == '1' for entry in native if 'argv' in entry), 'native child browser profiles were not isolated'
        if backend == 'codex':
            start = [entry['params'] for entry in native if entry.get('method') == 'thread/start'][0]
            assert start['sandbox'] == 'danger-full-access' and start['approvalPolicy'] == 'never'
            assert 'developerInstructions' in start
        else:
            argv = next(entry['argv'] for entry in native if 'argv' in entry)
            assert {'TaskOutput', 'TaskStop'} <= set(argv[argv.index('--tools') + 1].split(','))
            assert {'TaskOutput', 'TaskStop'} <= set(argv[argv.index('--allowedTools') + 1].split())

        log.write_text('')
        findings = check(command({**environment, 'NATIVE_FIXTURE_SID': 'NATIVE-REVIEW-1'},
                                 'review', str(repo), '--model', 'review-native')).strip()
        assert Path(findings).read_text().strip() == 'NATIVE_DONE'
        store = json.loads((session / 'child-sessions.json').read_text())
        review = [value for value in store.values() if value['role'] == 'review'][0]
        assert review['provider'] == backend and review['status'] == 'done'
        native = records(log)
        if backend == 'codex':
            start = [entry['params'] for entry in native if entry.get('method') == 'thread/start'][0]
            assert start['sandbox'] == 'read-only' and start['model'] == 'review-native'
        else:
            argv = next(entry['argv'] for entry in native if 'argv' in entry)
            assert argv[argv.index('--tools') + 1] == ''
            assert argv[argv.index('--allowedTools') + 1] == 'mcp__cerebro__command'
            assert '--strict-mcp-config' in argv

        if backend == 'codex':
            quiet = {**environment, 'NATIVE_FIXTURE_DELAY': '0.4', 'NATIVE_FIXTURE_SID': 'NATIVE-QUIET-FIXER',
                     'CEREBRO_PAIR_STALL': '0.1', 'CEREBRO_PAIR_STALL_BUSY': '0.1'}
            stdout = check(command(quiet, 'apply-review', str(repo), '--prompt', 'quiet native task', '--no-watch'))
            assert 'NATIVE_DONE' in stdout
            report = check(command({**quiet, 'NATIVE_FIXTURE_SID': 'NATIVE-QUIET-REVIEW'},
                                   'review', str(repo))).strip()
            assert Path(report).read_text().strip() == 'NATIVE_DONE'
            stalled = command({**quiet, 'CEREBRO_PAIR_STALL_RETRIES': '0'},
                              'apply-review', str(repo), '--prompt', 'quiet paired task', '--pair', '--no-watch')
            assert stalled.returncode != 0 and 'paired child stalled' in stalled.stderr

        log.write_text('')
        check(command(environment, 'answer', 'NATIVE-CHILD-1', 'continue within the contract'))
        native = records(log)
        if backend == 'codex':
            resume = [entry['params'] for entry in native if entry.get('method') == 'thread/resume'][0]
            assert resume['threadId'] == 'NATIVE-CHILD-1'
        else:
            argv = next(entry['argv'] for entry in native if 'argv' in entry)
            assert argv[argv.index('--resume') + 1] == 'NATIVE-CHILD-1'

        native = execute_pair(environment, repo, log, 'supervisor')
        assert all(entry.get('isolated') == '1' for entry in native if 'argv' in entry), 'paired child browser profiles were not isolated'
        if backend == 'codex':
            steering = [entry['params'] for entry in native if entry.get('method') == 'turn/start'][1]
            assert steering['threadId'] == 'NATIVE-CHILD-1'
            assert steering['input'][0]['text'].startswith('[supervisor]')
            assert 'not a new user requirement' in steering['input'][0]['text']
        else:
            steering = [entry for entry in native if entry.get('type') == 'user'][-1]
            assert steering['message']['content'].startswith('[supervisor]')
            assert 'not a new user requirement' in steering['message']['content']

        log.write_text('')
        result = command({**environment, 'NATIVE_FIXTURE_MODE': 'failure'}, 'execute', str(repo), '--prompt', 'failing native task')
        assert result.returncode != 0, result.stdout
        store = json.loads((session / 'child-sessions.json').read_text())
        interrupted = [value for value in store.values() if value['role'] == 'execute' and value['status'] == 'running']
        assert len(interrupted) == 1 and interrupted[0]['id'] == 'NATIVE-CHILD-1', 'failed child lost its resumable native session'
        native = records(log)
        if backend == 'codex':
            assert len([entry for entry in native if entry.get('method') == 'turn/start']) == 1

        # Even a resume rejected before its first native event must retain the
        # original session identity; restarting a worker could duplicate work.
        log.write_text('')
        rejected = command({**environment, 'NATIVE_FIXTURE_MODE': 'resume-reject'},
                           'execute', str(repo), '--prompt', 'failing native task')
        assert rejected.returncode != 0
        store = json.loads((session / 'child-sessions.json').read_text())
        interrupted = [value for value in store.values() if value['role'] == 'execute' and value['status'] == 'running']
        assert len(interrupted) == 1 and interrupted[0]['id'] == 'NATIVE-CHILD-1'
        if backend == 'codex':
            assert len([entry for entry in records(log) if entry.get('method') == 'thread/resume']) == 1
            log.write_text('')
            stdout = check(command({**environment, 'NATIVE_FIXTURE_MODE': 'background'}, 'execute', str(repo), '--prompt', 'joined native task'))
            child_log = Path(stdout.splitlines()[-1])
            assert 'BACKGROUND_JOINED' in child_log.read_text(), 'completion abandoned a native background command'

print('all checks passed')
