"""Classify bounded native activity and wake the parent through its durable job."""

from collections import Counter, deque
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import threading
import time
import uuid

from jev import ENDPOINT, Jev, number, write_trace
from task_packet import task_packet
from wait_detached import completion_socket


ROLES = {'execute'}
HISTORY_LIMIT = 64
HISTORY_CHARS = 24000
ACTIVITY_LIMIT = 2400


def scope_questions(state):
    questions = json.loads((Path(__file__).resolve().parent.parent /
                            'payloads' / 'jev' / 'questions.json').read_text())
    questions['evidence'] = {
        'type': 'choice',
        'instructions': 'Select the stable event ID with the strongest concrete evidence for an actionable concern. '
                        'It may be from `events` or `history`. Select none when no event supports a concern.',
        'criteria': {'none': 'No supplied event supports an actionable concern',
                     **{event['id']: 'The supplied event with ID ' + event['id']
                        for event in state.get('history', []) + state.get('events', [])}}
    }
    return questions


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
    job_id = os.environ.get('CEREBRO_JOB_ID', '')
    if not job_id:
        raise ValueError('--watch requires a durable job ID')
    session = Path(os.environ['CEREBRO_SESSION_DIR'])
    task_spec = Path(os.environ.get('CEREBRO_TASK_FILE', ''))
    if not task_spec.is_file() or not task_spec.read_text(encoding='utf-8').strip():
        raise ValueError('--watch requires task-local requirements')
    return client, confidence, status, job_id, session


class ScopeWatch:
    def __init__(self, backend, cwd, prompt, fifo, child_log):
        self.client, self.confidence, self.status, self.job_id, self.session = settings()
        self.task_spec = Path(os.environ['CEREBRO_TASK_FILE'])
        self.backend, self.cwd, self.prompt, self.fifo = backend, cwd, prompt, fifo
        self.role = os.environ['CEREBRO_CHILD_ROLE']
        self.native_id = ''
        self.log = Path(child_log).with_suffix('.scope.jsonl')
        self.trace = Path(child_log).with_suffix('.jev.jsonl')
        self.started = time.monotonic()
        self.phase_started = self.started
        self.last_phase_elapsed = 0
        self.phase = 1
        self.phase_activity = Counter()
        self.total_activity = Counter()
        self.steering = []
        self.decisions = []
        self.pending = deque()
        self.history = deque()
        self.history_chars = 0
        self.condition = threading.Condition()
        self.active = False
        self.stopped = False
        self.failed = False
        self.failure_publishing = False
        self.flush = False
        self.error = None
        self.publisher = None
        self.sequence = 0
        self.prefix = uuid.uuid4().hex[:12]
        self.notices = deque()
        self.notice_signatures = set()
        self.wake_fd, self.wake_write = os.pipe()
        os.set_blocking(self.wake_write, False)
        self.worker = threading.Thread(target=self.run, daemon=True)
        self.worker.start()

    def _decisions(self):
        path = self.session / 'decisions.jsonl'
        if not path.exists():
            return []
        records = []
        with path.open(encoding='utf-8', errors='replace') as source:
            for line in source:
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if item.get('job_id') == self.job_id and item.get('disposition') in ('continue', 'correct', 'stop'):
                    records.append({'sequence': item.get('sequence'), 'disposition': item['disposition'],
                                    'reason': str(item.get('reason', ''))[:1200], 'ts': item.get('ts')})
                    if len(records) > 64:
                        del records[0]
        return records

    def context(self):
        with self.condition:
            steering = list(self.steering)
            history = list(self.history)
            phase_activity = dict(self.phase_activity)
            total_activity = dict(self.total_activity)
            phase = self.phase
        decisions = self._decisions()
        packet = task_packet(self.task_spec)
        state = {
            'original_user_inputs': packet['original_user_inputs'],
            'supervisor_goal': packet['supervisor_goal'],
            'supervisor_acceptance_criteria': packet['supervisor_acceptance_criteria'],
            'supervisor_task_plan': packet['supervisor_task_plan'],
            'trusted_delegated_task': self.prompt,
            'trusted_supervisor_steering': steering,
            'supervisor_dispositions': decisions,
            'observed_history': history,
            'phase_facts': {'number': phase, 'elapsed_seconds': round(time.monotonic() - self.phase_started),
                            'last_completed_elapsed_seconds': self.last_phase_elapsed,
                            'activity_counts': phase_activity},
            'cumulative_facts': {'elapsed_seconds': round(time.monotonic() - self.started),
                                 'activity_counts': total_activity},
        }
        encoded = json.dumps(state, sort_keys=True, ensure_ascii=False)
        if not state['original_user_inputs'] or len(encoded) > 60000:
            raise ValueError('Jev requires nonempty requirements and at most 60,000 context characters')
        authority = {key: state[key] for key in ('original_user_inputs', 'supervisor_goal',
                                                   'supervisor_acceptance_criteria', 'supervisor_task_plan', 'trusted_delegated_task',
                                                   'trusted_supervisor_steering', 'supervisor_dispositions')}
        return state, hashlib.sha256(json.dumps(authority, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def wake(self):
        try:
            os.write(self.wake_write, b'1')
        except (BlockingIOError, OSError):
            pass

    def busy(self):
        with self.condition:
            return self.failure_publishing or (not self.failed and (self.active or bool(self.pending)))

    def check(self):
        # Jev is advisory. Its failure is surfaced through a durable notice and
        # sidecar while the native child remains in control of its own lifetime.
        return None

    def steered(self, text):
        with self.condition:
            if not self.failed:
                self.steering.append(text[:ACTIVITY_LIMIT])
                self.condition.notify_all()

    def turn_done(self):
        with self.condition:
            now = time.monotonic()
            self.last_phase_elapsed = round(now - self.phase_started)
            self.phase += 1
            self.phase_started = now
            self.phase_activity.clear()
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
            if item.get('type') in ('agent_message', 'agentMessage', 'command_execution', 'file_change', 'mcp_tool_call'):
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
            if self.failed:
                return
            for value in activity:
                if len(self.pending) >= 128:
                    self.dropped_activity = getattr(self, 'dropped_activity', 0) + 1
                    continue
                self.sequence += 1
                raw = json.dumps(value, ensure_ascii=False)
                self.pending.append({'id': self.prefix + '-' + str(self.sequence), 'type': kind,
                                     'activity': raw[:ACTIVITY_LIMIT], 'truncated': len(raw) > ACTIVITY_LIMIT})
                self.phase_activity[kind] += 1
                self.total_activity[kind] += 1
            self.condition.notify_all()

    def _failure_record(self, error):
        record = {'type': 'observer_failure', 'error': str(error), 'job_id': self.job_id,
                  'native_id': self.native_id, 'backend': self.backend, 'role': self.role,
                  'worktree': self.cwd}
        try:
            with self.log.open('a', encoding='utf-8') as log:
                log.write(json.dumps(record, ensure_ascii=False) + '\n')
        except OSError:
            pass
        try:
            write_trace(self.trace, record)
        except OSError:
            pass
        return record

    def _fail_locked(self, error):
        if self.failed or self.stopped:
            return
        self.failed, self.error = True, error
        self.failure_publishing = True
        self.pending.clear()
        self.active = False
        record = self._failure_record(error)
        self.condition.notify_all()
        self.wake()
        threading.Thread(target=self._publish_failure, args=(record,), daemon=True).start()

    def _publish_failure(self, record):
        try:
            self.publish({'observer_failure': record})
        except Exception as error:
            record['notice_error'] = str(error)
            try:
                with self.log.open('a', encoding='utf-8') as log:
                    log.write(json.dumps({'type': 'observer_failure_notice_error', 'error': str(error),
                                          'job_id': self.job_id}) + '\n')
            except OSError:
                pass
        finally:
            with self.condition:
                self.failure_publishing = False
            self.wake()

    def _append_history(self, events):
        with self.condition:
            for event in events:
                while self.history and (len(self.history) >= HISTORY_LIMIT or
                                        self.history_chars + len(event['activity']) > HISTORY_CHARS):
                    self.history_chars -= len(self.history.popleft()['activity'])
                self.history.append(event)
                self.history_chars += len(event['activity'])

    def _notice_signature(self, result, evidence, state):
        authority = {key: state[key] for key in ('original_user_inputs', 'supervisor_goal',
                                                 'supervisor_acceptance_criteria', 'supervisor_task_plan', 'trusted_delegated_task',
                                                 'trusted_supervisor_steering', 'supervisor_dispositions')}
        authority_sha = hashlib.sha256(json.dumps(authority, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        material = '\0'.join((result['attention'], result['reason'], evidence['activity'],
                               authority_sha))
        return hashlib.sha256(material.encode()).hexdigest()

    def publish(self, notice):
        with socket.socket(socket.AF_UNIX) as client:
            with self.condition:
                if self.stopped:
                    return
                self.publisher = client
            try:
                client.connect(completion_socket(self.status))
                client.sendall((json.dumps({'notify': notice}) + '\n').encode())
                with client.makefile('r') as stream:
                    response = json.loads(stream.readline())
                if not isinstance(response.get('acknowledged'), int):
                    raise ValueError('durable monitor did not acknowledge the scope notice')
            finally:
                with self.condition:
                    if self.publisher is client:
                        self.publisher = None

    def run(self):
        try:
            while True:
                with self.condition:
                    self.condition.wait_for(lambda: self.stopped or self.failed or self.pending)
                    if self.stopped or self.failed:
                        return
                    deadline = time.monotonic() + 2
                    while not self.flush and len(self.pending) < 16 and not self.stopped and not self.failed:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            break
                        self.condition.wait(remaining)
                    if self.stopped or self.failed:
                        return
                    events = [self.pending.popleft() for _ in range(min(16, len(self.pending)))]
                    dropped = getattr(self, 'dropped_activity', 0)
                    self.dropped_activity = 0
                    if dropped:
                        self.sequence += 1
                        events.append({'id': self.prefix + '-' + str(self.sequence), 'type': 'coverage_gap',
                                       'activity': f'{dropped} observer activity events were omitted by the bounded queue.',
                                       'truncated': False, 'coverage_gap': True})
                    self.active, self.flush = True, False
                while True:
                    state, fingerprint = self.context()
                    state['events'] = events
                    state['history'] = list(state.pop('observed_history'))
                    # Task and supervisor text are authoritative; observed activity and plans are untrusted.
                    response = self.client.evaluate(state, scope_questions(state), self.trace)
                    answers = response['answers']
                    result = {'attention': answers['attention']['choice'],
                              'confidence': answers['attention']['confidence'],
                              'reason': answers['reason']['choice'], 'evidence_id': answers['evidence']['choice']}
                    if self.context()[1] == fingerprint:
                        break
                    if self.stopped or self.failed:
                        return
                evidence = next((event for event in state['history'] + events
                                 if event['id'] == result['evidence_id']), None)
                if evidence and evidence.get('coverage_gap') and result['attention'] == 'possible_issue':
                    result['attention'] = 'uncertain'
                if evidence is None:
                    result['attention'] = 'quiet'
                elif result['attention'] == 'possible_issue' and result['confidence'] < self.confidence:
                    result['attention'] = 'uncertain'
                record = {'classification': result, 'evidence': evidence, 'request_id': response['request_id'],
                          'context_sha': fingerprint, 'native_id': self.native_id,
                          'backend': self.backend, 'role': self.role, 'worktree': self.cwd,
                          'steering_pipe': self.fifo, 'job_id': self.job_id}
                self._append_history(events)
                with self.log.open('a', encoding='utf-8') as log:
                    log.write(json.dumps(record, ensure_ascii=False) + '\n')
                if result['attention'] != 'quiet' and evidence:
                    signature = self._notice_signature(result, evidence, state)
                    if signature not in self.notice_signatures:
                        self.publish(record)
                        if len(self.notices) == 256:
                            self.notice_signatures.remove(self.notices.popleft())
                        self.notices.append(signature)
                        self.notice_signatures.add(signature)
                with self.condition:
                    self.active = False
                self.wake()
        except Exception as error:
            with self.condition:
                self._fail_locked(error)

    def close(self):
        with self.condition:
            self.stopped = True
            self.pending.clear()
            self.active = False
            if self.publisher:
                try:
                    self.publisher.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            self.condition.notify_all()
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
