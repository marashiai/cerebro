"""Launch the native Codex supervisor with its normal configuration and tools."""

import json
import os
from pathlib import Path
import sys


def toml(value):
    if isinstance(value, dict):
        return '{' + ','.join(json.dumps(k) + '=' + toml(v) for k, v in value.items()) + '}'
    return json.dumps(value)


def supervisor_options(session_dir):
    config = json.loads((Path(session_dir) / 'tools-supervisor.json').read_text())
    server = config['mcpServers']['cerebro']
    server.update(enabled=True, required=True, tool_timeout_sec=86400, env_vars=list(os.environ))
    return ['-c', 'mcp_servers.cerebro=' + toml(server),
            '-c', 'features.code_mode.direct_only_tool_namespaces=' + toml(['mcp__cerebro'])]


def main():
    executable, role, cwd, session_dir, native_id, model = sys.argv[1:]
    if role != 'supervisor':
        raise ValueError('interactive Codex launch requires supervisor role')
    payloads = Path(__file__).resolve().parent.parent / 'payloads'
    instructions = (payloads / 'skills' / 'cerebro-supervisor' / 'SKILL.md').read_text()
    options = supervisor_options(session_dir)
    options += ['-c', 'developer_instructions=' + toml(instructions)]
    options += ['-c', 'notify=' + toml(['python3', str(Path(__file__).with_name('codex_notify.py')), session_dir])]
    if model:
        options += ['--model', model]
    effort = os.environ.get('CEREBRO_SUPERVISOR_EFFORT')
    if effort:
        options += ['-c', 'model_reasoning_effort=' + toml(effort)]
    argv = [executable, *options]
    if native_id:
        argv += ['resume', native_id]
    os.chdir(cwd)
    os.execvp(executable, argv)


if __name__ == '__main__':
    main()
