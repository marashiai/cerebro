"""Read task authority from the controller's single durable packet."""
import json
from pathlib import Path


def task_packet(path):
    packet = json.loads(Path(path).read_text(encoding='utf-8'))['packet']
    return {name: packet[name] for name in ('goal', 'task', 'acceptance')}
