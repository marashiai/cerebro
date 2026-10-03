"""Collect measured usage, including partial and failed native/provider requests."""

import json
from pathlib import Path


def records(path):
    if not path.is_file():
        return []
    result = []
    for line in path.read_text().splitlines():
        try:
            result.append(json.loads(line))
        except ValueError:
            # A killed process can leave its last event incomplete.
            continue
    return result


def count(value):
    return value if type(value) is int and value >= 0 else None


def collect(directory, settings):
    ledger = []
    # Native counters are cumulative for a thread, including resumed turns.
    # Count each thread once instead of summing snapshots or resumed logs.
    threads, workers = {}, {}
    observations = sorted((event for path in directory.glob('observations-*.jsonl') for event in records(path)),
                          key=lambda event: event['time'])
    for event in observations:
        kind, key = event['type'], event.get('thread_id')
        if kind in ('model_attempt', 'model'):
            workers[event['worker_id']] = event
        if kind == 'model_resolved':
            workers.pop(event['worker_id'], None)
            item = threads.setdefault(key, {'provider': 'openai', 'role': event['role'],
                                           'model': event['model'], 'usage': {}, 'complete': False})
            if item['model'] != event['model'] or item['role'] != event['role']:
                item['ambiguous'] = True
        if key not in threads:
            continue
        item = threads[key]
        if kind == 'turn_started':
            item['complete'] = False
        elif kind == 'turn_finished':
            item['complete'] = event['completed']
        elif kind == 'token_usage':
            snapshot = event['usage']
            if snapshot.get('totalTokens', -1) >= item['usage'].get('totalTokens', -1):
                item['usage'] = snapshot
    for item in threads.values():
        tokens = item.pop('usage')
        item.update(input_tokens=count(tokens.get('inputTokens')),
                    cached_input_tokens=count(tokens.get('cachedInputTokens')),
                    cache_write_input_tokens=count(tokens.get('cacheWriteInputTokens')),
                    output_tokens=count(tokens.get('outputTokens')))
        item['complete'] &= (not item.pop('ambiguous', False) and item['input_tokens'] is not None
                             and item['output_tokens'] is not None)
        ledger.append(item)

    for event in workers.values():
        ledger.append({'provider': 'openai', 'role': event['role'], 'model': event['model'],
                       'input_tokens': None, 'cached_input_tokens': None, 'cache_write_input_tokens': None,
                       'output_tokens': None, 'complete': False})

    requests = {}
    for path in directory.rglob('*.jev.jsonl'):
        for event in records(path):
            identifier = event.get('request_id')
            if not identifier:
                continue
            item = requests.setdefault(identifier, {})
            item[event.get('type')] = event
    for item in requests.values():
        questions = item.get('request', {}).get('payload', {}).get('questions', {})
        if 'attention' in questions:
            role = 'jev-attention'
        elif ('validity' in questions and 'usefulness' in questions) or 'clean_validity' in questions:
            role = 'jev-review'
        else:
            role = 'jev-unknown'
        response = item.get('response', {})
        try:
            body = json.loads(response.get('body', '{}'))
        except ValueError:
            body = {}
        usage = body.get('usage', {}) if isinstance(body, dict) else {}
        model = body.get('model') if isinstance(body, dict) else None
        model = model or item.get('request', {}).get('payload', {}).get('model', settings['jev_model'])
        measured = {field: count(usage.get(field))
                    for field in ('input_tokens', 'cached_input_tokens', 'cache_write_input_tokens', 'output_tokens')}
        ledger.append({'provider': 'jev', 'role': role, 'model': model, **measured,
                       'complete': (role != 'jev-unknown' and not response.get('error') and measured['input_tokens'] is not None
                                    and measured['output_tokens'] is not None)})
    processes = list(directory.rglob('*.process.json'))
    native_failed = (any(json.loads(path.read_text()).get('exit_code') != 0 for path in processes)
                     or bool(list(directory.rglob('*.process-running.json'))))
    return {'usage_ledger': ledger, 'usage_complete': bool(ledger) and not native_failed and all(item['complete'] for item in ledger)}
