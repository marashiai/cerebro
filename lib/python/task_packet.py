"""Read task authority from the controller's single durable packet."""
import json
from pathlib import Path


def task_packet(path):
    state = json.loads(Path(path).read_text(encoding='utf-8'))
    packet = state['packet']
    user_inputs = state['user_inputs']
    if not isinstance(user_inputs, list) or not user_inputs:
        raise ValueError('task authority requires captured original user inputs')
    return {'original_user_inputs': user_inputs,
            'supervisor_goal': packet['goal'],
            'supervisor_task_plan': packet['task'],
            'supervisor_acceptance_criteria': packet['acceptance']}
