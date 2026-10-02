"""Exercise supervisor controls and the reviewer MCP boundary over real stdio."""

import base64
import json
import os
from pathlib import Path
import select
import shlex
import subprocess
import sys
import tempfile

root = Path(__file__).resolve().parent.parent
executable = str(root / 'bin' / 'cerebro')


def hunk_fixture(argv):
    """Record the actual bridge call; model only additive live-session notes."""
    home = Path(os.environ['CEREBRO_HOME'])
    stdin = sys.stdin.read()
    with (home / 'hunk-argv.jsonl').open('a') as log:
        log.write(json.dumps({'argv': argv, 'stdin': stdin, 'role': os.environ['CEREBRO_ROLE']}) + '\n')
    if argv == ['skill', 'path', 'hunk-review']:
        print(home / 'hunk-skill.md')
        return
    notes_path = home / 'hunk-notes.json'
    notes = json.loads(notes_path.read_text())
    if argv[:3] == ['session', 'comment', 'add']:
        note = {'summary': argv[argv.index('--summary') + 1]}
        if '--rationale' in argv:
            note['rationale'] = argv[argv.index('--rationale') + 1]
        if '--reply-to' in argv:
            note['replyTo'] = argv[argv.index('--reply-to') + 1]
        notes.append(note)
        notes_path.write_text(json.dumps(notes))
    elif argv[:3] == ['session', 'comment', 'apply']:
        notes.extend(json.loads(stdin)['comments'])
        notes_path.write_text(json.dumps(notes))
    print(json.dumps({'argv': argv, 'notes': notes, 'sidecar': str(notes_path)}))


if sys.argv[1:2] == ['--fixture-hunk']:
    hunk_fixture(sys.argv[2:])
    raise SystemExit


class Client:
    def __init__(self, role, home, session):
        self.role = role
        environment = {**os.environ, 'CEREBRO_HOME': str(home), 'CEREBRO_SESSION_ID': session,
                       'CEREBRO_SESSION_DIR': str(home / 'sessions' / session), 'CEREBRO_ROLE': role,
                       'CEREBRO_BACKEND': 'pi',
                       'CEREBRO_JEV_ENABLED': '0', 'CEREBRO_JEV_API_KEY': '',
                       'PATH': str(home / 'guards') + ':' + os.environ['PATH']}
        for backend in ('pi', 'claude', 'codex'):
            environment['CEREBRO_' + backend.upper() + '_CMD'] = str(home / 'guards' / backend)
        self.environment = environment
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
        line = self.proc.stdout.readline()
        assert line, 'MCP server closed stdout: ' + self.proc.stderr.read().decode()
        response = json.loads(line)
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


def hunk_contract(client, home, repo):
    log_path = home / 'hunk-argv.jsonl'
    notes_path = home / 'hunk-notes.json'
    skill = decoded(client.command(['hunk', 'skill', 'path', 'hunk-review']))['text'].strip()
    assert Path(skill) == home / 'hunk-skill.md'
    assert 'native Hunk review instructions' in decoded(client.command(['read', skill]))['text']
    literal = 'Review `code` $(touch ' + str(home / 'hunk-shell-expanded') + '); keep "quoted" input\nsecond line'
    batch = [{'filePath': 'requirements.txt', 'newLine': 1, 'summary': literal},
             {'replyTo': 'USER-NOTE', 'summary': 'add evidence to the existing thread'}]
    supported = [
        (['session', 'list'], ''),
        (['session', 'list', '--json'], ''),
        (['session', 'get', 'HUNK-SESSION', '--json'], ''),
        (['session', 'context', '--repo', str(repo), '--json'], ''),
        (['session', 'review', '--repo', str(repo), '--include-patch', '--include-notes', '--json'], ''),
        (['session', 'navigate', 'HUNK-SESSION', '--file', 'requirements.txt', '--new-line', '1', '--json'], ''),
        (['session', 'navigate', 'HUNK-SESSION', '--next-comment'], ''),
        (['session', 'comment', 'list', '--repo', str(repo), '--file', 'requirements.txt', '--type', 'all', '--json'], ''),
        (['session', 'comment', 'add', 'HUNK-SESSION', '--file', 'requirements.txt', '--new-line', '1',
          '--summary', literal, '--rationale', 'literal $(not-a-command) and `markup`', '--markup', '<b>code</b>', '--focus'], ''),
        (['session', 'comment', 'apply', '--repo', str(repo), '--stdin', '--focus', '--json'], json.dumps({'comments': batch})),
    ]
    before_notes = json.loads(notes_path.read_text())
    for argv, stdin in supported:
        result = json.loads(decoded(client.command(['hunk', *argv], stdin))['text'])
        assert result['argv'] == argv
        actual = json.loads(log_path.read_text().splitlines()[-1])
        assert actual == {'argv': argv, 'stdin': stdin, 'role': client.role}, actual
    notes = json.loads(notes_path.read_text())
    assert notes[:len(before_notes)] == before_notes, 'additive Hunk review overwrote user notes'
    assert notes[-2:] == batch and notes[-3]['summary'] == literal
    assert (repo / 'requirements.txt').read_text() == 'approved contract'
    assert not (home / 'hunk-shell-expanded').exists(), 'Hunk input was evaluated by a shell'

    dangerous = [
        ['diff', 'HEAD~1...HEAD'], ['show', 'HEAD'],
        ['skill', 'path', 'engineering'], ['skill', 'path', '../hunk-review'],
        ['session', 'reload', 'HUNK-SESSION'],
        ['session', 'comment', 'delete', 'HUNK-SESSION', '--comment', 'USER-NOTE'],
        ['session', 'comment', 'clear', 'HUNK-SESSION'],
        ['session', 'comment', 'remove', 'HUNK-SESSION'],
        ['session', 'get'], ['session', 'get', '--repo', '.'],
        ['session', 'get', 'HUNK-SESSION', '--repo', str(repo)],
        ['session', 'get', 'HUNK-SESSION', 'SECOND-SESSION'],
        ['session', 'get', '--repo', str(repo), '--repo', str(repo)],
        ['session', 'get', '--repo'], ['session', 'get', 'HUNK-SESSION', '--unknown'],
        ['session', 'list', '--repo', str(repo)],
        ['session', 'navigate', 'HUNK-SESSION', '--file'],
        ['session', 'comment', 'add', 'HUNK-SESSION', '--summary', '--focus'],
        ['session', 'comment', 'add', 'HUNK-SESSION', '--clear'],
        ['session', 'comment', 'apply', 'HUNK-SESSION'],
        ['session', 'comment', 'apply', 'HUNK-SESSION', '--stdin', '--replace'],
    ]
    before_log = log_path.read_text()
    before_sidecar = notes_path.read_text()
    for argv in dangerous:
        assert client.command(['hunk', *argv], json.dumps({'comments': batch}))['isError'], argv
        assert log_path.read_text() == before_log, 'refused Hunk command reached the executable: ' + repr(argv)
    assert notes_path.read_text() == before_sidecar


def steering_contract(client, home):
    fifo = home / 'sessions' / 'supervisor' / 'children' / 'execute-live.steer.fifo'
    other_fifo = home / 'sessions' / 'other' / 'children' / 'execute-other.steer.fifo'
    os.mkfifo(fifo)
    os.mkfifo(other_fifo)
    fd = os.open(fifo, os.O_RDWR | os.O_NONBLOCK)
    other_fd = os.open(other_fifo, os.O_RDWR | os.O_NONBLOCK)

    def packet(descriptor, expected):
        prefix, text = os.read(descriptor, 65536).decode().strip().split(' ', 1)
        assert prefix == expected
        return base64.b64decode(text).decode()

    try:
        correction = 'return to the approved delegated task'
        decoded(client.command(['steer', str(fifo), correction]))
        steering = packet(fd, 'S')
        assert steering.startswith('[supervisor]') and correction in steering
        assert 'not a new user requirement' in steering
        assert not select.select([other_fd], [], [], 0)[0], 'explicit steering reached another child'
        for action in ('steer', 'restart'):
            ambiguous = client.command([action, 'selecting an ambiguous child is unsafe'])
            assert ambiguous['isError'], ambiguous
            choices = json.loads(ambiguous['content'][0]['text'])
            assert choices['exit_code'] == 1 and str(fifo) in choices['text'] and str(other_fifo) in choices['text'], choices
        assert not select.select([fd, other_fd], [], [], 0)[0]

        decoded(client.command(['steer', str(other_fifo), 'explicit selection of the second child']))
        assert 'explicit selection' in packet(other_fd, 'S')
        os.close(other_fd)
        other_fd = None
        other_fifo.unlink()
        decoded(client.command(['steer', 'implicit correction for the only live child']))
        assert 'implicit correction' in packet(fd, 'S')
        decoded(client.command(['restart', str(fifo), 'approved replacement diagnosis']))
        assert packet(fd, 'R') == '[supervisor] approved replacement diagnosis'

        literal = 'literal `input` $(touch ' + str(home / 'steer-shell-expanded') + '); keep the user text'
        user_environment = {**client.environment}
        user_environment.pop('CEREBRO_ROLE', None)
        sent = subprocess.run([executable, 'steer', str(fifo), literal], env=user_environment,
                              text=True, capture_output=True, timeout=5)
        assert sent.returncode == 0, sent.stderr
        assert packet(fd, 'S') == '[user] ' + literal
        assert not (home / 'steer-shell-expanded').exists()
    finally:
        os.close(fd)
        if other_fd is not None:
            os.close(other_fd)
        fifo.unlink()
        if other_fifo.exists():
            other_fifo.unlink()


with tempfile.TemporaryDirectory(prefix='cerebro-command-tests-') as temporary:
    home = Path(temporary)
    (home / 'guards').mkdir()
    for backend in ('pi', 'claude', 'codex'):
        guard = home / 'guards' / backend
        guard.write_text('#!/usr/bin/env bash\nprintf "unexpected backend fixture\\n" >&2\nexit 97\n')
        guard.chmod(0o755)
    hunk = home / 'guards' / 'hunk'
    hunk.write_text('#!/usr/bin/env bash\nexec python3 ' + shlex.quote(str(Path(__file__).resolve()))
                    + ' --fixture-hunk "$@"\n')
    hunk.chmod(0o755)
    (home / 'hunk-argv.jsonl').write_text('')
    (home / 'hunk-skill.md').write_text('native Hunk review instructions')
    (home / 'hunk-notes.json').write_text(json.dumps([{'id': 'USER-NOTE', 'summary': 'keep the user note'}]))
    for session in ('supervisor', 'reviewer', 'other'):
        directory = home / 'sessions' / session
        (directory / 'children').mkdir(parents=True)
        (directory / 'plans').mkdir()
        (directory / 'transcript.jsonl').touch()
        (directory / 'spec.md').write_text('approved contract')
        (directory / 'metadata.json').write_text(json.dumps({'role': 'supervisor', 'backend': 'pi'}))
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
    hunk_contract(supervisor, home, repo)
    outside_jobs = home / 'outside-jobs'
    outside_jobs.mkdir()
    sentinel = outside_jobs / 'sentinel'
    sentinel.write_text('unchanged')
    (home / 'sessions' / 'supervisor' / 'detached-jobs').symlink_to(outside_jobs, target_is_directory=True)
    assert supervisor.command(['verify', str(repo), '--prompt', 'verification'])['isError']
    assert list(outside_jobs.iterdir()) == [sentinel] and sentinel.read_text() == 'unchanged'
    steering_contract(supervisor, home)
    supervisor.close()

    reviewer = Client('reviewer', home, 'reviewer')
    hunk_contract(reviewer, home, repo)
    decoded(reviewer.command(['git', str(repo), 'status']))
    write_attempt = reviewer.command(['git', str(repo), 'config', 'user.name', 'unauthorized'])
    assert write_attempt['isError'], 'read-only git bridge accepted configuration mutation'
    assert reviewer.command(['doc-write', str(repo)])['isError']
    assert reviewer.command(['spec', 'set', 'unapproved replacement'])['isError']
    for argv in (['execute', str(repo), '--prompt', 'unapproved development'],
                 ['verify', str(repo), '--prompt', 'unapproved runtime'],
                 ['steer', '/unselected/child.steer.fifo', 'unapproved correction'],
                 ['restart', '/unselected/child.steer.fifo', 'unapproved replacement'],
                 ['plans', 'rm', 'approved'], ['worktrees', 'cleanup']):
        refused = reviewer.command(argv)
        assert refused['isError'] and 'unavailable to the reviewer' in refused['content'][0]['text'], refused
    reviewer.close()

print('all checks passed')
