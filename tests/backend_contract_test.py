"""Assert the native OpenCode role policy from its executable configuration."""

import fnmatch
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'lib' / 'python'))
from opencode_config import config


def allowed(configuration, action, resource='*'):
    decision = None
    for rule in configuration['permissions']:
        if fnmatch.fnmatchcase(action, rule['action']) and fnmatch.fnmatchcase(resource, rule['resource']):
            decision = rule['effect']
    return decision == 'allow'


def check(role):
    if role in ('supervisor', 'observer', 'review', 'audit', 'improve'):
        session = Path(os.environ['CEREBRO_HOME']) / 'sessions' / os.environ['CEREBRO_SESSION_ID']
        session.mkdir(parents=True, exist_ok=True)
        os.environ['CEREBRO_SESSION_DIR'] = str(session)
        command_role = role if role in ('supervisor', 'observer') else 'reviewer'
        libraries = Path(__file__).resolve().parent.parent / 'lib'
        script = 'CEREBRO_LIB_DIR="$1"; . "$1/config.sh"; . "$1/backend.sh"; backend_supervisor_config "$2"'
        prepared = subprocess.run(['bash', '-c', script, '_', str(libraries), command_role],
                                  text=True, capture_output=True, timeout=5)
        assert prepared.returncode == 0, prepared.stderr
    configuration = config(os.environ['CEREBRO_HOME'], role)
    assert allowed(configuration, 'skill'), 'shared skills must be available'
    assert configuration['skills'] == [str(Path(os.environ['CEREBRO_HOME']) / '.agents' / 'skills')]
    if role in ('supervisor', 'observer', 'review', 'audit', 'improve'):
        for action in ('edit', 'shell', 'subagent', 'unrelated_tool'):
            assert not allowed(configuration, action), role + ' must deny ' + action
        assert allowed(configuration, 'cerebro_command'), 'guarded Cerebro commands must be available'
        assert configuration['mcp']['servers']['cerebro']['command'][-1] in ('supervisor', 'observer', 'reviewer')
    else:
        for action in ('edit', 'shell'):
            assert allowed(configuration, action), role + ' must support ' + action
        assert not allowed(configuration, 'question'), 'headless children cannot ask an interactive question'
        assert not allowed(configuration, 'subagent'), 'development stays in the delegated child'
    assert configuration['default_agent'] == ('build' if role in ('supervisor', 'observer') else 'general')


if __name__ == '__main__':
    check(sys.argv[1])
