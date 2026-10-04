# Shared child-session store library, imported by child_store.py and
# parse_stream.py -- so the id a child emits at startup is persisted the INSTANT
# it is known (not at the end of the run) and an interrupt therefore leaves a
# resumable pointer. All writes take an exclusive flock on a sidecar .lock file
# and rewrite the JSON atomically, so concurrent --pair children persisting
# their own ids never clobber each other.

import json, sys, os, time, calendar, tempfile
try:
    import fcntl
    _HAVE_FCNTL = True
except Exception:
    _HAVE_FCNTL = False

def _now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def _fresh(ts, ttl):
    try:
        t = calendar.timegm(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ"))
    except Exception:
        return False
    return (time.time() - t) <= ttl

def _load(f):
    try:
        with open(f) as fh:
            return json.load(fh)
    except Exception:
        return {}

def _atomic_write(f, data):
    d = os.path.dirname(f) or "."
    fd, tmp = tempfile.mkstemp(dir=d)
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
        os.replace(tmp, f)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

def store_upsert(f, key, fields):
    lf = open(f + ".lock", "w")
    try:
        if _HAVE_FCNTL:
            fcntl.flock(lf, fcntl.LOCK_EX)
        data = _load(f)
        entry = data.get(key) or {}
        for k, v in fields.items():
            if v is None:
                entry.pop(k, None)
            else:
                entry[k] = v
        data[key] = entry
        _atomic_write(f, data)
    finally:
        try:
            if _HAVE_FCNTL:
                fcntl.flock(lf, fcntl.LOCK_UN)
        except OSError:
            pass
        lf.close()
