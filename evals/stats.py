"""Validated public eval data and descriptive statistics for matched trials."""

from collections import defaultdict
from datetime import date
import hashlib
import json
import math
import random
import re
from urllib.parse import urlsplit


ROLES = ('baseline', 'implementation', 'review', 'supervisor')
ARMS = ('bare', 'cerebro', 'cerebro_jev', 'without_jev', 'with_jev', 'protocol')
LEDGER_ROLES = set(ROLES) | {'execute', 'apply-review', 'doc-write', 'verify',
                                'audit', 'improve', 'jev-scope', 'jev-review'}
TOKEN_KEYS = ('input_tokens', 'cached_input_tokens', 'cache_write_input_tokens', 'output_tokens')
PRICE_KEYS = ('input_per_million', 'cached_input_per_million', 'cache_write_input_per_million', 'output_per_million')
BOOTSTRAP_MIN_CASES = 10
BOOTSTRAP_SAMPLES = 2000
SCALAR_METRICS = (
    'functional_pass', 'scope_pass', 'scope_batches', 'scope_notices',
    'published_scope_notices', 'steers', 'native_steers_accepted',
    'same_child_tested_after_steering', 'steer_attempts', 'correction_children',
    'reviews', 'review_assessments', 'recorded_passing_test_runs',
    'recovered_after_observed_drift', 'candidate_false_alarms', 'unnecessary_steers',
    'parent_seconds', 'first_response_seconds',
)


def identifier(value, label):
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,95}', value):
        raise ValueError(label + ' must be a short public identifier, without paths or prose')
    if value.lower().startswith(('sk-', 'sk_', 'bearer')):
        raise ValueError(label + ' must not contain a credential')
    return value


def model_identifier(value):
    if (not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.:/+@-]{0,127}', value)
            or any(part in ('', '.', '..') or part.lower().startswith(('sk-', 'sk_'))
                   for part in value.split('/'))):
        raise ValueError('model must be a public model identifier, without a path or credential')
    return value


def number(value, label, *, integer=False, nonnegative=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(label + ' must be a finite number')
    if integer and not isinstance(value, int):
        raise ValueError(label + ' must be an integer')
    if nonnegative and value < 0:
        raise ValueError(label + ' must be nonnegative')
    return value


def boolean(value, label):
    if not isinstance(value, bool):
        raise ValueError(label + ' must be a boolean')
    return value


def public_settings(settings):
    if not isinstance(settings, dict):
        raise ValueError('each trial requires settings with models and efforts')
    public = {}
    for key in ('models', 'efforts'):
        values = settings.get(key)
        if not isinstance(values, dict) or set(values) != set(ROLES):
            raise ValueError('trial settings.' + key + ' must specify all four roles')
        public[key] = {role: model_identifier(values[role]) if key == 'models' else identifier(values[role], key)
                       for role in ROLES}
    public['jev_model'] = model_identifier(settings.get('jev_model'))
    public['jev_confidence'] = number(settings.get('jev_confidence'), 'jev_confidence', nonnegative=True)
    if public['jev_confidence'] > 1:
        raise ValueError('jev_confidence must be at most one')
    public['timeout'] = number(settings.get('timeout'), 'timeout', nonnegative=True)
    if public['timeout'] == 0:
        raise ValueError('timeout must be positive')
    endpoint = settings.get('jev_endpoint')
    if not isinstance(endpoint, str) or not endpoint or any(ord(char) < 32 for char in endpoint):
        raise ValueError('jev_endpoint must be a nonempty string without control characters')
    public['jev_endpoint_sha256'] = hashlib.sha256(endpoint.encode()).hexdigest()
    return public


def public_prices(prices):
    if prices is None:
        return None
    if not isinstance(prices, dict) or set(prices) != {'as_of', 'source', 'basis', 'models'}:
        raise ValueError('price sheet requires as_of, source, basis and models')
    try:
        date.fromisoformat(prices['as_of'])
        source = urlsplit(prices['source'])
    except (TypeError, ValueError):
        raise ValueError('invalid price date or source URL') from None
    if (source.scheme != 'https' or not source.netloc or source.username or source.password
            or source.query or source.fragment):
        raise ValueError('price source must be a public HTTPS URL without credentials, query or fragment')
    if not isinstance(prices['basis'], str) or not 1 <= len(prices['basis']) <= 600:
        raise ValueError('price basis must be a short public description')
    if not isinstance(prices['models'], dict):
        raise ValueError('price models must be an object keyed by model')
    result = {}
    for model, rates in prices['models'].items():
        model_identifier(model)
        if not isinstance(rates, dict) or set(rates) - set(PRICE_KEYS):
            raise ValueError('price rates accept only ' + ', '.join(PRICE_KEYS))
        result[model] = {key: number(value, key, nonnegative=True) for key, value in rates.items()}
    return {'as_of': prices['as_of'], 'source': prices['source'], 'basis': prices['basis'], 'models': result}


def entry_cost(item, prices):
    rates = (prices or {}).get('models', {}).get(item['model'], {})
    if not item['complete'] or any(key not in rates for key in PRICE_KEYS):
        return None
    if len({rates[key] for key in PRICE_KEYS[:3]}) == 1:
        input_cost = item['input_tokens'] * rates['input_per_million']
    else:
        if any(item[key] is None for key in ('cached_input_tokens', 'cache_write_input_tokens')):
            return None
        cached, written = item['cached_input_tokens'], item['cache_write_input_tokens']
        input_cost = ((item['input_tokens'] - cached - written) * rates['input_per_million']
                      + cached * rates['cached_input_per_million'] + written * rates['cache_write_input_per_million'])
    return (input_cost + item['output_tokens'] * rates['output_per_million']) / 1_000_000


def public_usage(row, prices):
    raw = row.get('usage_ledger', [])
    if not isinstance(raw, list):
        raise ValueError('usage_ledger must be a list')
    ledger = []
    for entry in raw:
        if not isinstance(entry, dict) or entry.get('provider') not in ('openai', 'jev'):
            raise ValueError('usage provider must be openai or jev')
        if entry.get('role') not in LEDGER_ROLES:
            raise ValueError('unrecognized usage role')
        item = {'provider': entry['provider'], 'role': entry['role'],
                'model': model_identifier(entry.get('model')),
                'complete': boolean(entry.get('complete'), 'usage complete')}
        for key in TOKEN_KEYS:
            value = entry.get(key)
            item[key] = None if value is None else number(value, key, integer=True, nonnegative=True)
        if item['complete'] and any(item[key] is None for key in ('input_tokens', 'output_tokens')):
            raise ValueError('complete usage requires input and output tokens')
        if (item['input_tokens'] is not None
                and sum(item[key] or 0 for key in ('cached_input_tokens', 'cache_write_input_tokens'))
                > item['input_tokens']):
            raise ValueError('cached and cache-write input tokens exceed input tokens')
        item['estimated_cost_usd'] = entry_cost(item, prices)
        ledger.append(item)
    complete = boolean(row.get('usage_complete', False), 'usage_complete')
    if complete and (not ledger or not all(item['complete'] for item in ledger)):
        raise ValueError('usage_complete requires complete usage from every recorded worker')
    known = {key: sum(values) if (values := [item[key] for item in ledger if item[key] is not None])
             else None for key in TOKEN_KEYS}
    costs = [item['estimated_cost_usd'] for item in ledger]
    cost = sum(costs) if complete and all(value is not None for value in costs) else None
    return {'usage_ledger': ledger, 'usage_complete': complete, 'known_tokens': known, 'cost_usd': cost}


def public_trials(document, prices=None):
    if not isinstance(document, dict) or not isinstance(document.get('trials'), list) or not document['trials']:
        raise ValueError('results.json must contain a nonempty trials list')
    prices = public_prices(prices)
    trials = []
    seen = set()
    for raw in document['trials']:
        if not isinstance(raw, dict):
            raise ValueError('each trial must be an object')
        row = {key: identifier(raw.get(key), key) for key in ('case', 'kind', 'arm', 'mode')}
        if row['mode'] not in ('comparison', 'live', 'calibration', 'protocol') or row['arm'] not in ARMS:
            raise ValueError('unrecognized trial mode or arm')
        if 'capability' in raw:
            row['capability'] = identifier(raw['capability'], 'capability')
        row['repeat'] = number(raw.get('repeat'), 'repeat', integer=True, nonnegative=True)
        expected = raw.get('expected_arms')
        if (not isinstance(expected, list) or not expected or any(not isinstance(arm, str) for arm in expected)
                or len(set(expected)) != len(expected)
                or any(arm not in ARMS for arm in expected) or row['arm'] not in expected):
            raise ValueError('trial requires distinct expected_arms including its own arm')
        allowed = ({'bare', 'cerebro'}, {'bare', 'cerebro', 'cerebro_jev'}) if row['mode'] == 'comparison' else (
            ({'protocol'},) if row['mode'] == 'protocol' else ({'without_jev', 'with_jev'}, {'without_jev'}))
        if set(expected) not in allowed:
            raise ValueError('expected_arms must match the trial mode')
        row['paired'] = len(expected) > 1
        row['expected_arms'] = [arm for arm in ARMS if arm in expected]
        row['settings'] = public_settings(raw.get('settings'))
        row['configuration'] = hashlib.sha256(json.dumps(row['settings'], sort_keys=True).encode()).hexdigest()[:12]
        key = (row['configuration'], row['mode'], row['case'], row['repeat'], row['arm'])
        if key in seen:
            raise ValueError('duplicate arm for case, repeat and model/effort configuration')
        seen.add(key)
        row['error'] = bool(raw.get('error'))
        row['correct'] = boolean(raw.get('correct'), 'correct') and not row['error']
        row['elapsed_seconds'] = number(raw.get('elapsed_seconds'), 'elapsed_seconds', nonnegative=True)
        checks = raw.get('checks', {})
        if not isinstance(checks, dict):
            raise ValueError('checks must be an object')
        row['checks'] = {identifier(key, 'check name'): boolean(value, 'check') for key, value in checks.items()}
        metrics = {key: raw[key] for key in SCALAR_METRICS if raw.get(key) is not None}
        nested = raw.get('metrics', {})
        if not isinstance(nested, dict):
            raise ValueError('metrics must be an object')
        for key, value in nested.items():
            if isinstance(value, (int, float)):
                key = identifier(key, 'metric name')
                if key in metrics and metrics[key] != value:
                    raise ValueError('conflicting metric values')
                metrics[key] = value
        row['metrics'] = {identifier(key, 'metric name'): value if isinstance(value, bool)
                          else number(value, 'metric') for key, value in metrics.items()}
        for source, target in (('protocol_violations', 'protocol_violation_count'),
                               ('transient_unrelated_edits', 'transient_unrelated_edit_count')):
            if source in raw:
                if not isinstance(raw[source], list):
                    raise ValueError(source + ' must be a list')
                row['metrics'][target] = len(raw[source])
        jev = raw.get('jev')
        if isinstance(jev, dict) and isinstance(jev.get('score'), dict) and 'correct' in jev['score']:
            row['metrics']['jev_classification_correct'] = boolean(jev['score']['correct'], 'Jev score')
        row.update(public_usage(raw, prices))
        trials.append(row)
    return trials


def wilson(successes, total):
    if total == 0:
        return None
    z = 1.959963984540054
    rate = successes / total
    denominator = 1 + z * z / total
    center = (rate + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(rate * (1 - rate) / total + z * z / (4 * total * total)) / denominator
    return [max(0, center - margin), min(1, center + margin)]


def arm_summary(rows):
    count = len(rows)
    passed = sum(row['correct'] for row in rows)
    costs = [row['cost_usd'] for row in rows if row['cost_usd'] is not None]
    metrics = {}
    for key in sorted({key for row in rows for key in row['metrics']}):
        values = [row['metrics'][key] for row in rows if key in row['metrics']]
        metrics[key] = {'n': len(values), 'sum': sum(values), 'mean': sum(values) / len(values)}
    workers = defaultdict(list)
    for row in rows:
        for item in row['usage_ledger']:
            workers[(item['provider'], item['role'], item['model'])].append(item)
    by_role = []
    for (provider, role, model), entries in sorted(workers.items()):
        estimated = [item['estimated_cost_usd'] for item in entries]
        by_role.append({'provider': provider, 'role': role, 'model': model, 'entries': len(entries),
                        'complete_entries': sum(item['complete'] for item in entries),
                        'known_tokens': {key: sum(values) if (values := [item[key] for item in entries
                                                                        if item[key] is not None])
                                         else None for key in TOKEN_KEYS},
                        'token_known_entries': {key: sum(item[key] is not None for item in entries) for key in TOKEN_KEYS},
                        'estimated_cost_usd': sum(estimated) if all(value is not None for value in estimated) else None})
    return {'trials': count, 'passed': passed, 'errors': sum(row['error'] for row in rows),
            'pass_rate': passed / count if count else None, 'wilson_95': wilson(passed, count),
            'mean_seconds': sum(row['elapsed_seconds'] for row in rows) / count if count else None,
            'total_seconds': sum(row['elapsed_seconds'] for row in rows),
            'mean_cost_usd': sum(costs) / count if count and len(costs) == count else None,
            'total_cost_usd': sum(costs) if count and len(costs) == count else None,
            'cost_known_trials': len(costs), 'usage_complete_trials': sum(row['usage_complete'] for row in rows),
            'known_tokens': {key: sum(values) if (values := [row['known_tokens'][key] for row in rows
                                                            if row['known_tokens'][key] is not None])
                             else None for key in TOKEN_KEYS}, 'usage_by_role': by_role, 'metrics': metrics}


def percentile(values, quantile):
    position = (len(values) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def paired_delta(units, before, after):
    count = len(units)
    grouped = defaultdict(list)
    for unit in units:
        a, b = unit[before], unit[after]
        grouped[a['case']].append((100 * (int(b['correct']) - int(a['correct'])),
                                  b['elapsed_seconds'] - a['elapsed_seconds'],
                                  b['cost_usd'] - a['cost_usd']
                                  if a['cost_usd'] is not None and b['cost_usd'] is not None else None))
    values = [value for case in grouped.values() for value in case]
    keys = ('pass_rate_delta_pp', 'mean_seconds_delta', 'mean_cost_usd_delta')
    result = {'before': before, 'after': after, 'pairs': count, 'independent_cases': len(grouped),
              'improved': sum(value[0] > 0 for value in values), 'regressed': sum(value[0] < 0 for value in values),
              'tied': sum(value[0] == 0 for value in values), 'cluster_bootstrap_95': {},
              'bootstrap_minimum_cases': BOOTSTRAP_MIN_CASES}
    for index, key in enumerate(keys):
        selected = [value[index] for value in values]
        result[key] = sum(selected) / count if count and all(value is not None for value in selected) else None
        result['cluster_bootstrap_95'][key] = None
    if len(grouped) >= BOOTSTRAP_MIN_CASES:
        rng = random.Random(1729)
        cases = [grouped[case] for case in sorted(grouped)]
        samples = {key: [] for key in keys if result[key] is not None}
        for _ in range(BOOTSTRAP_SAMPLES):
            sample = [value for case in rng.choices(cases, k=len(cases)) for value in case]
            for index, key in enumerate(keys):
                if key in samples:
                    samples[key].append(sum(value[index] for value in sample) / len(sample))
        for key, sample in samples.items():
            sample.sort()
            result['cluster_bootstrap_95'][key] = [percentile(sample, 0.025), percentile(sample, 0.975)]
    return result


def cohort_summary(rows):
    expected = rows[0]['expected_arms']
    units = defaultdict(dict)
    for row in rows:
        unit = units[(row['case'], row['repeat'])]
        if row['expected_arms'] != expected:
            raise ValueError('inconsistent expected arms within cohort')
        unit[row['arm']] = row
    complete = [unit for unit in units.values() if set(unit) == set(expected)]
    missing = [{'case': case, 'repeat': repeat, 'present_arms': list(unit),
                'missing_arms': [arm for arm in expected if arm not in unit]}
               for (case, repeat), unit in units.items() if set(unit) != set(expected)]
    selected = [row for unit in complete for row in unit.values()]
    comparisons = [(expected[0], arm) for arm in expected[1:]]
    if expected == ['bare', 'cerebro', 'cerebro_jev']:
        comparisons.append(('cerebro', 'cerebro_jev'))
    return {'configuration': rows[0]['configuration'], 'settings': rows[0]['settings'],
            'mode': rows[0]['mode'], 'kind': rows[0]['kind'] if rows[0]['mode'] != 'comparison' else 'capabilities',
            'expected_arms': expected, 'matched_units': len(complete),
            'independent_cases': len({row['case'] for row in selected}), 'incomplete_units': missing,
            'excluded_trials': len(rows) - len(selected), 'all_trials': len(rows),
            'all_errors': sum(row['error'] for row in rows),
            'arms': {arm: arm_summary([unit[arm] for unit in complete]) for arm in expected},
            'deltas': [paired_delta(complete, before, after) for before, after in comparisons],
            'capabilities': [{'case': case, 'kind': next(row.get('capability', row['kind']) for row in selected if row['case'] == case),
                              'arms': {arm: arm_summary([row for row in selected if row['case'] == case
                                                        and row['arm'] == arm]) for arm in expected}}
                             for case in sorted({row['case'] for row in selected})]}


def summarize(document, prices=None):
    prices = public_prices(prices)
    rows = public_trials(document, prices)
    groups = defaultdict(list)
    units = {}
    for row in rows:
        unit_key = (row['configuration'], row['mode'], row['case'], row['repeat'])
        definition = (row['kind'], tuple(row['expected_arms']))
        if unit_key in units and units[unit_key] != definition:
            raise ValueError('case/repeat has inconsistent kind or expected arms')
        units[unit_key] = definition
        kind = 'capabilities' if row['mode'] == 'comparison' else row['kind']
        groups[(row['configuration'], row['mode'], kind, tuple(row['expected_arms']))].append(row)
    cohorts = [cohort_summary(group) for _, group in sorted(groups.items())]
    return {'schema_version': 1, 'trials': rows, 'cohorts': cohorts, 'price_sheet': prices,
            'total_trials': len(rows), 'total_errors': sum(row['error'] for row in rows),
            'bootstrap': {'minimum_cases': BOOTSTRAP_MIN_CASES, 'samples': BOOTSTRAP_SAMPLES,
                          'unit': 'case; all repetitions retained together'},
            'scope': 'Selected small CSV fixtures; descriptive outcomes, not a general model benchmark.'}
