"""Task lifecycle over real Git and native CLI transport fixtures, without models."""
import base64
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
sys.path.insert(0, str(ROOT / 'lib' / 'python'))
from user_input import record, record_text, snapshot


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
        record_text(self.session, 'Original user goal. Preserve None distinctly from False.',
                    source='codex', native_id='NATIVE-USER', turn_id='TURN-USER')
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
                    self.assertIn('Preserve None distinctly from False', prompt)
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
        first_prompt = next((self.session / 'children').glob('execute-*.prompt'))
        first_prompt_text = first_prompt.read_text()
        record_text(self.session, 'Clarification: keep None and False distinct in stored values.',
                    source='codex', native_id='NATIVE-USER', turn_id='TURN-CLARIFICATION')
        self.update('execute', status='complete')
        self.cli('answer', result['task_id'], 'Use the requested behavior')
        self.assertEqual(self.stages(), ['execute', 'execute', 'review'])
        resume = [item for item in self.records() if item.get('method') == 'thread/resume']
        self.assertEqual(len(resume), 1)
        self.assertEqual(resume[0]['params']['threadId'], 'NATIVE-CHILD-1')
        turns = [item['params']['input'][0]['text'] for item in self.records() if item.get('method') == 'turn/start']
        self.assertIn('Clarification: keep None and False distinct', turns[1])
        self.assertEqual(first_prompt.read_text(), first_prompt_text)
        resumed_prompts = [path for path in first_prompt.parent.glob('execute-*.prompt') if path != first_prompt]
        self.assertTrue(any('Clarification: keep None and False' in path.read_text() for path in resumed_prompts))
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

    def test_rejected_handoff_resume_explains_rejection_and_accepts_restated_criteria(self):
        self.update('execute', status='malformed')
        result = self.cli('execute', packet=self.packet, ok=False)
        self.assertEqual(result['status'], 'unfinished')
        self.update('execute', status='complete', criterion_prefix='Restated: ')
        result = self.cli('execute', '--resume', result['task_id'])
        self.assertEqual(result['stage'], 'done')
        self.assertEqual(result['implementation']['criteria'][0]['criterion'], 'Restated: Original acceptance')
        turns = [item['params']['input'][0]['text'] for item in self.records() if item.get('method') == 'turn/start']
        self.assertNotIn('rejected your previous closing JSON', turns[0])
        self.assertIn('rejected your previous closing JSON: invalid execute handoff', turns[1])
        self.assertEqual(self.stages(), ['execute', 'execute', 'review'])

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

    def test_review_findings_state_their_basis(self):
        from task_lifecycle import handoff
        finding = {'id': 'F1', 'severity': 'low', 'file': 'file', 'line': 1, 'problem': 'p',
                   'evidence': 'e', 'requested_change': 'c'}
        report = {'status': 'complete', 'summary': 's', 'criteria': [
            {'criterion': 'Original acceptance', 'result': 'passed', 'evidence': 'e'}]}
        path = self.root / 'review.reply'
        for extra, error in (({}, 'requires basis'), ({'basis': 'requirement'}, 'quote the stated requirement'),
                             ({'basis': 'requirement', 'requirement': ' '}, 'quote the stated requirement'),
                             ({'basis': 'robustness'}, None),
                             ({'basis': 'requirement', 'requirement': 'Original acceptance'}, None)):
            with self.subTest(extra=extra):
                path.write_text(json.dumps({**report, 'findings': [{**finding, **extra}]}))
                if error:
                    with self.assertRaisesRegex(ValueError, error):
                        handoff(path, 'review', ['Original acceptance'])
                else:
                    self.assertEqual(handoff(path, 'review', ['Original acceptance'])['findings'][0]['basis'],
                                     extra['basis'])

    def test_unverified_criteria_do_not_become_passed(self):
        self.update('execute', criterion_result='unverified')
        result = self.cli('execute', packet=self.packet)
        self.assertEqual(result['implementation']['criteria'][0]['result'], 'unverified')
        self.assertEqual(self.stages(), ['execute', 'review'])

    def test_turn_end_with_unfinished_tool_delivers_report_and_receipt(self):
        self.update('execute', native_mode='abandoned')
        self.update('review', native_mode='abandoned', findings=[{
            'id': 'F1', 'severity': 'medium', 'basis': 'robustness', 'file': 'file', 'line': 1, 'problem': 'Original finding',
            'evidence': 'Replacement check output', 'requested_change': 'Fix it'}])
        for backend in ('codex', 'claude', 'pi'):
            with self.subTest(backend=backend):
                self.env['CEREBRO_BACKEND'] = backend
                (self.session / 'metadata.json').write_text(json.dumps({'backend': backend}))
                self.packet['task'] = 'Abandoned check for ' + backend
                result = self.cli('execute', packet=self.packet)
                self.assertEqual((result['stage'], result['status']), ('done', 'complete'))
                self.assertEqual(result['review']['findings'][0]['problem'], 'Original finding')
                for name in ('implementation', 'review'):
                    tools = result[name + '_unfinished_tools']
                    self.assertEqual([(item['id'], item['command']) for item in tools],
                                     [('check-command', 'python3 hanging_check.py')])
                review_prompt = Path(result['review_path']).with_suffix('.prompt').read_text()
                self.assertIn('never reached the implementor', review_prompt)
                self.assertIn('python3 hanging_check.py', review_prompt)
                resumed = self.cli('execute', '--resume', result['task_id'])
                self.assertEqual(resumed['review_unfinished_tools'], result['review_unfinished_tools'])

    def test_unfinished_tool_from_question_attempt_survives_resume(self):
        self.update('execute', native_mode='abandoned', status='question')
        result = self.cli('execute', packet=self.packet)
        self.assertEqual(result['status'], 'question')
        self.update('execute', native_mode='ok', status='complete')
        result = self.cli('answer', result['task_id'], 'Use the requested behavior')
        self.assertEqual(result['stage'], 'done')
        self.assertEqual([item['command'] for item in result['implementation_unfinished_tools']],
                         ['python3 hanging_check.py'])
        self.assertNotIn('review_unfinished_tools', result)

    def test_correction_reuses_checkout_and_reviews_everything_unreviewed(self):
        self.packet['worktree'] = True
        self.update('review', findings=[{'id': 'F1', 'severity': 'low', 'basis': 'requirement', 'requirement': 'Original acceptance', 'file': 'file', 'line': 1,
                                         'problem': 'Original finding', 'evidence': 'observed',
                                         'requested_change': 'Fix it'}])
        first = self.cli('execute', packet=self.packet)
        checkout = Path(first['workspace'])
        (checkout / 'earlier.txt').write_text('earlier work\n')
        correction = {**self.packet, 'task': 'Fix accepted finding F1 only', 'correction_of': first['task_id']}
        del correction['worktree']
        self.update('execute', native_mode='edit')
        self.update('review', findings=[])
        result = self.cli('execute', packet=correction)
        self.assertEqual((result['stage'], result['workspace']), ('done', str(checkout)))
        prompt = Path(result['review_path']).with_suffix('.prompt').read_text()
        self.assertIn('Original finding', prompt)
        before, after = prompt.split('Correction diff: git diff ')[1].split()[:2]
        changed = subprocess.run(['git', '-C', str(checkout), 'diff', '--name-only', before, after],
                                 text=True, capture_output=True, check=True).stdout.split()
        self.assertEqual(changed, ['corrected.txt', 'earlier.txt'])
        self.assertIn('git diff ' + before + ' ' + after, prompt)
        follow_up = {**correction, 'task': 'Second correction', 'correction_of': result['task_id']}
        self.update('execute', native_mode='ok')
        second = self.cli('execute', packet=follow_up)
        prompt = Path(second['review_path']).with_suffix('.prompt').read_text()
        self.assertIn('Correction diff: git diff ' + after + ' ', prompt)
        self.assertEqual(subprocess.run(['git', '-C', str(checkout), 'status', '--porcelain'], text=True,
                                        capture_output=True, check=True).stdout.split(),
                         ['??', 'corrected.txt', '??', 'earlier.txt'])
        execute_prompt = Path(result['implementation_path']).with_suffix('.prompt').read_text()
        self.assertNotIn('Correction diff', execute_prompt)

    def test_correction_requires_reviewed_task_and_its_checkout(self):
        unknown = {**self.packet, 'correction_of': 'missing'}
        self.assertIn('completed, reviewed task', self.cli('execute', packet=unknown, ok=False).stderr)
        self.update('execute', status='question')
        questioned = self.cli('execute', packet={**self.packet, 'task': 'Questioned'})
        packet = {**self.packet, 'correction_of': questioned['task_id']}
        self.assertIn('completed, reviewed task', self.cli('execute', packet=packet, ok=False).stderr)
        self.update('execute', status='complete')
        first = self.cli('execute', packet=self.packet)
        for change, message in (({'worktree': True}, 'omit worktree and branch'),
                                ({'branch': 'other'}, 'omit worktree and branch'),
                                ({'base': 'HEAD'}, 'keeps its reviewed task repo and base')):
            packet = {**self.packet, 'correction_of': first['task_id'], **change}
            self.assertIn(message, self.cli('execute', packet=packet, ok=False).stderr)
        subprocess.run(['git', '-C', str(self.repo), 'switch', '-qc', 'unrelated'], check=True)
        packet = {**self.packet, 'correction_of': first['task_id']}
        self.assertIn('has left branch main', self.cli('execute', packet=packet, ok=False).stderr)

    def test_tool_completion_queued_behind_turn_end_is_not_unfinished(self):
        self.update('review', native_mode='joined')
        result = self.cli('execute', packet=self.packet)
        self.assertEqual(result['stage'], 'done')
        self.assertNotIn('review_unfinished_tools', result)
        self.assertFalse(Path(result['review_path']).with_suffix('.unfinished.json').exists())

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
            late = record_text(self.session, 'After review receipt: a new user requirement.',
                               source='codex', native_id='NATIVE-USER', turn_id='TURN-AFTER-REVIEW-RECEIPT')
            result = self.cli('execute', '--resume', task_id)
            self.assertEqual(self.stages(), ['execute', 'review'])
            self.assertIn(late['id'], result['pending_user_input_ids'])
            saved = json.loads((self.session / 'tasks' / task_id / 'task.json').read_text())
            self.assertNotIn(late['id'], [item['id'] for item in saved['user_inputs']])
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
            steers = [item for item in self.records() if item.get('method') == 'turn/steer']
            self.assertEqual(len(steers), 1)
            self.assertIn('Keep the requested original plan focused', steers[0]['params']['input'][0]['text'])
            self.assertEqual(len([item for item in self.records() if item.get('method') == 'turn/start']), 2)
            self.assertEqual(self.stages(), ['execute', 'review'])
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()

    def test_interrupt_stops_the_running_turn_and_starts_the_message(self):
        proc = self.start_task(NATIVE_FIXTURE_MODE='steer')
        try:
            self.wait_until(lambda: self.log.exists() and any(item.get('method') == 'turn/start' for item in self.records()), proc)
            state = self.running_task()
            fifo = Path(state['attempt']['log']).with_suffix('.steer.fifo')
            self.cli('steer', '--interrupt', str(fifo), 'Stop and keep the original plan focused')
            stdout, stderr = proc.communicate(timeout=10)
            self.assertEqual(proc.returncode, 0, stderr)
            self.assertEqual(json.loads(stdout)['stage'], 'done')
            methods = [item.get('method') for item in self.records() if item.get('method', '').startswith('turn/')]
            self.assertEqual(methods, ['turn/start', 'turn/interrupt', 'turn/start', 'turn/start'])
            turns = [item['params']['input'][0]['text'] for item in self.records() if item.get('method') == 'turn/start']
            self.assertIn('Stop and keep the original plan focused', turns[1])
            self.assertIn('(interrupt)', fifo.with_name(fifo.name.replace('.steer.fifo', '.steering.md')).read_text())
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()

    def test_correct_interrupt_writes_an_interrupt_and_steer_takes_one_message(self):
        fifo = self.session / 'children' / 'execute-urgent.steer.fifo'
        os.mkfifo(fifo)
        reader = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
        self.addCleanup(os.close, reader)
        job_id = self.concern_job({'steering_pipe': str(fifo)})
        self.cli('wait', job_id, '--after', '1', '--disposition', 'correct', '--note', 'Stop the full suite', '--interrupt')
        self.assertTrue(os.read(reader, 65536).startswith(b'I '))
        misplaced = self.cli('steer', str(fifo), '--interrupt', 'Stop', ok=False)
        self.assertIn('usage', misplaced.stderr)

    def test_interrupt_requires_correct_disposition(self):
        rejected = self.cli('wait', '0d5a8f3e-1111-4c2e-9a6b-2b1f9e3c7d10', '--after', '1',
                            '--disposition', 'continue', '--note', 'Fine', '--interrupt', ok=False)
        self.assertIn('--interrupt applies only to --disposition correct', rejected.stderr)

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

    def concern_job(self, notice):
        job_id = '0d5a8f3e-1111-4c2e-9a6b-2b1f9e3c7d10'
        jobs = self.session / 'detached-jobs'
        jobs.mkdir(exist_ok=True)
        status = jobs / (job_id + '.status')
        status.write_text('0\n')
        for suffix in ('.out', '.result'):
            status.with_suffix(suffix).write_text('')
        Path(str(status) + '.updates.json').write_text(json.dumps(
            {'sequence': 1, 'acknowledged': 0, 'notices': [{'sequence': 1, 'notice': notice}]}))
        (jobs / (job_id + '.json')).write_text(json.dumps({'id': job_id, 'status': str(status),
            'output': str(status.with_suffix('.out')), 'result': str(status.with_suffix('.result'))}))
        return job_id

    def decisions(self):
        path = self.session / 'decisions.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_correct_disposition_steers_the_cited_child_before_recording(self):
        fifo = self.session / 'children' / 'execute-concern.steer.fifo'
        os.mkfifo(fifo)
        reader = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
        self.addCleanup(os.close, reader)
        job_id = self.concern_job({'classification': {'reason': 'apparent_mistake'}, 'steering_pipe': str(fifo)})
        self.env['CEREBRO_ROLE'] = 'supervisor'
        result = self.cli('wait', job_id, '--after', '1', '--disposition', 'correct',
                          '--note', 'Fix the failing regression test before continuing')
        self.assertEqual(result['state'], 'completed')
        prefix, encoded = os.read(reader, 65536).decode().strip().split(' ', 1)
        delivered = base64.b64decode(encoded).decode()
        self.assertEqual(prefix, 'S')
        self.assertTrue(delivered.startswith('[supervisor] '))
        self.assertIn('Fix the failing regression test before continuing', delivered)
        self.assertEqual([item['disposition'] for item in self.decisions()], ['correct'])

    def test_invalid_correct_decision_delivers_nothing(self):
        fifo = self.session / 'children' / 'execute-invalid.steer.fifo'
        os.mkfifo(fifo)
        reader = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
        self.addCleanup(os.close, reader)
        job_id = self.concern_job({'steering_pipe': str(fifo)})
        status = self.session / 'detached-jobs' / (job_id + '.status')
        for target, after in ((str(status), '1'), (job_id, '0')):
            with self.subTest(target=target, after=after):
                failed = self.cli('wait', target, '--after', after, '--disposition', 'correct', '--note', 'Fix it', ok=False)
                self.assertIn('requires a job ID, --after', failed.stderr)
        self.assertEqual(os.read(reader, 65536), b'', 'a rejected decision delivered its note')
        self.assertEqual(self.decisions(), [])

    def test_correct_disposition_without_live_child_records_nothing(self):
        for notice in ({'observer_failure': {'error': 'invalid classification'}},
                       {'steering_pipe': str(self.session / 'children' / 'finished.steer.fifo')}):
            with self.subTest(notice=notice):
                job_id = self.concern_job(notice)
                failed = self.cli('wait', job_id, '--after', '1', '--disposition', 'correct',
                                  '--note', 'Fix it', ok=False)
                self.assertIn('wait' if 'observer_failure' in notice else 'steer', failed.stderr)
                self.assertEqual(self.decisions(), [])

    def test_wait_missing_values_fail_without_hanging(self):
        for flag in ('--after', '--note', '--disposition'):
            result = subprocess.run([CLI, 'wait', '123', flag], env=self.env, text=True, capture_output=True, timeout=2)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('missing value', result.stderr)

    def test_user_input_is_ordered_exact_and_preserves_same_turn_messages(self):
        first = snapshot(self.session)[0]
        duplicate = record_text(self.session, 'Original user goal. Preserve None distinctly from False.',
                                source='codex', native_id='NATIVE-USER', turn_id='TURN-USER')
        self.assertNotEqual(first['id'], duplicate['id'])
        intentional_repeat = record_text(self.session, 'Original user goal. Preserve None distinctly from False.',
                                         source='codex', native_id='NATIVE-USER', turn_id='TURN-USER')
        self.assertNotEqual(duplicate['id'], intentional_repeat['id'])
        record_text(self.session, '  same text  ', source='codex', native_id='NATIVE-USER', turn_id='TURN-2')
        repeated = record_text(self.session, '  same text  ', source='codex', native_id='NATIVE-USER', turn_id='TURN-3')
        entries = snapshot(self.session)
        self.assertEqual(entries[-2]['content'][0]['text'], '  same text  ')
        self.assertNotEqual(entries[-2]['id'], repeated['id'])
        same_turn = record_text(self.session, 'A second message in one active turn.',
                                source='codex', native_id='NATIVE-USER', turn_id='TURN-USER')
        self.assertEqual(same_turn['turn_id'], first['turn_id'])
        self.assertEqual(snapshot(self.session)[-1]['content'][0]['text'], 'A second message in one active turn.')
        with self.assertRaisesRegex(ValueError, 'non-whitespace'):
            record_text(self.session, ' \n ', source='codex', native_id='NATIVE-USER', turn_id='TURN-BLANK')
        attachment = record(self.session, [{'type': 'resource', 'resource': {'uri': 'resource://input/1'}}],
                            source='acp', native_id='NATIVE-ACP', turn_id='TURN-4')
        self.assertEqual(attachment['content'][0]['resource']['uri'], 'resource://input/1')

    def test_new_original_input_changes_task_identity_even_when_plan_is_unchanged(self):
        first = self.cli('execute', packet=self.packet)
        record_text(self.session, 'An additional user requirement omitted from the plan.',
                    source='codex', native_id='NATIVE-USER', turn_id='TURN-AMENDMENT')
        second = self.cli('execute', packet=self.packet)
        self.assertNotEqual(first['task_id'], second['task_id'])
        turns = [item['params']['input'][0]['text'] for item in self.records() if item.get('method') == 'turn/start']
        self.assertIn('An additional user requirement omitted from the plan.', turns[2])

    def test_input_captured_during_implementation_reaches_new_review_stage(self):
        proc = self.start_task(NATIVE_FIXTURE_DELAY='0.6')
        try:
            self.wait_until(lambda: self.log.exists() and
                            any(item.get('method') == 'turn/start' for item in self.records()), proc)
            record_text(self.session, 'During execution: keep absent values separate from false values.',
                        source='codex', native_id='NATIVE-USER', turn_id='TURN-DURING-EXECUTE')
            stdout, stderr = proc.communicate(timeout=10)
            self.assertEqual(proc.returncode, 0, stderr)
            result = json.loads(stdout)
            self.assertEqual(result['stage'], 'done')
            turns = [item['params']['input'][0]['text'] for item in self.records() if item.get('method') == 'turn/start']
            self.assertIn('During execution: keep absent values separate', turns[1])
            state = json.loads((self.session / 'tasks' / result['task_id'] / 'task.json').read_text())
            self.assertEqual(state['user_inputs'][-1]['turn_id'], 'TURN-DURING-EXECUTE')
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()

    def test_completed_resume_does_not_change_evaluated_input_snapshot(self):
        result = self.cli('execute', packet=self.packet)
        path = self.session / 'tasks' / result['task_id'] / 'task.json'
        original = json.loads(path.read_text())['user_inputs']
        record_text(self.session, 'A later session input belongs to a new task.',
                    source='codex', native_id='NATIVE-USER', turn_id='TURN-LATER')
        resumed = self.cli('execute', '--resume', result['task_id'])
        self.assertEqual(resumed['user_input_ids'], [item['id'] for item in original])
        self.assertEqual(json.loads(path.read_text())['user_inputs'], original)

    def test_input_during_review_is_pending_and_does_not_rewrite_evaluated_snapshot(self):
        proc = self.start_task(NATIVE_FIXTURE_DELAY='0.6')
        try:
            self.wait_until(lambda: self.log.exists() and
                            sum(item.get('method') == 'turn/start' for item in self.records()) == 2, proc)
            task_file = max((self.session / 'tasks').glob('*/task.json'), key=lambda path: path.stat().st_mtime_ns)
            reviewed_inputs = json.loads(task_file.read_text())['user_inputs']
            late = record_text(self.session, 'During review: add a meaningful omitted requirement.',
                               source='codex', native_id='NATIVE-USER', turn_id='TURN-DURING-REVIEW')
            stdout, stderr = proc.communicate(timeout=10)
            self.assertEqual(proc.returncode, 0, stderr)
            result = json.loads(stdout)
            self.assertIn(late['id'], result['pending_user_input_ids'])
            self.assertEqual(result['user_input_ids'], [item['id'] for item in reviewed_inputs])
            self.assertEqual(json.loads(task_file.read_text())['user_inputs'], reviewed_inputs)
            review_prompt = [item['params']['input'][0]['text'] for item in self.records()
                             if item.get('method') == 'turn/start'][1]
            self.assertNotIn('During review: add a meaningful omitted requirement.', review_prompt)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()

    def test_missing_original_input_capture_fails_before_launch(self):
        (self.session / 'user-inputs.json').unlink()
        result = self.cli('execute', packet=self.packet, ok=False)
        self.assertIn('no original user input has been captured', result.stderr)
        self.assertFalse(self.log.exists())


if __name__ == '__main__':
    unittest.main()
