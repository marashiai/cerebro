"""Bind a completed native Codex root turn to its Cerebro session."""

import datetime
import json
import os
from pathlib import Path
import sys


def bind(directory, event):
    if event.get('type') != 'agent-turn-complete':
        return
    directory = Path(directory)
    metadata = directory / 'metadata.json'
    meta = json.loads(metadata.read_text())
    native_id = event['thread-id']
    if meta.get('foreign_session_id') and meta['foreign_session_id'] != native_id:
        raise ValueError('notification belongs to another native thread')
    meta['foreign_session_id'] = native_id
    meta['last_touched'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    temporary = metadata.with_name(f'metadata.json.tmp.{os.getpid()}')
    temporary.write_text(json.dumps(meta, indent=2) + '\n')
    temporary.replace(metadata)
    with (directory / 'transcript.jsonl').open('a') as transcript:
        for text in event.get('input-messages', []):
            transcript.write(json.dumps({'kind': 'user', 'ts': meta['last_touched'], 'text': text}) + '\n')


if __name__ == '__main__':
    bind(sys.argv[1], json.loads(sys.argv[2]))
