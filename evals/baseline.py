"""Native Codex sessions, recorded without changing their tools or model decisions."""

import json
import os
from pathlib import Path
import shlex
import sys

from runtime import LIB, process, toml


def workspaces(repo, directory):
    from fixtures import git
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


def run(directory, repo, prompt, settings, schema, role, *, env=None, options=(), instructions=''):
    directory.mkdir(parents=True, exist_ok=True)
    native = directory / 'native-parent'
    flags = list(options)
    effort = settings['efforts'][role]
    if effort:
        flags += ['-c', 'model_reasoning_effort=' + toml(effort)]
    native.write_text('#!/bin/sh\nexec ' + shlex.join([
        sys.executable, str(Path(__file__).with_name('native.py')), settings['codex'], *flags
    ]) + ' "$@"\n')
    native.chmod(0o700)
    if env is None:
        env = {key: value for key, value in os.environ.items() if not key.startswith('CEREBRO_')}
    env = {**env, 'CEREBRO_EVAL_DIR': str(directory), 'CEREBRO_EVAL_REPO': str(repo),
           'CEREBRO_CHILD_ROLE': role, 'CEREBRO_CHILD_INSTRUCTIONS': instructions,
           'CEREBRO_CHILD_EFFORT': effort or '', 'CEREBRO_JEV_ENABLED': '0',
           'CEREBRO_PAIR_IDLE': '0', 'CEREBRO_PAIR_STALL': '0', 'CEREBRO_PAIR_STALL_BUSY': '0'}
    if schema:
        prompt += '\nReturn one JSON object, without fences, matching this schema:\n' + json.dumps(schema)
    (directory / 'prompt.txt').write_text(prompt)
    argv = [sys.executable, str(LIB / 'python/pair_process.py'), 'codex', str(repo), '',
            settings['models'][role] or '', '', '', str(directory / 'parent.stdout.jsonl'), str(native)]
    outcome = process(argv, env, repo, directory / 'parent', prompt, settings['timeout'])
    messages = []
    for line in (directory / 'parent.stdout.jsonl').read_text().splitlines():
        event = json.loads(line)
        item = event.get('item', {})
        if event.get('type') == 'item.completed' and item.get('type') == 'agent_message':
            if item.get('phase') in (None, 'final_answer'):
                messages.append(item['text'])
    if not messages:
        raise RuntimeError('native Codex completed without a final answer')
    answer = messages[-1]
    (directory / ('answer.json' if schema else 'answer.md')).write_text(answer)
    outcome['answer'] = json.loads(answer) if schema else answer
    return outcome
