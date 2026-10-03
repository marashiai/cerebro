"""Offline checks for the eval's ground truth, scoring and isolation."""

import argparse
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import run
import runtime
from model_config import add_arguments, cli_overrides, load_config, resolve_config


def config_native(root):
    path = root / 'configuration-native'
    path.write_text('#!' + sys.executable + '\n' + '''import json,os,sys
from pathlib import Path
args=sys.argv[1:]
if '--version' in args:
    print('configuration-test-native')
elif 'mcp' in args:
    print('[]')
elif 'exec' in args:
    effort=json.loads(next(arg.split('=',1)[1] for arg in args if arg.startswith('model_reasoning_effort=')))
    Path(os.environ['CEREBRO_EVAL_DIR'],'parent-request.json').write_text(json.dumps({
        'model':args[args.index('--model')+1],'effort':effort}))
    Path(args[args.index('--output-last-message')+1]).write_text('configured parent')
    print(json.dumps({'type':'turn.completed','usage':{}}))
elif 'app-server' in args:
    def emit(value):
        print(json.dumps(value),flush=True)
    effort=json.loads(next(arg.split('=',1)[1] for arg in args if arg.startswith('model_reasoning_effort=')))
    for line in sys.stdin:
        request=json.loads(line);method=request.get('method');params=request.get('params',{})
        if method=='initialize':
            emit({'id':request['id'],'result':{}})
        elif method=='thread/start':
            Path(os.environ['CEREBRO_EVAL_DIR'],'child-request.json').write_text(json.dumps(params))
            emit({'id':request['id'],'result':{'thread':{'id':'configuration-child'},'model':params['model'],'reasoningEffort':effort}})
        elif method=='turn/start':
            emit({'id':request['id'],'result':{'turn':{'id':'configuration-turn'}}})
            emit({'method':'item/completed','params':{'item':{'id':'answer','type':'agentMessage','text':'configuration recorded'}}})
            emit({'method':'turn/completed','params':{'turn':{'id':'configuration-turn','status':'completed'}}})
''')
    path.chmod(0o700)
    return path


class EvalTests(unittest.TestCase):
    def test_native_wrapper_assigns_effort_by_role(self):
        with tempfile.TemporaryDirectory(prefix="eval roles '") as directory:
            root = Path(directory)
            native = root / 'fake-native'
            native.write_text('#!' + sys.executable + '\nimport json,sys\nprint(json.dumps(sys.argv[1:]))\n')
            native.chmod(0o700)
            settings = {'codex': str(native), 'timeout': 30,
                        'models': {'implementation': 'future-implementation', 'review': 'future-review',
                                   'supervisor': 'future-supervisor', 'baseline': 'future-baseline'},
                        'efforts': {'implementation': 'minimal', 'review': 'xhigh', 'supervisor': 'ultra', 'baseline': 'none'},
                        'jev_api_key': 'offline-test', 'jev_model': 'jev-latest',
                        'jev_endpoint': 'https://unused.invalid', 'jev_confidence': 0.8}
            env, session = run.setup(root, settings, False, 'Offline configuration test.')
            for name, role in [('CEREBRO_MODEL', 'implementation'), ('CEREBRO_REVIEW_MODEL', 'review'),
                               ('CEREBRO_SUPERVISOR_MODEL', 'supervisor')]:
                self.assertEqual(env[name], settings['models'][role])
                config = json.loads((session / 'tools-supervisor.json').read_text())
                self.assertEqual(config['mcpServers']['cerebro']['env'][name], settings['models'][role])
            for role, group in run.ROLE_GROUPS.items():
                output = subprocess.check_output([env['CEREBRO_CODEX_CMD'], 'mcp', 'list', '--json'],
                                                 env=dict(env, CEREBRO_CHILD_ROLE=role), text=True)
                args = json.loads(output)
                self.assertEqual(args[:2], ['-c', 'model_reasoning_effort=' + json.dumps(settings['efforts'][group])])

    def test_role_models_and_arm_order(self):
        models = resolve_config({})['models']
        self.assertEqual(run.role_model('execute', models), 'gpt-6-luna')
        self.assertEqual(run.role_model('apply-review', models), 'gpt-6-luna')
        self.assertEqual(run.role_model('review', models), 'gpt-5.6-terra')
        self.assertEqual(run.role_model('verify', models), 'gpt-5.6-terra')
        self.assertEqual(run.role_model('baseline', models), 'gpt-5.6-terra')
        self.assertEqual(run.arm_order(0, 0, 42), list(reversed(run.arm_order(0, 1, 42))))
        self.assertNotEqual(run.arm_order(0, 0, 42), run.arm_order(1, 0, 42))

    def test_model_configuration_precedence_and_dynamic_baseline_inheritance(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'models.json'
            document = {'defaults': {'models': {'supervisor': 'default-parent', 'review': 'default-review'},
                                     'efforts': {'supervisor': 'high'}},
                        'cases': {'one': {'models': {'supervisor': 'case-parent', 'review': 'case-review'},
                                          'efforts': {'implementation': 'minimal'}}}}
            path.write_text(json.dumps(document))
            parser = argparse.ArgumentParser()
            add_arguments(parser)
            args = parser.parse_args(['--config', str(path), '--review-model', 'future-model:release',
                                      '--supervisor-effort', 'ultra'])
            config = load_config(args.config, {'one', 'two'})
            overrides = cli_overrides(args)
            one = resolve_config(config, 'one', overrides)
            two = resolve_config(config, 'two', overrides)
            self.assertEqual(one['models'], {'implementation': 'gpt-6-luna', 'review': 'future-model:release',
                                              'supervisor': 'case-parent', 'baseline': 'case-parent'})
            self.assertEqual(one['efforts'], {'implementation': 'minimal', 'review': 'medium',
                                              'supervisor': 'ultra', 'baseline': 'ultra'})
            self.assertEqual(two['models']['supervisor'], 'default-parent')
            self.assertEqual(two['efforts']['implementation'], 'low')
            self.assertEqual(resolve_config(config, 'one')['models']['review'], 'case-review')
            self.assertEqual(resolve_config(config, 'two')['models']['review'], 'default-review')
            self.assertEqual(config, document)
            one['models']['implementation'] = 'changed-in-one-trial'
            self.assertEqual(two['models']['implementation'], 'gpt-6-luna')
            self.assertEqual(resolve_config({})['models']['implementation'], 'gpt-6-luna')
            config['defaults']['models']['baseline'] = 'configured-baseline'
            config['defaults']['efforts']['baseline'] = 'future-effort'
            baseline = resolve_config(config, 'one', overrides)
            self.assertEqual(baseline['models']['baseline'], 'configured-baseline')
            self.assertEqual(baseline['efforts']['baseline'], 'future-effort')
            args = parser.parse_args(['--baseline-model', 'cli-baseline', '--baseline-effort', 'none'])
            baseline = resolve_config(config, 'one', cli_overrides(args))
            self.assertEqual(baseline['models']['baseline'], 'cli-baseline')
            self.assertEqual(baseline['efforts']['baseline'], 'none')

    def test_model_configuration_rejects_unknown_fields_cases_roles_and_empty_values(self):
        invalid = [[], {'unknown': {}}, {'defaults': {'model': {}}},
                   {'defaults': {'models': {'worker': 'model'}}}, {'cases': {'missing-case': {}}},
                   {'cases': {'one': {'unknown': {}}}}, {'defaults': {'models': {'review': ''}}},
                   {'defaults': {'models': {'review': None}}}, {'defaults': {'efforts': {'review': '  '}}},
                   {'defaults': {'efforts': {'review': 1}}}, {'defaults': {'efforts': []}}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'models.json'
            for document in invalid:
                with self.subTest(document=document):
                    path.write_text(json.dumps(document))
                    with self.assertRaises(ValueError):
                        load_config(path, {'one'})
            path.write_text(json.dumps({'defaults': {'models': {'review': 'unreleased-model-v99'},
                                                       'efforts': {'review': 'future-effort'}}}))
            config = load_config(path, {'one'})
            self.assertEqual(resolve_config(config)['models']['review'], 'unreleased-model-v99')
            self.assertEqual(resolve_config(config)['efforts']['review'], 'future-effort')

    def test_resolved_models_reach_parent_and_child_requests_and_grade_against_that_case(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            native = config_native(root)
            repo = root / 'repo'
            run.seed_episode(repo)
            roles = resolve_config({'defaults': {'models': {'implementation': 'future-worker', 'supervisor': 'future-parent'},
                                                 'efforts': {'implementation': 'xhigh', 'supervisor': 'ultra'}}})
            settings = {'codex': str(native), 'timeout': 30, **roles, 'jev_api_key': 'offline-test',
                        'jev_model': 'jev-latest', 'jev_endpoint': 'https://unused.invalid', 'jev_confidence': 0.8}
            env, session = run.setup(root, settings, False, 'Configuration probe only.')
            try:
                parent = runtime.codex(root, env, 'Record configured arguments.', settings)
                self.assertEqual(parent['answer'], 'configured parent')
                self.assertEqual(json.loads((root / 'parent-request.json').read_text()),
                                 {'model': 'future-parent', 'effort': 'ultra'})
                result = run.command(root, env, ['execute', str(repo), '--prompt', 'Record configuration only.',
                                                '--no-watch'], 30)
                self.assertEqual(result['exit_code'], 0, result['text'])
                self.assertEqual(json.loads((root / 'child-request.json').read_text())['model'], 'future-worker')
                criteria = session / 'plans/criteria.md'
                criteria.write_text('Configuration probe only.')
                args = (root, session, 'without_jev', repo, run.git(repo, 'rev-parse', 'HEAD'), criteria)
                violations = run.episode_metrics(*args, settings)['protocol_violations']
                self.assertNotIn('native child used an unexpected model', violations)
                self.assertNotIn('native child used an unexpected reasoning effort', violations)
                wrong_model = {**settings, 'models': {**settings['models'], 'implementation': 'wrong-worker'}}
                self.assertIn('native child used an unexpected model',
                              run.episode_metrics(*args, wrong_model)['protocol_violations'])
                wrong_effort = {**settings, 'efforts': {**settings['efforts'], 'implementation': 'low'}}
                self.assertIn('native child used an unexpected reasoning effort',
                              run.episode_metrics(*args, wrong_effort)['protocol_violations'])
            finally:
                runtime.cleanup(env)

    def test_runner_persists_resolved_case_settings_without_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            native = config_native(root)
            config = root / 'model-config.json'
            config.write_text(json.dumps({'cases': {'provider-token-limit': {
                'models': {'implementation': 'case-worker'}, 'efforts': {'supervisor': 'high'}}}}))
            out = root / 'results'
            argv = ['run.py', '--suite', 'failures', '--case', 'provider-token-limit', '--config', str(config),
                    '--review-model', 'cli-review', '--out', str(out)]
            with patch.object(sys, 'argv', argv), patch.dict(os.environ, {
                    'CEREBRO_HOME': str(root / 'home'), 'CEREBRO_CODEX_CMD': str(native),
                    'CEREBRO_JEV_API_KEY': 'private-test-credential'}), patch('probes.trial') as trial, redirect_stdout(io.StringIO()):
                trial.return_value = {'correct': True, 'checks': {'configuration_probe': True}}
                self.assertEqual(run.main(), 0)
            resolved = trial.call_args.args[2]
            self.assertEqual(resolved['models']['implementation'], 'case-worker')
            self.assertEqual(resolved['models']['review'], 'cli-review')
            self.assertEqual(resolved['efforts']['baseline'], 'high')
            manifest = json.loads((out / 'manifest.json').read_text())
            expected = manifest['resolved_case_settings']['provider-token-limit']
            actual = json.loads((out / 'r01-c01/protocol/settings.json').read_text())
            self.assertEqual(actual, expected)
            row = json.loads((out / 'r01-c01/protocol/result.json').read_text())
            self.assertEqual(row['settings'], expected)
            self.assertNotIn('jev_api_key', actual)
            self.assertNotIn('private-test-credential', json.dumps(manifest))

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
