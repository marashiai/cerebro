# Utility estimation: scan the trace corpus under $CEREBRO_HOME/sessions/
# and compute a session-quality proxy U in [0, 1]. Higher = healthier runs.
#
# The proxy is a weighted composite of three signals already in the corpus:
#   w1 * (1 - child_failure_rate)   -- fewer child failures/stalls
#   w2 * (1 - review_rounds_norm)   -- fewer review rounds until quiet
#   w3 * (1 - correction_density)   -- fewer user corrections (learn-notes)
#
# Usage: improve_utility.py <cerebro_home> [since_ts]
#   <since_ts>  -- ISO timestamp; only sessions created after this are scored
#                  (empty = score all sessions).
# Prints a single float in [0, 1].

import collections
import glob
import json
import os
import sys

def _scan_transcript(path):
    """Return (events, n_prompts) for one transcript.jsonl."""
    events = collections.Counter()
    n_prompts = 0
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                kind = obj.get("kind", "")
                if kind == "user":
                    n_prompts += 1
                elif kind == "event":
                    what = obj.get("what", "")
                    events[what] += 1
    except Exception:
        pass
    return events, n_prompts

def _child_failures(session_dir):
    """Count explicit failed and completed child results.

    Native terminal events and explicit transport stalls determine completion.
    Incomplete logs have no truthful outcome and are excluded rather than
    guessed from assistant text near the end of the file.
    """
    n_fail = 0
    n_total = 0
    children_dir = os.path.join(session_dir, "children")
    for f in glob.glob(os.path.join(children_dir, "*.jsonl")):
        if os.path.isfile(os.path.splitext(f)[0] + ".stalled"):
            n_fail += 1
            n_total += 1
            continue
        try:
            with open(f, encoding="utf-8") as fh:
                events = [json.loads(line) for line in fh if line.strip()]
        except (OSError, ValueError):
            continue
        failed = any(event.get("type") in ("error", "turn.failed") for event in events)
        claude_results = [event for event in events if event.get("type") == "result"]
        if claude_results:
            failed = failed or claude_results[-1].get("subtype") != "success"
            n_total += 1
        elif any(event.get("type") == "agent_settled" for event in events):
            assistants = [event["message"] for event in events
                          if event.get("type") == "message_end" and event.get("message", {}).get("role") == "assistant"]
            failed = failed or not assistants or assistants[-1].get("stopReason") in ("error", "aborted")
            n_total += 1
        elif failed:
            n_total += 1
        elif any(event.get("type") == "turn.completed" for event in events):
            n_total += 1
        else:
            continue
        if failed:
            n_fail += 1
    return n_fail, n_total

def compute_utility(cerebro_home, since_ts=""):
    sessions_dir = os.path.join(cerebro_home, "sessions")
    if not os.path.isdir(sessions_dir):
        return 0.5  # neutral when no data

    total_sessions = 0
    total_child_fails = 0
    total_children = 0
    total_review_rounds = 0
    total_learn_notes = 0
    total_prompts = 0

    for entry in os.listdir(sessions_dir):
        sess_dir = os.path.join(sessions_dir, entry)
        if not os.path.isdir(sess_dir):
            continue
        # Filter by since_ts if provided: check metadata.json created_at
        if since_ts:
            meta = os.path.join(sess_dir, "metadata.json")
            created = ""
            try:
                with open(meta) as fh:
                    m = json.load(fh)
                    created = m.get("created_at", "")
            except Exception:
                pass
            if created and created <= since_ts:
                continue

        total_sessions += 1
        transcript = os.path.join(sess_dir, "transcript.jsonl")
        events, n_prompts = _scan_transcript(transcript)
        total_prompts += n_prompts
        total_learn_notes += events.get("learn_note", 0)
        total_review_rounds += events.get("review_written", 0)
        # Child failures: use only the file-scan heuristic (single source for
        # both numerator and denominator) to avoid a numerator/denominator
        # mismatch when events exist but child files don't.
        cf, ct = _child_failures(sess_dir)
        total_child_fails += cf
        total_children += ct
    if total_sessions == 0:
        return 0.5  # neutral when no sessions in window

    # Guard: sessions with no transcript data (empty corpus) should score
    # neutral (0.5), not perfect (1.0).  Each density term uses 0.5 when its
    # denominator is 0.
    if total_prompts == 0:
        return 0.5

    child_health = (1.0 - total_child_fails / total_children
                    if total_children else 0.5)
    review_density = total_review_rounds / max(total_prompts, 1)
    correction_density = total_learn_notes / max(total_prompts, 1)

    w1, w2, w3 = 0.4, 0.3, 0.3
    u = (w1 * child_health
         + w2 * (1.0 - min(review_density, 1.0))
         + w3 * (1.0 - min(correction_density, 1.0)))
    return round(u, 4)

if __name__ == "__main__":
    home = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("CEREBRO_HOME", "")
    since = sys.argv[2] if len(sys.argv) > 2 else ""
    if not home:
        sys.stdout.write("0.5\n")
        sys.exit(0)
    print(compute_utility(home, since))
