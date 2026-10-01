#!/usr/bin/env bash
# UserPromptSubmit hook for cerebro. Reads claude's hook payload from
# stdin, routes the prompt to the matching cerebro session's transcript
# by session_id, and updates the current-session symlink so bare-resume
# can find its way home. Silently no-ops for non-cerebro claude sessions.

set -uo pipefail

CEREBRO_HOME="${CEREBRO_HOME:-$HOME/.cerebro}"

# Read the whole payload once -- jq doesn't share stdin between filters.
payload="$(cat 2>/dev/null || true)"
[[ -z "$payload" ]] && exit 0

if ! command -v jq >/dev/null 2>&1; then
  exit 0
fi

sid="$(printf '%s' "$payload" | jq -r '.session_id // empty' 2>/dev/null)"
text="$(printf '%s' "$payload" | jq -r '.prompt // empty' 2>/dev/null)"

[[ -z "$sid" ]] && exit 0

sess_dir="$CEREBRO_HOME/sessions/$sid"
[[ -d "$sess_dir" ]] || exit 0

ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
jq -nc --arg ts "$ts" --arg text "$text" '{kind:"user", ts:$ts, text:$text}' \
  >> "$sess_dir/transcript.jsonl" 2>/dev/null || true

# Keep current-session pointing at whichever session most recently saw a
# user prompt. Sub-commands consult this when CEREBRO_SESSION_ID is unset
# (bare `cerebro --resume` path).
ln -sfn "$sess_dir" "$CEREBRO_HOME/current-session" 2>/dev/null || true

# Claude's explicitly chosen session ID is also its native conversation ID.
if [[ -f "$sess_dir/metadata.json" ]]; then
  tmp="$(mktemp 2>/dev/null)" && {
    if jq --arg ts "$ts" --arg sid "$sid" '.last_touched = $ts | .foreign_session_id = $sid' "$sess_dir/metadata.json" \
        > "$tmp" 2>/dev/null; then
      mv "$tmp" "$sess_dir/metadata.json"
    else
      rm -f "$tmp"
    fi
  }
fi

exit 0
