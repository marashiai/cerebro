"""Exercise Jev's typed boundary and live parent notice/steering handoff."""

import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'lib' / 'python'))
from jev import Jev
from scope_watch import scope_questions
import wait_detached


class Classifier(BaseHTTPRequestHandler):
    requests = []
    mode = 'normal'
    started = threading.Event()
    release = threading.Event()

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.requests.append(body)
        if self.mode == 'http-error':
            self.send_response(429)
            self.end_headers()
            self.wfile.write(b'{"error":"fixture quota exceeded"}')
            return
        if self.mode == 'stale' and len(self.requests) == 1:
            self.started.set()
            self.release.wait(10)
        state, questions = body['state'], body['questions']
        drift = [event for event in state.get('events', []) if 'BILLING_DRIFT' in event['activity']]
        scope = 'possible_deviation' if drift and 'Billing is now authorized' not in state['requirements'] else 'in_scope'
        choices = {'scope': scope, 'reason': 'unrelated_work' if scope != 'in_scope' else 'none',
                   'evidence': drift[0]['id'] if scope != 'in_scope' else 'none'}
        if 'validity' in questions:
            choices = {'validity': 'supported', 'usefulness': 'useful', 'reason': 'grounded',
                       'evidence': state['evidence'][0]['id']}
            if self.mode in ('unsupported-review', 'ambiguous-evidence'):
                choices.update(validity='unsupported', usefulness='low_value', reason='code_contradiction')
        answers = {name: {'type': 'choice', 'choice': choices[name],
                         'confidence': 0.3 if self.mode == 'low-confidence' else 0.99,
                         'probabilities': {option: float(option == choices[name]) for option in question['criteria']}}
                   for name, question in questions.items()}
        if self.mode == 'ambiguous-evidence':
            answers['evidence']['confidence'] = 0.3
        if self.mode == 'invalid':
            name = 'validity' if 'validity' in questions else 'scope'
            answers[name]['probabilities'][next(iter(questions[name]['criteria']))] = 9
        elif self.mode in ('rounded-down', 'rounded-up', 'unnormalized'):
            answers['reason']['probabilities'].update(
                none=0.76, insufficient_evidence=0.17,
                unrelated_work={'rounded-down': 0.06, 'rounded-up': 0.08, 'unnormalized': 0}[self.mode])
        raw = json.dumps({'model': 'jev-test', 'answers': answers}).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        if self.mode == 'slow':
            for offset in range(0, len(raw), 32):
                self.wfile.write(raw[offset:offset + 32])
                self.wfile.flush()
                time.sleep(0.015)
        else:
            self.wfile.write(raw)


FAKE_CODEX = r'''#!/usr/bin/env python3
import json, os, sys
if 'mcp' in sys.argv:
    print('[]')
    raise SystemExit(0)
def send(value):
    print(json.dumps(value), flush=True)
for line in sys.stdin:
    request = json.loads(line)
    method, params = request.get('method'), request.get('params', {})
    if method == 'initialize':
        send({'id': request['id'], 'result': {}})
    elif method in ('thread/start', 'thread/resume'):
        send({'id': request['id'], 'result': {'thread': {'id': 'native-watch-child'}}})
    elif method == 'turn/start':
        text = params['input'][0]['text']
        turn = str(request['id'])
        send({'id': request['id'], 'result': {'turn': {'id': turn}}})
        send({'method': 'turn/started', 'params': {'turn': {'id': turn}}})
        answer = 'PARSER_VERIFIED' if 'SCOPE_CORRECTION' in text or 'IN_SCOPE_ONLY' in text else 'BILLING_DRIFT: implementing unrelated checkout billing.'
        answer = os.environ.get('NATIVE_REVIEW_TEXT', answer)
        send({'method': 'item/completed', 'params': {'item': {'id': turn, 'type': 'agentMessage', 'text': answer}}})
        send({'method': 'turn/completed', 'params': {'turn': {'id': turn, 'status': 'completed'}}})
'''


class WatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Classifier)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.endpoint = 'http://127.0.0.1:' + str(cls.server.server_port) + '/v1/systemone'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        Classifier.requests = []
        Classifier.mode = 'normal'
        Classifier.started.clear()
        Classifier.release.clear()
        self.temp = tempfile.TemporaryDirectory(prefix='cerebro-jev-test-')
        self.directory = Path(self.temp.name)
        self.repo = self.directory / 'repo'
        self.repo.mkdir()
        subprocess.run(['git', 'init', '-qb', 'main', str(self.repo)], check=True)
        (self.repo / 'parser.txt').write_text('parser fixture\n')
        subprocess.run(['git', '-C', str(self.repo), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(self.repo), '-c', 'user.name=Fixture', '-c', 'user.email=fixture@localhost',
                        'commit', '-qm', 'test fixture'], check=True)
        self.home = self.directory / 'home'
        self.session = self.home / 'sessions' / 'watch-parent'
        (self.session / 'plans').mkdir(parents=True)
        (self.session / 'children').mkdir()
        (self.session / 'metadata.json').write_text(json.dumps({'backend': 'codex', 'role': 'supervisor'}))
        (self.session / 'spec.md').write_text('Fix the parser. No billing or publishing changes.')
        (self.session / 'plans' / 'work.md').write_text('Inspect parser; fix bug; run tests.')
        self.native = self.directory / 'codex'
        self.native.write_text(FAKE_CODEX)
        self.native.chmod(0o755)
        self.env = {key: value for key, value in os.environ.items() if not key.startswith('CEREBRO_')}
        self.env.update(CEREBRO_HOME=str(self.home), CEREBRO_SESSION_ID='watch-parent', CEREBRO_SESSION_DIR=str(self.session),
                        CEREBRO_BACKEND='codex', CEREBRO_CODEX_CMD=str(self.native), CEREBRO_JEV_ENABLED='0',
                        CEREBRO_JEV_API_KEY='private-fixture-key', CEREBRO_JEV_ENDPOINT=self.endpoint)
        self.processes = []
        self.messages = queue.Queue()

    def tearDown(self):
        Classifier.release.set()
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            if hasattr(process, 'reader'):
                process.reader.join(timeout=1)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream:
                    stream.close()
        for job_file in (self.session / 'detached-jobs').glob('*.json'):
            subprocess.run([str(ROOT / 'bin' / 'cerebro'), 'cancel', job_file.stem], env=self.env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        self.temp.cleanup()

    def cli(self, *argv):
        return subprocess.run([str(ROOT / 'bin' / 'cerebro'), *argv], env=self.env, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)

    def start_parent(self):
        process = subprocess.Popen([sys.executable, str(ROOT / 'lib' / 'python' / 'command_server.py'),
                                    'supervisor', str(ROOT / 'bin' / 'cerebro')], env=self.env, text=True,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.processes.append(process)
        def read():
            for line in process.stdout:
                self.messages.put(json.loads(line))
        process.reader = threading.Thread(target=read, daemon=True)
        process.reader.start()
        return process

    def request(self, process, identifier, argv):
        process.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': identifier, 'method': 'tools/call',
                                       'params': {'name': 'command', 'arguments': {'argv': argv}}}) + '\n')
        process.stdin.flush()

    def response(self, identifier):
        message = self.messages.get(timeout=20)
        self.assertEqual(message['id'], identifier)
        response = json.loads(message['result']['content'][0]['text'])
        self.assertEqual(message['result']['isError'], response['exit_code'] != 0)
        return response

    def execute(self, process, identifier=1, marker=''):
        self.request(process, identifier, ['execute', str(self.repo), '--prompt', 'Fix and verify parser. ' + marker, '--watch'])

    def evaluate(self, client, state):
        response = client.evaluate(state, scope_questions(state), self.directory / 'client.jev.jsonl')
        return response['answers']['scope']['choice']

    def test_typed_client_rejects_invalid_and_remote_plaintext(self):
        state = {'requirements': 'Fix parser', 'delegated_task': 'Fix parser', 'current_plan': '', 'steering': [],
                 'events': [{'id': 'event-1', 'activity': 'Read parser'}]}
        client = Jev('private-fixture-key', endpoint=self.endpoint)
        self.assertEqual(self.evaluate(client, state), 'in_scope')
        Classifier.mode = 'invalid'
        with self.assertRaisesRegex(ValueError, 'invalid typed'):
            self.evaluate(client, state)
        with self.assertRaisesRegex(ValueError, 'HTTPS'):
            Jev('key', endpoint='http://example.com/v1/systemone')

    def test_typed_client_accepts_rounded_probability_totals(self):
        state = {'requirements': 'Fix parser', 'delegated_task': 'Fix parser', 'current_plan': '', 'steering': [],
                 'events': [{'id': 'event-1', 'activity': 'Read parser'}]}
        client = Jev('private-fixture-key', endpoint=self.endpoint)
        for mode in ('rounded-down', 'rounded-up'):
            with self.subTest(mode=mode):
                Classifier.mode = mode
                self.assertEqual(self.evaluate(client, state), 'in_scope')
        Classifier.mode = 'unnormalized'
        with self.assertRaisesRegex(ValueError, 'invalid typed classification.*reason'):
            self.evaluate(client, state)

    def test_rounded_classification_finishes_watched_child(self):
        Classifier.mode = 'rounded-down'
        process = self.start_parent()
        self.execute(process, marker='IN_SCOPE_ONLY')
        result = self.response(1)
        self.assertEqual(result['exit_code'], 0)
        self.assertEqual(result['state'], 'completed')
        self.assertIn('PARSER_VERIFIED', result['text'])
        child = next(iter(json.loads((self.session / 'child-sessions.json').read_text()).values()))
        self.assertEqual(child['status'], 'done')

    def test_review_includes_logged_jev_assessment_with_code_and_requirements(self):
        review = '[P1] Handle empty input at parser.txt:1. The current implementation returns the wrong result.\n'
        (self.repo / 'parser.txt').write_text('incorrect empty-input handling\n')
        criteria = self.session / 'plans' / 'criteria.md'
        criteria.write_text('Empty input must return an empty result.')
        self.env.update(CEREBRO_JEV_ENABLED='1', NATIVE_REVIEW_TEXT=review)
        result = self.cli('review', str(self.repo), '--base', 'main', '--criteria-file', str(criteria))
        self.assertEqual(result.returncode, 0, result.stderr)
        report = Path(result.stdout.strip())
        self.assertIn('Jev review assessment', report.read_text())
        self.assertEqual(report.with_suffix('.assessment.json').stat().st_mode & 0o777, 0o600)
        self.assertTrue(report.read_text().endswith(review))
        self.assertEqual(len(Classifier.requests), 1)
        state = Classifier.requests[0]['state']
        self.assertIn('No billing', state['requirements']['text'])
        self.assertIn('Empty input', state['criteria']['text'])
        self.assertEqual(state['review']['text'], review)
        self.assertIn('incorrect empty-input handling', json.dumps(state['evidence']))
        trace = report.with_suffix('.jev.jsonl')
        records = [json.loads(line) for line in trace.read_text().splitlines()]
        self.assertEqual(records[0]['payload'], Classifier.requests[0])
        self.assertEqual(records[0]['request_id'], records[1]['request_id'])
        self.assertNotIn('private-fixture-key', trace.read_text())

    def test_review_retains_findings_and_uncertainty(self):
        review = '[P1] A concrete claim at parser.txt:1.\n'
        self.env.update(CEREBRO_JEV_ENABLED='1', NATIVE_REVIEW_TEXT=review)
        for mode, validity in [('unsupported-review', 'unsupported'), ('low-confidence', 'uncertain'),
                               ('ambiguous-evidence', 'unsupported')]:
            with self.subTest(mode=mode):
                Classifier.mode = mode
                result = self.cli('review', str(self.repo), '--base', 'main')
                self.assertEqual(result.returncode, 0, result.stderr)
                report = Path(result.stdout.strip())
                assessment = json.loads(report.with_suffix('.assessment.json').read_text())
                self.assertEqual(assessment['validity'], validity)
                self.assertTrue(report.read_text().endswith(review))
                if mode == 'low-confidence':
                    self.assertIn('uncertain** (Jev answer: supported, confidence 0.30)', report.read_text())

    def test_review_assessment_failure_retains_original_and_does_not_advance_state(self):
        Classifier.mode = 'invalid'
        review = '[P1] A concrete claim at parser.txt:1.\n'
        self.env.update(CEREBRO_JEV_ENABLED='1', NATIVE_REVIEW_TEXT=review)
        result = self.cli('review', str(self.repo), '--base', 'main')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Jev assessment failed', result.stderr)
        self.assertEqual(list((self.session / 'review-state').glob('*.json')), [])
        report = next((self.session / 'children').glob('review-*.md'))
        self.assertEqual(report.read_text(), review)
        records = [json.loads(line) for line in report.with_suffix('.jev.jsonl').read_text().splitlines()]
        self.assertIn('invalid typed classification (validity)', records[-1]['error'])

    def test_review_evidence_includes_untracked_files_and_never_follows_outside_symlinks(self):
        from review_check import context
        outside = self.directory / 'outside.txt'
        outside.write_text('PRIVATE_OUTSIDE_CONTENT')
        (self.repo / 'escape.txt').symlink_to(outside)
        (self.repo / 'new.txt').write_text('NEW_UNTRACKED_EVIDENCE')
        report = self.session / 'review.md'
        report.write_text('[P1] Inspect escape.txt:1 and new.txt:1.')
        state = context(self.repo, 'HEAD', report, '', self.session)
        self.assertIn('NEW_UNTRACKED_EVIDENCE', json.dumps(state['evidence']))
        self.assertNotIn('PRIVATE_OUTSIDE_CONTENT', json.dumps(state))
        self.assertIn({'path': 'escape.txt', 'line': 1}, state['omitted_sources'])

    def test_partial_new_file_context_is_marked_with_its_actual_range(self):
        from review_check import context
        (self.repo / 'long-new.txt').write_text('first line\n' * 50)
        report = self.session / 'review.md'
        report.write_text('The new file looks correct.')
        state = context(self.repo, 'HEAD', report, '', self.session)
        excerpt = next(item for item in state['evidence'] if item.get('path') == 'long-new.txt')
        self.assertTrue(excerpt['truncated'])
        self.assertEqual((excerpt['start_line'], excerpt['end_line']), (1, 21))

    def test_report_replacement_failure_preserves_original_findings(self):
        from review_check import assess
        report = self.session / 'review.md'
        original = '[P1] Inspect parser.txt:1.\n'
        report.write_text(original)
        replace = os.replace
        def fail_report(source, destination):
            if destination == report:
                raise OSError('fixture report replacement failure')
            replace(source, destination)
        with patch.dict(os.environ, self.env), patch('review_check.os.replace', side_effect=fail_report):
            with self.assertRaisesRegex(OSError, 'replacement failure'):
                assess(self.repo, 'HEAD', report, '', self.session)
        self.assertEqual(report.read_text(), original)
        self.assertEqual(list(self.session.glob('.review.md-*')), [])

    def test_changed_review_inputs_are_not_published_as_current(self):
        Classifier.mode = 'stale'
        self.env.update(CEREBRO_JEV_ENABLED='1', NATIVE_REVIEW_TEXT='[P1] Inspect parser.txt:1.\n')
        process = self.start_parent()
        self.request(process, 1, ['review', str(self.repo), '--base', 'main'])
        self.assertTrue(Classifier.started.wait(10))
        (self.repo / 'parser.txt').write_text('changed during assessment')
        Classifier.release.set()
        result = self.response(1)
        self.assertNotEqual(result['exit_code'], 0)
        self.assertIn('inputs changed', result['text'])
        self.assertEqual(list((self.session / 'children').glob('*.assessment.json')), [])

    def test_http_failure_is_logged_without_authorization(self):
        Classifier.mode = 'http-error'
        state = {'events': []}
        with self.assertRaisesRegex(RuntimeError, 'Jev HTTP 429'):
            self.evaluate(Jev('private-fixture-key', endpoint=self.endpoint), state)
        path = self.directory / 'client.jev.jsonl'
        records = [json.loads(line) for line in path.read_text().splitlines()]
        self.assertEqual(records[-1]['http_status'], 429)
        self.assertIn('fixture quota exceeded', records[-1]['body'])
        self.assertEqual(records[-1]['error'], 'Jev HTTP 429')
        self.assertNotIn('Authorization', path.read_text())
        self.assertNotIn('private-fixture-key', path.read_text())

    def test_notice_wakes_parent_then_native_steering_finishes_same_job(self):
        process = self.start_parent()
        self.execute(process)
        response = self.response(1)
        self.assertEqual(response['state'], 'running')
        self.assertEqual(response['notice']['classification']['scope'], 'possible_deviation')
        self.assertIn('BILLING_DRIFT', response['notice']['evidence']['activity'])
        self.assertEqual(response['notice']['native_id'], 'native-watch-child')
        self.assertTrue(Path(response['notice']['steering_pipe']).is_fifo())
        payload = Classifier.requests[0]['state']
        self.assertIn('No billing', payload['requirements'])
        self.assertIn('Fix and verify parser', payload['delegated_task'])
        self.assertIn('Inspect parser', payload['current_plan'])
        self.request(process, 2, ['steer', response['notice']['steering_pipe'], 'SCOPE_CORRECTION: fix parser only.'])
        self.assertEqual(self.response(2)['exit_code'], 0)
        self.request(process, 3, ['wait', response['job_id'], '--after', str(response['sequence'])])
        completed = self.response(3)
        self.assertEqual(completed['state'], 'completed')
        self.assertIn('PARSER_VERIFIED', completed['text'])
        self.assertTrue(any('SCOPE_CORRECTION' in json.dumps(request['state']['steering']) for request in Classifier.requests))
        self.assertNotIn('private-fixture-key', json.dumps(completed))

    def test_total_deadline_and_malformed_key_do_not_leak_credentials(self):
        Classifier.mode = 'slow'
        state = {'requirements': 'Fix parser', 'delegated_task': 'Fix parser', 'current_plan': '', 'steering': [],
                 'events': [{'id': 'event-1', 'activity': 'Read parser'}]}
        started = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, 'total deadline'):
            self.evaluate(Jev('private-fixture-key', endpoint=self.endpoint, timeout=0.05), state)
        self.assertLess(time.monotonic() - started, 0.3)
        response = json.loads((self.directory / 'client.jev.jsonl').read_text().splitlines()[-1])
        self.assertIn('total deadline', response['error'])
        with self.assertRaises(ValueError) as error:
            Jev('private-fixture-key\ninvalid', endpoint=self.endpoint)
        self.assertNotIn('private-fixture-key', str(error.exception))

    def test_in_scope_is_silent_and_no_watch_overrides_default(self):
        process = self.start_parent()
        self.execute(process, marker='IN_SCOPE_ONLY')
        completed = self.response(1)
        self.assertEqual(completed['state'], 'completed')
        self.assertNotIn('notice', completed)
        self.assertIn('PARSER_VERIFIED', completed['text'])
        self.env['CEREBRO_JEV_ENABLED'] = '1'
        self.env['CEREBRO_JEV_API_KEY'] = ''
        self.env['CEREBRO_PAIR_IDLE'] = '0'
        direct = self.cli('doc-write', str(self.repo), '--prompt', 'IN_SCOPE_ONLY', '--no-watch')
        self.assertEqual(direct.returncode, 0, direct.stderr)

    def test_low_confidence_is_uncertain_and_notice_survives_parent_disconnect(self):
        Classifier.mode = 'low-confidence'
        process = self.start_parent()
        self.execute(process)
        notice = self.response(1)
        self.assertEqual(notice['notice']['classification']['scope'], 'uncertain')
        process.stdin.close()
        process.wait(timeout=5)
        repeated = self.cli('wait', notice['job_id'])
        self.assertEqual(json.loads(repeated.stdout)['sequence'], notice['sequence'])
        self.assertEqual(self.cli('steer', notice['notice']['steering_pipe'], 'SCOPE_CORRECTION: parser only.').returncode, 0)
        final = self.cli('wait', notice['job_id'], '--after', str(notice['sequence']))
        self.assertEqual(final.returncode, 0, final.stderr)
        self.assertIn('PARSER_VERIFIED', json.loads(final.stdout)['text'])

    def test_changed_requirements_invalidate_in_flight_classification(self):
        Classifier.mode = 'stale'
        process = self.start_parent()
        self.execute(process)
        self.assertTrue(Classifier.started.wait(10))
        trace = next((self.session / 'children').glob('*.jev.jsonl'))
        started = [json.loads(line) for line in trace.read_text().splitlines()]
        self.assertEqual(len(started), 1)
        self.assertEqual(started[0]['type'], 'request')
        self.assertEqual(started[0]['payload'], Classifier.requests[0])
        self.assertEqual(trace.stat().st_mode & 0o777, 0o600)
        (self.session / 'spec.md').write_text('Billing is now authorized for this task.')
        Classifier.release.set()
        completed = self.response(1)
        self.assertEqual(completed['state'], 'completed')
        self.assertNotIn('notice', completed)
        self.assertGreaterEqual(len(Classifier.requests), 2)
        self.assertIn('Billing is now authorized', Classifier.requests[-1]['state']['requirements'])
        records = [json.loads(line) for line in trace.read_text().splitlines()]
        self.assertEqual([record['type'] for record in records], ['request', 'response', 'request', 'response'])
        self.assertEqual(records[0]['request_id'], records[1]['request_id'])
        self.assertNotEqual(records[0]['request_id'], records[2]['request_id'])
        self.assertNotIn('private-fixture-key', trace.read_text())
        decision = json.loads(next((self.session / 'children').glob('*.scope.jsonl')).read_text().splitlines()[-1])
        self.assertEqual(decision['request_id'], records[-1]['request_id'])

    def test_classifier_error_stops_child_and_preserves_resume_record(self):
        Classifier.mode = 'invalid'
        process = self.start_parent()
        self.execute(process)
        result = self.response(1)
        self.assertNotEqual(result['exit_code'], 0)
        self.assertEqual(result['state'], 'completed')
        self.assertIn('Jev watch stopped', result['text'])
        child = next(iter(json.loads((self.session / 'child-sessions.json').read_text()).values()))
        self.assertEqual(child['id'], 'native-watch-child')
        self.assertEqual(child['status'], 'running')
        self.assertEqual(Path(child['repo']).resolve(), self.repo.resolve())
        self.assertEqual(list((self.session / 'children').glob('*.steer.fifo')), [])
        trace = next((self.session / 'children').glob('*.jev.jsonl'))
        records = [json.loads(line) for line in trace.read_text().splitlines()]
        response = records[-1]
        self.assertEqual(response['type'], 'response')
        self.assertEqual(response['http_status'], 200)
        self.assertEqual(json.loads(response['body'])['answers']['scope']['probabilities']['in_scope'], 9)
        self.assertIn('invalid typed classification', response['error'])
        self.assertGreaterEqual(response['elapsed_ms'], 0)
        self.assertNotIn('private-fixture-key', trace.read_text())

    def test_watch_preflight_and_wait_sidecar_confinement(self):
        direct = self.cli('execute', str(self.repo), '--prompt', 'Fix parser', '--watch')
        self.assertNotEqual(direct.returncode, 0)
        self.assertIn('durable', direct.stderr)
        self.assertFalse((self.home / 'worktrees').exists())
        status = self.session / 'example.status'
        status.write_text('0\n')
        outside = self.directory / 'outside.json'
        outside.write_text('{"sequence":1,"acknowledged":0,"notices":[]}')
        Path(str(status) + '.updates.json').symlink_to(outside)
        result = self.cli('wait', str(status), '--after', '1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('detached output must be under', result.stderr)
        self.assertEqual(json.loads(outside.read_text())['acknowledged'], 0)

    def test_completed_acknowledgments_never_regress(self):
        status = self.session / 'done.status'
        status.write_text('0\n')
        updates = Path(str(status) + '.updates.json')
        wait_detached.write_updates(updates, {'sequence': 2, 'acknowledged': 0,
            'notices': [{'sequence': number, 'notice': {'scope': 'uncertain'}} for number in (1, 2)]})
        entered, release, newer_done = threading.Event(), threading.Event(), threading.Event()
        write = wait_detached._write_updates
        errors = []

        def delayed_write(path, state):
            if threading.current_thread().name == 'older-ack':
                entered.set()
                release.wait(2)
            write(path, state)

        def acknowledge(sequence):
            try:
                wait_detached.terminal_update(str(status), sequence)
            except Exception as error:
                errors.append(error)
            finally:
                if sequence == 2:
                    newer_done.set()

        wait_detached._write_updates = delayed_write
        older = threading.Thread(target=acknowledge, args=(1,), name='older-ack')
        newer = threading.Thread(target=acknowledge, args=(2,))
        try:
            older.start()
            self.assertTrue(entered.wait(2))
            newer.start()
            newer_done.wait(0.1)
        finally:
            release.set()
            older.join(timeout=2)
            newer.join(timeout=2)
            wait_detached._write_updates = write
        self.assertEqual(errors, [])
        self.assertEqual(json.loads(updates.read_text())['acknowledged'], 2)
        self.assertEqual(wait_detached.terminal_update(str(status), 0)['kind'], 'completed')
        # A monitor finishing cancellation must also retain a terminal waiter's ACK.
        wait_detached.write_updates(updates, {'sequence': 2, 'acknowledged': 1,
            'notices': [{'sequence': 2, 'notice': {'scope': 'uncertain'}}]})
        self.assertEqual(json.loads(updates.read_text())['acknowledged'], 2)


if __name__ == '__main__':
    unittest.main()
