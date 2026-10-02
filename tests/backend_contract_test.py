"""Native Pi launch resources keep parent/reviewer authority explicit."""

import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'lib' / 'python'))
from pi_launch import PAYLOADS, run_argv


def check(role):
    home = Path(os.environ['CEREBRO_HOME'])
    session = home / 'sessions' / os.environ['CEREBRO_SESSION_ID']
    session.mkdir(parents=True, exist_ok=True)
    os.environ['CEREBRO_SESSION_DIR'] = str(session)
    native_role = 'reviewer' if role in ('review', 'audit', 'improve') else role
    restricted = native_role in ('supervisor', 'reviewer')
    if restricted:
        libraries = Path(__file__).resolve().parent.parent / 'lib'
        script = 'CEREBRO_LIB_DIR="$1"; . "$1/config.sh"; . "$1/backend.sh"; backend_supervisor_config "$2"'
        prepared = subprocess.run(['bash', '-c', script, '_', str(libraries), native_role],
                                  text=True, capture_output=True, timeout=5)
        assert prepared.returncode == 0, prepared.stderr
    instructions = 'literal managed instructions `code` $(never execute)'
    argv = run_argv('fixture-pi', native_role, str(home), str(session), '', 'native/model', instructions)
    assert argv[0] == 'fixture-pi'
    assert all(flag in argv for flag in ('--no-extensions', '--no-skills', '--no-prompt-templates', '--no-approve'))
    assert argv[argv.index('--skill') + 1] == str(PAYLOADS / 'skills')
    assert argv[argv.index('--append-system-prompt') + 1] == instructions
    assert argv[argv.index('--model') + 1] == 'native/model'
    if restricted:
        tools = argv[argv.index('--tools') + 1].split(',')
        assert tools == ['mcp__cerebro__command']
        assert '--no-context-files' in argv
        assert argv[argv.index('-e') + 1] == str(PAYLOADS / 'pi' / 'cerebro.ts')
        assert argv[argv.index('--cerebro-role') + 1] == native_role
        configuration = Path(argv[argv.index('--cerebro-mcp-config') + 1])
        assert configuration == session / ('tools-' + native_role + '.json')
        server = json.loads(configuration.read_text())['mcpServers']['cerebro']
        assert server['args'] == ['tools', native_role]
        assert server['env']['CEREBRO_ROLE'] == native_role
    else:
        assert '--tools' not in argv, 'writer must preserve native tool selection and MCP activation'
        assert [argv[i + 1] for i, arg in enumerate(argv) if arg == '-e'] == [
            'builtin:mcp', 'builtin:codemode', 'builtin:tool-search']
        assert '--cerebro-mcp-config' not in argv, 'writer acquired a privileged command channel'
    for invalid in ('observer', 'unknown-role'):
        try:
            run_argv('fixture-pi', invalid, str(home), str(session), '', '', instructions)
        except ValueError as error:
            assert 'unsupported Pi role' in str(error)
        else:
            raise AssertionError('unsupported role gained native Pi tools: ' + invalid)


if __name__ == '__main__':
    check(sys.argv[1])
