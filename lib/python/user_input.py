"""Durable, ordered capture of the user's original session inputs."""

import fcntl
import json
from pathlib import Path
import uuid

from child_store_lib import _atomic_write, _now_iso


def _path(session):
    return Path(session) / 'user-inputs.json'


def _load(path):
    if not path.is_file():
        return []
    entries = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
        raise ValueError('captured user inputs must be an ordered JSON array')
    return entries


def record(session: Path, content: list[dict], *, source: str, native_id: str = '', turn_id: str = ''):
    if not isinstance(source, str) or not source:
        raise ValueError('user input source must be nonempty')
    if not isinstance(content, list) or not content or any(not isinstance(block, dict) for block in content):
        raise ValueError('user input content must be a nonempty array of JSON objects')
    if all(block.get('type') == 'text' and not str(block.get('text', '')).strip() for block in content):
        raise ValueError('user input content contains no non-whitespace text or structured blocks')
    encoded = json.dumps(content, ensure_ascii=False)
    content = json.loads(encoded)
    path = _path(session)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix('.json.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        entries = _load(path)
        entry = {'id': uuid.uuid4().hex, 'source': source, 'native_id': native_id,
                 'turn_id': turn_id, 'content': content, 'timestamp': _now_iso()}
        entries.append(entry)
        _atomic_write(str(path), entries)
        return entry


def record_text(session, text, **kwargs):
    if not isinstance(text, str) or not text.strip():
        raise ValueError('user input text must contain non-whitespace content')
    return record(session, [{'type': 'text', 'text': text}], **kwargs)


def snapshot(session) -> list[dict]:
    entries = _load(_path(session))
    if not entries:
        raise ValueError('no original user input has been captured for this session')
    return entries
