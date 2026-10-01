"""Exercise the supervisor/observer/reviewer MCP boundary over real stdio."""

import base64
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parent.parent
executable = str(root / 'bin' / 'cerebro')


class Client:
    def __init__(self, role, home, session):
        environment = {**os.environ, 'CEREBRO_HOME': str(home), 'CEREBRO_SESSION_ID': session,
                       'CEREBRO_SESSION_DIR': str(home / 'sessions' / session), 'CEREBRO_ROLE': role,
                       'CEREBRO_BACKEND': 'opencode',
                       'PATH': str(home / 'guards') + ':' + os.environ['PATH']}
        for backend in ('opencode', 'claude', 'codex'):
            environment['CEREBRO_' + backend.upper() + '_CMD'] = str(home / 'guards' / backend)
        self.proc = subprocess.Popen([executable, 'tools', role], env=environment,
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.counter = 0
        self.request('initialize', {'protocolVersion': '2024-11-05', 'capabilities': {},
                                    'clientInfo': {'name': 'cerebro-tests', 'version': '1'}})
        self.proc.stdin.write(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n')
        self.proc.stdin.flush()

    def request(self, method, params):
        self.counter += 1
        message = {'jsonrpc': '2.0', 'id': self.counter, 'method': method, 'params': params}
        self.proc.stdin.write((json.dumps(message) + '\n').encode())
        self.proc.stdin.flush()
        ready, _, _ = select.select([self.proc.stdout], [], [], 10)
        assert ready, 'MCP response timed out'
        response = json.loads(self.proc.stdout.readline())
        assert response['id'] == self.counter, response
        assert 'error' not in response, response
        return response['result']

    def command(self, argv, stdin=''):
        return self.request('tools/call', {'name': 'command', 'arguments': {'argv': argv, 'stdin': stdin}})

    def close(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=10)
        assert self.proc.returncode == 0, self.proc.stderr.read().decode()
        self.proc.stdout.close()
        self.proc.stderr.close()


def decoded(result):
    assert not result.get('isError'), result
    value = json.loads(result['content'][0]['text'])
    assert value['exit_code'] == 0, value
    return value


with tempfile.TemporaryDirectory(prefix='cerebro-command-tests-') as temporary:
    home = Path(temporary)
    (home / 'guards').mkdir()
    for backend in ('opencode', 'claude', 'codex'):
        guard = home / 'guards' / backend
        guard.write_text('#!/usr/bin/env bash\nprintf "unexpected backend fixture\\n" >&2\nexit 97\n')
        guard.chmod(0o755)
    for session in ('supervisor', 'observer', 'reviewer', 'target'):
        directory = home / 'sessions' / session
        (directory / 'children').mkdir(parents=True)
        (directory / 'plans').mkdir()
        (directory / 'transcript.jsonl').touch()
        (directory / 'spec.md').write_text('approved contract')
        (directory / 'metadata.json').write_text(json.dumps({'role': session, 'observe_target': 'target'}))
    repo = home / 'repo'
    repo.mkdir()
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    (repo / 'requirements.txt').write_text('approved contract')

    supervisor = Client('supervisor', home, 'supervisor')
    tools = supervisor.request('tools/list', {})['tools']
    assert [tool['name'] for tool in tools] == ['command']
    assert tools[0]['inputSchema']['properties']['argv']['items']['type'] == 'string'
    assert decoded(supervisor.command(['read', str(repo), 'requirements.txt']))['text'].strip() == 'approved contract'
    body = 'Literal $(touch ' + str(home / 'shell-expanded') + ') `uname` ; echo not-a-shell'
    decoded(supervisor.command(['plan', '--stdin', '--out', 'literal-plan'], body))
    assert body in (home / 'sessions' / 'supervisor' / 'plans' / 'literal-plan.md').read_text()
    assert not (home / 'shell-expanded').exists(), 'argv/stdin was evaluated by a shell'
    assert supervisor.command(['sh', '-c', 'echo shell'])['isError']
    assert supervisor.command(['read', 1])['isError']
    outside_jobs = home / 'outside-jobs'
    outside_jobs.mkdir()
    sentinel = outside_jobs / 'sentinel'
    sentinel.write_text('unchanged')
    (home / 'sessions' / 'supervisor' / 'detached-jobs').symlink_to(outside_jobs, target_is_directory=True)
    assert supervisor.command(['verify', str(repo), '--prompt', 'verification'])['isError']
    assert list(outside_jobs.iterdir()) == [sentinel] and sentinel.read_text() == 'unchanged'
    supervisor.close()

    observer = Client('observer', home, 'observer')
    assert observer.command(['execute', str(repo), '--prompt', 'unapproved'])['isError']
    assert observer.command(['spec', 'set', 'unapproved replacement'])['isError']
    assert 'approved contract' in decoded(observer.command(['spec', 'show']))['text']
    assert (home / 'sessions' / 'target' / 'spec.md').read_text() == 'approved contract'
    observed_plan = home / 'sessions' / 'target' / 'plans' / 'approved.md'
    observed_plan.write_text('approved plan')
    assert 'approved.md' in decoded(observer.command(['plans']))['text']
    assert observer.command(['plans', 'rm', 'approved'])['isError']
    assert observed_plan.read_text() == 'approved plan'
    for argv in (['worktrees'], ['worktrees', 'list']):
        decoded(observer.command(argv))
    assert observer.command(['worktrees', 'cleanup'])['isError']
    assert '=== OBSERVE session target ===' in decoded(observer.command(['observe']))['text']
    assert observer.command(['observe', 'supervisor'])['isError']
    fifo = home / 'sessions' / 'target' / 'children' / 'execute-live.steer.fifo'
    other_fifo = home / 'sessions' / 'supervisor' / 'children' / 'execute-other.steer.fifo'
    os.mkfifo(fifo)
    os.mkfifo(other_fifo)
    fd = os.open(fifo, os.O_RDWR | os.O_NONBLOCK)
    other_fd = os.open(other_fifo, os.O_RDWR | os.O_NONBLOCK)
    alias = fifo.with_name('outside-alias.steer.fifo')
    alias.symlink_to(other_fifo)
    try:
        decoded(observer.command(['steer', str(fifo), 'return to the approved plan']))
        line = os.read(fd, 65536).decode().strip()
        prefix, encoded = line.split(' ', 1)
        steering = base64.b64decode(encoded).decode()
        assert prefix == 'S' and steering.startswith('[observer]'), steering
        assert 'not a new user requirement' in steering
        decoded(observer.command(['steer', 'implicit correction for the assigned target']))
        prefix, encoded = os.read(fd, 65536).decode().strip().split(' ', 1)
        assert prefix == 'S' and 'implicit correction' in base64.b64decode(encoded).decode()
        for action in ('steer', 'restart'):
            for path in (other_fifo, alias):
                assert observer.command([action, str(path), 'cross-target correction'])['isError']
        alias.unlink()
        os.close(fd)
        fd = None
        for action in ('steer', 'restart'):
            assert observer.command([action, 'assigned target has finished'])['isError']
        metadata = home / 'sessions' / 'observer' / 'metadata.json'
        metadata.write_text(json.dumps({'role': 'observer'}))
        for action in ('steer', 'restart'):
            assert observer.command([action, str(other_fifo), 'observer has no assigned target'])['isError']
        assert not select.select([other_fd], [], [], 0)[0], 'observer wrote to another target FIFO'
    finally:
        if fd is not None:
            os.close(fd)
        os.close(other_fd)
    observer.close()

    reviewer = Client('reviewer', home, 'reviewer')
    decoded(reviewer.command(['git', str(repo), 'status']))
    write_attempt = reviewer.command(['git', str(repo), 'config', 'user.name', 'unauthorized'])
    assert write_attempt['isError'], 'read-only git bridge accepted configuration mutation'
    assert reviewer.command(['doc-write', str(repo)])['isError']
    assert reviewer.command(['spec', 'set', 'unapproved replacement'])['isError']
    reviewer.close()

print('all checks passed')
