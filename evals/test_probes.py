"""Offline checks prevent protocol grading from mistaking arbitrary errors for success."""

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from probes import guarded_checks, jobs, native_failure_matches, response_values, start_parent, terminal_failure
from probes_provider import encode_events, response_events


class ProbeGradingTests(unittest.TestCase):
    def test_parent_probe_passes_resolved_model_and_effort_to_native_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            native = root / 'native-argument-recorder'
            native.write_text('#!' + sys.executable + '\nimport json,sys\n'
                              'if "mcp" in sys.argv:\n    print("[]")\n'
                              'else:\n    print(json.dumps({"argv":sys.argv[1:],"prompt":sys.stdin.read()}))\n')
            native.chmod(0o700)
            session = root / 'session'
            session.mkdir()
            (session / 'tools-supervisor.json').write_text(json.dumps({'mcpServers': {'cerebro': {'env': {}}}}))
            settings = {'codex': str(native), 'models': {'supervisor': 'future-protocol-parent'},
                        'efforts': {'supervisor': 'ultra'}}
            processes = []
            process = start_parent(root, {**os.environ, 'CEREBRO_SESSION_DIR': str(session)},
                                   settings, 'parent', 'Configuration boundary probe.', processes)
            try:
                self.assertEqual(process.finish(10)['exit_code'], 0)
                observed = json.loads((root / 'parent.stdout.jsonl').read_text())
                argv = observed['argv']
                self.assertEqual(argv[argv.index('--model') + 1], settings['models']['supervisor'])
                self.assertEqual([arg for arg in argv if arg.startswith('model_reasoning_effort=')],
                                 ['model_reasoning_effort=' + json.dumps(settings['efforts']['supervisor'])])
                self.assertEqual(observed['prompt'], 'Configuration boundary probe.')
                self.assertEqual(processes, [process])
            finally:
                process.stop()

    def test_failure_requires_matching_terminal_job_and_error_handoff(self):
        response = {'job_id': 'one', 'state': 'completed', 'exit_code': 2, 'mcp_is_error': True}
        self.assertTrue(terminal_failure(response, [{'id': 'one', 'exit_code': 2}]))
        for field, value in [('state', 'running'), ('exit_code', 0), ('exit_code', True), ('mcp_is_error', False), ('job_id', 'wrong')]:
            with self.subTest(field=field, value=value):
                self.assertFalse(terminal_failure({**response, field: value}, [{'id': 'one', 'exit_code': 2}]))
        for durable in ([], [{'exit_code': None}], [{'exit_code': 0}], [{'exit_code': 2}, {'exit_code': 2}]):
            self.assertFalse(terminal_failure(response, durable))

    def test_guard_requires_both_native_rejections_and_successful_guarded_read(self):
        def request(output=None):
            body = {'input': [] if output is None else [{'type': 'function_call_output', 'output': output}]}
            return {'kind': 'native_request', 'role': 'parent', 'body': body}
        read = json.dumps({'content': [{'type': 'text', 'text': json.dumps({'exit_code': 0, 'text': 'PROBE_PROOF'})}]})
        good = [request(), request('code-mode host is disabled'), request('Unknown tool exec_command'), request(read)]
        self.assertTrue(all(guarded_checks(good, 'parent').values()))
        bad_code = [good[0], request('PROBE_FORBIDDEN_EXECUTED'), *good[2:]]
        self.assertFalse(guarded_checks(bad_code, 'parent')['parent_code_mode_rejected'])
        bad_direct = [*good[:2], request('PROBE_FORBIDDEN_EXECUTED'), good[3]]
        self.assertFalse(guarded_checks(bad_direct, 'parent')['parent_direct_executor_rejected'])
        failed_read = [*good[:3], request(json.dumps({'exit_code': 1, 'text': 'PROBE_PROOF'}))]
        self.assertFalse(guarded_checks(failed_read, 'parent')['parent_allowed_read_succeeded'])

    def test_provider_cause_must_match_native_failure_not_arbitrary_nonzero(self):
        events = [{'type': 'turn.failed', 'error': {'message': 'some unrelated failure', 'codexErrorInfo': 'other'}}]
        for name in ('provider-token-limit', 'provider-quota-exhaustion', 'provider-interrupted-stream'):
            self.assertFalse(native_failure_matches(name, events))
        rate = [{'type': 'turn.failed', 'error': {'codexErrorInfo': {
            'responseTooManyFailedAttempts': {'httpStatusCode': 429}}}}]
        quota = [{'type': 'turn.failed', 'error': {'codexErrorInfo': 'usageLimitExceeded'}}]
        self.assertTrue(native_failure_matches('provider-token-limit', rate))
        self.assertTrue(native_failure_matches('provider-quota-exhaustion', quota))
        self.assertFalse(native_failure_matches('provider-quota-exhaustion', rate))
        self.assertFalse(native_failure_matches('provider-token-limit', quota))

    def test_job_inventory_does_not_count_update_sidecars_as_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            session = Path(directory)
            child = session / 'detached-jobs'
            child.mkdir()
            status = child / 'one.out.status'
            status.write_text('130\n')
            (child / 'one.json').write_text(json.dumps({'id': 'one', 'status': str(status)}))
            (child / 'one.out.status.updates.json').write_text(json.dumps({'sequence': 0, 'notices': []}))
            self.assertEqual([job['exit_code'] for job in jobs(session)], [130])

    def test_nested_handoff_decoding_does_not_accept_plain_completion_prose(self):
        completed = {'job_id': 'job', 'state': 'completed', 'exit_code': 0, 'text': 'done'}
        packet = {'content': [{'text': json.dumps(completed)}]}
        self.assertEqual(response_values(json.dumps(packet)), [completed])
        self.assertEqual(response_values('The job completed successfully'), [])

    def test_interrupted_provider_fixture_has_partial_text_without_terminal_event(self):
        events = response_events(text='PROBE_PARTIAL_BEFORE_DISCONNECT')[:4]
        encoded = encode_events(events)
        self.assertIn(b'PROBE_PARTIAL_BEFORE_DISCONNECT', encoded)
        self.assertNotIn(b'response.completed', encoded)
        self.assertNotIn(b'response.output_item.done', encoded)


if __name__ == '__main__':
    unittest.main()
