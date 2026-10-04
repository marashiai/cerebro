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
from runtime import file_hashes
from fixtures import grade_episode
from observations import condition_metrics, job_outcomes, tool_response
import runtime
from model_config import add_arguments, cli_overrides, load_config, resolve_config


def config_native(root):
    path = root / 'configuration-native'
    path.write_text('#!' + sys.executable + '\n' + '''import json,os,sys
from pathlib import Path
args=sys.argv[1:]
if '--version' in args:
    print('codex-cli 0.0.0-eval-fixture')
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
    def test_configuration_has_no_model_catalogue_and_precedence_is_explicit(self):
        parser = argparse.ArgumentParser()
        add_arguments(parser)
        args = parser.parse_args(['--review-model', 'provider/future-model:release', '--supervisor-effort', 'ultra'])
        config = {'defaults': {'models': {'supervisor': 'parent', 'implementation': 'worker'},
                               'efforts': {'supervisor': 'high'}},
                  'cases': {'one': {'models': {'review': 'case-review'}, 'efforts': {'implementation': 'minimal'}}}}
        one = resolve_config(config, 'one', cli_overrides(args))
        self.assertEqual(one['models'], {'implementation': 'worker', 'review': 'provider/future-model:release',
                                         'supervisor': 'parent'})
        self.assertEqual(one['efforts'], {'implementation': 'minimal', 'review': None, 'supervisor': 'ultra'})
        self.assertTrue(all(value is None for values in resolve_config({}).values() for value in values.values()))
        one['models']['implementation'] = 'changed'
        self.assertEqual(resolve_config(config)['models']['implementation'], 'worker')

    def test_configuration_rejects_unknown_fields_and_empty_choices(self):
        invalid = [[], {'unknown': {}}, {'defaults': {'model': {}}},
                   {'defaults': {'models': {'worker': 'model'}}}, {'cases': {'missing': {}}},
                   {'defaults': {'models': {'review': ''}}}, {'defaults': {'efforts': {'review': None}}}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            for document in invalid:
                path.write_text(json.dumps(document))
                with self.subTest(document=document), self.assertRaises(ValueError):
                    load_config(path, {'one'})

    def test_actual_native_parent_receives_arbitrary_model_effort_and_auth_copy_is_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            run.seed_episode(root / 'repo')
            native = config_native(root)
            roles = resolve_config({'defaults': {'models': {'implementation': 'future-worker', 'review': 'future-review',
                                                            'supervisor': 'future-parent'},
                                                'efforts': {'implementation': 'minimal', 'review': 'xhigh',
                                                            'supervisor': 'ultra'}}})
            settings = {'codex': str(native), 'timeout': 20, **roles, 'jev_api_key': '', 'jev_model': 'jev-test',
                        'jev_endpoint': 'https://unused.invalid', 'jev_confidence': .8}
            auth_home = root / 'original-native-home'
            auth_home.mkdir()
            auth = auth_home / 'auth.json'
            auth.write_text('{"private":"test-auth"}')
            with patch.dict(os.environ, {'CODEX_HOME': str(auth_home)}):
                env, session = runtime.setup(root, settings, False)
            try:
                self.assertEqual(Path(env['CODEX_HOME']).parent, root)
                self.assertTrue((Path(env['CODEX_HOME']) / 'auth.json').is_file())
                self.assertEqual(env['CEREBRO_INPUT_OWNER'], 'external')
                runtime.capture_task_input(env, 'Record arguments without modifying source.')
                result = runtime.codex(root, env, 'Record arguments without modifying source.', settings, supervisor=True)
                self.assertEqual(result['answer'], 'configuration recorded')
                observed = json.loads((root / 'child-request.json').read_text())
                self.assertEqual(observed['model'], 'future-parent')
                recorded = [json.loads(line) for path in root.glob('observations-*.jsonl')
                            for line in path.read_text().splitlines()]
                resolved = next(item for item in recorded if item['type'] == 'model_resolved')
                self.assertEqual((resolved['model'], resolved['effort']), ('future-parent', 'ultra'))
            finally:
                runtime.cleanup(env)
            self.assertFalse((Path(env['CODEX_HOME']) / 'auth.json').exists())
            self.assertEqual(auth.read_text(), '{"private":"test-auth"}')

    def test_cleanup_ignores_repo_receipts_and_rejects_symlinks_without_skipping_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            repo = root / 'repo'
            repo.mkdir()
            session = root / 'home/sessions/eval'
            jobs = session / 'detached-jobs'
            jobs.mkdir(parents=True)
            unrelated = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(30)'], start_new_session=True)
            try:
                identity = subprocess.check_output(['ps', '-p', str(unrelated.pid), '-o', 'lstart='], text=True).strip()
                receipt = repo / 'parent.process-running.json'
                receipt.write_text(json.dumps({'owner': 'cerebro-eval', 'prefix': 'parent',
                    'trial_directory': str(root), 'pid': unrelated.pid, 'identity': identity}))
                (root / 'parent.process-running.json').symlink_to(receipt)
                (root / 'configure.process-running.json').write_text('[]')
                (root / 'delegation.process-running.json').write_text('null')
                (root / 'native-contract.process-running.json').mkdir()
                (jobs / 'invalid-list.json').write_text('[]')
                (jobs / 'invalid-null.json').write_text('null')
                status = jobs / 'one.status'
                (jobs / 'one.json').write_text(json.dumps({'id': 'one', 'status': str(status)}))
                native_home = root / 'native-home'
                native_home.mkdir()
                (native_home / 'auth.json').write_text('temporary auth')
                env = {'CEREBRO_EVAL_DIR': str(root), 'CEREBRO_SESSION_DIR': str(session)}
                with patch('runtime.subprocess.run', return_value=subprocess.CompletedProcess([], 0)) as cancelled:
                    with self.assertRaises(RuntimeError):
                        runtime.cleanup(env)
                    self.assertEqual(cancelled.call_count, 1)
                    self.assertEqual(cancelled.call_args.args[0][1:], ['cancel', 'one'])
                self.assertIsNone(unrelated.poll())
                self.assertFalse((native_home / 'auth.json').exists())
            finally:
                unrelated.terminate()
                unrelated.wait(timeout=5)

    def test_counterbalance_preserves_each_arm_once_and_smoke_has_distinct_task_types(self):
        from comparison import ARMS, SMOKE
        orders = [run.arm_order(0, repeat, 42, ARMS) for repeat in range(len(ARMS))]
        self.assertEqual({tuple(order) for order in orders}.__len__(), len(ARMS))
        self.assertTrue(all(set(order) == set(ARMS) for order in orders))
        self.assertEqual(len(SMOKE), 3)
        self.assertIn('comparison-persisted-job-restart', SMOKE)
        self.assertIn('comparison-legitimate-investigation', SMOKE)

    def test_errors_and_abstentions_cannot_become_correct_labels(self):
        expected = {'validity': 'unsupported', 'usefulness': 'low_value', 'action': 'dismiss'}
        self.assertFalse(run.score_decision({}, expected)['correct'])
        self.assertFalse(run.score_decision({'error': 'timeout'}, expected)['correct'])
        self.assertFalse(run.score_decision(dict(expected, validity='uncertain'), expected)['correct'])
        self.assertTrue(run.score_decision(expected, expected)['correct'])
        self.assertEqual(run.exit_status([{'correct': True, 'jev': {'score': {'correct': False}}}]), 1)
        self.assertEqual(run.exit_status([{'correct': True, 'error': 'provider timeout'}]), 2)

    def test_fixture_grader_checks_behavior_and_preserves_supplied_tests(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            run.seed_episode(repo)
            before = file_hashes(repo)
            self.assertFalse(grade_episode(repo, before)['functional_pass'])
            (repo / 'parser.py').write_text('import csv\ndef parse_row(text):\n    return next(csv.reader([text])) if text else []\n')
            self.assertTrue(grade_episode(repo, before)['correct'])
            (repo / 'test_parser.py').write_text('print("all tests pass")\n')
            self.assertFalse(grade_episode(repo, before)['scope_pass'])

    def test_unfinished_and_failed_jobs_are_not_terminal_success(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory)
            jobs = session / 'detached-jobs'
            jobs.mkdir()
            for identifier, status in [('a', 'running'), ('b', '2'), ('c', '0')]:
                path = jobs / (identifier + '.status')
                path.write_text(status)
                (jobs / (identifier + '.json')).write_text(json.dumps({
                    'id': identifier, 'command': 'execute', 'status': str(path)}))
            self.assertEqual({job['id']: job['exit_code'] for job in job_outcomes(session)},
                             {'a': None, 'b': 2, 'c': 0})

    def test_supervisor_delegation_edits_are_not_parent_coding_and_native_default_overrides_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / 'repo'
            run.seed_episode(repo)
            session = root / 'session'
            (session / 'children').mkdir(parents=True)
            source = file_hashes(repo)
            events = [
                {'type': 'model', 'role': 'execute', 'model': 'worker'},
                {'type': 'model_resolved', 'role': 'execute', 'model': 'worker', 'effort': 'low'},
                {'type': 'model', 'role': 'review', 'model': 'reviewer'},
                {'type': 'model_resolved', 'role': 'review', 'model': 'reviewer', 'effort': 'medium'},
                {'type': 'review', 'role': 'review', 'passed': True, 'unchanged_during_check': True, 'source': source},
                {'type': 'activity', 'role': 'supervisor', 'changed_files': ['parser.py'], 'item_type': 'mcpToolCall'}]
            path = root / 'observations-test.jsonl'
            path.write_text(''.join(json.dumps(event) + '\n' for event in events))
            settings = {'models': {'implementation': 'worker', 'review': 'reviewer', 'supervisor': 'parent'},
                        'efforts': {'implementation': None, 'review': None, 'supervisor': None}}
            self.assertTrue(condition_metrics(root, session, 'supervisor', repo, settings)['condition_valid'])
            events[-1]['item_type'] = 'fileChange'
            path.write_text(''.join(json.dumps(event) + '\n' for event in events))
            self.assertFalse(condition_metrics(root, session, 'supervisor', repo, settings)['condition_valid'])
            events[-1]['changed_files'] = []
            events.append({'type': 'turn_settings', 'role': 'execute', 'effort': 'high', 'thread_id': 'worker'})
            path.write_text(''.join(json.dumps(event) + '\n' for event in events))
            self.assertIn('unexpected requested effort for execute', condition_metrics(
                root, session, 'supervisor', repo, settings)['condition_violations'])
            events.pop()
            events.append({'type': 'activity', 'role': 'supervisor', 'changed_files': ['parser.py'],
                           'item_type': 'commandExecution', 'source_root': str(repo), 'source': source,
                           'started_at': 10, 'finished_at': 11})
            events.append({'type': 'activity', 'role': 'execute', 'changed_files': ['parser.py'],
                           'item_type': 'fileChange', 'source_root': str(repo), 'source': source,
                           'started_at': 9, 'finished_at': 12})
            path.write_text(''.join(json.dumps(event) + '\n' for event in events))
            metric = condition_metrics(root, session, 'supervisor', repo, settings)
            self.assertEqual(metric['supervisor_source_edits'], 0)
            self.assertEqual(metric['ambiguous_source_ownership'], 1)
            self.assertFalse(metric['condition_valid'])
            (session / 'children/execute.scope.jsonl').write_text(json.dumps({
                'type': 'observer_failure', 'error': 'request context exceeded its limit'}) + '\n')
            metric = condition_metrics(root, session, 'supervisor_jev', repo, settings)
            self.assertEqual(metric['jev_batches'], 0)
            self.assertEqual(metric['jev_observer_failures'], 1)
            self.assertFalse(metric['condition_valid'])



if __name__ == '__main__':
    unittest.main()
