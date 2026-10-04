# True (exit 0) if a child's pump is still reading the pipe at argv[1] -- a
# non-blocking writer-open succeeds -- i.e. it is a live paired session rather
# than a stale pipe left by a crashed child. Exit 1 otherwise.
import os, sys
try:
    fd = os.open(sys.argv[1], os.O_WRONLY | os.O_NONBLOCK)
except OSError:
    sys.exit(1)
os.close(fd)
