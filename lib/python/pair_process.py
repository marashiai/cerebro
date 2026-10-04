"""Steer native Pi RPC, Claude stream-json or Codex app-server children.

The native stdout stream drives completion. No child-log/session-file polling
or transcript reconciliation is involved; Cerebro owns only the FIFO side
channel, its authorized restart, and the post-turn steering window.

A terminal native turn ends the stage even when tool calls it started never
reported completion: the final reply cannot depend on their results. Those
calls are recorded in an `.unfinished.json` receipt beside the child log.
"""

import base64
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time

from child_store_lib import _atomic_write
from scope_watch import watch_child


watcher = None


def emit(event):
    print(json.dumps(event), flush=True)
    if watcher:
        watcher.event(event)


class Claude:
    def __init__(self, send, resume):
        self.send = send
        self.outstanding = 0
        self.busy = {}

    def start(self, prompt):
        self.steer(prompt)

    def steer(self, text):
        self.send({'type': 'user', 'message': {'role': 'user', 'content': text}})
        self.outstanding += 1

    def event(self, event):
        emit(event)
        kind = event.get('type')
        for block in (event.get('message') or {}).get('content', []) or []:
            if not isinstance(block, dict):
                continue
            if block.get('type') == 'tool_use':
                tool_input = block.get('input') or {}
                self.busy[block['id']] = {'tool': block.get('name'),
                                          'command': tool_input.get('command') or tool_input.get('description')}
            elif block.get('type') == 'tool_result':
                self.busy.pop(block['tool_use_id'], None)
        if kind == 'result':
            self.outstanding -= 1
        return self.ended()

    def ended(self):
        return self.outstanding == 0


class Codex:
    def __init__(self, send, resume, cwd, model, instructions, readonly=False):
        self.send = send
        self.resume = resume
        self.params = {'cwd': cwd, 'approvalPolicy': 'never', 'sandbox': 'danger-full-access',
                       'developerInstructions': instructions}
        if model:
            self.params['model'] = model
        self.next_id = 0
        self.requests = {}
        self.thread = None
        self.pending = []
        self.busy = {}
        self.active_turns = set()
        self.completed_turns = set()

    def request(self, method, params, purpose):
        self.next_id += 1
        self.requests[self.next_id] = purpose
        self.send({'jsonrpc': '2.0', 'id': self.next_id, 'method': method, 'params': params})

    def start(self, prompt):
        self.pending.append(prompt)
        self.request('initialize', {'clientInfo': {'name': 'cerebro', 'version': '2.0.0'}}, 'initialize')

    def next_turn(self):
        text = '\n\n'.join(self.pending)
        self.pending.clear()
        self.request('turn/start', {'threadId': self.thread,
                     'input': [{'type': 'text', 'text': text}],
                     **({'effort': os.environ['CEREBRO_CHILD_EFFORT']} if os.environ.get('CEREBRO_CHILD_EFFORT') else {})}, 'turn')

    def steer(self, text):
        # Native turn/start atomically starts an idle turn or steers the active
        # one, including the boundary where a completion is still in transit.
        self.pending.append(text)
        if self.thread:
            self.next_turn()

    def event(self, event):
        if 'id' in event and 'method' not in event:
            purpose = self.requests.pop(event['id'], None)
            if 'error' in event:
                raise RuntimeError(json.dumps(event['error']))
            result = event.get('result') or {}
            if purpose == 'initialize':
                self.send({'jsonrpc': '2.0', 'method': 'initialized', 'params': {}})
                params = dict(self.params)
                if self.resume:
                    params['threadId'] = self.resume
                    params['excludeTurns'] = True
                self.request('thread/resume' if self.resume else 'thread/start', params, 'thread')
            elif purpose == 'thread':
                self.thread = result['thread']['id']
                emit({'type': 'thread.started', 'thread_id': self.thread})
                self.next_turn()
            elif purpose == 'turn':
                turn_id = result['turn']['id']
                if turn_id not in self.completed_turns:
                    self.active_turns.add(turn_id)
            return self.ended()
        method = event.get('method', '')
        params = event.get('params') or {}
        if 'id' in event:
            # never/danger-full-access should not ask. Reject unexpected host
            # requests rather than treating them as authorization.
            self.send({'jsonrpc': '2.0', 'id': event['id'],
                       'error': {'code': -32601, 'message': 'Unexpected host request'}})
            return self.ended()
        if method == 'turn/started':
            self.active_turns.add(params['turn']['id'])
            emit({'type': 'turn.started'})
        elif method in ('item/started', 'item/completed'):
            item = params['item']
            kind = item['type']
            if kind in ('commandExecution', 'mcpToolCall', 'fileChange'):
                if method == 'item/started':
                    self.busy[item['id']] = {'tool': kind, 'command': item.get('command') or item.get('tool')}
                else:
                    self.busy.pop(item['id'], None)
            normalized = {'id': item['id'], 'type': {
                'agentMessage': 'agent_message', 'commandExecution': 'command_execution',
                'fileChange': 'file_change', 'mcpToolCall': 'mcp_tool_call',
            }.get(kind, kind)}
            normalized.update({k: v for k, v in item.items() if k not in ('id', 'type')})
            emit({'type': 'item.completed' if method == 'item/completed' else 'item.started', 'item': normalized})
        elif method == 'turn/completed':
            turn = params['turn']
            if turn['status'] != 'completed':
                emit({'type': 'turn.failed', 'error': turn.get('error') or {'message': turn['status']}})
                raise RuntimeError('Codex turn ' + turn['status'])
            emit({'type': 'turn.completed'})
            self.completed_turns.add(turn['id'])
            self.active_turns.discard(turn['id'])
        else:
            emit({'type': 'progress', 'event': event})
        return self.ended()

    def ended(self):
        return bool(self.completed_turns) and not self.active_turns and not self.pending and 'turn' not in self.requests.values()


def run():
    global watcher
    backend, cwd, resume, model, fifo, steer_path, child_log, executable = sys.argv[1:9]
    prompt = sys.stdin.read()
    watcher = watch_child(backend, cwd, prompt, fifo, child_log)
    if backend == 'pi':
        from pi_launch import run_argv
        argv = [*run_argv(executable, os.environ['CEREBRO_CHILD_ROLE'], cwd,
                        os.environ['CEREBRO_SESSION_DIR'], resume, model,
                        os.environ['CEREBRO_CHILD_INSTRUCTIONS']), '--mode', 'rpc']
    elif backend == 'claude':
        argv = [executable, *sys.argv[9:]]
    else:
        argv = [executable, 'app-server']
    env = dict(os.environ)
    for key in ('CEREBRO_SESSION_ID', 'CEREBRO_SESSION_DIR', 'CEREBRO_ROLE',
                'CEREBRO_JEV_API_KEY', 'CEREBRO_CFG_JEV_API_KEY', 'CEREBRO_JOB_STATUS', 'CEREBRO_JOB_ID'):
        env.pop(key, None)
    proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=sys.stderr, start_new_session=True, env=env)

    def send(event):
        proc.stdin.write((json.dumps(event) + '\n').encode())
        proc.stdin.flush()

    if backend == 'pi':
        from pair_pi import Pi
        adapter = Pi(send, emit, resume)
    elif backend == 'claude':
        adapter = Claude(send, resume)
    else:
        adapter = Codex(send, resume, cwd, model, os.environ['CEREBRO_CHILD_INSTRUCTIONS'])
    fd = os.open(fifo, os.O_RDWR | os.O_NONBLOCK) if fifo else None
    idle_grace = float(os.environ.get('CEREBRO_PAIR_IDLE', '60'))
    stall = float(os.environ.get('CEREBRO_PAIR_STALL', '180'))
    stall_busy = float(os.environ.get('CEREBRO_PAIR_STALL_BUSY', '450'))
    output_buffer = b''
    fifo_buffer = b''
    receipt = child_log[:-6] if child_log.endswith('.jsonl') else child_log

    def record_unfinished():
        # Native turn completion does not prove these calls finished.
        if adapter.busy:
            tools = [{'id': key, **value} for key, value in adapter.busy.items()]
            _atomic_write(receipt + '.unfinished.json', tools)
            print(f'cerebro pair: native turn ended with {len(tools)} unfinished tool call(s)', file=sys.stderr)

    last_activity = time.monotonic()
    idle_deadline = None
    native_exit = None
    # SIGTERM from a timeout/cancellation must run the process-group cleanup.
    def stop(signum, frame):
        raise SystemExit(128 + signum)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        adapter.start(prompt)
        while True:
            if watcher:
                watcher.check()
            if native_exit is not None and not (watcher and watcher.busy()):
                if not native_exit:
                    record_unfinished()
                return native_exit
            now = time.monotonic()
            deadline = idle_deadline
            if deadline is None and fd is not None:
                limit = stall_busy if adapter.busy else stall
                if limit > 0:
                    deadline = last_activity + limit
            watched = ([proc.stdout.fileno()] if native_exit is None else []) + ([fd] if fd is not None else [])
            if watcher:
                watched.append(watcher.wake_fd)
            timeout = None
            if deadline is not None and not (watcher and watcher.busy() and idle_deadline is not None):
                timeout = max(0, deadline - now)
            ready, _, _ = select.select(watched, [], [], timeout)
            if watcher and watcher.wake_fd in ready:
                os.read(watcher.wake_fd, 65536)
                watcher.check()
            if not ready:
                if idle_deadline is not None:
                    record_unfinished()
                    return 0
                limit = stall_busy if adapter.busy else stall
                print(f'cerebro pair: child stalled -- no native events for {limit:g}s', file=sys.stderr)
                Path(receipt + '.stalled').touch()
                return 5
            if fd in ready:
                fifo_buffer += os.read(fd, 65536)
                while b'\n' in fifo_buffer:
                    line, fifo_buffer = fifo_buffer.split(b'\n', 1)
                    prefix, encoded = line.decode().split(' ', 1)
                    message = base64.b64decode(encoded).decode()
                    if prefix == 'R':
                        Path(receipt + '.restart').write_text(message)
                        return 0
                    if prefix != 'S':
                        raise ValueError('invalid steering prefix')
                    adapter.steer(message)
                    if watcher:
                        watcher.steered(message)
                    with open(steer_path, 'a') as record:
                        record.write('- ' + message.replace('\n', '\n  ') + '\n')
                    idle_deadline = None
                    last_activity = time.monotonic()
            if native_exit is None and proc.stdout.fileno() in ready:
                chunk = os.read(proc.stdout.fileno(), 65536)
                if not chunk:
                    native_exit = proc.wait() or (0 if idle_deadline is not None else 2)
                    if fd is not None:
                        os.close(fd)
                        fd = None
                    if native_exit:
                        return native_exit
                    continue
                output_buffer += chunk
                while b'\n' in output_buffer:
                    line, output_buffer = output_buffer.split(b'\n', 1)
                    if not line.strip():
                        continue
                    event = json.loads(line)
                    last_activity = time.monotonic()
                    if not adapter.event(event):
                        idle_deadline = None
                    elif idle_deadline is None:
                        if watcher:
                            watcher.turn_done()
                        idle_deadline = last_activity + idle_grace
    finally:
        if watcher:
            watcher.close()
        if fd is not None:
            os.close(fd)
        proc.stdin.close()
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()


if __name__ == '__main__':
    try:
        sys.exit(run())
    except Exception as error:
        print('cerebro pair: ' + str(error), file=sys.stderr)
        sys.exit(2)
