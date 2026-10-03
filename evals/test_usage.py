import json
from pathlib import Path
import tempfile
import unittest

from usage import collect


class UsageTests(unittest.TestCase):
    def test_resumed_thread_totals_are_counted_once_and_failed_turn_is_partial(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def write(name, rows):
                (root / name).write_text(''.join(json.dumps(row) + '\n' for row in rows))
            write('observations-one.jsonl', [
                {'time': 1, 'type': 'model', 'worker_id': 'one', 'role': 'execute', 'model': 'worker'},
                {'time': 2, 'type': 'model_resolved', 'thread_id': 'shared', 'worker_id': 'one',
                 'role': 'execute', 'model': 'worker'},
                {'time': 3, 'type': 'token_usage', 'thread_id': 'shared', 'usage': {
                    'totalTokens': 110, 'inputTokens': 100, 'cachedInputTokens': 60,
                    'cacheWriteInputTokens': 0, 'outputTokens': 10}},
                {'time': 4, 'type': 'turn_finished', 'thread_id': 'shared', 'completed': True},
            ])
            write('observations-two.jsonl', [
                {'time': 5, 'type': 'model_resolved', 'thread_id': 'shared', 'worker_id': 'two',
                 'role': 'execute', 'model': 'worker'},
                {'time': 6, 'type': 'turn_started', 'thread_id': 'shared'},
                {'time': 7, 'type': 'token_usage', 'thread_id': 'shared', 'usage': {
                    'totalTokens': 330, 'inputTokens': 300, 'cachedInputTokens': 160,
                    'cacheWriteInputTokens': 10, 'outputTokens': 30}},
            ])
            measured = collect(root, {'models': {'supervisor': 'parent'}, 'jev_model': 'jev'})
            self.assertEqual(len(measured['usage_ledger']), 1)
            item = measured['usage_ledger'][0]
            self.assertEqual(item['input_tokens'], 300)
            self.assertEqual(item['cached_input_tokens'], 160)
            self.assertEqual(item['cache_write_input_tokens'], 10)
            self.assertEqual(item['output_tokens'], 30)
            self.assertFalse(item['complete'])
            self.assertFalse(measured['usage_complete'])

    def test_parent_failures_and_unanswered_jev_are_not_zero_cost_successes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            events = [
                {'time': 1, 'type': 'model_resolved', 'thread_id': 'parent', 'worker_id': 'one',
                 'role': 'implementation', 'model': 'bare-model'},
                {'time': 2, 'type': 'token_usage', 'thread_id': 'parent', 'usage': {
                    'totalTokens': 110, 'inputTokens': 100, 'cachedInputTokens': 50, 'outputTokens': 10}},
                {'time': 3, 'type': 'turn_finished', 'thread_id': 'parent', 'completed': False}]
            (root / 'observations-one.jsonl').write_text(''.join(json.dumps(event) + '\n' for event in events))
            (root / 'parent.process.json').write_text(json.dumps({'exit_code': -15, 'timed_out': True}))
            (root / 'request.jev.jsonl').write_text(json.dumps({
                'type': 'request', 'request_id': 'lost', 'payload': {
                    'model': 'jev-test', 'questions': {'attention': {}}}}) + '\n')
            measured = collect(root, {'jev_model': 'jev'})
            parent, jev = measured['usage_ledger']
            self.assertEqual(parent['input_tokens'], 100)
            self.assertEqual(parent['model'], 'bare-model')
            self.assertFalse(parent['complete'])
            self.assertIsNone(jev['input_tokens'])
            self.assertFalse(jev['complete'])
            self.assertFalse(measured['usage_complete'])

    def test_jev_measured_response_and_native_failure_before_thread_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = [
                {'type': 'request', 'request_id': 'one', 'payload': {
                    'model': 'jev-alias', 'questions': {'attention': {}}}},
                {'type': 'response', 'request_id': 'one', 'body': json.dumps({
                    'model': 'jev-resolved', 'usage': {'input_tokens': 20, 'output_tokens': 5}})},
            ]
            (root / 'execute-20261003T141705Z-57979-31113.jev.jsonl').write_text(
                ''.join(json.dumps(row) + '\n' for row in rows))
            (root / 'observations-broken.jsonl').write_text(json.dumps({
                'time': 1, 'type': 'model', 'worker_id': 'broken', 'role': 'review', 'model': 'review-model'}) + '\n')
            measured = collect(root, {'jev_model': 'jev'})
            child, jev = measured['usage_ledger']
            self.assertFalse(child['complete'])
            self.assertEqual(jev['role'], 'jev-attention')
            self.assertEqual(jev['model'], 'jev-resolved')
            self.assertEqual(jev['input_tokens'], 20)
            self.assertTrue(jev['complete'])
            self.assertIsNone(jev['cached_input_tokens'])
            self.assertFalse(measured['usage_complete'])

    def test_jev_role_uses_request_schema_and_missing_requests_remain_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = [
                {'type': 'request', 'request_id': 'review', 'payload': {
                    'model': 'jev-alias', 'questions': {'validity': {}, 'usefulness': {}}}},
                {'type': 'response', 'request_id': 'review', 'body': json.dumps({
                    'model': 'jev-resolved', 'usage': {'input_tokens': 20, 'output_tokens': 5}})},
                {'type': 'response', 'request_id': 'orphan', 'body': json.dumps({
                    'model': 'jev-resolved', 'usage': {'input_tokens': 40, 'output_tokens': 10}})},
            ]
            (root / 'trace.jev.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
            measured = collect(root, {'jev_model': 'jev'})
            review, orphan = measured['usage_ledger']
            self.assertEqual(review['role'], 'jev-review')
            self.assertTrue(review['complete'])
            self.assertEqual(orphan['role'], 'jev-unknown')
            self.assertEqual(orphan['input_tokens'], 40)
            self.assertFalse(orphan['complete'])
            self.assertFalse(measured['usage_complete'])


if __name__ == '__main__':
    unittest.main()
