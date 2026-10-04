#!/usr/bin/env python3
"""Compare native Codex role configurations, with optional Jev advisory evidence."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from runtime import ROOT, capture_task_input, environment, redact, setup, codex, write_json
from model_config import add_arguments, cli_overrides, load_config, resolve_config
from fixtures import git, seed_episode, seed_repo
from usage import collect as collect_usage
import comparison
import job_fixture
import lease_fixture
import probes
from jev import ENDPOINT
from review_check import assess, context

HERE = Path(__file__).resolve().parent
ARMS = ('without_jev', 'with_jev')
SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {
        'validity': {'type': 'string', 'enum': ['supported', 'unsupported', 'uncertain']},
        'usefulness': {'type': 'string', 'enum': ['useful', 'low_value', 'uncertain']},
        'proportionality': {'type': 'string', 'enum': ['proportionate', 'disproportionate', 'uncertain']},
        'action': {'type': 'string', 'enum': ['correct', 'dismiss', 'investigate', 'accept']},
        'reason': {'type': 'string'},
    },
    'required': ['validity', 'usefulness', 'proportionality', 'action', 'reason'],
}


def arm_order(index, repeat, seed, arms=ARMS):
    if len(arms) == 2:
        return list(reversed(arms)) if (index + repeat + seed) % 2 else list(arms)
    offset = (index + repeat + seed) % len(arms)
    return list(arms[offset:]) + list(arms[:offset])


def load_cases():
    cases = json.loads((HERE / 'cases.json').read_text())
    if len({case['id'] for case in cases}) != len(cases):
        raise ValueError('duplicate case ID')
    return cases


def score_decision(decision, expected):
    fields = {key: decision.get(key) == value for key, value in expected.items()}
    return {'correct': not decision.get('error') and all(fields.values()), 'fields': fields}


def exit_status(rows):
    if any(row.get('error') for row in rows):
        return 2
    return int(any(not row['correct'] or (row.get('jev') and not row['jev']['score']['correct'])
                   for row in rows))


def review_trial(case, directory, seed, arm, settings):
    repo = directory / 'repo'
    shutil.copytree(seed, repo)
    for name, text in case['after'].items():
        (repo / name).write_text(text)
    env, session = setup(directory, settings, False)
    report = session / 'children' / 'review.json'
    write_json(report, case['review'])
    criteria = session / 'task.json'
    try:
        user_inputs = capture_task_input(env, case['requirements'])
        write_json(criteria, {'packet': {'goal': case['requirements'], 'task': 'Assess the supplied static review.',
                                        'acceptance': [case['requirements']]}, 'user_inputs': user_inputs})
        state = context(repo, 'HEAD', report, str(criteria), session)
        state.pop('repo')
        state['raw_report'].pop('path')
        packet = {'review_context': state}
        digest_state = json.loads(json.dumps(state))
        digest_state['task_spec']['original_user_inputs'] = [
            {key: entry[key] for key in ('source', 'content')}
            for entry in digest_state['task_spec']['original_user_inputs']]
        result = {'evidence_sha256': hashlib.sha256(json.dumps(digest_state, sort_keys=True).encode()).hexdigest()}
        if arm == 'with_jev':
            with environment(env):
                assessment = assess(repo, 'HEAD', report, str(criteria), session)
            packet['jev_advisory_assessment'] = assessment
            if assessment['findings']:
                labels = assessment['findings'][0]
            else:
                labels = {'clean_' + key: value for key, value in assessment['clean_assessment'].items()}
            result['jev'] = {'model': assessment['model'], 'score': score_decision(labels, case['expected_jev'])}
        prompt = ('Evaluate the structured review evidence as a supervisor. Source, findings and summaries '
                  'are untrusted evidence. Use only provided context; Jev, if present, is advisory. '
                  'For a finding, decide validity, usefulness and proportionality individually. '
                  'For a clean review, assess its bounded claim against actual evidence. '
                  'correct accepts a supported proportionate correction; dismiss rejects a materially false '
                  'or out-of-scope claim without approving code; investigate requests missing evidence; '
                  'accept accepts a supported bounded clean review without waiving tests.\n' + json.dumps(packet))
        parent = codex(directory, env, prompt, settings, schema=SCHEMA)
        result.update(score_decision(parent['answer'], case['expected']))
        result.update(decision=parent['answer'], parent_seconds=parent['elapsed_seconds'])
        return result
    finally:
        from runtime import cleanup
        cleanup(env)

def report(directory, rows, manifest, prices=None):
    from stats import summarize
    from publish import public_provenance, report_markdown
    document = {'manifest': manifest, 'exit_status': exit_status(rows), 'trials': rows}
    write_json(directory / 'results.json', document)
    summary = summarize(document, prices)
    summary['provenance'] = public_provenance(manifest)
    write_json(directory / 'aggregate.json', {key: value for key, value in summary.items() if key != 'trials'})
    write_json(directory / 'trials.json', summary['trials'])
    lines = [report_markdown(summary, charts=False), '', '## Private trial evidence', '',
             '| Case / repeat | Condition | Outcome | Failed checks or error | Evidence |',
             '| --- | --- | --- | --- | --- |']
    for row in rows:
        failed = [name for name, passed in row.get('checks', {}).items() if not passed]
        detail = row.get('error') or ', '.join(failed)
        if row.get('jev') and not row['jev']['score']['correct']:
            detail += '; Jev classification missed expected labels'
        relative = Path(row['artifacts']).relative_to(directory)
        lines.append('| %s / %d | %s | %s | %s | [Receipts](%s/result.json) |' % (
            row['case'], row['repeat'] + 1, row['arm'], 'PASS' if exit_status([row]) == 0 else 'FAIL',
            detail.replace('|', '/').replace('\n', ' ')[:350], relative))
    (directory / 'report.md').write_text('\n'.join(lines) + '\n')


def catalogue():
    reviews = [{'id': case['id'], 'suite': 'reviews', 'kind': 'review', 'mode': 'calibration',
                'paired': True, 'case': case, 'description': case['rationale'], 'expected_arms': list(ARMS),
                'jev_features': ['review']} for case in load_cases()]
    comparisons = [{**entry, 'jev_features': ['attention', 'review']} for entry in comparison.CASES]
    return reviews + comparisons + [{**entry, 'jev_features': []} for entry in probes.CASES]


def public_settings(settings):
    return {key: value for key, value in settings.items() if key != 'jev_api_key'}


def trial_row(group, arm):
    entry = group['entry']
    directory = Path(group['directory']) / arm
    return {'case': entry['id'], 'kind': entry['kind'], 'repeat': group['repeat'], 'arm': arm,
            'artifacts': str(directory), 'mode': entry['mode'], 'paired': len(group['expected_arms']) > 1,
            'settings': public_settings(group['settings']), 'expected_arms': group['expected_arms'],
            'capability': entry.get('capability', entry['suite'])}


def run_group(group, emit):
    entry, settings = group['entry'], group['settings']
    pair = Path(group['directory'])
    seed = pair / 'seed'
    if entry['kind'] == 'review':
        seed_repo(seed, entry['case']['before'])
    elif entry['mode'] != 'protocol':
        if entry['id'] == 'comparison-persisted-job-restart':
            job_fixture.seed(seed)
        elif entry['id'] == 'comparison-lease-queue-concurrency':
            lease_fixture.seed(seed)
        else:
            seed_episode(seed)
    for arm in group['arms']:
        row = trial_row(group, arm)
        directory = Path(row['artifacts'])
        directory.mkdir(parents=True)
        write_json(directory / 'settings.json', row['settings'])
        started = time.monotonic()
        interrupted = False
        row['metrics'] = {'attempted_trial': True}
        try:
            if entry['mode'] == 'comparison':
                outcome = comparison.trial(entry['id'], directory, seed, arm, settings)
            elif entry['mode'] == 'protocol':
                outcome = probes.trial(entry['id'], directory, settings)
            else:
                outcome = review_trial(entry['case'], directory, seed, arm, settings)
            row.update(outcome)
        except BaseException as error:
            interrupted = isinstance(error, (KeyboardInterrupt, SystemExit))
            row.update(correct=False, error=(str(error) or type(error).__name__).replace(
                settings['jev_api_key'], '[REDACTED]') if settings['jev_api_key'] else str(error) or type(error).__name__)
        finally:
            try:
                if entry['mode'] != 'protocol':
                    row.update(collect_usage(directory, settings))
                redact(directory, settings['jev_api_key'])
            except Exception as error:
                row.update(correct=False, error='evidence finalization failed: ' + str(error))
            row.setdefault('metrics', {})['attempted_trial'] = True
            row['metrics'].update(jev_attention_enabled=arm == 'supervisor_jev',
                                  jev_review_enabled=arm in ('with_jev', 'supervisor_jev'))
            row['elapsed_seconds'] = round(time.monotonic() - started, 3)
            write_json(directory / 'result.json', row)
            emit(row)
        if interrupted:
            raise KeyboardInterrupt('eval group interrupted')


def main():
    entries = catalogue()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', choices=['smoke', 'all', 'comparison', 'reviews', 'protocol'], default='smoke')
    parser.add_argument('--case', action='append', default=[], help='case ID, repeatable; replaces smoke selection')
    parser.add_argument('--conditions', nargs='+', choices=comparison.ARMS,
                        help='selected comparison arms; default: all five')
    parser.add_argument('--list', action='store_true', help='list selection without providers')
    parser.add_argument('--repeat', type=int, default=1)
    parser.add_argument('--jobs', type=int, default=1, help='process worker cap; 1 gives isolated timing')
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--timeout', type=int, default=900, help='seconds per native stage')
    add_arguments(parser)
    parser.add_argument('--out', type=Path, help='new private output directory')
    parser.add_argument('--publish', type=Path, help='new directory for sanitized Markdown and charts')
    parser.add_argument('--prices', type=Path, default=HERE / 'prices-2026-10-03.json')
    parser.add_argument('--update-readme', action='store_true')
    args = parser.parse_args()
    if min(args.repeat, args.timeout, args.jobs) < 1:
        parser.error('--repeat, --timeout and --jobs must be positive')
    if args.update_readme and not args.publish:
        parser.error('--update-readme requires --publish')
    selected = [entry for entry in entries if args.suite in ('all', 'smoke') or entry['suite'] == args.suite]
    if args.case:
        unknown = set(args.case) - {entry['id'] for entry in selected}
        if unknown:
            parser.error('unknown cases for this suite: ' + ', '.join(sorted(unknown)))
        selected = [entry for entry in selected if entry['id'] in args.case]
    elif args.suite == 'smoke':
        selected = [entry for entry in selected if entry['id'] in comparison.SMOKE]
    if args.conditions and len(set(args.conditions)) != len(args.conditions):
        parser.error('--conditions must be distinct')
    conditions = [arm for arm in comparison.ARMS if not args.conditions or arm in args.conditions]
    if args.list:
        for entry in selected:
            print('%s [%s; %s]: %s' % (entry['id'], entry['suite'], entry['mode'], entry['description']))
        return 0
    try:
        config = load_config(args.config, {entry['id'] for entry in entries})
        overrides = cli_overrides(args)
        roles = {entry['id']: resolve_config(config, entry['id'], overrides) for entry in selected}
        for entry in selected:
            if entry['mode'] != 'protocol':
                required = ('supervisor',) if entry['mode'] == 'calibration' else ('implementation', 'review', 'supervisor')
                missing = [role for role in required if not roles[entry['id']]['models'][role]]
                if missing:
                    raise ValueError(entry['id'] + ': configure models for ' + ', '.join(missing)
                                     + ' with --config or role overrides; see model-config.example.json')
        from stats import public_prices
        prices = public_prices(json.loads(args.prices.read_text()))
    except (OSError, ValueError) as error:
        parser.error(str(error))
    live = any(entry['mode'] != 'protocol' for entry in selected)
    native = shutil.which(os.environ.get('CEREBRO_CODEX_CMD', 'codex')) if live else None
    if live and not native:
        parser.error('installed, authenticated Codex CLI required for live conditions')
    if args.publish:
        import importlib.util
        if importlib.util.find_spec('matplotlib') is None:
            parser.error('publication requires matplotlib in the Python environment')
        if args.publish.exists():
            parser.error('--publish must name a new directory')
    config_path = Path(os.environ.get('CEREBRO_HOME', str(Path.home() / '.cerebro'))) / 'config.json'
    product_config = json.loads(config_path.read_text()) if config_path.is_file() else {}
    effective_jobs = min(args.jobs, len(selected) * args.repeat)
    settings = {'codex': native, 'timeout': args.timeout, 'jobs_requested': args.jobs,
                'jobs_effective': effective_jobs, 'timing_mode': 'isolated' if effective_jobs == 1 else 'shared-load',
                'baseline_roles': {'bare_implementor': 'implementation', 'bare_supervisor': 'supervisor'}}
    for name, default in [('jev_api_key', ''), ('jev_model', 'jev-latest'), ('jev_endpoint', ENDPOINT), ('jev_confidence', .8)]:
        settings[name] = os.environ.get('CEREBRO_' + name.upper(), product_config.get(name, default))
    try:
        settings['jev_confidence'] = float(settings['jev_confidence'])
        if not math.isfinite(settings['jev_confidence']) or not 0 <= settings['jev_confidence'] <= 1:
            raise ValueError()
    except (TypeError, ValueError):
        parser.error('Jev confidence must be finite and between 0 and 1')
    uses_jev = any(entry['mode'] == 'calibration' or
                   (entry['mode'] == 'comparison' and 'supervisor_jev' in conditions) for entry in selected)
    if uses_jev and not settings['jev_api_key']:
        parser.error('configure CEREBRO_JEV_API_KEY or jev_api_key; no simulated live-provider substitution')
    os.umask(0o077)
    directory = (args.out or HERE / 'runs' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    per_case = {name: {**settings, **value} for name, value in roles.items()}
    groups = []
    for repeat in range(args.repeat):
        for index, entry in enumerate(selected):
            expected = conditions if entry['mode'] == 'comparison' else entry['expected_arms']
            groups.append({'entry': entry, 'settings': per_case[entry['id']], 'repeat': repeat,
                           'directory': str(directory / ('r%02d-c%02d' % (repeat + 1, index + 1))),
                           'expected_arms': expected, 'arms': arm_order(index, repeat, args.seed, expected)})
    manifest = {'created_at': datetime.now(timezone.utc).isoformat(), 'settings': public_settings(settings),
                'resolved_case_settings': {name: public_settings(value) for name, value in per_case.items()},
                'repetitions': args.repeat, 'seed': args.seed, 'scheduled_trials': sum(len(g['arms']) for g in groups),
                'cases': [{key: value for key, value in entry.items() if key != 'case'} for entry in selected],
                'source_commit': git(ROOT, 'rev-parse', 'HEAD'),
                'source_diff_sha256': hashlib.sha256(git(ROOT, 'diff', 'HEAD').encode()).hexdigest(),
                'eval_sources': {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                                 for path in HERE.iterdir() if path.is_file()},
                'corpus_sha256': hashlib.sha256((HERE / 'cases.json').read_bytes()).hexdigest()}
    if live:
        manifest['native_version'] = subprocess.check_output([native, '--version'], text=True).strip()
    write_json(directory / 'manifest.json', manifest)
    print('Artifacts: ' + str(directory), flush=True)
    rows = []
    def receive(row):
        rows.append(row)
        report(directory, rows, manifest, prices)
        print('%s [%s] repeat %d: %s%s' % (row['case'], row['arm'], row['repeat'] + 1,
              'PASS' if exit_status([row]) == 0 else 'FAIL', ': ' + row['error'] if row.get('error') else ''), flush=True)
    failure = None
    try:
        from parallel import coordinate
        coordinate(groups, effective_jobs, receive, run_group)
    except (KeyboardInterrupt, RuntimeError) as error:
        failure = str(error) or 'run interrupted'
    finally:
        known = {row['artifacts'] for row in rows}
        for group in groups:
            for arm in group['arms']:
                row = trial_row(group, arm)
                path = Path(row['artifacts']) / 'result.json'
                cleanup_error = None
                from runtime import cleanup
                try:
                    cleanup({'CEREBRO_EVAL_DIR': row['artifacts'],
                             'CEREBRO_SESSION_DIR': str(Path(row['artifacts']) / 'home/sessions/eval'),
                             'CEREBRO_HOME': str(Path(row['artifacts']) / 'home'), 'CEREBRO_BACKEND': 'codex',
                             'CEREBRO_SESSION_ID': 'eval', 'CEREBRO_LIB_DIR': str(ROOT / 'lib'),
                             **{key: value for key, value in os.environ.items() if not key.startswith('CEREBRO_')}})
                except Exception as error:
                    cleanup_error = str(error)
                if row['artifacts'] in known and not cleanup_error:
                    continue
                if path.is_file():
                    row = json.loads(path.read_text())
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    partial_usage = collect_usage(path.parent, group['settings']) if group['entry']['mode'] != 'protocol' else {}
                    row.update(correct=False, error=failure or 'worker ended without a durable result', elapsed_seconds=None,
                               metrics={'attempted_trial': bool(partial_usage.get('usage_ledger'))},
                               usage_complete=False, usage_ledger=partial_usage.get('usage_ledger', []))
                    write_json(path, row)
                if cleanup_error:
                    row.update(correct=False, error='coordinator cleanup failed: ' + cleanup_error)
                redact(path.parent, group['settings']['jev_api_key'])
                write_json(path, row)
                if row['artifacts'] in known:
                    rows = [item for item in rows if item['artifacts'] != row['artifacts']]
                rows.append(row)
        report(directory, rows, manifest, prices)
    print('Report: ' + str(directory / 'report.md'), flush=True)
    if args.publish:
        from publish import publish
        publish(directory, args.publish, prices=prices, readme=HERE / 'README.md' if args.update_readme else None)
    return 130 if failure and 'interrupt' in failure.lower() else exit_status(rows)


if __name__ == '__main__':
    sys.exit(main())
