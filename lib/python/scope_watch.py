"""Classify fresh native activity and wake the parent through its durable job."""

from collections import deque
import hashlib
import json
import os
from pathlib import Path
import socket
import threading
import time
import uuid

from jev import ENDPOINT, Jev, number
from wait_detached import completion_socket


ROLES = {'execute', 'apply-review', 'doc-write'}


def settings():
    confidence = float(os.environ.get('CEREBRO_JEV_CONFIDENCE', '0.8'))
    if not number(confidence) or not 0 <= confidence <= 1:
        raise ValueError('Jev confidence must be between 0 and 1')
    client = Jev(os.environ.get('CEREBRO_JEV_API_KEY', ''),
                 os.environ.get('CEREBRO_JEV_MODEL', 'jev-latest'),
                 os.environ.get('CEREBRO_JEV_ENDPOINT', ENDPOINT))
    status = os.environ.get('CEREBRO_JOB_STATUS', '')
    if not status or not Path(completion_socket(status)).is_socket():
        raise ValueError('--watch requires a durable command-tool or detach job')
    session = Path(os.environ['CEREBRO_SESSION_DIR'])
    if not (session / 'spec.md').read_text().strip():
        raise ValueError('--watch requires recorded requirements (spec set)')
    return client, confidence, status, session


class ScopeWatch:
    def __init__(self, backend, cwd, prompt, fifo, child_log):
        self.client, self.confidence, self.status, self.session = settings()
        self.backend, self.cwd, self.prompt, self.fifo = backend, cwd, prompt, fifo
        self.role = os.environ['CEREBRO_CHILD_ROLE']
        self.native_id = ''
        self.log = Path(child_log).with_suffix('.scope.jsonl')
        self.steering = []
        self.pending = deque()
        self.condition = threading.Condition()
        self.active = False
        self.stopped = False
        self.flush = False
        self.error = None
        self.publisher = None
        self.sequence = 0
        self.prefix = uuid.uuid4().hex[:12]
        self.wake_fd, self.wake_write = os.pipe()
        os.set_blocking(self.wake_write, False)
        self.context()
        self.worker = threading.Thread(target=self.run, daemon=True)
        self.worker.start()

    def context(self):
        plan = os.environ.get('CEREBRO_WATCH_PLAN') or str(self.session / 'plans' / 'work.md')
        with self.condition:
            steering = list(self.steering)
        state = {'requirements': (self.session / 'spec.md').read_text(),
                 'delegated_task': self.prompt,
                 'current_plan': Path(plan).read_text() if Path(plan).exists() else '',
                 'steering': steering}
        encoded = json.dumps(state, sort_keys=True)
        if not state['requirements'].strip() or len(encoded) > 60000:
            raise ValueError('Jev requires nonempty requirements and at most 60,000 context characters')
        return state, hashlib.sha256(encoded.encode()).hexdigest()

    def wake(self):
        try:
            os.write(self.wake_write, b'1')
        except (BlockingIOError, OSError):
            pass

    def busy(self):
        with self.condition:
            return self.active or bool(self.pending)

    def check(self):
        if self.error:
            raise RuntimeError('Jev watch stopped: ' + str(self.error))

    def steered(self, text):
        with self.condition:
            self.steering.append(text)
            self.condition.notify_all()

    def turn_done(self):
        with self.condition:
            self.flush = True
            self.condition.notify_all()

    def event(self, event):
        kind = event.get('type')
        if kind == 'thread.started':
            self.native_id = event['thread_id']
        elif kind == 'system' and event.get('subtype') == 'init':
            self.native_id = event['session_id']
        elif kind == 'session.started':
            self.native_id = event['session_id']
        activity = []
        if kind in ('item.started', 'item.completed'):
            item = event['item']
            if item.get('type') in ('agent_message', 'command_execution', 'file_change', 'mcp_tool_call'):
                activity.append(item)
        elif kind == 'assistant':
            activity.extend(block for block in (event.get('message') or {}).get('content', [])
                            if block.get('type') in ('text', 'tool_use'))
        elif kind in ('tool_execution_start', 'tool_execution_end'):
            activity.append(event)
        elif kind == 'message_end' and event.get('message', {}).get('role') == 'assistant':
            activity.extend(block for block in event['message'].get('content', [])
                            if block.get('type') in ('text', 'toolCall'))
        elif kind == 'result' and isinstance(event.get('result'), str):
            activity.append({'text': event['result']})
        with self.condition:
            for value in activity:
                if len(self.pending) >= 128:
                    self.error = ValueError('fresh activity exceeded the 128-event buffer')
                    self.wake()
                    break
                self.sequence += 1
                raw = json.dumps(value, ensure_ascii=False)
                self.pending.append({'id': self.prefix + '-' + str(self.sequence),
                                     'type': kind, 'activity': raw[:2400],
                                     'truncated': len(raw) > 2400})
            self.condition.notify_all()

    def publish(self, notice):
        with socket.socket(socket.AF_UNIX) as client:
            with self.condition:
                if self.stopped:
                    return
                self.publisher = client
            client.connect(completion_socket(self.status))
            client.sendall((json.dumps({'notify': notice}) + '\n').encode())
            with client.makefile('r') as stream:
                response = json.loads(stream.readline())
            if not isinstance(response.get('acknowledged'), int):
                raise ValueError('durable monitor did not acknowledge the scope notice')
        with self.condition:
            self.publisher = None

    def run(self):
        try:
            while True:
                with self.condition:
                    self.condition.wait_for(lambda: self.stopped or self.pending)
                    if self.stopped:
                        return
                    deadline = time.monotonic() + 2
                    while not self.flush and len(self.pending) < 16 and not self.stopped:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            break
                        self.condition.wait(remaining)
                    if self.stopped:
                        return
                    events = [self.pending.popleft() for _ in range(min(16, len(self.pending)))]
                    self.active, self.flush = True, False
                while True:
                    state, fingerprint = self.context()
                    result = self.client.classify({**state, 'events': events})
                    if self.context()[1] == fingerprint:
                        break
                    if self.stopped:
                        return
                evidence = next((event for event in events if event['id'] == result['evidence_id']), None)
                if result['scope'] == 'possible_deviation' and (result['confidence'] < self.confidence or evidence is None):
                    result['scope'] = 'uncertain'
                record = {'classification': result, 'evidence': evidence,
                          'context_sha': fingerprint, 'native_id': self.native_id,
                          'backend': self.backend, 'role': self.role, 'worktree': self.cwd,
                          'steering_pipe': self.fifo}
                with self.log.open('a') as log:
                    log.write(json.dumps(record) + '\n')
                if result['scope'] != 'in_scope':
                    self.publish(record)
                with self.condition:
                    self.active = False
                self.wake()
        except Exception as error:
            with self.condition:
                if not self.stopped:
                    self.error = error
            self.wake()

    def close(self):
        with self.condition:
            self.stopped = True
            if self.publisher:
                try:
                    self.publisher.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            self.condition.notify_all()
        # HTTP requests have their own bounded timeout. A cancelled child never
        # waits for the classifier thread or retains its native process group.
        os.close(self.wake_fd)
        os.close(self.wake_write)


def watch_child(backend, cwd, prompt, fifo, child_log):
    if os.environ.get('CEREBRO_JEV_ENABLED', '0') != '1' or os.environ.get('CEREBRO_CHILD_ROLE') not in ROLES:
        return None
    if not fifo:
        raise ValueError('--watch requires a steerable native child')
    return ScopeWatch(backend, cwd, prompt, fifo, child_log)


if __name__ == '__main__':
    try:
        settings()
    except (ValueError, OSError, KeyError) as error:
        raise SystemExit('cerebro: ' + str(error))
