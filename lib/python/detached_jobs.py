"""List and cancel detached Cerebro jobs registered to a parent session."""

import glob
import json
import os
import signal
import subprocess
import sys
import time


def read(path):
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError:
        return ""


def load(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except Exception:
        return {}


def write_atomic(path, value):
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w") as fh:
        fh.write(value)
    os.replace(tmp, path)


def alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError):
        return False


def monitor_alive(job):
    pid = job.get("pid")
    status = job.get("status", "")
    if not alive(pid) or not status:
        return False
    try:
        command = subprocess.check_output(
            ["ps", "-p", str(pid), "-o", "command="], text=True
        )
    except subprocess.CalledProcessError:
        return False
    return "detach_process.py --monitor" in command and status in command


def state(job):
    value = read(job.get("status", ""))
    try:
        rc = int(value)
        return "succeeded" if rc == 0 else ("cancelled" if rc == 130 else f"failed:{rc}")
    except ValueError:
        if value in ("starting", "running"):
            return "running" if monitor_alive(job) else "orphaned"
        return "lost"


def list_jobs(directory):
    paths = sorted(glob.glob(os.path.join(directory, "*.json")))
    jobs = [load(path) for path in paths]
    jobs = [job for job in jobs if job.get("id")]
    if not jobs:
        print("detached jobs: (none)")
        return
    print("detached jobs:")
    for job in sorted(jobs, key=lambda item: item.get("created_at", ""), reverse=True):
        job_state = state(job)
        print(
            f"  [{job_state}] {job['id']}  {job.get('command') or '?'}  "
            f"pid={job.get('pid') or '?'}"
        )
        print(f"      output: {job.get('output') or '?'}")
        if job_state in ("running", "orphaned"):
            print(f"      notify: cerebro wait {job['id']}")
        if job_state == "running":
            print(f"      cancel: cerebro cancel {job['id']}")


def process_table():
    out = subprocess.check_output(["ps", "-axo", "pid=,ppid="], text=True)
    children = {}
    for line in out.splitlines():
        try:
            pid, ppid = map(int, line.split())
        except ValueError:
            continue
        children.setdefault(ppid, []).append(pid)
    return children


def descendants(root):
    table = process_table()
    found = []
    stack = [root]
    while stack:
        parent = stack.pop()
        for child in table.get(parent, []):
            found.append(child)
            stack.append(child)
    return found


def cancel(job_file):
    job = load(job_file)
    pid = job.get("pid")
    status = job.get("status", "")
    if not isinstance(pid, int) or not status:
        sys.exit("cerebro: malformed detached job")
    if state(job) != "running":
        sys.exit(f"cerebro: detached job is not running ({state(job)})")

    if not monitor_alive(job):
        sys.exit("cerebro: refusing to signal PID whose monitor identity does not match")

    targets = descendants(pid)
    # Publish cancellation before signalling the monitor: socket EOF and its
    # final child result must agree with the registered job's cancellation.
    write_atomic(status, "130\n")
    output_status = job.get("output_status")
    if output_status:
        write_atomic(output_status, "130\n")
    for target in reversed(targets):
        try:
            os.kill(target, signal.SIGTERM)
        except OSError:
            pass
    time.sleep(0.5)
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass
    time.sleep(0.5)
    for target in reversed(targets + [pid]):
        if alive(target):
            try:
                os.kill(target, signal.SIGKILL)
            except OSError:
                pass
    print(f"cerebro: cancelled detached job {job.get('id')} (pid {pid})")


if len(sys.argv) != 3:
    sys.exit("usage: detached_jobs.py <list|cancel> <path>")
if sys.argv[1] == "list":
    list_jobs(sys.argv[2])
elif sys.argv[1] == "cancel":
    cancel(sys.argv[2])
else:
    sys.exit("unknown detached-jobs operation")
