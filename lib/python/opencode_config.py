"""OpenCode V2 role policy; role instructions remain backend-neutral skills."""

import json
import os
from pathlib import Path
import sys


def config(home, role, model=''):
    home = Path(home)
    overlay = json.loads(os.environ.get('OPENCODE_CONFIG_CONTENT') or '{}')
    parent = role in ('supervisor', 'observer')
    reviewer = role in ('review', 'audit', 'improve')
    allowed = ['read', 'grep', 'glob', 'skill', 'external_directory']
    rules = [{'action': '*', 'resource': '*', 'effect': 'deny' if parent or reviewer else 'allow'}]
    if parent or reviewer:
        if parent:
            allowed += ['question']
        allowed += ['cerebro_command']
        rules += [{'action': action, 'resource': '*', 'effect': 'allow'} for action in allowed]
    else:
        rules += [{'action': 'question', 'resource': '*', 'effect': 'deny'},
                  {'action': 'subagent', 'resource': '*', 'effect': 'deny'}]
    overlay.update(permissions=rules, update='disable', share='disabled',
                   default_agent='build' if parent else 'general')
    skills = str(home / '.agents' / 'skills')
    overlay['skills'] = [skills]
    payloads = Path(__file__).resolve().parent.parent / 'payloads'
    skill_role = 'supervisor' if role == 'supervisor' else role
    if reviewer:
        instructions = [str(payloads / 'prompts' / 'reviewer-note.md')]
    else:
        instructions = [str(home / '.agents' / 'skills' / ('cerebro-' + skill_role) / 'SKILL.md')]
    if not parent:
        instructions.append(str(payloads / 'prompts' / 'noninteractive-note.md'))
    overlay['instructions'] = instructions
    if model:
        overlay['model'] = model
    plugin = str(home / 'opencode-plugin')
    overlay['plugins'] = [p for p in overlay.get('plugins', [])
                          if p != plugin and not (isinstance(p, dict) and p.get('package') == plugin)]
    overlay['plugins'].append({'package': plugin, 'options': {
        'role': role, 'permissions': rules}})
    if parent or reviewer:
        mcp = overlay.setdefault('mcp', {})
        servers = mcp.setdefault('servers', {})
        for server in servers.values():
            server['disabled'] = True
        command_role = role if parent else 'reviewer'
        config = json.loads((Path(os.environ['CEREBRO_SESSION_DIR']) / ('tools-' + command_role + '.json')).read_text())
        env = config['mcpServers']['cerebro']['env']
        servers['cerebro'] = {'type': 'local', 'command': [str(payloads.parent.parent / 'bin' / 'cerebro'),
                             'tools', env['CEREBRO_ROLE']], 'environment': env,
                             'codemode': False, 'timeout': {'execution': 86400000}}
    else:
        # A child must not inherit its parent's privileged control channel.
        overlay.get('mcp', {}).get('servers', {}).pop('cerebro', None)
    return overlay


if __name__ == '__main__':
    print(json.dumps(config(*sys.argv[1:])))
