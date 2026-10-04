# One-shot inject for `cerebro steer` / `cerebro restart`. argv is
# <fifo> <message> [prefix]. It writes a single "<prefix> <base64>" line to the
# child's pipe and returns. The default prefix `S` is a steer (it reaches the
# running turn at its next model step); `I` interrupts the running turn first so
# the message starts the next turn; `R` is a restart (the pump reaps the child and
# marks it for a clean-slate relaunch).

import base64, os, sys
fifo, msg = sys.argv[1], sys.argv[2]
prefix = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] else "S"
try:
    fd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
except OSError:
    sys.stderr.write("cerebro: the child is not listening (it may have finished).\n")
    sys.exit(3)
try:
    os.write(fd, (prefix + " " + base64.b64encode(msg.encode("utf-8")).decode() + "\n").encode())
finally:
    os.close(fd)
