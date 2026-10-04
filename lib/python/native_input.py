"""Capture a native supervisor prompt before its first delegated task."""

import fcntl
import json
import os
from pathlib import Path
import sys

from child_store_lib import _atomic_write
from user_input import record


def capture(backend, event, session=None):
    if os.environ.get('CEREBRO_INPUT_OWNER') == 'external':
        return
    native_id = event['session_id']
    if not isinstance(native_id, str) or not native_id:
        raise ValueError('native prompt has no session identity')
    if session is None:
        session = Path(os.environ.get('CEREBRO_HOME', str(Path.home() / '.cerebro'))) / 'sessions' / native_id
        if not session.is_dir():
            return
    session = Path(session)
    with (session / 'native-input.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        metadata = session / 'metadata.json'
        meta = json.loads(metadata.read_text())
        if meta.get('role', 'supervisor') != 'supervisor':
            return
        if meta.get('foreign_session_id') and meta['foreign_session_id'] != native_id:
            raise ValueError('native prompt belongs to another parent session')
        content = event.get('content')
        if content is None:
            text = event['prompt']
            if not isinstance(text, str):
                raise ValueError('native prompt text must be a string')
            content = [{'type': 'text', 'text': text}]
        entry = record(session, content, source=backend, native_id=native_id,
                       turn_id=event.get('turn_id') or '')
        meta.update(foreign_session_id=native_id, last_touched=entry['timestamp'])
        _atomic_write(str(metadata), meta)
        transcript = session / 'transcript.jsonl'
        text = '\n'.join(block['text'] for block in content if block.get('type') == 'text')
        with transcript.open('a') as output:
            output.write(json.dumps({'kind': 'user', 'ts': entry['timestamp'],
                                     'text': text, 'input_id': entry['id']}) + '\n')
    if backend == 'claude':
        current = session.parent.parent / 'current-session'
        current.unlink(missing_ok=True)
        current.symlink_to(session)


if __name__ == '__main__':
    try:
        capture(sys.argv[1], json.load(sys.stdin), sys.argv[2] if len(sys.argv) > 2 else None)
    except (KeyError, ValueError, OSError) as error:
        print('cerebro: native user input capture failed: ' + str(error), file=sys.stderr)
        sys.exit(2)
