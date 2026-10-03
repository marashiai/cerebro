"""Reproducible protocol and fault evals through real Cerebro and native Codex.

These probes use a scripted loopback provider. Their results measure transport,
permissions and lifecycle contracts, not OpenAI model quality or Jev accuracy.
"""

import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import time

from fixtures import git, seed_repo
from probes_provider import Provider, outputs
from runtime import CLI, LIB, cleanup, environment, guarded_options, setup, toml, write_json


CASES = [
    {'id': 'quiet-reconnect', 'suite': 'lifecycle', 'description':
     'A child stays quiet for 35 seconds; reconnecting the parent waits for the same durable job.'},
    {'id': 'provider-token-limit', 'suite': 'failures', 'description':
     'An injected provider token-rate HTTP 429 reaches a failed durable handoff.'},
    {'id': 'provider-quota-exhaustion', 'suite': 'failures', 'description':
     'An injected provider quota HTTP 429 reaches a failed durable handoff.'},
    {'id': 'provider-interrupted-stream', 'suite': 'failures', 'description':
     'A provider stream closes after partial output without a completion event.'},
    *[{'id': 'jev-' + role + '-' + fault, 'suite': 'failures', 'description':
       'The Jev ' + role + ' path preserves an explicit failure for an injected ' + fault + ' response.'}
      for role in ('scope', 'review') for fault in ('http', 'malformed')],
    {'id': 'guarded-executors', 'suite': 'boundaries', 'description':
     'Native parent/reviewer direct and code-mode executors reject calls; implementation shell works.'},
    {'id': 'user-cancel', 'suite': 'intervention', 'description':
     'User cancellation stops an active child, preserves its work and produces exit 130.'},
    {'id': 'answer-resume', 'suite': 'intervention', 'description':
     'An answer resumes the exact native conversation and retains the child\'s existing work.'},
]
for _case in CASES:
    _case.update(mode='protocol', paired=False)

REQUIREMENTS = 'Write only proof.txt in this disposable repository. Preserve user-work.txt. Do not commit or publish.'


class Process:
    def __init__(self, argv, env, directory, name, stdin=''):
        self.prefix = directory / name
        self.started = time.monotonic()
        self.stdout = self.prefix.with_suffix('.stdout.jsonl').open('w')
        self.stderr = self.prefix.with_suffix('.stderr').open('w')
        self.proc = subprocess.Popen(argv, env=env, cwd=directory, stdin=subprocess.PIPE,
                                     stdout=self.stdout, stderr=self.stderr, text=True,
                                     start_new_session=True)
        self.proc.stdin.write(stdin)
        self.proc.stdin.close()

    def finish(self, timeout=90):
        try:
            self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.stop()
            raise RuntimeError('protocol stage exceeded its deadline; see ' + str(self.prefix)) from None
        self.stdout.close()
        self.stderr.close()
        result = {'exit_code': self.proc.returncode,
                  'elapsed_seconds': round(time.monotonic() - self.started, 3)}
        write_json(self.prefix.with_suffix('.process.json'), result)
        return result

    def stop(self):
        if self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid, signal.SIGKILL)
                self.proc.wait(timeout=5)
        self.stdout.close()
        self.stderr.close()

    def response(self):
        self.finish()
        messages = [json.loads(line) for line in self.prefix.with_suffix('.stdout.jsonl').read_text().splitlines()]
        if len(messages) != 1 or 'result' not in messages[0]:
            raise RuntimeError('missing MCP response; see ' + str(self.prefix))
        result = messages[0]['result']
        value = json.loads(result['content'][0]['text'])
        value['mcp_is_error'] = result['isError']
        write_json(self.prefix.with_suffix('.response.json'), value)
        return value


def start_command(directory, env, argv, name, processes):
    request = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
               'params': {'name': 'command', 'arguments': {'argv': argv}}}
    result = Process([sys.executable, str(LIB / 'python/command_server.py'), 'supervisor', str(CLI)],
                     env, directory, name, json.dumps(request) + '\n')
    processes.append(result)
    return result


def start_parent(directory, env, settings, name, prompt, processes):
    with environment(env):
        options = guarded_options(settings['codex'], 'supervisor', str(directory), env['CEREBRO_SESSION_DIR'])
    argv = [settings['codex'], '--no-daemon', '--strict-config', *options,
            '-c', 'project_doc_max_bytes=0',
            '-c', 'model_reasoning_effort=' + toml(settings['efforts']['supervisor']),
            'exec', '--ephemeral', '--skip-git-repo-check', '--json', '--color', 'never',
            '--model', settings['models']['supervisor'], '-']
    result = Process(argv, env, directory, name, prompt)
    processes.append(result)
    return result


def prepare(directory, settings, provider):
    native_home = directory / 'native-home'
    native_home.mkdir()
    wrapper = directory / 'codex-protocol'
    config = {'name': 'Scripted Protocol Probe', 'base_url': provider.endpoint + '/v1',
              'wire_api': 'responses', 'requires_openai_auth': False,
              'request_max_retries': 0, 'stream_max_retries': 0, 'stream_idle_timeout_ms': 20000}
    wrapper.write_text('#!/usr/bin/env bash\nexec ' + shlex.quote(settings['codex'])
                       + ' -c model_provider="cerebro-protocol-probe" -c '
                       + shlex.quote('model_providers.cerebro-protocol-probe=' + toml(config)) + ' "$@"\n')
    wrapper.chmod(0o700)
    local = {**settings, 'codex': str(wrapper), 'jev_api_key': 'protocol-probe-local-only',
             'jev_model': 'scripted-probe', 'jev_endpoint': provider.endpoint + '/jev',
             'jev_confidence': 0.8, 'timeout': 90}
    isolated = {key: value for key, value in os.environ.items()
                if not key.startswith(('CEREBRO_', 'OPENAI', 'ANTHROPIC', 'AZURE', 'GEMINI', 'GOOGLE'))}
    isolated['CODEX_HOME'] = str(native_home)
    with environment(isolated):
        env, session = setup(directory, local, provider.case.startswith('jev-'), REQUIREMENTS)
    write_json(directory / 'protocol-config.json', {
        'provider': 'scripted-loopback', 'backend': 'codex', 'real_model_inference': False,
        'retry_policy': {'request_max_retries': 0, 'stream_max_retries': 0},
        'quiet_seconds': 35 if provider.case == 'quiet-reconnect' else None,
        'native_version': subprocess.check_output([settings['codex'], '--version'], text=True).strip()})
    return env, session, local


def jobs(session):
    result = []
    for path in sorted((session / 'detached-jobs').glob('*.json')):
        job = json.loads(path.read_text())
        if not job.get('id') or not job.get('status'):
            continue
        value = Path(job['status']).read_text().strip()
        result.append({**job, 'exit_code': int(value) if value.lstrip('-').isdigit() else None})
    return result


def child_records(session):
    path = session / 'child-sessions.json'
    return list(json.loads(path.read_text()).values()) if path.exists() else []


def jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def child_events(session):
    return [event for path in (session / 'children').glob('*.jsonl')
            if not path.name.endswith(('.jev.jsonl', '.scope.jsonl')) for event in jsonl(path)]


def response_values(value):
    result = []
    if isinstance(value, dict):
        if 'exit_code' in value and 'text' in value:
            result.append(value)
        for nested in value.values():
            result.extend(response_values(nested))
    elif isinstance(value, list):
        for nested in value:
            result.extend(response_values(nested))
    elif isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return result
        if isinstance(parsed, (dict, list)):
            result.extend(response_values(parsed))
    return result


def terminal_failure(response, durable):
    return (response.get('state') == 'completed' and response.get('mcp_is_error') is True
            and type(response.get('exit_code')) is int and response['exit_code'] != 0
            and len(durable) == 1 and durable[0]['exit_code'] == response['exit_code']
            and bool(response.get('job_id')) and response['job_id'] == durable[0].get('id'))


def native_failure_matches(case, events):
    failures = [event['error'] for event in events if event.get('type') == 'turn.failed']
    if case == 'provider-token-limit':
        return any(error.get('codexErrorInfo') == {'responseTooManyFailedAttempts': {'httpStatusCode': 429}}
                   for error in failures)
    if case == 'provider-quota-exhaustion':
        return any(error.get('codexErrorInfo') == 'usageLimitExceeded' for error in failures)
    if case == 'provider-interrupted-stream':
        return any('stream disconnected before completion' in error.get('message', '') for error in failures)
    raise ValueError('no native failure contract for ' + case)


def guarded_checks(records, role):
    requests = [record for record in records if record.get('kind') == 'native_request'
                and record.get('role') == role]
    results = [outputs(request['body']) for request in requests]
    code = json.dumps(results[1][-1]) if len(results) > 1 and results[1] else ''
    direct = json.dumps(results[2][-1]) if len(results) > 2 and results[2] else ''
    reads = response_values(results[3][-1]) if len(results) > 3 and results[3] else []
    return {role + '_code_mode_rejected': 'code-mode host is disabled' in code,
            role + '_direct_executor_rejected': ('exec_command' in direct and
                any(word in direct.lower() for word in ('unsupported', 'unknown', 'unrecognized', 'disabled', 'unavailable'))),
            role + '_allowed_read_succeeded': any(value['exit_code'] == 0 and 'PROBE_PROOF' in value['text']
                                                  for value in reads)}


def content_is(path, expected):
    return path.is_file() and path.read_text() == expected


def wait_boundary(event, name):
    if not event.wait(45):
        raise RuntimeError('native runtime did not reach ' + name + '; inspect the saved process and provider logs')


def descendants(pid):
    rows = subprocess.check_output(['ps', '-axo', 'pid=,ppid='], text=True).splitlines()
    children = {}
    for row in rows:
        child, parent = map(int, row.split())
        children.setdefault(parent, []).append(child)
    found, pending = [], [pid]
    while pending:
        added = children.get(pending.pop(), [])
        found.extend(added)
        pending.extend(added)
    return found


def processes_stopped(pids):
    for pid in pids:
        result = subprocess.run(['ps', '-p', str(pid), '-o', 'stat='], text=True, capture_output=True)
        if result.stdout.strip() and not result.stdout.strip().startswith('Z'):
            return False
    return True


def quiet_reconnect(directory, env, settings, session, provider, processes):
    original = start_parent(directory, env, settings, 'parent-original',
                            'PROBE_PARENT: delegate the isolated proof task through Cerebro.', processes)
    wait_boundary(provider.worker_started, 'the quiet worker request')
    quiet_start = time.monotonic()
    provider.release.wait(35)
    quiet_duration = time.monotonic() - quiet_start
    checks = {'quiet_for_35_seconds': quiet_duration >= 35,
              'original_parent_remained_waiting': original.proc.poll() is None and provider.counts['parent'] == 1}
    durable = jobs(session)
    if len(durable) != 1:
        return {**checks, 'one_durable_job': False}
    job = durable[0]
    provider.job_id = job['id']
    original.stop()
    original.finish()
    checks['disconnect_did_not_finish_child'] = jobs(session)[0]['exit_code'] is None
    reconnected = start_parent(directory, env, settings, 'parent-reconnected',
                              'PROBE_RECONNECTED: wait for existing job ' + job['id'] + '.', processes)
    wait_boundary(provider.parent_reconnected, 'the reconnected native parent')
    provider.release.set()
    outcome = reconnected.finish()
    repeat = start_command(directory, env, ['wait', job['id']], 'durable-repeat', processes).response()
    requests = [record for record in provider.records if record.get('kind') == 'native_request'
                and record.get('role') == 'reconnected']
    handoffs = [item for item in response_values(outputs(requests[1]['body'])) if 'job_id' in item] if len(requests) > 1 else []
    checks.update(reconnected_parent_completed=outcome['exit_code'] == 0,
                  exactly_one_terminal_wakeup=len(requests) == 2 and bool(handoffs)
                    and all(item['job_id'] == job['id'] and item['state'] == 'completed'
                            and item['exit_code'] == 0 for item in handoffs),
                  one_child_and_job=len(child_records(session)) == len(jobs(session)) == 1,
                  durable_terminal_replayed=repeat.get('job_id') == job['id']
                    and repeat.get('state') == 'completed' and repeat.get('exit_code') == 0,
                  child_completed_once=provider.counts['worker'] == 2
                    and content_is(directory / 'repo/proof.txt', 'PROBE_PROOF'))
    return checks


def fault(directory, env, session, provider, processes):
    review = provider.case.startswith('jev-review-')
    if review:
        criteria = session / 'plans/probe-review.md'
        criteria.write_text('PROBE_REVIEW: assess the retained proof and user work only.')
        argv = ['review', str(directory / 'repo'), '--base', 'HEAD', '--criteria-file', str(criteria)]
    else:
        argv = ['execute', str(directory / 'repo'), '--prompt', 'PROBE_WORKER: write proof.txt.',
                '--watch' if provider.case.startswith('jev-') else '--no-watch']
    response = start_command(directory, env, argv, 'fault', processes).response()
    checks = {'durable_failure_matches_handoff': terminal_failure(response, jobs(session)),
              'injection_reached': any(record['kind'] == 'injected_fault' for record in provider.records)}
    if provider.case.startswith('provider-'):
        checks['native_reports_injected_cause'] = native_failure_matches(provider.case, child_events(session))
        checks['native_turn_failed'] = any(event.get('type') == 'turn.failed' for event in child_events(session))
        checks['no_completed_proof'] = not (directory / 'repo/proof.txt').exists()
    else:
        traces = [jsonl(path) for path in session.rglob('*.jev.jsonl')]
        pairs = [(request, reply) for trace in traces for request in trace for reply in trace
                 if request.get('type') == 'request' and reply.get('type') == 'response'
                 and request.get('request_id') == reply.get('request_id')]
        expected = 'Jev HTTP 429' if provider.case.endswith('-http') else 'invalid typed classification'
        checks['logged_causal_jev_error'] = bool(pairs) and all(expected in pair[1].get('error', '') for pair in pairs)
        checks['jev_failure_reaches_caller'] = ('Jev assessment failed' if review else 'Jev watch stopped') in response.get('text', '')
        checks['no_successful_assessment'] = not list(session.rglob('*.assessment.json'))
        if review:
            reports = [path for path in (session / 'children').glob('*.md') if 'PROBE_REVIEW_DONE' in path.read_text()]
            checks['original_review_retained'] = len(reports) == 1 and 'Jev review assessment' not in reports[0].read_text()
        checks['jev_trace_private'] = bool(traces) and all(path.stat().st_mode & 0o777 == 0o600
                                                         for path in session.rglob('*.jev.jsonl'))
    return checks


def guards(directory, env, settings, session, provider, processes):
    implementation = start_command(directory, env, ['execute', str(directory / 'repo'), '--prompt',
        'PROBE_WORKER: write proof.txt.', '--no-watch'], 'implementation', processes).response()
    parent = start_parent(directory, env, settings, 'parent-guards', 'PROBE_PARENT: inspect the proof.', processes).finish()
    criteria = session / 'plans/probe-review.md'
    criteria.write_text('PROBE_REVIEW: inspect proof.txt without running commands.')
    review = start_command(directory, env, ['review', str(directory / 'repo'), '--base', 'HEAD',
        '--criteria-file', str(criteria)], 'review-guards', processes).response()
    checks = {**guarded_checks(provider.records, 'parent'), **guarded_checks(provider.records, 'review')}
    checks.update(parent_completed=parent['exit_code'] == 0,
                  implementation_completed=implementation['exit_code'] == 0,
                  reviewer_completed=review['exit_code'] == 0,
                  implementation_shell_executed=any(event.get('type') == 'item.completed'
                    and event.get('item', {}).get('type') == 'command_execution'
                    and event['item'].get('exitCode') == 0 for event in child_events(session))
                    and content_is(directory / 'repo/proof.txt', 'PROBE_PROOF'))
    return checks


def cancel(directory, env, session, provider, processes):
    pending = start_command(directory, env, ['execute', str(directory / 'repo'), '--prompt',
        'PROBE_WORKER: write proof.txt.', '--no-watch'], 'cancelled-work', processes)
    wait_boundary(provider.worker_after_tool, 'the worker after its first real shell command')
    durable = jobs(session)
    if len(durable) != 1:
        return {'one_durable_job': False}
    job = durable[0]
    owned_pids = [job['pid'], *descendants(job['pid'])]
    write_json(directory / 'cancel-owned-pids.json', owned_pids)
    cancelled = start_command(directory, env, ['cancel', job['id']], 'user-cancel', processes).response()
    response = pending.response()
    provider.release.set()
    return {'cancel_accepted': cancelled['exit_code'] == 0,
            'terminal_cancellation': terminal_failure(response, jobs(session)) and response['exit_code'] == 130,
            'retained_work': content_is(directory / 'repo/proof.txt', 'PROBE_PROOF'),
            'no_late_work': not (directory / 'repo/late.txt').exists(),
            'owned_children_stopped': bool(owned_pids) and processes_stopped(owned_pids)}


def answer(directory, env, session, provider, processes):
    initial = start_command(directory, env, ['execute', str(directory / 'repo'), '--prompt',
        'PROBE_WORKER: write proof.txt then ask for authorization to append.', '--no-watch'],
        'question', processes).response()
    children = child_records(session)
    if len(children) != 1 or not children[0].get('id'):
        return {'one_resumable_child': False}
    child = children[0]
    checks = {'question_surfaced': initial['exit_code'] == 0 and 'QUESTION:' in initial['text'],
              'prior_work_exists': content_is(directory / 'repo/proof.txt', 'PROBE_PROOF')}
    resumed = start_command(directory, env, ['answer', child['id'], 'Append :ANSWER to the existing proof.'],
                            'answer', processes).response()
    events = child_events(session)
    threads = [event['thread_id'] for event in events if event.get('type') == 'thread.started']
    current = child_records(session)
    checks.update(answer_completed=resumed['exit_code'] == 0 and 'PROBE_WORKER_DONE' in resumed['text'],
                  answer_delivered=any(record.get('kind') == 'native_request' and record.get('role') == 'worker'
                    and record.get('call') == 3 and 'Append :ANSWER to the existing proof.' in json.dumps(record['body'])
                    for record in provider.records),
                  exact_native_conversation=len(threads) == 2 and set(threads) == {child['id']},
                  one_retained_child=len(current) == 1 and current[0]['id'] == child['id'],
                  retained_and_extended_work=content_is(directory / 'repo/proof.txt', 'PROBE_PROOF:ANSWER'),
                  both_jobs_completed=len(jobs(session)) == 2 and all(job['exit_code'] == 0 for job in jobs(session)))
    return checks


def trial(name, directory, settings):
    if name not in {case['id'] for case in CASES}:
        raise ValueError('unknown protocol probe: ' + name)
    directory.mkdir(parents=True, exist_ok=True)
    seed_repo(directory / 'repo', {'AGENTS.md': REQUIREMENTS + '\n', 'user-work.txt': 'PRESERVE_USER_WORK\n'})
    head = git(directory / 'repo', 'rev-parse', 'HEAD')
    processes, env = [], None
    started = time.monotonic()
    with Provider(directory, name) as provider:
        try:
            env, session, local = prepare(directory, settings, provider)
            if name == 'quiet-reconnect':
                checks = quiet_reconnect(directory, env, local, session, provider, processes)
            elif name == 'guarded-executors':
                checks = guards(directory, env, local, session, provider, processes)
            elif name == 'user-cancel':
                checks = cancel(directory, env, session, provider, processes)
            elif name == 'answer-resume':
                checks = answer(directory, env, session, provider, processes)
            else:
                checks = fault(directory, env, session, provider, processes)
            if provider.errors:
                raise RuntimeError('scripted provider error; see provider.jsonl: ' + '; '.join(provider.errors))
            durable = jobs(session)
            checks.update(all_jobs_terminal=bool(durable) and all(job['exit_code'] is not None for job in durable),
                          user_work_preserved=content_is(directory / 'repo/user-work.txt', 'PRESERVE_USER_WORK\n'),
                          requirements_preserved=content_is(session / 'spec.md', REQUIREMENTS),
                          checkout_preserved=git(directory / 'repo', 'rev-parse', 'HEAD') == head
                            and git(directory / 'repo', 'branch', '--show-current') == 'main')
            native_errors = [event['error'] for event in child_events(session) if event.get('type') == 'turn.failed']
            provider_messages = [record['body']['error']['message'] for record in provider.records
                                 if record.get('kind') == 'injected_fault' and 'body' in record]
            result = {'correct': all(checks.values()), 'checks': checks, 'mode': 'protocol',
                      'provider': 'scripted-loopback', 'backend': 'codex', 'real_model_inference': False,
                      'elapsed_seconds': round(time.monotonic() - started, 3),
                      'metrics': {'native_requests_by_role': dict(provider.counts),
                                  'native_retries': max(0, provider.counts['worker'] - 1) if name.startswith('provider-') else None,
                                  'native_errors': native_errors,
                                  'provider_message_preserved': all(message in json.dumps(native_errors)
                                    for message in provider_messages) if provider_messages else None,
                                  'job_exit_codes': [job['exit_code'] for job in durable]},
                      'evidence': ['provider.jsonl', 'protocol-config.json', 'home/sessions/eval/detached-jobs']}
            write_json(directory / 'probe-result.json', result)
            return result
        finally:
            for process in processes:
                process.stop()
            if env:
                cleanup(env)
