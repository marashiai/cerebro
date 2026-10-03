"""A single native coding agent with no Cerebro supervisor or tools."""

import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from fixtures import git
from native import TestJournal
from runtime import file_hashes, process, toml, write_json


def workspaces(repo, directory):
    roots = set()
    for current, dirs, files in os.walk(directory):
        if '.git' in dirs or '.git' in files:
            root = Path(current).resolve()
            marker = root / '.git'
            if (root.is_relative_to(directory.resolve()) and not marker.is_symlink()
                    and git(root, 'rev-parse', '--show-toplevel') == str(root)):
                roots.add(root)
        dirs[:] = [name for name in dirs if name not in ('.git', '__pycache__')]
    return sorted(roots)


def run(directory, repo, prompt, settings, schema):
    schema_path = directory / 'output-schema.json'
    write_json(schema_path, schema)
    (directory / 'prompt.txt').write_text(prompt)
    env = {key: value for key, value in os.environ.items() if not key.startswith('CEREBRO_')}
    env.update(CEREBRO_EVAL_DIR=str(directory), CEREBRO_EVAL_REPO=str(repo), CEREBRO_CHILD_ROLE='baseline')
    argv = [sys.executable, str(Path(__file__).resolve()), settings['codex'],
            settings['models']['baseline'], settings['efforts']['baseline']]
    outcome = process(argv, env, repo, directory / 'parent', prompt, settings['timeout'])
    outcome['answer'] = json.loads((directory / 'answer.json').read_text())
    return outcome


def main():
    executable, model, effort = sys.argv[1:]
    directory, repo = (Path(os.environ[name]).resolve() for name in ('CEREBRO_EVAL_DIR', 'CEREBRO_EVAL_REPO'))
    worker = uuid.uuid4().hex
    journal = TestJournal(directory, worker, 'baseline')
    observations = directory / ('baseline-observations-' + worker + '.jsonl')
    previous = {str(root): file_hashes(root) for root in workspaces(repo, directory)}
    thread = None

    def record(value):
        with observations.open('a') as out:
            out.write(json.dumps({'time': time.time(), 'worker_id': worker, 'role': 'baseline', **value}) + '\n')

    argv = [executable, '--no-daemon', '--strict-config', '--disable', 'multi_agent',
            '--disable', 'multi_agent_v2', '--disable', 'apps', '--disable', 'plugins', '--disable', 'hooks',
            '-c', 'project_doc_max_bytes=0', '-c', 'developer_instructions=""',
            '-c', 'model_reasoning_effort=' + toml(effort), '-c', 'approval_policy="never"',
            'exec', '--ignore-user-config', '--ignore-rules', '--ephemeral', '--skip-git-repo-check',
            '--sandbox', 'danger-full-access', '--json', '--color', 'never', '--model', model,
            '--output-schema', str(directory / 'output-schema.json'),
            '--output-last-message', str(directory / 'answer.json'), '-']
    record({'type': 'model_requested', 'model': model, 'effort': effort, 'source_root': str(repo)})
    proc = subprocess.Popen(argv, stdin=sys.stdin.buffer, stdout=subprocess.PIPE, stderr=sys.stderr,
                            env={**os.environ, **journal.environment()})
    for line in proc.stdout:
        event = json.loads(line)
        if event.get('type') == 'thread.started':
            thread = event['thread_id']
        if event.get('type') in ('item.completed', 'turn.completed'):
            roots = workspaces(repo, directory)
            item = event.get('item', {})
            if item.get('type') in ('command_execution', 'file_change'):
                for root in roots:
                    after = file_hashes(root)
                    before = previous.get(str(root), {})
                    changed = sorted(name for name in before.keys() | after.keys()
                                     if before.get(name) != after.get(name))
                    record({'type': 'activity', 'thread_id': thread, 'source_root': str(root), 'cwd': str(repo),
                            'command': item.get('command'), 'exit_code': item.get('exit_code'),
                            'output': (item.get('aggregated_output') or '')[-4000:],
                            'source': after, 'changed_files': changed, 'unchanged_during_check': before == after})
                    previous[str(root)] = after
            for receipt in journal.take(roots, thread):
                record({'type': 'tests', **receipt})
        sys.stdout.buffer.write(line)
        sys.stdout.buffer.flush()
    for receipt in journal.take(workspaces(repo, directory), thread):
        record({'type': 'tests', **receipt})
    return proc.wait()


if __name__ == '__main__':
    sys.exit(main())
