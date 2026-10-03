# cerebro lib: session-store
# session metadata + child-agent session store
# Sourced by bin/cerebro; not meant to be executed directly.

# ----- session metadata -----------------------------------------------------

# Cerebro owns the directory ID; metadata binds its backend and native conversation.
write_metadata_new() {
  local sess_dir="$1" sid="$2" ts="$3"
  jq -n --arg sid "$sid" --arg backend "$(current_backend)" --arg ts "$ts" \
    '{cerebro_session_id:$sid, backend:$backend, foreign_session_id:"", created_at:$ts, last_touched:$ts}' \
    > "$sess_dir/metadata.json"
}

touch_metadata() {
  local sess_dir="$1" ts="$2" tmp
  [[ -f "$sess_dir/metadata.json" ]] || return 0
  tmp="$(mktemp)" || return 0
  jq --arg ts "$ts" '.last_touched = $ts' "$sess_dir/metadata.json" \
    > "$tmp" 2>/dev/null && mv "$tmp" "$sess_dir/metadata.json" || rm -f "$tmp"
}

# set_metadata_foreign <sess-dir> <foreign-id> -- record the provider-assigned
# conversation id for a session (used by the ACP proxy to reopen the same
# upstream conversation on session/load + session/resume). Idempotent.
set_metadata_foreign() {
  local sess_dir="$1" foreign="$2" tmp
  [[ -f "$sess_dir/metadata.json" ]] || return 0
  tmp="$(mktemp)" || return 0
  jq --arg f "$foreign" '.foreign_session_id = $f' "$sess_dir/metadata.json" \
    > "$tmp" 2>/dev/null && mv "$tmp" "$sess_dir/metadata.json" || rm -f "$tmp"
}

# Every child uses the recorded backend; incomplete metadata cannot select another.
session_backend() {
  local sess_dir="$1"
  local backend
  backend="$(jq -er '.backend | select(. == "pi" or . == "codex" or . == "claude")' \
    "$sess_dir/metadata.json" 2>/dev/null)" || die "session metadata has no supported backend: $sess_dir"
  printf '%s' "$backend"
}

session_foreign_id() {
  local sess_dir="$1"
  [[ -f "$sess_dir/metadata.json" ]] || return 0
  jq -r '.foreign_session_id // empty' "$sess_dir/metadata.json" 2>/dev/null
}

# ----- child agent session store -------------------------------------------
# Persist native child IDs before completion. Only incomplete work auto-resumes;
# answer explicitly resumes a child that ended with a question.

child_sessions_file() { printf '%s\n' "$CEREBRO_SESSION_DIR/child-sessions.json"; }

# Keep each repo/role/task conversation distinct in the child store.
child_key() {
  local repo="$1" role="$2" branch="${3:-default}"
  printf '%s\0%s\0%s' "$repo" "$role" "$branch" | python3 -c 'import sys,hashlib; print(hashlib.sha1(sys.stdin.buffer.read()).hexdigest())' | cut -c1-16
}

# child_store <op> [args...] -- the single entry point for every mutation
# and query of child-sessions.json. All access goes through one fcntl-locked
# python process (lib/python/child_store.py) so concurrent --pair children
# that each persist their own id at startup cannot clobber the whole-file
# rewrite.
child_store() { python3 "$CEREBRO_LIB_DIR/python/child_store.py" "$(child_sessions_file)" "$@"; }

# child_store_begin <key> <provider> <role> <repo> <branch> <log> -- mark a
# child as in-flight (status=running) the moment before it launches, so an
# interrupt mid-run leaves a discoverable, resumable record. Pass a seventh
# argument of "preserve-id" only when launching with an explicit provider
# resume id; otherwise an old id for the same key is cleared.
child_store_begin() {
  [[ -n "${1:-}" ]] || return 0
  local f; f="$(child_sessions_file)"
  [[ -f "$f" ]] || printf '{}\n' > "$f"
  child_store begin "$1" "$2" "$3" "$4" "$5" "$6" "$(ts_iso)" "${7:-}"
}
