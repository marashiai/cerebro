"""Disconnect and cancellation ownership through the actual durable MCP monitor."""
import json
import os
from pathlib import Path
import subprocess
import time

from task_lifecycle_test import LifecycleTests, CLI

fixture = LifecycleTests()
fixture.setUp()


def until(predicate):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.02)
    raise AssertionError('durable fixture did not reach its native boundary')


def start(packet, delay='0.4'):
    before = {path for path in (fixture.session / 'detached-jobs').glob('*.json') if len(path.stem) == 36}
    proc = subprocess.Popen([CLI, 'tools', 'supervisor'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True,
                            env={**fixture.env, 'NATIVE_FIXTURE_DELAY': delay})
    proc.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                'params': {'name': 'command', 'arguments': {'argv': ['execute'], 'stdin': json.dumps(packet)}}}) + '\n')
    proc.stdin.flush()
    path = until(lambda: next(iter({path for path in (fixture.session / 'detached-jobs').glob('*.json') if len(path.stem) == 36} - before), None))
    return proc, json.loads(path.read_text())


def cli(*argv, ok=True):
    result = subprocess.run([CLI, *argv], env=fixture.env, text=True, capture_output=True, timeout=20)
    assert (result.returncode == 0) == ok, result.stdout + result.stderr
    return json.loads(result.stdout) if result.stdout.startswith('{') else result.stdout


servers = []
try:
    server, job = start(fixture.packet)
    servers.append(server)
    until(lambda: fixture.log.exists() and fixture.stages() == ['execute'])
    server.kill()
    server.communicate(timeout=5)
    result = cli('wait', job['id'])
    task = json.loads(result['text'])
    assert task['stage'] == 'done' and fixture.stages() == ['execute', 'review']
    cli('execute', '--resume', task['task_id'])
    assert fixture.stages() == ['execute', 'review'], 'reconnect duplicated a completed child'

    fixture.log.write_text('')
    first, cancelled = start({**fixture.packet, 'task': 'Cancel this one'}, '2')
    second, retained = start({**fixture.packet, 'task': 'Retain this other task'}, '2')
    servers += [first, second]
    until(lambda: fixture.stages().count('execute') == 2)
    cli('cancel', cancelled['id'])
    stopped = cli('wait', cancelled['id'], ok=False)
    assert stopped['exit_code'] == 130
    completed = cli('wait', retained['id'])
    assert json.loads(completed['text'])['stage'] == 'done'
    assert fixture.stages().count('review') == 1, 'cancel affected another task or reviewed cancelled work'
    for server in (first, second):
        server.stdin.close()
        server.stdin = None
        server.communicate(timeout=5)
finally:
    for server in servers:
        if server.poll() is None:
            server.kill()
            server.communicate()
    fixture.doCleanups()
print('all checks passed')
