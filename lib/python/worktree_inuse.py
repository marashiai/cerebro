# worktree_inuse.py <live|owned> <store> <wt> <ttl> -- test a managed checkout's
# active children or explicit creation record. Missing ownership prevents cleanup.
import os, sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from child_store_lib import _fresh, _load
from task_workspace import common_dir, git

mode, f, wt, ttl = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
wt = os.path.realpath(wt)
for e in _load(f).values():
    if mode == 'owned':
        workspace = e.get('workspace') or {}
        if os.path.realpath(workspace.get('path') or '') == wt and workspace.get('created_worktree') is True:
            try:
                if (common_dir(wt) == workspace.get('common_dir')
                        and os.path.realpath(git(wt, 'rev-parse', '--show-toplevel')) == wt):
                    sys.exit(0)
            except (ValueError, OSError):
                pass
        continue
    if e.get("status") != "running":
        continue
    ts = e.get("updated_at") or ""
    if not (ts and _fresh(ts, ttl)):
        continue
    if os.path.realpath(e.get("repo") or "") == wt:
        sys.exit(0)
sys.exit(1)
