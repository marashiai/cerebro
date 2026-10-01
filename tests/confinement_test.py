"""Guarded command outputs and detached statuses stay within allowed roots."""

import json
import os
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parent.parent


with tempfile.TemporaryDirectory(prefix='cerebro-confinement-tests-') as temporary, \
        tempfile.TemporaryDirectory(prefix='cerebro-', dir='/tmp') as scratch_temporary:
    directory = Path(temporary)
    scratch = Path(scratch_temporary)
    session_id = scratch.name[len('cerebro-'):]
    home = directory / 'home'
    session = home / 'sessions' / session_id
    session.mkdir(parents=True)
    outside = directory / 'outside'
    outside.mkdir()
    guards = directory / 'guards'
    guards.mkdir()
    for backend in ('opencode', 'claude', 'codex'):
        executable = guards / backend
        executable.write_text('#!/usr/bin/env bash\nprintf "unexpected backend fixture\\n" >&2\nexit 97\n')
        executable.chmod(0o755)
    environment = {**os.environ, 'CEREBRO_HOME': str(home), 'CEREBRO_SESSION_ID': session_id,
                   'CEREBRO_OPENCODE_CMD': str(guards / 'opencode'), 'CEREBRO_CLAUDE_CMD': str(guards / 'claude'),
                   'CEREBRO_CODEX_CMD': str(guards / 'codex'), 'CEREBRO_BACKEND': 'opencode'}

    def command(*arguments):
        return subprocess.run([str(root / 'bin' / 'cerebro'), *arguments], env=environment,
                              text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)

    def refused(*arguments):
        result = command(*arguments)
        assert result.returncode != 0, result.stdout
        assert 'unexpected backend fixture' not in result.stderr, 'output validation reached a backend'
        return result

    # Every input source retains literal Markdown; no shell or escaping step.
    body = '# Approved plan\nLiteral `code` and $variables.\n'
    source = directory / 'source.md'
    source.write_text(body)
    result = command('plan', '--from-file', str(source), '--out', 'approved-plan')
    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()).read_text() == body
    refused('plan', body, '--out', '../escaped-plan')
    refused('plan', body, '--out', str(outside / 'escaped-plan'))

    sentinel = outside / 'sentinel.md'
    sentinel.write_text('unchanged')
    (session / 'plans' / 'symlink-plan.md').symlink_to(sentinel)
    refused('plan', body, '--out', 'symlink-plan')
    refused('plans', 'rm', 'symlink-plan')
    assert sentinel.read_text() == 'unchanged'
    (session / 'plans' / 'symlink-plan.md').unlink()
    (session / 'plans' / 'approved-plan.md').unlink()
    (session / 'plans').rmdir()
    (session / 'plans').symlink_to(outside, target_is_directory=True)
    refused('plan', body, '--out', 'escaped-root-plan')
    refused('plans', 'rm', 'sentinel')
    assert sentinel.read_text() == 'unchanged'
    assert not (outside / 'escaped-root-plan.md').exists()

    repo = directory / 'repo'
    repo.mkdir()
    refused('audit', str(repo), str(source), '--out', '../escaped-audit')
    audits = session / 'audits'
    audits.mkdir(exist_ok=True)
    (audits / 'symlink-audit.md').symlink_to(sentinel)
    refused('audit', str(repo), str(source), '--out', 'symlink-audit')
    assert sentinel.read_text() == 'unchanged'
    (audits / 'symlink-audit.md').unlink()
    audits.rmdir()
    audits.symlink_to(outside, target_is_directory=True)
    refused('audit', str(repo), str(source), '--out', 'escaped-root-audit')
    assert not (outside / 'escaped-root-audit.log').exists()

    (session / 'escape').symlink_to(outside, target_is_directory=True)
    refused('detach', '--output', str(session / 'escape' / 'escaped.out'), '--', 'verify')
    refused('detach', '--output', str(session / '..' / '..' / '..' / 'escaped.out'), '--', 'verify')
    assert not (outside / 'escaped.out').exists()

    completed = session / 'completed.status'
    completed.write_text('0\n')
    result = command('wait', str(completed.resolve()))
    assert result.returncode == 0 and 'finished (exit 0)' in result.stdout, result.stderr
    assert completed.read_text() == '0\n'
    scratch_status = scratch / 'completed.status'
    scratch_status.write_text('0\n')
    for path in (scratch_status, scratch_status.resolve()):
        result = command('wait', str(path))
        assert result.returncode == 0, result.stderr

    # Lost-monitor recovery publishes exit 125. Reject an escaped status
    # before that write, including a status loaded from a stored job record.
    lost = outside / 'lost.status'
    lost.write_text('running\n')

    def refused_wait(path):
        result = refused('wait', str(path))
        assert result.returncode != 125, 'escaped wait entered lost-monitor recovery'
        assert lost.read_text() == 'running\n', 'wait wrote outside its session'

    refused_wait(session / '..' / '..' / '..' / 'outside' / 'lost.status')
    refused_wait(session / 'escape' / 'lost.status')
    alias = session / 'external-file.status'
    alias.symlink_to(lost)
    refused_wait(alias)
    jobs = session / 'detached-jobs'
    jobs.mkdir()
    job = jobs / 'c0ffee.json'
    for path in (lost, alias):
        job.write_text(json.dumps({'status': str(path)}))
        refused_wait('c0ffee')

    for extension in ('.status', '.pid'):
        output = session / ('detached' + extension + '.out')
        sidecar = Path(str(output) + extension)
        sidecar.symlink_to(lost)
        refused('detach', '--output', str(output), '--', 'verify')
        assert not output.exists(), 'detach launched before sidecar validation'
        assert sidecar.is_symlink() and lost.read_text() == 'running\n'

print('all checks passed')
