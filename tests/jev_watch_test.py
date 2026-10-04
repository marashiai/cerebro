"""Exercise Jev's typed client, advisory watcher and per-finding review evidence."""

from collections import Counter, deque
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'lib' / 'python'))
from jev import Jev
from review_check import assess, context
from scope_watch import HISTORY_CHARS, HISTORY_LIMIT, ScopeWatch, scope_questions
from wait_detached import completion_socket
from user_input import record_text, snapshot


class Classifier(BaseHTTPRequestHandler):
    requests = []
    mode = 'normal'
    attention = None
    started = threading.Event()
    release = threading.Event()

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.requests.append(body)
        if self.mode == 'blocked' and len(self.requests) == 1:
            self.started.set()
            self.release.wait(10)
        if self.mode == 'http-error':
            self.send_response(429)
            self.end_headers()
            self.wfile.write(b'{"error":"fixture quota exceeded"}')
            return
        state, questions = body['state'], body['questions']
        choices = {}
        confidence = {}
        if 'attention' in questions:
            activity = state.get('events', []) + state.get('history', [])
            concern = next((event for event in activity if 'APPARENT_MISTAKE' in event['activity'] or
                            'BILLING_DRIFT' in event['activity']), None)
            choices = {'attention': 'possible_issue' if concern else 'quiet',
                       'reason': ('apparent_mistake' if concern and 'APPARENT_MISTAKE' in concern['activity']
                                  else 'scope_drift' if concern else 'none'),
                       'evidence': concern['id'] if concern else 'none'}
            if self.attention:
                attention, reason, confidence['attention'] = self.attention
                oldest = (state.get('history', []) + state.get('events', []))[0]
                choices = {'attention': attention, 'reason': reason, 'evidence': oldest['id']}
        else:
            for name, question in questions.items():
                if name == 'validity':
                    choices[name] = ('unsupported' if self.mode == 'mixed' and
                                     state['finding']['id'] == 'supported' else 'supported')
                elif name == 'usefulness':
                    choices[name] = 'useful'
                elif name == 'proportionality':
                    choices[name] = 'proportionate'
                elif name == 'evidence':
                    choices[name] = next((key for key in question['criteria'] if key != 'none'), 'none')
                elif name == 'clean_validity':
                    choices[name] = 'bounded'
                elif name == 'clean_usefulness':
                    choices[name] = 'useful'
                elif name == 'clean_evidence':
                    choices[name] = 'clean-diff'
        answers = {name: {'type': 'choice', 'choice': choices[name],
                          'confidence': confidence.get(name, 0.99),
                          'probabilities': {key: float(key == choices[name]) for key in question['criteria']}}
                   for name, question in questions.items()}
        if self.mode == 'invalid':
            name = next(iter(questions))
            answers[name]['probabilities'][next(iter(questions[name]['criteria']))] = 9
        raw = json.dumps({'model': 'jev-test', 'answers': answers}).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class NoticeSocket:
    def __init__(self, status, delayed=False):
        self.path = completion_socket(status)
        self.path_obj = Path(self.path)
        self.path_obj.unlink(missing_ok=True)
        self.server = socket.socket(socket.AF_UNIX)
        self.server.bind(self.path)
        self.server.listen()
        self.notices = []
        self.delayed = delayed
        self.entered = threading.Event()
        self.release = threading.Event()
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def run(self):
        sequence = 0
        while not self.stopped.is_set():
            try:
                client, _ = self.server.accept()
            except OSError:
                return
            with client:
                raw = b''
                while b'\n' not in raw:
                    part = client.recv(65536)
                    if not part:
                        break
                    raw += part
                request = json.loads(raw.split(b'\n', 1)[0])
                if 'notify' in request:
                    sequence += 1
                    self.notices.append(request['notify'])
                    if self.delayed:
                        self.entered.set()
                        self.release.wait(10)
                    client.sendall((json.dumps({'acknowledged': sequence}) + '\n').encode())

    def close(self):
        self.stopped.set()
        self.server.close()
        self.path_obj.unlink(missing_ok=True)
        self.release.set()
        self.thread.join(timeout=1)


def finding(identifier, file='parser.py', line=1, problem='Empty input is mishandled.'):
    return {'id': identifier, 'severity': 'medium', 'file': file, 'line': line,
            'problem': problem, 'evidence': 'The empty-input branch returns the wrong value.',
            'requested_change': 'Handle empty input explicitly.'}


class JevTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http = ThreadingHTTPServer(('127.0.0.1', 0), Classifier)
        cls.http_thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.http_thread.start()
        cls.endpoint = 'http://127.0.0.1:' + str(cls.http.server_port) + '/v1/systemone'

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()

    def setUp(self):
        Classifier.requests = []
        Classifier.mode = 'normal'
        Classifier.attention = None
        Classifier.started.clear()
        Classifier.release.clear()
        self.temp = tempfile.TemporaryDirectory(prefix='cerebro-jev-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.session = self.root / 'session'
        self.task = self.session / 'tasks' / 'task-a'
        self.task.mkdir(parents=True)
        record_text(self.session, 'Fix parser carefully. Avoid billing changes. Preserve None and False as different values.',
                    source='test-native', native_id='session-a', turn_id='turn-original')
        self.user_inputs = snapshot(self.session)
        self.write_task({'goal': 'Summarize parser work.', 'task': 'Fix and verify parser.',
                         'acceptance': ['parser works']})
        self.status = str(self.root / 'job.status')
        self.socket = NoticeSocket(self.status)
        self.addCleanup(self.socket.close)
        self.child_log = self.session / 'children' / 'execute-test.jsonl'
        self.child_log.parent.mkdir(parents=True)
        self.env = {key: value for key, value in os.environ.items() if not key.startswith('CEREBRO_')}
        self.env.update(CEREBRO_JEV_API_KEY='private-fixture-key', CEREBRO_JEV_ENDPOINT=self.endpoint,
                        CEREBRO_JOB_STATUS=self.status, CEREBRO_JOB_ID='job-a',
                        CEREBRO_SESSION_DIR=str(self.session), CEREBRO_TASK_FILE=str(self.task / 'task.json'),
                        CEREBRO_CHILD_ROLE='execute')

    def start_watch(self):
        with patch.dict(os.environ, self.env):
            return ScopeWatch('codex', str(self.root), 'Fix and verify parser.',
                              str(self.root / 'steer.fifo'), str(self.child_log))

    def write_task(self, packet):
        (self.task / 'task.json').write_text(json.dumps({'packet': packet, 'user_inputs': self.user_inputs}))

    def wait_for(self, predicate, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        self.fail('timed out waiting for Jev event')

    def test_client_validates_typed_choices_and_never_logs_credentials(self):
        state = {'events': [{'id': 'evt-1', 'activity': 'Read parser'}], 'history': []}
        trace = self.root / 'jev.jsonl'
        client = Jev('private-fixture-key', endpoint=self.endpoint)
        response = client.evaluate(state, {'attention': {'type': 'choice', 'criteria': {'quiet': 'quiet'}}}, trace)
        self.assertEqual(response['answers']['attention']['choice'], 'quiet')
        Classifier.mode = 'invalid'
        with self.assertRaisesRegex(ValueError, 'invalid typed'):
            client.evaluate(state, {'attention': {'type': 'choice', 'criteria': {'quiet': 'quiet'}}}, trace)
        self.assertNotIn('private-fixture-key', trace.read_text())
        with self.assertRaisesRegex(ValueError, 'HTTPS'):
            Jev('key', endpoint='http://example.com/v1/systemone')

    def test_productive_long_investigation_stays_quiet(self):
        watch = self.start_watch()
        self.addCleanup(watch.close)
        watch.event({'type': 'tool_execution_start', 'command': 'Investigating parser edge cases'})
        watch.turn_done()
        watch.last_phase_elapsed = 100000
        self.wait_for(lambda: len(Classifier.requests) == 1)
        self.assertEqual(self.socket.notices, [])
        state = Classifier.requests[0]['state']
        self.assertGreater(state['phase_facts']['last_completed_elapsed_seconds'], 90000)
        self.assertEqual(state['original_user_inputs'], self.user_inputs)
        self.assertEqual(state['supervisor_goal'], 'Summarize parser work.')
        self.assertIn('Preserve None and False', json.dumps(state['original_user_inputs']))
        self.assertNotIn('Preserve None and False', state['supervisor_goal'])

    def test_scope_and_mistake_notice_cites_stable_event(self):
        watch = self.start_watch()
        self.addCleanup(watch.close)
        watch.event({'type': 'tool_execution_start', 'command': 'APPARENT_MISTAKE: assuming parse(None) is valid'})
        watch.turn_done()
        self.wait_for(lambda: bool(self.socket.notices))
        notice = self.socket.notices[0]
        self.assertEqual(notice['classification']['reason'], 'apparent_mistake')
        self.assertEqual(notice['evidence']['id'], notice['classification']['evidence_id'])
        self.assertEqual(notice['job_id'], 'job-a')

    def scope_records(self):
        path = self.child_log.with_suffix('.scope.jsonl')
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def classify(self, watch, attention, reason, confidence):
        Classifier.attention = (attention, reason, confidence)
        watch.event({'type': 'tool_execution_start', 'command': 'git diff --stat'})
        watch.turn_done()
        count = len(self.scope_records()) + 1
        self.wait_for(lambda: len(self.scope_records()) == count and not watch.busy())
        return self.scope_records()[-1]

    def test_wake_requires_concrete_reason_and_confidence_once_per_event_reason(self):
        watch = self.start_watch()
        self.addCleanup(watch.close)
        # Observed live: low-confidence issues citing an ordinary read with no reason.
        watch.event({'type': 'tool_execution_start', 'command': 'cat AGENTS.md && cat jobs.py'})
        cases = [(('possible_issue', 'none', 0.33), False, 'uncertain'),
                 (('possible_issue', 'none', 0.99), False, 'possible_issue'),
                 (('possible_issue', 'apparent_mistake', 0.5), False, 'uncertain'),
                 (('uncertain', 'insufficient_evidence', 0.95), False, 'uncertain'),
                 (('uncertain', 'skipped_verification', 0.9), True, 'uncertain'),
                 (('possible_issue', 'skipped_verification', 0.99), False, 'possible_issue'),
                 (('possible_issue', 'scope_drift', 0.9), True, 'possible_issue')]
        for answer, wake, attention in cases:
            with self.subTest(answer=answer):
                record = self.classify(watch, *answer)
                self.assertEqual((record['wake'], record['classification']['attention']), (wake, attention))
        self.assertEqual([notice['classification']['reason'] for notice in self.socket.notices],
                         ['skipped_verification', 'scope_drift'])

    def test_new_user_input_can_reraise_an_acknowledged_concern(self):
        watch = self.start_watch()
        self.addCleanup(watch.close)
        watch.event({'type': 'tool_execution_start', 'command': 'edit billing.py'})
        self.assertTrue(self.classify(watch, 'possible_issue', 'scope_drift', 0.9)['wake'])
        (self.session / 'decisions.jsonl').write_text(json.dumps(
            {'job_id': 'job-a', 'sequence': 1, 'disposition': 'continue', 'reason': 'Billing is in scope.'}) + '\n')
        self.assertFalse(self.classify(watch, 'possible_issue', 'scope_drift', 0.9)['wake'])
        record_text(self.session, 'Clarification: do not touch billing.', source='test-native',
                    native_id='session-a', turn_id='turn-clarification')
        self.user_inputs = snapshot(self.session)
        self.write_task({'goal': 'Summarize parser work.', 'task': 'Fix and verify parser.',
                         'acceptance': ['parser works']})
        self.assertTrue(self.classify(watch, 'possible_issue', 'scope_drift', 0.9)['wake'])
        self.assertEqual(len(self.socket.notices), 2)

    def test_wake_reasons_are_offered_to_the_classifier(self):
        from scope_watch import CONCRETE_REASONS
        offered = scope_questions({})['reason']['criteria']
        self.assertLessEqual(CONCRETE_REASONS, offered.keys())
        self.assertNotIn('none', CONCRETE_REASONS)
        self.assertNotIn('insufficient_evidence', CONCRETE_REASONS)

    def test_concrete_concern_wakes_once_per_event_despite_continue(self):
        watch = self.start_watch()
        self.addCleanup(watch.close)
        watch.event({'type': 'tool_execution_start', 'command': 'APPARENT_MISTAKE: assuming parse(None) is valid'})
        watch.turn_done()
        self.wait_for(lambda: len(self.socket.notices) == 1 and not watch.busy())
        (self.session / 'decisions.jsonl').write_text(json.dumps(
            {'job_id': 'job-a', 'sequence': 1, 'disposition': 'continue', 'reason': 'Checked; None is valid here.'}) + '\n')
        watch.event({'type': 'tool_execution_start', 'command': 'PARSER_PROGRESS: added focused regression test'})
        watch.turn_done()
        self.wait_for(lambda: len(self.scope_records()) == 2 and not watch.busy())
        self.assertEqual(len(self.socket.notices), 1)
        self.assertEqual(self.scope_records()[1]['evidence']['id'], self.socket.notices[0]['evidence']['id'])
        self.assertFalse(self.scope_records()[1]['wake'])
        self.assertIn('None is valid here', json.dumps(Classifier.requests[-1]['state']['supervisor_dispositions']))
        watch.event({'type': 'tool_execution_start', 'command': 'APPARENT_MISTAKE: now deleting test_jobs.py'})
        watch.turn_done()
        self.wait_for(lambda: len(self.socket.notices) == 2)
        self.assertIn('deleting test_jobs.py', self.socket.notices[1]['evidence']['activity'])

    def test_authority_change_reassesses_retained_events_and_new_activity_is_kept(self):
        Classifier.mode = 'blocked'
        watch = self.start_watch()
        self.addCleanup(watch.close)
        watch.event({'type': 'tool_execution_start', 'command': 'APPARENT_MISTAKE: assumed bad parser behavior'})
        watch.turn_done()
        self.assertTrue(Classifier.started.wait(5))
        self.write_task({'goal': 'Changed supervisor summary.', 'task': 'Fix and verify parser.',
                         'acceptance': ['parser works']})
        watch.event({'type': 'tool_execution_start', 'command': 'PARSER_PROGRESS: added focused regression test'})
        Classifier.release.set()
        self.wait_for(lambda: len(Classifier.requests) >= 3)
        self.wait_for(lambda: bool(self.socket.notices))
        self.assertIn('APPARENT_MISTAKE', json.dumps(Classifier.requests[1]['state']['events']))
        later = Classifier.requests[-1]['state']
        self.assertIn('APPARENT_MISTAKE', json.dumps(later['history']))
        self.assertEqual(len(self.socket.notices), 1)

    def test_delayed_notice_ack_does_not_fail_observer(self):
        self.socket.close()
        self.socket = NoticeSocket(self.status, delayed=True)
        watch = self.start_watch()
        self.addCleanup(watch.close)
        watch.event({'type': 'tool_execution_start', 'command': 'BILLING_DRIFT: changed unrelated billing module'})
        watch.turn_done()
        self.assertTrue(self.socket.entered.wait(5))
        self.assertTrue(watch.busy())
        self.assertIsNone(watch.check())
        time.sleep(5.1)
        self.assertTrue(watch.busy())
        self.socket.release.set()
        self.wait_for(lambda: not watch.busy())
        self.assertFalse(watch.failed)

    def test_classifier_failure_is_advisory_and_durable(self):
        Classifier.mode = 'invalid'
        watch = self.start_watch()
        self.addCleanup(watch.close)
        watch.event({'type': 'tool_execution_start', 'command': 'Read parser source'})
        watch.turn_done()
        self.wait_for(lambda: bool(self.socket.notices))
        self.assertIn('observer_failure', self.socket.notices[0])
        self.assertFalse(watch.busy())
        self.assertIsNone(watch.check())
        watch.event({'type': 'tool_execution_start', 'command': 'Later native work continues'})
        self.assertFalse(watch.pending)
        self.assertIn('invalid typed classification', self.child_log.with_suffix('.jev.jsonl').read_text())
        self.assertIn('observer_failure', self.child_log.with_suffix('.scope.jsonl').read_text())

    def test_rolling_history_is_bounded(self):
        watch = object.__new__(ScopeWatch)
        watch.history = deque()
        watch.history_chars = 0
        watch.condition = threading.Condition()
        watch._append_history([{'id': str(index), 'activity': 'x' * 2400} for index in range(100)])
        self.assertLessEqual(len(watch.history), HISTORY_LIMIT)
        self.assertLessEqual(watch.history_chars, HISTORY_CHARS)
        self.assertEqual(watch.history_chars, sum(len(item['activity']) for item in watch.history))

    def test_review_assesses_findings_individually_and_preserves_original_json(self):
        repo = self.root / 'repo'
        repo.mkdir()
        subprocess.run(['git', '-C', str(repo), 'init', '-qb', 'main'], check=True)
        subprocess.run(['git', '-C', str(repo), 'config', 'user.name', 'Fixture'], check=True)
        subprocess.run(['git', '-C', str(repo), 'config', 'user.email', 'fixture@localhost'], check=True)
        source = repo / 'parser.py'
        source.write_text(''.join(f'line {number}\n' for number in range(1, 81)))
        subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(repo), 'commit', '-qm', 'base'], check=True)
        base = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
        lines = source.read_text().splitlines()
        lines[39] = 'changed line 40'
        source.write_text('\n'.join(lines) + '\n')
        report = self.task / 'review.reply'
        original = json.dumps({'status': 'complete', 'summary': 'Review parser changes.',
                               'findings': [finding('supported', line=40), finding('missing', 'absent.py', 40)],
                               'criteria': [{'criterion': 'parser works', 'result': 'passed',
                                             'evidence': 'Focused source review.'}]})
        report.write_text(original)
        criteria = self.task / 'task.json'
        Classifier.mode = 'mixed'
        late_input = record_text(self.session, 'Captured during review and outside this evaluated snapshot.',
                                 source='test-native', native_id='session-a', turn_id='turn-late-review')
        with patch.dict(os.environ, self.env):
            assessment = assess(repo, base, report, str(criteria), self.session)
        self.assertEqual(report.read_text(), original)
        self.assertEqual(assessment['findings'][0]['validity'], 'unsupported')
        self.assertEqual(assessment['findings'][1]['validity'], 'uncertain')
        self.assertEqual(len(Classifier.requests), 2)
        self.assertEqual(Classifier.requests[0]['state']['finding']['id'], 'supported')
        self.assertIn('Avoid billing changes', json.dumps(Classifier.requests[0]['state']['task_spec']['original_user_inputs']))
        self.assertIn('Preserve None and False', json.dumps(Classifier.requests[0]['state']['task_spec']['original_user_inputs']))
        self.assertEqual(Classifier.requests[0]['state']['task_spec']['supervisor_goal'], 'Summarize parser work.')
        self.assertNotIn(late_input['id'], json.dumps(Classifier.requests[0]['state']['task_spec']))
        self.assertEqual(len(Classifier.requests[0]['state']['evidence']), 2)
        self.assertTrue(Classifier.requests[0]['state']['evidence'][1]['windowed'])
        self.assertFalse(Classifier.requests[0]['state']['evidence'][1]['truncated'])
        self.assertLess(len(json.dumps(Classifier.requests[0])), 30000)
        self.assertNotIn('missing', json.dumps(Classifier.requests[0]['state']))
        self.assertEqual(assessment['findings'][0]['evidence']['kind'], 'diff')
        self.assertFalse(assessment['findings'][0]['context_incomplete'])
        self.assertTrue(assessment['findings'][1]['context_incomplete'])
        self.assertEqual(report.with_suffix('.assessment.json').stat().st_mode & 0o777, 0o600)
        trace = report.with_suffix('.jev.jsonl').read_text()
        self.assertEqual(len(assessment['request_ids']), 2)
        self.assertNotIn(original[:100], trace)
        self.assertNotIn('private-fixture-key', trace)

    def test_clean_review_is_bounded_and_not_treated_as_proof_of_no_defects(self):
        repo = self.root / 'clean'
        repo.mkdir()
        subprocess.run(['git', '-C', str(repo), 'init', '-qb', 'main'], check=True)
        subprocess.run(['git', '-C', str(repo), 'config', 'user.name', 'Fixture'], check=True)
        subprocess.run(['git', '-C', str(repo), 'config', 'user.email', 'fixture@localhost'], check=True)
        (repo / 'parser.py').write_text('def parse(value):\n    return value\n')
        subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(repo), 'commit', '-qm', 'base'], check=True)
        base = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
        (repo / 'parser.py').write_text('def parse(value):\n    return [] if value is None else value\n')
        report = self.task / 'clean.reply'
        original = json.dumps({'status': 'complete', 'summary': 'No findings in bounded review.',
                               'findings': [], 'criteria': []})
        report.write_text(original)
        with patch.dict(os.environ, self.env):
            assessment = assess(repo, base, report, str(self.task / 'task.json'), self.session)
        self.assertEqual(report.read_text(), original)
        self.assertEqual(assessment['clean_assessment']['validity'], 'bounded')
        self.assertEqual(assessment['findings'], [])
        self.assertEqual(assessment['clean_assessment']['evidence']['kind'], 'diff')

    def test_packet_headings_cannot_replace_goal_or_acceptance(self):
        goal = 'Original goal\n# Goal\nThis heading is part of the original goal.'
        task = 'Implement requested task\n# Goal\nThis is task prose, not a replacement goal.\n# Acceptance criteria\nTask prose.'
        packet = {'goal': goal, 'task': task, 'acceptance': ['Original acceptance\n# Goal\nStill a criterion']}
        self.write_task(packet)
        watch = self.start_watch()
        self.addCleanup(watch.close)
        state, _ = watch.context()
        self.assertEqual(state['original_user_inputs'], self.user_inputs)
        self.assertEqual(state['supervisor_goal'], goal)
        self.assertEqual(state['supervisor_task_plan'], task)
        self.assertEqual(state['supervisor_acceptance_criteria'], packet['acceptance'])

    def test_scope_questions_name_original_context_and_explicit_precedence(self):
        watch = self.start_watch()
        self.addCleanup(watch.close)
        state, _ = watch.context()
        instructions = scope_questions(state)['attention']['instructions']
        self.assertIn('original_user_inputs', instructions)
        self.assertIn('supervisor_goal', instructions)
        self.assertIn('supervisor_acceptance_criteria', instructions)
        self.assertIn('do not erase original requirements', instructions)
        self.assertNotIn('original_user_goal', instructions)

    def test_large_task_context_failure_is_advisory(self):
        packet = {'goal': 'Original goal', 'task': 'large plan ' * 7000, 'acceptance': ['Original acceptance']}
        self.write_task(packet)
        watch = self.start_watch()
        self.addCleanup(watch.close)
        watch.event({'type': 'tool_execution_start', 'command': 'Native work still runs'})
        watch.turn_done()
        self.wait_for(lambda: bool(self.socket.notices))
        self.assertIn('observer_failure', self.socket.notices[0])
        self.wait_for(lambda: not watch.busy())

    def test_native_eof_waits_for_delayed_advisory_ack(self):
        for classifier_mode in ('normal', 'http-error'):
            with self.subTest(mode=classifier_mode):
                Classifier.mode = classifier_mode
                self.socket.close()
                self.socket = NoticeSocket(self.status, delayed=True)
                native = self.root / 'claude-eof'
                marker = self.root / 'native-exited'
                native.write_text('#!/usr/bin/env python3\nimport json,sys\nfrom pathlib import Path\n'
                    'print(json.dumps({"type":"system","subtype":"init","session_id":"native-eof"}),flush=True)\n'
                    'sys.stdin.readline()\n'
                    'print(json.dumps({"type":"result","subtype":"success","result":"BILLING_DRIFT: inspect this concern"}),flush=True)\n'
                    'Path(' + repr(str(marker)) + ').touch()\n')
                native.chmod(0o755)
                fifo = self.root / 'eof.steer.fifo'
                if fifo.exists():
                    fifo.unlink()
                os.mkfifo(fifo)
                proc = subprocess.Popen([sys.executable, str(ROOT / 'lib/python/pair_process.py'), 'claude', str(self.root), '', '',
                                         str(fifo), str(self.root / 'steering.md'), str(self.child_log), str(native),
                                         '--input-format', 'stream-json'],
                                        env={**self.env, 'CEREBRO_JEV_ENABLED': '1', 'CEREBRO_PAIR_IDLE': '0'},
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    proc.stdin.write('Original native task')
                    proc.stdin.close()
                    proc.stdin = None
                    self.assertTrue(self.socket.entered.wait(5))
                    self.wait_for(marker.is_file)
                    time.sleep(0.1)
                    self.assertIsNone(proc.poll(), 'advisory ACK must preserve completed native outcome')
                    live = subprocess.run([sys.executable, str(ROOT / 'lib/python/fifo_live.py'), str(fifo)], capture_output=True)
                    self.assertNotEqual(live.returncode, 0, 'native EOF must close its steering transport')
                    self.socket.release.set()
                    stdout, stderr = proc.communicate(timeout=5)
                    self.assertEqual(proc.returncode, 0, stderr + stdout)
                    self.assertIn('BILLING_DRIFT', stdout)
                    if classifier_mode == 'http-error':
                        self.assertIn('observer_failure', self.socket.notices[0])
                finally:
                    if proc.poll() is None:
                        proc.kill()
                        proc.communicate()
                    marker.unlink(missing_ok=True)

    def test_clean_untracked_code_is_an_explicit_evidence_gap(self):
        repo = self.root / 'untracked'
        repo.mkdir()
        for arguments in (('init', '-qb', 'main'), ('config', 'user.name', 'Fixture'),
                          ('config', 'user.email', 'fixture@localhost'), ('commit', '--allow-empty', '-qm', 'base')):
            subprocess.run(['git', '-C', str(repo), *arguments], check=True)
        (repo / 'new-parser.py').write_text('def parse(value):\n    return value\n')
        report = self.task / 'untracked.reply'
        report.write_text(json.dumps({'status': 'complete', 'summary': 'No findings.', 'findings': [], 'criteria': []}))
        state = context(repo, 'HEAD', report, str(self.task / 'task.json'), self.session)
        self.assertEqual(state['clean_evidence']['untracked_files'], ['new-parser.py'])
        self.assertTrue(state['clean_evidence']['untracked_source_omitted'])
        with patch.dict(os.environ, self.env):
            assessment = assess(repo, 'HEAD', report, str(self.task / 'task.json'), self.session)
        self.assertTrue(assessment['context_incomplete'])
        self.assertEqual(assessment['clean_assessment']['validity'], 'uncertain')


if __name__ == '__main__':
    unittest.main()
