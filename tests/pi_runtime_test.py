"""Opt-in feature checks using installed Pi and a local scripted model provider.

No model credentials, remote repository, or user configuration is used.
Artifacts remain in the directory printed by native_runtime.
"""
import json
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer

import native_runtime as native


COUNTS = {}
READY = threading.Event()
SCENARIO = ''
WORKTREE = None
ENV = None
PARENT_DONE = 'PI_PARENT_RESUMED'
BROWSER_SCREENSHOT = None
MCP_EXPOSURE = 'direct'
ORIGINAL = native.action
ORIGINAL_GET = native.Provider.do_GET


def browser_page(handler):
    if handler.path != '/browser':
        return ORIGINAL_GET(handler)
    handler.reply('text/html', b'<!doctype html><title>Pi native browser</title>'
                  b'<main><h1>Pi native browser</h1><button onclick="document.querySelector(\'output\').textContent=\'BROWSER_VERIFIED\'">Verify</button>'
                  b'<output>Waiting</output></main>')


native.Provider.do_GET = browser_page


def action(body, backend):
    raw = json.dumps(body)
    if 'PI_CHECK_' not in raw:
        return ORIGINAL(body, backend)
    role = SCENARIO
    if role == 'improve':
        role = 'meta' if 'META CLIMB:' in raw else 'improve'
    COUNTS[role] = COUNTS.get(role, 0) + 1
    turn = COUNTS[role]
    names = [t['function']['name'] for t in body['tools']]
    readonly = role in ('audit', 'improve', 'meta', 'resume')
    assert ('bash' not in names) == readonly, (role, names)
    if readonly:
        assert names == ['mcp__cerebro__command'], names
    expected = ENV['CEREBRO_SUPERVISOR_MODEL'] if role == 'resume' else ENV[
        'CEREBRO_REVIEW_MODEL' if readonly or role in ('verify', 'browser') else 'CEREBRO_MODEL']
    assert body['model'] == expected.split('/', 1)[1], (role, body['model'])
    if role == 'browser':
        latest = body['messages'][-1]['content']
        if turn == 1:
            return 'mcp__playwright__browser_navigate', {'url': native.base + '/browser'}, None
        if turn == 2:
            snapshot = re.search(r'Snapshot\]\(([^)]+)\)', latest)
            assert snapshot, latest
            return 'read', {'path': str(native.ROOT / Path(snapshot[1]).name)}, None
        if turn == 3:
            ref = re.search(r'button "Verify" \[ref=([^\]]+)\]', latest)
            assert ref, latest
            return 'mcp__playwright__browser_click', {'target': ref[1], 'element': 'Verify button'}, None
        if turn == 4:
            snapshot = re.search(r'Snapshot\]\(([^)]+)\)', latest)
            assert snapshot, latest
            return 'read', {'path': str(native.ROOT / Path(snapshot[1]).name)}, None
        if turn == 5:
            assert 'BROWSER_VERIFIED' in latest, latest
            return 'mcp__playwright__browser_take_screenshot', {'type': 'png', 'filename': 'pi-browser.png', 'fullPage': True, 'scale': 'css'}, None
        if turn == 6:
            return 'read', {'path': str(BROWSER_SCREENSHOT)}, None
        if turn == 7:
            assert 'data:image/png;base64,' in json.dumps(latest), str(latest)[:200]
            return 'mcp__playwright__browser_close', {}, None
        return None, None, 'QA: PASS'
    if turn == 1:
        if role == 'mcp':
            name = 'mcp__verification__echo_proof'
            if MCP_EXPOSURE == 'codemode':
                assert 'codemode' in names and name not in names, names
                return 'codemode', {'code': 'const matches = await searchTools("echo proof", {namespace: "verification"}); if (!matches.some(t => t.name === "' + name + '")) throw new Error("MCP tool not found"); text(await tools.' + name + '({}));'}, None
            if MCP_EXPOSURE == 'deferred':
                assert 'tool_search' in names and name not in names, names
                return 'tool_search', {'query': 'verification echo proof', 'limit': 1}, None
            assert name in names, 'configured native Pi MCP tool is missing: ' + str(names)
            return name, {}, None
        if readonly:
            argv = ['status'] if role == 'resume' else ['read', str(WORKTREE), 'AGENTS.md']
            return 'mcp__cerebro__command', {'argv': argv}, None
        commands = {
            'docs': "printf '%s' 'Native documentation proof' > pi-docs.md",
            'apply': "printf '%s' NATIVE_PATCHED > native-proof.txt",
            'verify': "test \"$(cat native-proof.txt)\" = NATIVE_PATCHED && test -s pi-docs.md && echo PI_VERIFIED_RUNTIME",
            'incomplete': "printf '%s' RETAINED > recovery-proof.txt",
            'restart': "printf '%s' STRAYED > restart-proof.txt",
            'restart-existing': "printf '%s' STRAYED > restart-proof.txt",
            'restart-remote': "printf '%s' STRAYED > restart-proof.txt",
            'detach': "sleep 2; printf '%s' DETACHED > detached-proof.txt",
            'cancel': "git switch -c feat/pi-cancel && printf '%s' $$ > cancellation-pid; sleep 60; printf BAD > after-cancel.txt",
            'stall': "printf '%s' $$ > stall-pid; sleep 60; printf BAD > after-stall.txt",
            'timeout': "printf '%s' $$ > timeout-pid; sleep 60; printf BAD > after-timeout.txt",
            'deviation': "printf '%s' BILLING > billing.py",
        }
        if role == 'restart':
            commands[role] = (commands[role] +
                              " && git add restart-proof.txt && git -c user.name='Native Verification' "
                              "-c user.email=native@localhost commit -qm 'test fixture' && git push origin HEAD")
        return 'bash', {'command': commands[role]}, None
    if role == 'cancel' and turn == 2:
        assert 'Selected branch: feat/pi-cancel' in raw
        return 'bash', {'command': 'test "$(git branch --show-current)" = feat/pi-cancel && printf RECOVERED > after-resume.txt'}, None
    if role == 'mcp' and MCP_EXPOSURE == 'deferred' and turn == 2:
        name = 'mcp__verification__echo_proof'
        assert name in names, 'discovered native Pi MCP tool is missing: ' + str(names)
        return name, {}, None
    if role == 'deviation':
        if turn == 2:
            return None, None, 'BILLING_DRIFT: implementing unrelated checkout billing.'
        if turn == 3:
            assert 'SCOPE_CORRECTION' in raw
            return 'bash', {'command': 'rm billing.py; printf CORRECTED > corrected-proof.txt'}, None
        return None, None, 'PI_WATCH_CORRECTED'
    READY.set()
    if readonly:
        assert ('session:' if role == 'resume' else 'selected checkout') in raw, raw[-1000:]
    if role == 'verify':
        assert 'PI_VERIFIED_RUNTIME' in body['messages'][-1]['content'], body['messages'][-1]
    if role == 'mcp':
        assert 'NATIVE_MCP_PROOF' in body['messages'][-1]['content'], body['messages'][-1]
    if role == 'incomplete' and 'PI_CHECK_RECOVER' not in raw:
        return None, None, ''
    verdicts = {'improve': 'HILL CLIMB: NO CHANGES RECOMMENDED',
                'meta': 'META CLIMB: NO CHANGES RECOMMENDED', 'verify': 'QA: PASS',
                'resume': PARENT_DONE}
    return None, None, verdicts.get(role, 'PI_CHECK_' + role.upper() + '_DONE')


native.action = action


def scenario(name):
    global SCENARIO
    SCENARIO = name
    COUNTS.pop(name, None)
    READY.clear()


def run(*args, ok=True, env=None):
    result = subprocess.run([str(native.SOURCE / 'bin/cerebro'), *map(str, args)],
                            env=env or ENV, text=True, capture_output=True, timeout=65)
    stem = native.ROOT / (SCENARIO + '-' + args[0])
    stem.with_suffix('.stdout').write_text(result.stdout)
    stem.with_suffix('.stderr').write_text(result.stderr)
    native.assert_provider_ok()
    assert (result.returncode == 0) == ok, (args, result.returncode, result.stderr)
    return result


def children(session):
    return json.loads((session / 'child-sessions.json').read_text())


def child(session, role):
    return next(row for row in children(session).values() if row['role'] == role)


def assert_tool_gone(marker):
    pid = int(marker.read_text())
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return
    os.kill(pid, signal.SIGKILL)
    raise AssertionError('native shell tool survived cleanup: ' + str(marker))


def paired_control(repo, session, name, control, ok=True):
    scenario(name)
    selected = {**ENV, 'CEREBRO_PAIR_IDLE': '10'}
    branch = {'restart': 'feat/pi-restart', 'restart-existing': 'feat/unrelated',
              'restart-remote': 'feat/remote-existing'}[name]
    args = ['execute', str(repo), '--pair', '--no-watch', '--worktree', '--branch', branch,
            '--prompt', 'PI_CHECK_' + name.upper() + ': exercise the native lifecycle.']
    with (native.ROOT / (name + '.stderr')).open('w') as errors:
        proc = subprocess.Popen([str(native.SOURCE / 'bin/cerebro'), *args], env=selected,
                                stdout=subprocess.PIPE, stderr=errors, text=True, start_new_session=True)
        try:
            assert READY.wait(25), name + ' native turn did not reach its final reply'
            fifo, = (session / 'children').glob('*.steer.fifo')
            row = next(row for row in children(session).values()
                       if row['role'] == 'execute' and row['branch'] == branch)
            native_id = row['id']
            with Path(native_id).open() as stream:
                worktree = Path(json.loads(stream.readline())['cwd'])
            before = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', branch], text=True).strip()
            run(control, str(fifo), 'Replace the strayed task with a clean native task.')
            output, _ = proc.communicate(timeout=15)
            assert (proc.returncode == 0) == ok, (name, proc.returncode, output)
            assert not list((session / 'children').glob('*.steer.fifo'))
            assert 'RESTART REQUESTED' in output, output
            assert worktree.is_dir(), 'restart removed retained workspace'
            assert (worktree / 'restart-proof.txt').read_text() == 'STRAYED'
            assert subprocess.check_output(['git', '-C', str(repo), 'rev-parse', branch], text=True).strip() == before
            assert not any(row['status'] == 'running' for row in children(session).values())
            assert not any(row.get('id') == native_id for row in children(session).values())
            run('answer', native_id, 'Do not resume this retired conversation.', ok=False)
            if name == 'restart':
                assert subprocess.check_output(['git', '-C', str(repo), 'ls-remote', '--heads',
                                                'origin', branch], text=True).strip()
            print('PASS native restart retains work and retires the native conversation', flush=True)
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(timeout=8)


def pty_frontend(env):
    from cerebro_mcp_server import McpClient
    python = os.environ.get('CEREBRO_TEST_PYTHON')
    if not python:
        print('SKIP native Pi PTY MCP journey: set CEREBRO_TEST_PYTHON to a Python with mcp>=2', flush=True)
        return
    scenario('resume')
    selected = {**env, 'PATH': str(Path(python).parent) + ':' + env['PATH'], 'TERM': 'xterm-256color'}
    client = McpClient([str(native.SOURCE / 'bin/cerebro'), 'cerebro-mcp'], selected)
    sid = None
    try:
        client.request('initialize', {'protocolVersion': '2024-11-05', 'capabilities': {},
                                     'clientInfo': {'name': 'native-pi-test', 'version': '1'}})
        client.notify('notifications/initialized')
        spawned = client.call_tool('cerebro_spawn', {'command': str(native.SOURCE / 'bin/cerebro'),
                                                    'cwd': str(native.SOURCE), 'env': env, 'cols': 140, 'rows': 40})
        sid = spawned['sessionId']
        ready = client.call_tool('cerebro_wait', {'sessionId': sid, 'idleMs': 30000,
                                                 'match': r'0\.0%/[0-9]+k.*supervisor', 'timeoutMs': 30000})
        assert ready['event'] == 'match', ready
        client.call_tool('cerebro_wait', {'sessionId': sid, 'idleMs': 1000, 'timeoutMs': 3000})
        escape = Path(env['CEREBRO_HOME']) / 'user-bash-escape.txt'
        client.call_tool('cerebro_send', {'sessionId': sid, 'text': '\x1b[200~!printf BAD > ' + shlex.quote(str(escape)) + '\x1b[201~', 'key': 'enter'})
        blocked = client.call_tool('cerebro_wait', {'sessionId': sid, 'idleMs': 30000,
                                                   'match': 'Shell execution is unavailable', 'timeoutMs': 15000})
        assert blocked['event'] == 'match' and not escape.exists(), blocked
        client.call_tool('cerebro_send', {'sessionId': sid, 'text': '\x1b[200~PI_CHECK_RESUME: inspect current state.\x1b[201~', 'key': 'enter'})
        done = client.call_tool('cerebro_wait', {'sessionId': sid, 'idleMs': 30000,
                                                'match': PARENT_DONE, 'timeoutMs': 30000})
        assert done['event'] == 'match' and COUNTS['resume'] == 2, done
        (native.ROOT / 'pi-pty-mcp.txt').write_text(client.call_tool('cerebro_read', {'sessionId': sid})['text'])
        client.call_tool('cerebro_resize', {'sessionId': sid, 'cols': 100, 'rows': 30})
        client.call_tool('cerebro_close', {'sessionId': sid})
        assert sid not in json.dumps(client.call_tool('cerebro_list', {}))
        sid = None
        print('PASS native Pi through PTY MCP; supervisor shell escape blocked; interaction continues', flush=True)
    finally:
        if sid:
            (native.ROOT / 'pi-pty-mcp-failed.txt').write_text(client.call_tool('cerebro_read', {'sessionId': sid})['text'])
            client.call_tool('cerebro_close', {'sessionId': sid})
        client.close()


def native_mcp(env, repo):
    global MCP_EXPOSURE
    python = os.environ.get('CEREBRO_TEST_PYTHON')
    if not python:
        print('SKIP configured native MCP tools: set CEREBRO_TEST_PYTHON', flush=True)
        return
    script = native.ROOT / 'verification_mcp.py'
    script.write_text("from mcp.server.mcpserver import MCPServer\n"
                      "mcp = MCPServer('verification')\n"
                      "@mcp.tool()\ndef echo_proof() -> str:\n    return 'NATIVE_MCP_PROOF'\n"
                      "mcp.run()\n")
    config = Path(env['PI_CODING_AGENT_DIR']) / 'mcp.json'
    try:
        for exposure in ('direct', 'codemode', 'deferred'):
            MCP_EXPOSURE = exposure
            server = {'command': python, 'args': [str(script)]}
            if exposure != 'codemode':
                server['exposure'] = exposure
            config.write_text(json.dumps({'mcpServers': {'verification': server}}))
            scenario('mcp')
            result = run('execute', repo, '--prompt', 'PI_CHECK_MCP: invoke the configured verification tool.')
            assert 'PI_CHECK_MCP_DONE' in result.stdout
            assert COUNTS['mcp'] == (3 if exposure == 'deferred' else 2)
            print('PASS configured native Pi MCP tool with ' + exposure + ' exposure', flush=True)
        pty_frontend(env)
    finally:
        config.unlink()


def browser_check(env, repo):
    global BROWSER_SCREENSHOT
    BROWSER_SCREENSHOT = repo / 'pi-browser.png'
    command, executable = os.environ.get('CEREBRO_TEST_PLAYWRIGHT_MCP'), os.environ.get('CEREBRO_TEST_CHROMIUM')
    if not command or not executable:
        print('SKIP native browser journey: set CEREBRO_TEST_PLAYWRIGHT_MCP and CEREBRO_TEST_CHROMIUM', flush=True)
        return
    config = Path(env['PI_CODING_AGENT_DIR']) / 'mcp.json'
    config.write_text(json.dumps({'mcpServers': {'playwright': {
        'command': command, 'args': ['--headless', '--browser', 'chrome', '--executable-path', executable,
                                    '--output-dir', str(native.ROOT)], 'exposure': 'direct'}}}))
    models = Path(env['PI_CODING_AGENT_DIR']) / 'models.json'
    options = json.loads(models.read_text())
    options['providers']['cerebro-local']['models'][2]['input'] = ['text', 'image']
    models.write_text(json.dumps(options))
    scenario('browser')
    try:
        report = Path(run('verify', repo, '--prompt', 'PI_CHECK_BROWSER: navigate, click, capture and read a screenshot.').stdout.strip())
        assert report.read_text() == 'QA: PASS'
        print('PASS native Pi + official Playwright MCP navigation, click, screenshot and image delivery', flush=True)
    finally:
        config.unlink()


def watch_checks(env, repo, session):
    from cerebro_mcp_server import McpClient
    from jev_watch_test import Classifier
    Classifier.requests = []
    Classifier.mode = 'normal'
    server = ThreadingHTTPServer(('127.0.0.1', 0), Classifier)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    selected = {**env, 'CEREBRO_JEV_API_KEY': 'private-fixture-key',
                'CEREBRO_JEV_ENDPOINT': 'http://127.0.0.1:' + str(server.server_port),
                'CEREBRO_JEV_ENABLED': '1'}
    client = McpClient([str(native.SOURCE / 'bin/cerebro'), 'tools', 'supervisor'], selected)

    def command(*argv):
        result = client.request('tools/call', {'name': 'command', 'arguments': {'argv': list(map(str, argv))}})
        value = json.loads(result['content'][0]['text'])
        assert result['isError'] == (value['exit_code'] != 0)
        return value

    try:
        client.request('initialize', {'protocolVersion': '2024-11-05', 'capabilities': {},
                                     'clientInfo': {'name': 'pi-watch-test', 'version': '1'}})
        client.notify('notifications/initialized')
        command('spec', 'set', 'Deliver only the native proof and documentation; no billing or publication.')
        for role, argv in [('docs', ['doc-write', repo]), ('apply', ['apply-review', repo]),
                           ('docs', ['execute', repo])]:
            scenario(role)
            before = len(Classifier.requests)
            value = command(*argv, '--prompt', 'PI_CHECK_' + role.upper() + ': deliver only the native proof.', '--watch')
            assert value['state'] == 'completed' and value['exit_code'] == 0 and 'notice' not in value, value
            assert len(Classifier.requests) > before
        print('PASS native Pi scope monitoring for execute, apply-review and doc-write', flush=True)

        scenario('deviation')
        Classifier.mode = 'low-confidence'
        value = command('execute', repo, '--prompt', 'PI_CHECK_DEVIATION: write only a proof file. No billing.', '--watch')
        assert value['state'] == 'running' and value['notice']['classification']['scope'] == 'uncertain', value
        command('steer', value['notice']['steering_pipe'], 'SCOPE_CORRECTION: remove billing.py and write the proof.')
        completed = command('wait', value['job_id'], '--after', value['sequence'])
        assert completed['state'] == 'completed' and completed['exit_code'] == 0 and 'PI_WATCH_CORRECTED' in completed['text'], completed
        print('PASS native uncertain-scope notice, parent steering and acknowledged completion', flush=True)

        scenario('docs')
        Classifier.mode = 'invalid'
        before = set(children(session))
        value = command('execute', repo, '--prompt', 'PI_CHECK_DOCS: test classifier failure recovery.', '--watch')
        assert value['state'] == 'completed' and value['exit_code'] != 0, value
        after = children(session)
        retained, = [row for key, row in after.items() if key not in before]
        assert retained['status'] == 'running' and Path(retained['id']).is_file()
        assert 'private-fixture-key' not in value['text']
        print('PASS native classifier failure stops work, preserves its session and hides credentials', flush=True)
    finally:
        client.close()
        server.shutdown()
        server.server_close()


def main():
    global ENV, WORKTREE, PARENT_DONE
    print('Pi feature verification artifacts:', native.ROOT, flush=True)
    ENV, repo, session = native.prepare('pi')
    ENV['CEREBRO_JEV_ENABLED'] = '0'
    settings = Path(ENV['PI_CODING_AGENT_DIR']) / 'settings.json'
    options = json.loads(settings.read_text())
    options['retry'] = {'enabled': False}
    settings.write_text(json.dumps(options))
    if '--pty-only' in sys.argv:
        pty_frontend(ENV)
        return
    if '--mcp-only' in sys.argv:
        native_mcp(ENV, repo)
        return
    if '--browser-only' in sys.argv:
        browser_check(ENV, repo)
        return
    if '--watch-only' in sys.argv:
        watch_checks(ENV, repo, session)
        return
    # A local repo must never contact GitHub while exercising branch cleanup.
    gh = Path(ENV['PATH'].split(':', 1)[0]) / 'gh'
    gh.write_text('#!/bin/sh\nexit 1\n')
    gh.chmod(0o755)
    origin = native.ROOT / 'origin.git'
    subprocess.run(['git', 'init', '--bare', '-q', str(origin)], check=True)
    subprocess.run(['git', '-C', str(repo), 'remote', 'add', 'origin', str(origin)], check=True)
    subprocess.run(['git', '-C', str(repo), 'push', '-q', 'origin', 'main'], check=True)
    native.call(ENV, 'execute', str(repo), '--prompt', 'NATIVE_WORKER: write the proof file.',
                '--branch', 'feat/matrix-proof', '--worktree')
    worker = child(session, 'execute')
    with Path(worker['id']).open() as stream:
        WORKTREE = Path(json.loads(stream.readline())['cwd'])
    assert WORKTREE.resolve() != repo.resolve() and WORKTREE.is_dir()
    original = worker['id']
    native.call(ENV, 'answer', original, 'Continue the same task.')
    assert child(session, 'execute')['id'] == original
    print('PASS native execute isolation and exact-session answer', flush=True)

    scenario('docs')
    run('doc-write', WORKTREE, '--prompt', 'PI_CHECK_DOCS: write pi-docs.md.')
    assert (WORKTREE / 'pi-docs.md').read_text() == 'Native documentation proof'
    first = child(session, 'doc-write')['id']
    scenario('docs')
    run('doc-write', WORKTREE, '--prompt', 'PI_CHECK_DOCS: verify the documentation again.')
    assert child(session, 'doc-write')['id'] != first
    print('PASS native doc-write and fresh conversation after completion', flush=True)

    scenario('apply')
    run('apply-review', WORKTREE, '--prompt', 'PI_CHECK_APPLY: correct the proof file.')
    assert (WORKTREE / 'native-proof.txt').read_text() == 'NATIVE_PATCHED'
    print('PASS native apply-review', flush=True)

    scenario('verify')
    report = Path(run('verify', WORKTREE, '--prompt', 'PI_CHECK_VERIFY: run acceptance checks.').stdout.strip())
    assert report.read_text() == 'QA: PASS'
    print('PASS native verify executes acceptance checks and captures its report', flush=True)

    plan = Path(run('plan', 'PI_CHECK_AUDIT: deliver only the proof.', '--out', 'native-plan').stdout.strip())
    run('spec', 'set', 'Deliver only the proof and its documentation.')
    scenario('audit')
    report = Path(run('audit', WORKTREE, plan).stdout.strip())
    assert 'PI_CHECK_AUDIT_DONE' in report.read_text()
    print('PASS native read-only audit', flush=True)

    scenario('improve')
    paths = run('improve', repo, '--meta', '--context', 'PI_CHECK_IMPROVE: inspect native traces.').stdout.splitlines()
    assert len(paths) == 2 and 'HILL CLIMB:' in Path(paths[0]).read_text() and 'META CLIMB:' in Path(paths[1]).read_text()
    print('PASS native improvement fast and meta loops', flush=True)

    scenario('incomplete')
    failed = run('doc-write', WORKTREE, '--prompt', 'PI_CHECK_INCOMPLETE: write then return no report.', ok=False)
    retained = child(session, 'doc-write')
    assert retained['status'] == 'running' and Path(retained['id']).is_file()
    run('doc-write', WORKTREE, '--prompt', 'PI_CHECK_RECOVER: finish the retained native task.')
    assert child(session, 'doc-write')['id'] == retained['id']
    assert (WORKTREE / 'recovery-proof.txt').read_text() == 'RETAINED' and COUNTS['incomplete'] == 3
    print('PASS native failure retains work and reissuing resumes the exact child', flush=True)

    paired_control(repo, session, 'restart', 'restart')
    subprocess.run(['git', '-C', str(repo), 'branch', 'feat/unrelated', 'main'], check=True)
    paired_control(repo, session, 'restart-existing', 'restart')
    subprocess.run(['git', '-C', str(repo), 'push', '-q', 'origin', 'main:refs/heads/feat/remote-existing'], check=True)
    subprocess.run(['git', '-C', str(repo), 'fetch', '-q', 'origin'], check=True)
    paired_control(repo, session, 'restart-remote', 'restart')

    scenario('stall')
    run('execute', repo, '--pair', '--no-watch', '--worktree', '--branch', 'feat/pi-stall',
        '--prompt', 'PI_CHECK_STALL: exercise bounded native stall recovery.',
        env={**ENV, 'CEREBRO_PAIR_STALL': '3', 'CEREBRO_PAIR_STALL_BUSY': '0.5',
             'CEREBRO_PAIR_STALL_RETRIES': '1', 'CEREBRO_PAIR_STALL_BACKOFF': '0'})
    assert COUNTS['stall'] == 2
    native_sessions = Path(ENV['PI_CODING_AGENT_DIR']) / 'sessions'
    assert sum('PI_CHECK_STALL' in p.read_text() for p in native_sessions.rglob('*.jsonl')) == 1
    stall_marker, = (Path(ENV['CEREBRO_HOME']) / 'worktrees').glob('*/stall-pid')
    assert_tool_gone(stall_marker)
    print('PASS native stalled tool is bounded and the same session resumes', flush=True)

    scenario('timeout')
    run('execute', repo, '--worktree', '--branch', 'feat/pi-timeout',
        '--prompt', 'PI_CHECK_TIMEOUT: exercise the native wall-clock timeout.', ok=False,
        env={**ENV, 'CEREBRO_TIMEOUT': '3'})
    marker, = (Path(ENV['CEREBRO_HOME']) / 'worktrees').glob('*/timeout-pid')
    assert_tool_gone(marker)
    assert not (marker.parent / 'after-timeout.txt').exists()
    print('PASS native timeout reaps its shell tool and preserves resumable work', flush=True)

    scenario('detach')
    output = session / 'complete.out'
    run('detach', '--output', output, '--', 'doc-write', WORKTREE,
        '--prompt', 'PI_CHECK_DETACH: finish after the launcher disconnects.')
    job_path = next(p for p in (session / 'detached-jobs').glob('*.json') if json.loads(p.read_text()).get('output') == str(output.resolve()))
    detached_job = json.loads(job_path.read_text())
    completion = json.loads(run('wait', detached_job['id']).stdout)
    assert completion['exit_code'] == 0 and (WORKTREE / 'detached-proof.txt').read_text() == 'DETACHED'
    print('PASS native detached work survives launcher exit and reports completion', flush=True)

    scenario('cancel')
    output = session / 'cancel.out'
    result = run('detach', '--output', output, '--', 'execute', repo, '--worktree',
                 '--prompt', 'PI_CHECK_CANCEL: exercise cancellation.')
    job_path = next(p for p in (session / 'detached-jobs').glob('*.json') if json.loads(p.read_text()).get('output') == str(output.resolve()))
    job = json.loads(job_path.read_text())
    deadline = time.monotonic() + 25
    marker = None
    while time.monotonic() < deadline:
        marker = next((p for p in (Path(ENV['CEREBRO_HOME']) / 'worktrees').glob('*/cancellation-pid')), None)
        if marker:
            break
        time.sleep(0.1)
    assert marker, 'native cancellable tool did not start'
    tool_pid = int(marker.read_text())
    pi_pid = int(subprocess.check_output(['ps', '-p', str(tool_pid), '-o', 'ppid='], text=True).strip())
    pi_command = subprocess.check_output(['ps', '-p', str(pi_pid), '-o', 'command='], text=True).strip()
    assert pi_command == 'pi', pi_command
    run('cancel', job['id'])
    completion = json.loads(run('wait', job['id'], ok=False).stdout)
    assert completion['exit_code'] == 130, completion
    assert_tool_gone(marker)
    try:
        os.kill(pi_pid, 0)
    except ProcessLookupError:
        pass
    else:
        raise AssertionError('native Pi process survived cancellation: ' + str(pi_pid))
    assert not (marker.parent / 'after-cancel.txt').exists()
    print('PASS native detached cancellation reaps Pi and its shell tool; wait reports 130', flush=True)
    cancelled = next(row for row in children(session).values()
                     if Path(row['repo']).resolve() == marker.parent.resolve())
    assert cancelled['workspace']['branch'] == 'HEAD', 'cancellation should leave the initial branch observation'
    run('answer', cancelled['id'], 'Continue in the retained checkout and branch.')
    resumed = next(row for row in children(session).values() if row.get('id') == cancelled['id'])
    assert resumed['workspace']['branch'] == 'feat/pi-cancel'
    assert (marker.parent / 'after-resume.txt').read_text() == 'RECOVERED'
    print('PASS native cancellation resumes the exact child on its retained branch', flush=True)

    # The initial parent uses the real TUI, then resumes its recorded Pi file.
    scenario('')
    native.COUNTS.pop(('pi', 'worker'), None)
    executable = ENV['CEREBRO_PI_CMD']
    wrapper = native.ROOT / 'pi-parent-local'
    direct = 'for arg; do if [ "$arg" = rpc ]; then exec ' + shlex.quote(executable) + ' "$@"; fi; done\n'
    wrapper.write_text('#!/bin/sh\n' + direct + 'exec ' + shlex.quote(executable) + ' "$@" ' + shlex.quote('NATIVE_PARENT: delegate the native proof task.') + '\n')
    wrapper.chmod(0o755)
    parent_env = {**ENV, 'CEREBRO_PI_CMD': str(wrapper)}
    parent_session = native.parent(parent_env)
    bound = json.loads((parent_session / 'metadata.json').read_text())['foreign_session_id']
    scenario('resume')
    wrapper.write_text('#!/bin/sh\n' + direct + 'exec ' + shlex.quote(executable) + ' "$@" ' + shlex.quote('PI_CHECK_RESUME: inspect state and continue this parent.') + '\n')
    # Misleading ambient backend must not override the backend recorded in metadata.
    resumed = native.parent({**parent_env, 'CEREBRO_BACKEND': 'claude'}, ('--resume', parent_session.name), PARENT_DONE)
    assert resumed == parent_session
    assert json.loads((resumed / 'metadata.json').read_text())['foreign_session_id'] == bound
    assert COUNTS['resume'] == 2
    print('PASS native parent resumes the recorded session and backend', flush=True)
    scenario('resume')
    PARENT_DONE = 'PI_PARENT_LATEST_RESUMED'
    latest = native.parent({**parent_env, 'CEREBRO_BACKEND': 'claude'}, ('--resume',), PARENT_DONE)
    assert latest == parent_session and COUNTS['resume'] == 2
    assert json.loads((latest / 'metadata.json').read_text())['foreign_session_id'] == bound
    print('PASS native most-recent parent resume keeps the same Pi file', flush=True)
    native_mcp(ENV, repo)
    browser_check(ENV, WORKTREE)
    watch_checks(ENV, WORKTREE, session)
    print('Pi native feature checks passed', flush=True)


if __name__ == '__main__':
    try:
        main()
    finally:
        if ENV:
            for path in (Path(ENV['CEREBRO_HOME']) / 'sessions').glob('*/detached-jobs/*.json'):
                job = json.loads(path.read_text())
                if 'pid' in job and Path(job['status']).read_text().strip() in ('starting', 'running'):
                    session = path.parent.parent
                    subprocess.run([str(native.SOURCE / 'bin/cerebro'), 'cancel', job['id']],
                                   env={**ENV, 'CEREBRO_SESSION_ID': session.name, 'CEREBRO_SESSION_DIR': str(session)},
                                   capture_output=True, timeout=10)
        native.server.shutdown()
