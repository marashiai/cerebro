"""Separate task outcomes from model, role and monitoring condition evidence."""

import json
from pathlib import Path

from runtime import ROLE_GROUPS, file_hashes
from usage import records


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
    for event in records(directory / 'parent.stdout.jsonl'):
        item = event.get('item', {})
        if event.get('type') != 'item.completed' or item.get('type') != 'mcp_tool_call':
            continue
        args = item.get('arguments', {})
        if isinstance(args, str):
            args = json.loads(args)
        if isinstance(args.get('argv'), list):
            response = tool_response(item)
            calls.append({'argv': args['argv'], 'response': response,
                          'success': response.get('exit_code') == 0})
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
        outcomes.append({'id': job['id'], 'command': job['command'],
                         'exit_code': int(text) if text.lstrip('-').isdigit() else None,
                         'published_notices': json.loads(updates.read_text())['sequence'] if updates.exists() else 0})
    return outcomes


def evidence(directory):
    return [event for path in directory.rglob('observations-*.jsonl') for event in records(path)]


def condition_metrics(directory, session, arm, repo, settings):
    seen = evidence(directory)
    final = file_hashes(repo)
    child = [item for item in seen if item.get('role') in ('execute', 'review')]
    requested = [item for item in seen if item['type'] == 'model']
    resolved = [item for item in seen if item['type'] == 'model_resolved']
    violations = []
    if len(requested) != len(resolved):
        violations.append('missing native model resolution evidence')
    for item in requested:
        role = ROLE_GROUPS[item['role']]
        expected_model = settings['models'][role]
        if item.get('model') != expected_model:
            violations.append('unexpected requested model for ' + role)
    for item in seen:
        if item['type'] == 'turn_settings':
            expected_effort = settings['efforts'][ROLE_GROUPS[item['role']]]
            if item.get('effort') != expected_effort:
                violations.append('unexpected requested effort for ' + item['role'])
    expected_review = arm not in ('bare_implementor', 'bare_supervisor')
    reviews = [item for item in child if item['type'] == 'review' and item.get('passed')
               and item.get('unchanged_during_check') and item.get('source') == final]
    implementations = [item for item in child if item['type'] == 'model' and item['role'] == 'execute']
    parent_edits = []
    ambiguous_edits = []
    for item in seen:
        if (item['type'] != 'activity' or item.get('role') != 'supervisor'
                or item.get('item_type') not in ('commandExecution', 'fileChange')):
            continue
        changed = set(item.get('changed_files', []))
        ambiguous = set()
        if item.get('item_type') == 'commandExecution':
            for activity in child:
                if (activity['type'] == 'activity' and activity.get('source_root') == item.get('source_root')
                        and activity.get('started_at') is not None and item.get('started_at') is not None
                        and activity.get('finished_at', activity.get('time', 0)) >= item['started_at']
                        and activity['started_at'] <= item.get('finished_at', item.get('time', 0))):
                    shared = {path for path in activity.get('changed_files', [])
                              if path in changed and activity.get('source', {}).get(path) == item.get('source', {}).get(path)}
                    ambiguous |= shared
                    changed -= shared
        if ambiguous:
            ambiguous_edits.append({**item, 'changed_files': sorted(ambiguous)})
        if changed:
            parent_edits.append({**item, 'changed_files': sorted(changed)})
    if expected_review and not reviews:
        violations.append('missing independent review of delivered source')
    if expected_review and not implementations:
        violations.append('missing implementor delegation')
    if arm in ('supervisor', 'supervisor_jev'):
        if parent_edits:
            violations.append('supervisor edited task source')
        if ambiguous_edits:
            violations.append('ambiguous supervisor/child source ownership')
    if arm == 'implementor_reviewer' and any(item.get('role') == 'supervisor' for item in seen):
        violations.append('unexpected supervisor inference')
    scope_rows = [item for path in (session / 'children').glob('*.scope.jsonl') for item in records(path)]
    classifications = [item for item in scope_rows if isinstance(item.get('classification'), dict)]
    observer_failures = sum(item.get('type') == 'observer_failure' for item in scope_rows)
    assessments = list((session / 'children').glob('*.assessment.json'))
    monitored = arm == 'supervisor_jev'
    if monitored and observer_failures:
        violations.append('Jev implementation observer failed')
    if monitored and implementations and not classifications:
        violations.append('missing Jev implementation observation')
    if monitored and reviews and not assessments:
        violations.append('missing Jev review assessment')
    if not monitored and (scope_rows or assessments):
        violations.append('unexpected Jev use')
    provider_failures = sum(bool(item.get('error')) for path in session.rglob('*.jev.jsonl')
                            for item in records(path) if item.get('type') == 'response')
    jobs = job_outcomes(session)
    calls = parent_calls(directory) if (directory / 'parent.stdout.jsonl').exists() else []
    turn_efforts = {item['thread_id']: item.get('effort') for item in seen if item['type'] == 'turn_settings'}
    effective = [{key: item.get(key) for key in ('role', 'model', 'effort', 'thread_id')} for item in resolved]
    for item in effective:
        item['thread_default_effort'] = item['effort']
        override = turn_efforts.get(item['thread_id'])
        item['effort_origin'] = 'turn_override' if override is not None else 'native_default'
        if override is not None:
            item['effort'] = override
    return {'role_separation_expected': expected_review, 'condition_valid': not violations, 'condition_violations': sorted(set(violations)),
            'effective_native_settings': effective,
            'requested_native_settings': [{key: item.get(key) for key in ('role', 'model', 'effort', 'method')}
                                          for item in requested],
            'jev_observer_failures': observer_failures, 'jev_provider_failures': provider_failures, 'jobs': jobs, 'reviews': len(reviews), 'implementation_attempts': len(implementations),
            'supervisor_source_edits': len(parent_edits), 'ambiguous_source_ownership': len(ambiguous_edits), 'jev_batches': len(classifications),
            'jev_notices': sum(job['published_notices'] for job in jobs), 'review_assessments': len(assessments),
            'accepted_steers': sum(item['type'] == 'steer' and item.get('passed', False) for item in seen),
            'parent_calls': calls}
