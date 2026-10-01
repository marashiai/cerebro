"""Block on a detached monitor's completion socket without status polling."""

import hashlib
import os
from pathlib import Path
import socket
import sys
import tempfile


def completion_socket(status_path):
    directory = Path('/tmp') / f'cerebro-wait-{os.getuid()}'
    directory.mkdir(mode=0o700, exist_ok=True)
    if directory.stat().st_uid != os.getuid() or directory.stat().st_mode & 0o077:
        raise OSError('completion socket directory must be private')
    key = hashlib.sha256(os.path.realpath(status_path).encode()).hexdigest()[:32]
    return str(directory / (key + '.sock'))


def final_status(path):
    try:
        return int(Path(path).read_text().strip())
    except (OSError, ValueError):
        return None


def wait_for_completion(status_path):
    rc = final_status(status_path)
    if rc is not None:
        return rc
    try:
        with socket.socket(socket.AF_UNIX) as client:
            client.connect(completion_socket(status_path))
            with client.makefile('r') as stream:
                return int(stream.readline().strip())
    except (OSError, ValueError):
        # Status is published before socket closure, including the race between
        # reading running and connecting. EOF without a result means monitor loss.
        rc = final_status(status_path)
        if rc is not None:
            return rc
        with tempfile.NamedTemporaryFile(mode='w', dir=Path(status_path).parent, delete=False) as result:
            result.write('125\n')
        os.replace(result.name, status_path)
        sys.stderr.write('cerebro: detached monitor disappeared before completion (exit 125)\n')
        return 125


if __name__ == '__main__':
    if len(sys.argv) != 2:
        sys.exit('wait_detached: expected <status-path>')
    rc = wait_for_completion(sys.argv[1])
    print(f'cerebro: detached child finished (exit {rc})')
    sys.exit(rc)
