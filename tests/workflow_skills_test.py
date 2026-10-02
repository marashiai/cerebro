"""Shared skills and review/verify task packets reach each selected backend."""

import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import sys

root = Path(__file__).resolve().parent.parent
cli = str(root / 'bin' / 'cerebro')
payloads = root / 'lib' / 'payloads'
skills = payloads / 'skills'
fixtures = Path(__file__).resolve().parent
sys.path.insert(0, str(root / 'lib' / 'python'))
from pi_launch import run_argv


def body(path):
    text = path.read_text()
    if text.startswith('---\n'):
        text = text.split('---\n', 2)[2]
    return text.rstrip('\n')


def command(environment, *argv):
    result = subprocess.run([cli, *argv], env=environment, text=True,
                            capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr + '\n' + result.stdout
    return result.stdout


def records(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


with tempfile.TemporaryDirectory(prefix='cerebro-workflow-skills-tests-') as temporary:
    directory = Path(temporary)
    guards = directory / 'guards'
    guards.mkdir()
    for backend in ('pi', 'claude', 'codex', 'gh', 'hunk'):
        guard = guards / backend
        guard.write_text('#!/usr/bin/env bash\nprintf "unexpected workflow fixture\\n" >&2\nexit 97\n')
        guard.chmod(0o755)
    host = {'HOME': os.environ['HOME'], 'PATH': str(guards) + ':' + os.environ['PATH']}
    environment = {
        **host, 'CEREBRO_HOME': str(directory / 'shared-home'), 'CEREBRO_BACKEND': 'pi',
        'CEREBRO_JEV_ENABLED': '0', 'CEREBRO_JEV_API_KEY': '',
        'CEREBRO_PI_CMD': str(guards / 'pi'),
        'CEREBRO_CLAUDE_CMD': str(guards / 'claude'), 'CEREBRO_CODEX_CMD': str(guards / 'codex'),
    }
    for name in ('engineering', 'supervise', 'hashimoto-review', 'cerebro-worker', 'cerebro-supervisor'):
        assert command(environment, 'guide', name).rstrip('\n') == body(skills / name / 'SKILL.md')
    for name in ('../engineering', '/engineering', 'engineering/../../secret', 'engineering;uname', 'unknown-skill'):
        invalid = subprocess.run([cli, 'guide', name], env=environment, text=True, capture_output=True, timeout=5)
        assert invalid.returncode != 0 and ('usage:' in invalid.stderr or 'unknown skill' in invalid.stderr), invalid

    libraries = root / 'lib'
    shell = ('CEREBRO_LIB_DIR="$1"; . "$1/config.sh"; . "$1/helpers.sh"; '
             '. "$1/payloads.sh"; . "$1/backend.sh"; . "$1/backend-pi.sh"; materialise_home')
    materialized = subprocess.run(['bash', '-c', shell, '_', str(libraries)],
                                 env=environment, text=True, capture_output=True, timeout=5)
    assert materialized.returncode == 0, materialized.stderr
    home = Path(environment['CEREBRO_HOME'])
    for name in ('engineering', 'supervise', 'hashimoto-review', 'cerebro-worker', 'cerebro-supervisor'):
        shared = home / '.agents' / 'skills' / name / 'SKILL.md'
        assert shared.read_text().rstrip('\n') == (skills / name / 'SKILL.md').read_text().rstrip('\n')
        assert (home / '.claude' / 'skills' / name / 'SKILL.md').resolve() == shared.resolve()

    prompt_shell = ('CEREBRO_LIB_DIR="$1"; . "$1/config.sh"; . "$1/helpers.sh"; '
                    '. "$1/payloads.sh"; child_sys_prompt "$2"')
    worker_instructions = '\n\n'.join([body(skills / 'cerebro-worker' / 'SKILL.md'),
                                       body(skills / 'engineering' / 'SKILL.md'),
                                       body(payloads / 'prompts' / 'noninteractive-note.md')])
    reviewer_instructions = '\n\n'.join([body(payloads / 'prompts' / 'reviewer-note.md'),
                                         body(skills / 'engineering' / 'SKILL.md')])
    for role in ('execute', 'apply-review', 'doc-write', 'verify', 'review', 'audit', 'improve'):
        prompted = subprocess.run(['bash', '-c', prompt_shell, '_', str(libraries), role],
                                  env=environment, text=True, capture_output=True, timeout=5)
        assert prompted.returncode == 0, prompted.stderr
        assert prompted.stdout.rstrip('\n') == (worker_instructions if role in
                                                ('execute', 'apply-review', 'doc-write', 'verify')
                                                else reviewer_instructions), role
        if role in ('execute', 'apply-review', 'doc-write', 'verify'):
            argv = run_argv('fixture-pi', role, str(home), '', '', '', prompted.stdout.rstrip('\n'))
            assert argv[argv.index('--append-system-prompt') + 1] == worker_instructions
            assert argv[argv.index('--skill') + 1] == str(skills)

    for backend in ('pi', 'claude', 'codex'):
        home = directory / backend
        session = home / 'sessions' / 'workflow-session'
        (session / 'children').mkdir(parents=True)
        (session / 'plans').mkdir()
        (session / 'transcript.jsonl').touch()
        (session / 'metadata.json').write_text(json.dumps({'backend': backend, 'role': 'supervisor'}))
        repo = home / 'repo'
        subprocess.run(['git', 'init', '-q', '-b', 'main', str(repo)], check=True)
        subprocess.run(['git', '-C', str(repo), 'config', 'user.name', 'test'], check=True)
        subprocess.run(['git', '-C', str(repo), 'config', 'user.email', 'test@example.com'], check=True)
        source = repo / 'requirements.txt'
        source.write_text('approved contract')
        subprocess.run(['git', '-C', str(repo), 'add', 'requirements.txt'], check=True)
        subprocess.run(['git', '-C', str(repo), 'commit', '-q', '-m', 'workflow fixture'], check=True)
        head = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
        criteria = session / 'plans' / 'acceptance.md'
        criteria_text = '- Code change meets the contract\n- Browser/manual journey needs real runtime evidence'
        criteria.write_text(criteria_text + '\n')
        log = home / 'native.jsonl'
        selected = home / 'selected-backend'
        if backend == 'pi':
            selected.write_text('#!/usr/bin/env bash\nexec python3 '
                                + shlex.quote(str(fixtures / 'pi_fixture.py')) + ' '
                                + shlex.quote(str(home)) + ' "$@"\n')
        else:
            selected.write_text('#!/usr/bin/env bash\nexec python3 '
                                + shlex.quote(str(fixtures / 'native_child_fixture.py')) + ' '
                                + backend + ' "$@"\n')
        selected.chmod(0o755)
        review_model = 'fixture/review-native' if backend == 'pi' else 'review-native'
        environment = {
            **host, 'CEREBRO_HOME': str(home), 'CEREBRO_SESSION_ID': session.name,
            'CEREBRO_SESSION_DIR': str(session), 'CEREBRO_BACKEND': backend,
            'CEREBRO_PI_CMD': str(guards / 'pi'),
            'CEREBRO_CLAUDE_CMD': str(guards / 'claude'), 'CEREBRO_CODEX_CMD': str(guards / 'codex'),
            'CEREBRO_MODEL': 'fixture/development' if backend == 'pi' else 'development',
            'CEREBRO_REVIEW_MODEL': review_model, 'CEREBRO_TIMEOUT': '10',
            'CEREBRO_JEV_ENABLED': '0', 'CEREBRO_JEV_API_KEY': '',
            'CEREBRO_CHILD_IDLE_TIMEOUT': '0', 'CEREBRO_PAIR_IDLE': '0',
            'NATIVE_FIXTURE_LOG': str(log),
        }
        environment['CEREBRO_' + backend.upper() + '_CMD'] = str(selected)

        def delegated(argv, role, response='NATIVE_DONE'):
            log.write_text('')
            if backend == 'pi':
                (home / 'fixture.json').write_text(json.dumps({
                    'request_log': str(log), 'text': response}))
            output = command(environment, *argv)
            report = Path(output.strip().splitlines()[-1])
            assert report.is_file() and report.read_text().strip() == response
            native = records(log)
            if backend == 'pi':
                prompt = next(event['message'] for event in native if event.get('type') == 'prompt')
                arguments = next(event['argv'] for event in native if 'argv' in event)
                assert arguments[arguments.index('--model') + 1] == review_model
                assert arguments[arguments.index('--append-system-prompt') + 1] == (reviewer_instructions
                                                                                  if role == 'review' else worker_instructions)
                if role == 'review':
                    assert arguments[arguments.index('--tools') + 1] == 'mcp__cerebro__command'
                    assert arguments[arguments.index('--cerebro-role') + 1] == 'reviewer'
                else:
                    assert '--tools' not in arguments
                    assert [arguments[i + 1] for i, arg in enumerate(arguments) if arg == '-e'] == [
                        'builtin:mcp', 'builtin:codemode', 'builtin:tool-search']
            elif backend == 'codex':
                start = next(event['params'] for event in native if event.get('method') == 'thread/start')
                assert start['model'] == review_model
                assert start['sandbox'] == ('read-only' if role == 'review' else 'danger-full-access')
                assert start['developerInstructions'] == (reviewer_instructions if role == 'review' else worker_instructions)
                turn = next(event['params'] for event in native if event.get('method') == 'turn/start')
                prompt = turn['input'][0]['text']
            else:
                arguments = next(event['argv'] for event in native if 'argv' in event)
                assert arguments[arguments.index('--model') + 1] == review_model
                assert arguments[arguments.index('--append-system-prompt') + 1] == (reviewer_instructions
                                                                                  if role == 'review' else worker_instructions)
                if role == 'review':
                    assert arguments[arguments.index('--tools') + 1] == ''
                    assert arguments[arguments.index('--allowedTools') + 1] == 'mcp__cerebro__command'
                    assert '--strict-mcp-config' in arguments
                else:
                    assert {'Read', 'Bash', 'TaskOutput', 'TaskStop'} <= set(arguments[arguments.index('--tools') + 1].split(','))
                prompt = next(event['prompt'] for event in native if 'prompt' in event)
            store = json.loads((session / 'child-sessions.json').read_text())
            child = next(value for value in store.values() if value['role'] == role)
            assert child['provider'] == backend and child['status'] == 'done'
            return prompt

        base_prompt = body(payloads / 'prompts' / 'review.md').replace('__CEREBRO_BASE__', "base reference 'main'")
        base_prompt = base_prompt.replace('__CEREBRO_MERGE_BASE__', head)
        criteria_block = ('\n\n' + body(payloads / 'prompts' / 'review-criteria.md')
                          + '\n\n<requirements>\n' + criteria_text + '\n</requirements>')
        review_argv = ['review', str(repo), '--base', 'main', '--criteria-file', str(criteria)]
        review_response = ('Code criterion: MET\nBrowser criterion: EXTERNAL (manual check still needed)\n'
                           'ACCEPTANCE CRITERIA: MET') if backend == 'pi' else 'NATIVE_DONE'
        ordinary = delegated(review_argv, 'review', review_response)
        assert ordinary == base_prompt + criteria_block
        assert 'use verdict EXTERNAL' in ordinary
        assert 'EXTERNAL criteria do not make the final verdict NOT MET' in ordinary
        explained = delegated([*review_argv, '--explain'], 'review', review_response)
        expected_explanation = ('\n\n' + body(skills / 'hashimoto-review' / 'SKILL.md')
                                + '\n\n' + body(payloads / 'prompts' / 'explain-review.md'))
        assert explained == base_prompt + expected_explanation + criteria_block
        assert 'use verdict EXTERNAL' in explained
        assert 'EXTERNAL criteria do not make the final verdict NOT MET' in explained

        context = 'Parent runtime evidence: pending\nLiteral `command` and $(never execute)'
        request = 'Verify the scoped browser and command journey literally'
        verify_response = 'Runtime evidence recorded\nVERIFY: PASS' if backend == 'pi' else 'NATIVE_DONE'
        context_block = '\n\n<parent-context>\n' + context + '\n</parent-context>'
        verify_prompt = delegated(['verify', str(repo), '--prompt', request, '--context', context], 'verify', verify_response)
        assert verify_prompt == body(payloads / 'prompts' / 'verify.md') + '\n\nThe ad-hoc verification request: ' + request + context_block
        verify_plan = delegated(['verify', str(repo), '--plan', str(criteria), '--context', context], 'verify', verify_response)
        assert verify_plan == body(payloads / 'prompts' / 'verify.md') + '\n\n<requirements>\n' + criteria_text + '\n</requirements>' + context_block
        for prompt in (verify_prompt, verify_plan):
            assert all(verdict in prompt for verdict in ('VERIFY: PASS', 'VERIFY: FAIL', 'VERIFY: BLOCKED'))
        assert source.read_text() == 'approved contract'
        assert subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip() == head
        assert subprocess.check_output(['git', '-C', str(repo), 'status', '--porcelain'], text=True) == ''

print('all checks passed')
