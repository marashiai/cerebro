"""Launch native Codex with a guarded role, preserving native provider/auth config."""

import json
import os
from pathlib import Path
import subprocess
import sys

# A read-only sandbox still permits shell commands. Guarded roles must also
# disable native executors and unrelated capabilities.
DISABLED = ['apps', 'plugins', 'hooks', 'browser_use', 'browser_use_external',
            'computer_use', 'in_app_browser', 'code_mode_host',
            'shell_tool', 'unified_exec',
            'image_generation', 'skill_mcp_dependency_install', 'multi_agent',
            'multi_agent_v2']


def toml(value):
    if isinstance(value, dict):
        return '{' + ','.join(json.dumps(k) + '=' + toml(v) for k, v in value.items()) + '}'
    return json.dumps(value)


def guarded_options(executable, role, cwd, session_dir):
    if role not in ('supervisor', 'reviewer'):
        raise ValueError('unsupported command role: ' + role)
    options = ['-c', 'project_root_markers=[]']
    for feature in DISABLED:
        options += ['--disable', feature]
    # Code-mode models still need the guarded tool, without a yielding executor
    # between the native parent and Cerebro's blocking completion response.
    options += ['-c', 'features.code_mode=' + toml({
        'enabled': False, 'direct_only_tool_namespaces': ['mcp__cerebro']})]
    # Empty tables merge with existing configuration. Explicitly close each
    # effective server, including IDs containing dots, in one TOML value.
    inventory = subprocess.run([executable, *options, 'mcp', 'list', '--json'], cwd=cwd,
                               text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if inventory.returncode:
        raise RuntimeError('cannot inventory Codex MCP servers: ' + inventory.stderr.strip())
    servers = {server['name']: {'enabled': False} for server in json.loads(inventory.stdout)}
    cerebro = Path(__file__).resolve().parent.parent.parent / 'bin' / 'cerebro'
    config = json.loads((Path(session_dir) / ('tools-' + role + '.json')).read_text())
    env = config['mcpServers']['cerebro']['env']
    env['CEREBRO_SESSION_DIR'] = session_dir
    env['CEREBRO_ROLE'] = role
    servers['cerebro'] = {'command': str(cerebro), 'args': ['tools', role],
                          'env': env, 'env_vars': list(os.environ),
                          'enabled': True, 'required': True, 'enabled_tools': ['command'],
                          'tool_timeout_sec': 86400,
                          'tools': {'command': {'approval_mode': 'approve'}}}
    options += ['-c', 'mcp_servers=' + toml(servers),
                '-c', 'approval_policy="never"', '-c', 'sandbox_mode="read-only"']
    return options


def main():
    executable, role, cwd, session_dir, native_id, model = sys.argv[1:]
    options = guarded_options(executable, role, cwd, session_dir)
    payloads = Path(__file__).resolve().parent.parent / 'payloads'
    skill = payloads / 'skills' / ('cerebro-' + role) / 'SKILL.md'
    instructions = skill.read_text()
    options += ['--no-daemon', '--strict-config', '-c', 'developer_instructions=' + toml(instructions)]
    options += ['-c', 'notify=' + toml(['python3', str(Path(__file__).with_name('codex_notify.py')), session_dir])]
    if model:
        options += ['--model', model]
    argv = [executable, *options, '-s', 'read-only']
    if native_id:
        argv += ['resume', native_id]
    os.chdir(cwd)
    os.execvp(executable, argv)


if __name__ == '__main__':
    main()
