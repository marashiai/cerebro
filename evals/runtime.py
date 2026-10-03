"""Run isolated eval sessions through Cerebro and the installed Codex CLI."""

from contextlib import contextmanager
import json
import hashlib
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / 'lib'
CLI = ROOT / 'bin' / 'cerebro'
sys.path.insert(0, str(LIB / 'python'))
from codex_launch import supervisor_options, toml

ROLE_GROUPS = {'execute': 'implementation', 'review': 'review', 'supervisor': 'supervisor',
               'implementation': 'implementation'}


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
    temporary = path.with_name('.' + path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def file_hashes(repo):
    return {str(path.relative_to(repo)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in repo.rglob('*') if path.is_file() and not path.is_symlink()
            and not any(part in ('.git', '__pycache__') for part in path.relative_to(repo).parts)}


def process(argv, env, cwd, prefix, stdin='', timeout=600):
    started = time.monotonic()
    failure = None
    with prefix.with_suffix('.stdout.jsonl').open('w') as out, prefix.with_suffix('.stderr').open('w') as err:
        proc = subprocess.Popen(argv, cwd=cwd, env=env, text=True, stdin=subprocess.PIPE,
                                stdout=out, stderr=err, start_new_session=True)
        running = prefix.with_suffix('.process-running.json')
        identity = subprocess.run(['ps', '-p', str(proc.pid), '-o', 'lstart='], capture_output=True, text=True).stdout.strip()
        write_json(running, {'pid': proc.pid, 'identity': identity, 'started_at': time.time(),
                             'owner': 'cerebro-eval', 'trial_directory': str(prefix.parent.resolve()),
                             'prefix': prefix.name})
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
            failure = error
    outcome = {'exit_code': proc.returncode, 'elapsed_seconds': round(time.monotonic() - started, 3),
               'timed_out': isinstance(failure, subprocess.TimeoutExpired)}
    write_json(prefix.with_suffix('.process.json'), outcome)
    running.unlink(missing_ok=True)
    if isinstance(failure, subprocess.TimeoutExpired):
        raise RuntimeError('native stage exceeded %ss; see %s' % (timeout, prefix)) from None
    if failure:
        raise failure
    if proc.returncode:
        raise RuntimeError('process exited %s; see %s' % (proc.returncode, prefix.with_suffix('.stderr')))
    return outcome


def setup(directory, settings, watch):
    session = directory / 'home' / 'sessions' / 'eval'
    for child in ('children',):
        (session / child).mkdir(parents=True, exist_ok=True)
    write_json(session / 'metadata.json', {'id': 'eval', 'backend': 'codex', 'role': 'supervisor'})
    native = directory / 'codex-eval'
    native.write_text('#!/usr/bin/env bash\nexec '
                      + shlex.quote(sys.executable) + ' '
                      + shlex.quote(str(Path(__file__).with_name('native.py'))) + ' '
                      + shlex.quote(settings['codex']) + ' "$@"\n')
    native.chmod(0o700)
    env = {key: value for key, value in os.environ.items() if not key.startswith('CEREBRO_')}
    native_home = directory / 'native-home'
    native_home.mkdir(exist_ok=True)
    auth = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'auth.json'
    if auth.is_file():
        shutil.copyfile(auth, native_home / 'auth.json')
        (native_home / 'auth.json').chmod(0o600)
    env['CODEX_HOME'] = str(native_home)
    env.update(CEREBRO_HOME=str(directory / 'home'), CEREBRO_SESSION_DIR=str(session),
               CEREBRO_SESSION_ID='eval', CEREBRO_BACKEND='codex', CEREBRO_LIB_DIR=str(LIB),
               CEREBRO_CODEX_CMD=str(native), CEREBRO_MODEL=settings['models']['implementation'] or '',
               CEREBRO_REVIEW_MODEL=settings['models']['review'] or '', CEREBRO_SUPERVISOR_MODEL=settings['models']['supervisor'] or '',
               CEREBRO_IMPLEMENTOR_EFFORT=settings['efforts']['implementation'] or '',
               CEREBRO_REVIEW_EFFORT=settings['efforts']['review'] or '',
               CEREBRO_SUPERVISOR_EFFORT=settings['efforts']['supervisor'] or '',
               CEREBRO_JEV_ENABLED=str(int(watch)), CEREBRO_JEV_API_KEY=settings['jev_api_key'],
               CEREBRO_JEV_MODEL=settings['jev_model'], CEREBRO_JEV_ENDPOINT=settings['jev_endpoint'],
               CEREBRO_JEV_CONFIDENCE=str(settings['jev_confidence']), CEREBRO_TIMEOUT=str(settings['timeout']),
               CEREBRO_PAIR_IDLE='0', CEREBRO_PAIR_STALL_RETRIES='0')
    env.update(CEREBRO_EVAL_DIR=str(directory), CEREBRO_EVAL_REPO=str(directory / 'repo'))
    # Use production materialization rather than maintaining a second MCP configuration.
    try:
        process(['bash', '-c', 'source "$1/backend.sh"; backend_supervisor_config supervisor',
                 'cerebro-eval', str(LIB)], env, directory, directory / 'configure', timeout=20)
    except BaseException:
        cleanup(env)
        raise
    return env, session


def codex(directory, env, prompt, settings, *, schema=None, supervisor=False):
    import baseline
    options = []
    if supervisor:
        with environment(env):
            options = supervisor_options(env['CEREBRO_SESSION_DIR'])
        instructions = (LIB / 'payloads/skills/cerebro-supervisor/SKILL.md').read_text()
    else:
        instructions = 'Use only the evidence supplied in the prompt. Return the requested JSON decision.'
    return baseline.run(directory, directory / 'repo', prompt, settings, schema, 'supervisor',
                        env=env, options=options, instructions=instructions)


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
    """Stop eval-owned detached jobs and remove the temporary native auth copy."""
    session = Path(env['CEREBRO_SESSION_DIR'])
    errors = []
    try:
        directory = Path(env['CEREBRO_EVAL_DIR'])
        for prefix in ('parent', 'configure', 'delegation', 'native-contract'):
            path = directory / (prefix + '.process-running.json')
            if not path.exists() and not path.is_symlink():
                continue
            try:
                if path.is_symlink() or not path.is_file() or path.resolve().parent != directory.resolve():
                    raise ValueError('eval process receipt must be a regular top-level file')
                owned = json.loads(path.read_text())
                if (not isinstance(owned, dict) or owned.get('owner') != 'cerebro-eval' or owned.get('prefix') != prefix
                        or owned.get('trial_directory') != str(directory.resolve())
                        or type(owned.get('pid')) is not int or owned['pid'] < 1
                        or not isinstance(owned.get('identity'), str)):
                    raise ValueError('invalid eval process ownership receipt')
                pid = owned['pid']
                current = subprocess.run(['ps', '-p', str(pid), '-o', 'lstart='], capture_output=True, text=True).stdout.strip()
                terminated = bool(current and current == owned['identity'])
                if terminated:
                    try:
                        if os.getpgid(pid) != pid:
                            raise ValueError('recorded eval process is no longer its process-group leader')
                        os.killpg(pid, signal.SIGTERM)
                        time.sleep(3.5)
                        active = subprocess.run(['ps', '-p', str(pid), '-o', 'lstart='],
                                                capture_output=True, text=True).stdout.strip()
                        state = subprocess.run(['ps', '-p', str(pid), '-o', 'stat='],
                                               capture_output=True, text=True).stdout.strip()
                        if active == owned['identity'] and not state.startswith('Z'):
                            os.killpg(pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                write_json(path.with_suffix('.cleanup.json'), {'owned_pid': pid, 'terminated': terminated})
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                errors.append(prefix + ': ' + str(error))
        for path in (session / 'detached-jobs').glob('*.json'):
            try:
                job = json.loads(path.read_text())
                if not isinstance(job, dict):
                    raise ValueError('detached eval job metadata must be an object')
                if not job.get('id') or not job.get('status'):
                    continue
                status = Path(job['status'])
                if status.exists() and status.read_text().strip().lstrip('-').isdigit():
                    continue
                result = subprocess.run([str(CLI), 'cancel', job['id']], env=env, capture_output=True, timeout=15)
                if result.returncode:
                    errors.append('could not cancel eval-owned job ' + job['id'])
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                errors.append(str(error))
    finally:
        native_home = Path(env['CEREBRO_EVAL_DIR']) / 'native-home'
        (native_home / 'auth.json').unlink(missing_ok=True)
    if errors:
        raise RuntimeError('; '.join(errors))


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
