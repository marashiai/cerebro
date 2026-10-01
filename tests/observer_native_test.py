"""Observe native Codex child items and bind a target before observer launch."""

import json
import os
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parent.parent
cli = str(root / 'bin' / 'cerebro')


with tempfile.TemporaryDirectory(prefix='cerebro-native-observer-tests-') as temporary:
    home = Path(temporary)
    guards = home / 'guards'
    guards.mkdir()
    for backend in ('opencode', 'claude', 'codex'):
        guard = guards / backend
        guard.write_text('#!/usr/bin/env bash\nprintf "unexpected backend fixture\\n" >&2\nexit 97\n')
        guard.chmod(0o755)
    environment = {'HOME': os.environ['HOME'], 'PATH': str(guards) + ':' + os.environ['PATH'],
                   'CEREBRO_HOME': str(home), 'CEREBRO_SESSION_ID': 'observer-bootstrap',
                   'CEREBRO_BACKEND': 'codex', 'CEREBRO_OBSERVE_WINDOW': '0.1', 'CEREBRO_OBSERVE_QUIET': '0',
                   'CEREBRO_OPENCODE_CMD': str(guards / 'opencode'), 'CEREBRO_CLAUDE_CMD': str(guards / 'claude'),
                   'CEREBRO_CODEX_CMD': str(guards / 'codex')}
    sessions = home / 'sessions'
    for name, touched in (('observer-bootstrap', '2026-10-01T00:00:00Z'),
                          ('older-target', '2026-10-01T00:00:00Z'),
                          ('native-target', '2026-10-01T01:00:00Z')):
        directory = sessions / name
        (directory / 'children').mkdir(parents=True)
        (directory / 'transcript.jsonl').touch()
        (directory / 'metadata.json').write_text(json.dumps({'backend': 'codex', 'role': 'supervisor',
                                                           'last_touched': touched, 'id': name}))
    children = sessions / 'native-target' / 'children'
    fifo = children / 'execute-native.steer.fifo'
    older_fifo = sessions / 'older-target' / 'children' / 'execute-older.steer.fifo'
    for path in (fifo, older_fifo):
        os.mkfifo(path)
    descriptors = [os.open(path, os.O_RDWR | os.O_NONBLOCK) for path in (fifo, older_fifo)]
    log = children / 'execute-native.jsonl'
    events = [
        {'type': 'thread.started', 'thread_id': 'native-thread'},
        {'type': 'item.completed', 'item': {'id': 'message', 'type': 'agent_message',
         'text': 'Implementing the approved contract'}},
        {'type': 'item.completed', 'item': {'id': 'command', 'type': 'command_execution',
         'command': 'git status --short', 'status': 'completed', 'exitCode': 0,
         'aggregatedOutput': 'working tree clean'}},
        {'type': 'item.completed', 'item': {'id': 'change', 'type': 'file_change', 'status': 'completed',
         'changes': [{'path': 'src/cache.py', 'kind': 'update', 'diff': '+ return approved_contract'}]}},
        {'type': 'item.completed', 'item': {'id': 'browser', 'type': 'mcp_tool_call',
         'server': 'browser', 'tool': 'navigate', 'arguments': {'url': 'https://fixture.invalid/approved'}}},
        {'type': 'turn.failed', 'error': {'message': 'native verification failed'}},
        {'type': 'turn.completed'},
    ]
    log.write_text(''.join(json.dumps(event) + '\n' for event in events))

    def command(*arguments):
        return subprocess.run([cli, *arguments], env=environment, text=True,
                              capture_output=True, timeout=10)

    try:
        observed = command('observe', 'native-target')
        assert observed.returncode == 0, observed.stderr
        for text in ('Implementing the approved contract', 'git status --short', 'working tree clean',
                     'src/cache.py', '+ return approved_contract', 'browser', 'navigate',
                     'native verification failed', '(turn complete)', 'OBSERVE STATUS: active'):
            assert text in observed.stdout, 'native observer omitted ' + text + ': ' + observed.stdout
        with log.open('a') as output:
            output.write(json.dumps({'type': 'item.completed', 'item': {'id': 'followup',
                         'type': 'agent_message', 'text': 'Scope correction accepted'}}) + '\n')
        observed = command('observe', 'native-target')
        assert 'Scope correction accepted' in observed.stdout
        assert 'Implementing the approved contract' not in observed.stdout, 'observer replayed old child activity'

        parent_log = home / 'parent.jsonl'
        wrapper = home / 'observer-codex'
        wrapper.write_text('#!/usr/bin/env bash\nexport NATIVE_FIXTURE_LOG="' + str(parent_log) + '"\n'
                           'export NATIVE_FIXTURE_MODE=observer-parent\n'
                           'exec python3 "' + str(Path(__file__).with_name('native_child_fixture.py')) + '" codex "$@"\n')
        wrapper.chmod(0o755)
        environment['CEREBRO_CODEX_CMD'] = str(wrapper)
        launched = command('--observe')
        assert launched.returncode == 0, launched.stderr
        native = [json.loads(line) for line in parent_log.read_text().splitlines()]
        metadata = next(event['parent_metadata'] for event in native if 'parent_metadata' in event)
        assert metadata['role'] == 'observer' and metadata['observe_target'] == 'native-target', metadata
        argv = next(event['argv'] for event in native if 'argv' in event and 'mcp' not in event['argv'])
        assert any('Observe session native-target.' in argument for argument in argv), argv
    finally:
        for descriptor in descriptors:
            os.close(descriptor)
    with log.open('a') as output:
        output.write('{"type":"turn.completed"}\n')
    observed = command('observe', 'native-target')
    assert observed.returncode == 0 and 'OBSERVE STATUS: done' in observed.stdout
    assert '(turn complete)' in observed.stdout, 'final native child activity was abandoned after its FIFO closed'
    for target in ('../native-target', str(home)):
        rejected = command('observe', target)
        assert rejected.returncode != 0, 'observer accepted a path as a session ID'

print('all checks passed')
