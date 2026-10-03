"""Grade receipts from the real parent, native children and durable jobs."""

from collections import Counter
import json
from pathlib import Path

from runtime import ROLE_GROUPS, file_hashes, role_model
from fixtures import git

def tool_response(item):
    if item.get('error') or item.get('status') == 'failed':
        return {'exit_code': 1, 'error': 'native tool failed'}
    for content in (item.get('result') or {}).get('content', []):
        if content.get('type') == 'text':
            try:
                value = json.loads(content['text'])
            except ValueError:
                continue
            if isinstance(value, dict) and 'exit_code' in value:
                return value
    return {'exit_code': 1, 'error': 'no structured Cerebro command response'}


def parent_calls(directory):
    calls = []
    for line in (directory / 'parent.stdout.jsonl').read_text().splitlines():
        event = json.loads(line)
        item = event.get('item', {})
        if event.get('type') != 'item.completed' or item.get('type') != 'mcp_tool_call':
            continue
        args = item.get('arguments', {})
        if isinstance(args, str):
            args = json.loads(args)
        if isinstance(args.get('argv'), list):
            response = tool_response(item)
            calls.append({'argv': args['argv'], 'response': response,
                          'success': response.get('exit_code') == 0 and response.get('job_exit_code') in (None, 0)})
    return calls


def job_outcomes(session):
    outcomes = []
    for path in (session / 'detached-jobs').glob('*.json'):
        job = json.loads(path.read_text())
        if not job.get('id') or not job.get('status'):
            continue
        status = Path(job['status'])
        text = status.read_text().strip() if status.exists() else ''
        updates = Path(job['status'] + '.updates.json')
        outcomes.append({'id': job['id'], 'exit_code': int(text) if text.lstrip('-').isdigit() else None,
                         'published_notices': json.loads(updates.read_text())['sequence'] if updates.exists() else 0})
    return outcomes


def episode_metrics(directory, session, arm, repo, base, criteria, efforts, *, max_implementations=2):
    calls = parent_calls(directory)
    commands = [call['argv'] for call in calls]
    classifications = []
    for path in (session / 'children').glob('*.scope.jsonl'):
        classifications.extend(json.loads(line)['classification'] for line in path.read_text().splitlines())
    observations = [json.loads(line) for path in directory.glob('observations-*.jsonl')
                    for line in path.read_text().splitlines()]
    final_source = file_hashes(repo)
    bound = [item for item in observations if item.get('passed') and item.get('unchanged_during_check')
             and item.get('source') == final_source]
    test_runs = sum(item['type'] == 'tests' for item in bound)
    violations = []
    resolved = [item for item in observations if item['type'] == 'model_resolved']
    if len(resolved) != sum(item['type'] == 'model' for item in observations):
        violations.append('missing effective native model or effort evidence')
    for item in observations:
        if item['type'] in ('model', 'model_resolved'):
            expected = role_model(item['role'])
            if item['model'] != expected:
                violations.append('native child used an unexpected model')
            if item['type'] == 'model_resolved' and item['effort'] != efforts[ROLE_GROUPS[item['role']]]:
                violations.append('native child used an unexpected reasoning effort')
    for argv in commands:
        if argv[0] in ('execute', 'apply-review', 'doc-write'):
            if ('--no-watch' if arm == 'with_jev' else '--watch') in argv:
                violations.append('changed assigned monitoring condition')
        if '--model' in argv:
            expected = role_model(argv[0])
            if argv[argv.index('--model') + 1] != expected:
                violations.append('changed assigned role model')
    reviews = []
    bound_reviews = {item['thread_id'] for item in bound if item['type'] == 'review'}
    for call in calls:
        argv = call['argv']
        if argv[0] != 'review' or not call['success'] or call['response'].get('state') != 'completed':
            continue
        if ('--base' not in argv or '--criteria-file' not in argv or
                git(repo, 'rev-parse', argv[argv.index('--base') + 1]) != base or
                Path(argv[argv.index('--criteria-file') + 1]).resolve() != criteria):
            violations.append('review used different base or criteria')
            continue
        path = Path(call['response'].get('text', '').strip())
        if not path.is_file() or not path.read_text().strip() or not path.with_suffix('.log').is_file():
            continue
        for line in path.with_suffix('.log').read_text().splitlines():
            event = json.loads(line)
            if event.get('type') == 'thread.started' and event.get('thread_id') in bound_reviews:
                reviews.append(path)
                break
    assessments = list((session / 'children').glob('review-*.assessment.json'))
    if not reviews:
        violations.append('no completed independent review')
    watched = any(item['type'] == 'model' and item['role'] in ('execute', 'apply-review', 'doc-write')
                  for item in observations)
    if arm == 'with_jev' and ((watched and not classifications) or
                              any(not path.with_suffix('.assessment.json').is_file() for path in reviews)):
        violations.append('missing scope or review assessment coverage')
    if arm == 'without_jev' and (classifications or assessments):
        violations.append('baseline unexpectedly used Jev')
    corrections = {call['response'].get('job_id') for call in calls
                   if call['argv'][0] in ('apply-review', 'execute') and call['success']}
    corrections.discard(None)
    if len(corrections) > max_implementations:
        violations.append('exceeded the implementation-job budget')
    initial_path = directory / 'first-response.json'
    initial = json.loads(initial_path.read_text()) if initial_path.exists() else {}
    received = [initial] + [call['response'] for call in calls]
    notices = {(value['job_id'], value['sequence']) for value in received if 'notice' in value}
    jobs = job_outcomes(session)
    steers = sum(call['argv'][0] == 'steer' and call['success'] for call in calls)
    original_native = initial.get('notice', {}).get('native_id')
    accepted_steers = [item for item in observations if item['type'] == 'steer' and item['passed']]
    recovered = bool(steers and original_native and any(
        test['type'] == 'tests' and test['thread_id'] == original_native
        and test.get('started_at') and test['started_at'] > steer['time']
        for test in bound for steer in accepted_steers if steer['thread_id'] == original_native))
    return {'scope_batches': len(classifications), 'scope_notices': len(notices),
            'published_scope_notices': sum(job['published_notices'] for job in jobs),
            'jobs': jobs,
            'scope_labels': dict(Counter(c['scope'] for c in classifications)),
            'steers': steers, 'native_steers_accepted': len(accepted_steers),
            'same_child_tested_after_steering': recovered,
            'steer_attempts': sum(argv[0] == 'steer' for argv in commands),
            'correction_children': len(corrections),
            'reviews': len(reviews), 'review_assessments': len(assessments),
            'recorded_passing_test_runs': test_runs, 'protocol_violations': violations,
            'effective_child_settings': [{'role': item['role'], 'model': item['model'], 'effort': item['effort']}
                                         for item in resolved],
            'parent_calls': calls, 'final_source_sha256': final_source}
