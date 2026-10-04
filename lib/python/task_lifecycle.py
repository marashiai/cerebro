"""Durable implementation -> review lifecycle over the existing native runners."""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

from child_store_lib import _atomic_write, _now_iso, store_upsert
from task_workspace import branch_at, git, prepare, refresh, snapshot, validate
from user_input import snapshot as snapshot_user_inputs

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT.parent / 'bin' / 'cerebro'
STATES = {'complete', 'question', 'blocked', 'unfinished', 'failed'}


def packet_from(raw):
    packet = json.loads(raw)
    if not isinstance(packet, dict):
        raise ValueError('task packet must be a JSON object')
    allowed = {'goal', 'task', 'acceptance', 'repo', 'base', 'branch', 'worktree', 'models', 'correction_of'}
    if packet.keys() - allowed:
        raise ValueError('unknown task packet fields: ' + ', '.join(sorted(packet.keys() - allowed)))
    for name in ('goal', 'task', 'repo', 'base'):
        if not isinstance(packet.get(name), str) or not packet[name].strip():
            raise ValueError('task packet requires nonempty ' + name)
    if not Path(packet['repo']).is_absolute():
        raise ValueError('task repo must be absolute')
    acceptance = packet.get('acceptance')
    if not isinstance(acceptance, list) or not acceptance or any(not isinstance(item, str) or not item.strip() for item in acceptance):
        raise ValueError('acceptance must be a nonempty array of criteria')
    if not isinstance(packet.get('worktree', False), bool):
        raise ValueError('worktree must be true or false')
    if 'branch' in packet and (not isinstance(packet['branch'], str) or not packet['branch']):
        raise ValueError('branch must be a nonempty branch name')
    if 'correction_of' in packet:
        if not isinstance(packet['correction_of'], str) or not packet['correction_of'].isalnum():
            raise ValueError('correction_of must be a task ID')
        if 'worktree' in packet or 'branch' in packet:
            raise ValueError('a correction reuses its reviewed task checkout; omit worktree and branch')
    models = packet.get('models', {})
    if not isinstance(models, dict) or models.keys() - {'implementor', 'reviewer'}:
        raise ValueError('models accepts implementor and reviewer settings')
    resolved = {}
    for role, model_var, effort_var in (
        ('implementor', 'CEREBRO_MODEL', 'CEREBRO_IMPLEMENTOR_EFFORT'),
        ('reviewer', 'CEREBRO_REVIEW_MODEL', 'CEREBRO_REVIEW_EFFORT'),
    ):
        settings = models.get(role, {})
        if not isinstance(settings, dict) or settings.keys() - {'model', 'effort'}:
            raise ValueError(role + ' settings accept model and effort')
        resolved[role] = {'model': settings.get('model', os.environ.get(model_var, '')),
                          'effort': settings.get('effort', os.environ.get(effort_var, ''))}
        if any(not isinstance(value, str) for value in resolved[role].values()):
            raise ValueError(role + ' model and effort must be strings')
    packet['models'] = resolved
    return packet


def user_input_spec(user_inputs):
    rendered = []
    for item in user_inputs:
        content = []
        for block in item['content']:
            if block.get('type') == 'text' and isinstance(block.get('text'), str):
                content.append(block['text'])
            else:
                content.append(json.dumps(block, ensure_ascii=False, indent=2))
        rendered.append('## Captured input ' + item['id'] + '\n\n' + '\n'.join(content))
    return ('# Original user inputs\n\nInputs are ordered by capture time. The latest actual user clarification '
            'supersedes earlier requests; earlier requests remain context. The current supervisor plan selects '
            'delegated work, and these original inputs remain requirements even when the plan omits them.\n\n' +
            '\n\n'.join(rendered) + '\n\n')


def spec(packet, user_inputs):
    return (user_input_spec(user_inputs) + '# Supervisor goal\n\n' + packet['goal'] +
            '\n\n# Supervisor task and plan\n\n' + packet['task'] +
            '\n\n# Supervisor acceptance criteria\n\n' +
            '\n'.join('- ' + item for item in packet['acceptance']) + '\n')


def merge_user_inputs(saved, captured):
    merged = list(saved)
    known = {item['id'] for item in saved}
    merged.extend(item for item in captured if item['id'] not in known)
    return merged


def refresh_user_inputs(session, directory, state):
    path = session / 'user-inputs.json'
    if not path.is_file():
        return
    merged = merge_user_inputs(state['user_inputs'], snapshot_user_inputs(session))
    if [item['id'] for item in merged] == [item['id'] for item in state['user_inputs']]:
        return
    state['user_inputs'] = merged
    save(directory, state)
    rendered = spec(state['packet'], merged)
    (directory / 'spec.md').write_text(rendered)
    (session / 'spec.md').write_text(rendered)


def handoff(path, role, acceptance):
    result = json.loads(path.read_text())
    if not isinstance(result, dict) or result.get('status') not in STATES:
        raise ValueError('closing JSON requires an explicit stage status')
    if not isinstance(result.get('summary'), str) or not result['summary'].strip():
        raise ValueError('closing JSON requires a summary')
    if result['status'] == 'question' and not result.get('question'):
        raise ValueError('question handoff requires the question')
    if result['status'] == 'complete':
        criteria = result.get('criteria')
        # Results map to acceptance criteria by position; restated wording is not compared.
        if not isinstance(criteria, list) or len(criteria) != len(acceptance):
            raise ValueError('complete handoff requires one result per acceptance criterion, in packet order')
        for item in criteria:
            if item.get('result') not in ('passed', 'failed', 'unverified') or not isinstance(item.get('evidence'), str) or not item['evidence'].strip():
                raise ValueError('every criterion requires a result and concrete evidence')
        if role == 'execute' and (not isinstance(result.get('evidence'), list) or any(not isinstance(item, str) for item in result['evidence'])):
            raise ValueError('implementation handoff requires an evidence array')
        if role == 'review':
            findings = result.get('findings')
            if not isinstance(findings, list):
                raise ValueError('review handoff requires a findings array')
            ids = set()
            for finding in findings:
                for field in ('id', 'file', 'problem', 'evidence', 'requested_change'):
                    if not isinstance(finding.get(field), str) or not finding[field].strip():
                        raise ValueError('each finding requires ' + field)
                if finding.get('severity') not in ('high', 'medium', 'low') or not isinstance(finding.get('line'), int) or isinstance(finding['line'], bool) or finding['line'] < 1:
                    raise ValueError('each finding requires severity and a positive line number')
                if finding.get('basis') not in ('requirement', 'robustness'):
                    raise ValueError('each finding requires basis requirement or robustness')
                if finding['basis'] == 'requirement' and (not isinstance(finding.get('requirement'), str)
                                                          or not finding['requirement'].strip()):
                    raise ValueError('a requirement finding must quote the stated requirement')
                if finding['id'] in ids:
                    raise ValueError('finding IDs must be unique')
                ids.add(finding['id'])
    return result


def unfinished_tools(state, role):
    return [tool for receipt in state.get('unfinished_receipts', {}).get(role, [])
            for tool in json.loads(Path(receipt).read_text())]


def reviewed_task(tasks, packet):
    path = tasks / packet['correction_of'] / 'task.json'
    prior = json.loads(path.read_text()) if path.is_file() else {}
    if prior.get('stage') != 'done' or not prior.get('reviewed_tree'):
        raise ValueError('correction_of must name a completed, reviewed task')
    if (prior['packet']['repo'], prior['packet']['base']) != (packet['repo'], packet['base']):
        raise ValueError('a correction keeps its reviewed task repo and base')
    validate(prior['workspace'])
    if branch_at(prior['workspace']['path']) != prior['workspace']['branch']:
        raise ValueError('the reviewed checkout has left branch ' + prior['workspace']['branch'])
    return prior


def save(directory, state):
    state['updated_at'] = _now_iso()
    _atomic_write(str(directory / 'task.json'), state)


def response(directory, state):
    result = {'task_id': directory.name, 'stage': state['stage'], 'status': state.get('status', 'running'),
              'workspace': state['workspace']['path'], 'packet': state['packet'],
              'user_input_ids': [item['id'] for item in state['user_inputs']],
              'user_input_count': len(state['user_inputs'])}
    captured_path = directory.parent.parent / 'user-inputs.json'
    captured = snapshot_user_inputs(captured_path.parent) if captured_path.is_file() else []
    captured_ids = {item['id'] for item in state['user_inputs']}
    result['pending_user_input_ids'] = [item['id'] for item in captured if item['id'] not in captured_ids]
    models = state['packet']['models']
    implementation_model, review_model = models['implementor']['model'], models['reviewer']['model']
    result['review_model_relation'] = ('native defaults unresolved' if not implementation_model or not review_model
                                       else 'same configured model' if implementation_model == review_model else 'different configured models')
    for role, name in (('execute', 'implementation'), ('review', 'review')):
        output = state.get(role + '_output')
        if output:
            result[name] = json.loads(Path(output).read_text())
            result[name + '_path'] = output
        tools = unfinished_tools(state, role)
        if tools:
            result[name + '_unfinished_tools'] = tools
    if state.get('error'):
        result['error'] = state['error']
    return result


def run(args):
    session = Path(os.environ['CEREBRO_SESSION_DIR'])
    tasks = session / 'tasks'
    tasks.mkdir(exist_ok=True)
    store = str(session / 'child-sessions.json')
    if args.resume:
        if not args.resume.isalnum():
            raise ValueError('invalid task ID')
        directory = tasks / args.resume
        if not (directory / 'task.json').is_file():
            raise ValueError('no such task: ' + args.resume)
        packet = None
    else:
        if args.answer:
            raise ValueError('--answer requires --resume')
        user_inputs = snapshot_user_inputs(session)
        packet = packet_from(Path(args.packet).read_text() if args.packet else sys.stdin.read())
        task_id = hashlib.sha256(json.dumps({'packet': packet, 'user_inputs': [item['id'] for item in user_inputs]},
                                            sort_keys=True).encode()).hexdigest()[:16]
        directory = tasks / task_id
        directory.mkdir(exist_ok=True)
    with (directory / 'lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('task is already running: ' + directory.name)
        if (directory / 'task.json').exists():
            state = json.loads((directory / 'task.json').read_text())
            if packet is not None and state['packet'] != packet:
                raise ValueError('task packet differs from its persisted record')
            packet = state['packet']
            validate(state['workspace'])
        elif 'correction_of' in packet:
            prior = reviewed_task(tasks, packet)
            store_upsert(store, directory.name + '-execute', {'workspace': prior['workspace']})
            state = {'packet': packet, 'workspace': prior['workspace'], 'review_base': prior['review_base'],
                     'correction': {'review': prior['review_output'], 'tree': prior['reviewed_tree']},
                     'user_inputs': user_inputs, 'stage': 'execute', 'status': 'running'}
            save(directory, state)
        else:
            isolated = str(Path(os.environ['CEREBRO_HOME']) / 'worktrees' / (session.name + '-' + directory.name)) if packet.get('worktree') else ''
            review_base = git(packet['repo'], 'rev-parse', '--verify', packet['base'] + '^{commit}')
            workspace = prepare(packet['repo'], isolated, packet.get('branch', ''), packet['base'], store,
                                directory.name + '-execute', False)
            state = {'packet': packet, 'workspace': workspace, 'review_base': review_base,
                     'user_inputs': user_inputs, 'stage': 'execute', 'status': 'running'}
            save(directory, state)
        (directory / 'spec.md').write_text(spec(packet, state['user_inputs']))
        (session / 'spec.md').write_text(spec(packet, state['user_inputs']))
        if state['stage'] == 'done':
            if args.answer:
                raise ValueError('task is complete; start a focused correction packet')
            print(json.dumps(response(directory, state)))
            return 0
        if state['status'] == 'question' and not args.answer:
            print(json.dumps(response(directory, state)))
            return 0
        if args.answer and state['status'] != 'question':
            raise ValueError('only a question stage accepts --answer')
        while state['stage'] in ('execute', 'review'):
            role = state['stage']
            settings = packet['models']['implementor' if role == 'execute' else 'reviewer']
            key = directory.name + '-' + role
            records = json.loads(Path(store).read_text()) if Path(store).exists() else {}
            child = records.get(key, {})
            prior = child.get('id', '') if child.get('status') == 'running' else ''
            if state['status'] in ('failed', 'question', 'unfinished', 'blocked') and not prior:
                raise ValueError('unfinished stage has no native conversation to resume; start a corrected task packet')
            attempt = state.get('attempt') if state['status'] in ('running', 'failed', 'unfinished', 'blocked') else None
            receipt = Path(attempt['log']).with_suffix('.exit') if attempt else None
            recover = state['status'] == 'running' and receipt and receipt.is_file()
            if recover:
                rc = int(receipt.read_text().strip())
            else:
                refresh_user_inputs(session, directory, state)
                if attempt:
                    prompt = Path(attempt['prompt']).read_text()
                    prompted_ids = set(attempt.get('user_input_ids', []))
                    additions = [item for item in state['user_inputs'] if item['id'] not in prompted_ids]
                    if additions:
                        prompt += '\n\nNew original user input captured since this stage prompt was created:\n\n'
                        prompt += user_input_spec(additions)
                    if state['status'] == 'unfinished' and state.get('error'):
                        prompt += ('\n\nThe controller rejected your previous closing JSON: ' + state['error'] +
                                   '\nReturn a corrected closing JSON object.\n')
                else:
                    prompt = (ROOT / 'payloads' / 'prompts' / (role + '.md')).read_text()
                    prompt += '\n\n' + spec(packet, state['user_inputs'])
                    prompt += '\nSelected checkout: ' + state['workspace']['path'] + '\nReview base commit: ' + state['review_base'] + '\n'
                    if role == 'review':
                        implementation = json.loads(Path(state['execute_output']).read_text())
                        prompt += '\nImplementation test evidence (verify independently):\n' + json.dumps(implementation.get('evidence', [])) + '\n'
                        tools = unfinished_tools(state, 'execute')
                        if tools:
                            prompt += ('\nImplementation tool calls still running when its turn ended; their results '
                                       'never reached the implementor:\n' + json.dumps(tools) + '\n')
                        # A later correction diffs from exactly what this review saw.
                        state['reviewed_tree'] = snapshot(state['workspace']['path'])
                        if 'correction' in state:
                            correction = state['correction']
                            prompt += ('\n' + (ROOT / 'payloads' / 'prompts' / 'correction-review.md').read_text() +
                                       '\nEarlier review report:\n' + Path(correction['review']).read_text().strip() +
                                       '\n\nCorrection diff: git diff ' + correction['tree'] + ' ' +
                                       state['reviewed_tree'] + '\n')
                    if state['status'] == 'restarted':
                        prompt += '\nSupervisor restart diagnosis; inspect retained work and correct this concern:\n' + state.get('error', '') + '\n'
                    if args.answer:
                        prompt += '\nSupervisor answer; continue from the retained work:\n' + args.answer + '\n'
                        args.answer = None
                child_log = session / 'children' / (role + '-' + uuid.uuid4().hex + '.jsonl')
                prompt_file = child_log.with_suffix('.prompt')
                prompt_file.write_text(prompt)
                attempt = {'log': str(child_log), 'prompt': str(prompt_file),
                           'user_input_ids': [item['id'] for item in state['user_inputs']]}
                state['attempt'] = attempt
                state['status'] = 'running'
                state.pop('error', None)
                save(directory, state)
                proc = subprocess.run([str(CLI), '_task-stage', str(directory), role, str(prompt_file), prior,
                                       settings['model'], settings['effort'], str(child_log)], pass_fds=(lock.fileno(),))
                rc = proc.returncode
            if role == 'execute':
                state['workspace'] = refresh(store, key)
            unfinished = Path(attempt['log']).with_suffix('.unfinished.json')
            if unfinished.is_file():
                receipts = state.setdefault('unfinished_receipts', {}).setdefault(role, [])
                if str(unfinished) not in receipts:
                    receipts.append(str(unfinished))
            if rc:
                restart = Path(attempt['log']).with_suffix('.restart')
                if restart and restart.is_file():
                    state['status'] = 'restarted'
                    state['error'] = restart.read_text()
                else:
                    state['status'] = 'failed'
                    state['error'] = 'native ' + role + ' stage exited ' + str(rc)
                save(directory, state)
                print(json.dumps(response(directory, state)))
                return rc
            try:
                output = Path(attempt['log']).with_suffix('.reply')
                result = handoff(output, role, packet['acceptance'])
            except (ValueError, TypeError, KeyError, AttributeError) as error:
                state['status'] = 'unfinished'
                state['error'] = 'invalid ' + role + ' handoff: ' + str(error) + '; original reply retained at ' + str(output)
                save(directory, state)
                print(json.dumps(response(directory, state)))
                return 1
            state[role + '_output'] = str(output)
            state.pop('attempt', None)
            state['status'] = result['status']
            if result['status'] == 'complete':
                state['stage'] = 'review' if role == 'execute' else 'done'
            save(directory, state)
            if result['status'] != 'complete':
                print(json.dumps(response(directory, state)))
                return 0
            store_upsert(store, key, {'status': 'done', 'updated_at': _now_iso()})
        print(json.dumps(response(directory, state)))
        return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument('--packet', help='JSON task packet file; otherwise read stdin')
    source.add_argument('--resume', help='resume an unfinished task ID')
    parser.add_argument('--answer', help='answer the current stage question')
    return run(parser.parse_args())


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        sys.exit('cerebro execute: ' + str(error))
