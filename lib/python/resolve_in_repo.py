# Resolve path argv[2] relative to repo argv[1], requiring the result to stay
# inside the repo. Prints the absolute resolved path on stdout; exits 6 if the
# path escapes. Used by helpers.sh resolve_in_repo() (python for
# cross-platform realpath; macOS lacks GNU realpath).
import os, sys
repo = os.path.realpath(sys.argv[1])
cand = sys.argv[2]
if not os.path.isabs(cand):
    cand = os.path.join(repo, cand)
cand = os.path.realpath(cand)
if cand != repo and not cand.startswith(repo + os.sep):
    sys.stderr.write(f"cerebro: error: path escapes repo: {sys.argv[2]}\n")
    sys.exit(6)
print(cand)
