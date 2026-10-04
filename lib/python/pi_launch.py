"""Launch Pi with explicit tools/resources while preserving native providers."""

import json
import os
from pathlib import Path
import sys


PAYLOADS = Path(__file__).resolve().parent.parent / 'payloads'
ROLES = ('supervisor', 'execute', 'review')


def validate_session(path):
    session = Path(path)
    if not session.is_absolute() or not session.is_file() or not session.stat().st_size:
        raise ValueError('Pi resume requires an existing nonempty absolute session file: ' + path)
    with session.open() as stream:
        header = json.loads(stream.readline())
    if header.get('type') != 'session' or not isinstance(header.get('id'), str) or not header['id']:
        raise ValueError('invalid Pi session header: ' + path)
    return header['id']


def run_argv(executable, role, cwd, session_dir, resume, model, instructions):
    if role not in ROLES:
        raise ValueError('unsupported Pi role: ' + role)
    argv = [executable, '--append-system-prompt', instructions]
    if role == 'supervisor':
        config = Path(session_dir) / 'tools-supervisor.json'
        json.loads(config.read_text())['mcpServers']['cerebro']
        argv += ['-e', str(PAYLOADS / 'pi' / 'cerebro.ts'),
                 '--cerebro-role', role, '--cerebro-mcp-config', str(config)]
    effort = os.environ.get('CEREBRO_SUPERVISOR_EFFORT' if role == 'supervisor' else 'CEREBRO_CHILD_EFFORT')
    if effort:
        argv += ['--thinking', effort]
    if model:
        argv += ['--model', model]
    if resume:
        validate_session(resume)
        argv += ['--session', resume]
    return argv


def main():
    executable, role, cwd, session_dir, resume, model = sys.argv[1:]
    if role != 'supervisor':
        raise ValueError('interactive Pi launch requires supervisor role')
    argv = run_argv(executable, role, cwd, session_dir, resume, model,
                    str(Path(cwd) / 'system-prompt.md'))
    argv += ['--cerebro-bind', session_dir]
    if not resume:
        argv += ['--session-id', Path(session_dir).name]
    os.chdir(cwd)
    os.execvp(executable, argv)


if __name__ == '__main__':
    main()
