"""Transparent child transport recorder; forwards native protocol bytes unchanged.

Bind test/review completion to source hashes so stale evidence cannot pass an eval.
This supplies no model replies, classifications, tool results or corrections.
"""

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import uuid

from runtime import file_hashes


def fixture_tests_passed(item, repo):
    output = item.get('aggregatedOutput') or ''
    identities = ('test_empty', 'test_cells', 'test_quoted_comma')
    return (item.get('exitCode') == 0 and item.get('cwd') is not None
            and Path(item['cwd']).resolve() == repo.resolve()
            and re.search(r'(?m)^Ran 3 tests in ', output) is not None
            and re.search(r'(?m)^OK\s*$', output) is not None
            and all(any(line.startswith(name + ' (test_parser.ParserTests') and line.endswith(' ... ok')
                        for line in output.splitlines()) for name in identities))


def main():
    argv = sys.argv[1:]
    if 'app-server' not in argv:
        os.execv(argv[0], argv)
    directory = Path(os.environ['CEREBRO_EVAL_DIR'])
    repo = Path(os.environ['CEREBRO_EVAL_REPO'])
    role = os.environ.get('CEREBRO_CHILD_ROLE')
    path = directory / ('observations-' + uuid.uuid4().hex + '.jsonl')
    lock = threading.Lock()
    starting = file_hashes(repo)
    tests = {}
    steering = {}
    thread_id = ''

    def record(value):
        with lock, path.open('a') as out:
            out.write(json.dumps({'time': time.time(), 'role': role, **value}) + '\n')

    proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=sys.stderr)

    def forward_input():
        for line in sys.stdin.buffer:
            request = json.loads(line)
            if request.get('method') in ('thread/start', 'thread/resume'):
                params = request.get('params', {})
                record({'type': 'model', 'model': params.get('model'), 'cwd': params.get('cwd')})
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
        is_test = (item.get('type') == 'commandExecution' and
                   re.search(r'\bpython3\s+-m\s+unittest(?:\s|$)', item.get('command', '')))
        if method == 'item/started' and is_test:
            tests[item['id']] = {'source': file_hashes(repo), 'started_at': time.time()}
        elif method == 'item/completed' and is_test:
            after = file_hashes(repo)
            before = tests.pop(item['id'], {})
            record({'type': 'tests', 'thread_id': thread_id, 'passed': fixture_tests_passed(item, repo),
                    'started_at': before.get('started_at'),
                    'unchanged_during_check': before.get('source') == after, 'source': after})
        elif method == 'turn/completed' and role == 'review':
            after = file_hashes(repo)
            record({'type': 'review', 'thread_id': thread_id, 'passed': params['turn']['status'] == 'completed',
                    'unchanged_during_check': starting == after, 'source': after})
        sys.stdout.buffer.write(line)
        sys.stdout.buffer.flush()
    return proc.wait()


if __name__ == '__main__':
    sys.exit(main())
