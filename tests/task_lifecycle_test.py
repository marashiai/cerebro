"""Task lifecycle over real Git and native CLI transport fixtures, without models."""
import json
import os
from pathlib import Path
import shlex
import signal
import time
import sys
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
CLI = str(ROOT / 'bin/cerebro')


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cerebro-task-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        for args in (('init', '-qb', 'main'), ('config', 'user.name', 'Fixture'),
                     ('config', 'user.email', 'fixture@example.com')):
            subprocess.run(['git', '-C', str(self.repo), *args], check=True)
        (self.repo / 'file').write_text('initial\n')
        subprocess.run(['git', '-C', str(self.repo), 'add', 'file'], check=True)
        subprocess.run(['git', '-C', str(self.repo), 'commit', '-qm', 'initial'], check=True)
        self.home = self.root / 'home'
        self.session = self.home / 'sessions' / 'fixture'
        (self.session / 'children').mkdir(parents=True)
        (self.session / 'metadata.json').write_text('{"backend":"codex"}')
        (self.session / 'transcript.jsonl').touch()
        self.config = self.root / 'config.json'
        self.settings = {'acceptance': ['Original acceptance'], 'execute': {}, 'review': {}}
        self.config.write_text(json.dumps(self.settings))
        self.log = self.root / 'native.jsonl'
        self.env = {**os.environ, 'CEREBRO_HOME': str(self.home), 'CEREBRO_SESSION_ID': 'fixture',
                    'CEREBRO_MODEL': 'implementation-model', 'CEREBRO_REVIEW_MODEL': 'review-model',
                    'CEREBRO_JEV_ENABLED': '0', 'CEREBRO_PAIR_IDLE': '0', 'NATIVE_FIXTURE_LOG': str(self.log),
                    'TASK_FIXTURE_CONFIG': str(self.config), 'CEREBRO_BACKEND': 'codex'}
        self.env.pop('CEREBRO_RESUME_BACKEND', None)
        self.env.pop('CEREBRO_SESSION_DIR', None)
        for backend in ('codex', 'claude'):
            native = self.root / backend
            native.write_text('#!/bin/sh\nexport NATIVE_FIXTURE_LOG=' + shlex.quote(str(self.log)) + '\nexport TASK_FIXTURE_CONFIG=' + shlex.quote(str(self.config)) + '\nexec python3 ' + shlex.quote(str(ROOT / 'tests/native_child_fixture.py')) + ' ' + backend + ' "$@"\n')
            native.chmod(0o755)
            self.env['CEREBRO_' + backend.upper() + '_CMD'] = str(native)
        pi = self.root / 'pi'
        fixture_dir = self.root / 'pi-fixture'
        fixture_dir.mkdir()
        (fixture_dir / 'fixture.json').write_text(json.dumps({'request_log': str(self.log)}))
        pi.write_text('#!/bin/sh\nexec python3 ' + shlex.quote(str(ROOT / 'tests/pi_fixture.py')) + ' ' + shlex.quote(str(fixture_dir)) + ' "$@"\n')
        pi.chmod(0o755)
        self.env['CEREBRO_PI_CMD'] = str(pi)
        self.packet = {'goal': 'Original user goal', 'task': 'Implement the original plan',
                       'acceptance': self.settings['acceptance'], 'repo': str(self.repo), 'base': 'main'}

    def cli(self, *argv, packet=None, ok=True):
        proc = subprocess.run([CLI, *argv], input=json.dumps(packet) if packet else None,
                              text=True, capture_output=True, env=self.env, timeout=20)
        self.assertEqual(proc.returncode == 0, ok, proc.stderr + proc.stdout)
        return json.loads(proc.stdout) if proc.stdout.startswith('{') else proc

    def records(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def stages(self):
        return [item.get('child_role') or item['role'] for item in self.records() if 'argv' in item]

    def update(self, role, **settings):
        self.settings[role].update(settings)
        self.config.write_text(json.dumps(self.settings))

    def test_happy_path_three_native_backends(self):
        for backend in ('codex', 'claude', 'pi'):
            with self.subTest(backend=backend):
                self.env['CEREBRO_BACKEND'] = backend
                (self.session / 'metadata.json').write_text(json.dumps({'backend': backend}))
                self.packet['task'] = 'Implement the original plan for ' + backend
                self.log.write_text('')
                result = self.cli('execute', packet=self.packet)
                self.assertEqual(self.stages(), ['execute', 'review'])
                self.assertEqual(result['stage'], 'done')
                self.assertEqual(result['implementation']['evidence'], ['bash tests/run.sh: fixture tests passed'])
                self.assertEqual(result['review']['findings'], [])
                turns = [item['params']['input'][0]['text'] for item in self.records() if item.get('method') == 'turn/start'] if backend == 'codex' else [item['message'] if item.get('type') == 'prompt' else item['message']['content'] for item in self.records() if item.get('type') in ('prompt', 'user')]
                self.assertEqual(len(turns), 2)
                for prompt in turns:
                    self.assertIn('Original user goal', prompt)
                    self.assertIn('Original acceptance', prompt)
                self.assertNotIn('execute finished', turns[1])
                self.assertEqual((self.session / 'spec.md').read_text(), (self.session / 'tasks' / result['task_id'] / 'spec.md').read_text())
                self.cli('execute', '--resume', result['task_id'])
                self.assertEqual(self.stages(), ['execute', 'review'])
                self.cli('answer', result['task_id'], 'unneeded', ok=False)
                self.assertEqual(self.stages(), ['execute', 'review'])

    def test_question_answer_same_child_then_one_review(self):
        self.update('execute', status='question')
        result = self.cli('execute', packet=self.packet)
        self.assertEqual(self.stages(), ['execute'])
        self.assertEqual(result['status'], 'question')
        self.update('execute', status='complete')
        self.cli('answer', result['task_id'], 'Use the requested behavior')
        self.assertEqual(self.stages(), ['execute', 'execute', 'review'])
        resume = [item for item in self.records() if item.get('method') == 'thread/resume']
        self.assertEqual(len(resume), 1)
        self.assertEqual(resume[0]['params']['threadId'], 'NATIVE-CHILD-1')
        self.cli('execute', '--resume', result['task_id'])
        self.assertEqual(len(self.stages()), 3)

    def test_incomplete_and_malformed_handoffs_never_review(self):
        for status in ('unfinished', 'blocked', 'failed', 'malformed'):
            self.log.write_text('')
            self.packet['task'] = status
            self.update('execute', status=status)
            result = self.cli('execute', packet=self.packet, ok=status != 'malformed')
            self.assertEqual(self.stages(), ['execute'])
            self.assertEqual(result['status'], 'unfinished' if status == 'malformed' else status)
            if status == 'malformed':
                self.assertIn('original reply retained', result['error'])

    def test_native_worker_failure_has_no_review(self):
        self.update('execute', native_mode='failure')
        result = self.cli('execute', packet=self.packet, ok=False)
        self.assertEqual(self.stages(), ['execute'])
        self.update('execute', native_mode='ok')
        self.cli('execute', '--resume', result['task_id'])
        self.assertEqual(self.stages(), ['execute', 'execute', 'review'])
        self.assertEqual(sum(item.get('method') == 'thread/resume' for item in self.records()), 1)

    def test_reviewer_failure_resumes_without_implementation(self):
        self.update('review', native_mode='failure')
        result = self.cli('execute', packet=self.packet, ok=False)
        self.assertEqual(result['stage'], 'review')
        self.update('review', native_mode='ok')
        self.cli('execute', '--resume', result['task_id'])
        self.assertEqual(self.stages(), ['execute', 'review', 'review'])

    def test_unverified_criteria_do_not_become_passed(self):
        self.update('execute', criterion_result='unverified')
        result = self.cli('execute', packet=self.packet)
        self.assertEqual(result['implementation']['criteria'][0]['result'], 'unverified')
        self.assertEqual(self.stages(), ['execute', 'review'])

    def test_same_configured_models_are_visible_without_substitution(self):
        self.env['CEREBRO_REVIEW_MODEL'] = self.env['CEREBRO_MODEL']
        result = self.cli('execute', packet=self.packet)
        self.assertEqual(result['review_model_relation'], 'same configured model')
        self.assertEqual(self.stages(), ['execute', 'review'])

    def test_native_default_models_and_arbitrary_role_settings(self):
        for backend in ('codex', 'claude', 'pi'):
            self.env['CEREBRO_BACKEND'] = backend
            (self.session / 'metadata.json').write_text(json.dumps({'backend': backend}))
            for defaults in (True, False):
                with self.subTest(backend=backend, defaults=defaults):
                    self.log.write_text('')
                    self.packet['task'] = backend + str(defaults)
                    self.env['CEREBRO_MODEL'] = self.env['CEREBRO_REVIEW_MODEL'] = ''
                    self.packet['models'] = {} if defaults else {
                        'implementor': {'model': 'arbitrary-worker-id', 'effort': 'arbitrary-worker-effort'},
                        'reviewer': {'model': 'arbitrary-review-id', 'effort': 'arbitrary-review-effort'}}
                    result = self.cli('execute', packet=self.packet)
                    self.assertEqual(self.stages(), ['execute', 'review'])
                    self.assertEqual(result['review_model_relation'], 'native defaults unresolved' if defaults else 'different configured models')
                    if backend == 'codex':
                        starts = [item['params'] for item in self.records() if item.get('method') == 'thread/start']
                        turns = [item['params'] for item in self.records() if item.get('method') == 'turn/start']
                        if defaults:
                            self.assertTrue(all('model' not in item for item in starts))
                            self.assertTrue(all('effort' not in item for item in turns))
                        else:
                            self.assertEqual([item['model'] for item in starts], ['arbitrary-worker-id', 'arbitrary-review-id'])
                            self.assertEqual([item['effort'] for item in turns], ['arbitrary-worker-effort', 'arbitrary-review-effort'])
                    else:
                        argvs = [item['argv'] for item in self.records() if 'argv' in item]
                        effort_flag = '--thinking' if backend == 'pi' else '--effort'
                        if defaults:
                            self.assertTrue(all('--model' not in argv and effort_flag not in argv for argv in argvs))
                        else:
                            self.assertEqual([argv[argv.index('--model') + 1] for argv in argvs], ['arbitrary-worker-id', 'arbitrary-review-id'])
                            self.assertEqual([argv[argv.index(effort_flag) + 1] for argv in argvs], ['arbitrary-worker-effort', 'arbitrary-review-effort'])

    def test_quiet_native_work_is_not_stalled_or_retried(self):
        self.env.update(NATIVE_FIXTURE_DELAY='0.3', CEREBRO_PAIR_STALL='0.05', CEREBRO_PAIR_STALL_BUSY='0.05')
        self.cli('execute', packet=self.packet)
        self.assertEqual(self.stages(), ['execute', 'review'])

    def wait_until(self, condition, proc=None):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            value = condition()
            if value:
                return value
            if proc and proc.poll() is not None:
                self.fail(str(proc.communicate()))
            time.sleep(0.02)
        self.fail('fixture boundary was not reached')

    def running_task(self):
        paths = list((self.session / 'tasks').glob('*/task.json'))
        return json.loads(paths[0].read_text()) if paths else None

    def start_task(self, **env):
        proc = subprocess.Popen([CLI, 'execute'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, env={**self.env, **env})
        proc.stdin.write(json.dumps(self.packet))
        proc.stdin.close()
        proc.stdin = None
        return proc

    def test_controller_interruption_before_and_after_receipt(self):
        for after_receipt in (False, True):
            with self.subTest(after_receipt=after_receipt):
                self.packet['task'] = 'Interruption ' + str(after_receipt)
                self.log.write_text('')
                proc = self.start_task(NATIVE_FIXTURE_DELAY='0.6')
                try:
                    self.wait_until(lambda: self.log.exists() and any(item.get('method') == 'turn/start' for item in self.records()), proc)
                    state = self.running_task() if not after_receipt else None
                    task_file = max((self.session / 'tasks').glob('*/task.json'), key=lambda path: path.stat().st_mtime_ns)
                    state = json.loads(task_file.read_text())
                    task_id = task_file.parent.name
                    receipt = Path(state['attempt']['log']).with_suffix('.exit')
                    if after_receipt:
                        os.kill(proc.pid, signal.SIGSTOP)
                        self.wait_until(receipt.is_file)
                    os.kill(proc.pid, signal.SIGKILL)
                    proc.wait(timeout=5)
                    if not after_receipt:
                        busy = self.cli('execute', '--resume', task_id, ok=False)
                        self.assertIn('already running', busy.stderr)
                        self.wait_until(receipt.is_file)
                    proc.communicate(timeout=5)
                    result = self.cli('execute', '--resume', task_id)
                    self.assertEqual(result['stage'], 'done')
                    self.assertEqual(self.stages(), ['execute', 'review'])
                finally:
                    if proc.poll() is None:
                        os.kill(proc.pid, signal.SIGKILL)
                        proc.communicate()

    def test_answer_interruption_before_launch_retains_pending_answer(self):
        self.update('execute', status='question')
        question = self.cli('execute', packet=self.packet)
        self.update('execute', status='complete')
        marker = self.root / 'controller-ready'
        proc = subprocess.Popen([sys.executable, str(ROOT / 'tests/controller_fixture.py'), '--resume', question['task_id'],
                                 '--answer', 'Pending answer survives interruption'],
                                env={**self.env, 'CEREBRO_SESSION_DIR': str(self.session), 'CONTROLLER_FIXTURE_READY': str(marker)},
                                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            self.wait_until(marker.is_file, proc)
            os.kill(proc.pid, signal.SIGKILL)
            proc.communicate(timeout=5)
            result = self.cli('execute', '--resume', question['task_id'])
            self.assertEqual(result['stage'], 'done')
            self.assertEqual(self.stages(), ['execute', 'execute', 'review'])
            turns = [item['params']['input'][0]['text'] for item in self.records() if item.get('method') == 'turn/start']
            self.assertIn('Pending answer survives interruption', turns[1])
        finally:
            if proc.poll() is None:
                os.kill(proc.pid, signal.SIGKILL)
                proc.communicate()

    def test_pending_answer_survives_failed_native_resume(self):
        self.update('execute', status='question')
        question = self.cli('execute', packet=self.packet)
        self.update('execute', status='complete', native_mode='resume-reject')
        failed = self.cli('answer', question['task_id'], 'Preserve this decision through retry', ok=False)
        self.assertEqual(failed['status'], 'failed')
        self.update('execute', native_mode='ok')
        self.cli('execute', '--resume', question['task_id'])
        turns = [item['params']['input'][0]['text'] for item in self.records() if item.get('method') == 'turn/start']
        self.assertIn('Preserve this decision through retry', turns[1])
        self.assertEqual(self.stages(), ['execute', 'execute', 'execute', 'review'])

    def test_review_receipt_recovery_without_repeat_review(self):
        proc = self.start_task(NATIVE_FIXTURE_DELAY='0.6')
        try:
            self.wait_until(lambda: self.log.exists() and sum(item.get('method') == 'turn/start' for item in self.records()) == 2, proc)
            os.kill(proc.pid, signal.SIGSTOP)
            state = self.running_task()
            self.assertEqual(state['stage'], 'review')
            receipt = Path(state['attempt']['log']).with_suffix('.exit')
            self.wait_until(receipt.is_file)
            os.kill(proc.pid, signal.SIGKILL)
            proc.communicate(timeout=5)
            task_id = next((self.session / 'tasks').iterdir()).name
            self.cli('execute', '--resume', task_id)
            self.assertEqual(self.stages(), ['execute', 'review'])
        finally:
            if proc.poll() is None:
                os.kill(proc.pid, signal.SIGKILL)
                proc.communicate()

    def test_failed_transport_receipt_does_not_advance_to_review(self):
        self.update('execute', native_mode='failure')
        proc = self.start_task(NATIVE_FIXTURE_DELAY='0.6')
        try:
            self.wait_until(lambda: self.log.exists() and any(item.get('method') == 'turn/start' for item in self.records()), proc)
            os.kill(proc.pid, signal.SIGSTOP)
            state = self.running_task()
            receipt = Path(state['attempt']['log']).with_suffix('.exit')
            self.wait_until(receipt.is_file)
            os.kill(proc.pid, signal.SIGKILL)
            proc.communicate(timeout=5)
            task_id = next((self.session / 'tasks').iterdir()).name
            result = self.cli('execute', '--resume', task_id, ok=False)
            self.assertEqual(result['stage'], 'execute')
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(self.stages(), ['execute'])
        finally:
            if proc.poll() is None:
                os.kill(proc.pid, signal.SIGKILL)
                proc.communicate()

    def test_restart_starts_fresh_native_child_and_preserves_diagnosis(self):
        proc = self.start_task(NATIVE_FIXTURE_DELAY='1')
        try:
            self.wait_until(lambda: self.log.exists() and any(item.get('method') == 'turn/start' for item in self.records()), proc)
            state = self.running_task()
            (self.repo / 'retained').write_text('keep this work')
            fifo = Path(state['attempt']['log']).with_suffix('.steer.fifo')
            self.cli('restart', str(fifo), 'Use the corrected original plan')
            stdout, stderr = proc.communicate(timeout=5)
            self.assertNotEqual(proc.returncode, 0, stderr)
            result = json.loads(stdout)
            self.assertEqual(result['status'], 'restarted')
            self.cli('execute', '--resume', result['task_id'])
            self.assertEqual(self.stages(), ['execute', 'execute', 'review'])
            self.assertFalse(any(item.get('method') == 'thread/resume' for item in self.records()))
            turns = [item['params']['input'][0]['text'] for item in self.records() if item.get('method') == 'turn/start']
            self.assertIn('Use the corrected original plan', turns[1])
            self.assertEqual((self.repo / 'retained').read_text(), 'keep this work')
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()

    def test_native_steering_is_available_without_jev(self):
        proc = self.start_task(NATIVE_FIXTURE_MODE='steer')
        try:
            self.wait_until(lambda: self.log.exists() and any(item.get('method') == 'turn/start' for item in self.records()), proc)
            state = self.running_task()
            fifo = Path(state['attempt']['log']).with_suffix('.steer.fifo')
            self.cli('steer', str(fifo), 'Keep the requested original plan focused')
            stdout, stderr = proc.communicate(timeout=10)
            self.assertEqual(proc.returncode, 0, stderr)
            self.assertEqual(json.loads(stdout)['stage'], 'done')
            turns = [item['params']['input'][0]['text'] for item in self.records() if item.get('method') == 'turn/start']
            self.assertIn('Keep the requested original plan focused', turns[1])
            self.assertEqual(self.stages(), ['execute', 'review'])
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()

    def test_explicit_empty_reviewer_keeps_native_defaults(self):
        for backend in ('codex', 'claude', 'pi'):
            self.env['CEREBRO_BACKEND'] = backend
            (self.session / 'metadata.json').write_text(json.dumps({'backend': backend}))
            self.packet['task'] = 'Empty reviewer ' + backend
            self.packet['models'] = {'reviewer': {'model': '', 'effort': ''}}
            self.log.write_text('')
            self.cli('execute', packet=self.packet)
            if backend == 'codex':
                starts = [item['params'] for item in self.records() if item.get('method') == 'thread/start']
                self.assertEqual(starts[0]['model'], 'implementation-model')
                self.assertNotIn('model', starts[1])
            else:
                argvs = [item['argv'] for item in self.records() if 'argv' in item]
                self.assertIn('implementation-model', argvs[0])
                self.assertNotIn('--model', argvs[1])

    def test_claude_native_endpoint_defaults_restore_before_review(self):
        self.env.update(CEREBRO_BACKEND='claude', CEREBRO_CLAUDE_BASE_URL='http://127.0.0.1:9',
                        ANTHROPIC_MODEL='supervisor-role-model', ANTHROPIC_DEFAULT_HAIKU_MODEL='supervisor-role-model',
                        CLAUDE_CODE_AUTO_COMPACT_WINDOW='parent-window',
                        CEREBRO_CLAUDE_NATIVE_MODEL_ENV=json.dumps({'ANTHROPIC_MODEL': 'native-default',
                            'ANTHROPIC_DEFAULT_HAIKU_MODEL': 'native-housekeeping', 'CLAUDE_CODE_AUTO_COMPACT_WINDOW': 'native-window'}))
        (self.session / 'metadata.json').write_text('{"backend":"claude"}')
        self.packet['models'] = {'reviewer': {'model': ''}}
        self.cli('execute', packet=self.packet)
        records = [item for item in self.records() if 'argv' in item]
        self.assertEqual(records[0]['anthropic_model'], 'implementation-model')
        self.assertEqual(records[1]['anthropic_model'], 'native-default')
        self.assertEqual(records[1]['haiku_model'], 'native-housekeeping')
        self.assertEqual(records[1]['context_window'], 'native-window')

    def test_wait_missing_values_fail_without_hanging(self):
        for flag in ('--after', '--note', '--disposition'):
            result = subprocess.run([CLI, 'wait', '123', flag], env=self.env, text=True, capture_output=True, timeout=2)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('missing value', result.stderr)


if __name__ == '__main__':
    unittest.main()
