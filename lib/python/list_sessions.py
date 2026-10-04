# Session table for `cerebro list`: argv[1] is the sessions root. Sorts by
# metadata.last_touched, newest first. With `--most-recent`, prints only the id
# of the newest session (used by bare `cerebro --resume`).
import json, os, sys
root = sys.argv[1]
most_recent = "--most-recent" in sys.argv[2:]
rows = []
for name in os.listdir(root):
    sess = os.path.join(root, name)
    md = os.path.join(sess, "metadata.json")
    created = touched = "-"
    if os.path.isfile(md):
        try:
            data = json.load(open(md))
            created = data.get("created_at", "-")
            touched = data.get("last_touched", created)
        except Exception:
            pass
    rows.append((touched, name, created))
rows.sort(reverse=True)
if most_recent:
    if rows:
        print(rows[0][1])
    sys.exit(0)
if not rows:
    print("cerebro: no sessions yet")
else:
    width = max((len(r[1]) for r in rows), default=8)
    print(f"{'session':<{width}}  created_at            last_touched")
    for touched, name, created in rows:
        print(f"{name:<{width}}  {created:<20}  {touched}")