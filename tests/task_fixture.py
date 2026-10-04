"""Deterministic native final handoffs, configured independently per model role."""
import json
import os
from pathlib import Path


def result(prompt, role):
    config = json.loads(Path(os.environ['TASK_FIXTURE_CONFIG']).read_text())
    settings = config.get(role, {})
    state = settings.get('status', 'complete')
    if state == 'malformed':
        return 'prose without a structured handoff'
    acceptance = config['acceptance']
    handoff = {'status': state, 'summary': role + ' finished',
               'criteria': [{'criterion': settings.get('criterion_prefix', '') + item,
                             'result': settings.get('criterion_result', 'passed'),
                             'evidence': 'fixture observed actual native handoff'} for item in acceptance]}
    if state == 'question':
        handoff['question'] = 'Which behavior is intended?'
    if role == 'execute':
        handoff['evidence'] = ['bash tests/run.sh: fixture tests passed']
    else:
        handoff['findings'] = settings.get('findings', [])
    return json.dumps(handoff)
