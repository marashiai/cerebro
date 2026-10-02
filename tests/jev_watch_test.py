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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'lib' / 'python'))
from jev import Jev
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
        if self.mode == 'stale' and len(self.requests) == 1:
            self.started.set()
            self.release.wait(10)
        state, questions = body['state'], body['questions']
        drift = [event for event in state['events'] if 'BILLING_DRIFT' in event['activity']]
        scope = 'possible_deviation' if drift and 'Billing is now authorized' not in state['requirements'] else 'in_scope'
        choices = {'scope': scope, 'reason': 'unrelated_work' if scope != 'in_scope' else 'none',
                   'evidence': drift[0]['id'] if scope != 'in_scope' else 'none'}
        answers = {name: {'type': 'choice', 'choice': choices[name],
                         'confidence': 0.3 if self.mode == 'low-confidence' else 0.99,
                         'probabilities': {option: float(option == choices[name]) for option in question['criteria']}}
                   for name, question in questions.items()}
        if self.mode == 'invalid':
            answers['scope']['probabilities']['in_scope'] = 9
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
import json, sys
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

    def test_typed_client_rejects_invalid_and_remote_plaintext(self):
        state = {'requirements': 'Fix parser', 'delegated_task': 'Fix parser', 'current_plan': '', 'steering': [],
                 'events': [{'id': 'event-1', 'activity': 'Read parser'}]}
        client = Jev('private-fixture-key', endpoint=self.endpoint)
        self.assertEqual(client.classify(state)['scope'], 'in_scope')
        Classifier.mode = 'invalid'
        with self.assertRaisesRegex(ValueError, 'invalid typed'):
            client.classify(state)
        with self.assertRaisesRegex(ValueError, 'HTTPS'):
            Jev('key', endpoint='http://example.com/v1/systemone')

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
            Jev('private-fixture-key', endpoint=self.endpoint, timeout=0.05).classify(state)
        self.assertLess(time.monotonic() - started, 0.3)
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
        (self.session / 'spec.md').write_text('Billing is now authorized for this task.')
        Classifier.release.set()
        completed = self.response(1)
        self.assertEqual(completed['state'], 'completed')
        self.assertNotIn('notice', completed)
        self.assertGreaterEqual(len(Classifier.requests), 2)
        self.assertIn('Billing is now authorized', Classifier.requests[-1]['state']['requirements'])

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
        self.assertTrue(Path(self.home / 'worktrees').is_dir())
        self.assertEqual(list((self.session / 'children').glob('*.steer.fifo')), [])

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
