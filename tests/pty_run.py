#!/usr/bin/env python3
# Drive a command under an allocated pseudo-terminal, the way a controller
# such as Codex (launched with `tty: true`) drives cerebro. Used by the
# require_interactive / interactive-guard tests in tests/run.sh.
#
# Usage:
#   pty_run.py [--parent NAME] [--cwd DIR] [--timeout SEC]
#              [--script SCRIPT_FILE] -- cmd [args...]
#
# Modes:
#   * Default (no --script): run cmd under a PTY, capture all output until the
#     child exits, then print "RC=<n>" and the captured output (CR-stripped).
#   * --script FILE: drive an interactive session. Each line is a step:
#       EXPECT <substring>   wait (up to --timeout) for <substring> in output
#       SEND <text>          write <text> to the PTY (\x04=EOF, \x03=SIGINT),
#                            with a trailing newline appended if missing
#       SENDEOF              send Ctrl-D (canonical EOF) to the child's stdin
#       SENDINTR             send Ctrl-C (SIGINT) to the foreground process
#     After the steps (or once the child exits), print "RC=<n>" and output.
#
# --parent NAME wraps cmd in a foreground intermediate process whose kernel
# `comm` basename is NAME (a symlink to /bin/sh, run as `"$@"; exit $?` so it
# does not tail-exec and stays the child's parent). This reproduces the exact
# parent-name dimension the old executable-name heuristic used, so the codex /
# node / bash cases exercise that path without depending on it.
#
# Echo is disabled on the PTY so input written from the master is not reflected
# back into the read stream. Canonical mode (ICANON) is left on so Ctrl-D
# delivers clean EOF and Ctrl-C delivers SIGINT.
import os
import pty
import select
import sys
import termios
import time


def parse_args(argv):
    opts = {
        "parent": None,
        "cwd": None,
        "timeout": 8.0,
        "script": None,
    }
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--":
            i += 1
            break
        elif a == "--parent":
            opts["parent"] = argv[i + 1]; i += 2
        elif a == "--cwd":
            opts["cwd"] = argv[i + 1]; i += 2
        elif a == "--timeout":
            opts["timeout"] = float(argv[i + 1]); i += 2
        elif a == "--script":
            opts["script"] = argv[i + 1]; i += 2
        else:
            sys.stderr.write(f"pty_run.py: bad arg {a!r}\n")
            sys.exit(2)
    if i >= len(argv):
        sys.stderr.write("pty_run.py: no command given after --\n")
        sys.exit(2)
    opts["cmd"] = argv[i:]
    return opts


def build_parent_shim(name):
    """Return a path to a symlink `name` -> /bin/sh, so exec'ing it yields a
    process whose `comm` basename is `name`."""
    d = os.environ.get("TMPDIR", "/tmp")
    d = os.path.join(d, f"ptyrun-{os.getpid()}-{name}")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, name)
    if not os.path.exists(path):
        os.symlink("/bin/sh", path)
    return path


def start_child(opts):
    cmd = opts["cmd"]
    parent = opts["parent"]
    pid, master = pty.fork()
    if pid == 0:
        # child: disable echo on the controlling tty; keep canonical mode so
        # Ctrl-D -> EOF and Ctrl-C -> SIGINT both work.
        try:
            attrs = termios.tcgetattr(0)
            attrs[3] = attrs[3] & ~termios.ECHO
            termios.tcsetattr(0, termios.TCSANOW, attrs)
        except OSError:
            pass
        if opts["cwd"]:
            os.chdir(opts["cwd"])
        if parent:
            shim = build_parent_shim(parent)
            # `"$@"; exit $?` keeps the shim as the child's parent (no tail-exec)
            # while leaving the child in the foreground (PTY on stdin, no
            # /dev/null redirect). $0=sh, $@=cmd args.
            os.execv(shim, [parent, "-c", '"$@"; exit $?', "sh"] + cmd)
        else:
            os.execv(cmd[0], cmd)
    # parent
    try:
        attrs = termios.tcgetattr(master)
        attrs[3] = attrs[3] & ~termios.ECHO
        termios.tcsetattr(master, termios.TCSANOW, attrs)
    except OSError:
        pass
    return pid, master


def drain(master, timeout):
    """Read available output for up to `timeout` seconds. Returns b'' on EOF."""
    buf = b""
    end = time.monotonic() + timeout
    while True:
        remaining = end - time.monotonic()
        if remaining <= 0:
            return buf, False  # timed out
        r, _, _ = select.select([master], [], [], remaining)
        if not r:
            return buf, False
        try:
            data = os.read(master, 4096)
        except OSError:
            return buf, True  # EOF / closed
        if not data:
            return buf, True
        buf += data


def read_until(master, marker, timeout, buf):
    """Block until `marker` (bytes) appears in the accumulated buffer, or
    timeout. Returns (buf, found)."""
    marker_b = marker.encode() if isinstance(marker, str) else marker
    deadline = time.monotonic() + timeout
    while marker_b not in buf:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return buf, False
        r, _, _ = select.select([master], [], [], remaining)
        if not r:
            continue
        try:
            data = os.read(master, 4096)
        except OSError:
            return buf, False
        if not data:
            return buf, False
        buf += data
    return buf, True


def child_done(pid):
    try:
        wpid, status = os.waitpid(pid, os.WNOHANG)
    except ChildProcessError:
        return None
    if wpid == 0:
        return None
    if os.WIFEXITED(status):
        return os.WEXITSTATUS(status)
    if os.WIFSIGNALED(status):
        return -os.WTERMSIG(status)
    return None


def reap(pid):
    """Blocking reap. Returns exit code, -signal, or None if already reaped."""
    done = child_done(pid)
    if done is not None:
        return done
    try:
        wpid, status = os.waitpid(pid, 0)
    except ChildProcessError:
        return None
    if os.WIFEXITED(status):
        return os.WEXITSTATUS(status)
    if os.WIFSIGNALED(status):
        return -os.WTERMSIG(status)
    return None


def main():
    opts = parse_args(sys.argv[1:])
    pid, master = start_child(opts)
    captured = b""
    timeout = opts["timeout"]
    rc = None  # set once the child is observed exited

    if opts["script"]:
        with open(opts["script"]) as f:
            steps = [ln.rstrip("\n") for ln in f if ln.strip()]
        for step in steps:
            if not step or step.startswith("#"):
                continue
            sp = step.split(" ", 1)
            verb = sp[0]
            arg = sp[1] if len(sp) > 1 else ""
            if verb == "EXPECT":
                captured, found = read_until(master, arg, timeout, captured)
                if not found:
                    break
            elif verb == "SEND":
                text = arg.encode().decode("unicode_escape").encode("latin-1")
                if not text.endswith(b"\n"):
                    text += b"\n"
                os.write(master, text)
            elif verb == "SENDEOF":
                os.write(master, b"\x04")
            elif verb == "SENDINTR":
                os.write(master, b"\x03")
            else:
                sys.stderr.write(f"pty_run.py: unknown script step {verb!r}\n")
                sys.exit(2)
            # If the child has exited, stop driving (track rc once).
            done = child_done(pid)
            if done is not None:
                rc = done
                break
        # Drain any trailing output briefly.
        more, _ = drain(master, 0.5)
        captured += more
    else:
        # Run-and-capture: read until the child exits / EOF.
        while rc is None:
            data, eof = drain(master, timeout)
            captured += data
            done = child_done(pid)
            if done is not None:
                rc = done
                more, _ = drain(master, 0.3)
                captured += more
                break
            if eof:
                break

    if rc is None:
        # Child closed the PTY (EOF) but was not yet reaped; block for it.
        rc = reap(pid)
    try:
        os.close(master)
    except OSError:
        pass

    text = captured.replace(b"\r", b"").decode(errors="replace")
    print(f"RC={rc if rc is not None else 'none'}")
    print("---OUTPUT---")
    print(text, end="")
    if not text.endswith("\n"):
        print()
    print("---END---")


if __name__ == "__main__":
    main()