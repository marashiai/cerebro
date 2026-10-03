"""Pause a real controller immediately before a native stage launch."""
import os
from pathlib import Path
import signal
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'lib/python'))
import task_lifecycle

original = task_lifecycle.subprocess.run


def pause_before_stage(argv, *args, **kwargs):
    if isinstance(argv, list) and len(argv) > 1 and argv[1] == '_task-stage':
        Path(os.environ['CONTROLLER_FIXTURE_READY']).touch()
        os.kill(os.getpid(), signal.SIGSTOP)
    return original(argv, *args, **kwargs)


task_lifecycle.subprocess.run = pause_before_stage
sys.exit(task_lifecycle.main())
