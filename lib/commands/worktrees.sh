# cerebro lib: commands/worktrees
# subcommand: worktrees (list / GC the per-task execute worktrees)
# Sourced by bin/cerebro; not meant to be executed directly.

# `cerebro execute --worktree` creates a persistent managed checkout under
# $CEREBRO_HOME/worktrees. Only this cleanup command removes it.
# `cerebro worktrees` (or `... list`) reports every worktree with its
# branch, owning repo, and in-use verdict; `cerebro worktrees cleanup` removes
# the ones that are safe to delete. A worktree is KEPT when it is still in use
# by any of: uncommitted/untracked changes in the tree, an OPEN PR for its
# branch, an in-flight/resumable cerebro child, or local commits not yet
# pushed/merged. When any check is unknown it is KEPT (safe default), so cleanup
# never drops work it could not positively clear.

# worktree_owner <wt> -- the owning repo (the main working tree of the worktree
# set), or empty when <wt> is not a live worktree.
worktree_owner() {
  git -C "$1" worktree list --porcelain 2>/dev/null \
    | awk '/^worktree /{sub(/^worktree /, ""); print; exit}'
}

# Check creation ownership or active children across all session records.
worktree_has_record() {
  local mode="$1" wt="$2" f
  shopt -s nullglob
  for f in "$CEREBRO_HOME"/sessions/*/child-sessions.json; do
    if python3 "$CEREBRO_LIB_DIR/python/worktree_inuse.py" \
      "$mode" "$f" "$wt" "${CEREBRO_CHILD_SESSION_TTL:-86400}"; then
      shopt -u nullglob; return 0
    fi
  done
  shopt -u nullglob
  return 1
}

# worktree_in_use <wt> <branch> -- true (and sets WT_USE_REASON) when <wt> must
# be kept. Errs toward keeping: any check it cannot positively resolve keeps it.
WT_USE_REASON=""
worktree_in_use() {
  local wt="$1" branch="$2"
  WT_USE_REASON=""

  if ! worktree_has_record owned "$wt"; then WT_USE_REASON="no task creation record"; return 0; fi

  # 1. Uncommitted or untracked work in the tree. A non-empty `status
  # --porcelain` means there are local changes that were never committed (let
  # alone pushed); removing the worktree would destroy them outright, so keep
  # it. An unreadable status (git failed) is unknown -> keep.
  local dirty dirty_rc
  dirty="$(git -C "$wt" status --porcelain 2>/dev/null)"; dirty_rc=$?
  if (( dirty_rc != 0 )); then WT_USE_REASON="dirty-tree state unknown"; return 0; fi
  if [[ -n "$dirty" ]]; then WT_USE_REASON="uncommitted or untracked changes"; return 0; fi

  # 2. An OPEN PR for the branch. We must tell "no open PR" apart from "lookup
  # failed": a transient gh auth/network/CLI failure must NOT read as no-PR, or
  # cleanup could delete a worktree for a live pushed PR. `gh pr list` exits 0
  # with an empty array when there is genuinely no open PR; a non-zero exit is a
  # lookup failure -> unknown -> keep.
  if [[ -n "$branch" && "$branch" != "HEAD" ]]; then
    local prlist pr_rc
    prlist="$(cd "$wt" 2>/dev/null && gh pr list --head "$branch" --state open --json state -q '.[].state' 2>/dev/null)"
    pr_rc=$?
    if (( pr_rc != 0 )); then WT_USE_REASON="PR lookup failed for $branch"; return 0; fi
    if [[ "$prlist" == *OPEN* ]]; then WT_USE_REASON="open PR for $branch"; return 0; fi
  fi

  # 3. An in-flight or resumable cerebro child on this worktree.
  if worktree_has_record live "$wt"; then WT_USE_REASON="in-flight cerebro child"; return 0; fi

  # 4. Local commits not yet pushed/merged. With an upstream, anything in
  # @{upstream}..HEAD is unpushed; with none, anything ahead of the base ref is.
  # An unreadable count is unknown -> keep.
  local cnt
  if git -C "$wt" rev-parse '@{upstream}' >/dev/null 2>&1; then
    cnt="$(git -C "$wt" rev-list --count '@{upstream}..HEAD' 2>/dev/null)"
    if [[ -z "$cnt" ]]; then WT_USE_REASON="unpushed state unknown"; return 0; fi
    if [[ "$cnt" != 0 ]]; then WT_USE_REASON="$cnt commit(s) not pushed to upstream"; return 0; fi
  else
    local baseref baserev
    baseref="$(git -C "$wt" symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null \
               | sed 's#^origin/##')"
    [[ -n "$baseref" ]] || baseref="main"
    baserev="$(git -C "$wt" rev-parse --verify --quiet "origin/$baseref" 2>/dev/null)" \
      || baserev="$(git -C "$wt" rev-parse --verify --quiet "$baseref" 2>/dev/null)" || baserev=""
    if [[ -z "$baserev" ]]; then WT_USE_REASON="base ref unknown"; return 0; fi
    cnt="$(git -C "$wt" rev-list --count "$baserev..HEAD" 2>/dev/null)"
    if [[ -z "$cnt" ]]; then WT_USE_REASON="ahead count unknown"; return 0; fi
    if [[ "$cnt" != 0 ]]; then WT_USE_REASON="$cnt commit(s) ahead of $baseref"; return 0; fi
  fi
  return 1
}

# ----- subcommand: cerebro worktrees [list|cleanup] ------------------------
cmd_worktrees() {
  local action="${1:-list}"
  case "$action" in
    list|cleanup) ;;
    *) die "worktrees: usage: cerebro worktrees [list|cleanup]" ;;
  esac

  local wtroot="$CEREBRO_HOME/worktrees"
  if [[ ! -d "$wtroot" ]]; then
    say "cerebro: no execute worktrees yet ($wtroot does not exist)"
    return 0
  fi

  local kept=0 removed=0 wt branch owner
  shopt -s nullglob
  for wt in "$wtroot"/*; do
    [[ -d "$wt" ]] || continue
    branch="$(execute_worktree_branch "$wt")"
    owner="$(worktree_owner "$wt")"
    if worktree_in_use "$wt" "$branch"; then
      kept=$((kept + 1))
      printf '%s\tbranch=%s\towner=%s\tKEEP (%s)\n' \
        "$wt" "${branch:-detached}" "${owner:-?}" "$WT_USE_REASON"
    elif [[ "$action" == cleanup ]]; then
      execute_worktree_remove "${owner:-$wt}" "$wt"
      removed=$((removed + 1))
      printf '%s\tbranch=%s\towner=%s\tREMOVED (stale: no open PR, no in-flight child, no unpushed commits)\n' \
        "$wt" "${branch:-detached}" "${owner:-?}"
    else
      printf '%s\tbranch=%s\towner=%s\tSTALE (no open PR, no in-flight child, no unpushed commits)\n' \
        "$wt" "${branch:-detached}" "${owner:-?}"
    fi
  done
  shopt -u nullglob

  if [[ "$action" == cleanup ]]; then
    log_event "worktrees_cleanup" "kept=$kept removed=$removed"
    say "cerebro: worktrees cleanup -- kept $kept, removed $removed"
  else
    say "cerebro: $kept worktree(s) in use; run 'cerebro worktrees cleanup' to remove stale ones"
  fi
}
