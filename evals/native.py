"""Transparent child transport recorder; forwards native protocol bytes unchanged.

Bind test/review completion to source hashes so stale evidence cannot pass an eval.
This supplies no model replies, classifications, tool results or corrections.
"""

import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import threading
import time
import uuid

from runtime import file_hashes
from fixtures import PARSER_TESTS, TEST_IDENTITIES


def shell_commands(command):
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=';&|')
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return
    segments = [[]]
    for token in tokens:
        if token in ('&&', ';', '||', '|'):
            segments.append([])
        else:
            segments[-1].append(token)
    for segment in segments:
        while segment and re.match(r'^[A-Za-z_][A-Za-z_0-9]*=', segment[0]):
            segment.pop(0)
        if segment and Path(segment[0]).name in ('sh', 'bash', 'zsh'):
            for index, token in enumerate(segment[1:], 1):
                if token in ('-c', '-lc') and index + 1 < len(segment):
                    yield from shell_commands(segment[index + 1])
                    break
        else:
            yield segment


class TestJournal:
    def __init__(self, directory, worker_id, role):
        self.worker_id, self.role = worker_id, role
        self.path = directory / ('test-receipts-' + worker_id + '.jsonl')
        descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(descriptor)
        self.started_at = time.time()
        self.offset, self.buffer = 0, b''
        self.pending, self.consumed = {}, set()
        self.thread_id = None

    def environment(self):
        return {'CEREBRO_EVAL_TEST_JOURNAL': str(self.path), 'CEREBRO_EVAL_WORKER_ID': self.worker_id}

    def validate(self, before, after, repo):
        records = (before, after)
        source = before.get('source')
        if (not isinstance(source, dict) or source != after.get('source') or source != file_hashes(repo)
                or source.get('test_parser.py') != hashlib.sha256(PARSER_TESTS.encode()).hexdigest()
                or before.get('tests') != []
                or any(record.get('worker_id') != self.worker_id or record.get('role') != self.role
                       or record.get('cwd') != str(repo.resolve())
                       or record.get('source_root') != str(repo.resolve()) for record in records)
                or any(type(record.get('time')) not in (int, float) or not math.isfinite(record['time'])
                       for record in records)
                or not self.started_at <= before['time'] <= after['time'] <= time.time()):
            return None
        results = after.get('tests')
        passed = (isinstance(results, list) and len(results) == len(TEST_IDENTITIES)
                  and all(isinstance(result, dict) and isinstance(result.get('name'), str)
                          and result.get('passed') is True for result in results)
                  and sorted(result.get('name', '') for result in results) == sorted(TEST_IDENTITIES))
        return {'run_id': after['run_id'], 'worker_id': self.worker_id, 'journal': str(self.path),
                'thread_id': self.thread_id, 'passed': passed, 'source': source,
                'started_at': before['time'], 'finished_at': after['time'], 'unchanged_during_check': True}

    def take(self, repo, thread_id):
        if not thread_id:
            return []
        if self.thread_id is None:
            self.thread_id = thread_id
        if self.thread_id != thread_id:
            raise RuntimeError('test receipt journal cannot belong to multiple native threads')
        with self.path.open('rb') as source:
            source.seek(self.offset)
            self.buffer += source.read()
            self.offset = source.tell()
        lines = self.buffer.split(b'\n')
        self.buffer = lines.pop()
        receipts = []
        for line in lines:
            try:
                record = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            if not isinstance(record, dict):
                continue
            run_id = record.get('run_id')
            if not isinstance(run_id, str) or not re.fullmatch('[0-9a-f]{32}', run_id) or run_id in self.consumed:
                continue
            if record.get('phase') == 'before' and run_id not in self.pending:
                self.pending[run_id] = record
                continue
            before = self.pending.pop(run_id, None)
            self.consumed.add(run_id)
            if before is not None and record.get('phase') == 'after':
                receipt = self.validate(before, record, repo)
                if receipt is not None:
                    receipts.append(receipt)
        return receipts


def main():
    argv = sys.argv[1:]
    if 'app-server' not in argv:
        os.execv(argv[0], argv)
    directory = Path(os.environ['CEREBRO_EVAL_DIR'])
    repo = Path(os.environ['CEREBRO_EVAL_REPO'])
    role = os.environ.get('CEREBRO_CHILD_ROLE')
    worker_id = uuid.uuid4().hex
    path = directory / ('observations-' + worker_id + '.jsonl')
    journal = TestJournal(directory, worker_id, role)
    lock = threading.Lock()
    starting = file_hashes(repo)
    previous = starting
    actions = {}
    steering = {}
    thread_id = ''

    def record(value):
        with lock, path.open('a') as out:
            out.write(json.dumps({'time': time.time(), 'role': role, 'worker_id': worker_id,
                                  'source_root': str(repo.resolve()), **value}) + '\n')

    proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=sys.stderr,
                            env={**os.environ, **journal.environment()})

    def forward_input():
        nonlocal repo, starting, previous
        for line in sys.stdin.buffer:
            request = json.loads(line)
            if request.get('method') in ('thread/start', 'thread/resume'):
                params = request.get('params', {})
                repo = Path(params['cwd'])
                starting = file_hashes(repo)
                previous = starting
                record({'type': 'model', 'method': request['method'],
                        'model': params.get('model'), 'cwd': str(repo)})
            if request.get('method') == 'turn/start':
                params = request.get('params', {})
                if any(item.get('text', '').startswith('[supervisor]') for item in params.get('input', [])):
                    with lock:
                        steering[request['id']] = params['threadId']
            proc.stdin.write(line)
            proc.stdin.flush()
        proc.stdin.close()

    threading.Thread(target=forward_input, daemon=True).start()
    for line in proc.stdout:
        event = json.loads(line)
        params = event.get('params', {})
        if isinstance(event.get('result'), dict) and 'thread' in event['result']:
            result = event['result']
            thread_id = result['thread']['id']
            record({'type': 'model_resolved', 'thread_id': thread_id,
                    'model': result['model'], 'effort': result['reasoningEffort']})
        with lock:
            steered_thread = steering.pop(event.get('id'), None)
        if steered_thread:
            record({'type': 'steer', 'thread_id': steered_thread, 'passed': 'result' in event and 'error' not in event})
        method = event.get('method')
        item = params.get('item', {})
        is_action = item.get('type') in ('commandExecution', 'fileChange', 'mcpToolCall')
        if method == 'item/started' and is_action:
            actions[item['id']] = {'source': file_hashes(repo), 'started_at': time.time()}
        elif method == 'item/completed' and is_action:
            after = file_hashes(repo)
            before = actions.pop(item['id'], {})
            changed = sorted(name for name in set(previous) | set(after) if previous.get(name) != after.get(name))
            record({'type': 'activity', 'thread_id': thread_id, 'cwd': item.get('cwd', str(repo)),
                    'item_type': item['type'], 'command': item.get('command'),
                    'exit_code': item.get('exitCode'), 'changed_files': changed,
                    'output': (item.get('aggregatedOutput') or json.dumps(item.get('result') or ''))[-4000:],
                    'started_at': before.get('started_at'),
                    'unchanged_during_check': before.get('source') == after, 'source': after})
            previous = after
        elif method == 'turn/completed' and role == 'review':
            after = file_hashes(repo)
            record({'type': 'review', 'thread_id': thread_id, 'passed': params['turn']['status'] == 'completed',
                    'unchanged_during_check': starting == after, 'source': after})
        if method in ('item/completed', 'turn/completed'):
            for receipt in journal.take(repo, thread_id):
                record({'type': 'tests', **receipt})
        sys.stdout.buffer.write(line)
        sys.stdout.buffer.flush()
    for receipt in journal.take(repo, thread_id):
        record({'type': 'tests', **receipt})
    return proc.wait()


if __name__ == '__main__':
    sys.exit(main())
