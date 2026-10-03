"""Run isolated eval sessions through Cerebro and the installed Codex CLI."""

from contextlib import contextmanager
import json
import hashlib
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / 'lib'
CLI = ROOT / 'bin' / 'cerebro'
sys.path.insert(0, str(LIB / 'python'))
from codex_launch import guarded_options, toml
from model_config import ROLES

ROLE_GROUPS = {role: group for group, roles in {
    'implementation': ('execute', 'apply-review', 'doc-write'),
    'review': ('review', 'verify', 'audit', 'improve'),
    'supervisor': ('supervisor',),
    'baseline': ('baseline',),
}.items() for role in roles}


def role_model(role, models):
    return models[ROLE_GROUPS[role]]


@contextmanager
def environment(values):
    original = dict(os.environ)
    os.environ.clear()
    os.environ.update(values)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(original)


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + '\n')


def file_hashes(repo):
    return {str(path.relative_to(repo)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in repo.rglob('*') if path.is_file() and not path.is_symlink()
            and not any(part in ('.git', '__pycache__') for part in path.relative_to(repo).parts)}


def process(argv, env, cwd, prefix, stdin='', timeout=600):
    started = time.monotonic()
    with prefix.with_suffix('.stdout.jsonl').open('w') as out, prefix.with_suffix('.stderr').open('w') as err:
        proc = subprocess.Popen(argv, cwd=cwd, env=env, text=True, stdin=subprocess.PIPE,
                                stdout=out, stderr=err, start_new_session=True)
        try:
            proc.communicate(stdin, timeout=timeout)
        except BaseException as error:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
            if isinstance(error, subprocess.TimeoutExpired):
                raise RuntimeError('native stage exceeded %ss; see %s' % (timeout, prefix)) from None
            raise
    outcome = {'exit_code': proc.returncode, 'elapsed_seconds': round(time.monotonic() - started, 3)}
    write_json(prefix.with_suffix('.process.json'), outcome)
    if proc.returncode:
        raise RuntimeError('process exited %s; see %s' % (proc.returncode, prefix.with_suffix('.stderr')))
    return outcome


def setup(directory, settings, watch, requirements):
    session = directory / 'home' / 'sessions' / 'eval'
    for child in ('children', 'plans'):
        (session / child).mkdir(parents=True, exist_ok=True)
    write_json(session / 'metadata.json', {'id': 'eval', 'backend': 'codex', 'role': 'supervisor'})
    (session / 'spec.md').write_text(requirements)
    native = directory / 'codex-eval'
    choices = ''.join('|'.join(role for role, kind in ROLE_GROUPS.items() if kind == group)
                      + ') eval_effort=' + shlex.quote(toml(settings['efforts'][group])) + ' ;;\n'
                      for group in ROLES)
    native.write_text('#!/usr/bin/env bash\ncase "${CEREBRO_CHILD_ROLE:?missing child role}" in\n'
                      + choices + '*) echo "Unknown eval child role" >&2; exit 2 ;;\nesac\nexec '
                      + shlex.quote(sys.executable) + ' '
                      + shlex.quote(str(Path(__file__).with_name('native.py'))) + ' '
                      + shlex.quote(settings['codex']) + ' -c "model_reasoning_effort=$eval_effort" "$@"\n')
    native.chmod(0o700)
    env = {key: value for key, value in os.environ.items() if not key.startswith('CEREBRO_')}
    env.update(CEREBRO_HOME=str(directory / 'home'), CEREBRO_SESSION_DIR=str(session),
               CEREBRO_SESSION_ID='eval', CEREBRO_BACKEND='codex', CEREBRO_LIB_DIR=str(LIB),
               CEREBRO_CODEX_CMD=str(native), CEREBRO_MODEL=settings['models']['implementation'],
               CEREBRO_REVIEW_MODEL=settings['models']['review'], CEREBRO_SUPERVISOR_MODEL=settings['models']['supervisor'],
               CEREBRO_JEV_ENABLED=str(int(watch)), CEREBRO_JEV_API_KEY=settings['jev_api_key'],
               CEREBRO_JEV_MODEL=settings['jev_model'], CEREBRO_JEV_ENDPOINT=settings['jev_endpoint'],
               CEREBRO_JEV_CONFIDENCE=str(settings['jev_confidence']), CEREBRO_TIMEOUT=str(settings['timeout']),
               CEREBRO_PAIR_IDLE='0', CEREBRO_PAIR_STALL_RETRIES='0')
    env.update(CEREBRO_EVAL_DIR=str(directory), CEREBRO_EVAL_REPO=str(directory / 'repo'))
    # Use production materialization rather than maintaining a second MCP configuration.
    process(['bash', '-c', 'source "$1/backend.sh"; backend_supervisor_config supervisor',
             'cerebro-eval', str(LIB)], env, directory, directory / 'configure', timeout=20)
    return env, session


def codex(directory, env, prompt, settings, *, schema=None, supervisor=False):
    session = env['CEREBRO_SESSION_DIR']
    with environment(env):
        options = guarded_options(settings['codex'], 'supervisor', str(directory), session)
    if supervisor:
        instructions = (LIB / 'payloads/skills/cerebro-supervisor/SKILL.md').read_text()
    else:
        # Calibration measures a decision from identical evidence, without extra searches.
        options += ['-c', 'mcp_servers.cerebro.enabled=false']
        instructions = 'Use only the evidence supplied in the prompt. Return the requested JSON decision.'
    options += ['-c', 'developer_instructions=' + toml(instructions),
                '-c', 'model_reasoning_effort=' + toml(settings['efforts']['supervisor']),
                '-c', 'project_doc_max_bytes=0']
    output = directory / 'answer.json' if schema else directory / 'answer.md'
    argv = [settings['codex'], '--no-daemon', '--strict-config', *options, 'exec',
            '--ephemeral', '--skip-git-repo-check', '--json', '--color', 'never',
            '--model', settings['models']['supervisor'], '--output-last-message', str(output)]
    if schema:
        schema_path = directory / 'output-schema.json'
        write_json(schema_path, schema)
        argv += ['--output-schema', str(schema_path)]
    argv += ['-']
    (directory / 'prompt.txt').write_text(prompt)
    result = process(argv, env, directory, directory / 'parent', prompt, settings['timeout'])
    result['usage'] = {}
    for line in (directory / 'parent.stdout.jsonl').read_text().splitlines():
        event = json.loads(line)
        if event.get('type') == 'turn.completed':
            for key, value in event.get('usage', {}).items():
                if isinstance(value, (int, float)):
                    result['usage'][key] = result['usage'].get(key, 0) + value
    if not output.is_file():
        raise RuntimeError('Codex completed without a final answer')
    result['answer'] = json.loads(output.read_text()) if schema else output.read_text()
    return result


def command(directory, env, argv, timeout, *, prefix='delegation', stdin=''):
    request = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
               'params': {'name': 'command', 'arguments': {'argv': argv, 'stdin': stdin}}}
    process([sys.executable, str(LIB / 'python/command_server.py'), 'supervisor', str(CLI)],
            env, directory, directory / prefix, json.dumps(request) + '\n', timeout)
    message = json.loads((directory / (prefix + '.stdout.jsonl')).read_text())
    result = message['result']
    text = result['content'][0]['text']
    try:
        response = json.loads(text)
    except ValueError:
        raise RuntimeError('Cerebro command failed: ' + text) from None
    write_json(directory / ('first-response.json' if prefix == 'delegation' else prefix + '.response.json'), response)
    return response


def cleanup(env):
    """Cancel only still-running jobs belonging to this eval session."""
    session = Path(env['CEREBRO_SESSION_DIR'])
    for path in (session / 'detached-jobs').glob('*.json'):
        job = json.loads(path.read_text())
        if not job.get('id') or not job.get('status'):
            continue
        status = Path(job['status'])
        if status.exists() and status.read_text().strip().lstrip('-').isdigit():
            continue
        result = subprocess.run([str(CLI), 'cancel', job['id']], env=env, capture_output=True, timeout=15)
        if result.returncode:
            raise RuntimeError('could not cancel eval-owned job ' + job['id'])


def redact(directory, secret):
    if not secret:
        return
    for path in directory.rglob('*'):
        if not path.is_file() or path.is_symlink() or '.git' in path.parts:
            continue
        try:
            text = path.read_text()
        except UnicodeError:
            continue
        if secret in text:
            path.write_text(text.replace(secret, '[REDACTED]'))
