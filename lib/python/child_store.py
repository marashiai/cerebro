# Child-session store CLI: `child_store.py <file> <op> [args...]`. Ops:
#   begin <key> <provider> <role> <repo> <branch> <log> <ts>  -- mark running
#   set-id <key> <provider> <id> <ts>                          -- record the id
#   done <key> <ts>                                            -- mark finished
#   retire <key> <ts>                     -- finish and remove the resumable ID
#   get <key>                  -- print the stored id (empty if none)
#   running-fresh <key> <ttl>  -- exit 0 if status=running and fresh
#   find-id <ttl> <id>         -- TSV of fresh entries with that provider id
#   list-running <ttl>         -- TSV of still-fresh status=running entries
import os, sys

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from child_store_lib import _fresh, _load, store_upsert

f = sys.argv[1]
op = sys.argv[2]
if op == "begin":
    key, provider, role, repo, branch, log, ts = sys.argv[3:10]
    preserve_id = len(sys.argv) > 10 and sys.argv[10] == "preserve-id"
    fields = {"provider": provider, "role": role, "repo": repo,
              "branch": branch, "log": log, "status": "running",
              "started_at": ts, "updated_at": ts}
    if not preserve_id:
        fields["id"] = None
    store_upsert(f, key, fields)
elif op == "set-id":
    key, provider, cid, ts = sys.argv[3:7]
    if cid:
        store_upsert(f, key, {"id": cid, "provider": provider, "updated_at": ts})
elif op == "done":
    key, ts = sys.argv[3:5]
    store_upsert(f, key, {"status": "done", "updated_at": ts})
elif op == "retire":
    key, ts = sys.argv[3:5]
    store_upsert(f, key, {"status": "done", "updated_at": ts, "id": None})
elif op == "get":
    e = _load(f).get(sys.argv[3]) or {}
    sys.stdout.write(e.get("id") or "")
elif op == "running-fresh":
    e = _load(f).get(sys.argv[3]) or {}
    ts = e.get("updated_at") or ""
    sys.exit(0 if (e.get("status") == "running" and ts
                   and _fresh(ts, int(sys.argv[4]))) else 1)
elif op == "find-id":
    # find-id <ttl> <id> -- TSV(key, id, provider, role, repo, branch, status,
    # updated_at, log) for every still-fresh entry with the provider id.
    ttl = int(sys.argv[3]); cid = sys.argv[4]
    for key, e in _load(f).items():
        if e.get("id") == cid and _fresh(e.get("updated_at") or "", ttl):
            sys.stdout.write("\t".join([
                key, e.get("id") or "", e.get("provider") or "",
                e.get("role") or "", e.get("repo") or "",
                e.get("branch") or "", e.get("status") or "",
                e.get("updated_at") or "", e.get("log") or "",
            ]) + "\n")
elif op == "list-running":
    ttl = int(sys.argv[3])
    for key, e in _load(f).items():
        if e.get("status") == "running" and _fresh(e.get("updated_at") or "", ttl):
            sys.stdout.write("\t".join([
                key, e.get("role") or "", e.get("repo") or "",
                e.get("branch") or "", e.get("log") or "",
                e.get("started_at") or e.get("updated_at") or "",
            ]) + "\n")
else:
    sys.exit(2)
