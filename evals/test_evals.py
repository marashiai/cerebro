"""Offline checks for the eval's ground truth, scoring and isolation."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import run


class EvalTests(unittest.TestCase):
    def test_native_wrapper_assigns_effort_by_role(self):
        with tempfile.TemporaryDirectory(prefix="eval roles '") as directory:
            root = Path(directory)
            native = root / 'fake-native'
            native.write_text('#!' + sys.executable + '\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n')
            native.chmod(0o700)
            settings = {'codex': str(native), 'timeout': 30,
                        'efforts': {'implementation': 'low', 'review': 'medium', 'supervisor': 'high'},
                        'jev_api_key': 'offline-test', 'jev_model': 'jev-latest',
                        'jev_endpoint': 'https://unused.invalid', 'jev_confidence': 0.8}
            env, _ = run.setup(root, settings, False, 'Offline configuration test.')
            for role, group in run.ROLE_GROUPS.items():
                output = subprocess.check_output([env['CEREBRO_CODEX_CMD'], 'mcp', 'list', '--json'],
                                                 env=dict(env, CEREBRO_CHILD_ROLE=role), text=True)
                args = json.loads(output)
                self.assertEqual(args[:2], ['-c', 'model_reasoning_effort=' + json.dumps(settings['efforts'][group])])

    def test_role_models_and_arm_order(self):
        self.assertEqual(run.role_model('execute'), 'gpt-6-luna')
        self.assertEqual(run.role_model('apply-review'), 'gpt-6-luna')
        self.assertEqual(run.role_model('review'), 'gpt-6.1-sol')
        self.assertEqual(run.role_model('verify'), 'gpt-6.1-sol')
        self.assertEqual(run.arm_order(0, 0, 42), list(reversed(run.arm_order(0, 1, 42))))
        self.assertNotEqual(run.arm_order(0, 0, 42), run.arm_order(1, 0, 42))

    def test_errors_and_abstentions_cannot_become_correct_labels(self):
        expected = {'validity': 'unsupported', 'usefulness': 'low_value', 'action': 'dismiss'}
        self.assertFalse(run.score_decision({}, expected)['correct'])
        self.assertFalse(run.score_decision({'error': 'timeout'}, expected)['correct'])
        self.assertFalse(run.score_decision(dict(expected, validity='uncertain'), expected)['correct'])
        self.assertTrue(run.score_decision(expected, expected)['correct'])

    def test_classifier_failure_is_nonzero_even_when_parent_recovers(self):
        self.assertEqual(run.exit_status([{'correct': True}]), 0)
        self.assertEqual(run.exit_status([{'correct': False}]), 1)
        row = {'correct': True, 'jev': {'score': {'correct': False}}}
        self.assertEqual(run.exit_status([row]), 1)
        self.assertEqual(run.exit_status([row, {'correct': False, 'error': 'provider timeout'}]), 2)

    def test_paired_summary_preserves_regressions_and_errors(self):
        rows = [
            {'case': 'a', 'repeat': 0, 'arm': 'without_jev', 'correct': False},
            {'case': 'a', 'repeat': 0, 'arm': 'with_jev', 'correct': True},
            {'case': 'b', 'repeat': 0, 'arm': 'without_jev', 'correct': True},
            {'case': 'b', 'repeat': 0, 'arm': 'with_jev', 'correct': False, 'error': 'timeout'},
            {'case': 'c', 'repeat': 0, 'arm': 'without_jev', 'correct': True},
            {'case': 'c', 'repeat': 0, 'arm': 'with_jev', 'correct': True},
        ]
        summary = run.paired_summary(rows)
        self.assertEqual(summary['pairs'], 3)
        self.assertEqual((summary['improved'], summary['regressed'], summary['tied']), (1, 1, 1))
        self.assertEqual(summary['arms']['with_jev']['errors'], 1)
        self.assertEqual(summary['arms']['with_jev']['total'], 3)

    def test_missing_arm_is_not_counted_as_a_tie(self):
        summary = run.paired_summary([{'case': 'a', 'repeat': 0, 'arm': 'with_jev', 'correct': True}])
        self.assertEqual(summary['pairs'], 0)
        self.assertEqual(summary['incomplete_pairs'], 1)

    def test_ground_truth_is_not_in_model_packet(self):
        for case in run.load_cases():
            packet = run.public_case(case)
            self.assertNotIn('expected', packet)
            self.assertNotIn('rationale', packet)
            self.assertNotIn('id', packet)
            self.assertNotIn('expected', json.dumps(packet))

    def test_fixture_grader_checks_behavior_and_unrelated_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            run.seed_episode(repo)
            before = run.file_hashes(repo)
            self.assertFalse(run.grade_episode(repo, before)['functional_pass'])
            (repo / 'parser.py').write_text("import csv\n\ndef parse_row(text):\n    return next(csv.reader([text])) if text else []\n")
            self.assertTrue(run.grade_episode(repo, before)['correct'])
            (repo / 'billing.py').write_text('unrelated = True\n')
            result = run.grade_episode(repo, before)
            self.assertTrue(result['functional_pass'])
            self.assertFalse(result['scope_pass'])
            self.assertFalse(result['correct'])

    def test_changes_to_tests_do_not_fool_grader(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            run.seed_episode(repo)
            before = run.file_hashes(repo)
            (repo / 'test_parser.py').write_text('print("all tests pass")\n')
            result = run.grade_episode(repo, before)
            self.assertFalse(result['functional_pass'])
            self.assertFalse(result['scope_pass'])

    def test_failed_tool_and_failed_job_are_not_successful_steering(self):
        self.assertNotEqual(run.tool_response({'status': 'failed'})['exit_code'], 0)
        self.assertNotEqual(run.tool_response({'result': {'content': []}})['exit_code'], 0)
        response = {'exit_code': 0, 'job_exit_code': 2, 'state': 'completed', 'notice': {}}
        item = {'result': {'content': [{'type': 'text', 'text': json.dumps(response)}]}}
        self.assertEqual(run.tool_response(item)['job_exit_code'], 2)

    def test_unfinished_and_failed_jobs_remain_distinct_from_success(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory)
            jobs = session / 'detached-jobs'
            jobs.mkdir()
            for identifier, status in [('a', 'running'), ('b', '2'), ('c', '0')]:
                path = jobs / (identifier + '.status')
                path.write_text(status)
                (jobs / (identifier + '.json')).write_text(json.dumps({'id': identifier, 'status': str(path)}))
            results = {job['id']: job['exit_code'] for job in run.job_outcomes(session)}
            self.assertEqual(results, {'a': None, 'b': 2, 'c': 0})

    def test_matched_review_arms_get_identical_evidence(self):
        from review_check import context
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = run.load_cases()[0]
            seed = root / 'seed'
            run.seed_repo(seed, case['before'])
            states = []
            for arm in run.ARMS:
                import shutil
                repo = root / arm / 'repo'
                shutil.copytree(seed, repo)
                for name, text in case['after'].items():
                    (repo / name).write_text(text)
                session = root / arm / 'session'
                session.mkdir()
                (session / 'spec.md').write_text(case['requirements'])
                report = session / 'review.md'
                report.write_text(case['review'])
                criteria = session / 'criteria.md'
                criteria.write_text(case['requirements'])
                state = context(repo, 'HEAD', report, str(criteria), session)
                state.pop('repo')
                states.append(state)
            self.assertEqual(states[0], states[1])

if __name__ == '__main__':
    unittest.main()
