# Native supervisor/observer launch, recorded-backend resume and session listing.

learnings_file()         { printf '%s\n' "$CEREBRO_HOME/learnings.md"; }
pending_learnings_file() { printf '%s\n' "$CEREBRO_HOME/pending-learnings.md"; }

# User-owned harness overlays (global under $CEREBRO_HOME). Each overlay is a
# plain-markdown file the orchestrator/children can READ when they need local
# tuning. materialise_home() never creates or clobbers them; an absent or
# whitespace-only overlay is simply not read.
overlays_dir() { printf '%s\n' "$CEREBRO_HOME/overlays"; }
overlay_file() { printf '%s\n' "$(overlays_dir)/$1.md"; }   # $1 = target
overlay_body() {   # $1=target; echoes body only if present + non-whitespace
  local f; f="$(overlay_file "$1")"
  [[ -s "$f" ]] || return 0
  local b; b="$(cat "$f")"
  [[ "$b" =~ [^[:space:]] ]] && printf '%s' "$b"
}

cmd_launch() {
  require_interactive
  require_deps
  materialise_home

  local sid sess_dir ts
  sid="$(mint_uuid)"
  sess_dir="$CEREBRO_HOME/sessions/$sid"
  mkdir -p "$sess_dir/plans" "$sess_dir/children"
  : > "$sess_dir/transcript.jsonl"
  ts="$(ts_iso)"
  write_metadata_new "$sess_dir" "$sid" "$ts"

  export CEREBRO_SESSION_ID="$sid"
  export CEREBRO_SESSION_DIR="$sess_dir"
  export CEREBRO_HOME

  CEREBRO_SESSION_DIR="$sess_dir" log_event "session_created"

  say "cerebro: starting session $sid (backend $(current_backend))"
  cd "$CEREBRO_HOME" || die "cd to $CEREBRO_HOME failed"
  backend_launch_orchestrator "$sess_dir"
}

# ----- subcommand: cerebro --observe [<id>] --------------------------------

# Wait for live paired work before minting an observer session; Ctrl-C cancels the wait.
# This startup probe is separate from native child completion notification.
observer_wait_until_observable() {
  local target="${1:-}" selected waiting=0 who="paired children"
  [[ -z "$target" || "$target" =~ ^[a-zA-Z0-9_-]+$ ]] || die "observe: target must be a Cerebro session ID"
  [[ -n "$target" ]] && who="session $target's paired children"
  until selected="$(python3 "$CEREBRO_LIB_DIR/python/observe_pump.py" \
      "$CEREBRO_HOME/sessions" "$target" "" "" 0 0 probe 2>/dev/null)"; do
    (( waiting )) || say "cerebro: waiting for $who to observe... (Ctrl-C to cancel)"
    waiting=1
    sleep "${CEREBRO_OBSERVE_POLL:-2}"
  done
  printf '%s\n' "$selected"
}

# Observers use the same native session plumbing with a guarded read/steer role.
cmd_launch_observer() {
  require_interactive
  require_deps
  materialise_home

  local target="${1:-}"
  # Don't open the chat until something is observable; poll until it is. Done
  # before minting the session so a Ctrl-C here leaves no orphan session dir.
  target="$(observer_wait_until_observable "$target")" || return $?

  local sid sess_dir ts
  sid="$(mint_uuid)"
  sess_dir="$CEREBRO_HOME/sessions/$sid"
  mkdir -p "$sess_dir/plans" "$sess_dir/children"
  : > "$sess_dir/transcript.jsonl"
  ts="$(ts_iso)"
  write_metadata_new "$sess_dir" "$sid" "$ts"

  local metadata_tmp="$sess_dir/metadata.json.tmp"
  jq --arg target "$target" '.role="observer" | .observe_target=$target' "$sess_dir/metadata.json" > "$metadata_tmp"
  mv "$metadata_tmp" "$sess_dir/metadata.json"

  export CEREBRO_SESSION_ID="$sid"
  export CEREBRO_SESSION_DIR="$sess_dir"
  export CEREBRO_HOME

  CEREBRO_SESSION_DIR="$sess_dir" log_event "session_created" "observer"

  say "cerebro: starting observer session $sid${target:+ (watching $target)}"
  cd "$CEREBRO_HOME" || die "cd to $CEREBRO_HOME failed"
  backend_launch_observer "$sess_dir" "" "$target"
}

# ----- subcommand: cerebro --resume [<id>] ---------------------------------

cmd_resume() {
  require_interactive

  local id="${1:-}"
  export CEREBRO_HOME

  # With no ID, select the most recently touched Cerebro session.
  if [[ -z "$id" ]]; then
    id="$(python3 "$CEREBRO_LIB_DIR/python/list_sessions.py" "$CEREBRO_HOME/sessions" --most-recent 2>/dev/null)"
    [[ -n "$id" ]] || die "no sessions to resume"
    say "cerebro: resuming most recent session $id"
  fi

  local sess_dir="$CEREBRO_HOME/sessions/$id"
  [[ -d "$sess_dir" ]] || die "no such session: $id"
  touch_metadata "$sess_dir" "$(ts_iso)"

  # Pick up the backend the session was created under so the resumed orchestrator
  # and any children it spawns dispatch through the same implementation.
  CEREBRO_RESUME_BACKEND="$(session_backend "$sess_dir")"
  export CEREBRO_RESUME_BACKEND
  require_deps
  materialise_home

  export CEREBRO_SESSION_ID="$id"
  export CEREBRO_SESSION_DIR="$sess_dir"
  say "cerebro: resuming session $id (backend $CEREBRO_RESUME_BACKEND)"

  # Reopen the recorded native conversation; state files retain the approved contract.
  local foreign_id=""
  foreign_id="$(session_foreign_id "$sess_dir")"
  # Claude native session IDs are minted from the Cerebro ID.
  [[ "$CEREBRO_RESUME_BACKEND" != "claude" ]] || foreign_id="$id"

  cd "$CEREBRO_HOME" || die "cd to $CEREBRO_HOME failed"
  backend_resume_orchestrator "$sess_dir" "$foreign_id"
}

# ----- subcommand: cerebro list --------------------------------------------

cmd_list() {
  require_interactive
  if [[ ! -d "$CEREBRO_HOME/sessions" ]] || \
     [[ -z "$(ls -A "$CEREBRO_HOME/sessions" 2>/dev/null)" ]]; then
    echo "cerebro: no sessions yet"
    return 0
  fi
  # Sort by metadata.last_touched, newest first.
  python3 "$CEREBRO_LIB_DIR/python/list_sessions.py" "$CEREBRO_HOME/sessions"
}
