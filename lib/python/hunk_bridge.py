"""Allow Hunk inspection and additive review notes, never an interactive TUI."""

import os
import sys


OPTIONS = {
    'get': {}, 'context': {},
    'review': {'--include-patch': False, '--include-notes': False},
    'navigate': {'--file': True, '--hunk': True, '--old-line': True,
                 '--new-line': True, '--comment': True,
                 '--next-comment': False, '--prev-comment': False},
    'comment list': {'--file': True, '--type': True},
    'comment add': {'--reply-to': True, '--file': True, '--old-line': True,
                    '--new-line': True, '--summary': True, '--rationale': True,
                    '--markup': True, '--focus': False},
    'comment apply': {'--stdin': False, '--focus': False},
}


def validate(argv):
    if argv in (['skill', 'path', 'hunk-review'], ['session', 'list'],
                ['session', 'list', '--json']):
        return
    if len(argv) < 2 or argv[0] != 'session':
        raise ValueError('hunk: only skill lookup and live session review are available')
    action = argv[1]
    offset = 2
    if action == 'comment' and len(argv) > 2:
        action += ' ' + argv[2]
        offset = 3
    if action not in OPTIONS:
        raise ValueError('hunk: unavailable session action: ' + action)
    options = {'--repo': True, '--json': False, **OPTIONS[action]}
    selector = None
    while offset < len(argv):
        arg = argv[offset]
        offset += 1
        if arg.startswith('-'):
            if arg not in options:
                raise ValueError('hunk: unavailable option: ' + arg)
            if options[arg]:
                if offset == len(argv) or argv[offset].startswith('--'):
                    raise ValueError('hunk: missing value for ' + arg)
                value = argv[offset]
                offset += 1
                if arg == '--repo':
                    if not os.path.isabs(value):
                        raise ValueError('hunk: --repo must be an absolute path')
                    if selector is not None:
                        raise ValueError('hunk: use one repository or session selector')
                    selector = value
        else:
            if selector is not None:
                raise ValueError('hunk: use one repository or session selector')
            selector = arg
    if not selector:
        raise ValueError('hunk: select a session ID or an absolute --repo')
    if action == 'comment apply' and '--stdin' not in argv:
        raise ValueError('hunk: comment apply requires --stdin')


def main():
    argv = sys.argv[1:]
    try:
        validate(argv)
        os.execvp('hunk', ['hunk', *argv])
    except (ValueError, OSError) as exc:
        sys.exit(str(exc))


if __name__ == '__main__':
    main()
