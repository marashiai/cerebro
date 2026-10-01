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

# Read the recorded backend, using the configured backend when metadata is absent.
session_backend() {
  local sess_dir="$1"
  [[ -f "$sess_dir/metadata.json" ]] || { printf '%s' "${CEREBRO_BACKEND:-opencode}"; return 0; }
  local b; b="$(jq -r '.backend // empty' "$sess_dir/metadata.json" 2>/dev/null)"
  printf '%s' "${b:-${CEREBRO_BACKEND:-opencode}}"
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

# child_session_get <key> -- echo the stored provider id for <key>, or
# nothing when the file or entry is absent.
child_session_get() {
  local f; f="$(child_sessions_file)"
  [[ -f "$f" ]] || return 0
  child_store get "$1"
}

# child_session_running_fresh <key> -- succeed when <key> is still marked
# running and updated_at is within CEREBRO_CHILD_SESSION_TTL seconds. Normal
# child launches use this so completed children are never automatically reused
# by the next sub-agent.
child_session_running_fresh() {
  local f; f="$(child_sessions_file)"
  [[ -f "$f" ]] || return 1
  child_store running-fresh "$1" "${CEREBRO_CHILD_SESSION_TTL:-86400}"
}

# child_session_find_id <provider-id> -- emit TSV rows
# (key, id, provider, role, repo, branch, status, updated_at, log) for fresh
# entries in this parent session with that provider conversation id.
child_session_find_id() {
  local f; f="$(child_sessions_file)"
  [[ -f "$f" ]] || return 0
  child_store find-id "${CEREBRO_CHILD_SESSION_TTL:-86400}" "$1"
}

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

# execute_child_disc <branch> <plan-path> <prompt-text> -- discriminator used
# for execute child keys. The plan/prompt is always part of the key; the branch
# narrows it when the orchestrator pins one. This keeps same-branch trunk-based
# plan suites from sharing a provider conversation.
execute_child_disc() {
  local branch="${1:-}" plan_path="${2:-}" prompt_text="${3:-}" source_disc
  if [[ -n "$plan_path" ]]; then
    source_disc="plan:$plan_path"
  else
    source_disc="prompt:$prompt_text"
  fi
  if [[ -n "$branch" ]]; then
    printf 'branch:%s|%s\n' "$branch" "$source_disc"
  else
    printf '%s\n' "$source_disc"
  fi
}

# child_store_done <key> -- mark a child cleanly finished (status=done). An
# entry left at status=running is treated as interrupted/incomplete and is
# surfaced by `cerebro status` for the orchestrator to resume on continue.
child_store_done() {
  [[ -n "${1:-}" ]] || return 0
  child_store done "$1" "$(ts_iso)"
}

# child_store_list_running -- emit a TSV row
# (key, role, repo, branch, log, started_at) for every still-fresh child left
# at status=running (interrupted or failed before it could mark itself done).
child_store_list_running() {
  local f; f="$(child_sessions_file)"
  [[ -f "$f" ]] || return 0
  child_store list-running "${CEREBRO_CHILD_SESSION_TTL:-86400}"
}
