# Read-only code review and delegated corrections on the session backend.

cmd_review() {
  require_session
  build_timeout_cmd

  local repo="${1:-}"; shift || true
  local base=""
  local explicit_base="false"
  local criteria_file=""
  local model=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --base) shift; base="${1:-}"; explicit_base="true"; shift || true ;;
      --criteria-file) shift; criteria_file="${1:-}"; shift || true ;;
      --model) shift; model="${1:-}"; shift || true ;;
      *) die "review: unknown arg: $1" ;;
    esac
  done
  [[ -n "$model" ]] && require_model_for_backend "$model" "$(current_backend)" review
  [[ -n "$repo" ]] || die "usage: cerebro review <repo-abs-path> [--base <ref>] [--criteria-file <plan-path>] [--model <provider/model>]"
  [[ "$repo" = /* ]] || die "review: repo path must be absolute: $repo"
  [[ -d "$repo" ]] || die "review: repo not a directory: $repo"

  # --criteria-file points at the plan whose acceptance criteria the reviewer must
  # check the change against. Validate it up front (before any git/review
  # work) so a typo fails fast rather than after a full review.
  local criteria_block=""
  if [[ -n "$criteria_file" ]]; then
    [[ -r "$criteria_file" && -s "$criteria_file" ]] \
      || die "review: cannot read --criteria-file (or it is empty): $criteria_file"
    criteria_block="$(cat "$criteria_file")"
  fi

  # Canonical worktree root keys the per-repo review-state file so
  # re-reviews diff against the previously-reviewed commit instead of
  # main/origin (otherwise the reviewer would re-evaluate the entire PR diff
  # every time apply-review made new commits).
  local canonical_repo
  canonical_repo="$(git -C "$repo" rev-parse --show-toplevel 2>/dev/null)" \
    || die "review: not a git worktree: $repo"
  local repo_key
  repo_key="$(repo_state_key "$repo")" \
    || die "review: not a git worktree: $repo"
  local state_dir="$CEREBRO_SESSION_DIR/review-state"
  local state_file="$state_dir/$repo_key.json"

  # Child-session continuity is only for interrupted/incomplete reviews. A
  # cleanly finished review gets marked done, so a later review starts a fresh
  # reviewer session instead of inheriting stale reviewer context.
  local store_file; store_file="$(child_sessions_file)"
  local ckey="" prior="" review_branch
  review_branch="$(git -C "$repo" rev-parse --abbrev-ref HEAD 2>/dev/null)"
  if [[ -n "$review_branch" ]]; then
    ckey="$(child_key "$canonical_repo" review "$review_branch")"
    if prior="$(child_session_get "$ckey")" && [[ -n "$prior" ]] && child_session_running_fresh "$ckey"; then
      :
    else
      prior=""
    fi
  fi

  local base_ref base_description
  local using_review_state="false"

  # If the orchestrator did not pin --base, prefer the previously-reviewed
  # commit so the reviewer only inspects what changed since the last review.
  # Preconditions guard against stale state after rebases / branch
  # switches: the recorded SHA must still parse, still be an ancestor of
  # HEAD, and the branch name must match.
  if [[ "$explicit_base" == "false" && -f "$state_file" ]]; then
    local last_sha last_branch current_branch
    last_sha="$(jq -r '.last_reviewed_sha // empty' "$state_file" 2>/dev/null)"
    last_branch="$(jq -r '.branch // empty' "$state_file" 2>/dev/null)"
    current_branch="$(git -C "$repo" rev-parse --abbrev-ref HEAD 2>/dev/null)"
    if [[ -n "$last_sha" && -n "$last_branch" && -n "$current_branch" \
          && "$last_branch" == "$current_branch" ]] \
       && git -C "$repo" rev-parse --verify --quiet "$last_sha^{commit}" \
            >/dev/null 2>&1 \
       && git -C "$repo" merge-base --is-ancestor "$last_sha" HEAD \
            >/dev/null 2>&1; then
      local short_sha
      short_sha="$(git -C "$repo" rev-parse --short "$last_sha" 2>/dev/null)"
      base_ref="$last_sha"
      base_description="previously-reviewed commit $short_sha"
      using_review_state="true"
      say "cerebro: re-review mode, diffing against $short_sha"
    fi
  fi

  if [[ "$using_review_state" == "false" ]]; then
    # Resolve the base ref: explicit --base wins; otherwise try the PR
    # base via gh; then origin/HEAD; then 'main'.
    if [[ -z "$base" ]]; then
      base="$(cd "$repo" && gh pr view --json baseRefName -q .baseRefName 2>/dev/null)"
    fi
    if [[ -z "$base" ]]; then
      base="$(cd "$repo" && git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's@^refs/remotes/origin/@@')"
    fi
    [[ -z "$base" ]] && base="main"

    # Prefer the remote-tracking ref when it exists, so reviews still work
    # on branches whose local copy of the base is stale. Fall back to the
    # local branch, then to a raw commit. Matches dev-tools `review`.
    if git -C "$repo" show-ref --verify --quiet "refs/remotes/origin/$base"; then
      base_ref="origin/$base"
    elif git -C "$repo" show-ref --verify --quiet "refs/heads/$base"; then
      base_ref="$base"
    elif git -C "$repo" rev-parse --verify --quiet "$base^{commit}" >/dev/null 2>&1; then
      base_ref="$base"
    else
      die "review: could not resolve base reference '$base' in $repo"
    fi
    if [[ "$base_ref" == "$base" ]]; then
      base_description="base reference '$base'"
    else
      base_description="base reference '$base' (resolved locally as '$base_ref')"
    fi
  fi

  local merge_base
  merge_base="$(git -C "$repo" merge-base HEAD "$base_ref" 2>/dev/null)" \
    || die "review: failed to determine merge base against '$base_ref'"

  # Uniquify the findings filename. ts_compact() is second-resolution, so two
  # parallel reviews started in the same second would otherwise clobber each
  # other's findings. Append the repo basename plus PID and a random token.
  local out_path="$CEREBRO_SESSION_DIR/children/review-$(ts_compact)-$(basename "$repo")-$$-${RANDOM}.md"
  local child_log="${out_path%.md}.log"

  say "cerebro: reviewing $repo against $base_description (merge-base $merge_base)"
  log_event "review_started" "repo=$repo base=$base_ref merge_base=$merge_base resume=${prior:-none}"

  # Supply the merge base instead of duplicating the full diff in the prompt.
  # The native adapter enforces read-only access.
  local review_prompt
  review_prompt="Review the code changes against the $base_description. The merge base commit for this comparison is $merge_base. Run \`git diff $merge_base\` to inspect the changes included since that merge base. Provide prioritized, actionable findings: bugs, regressions, security issues, missing tests, and correctness problems. Skip style nits, speculative concerns, and over-engineering suggestions (gold-plating, defensive code for cases that cannot occur, premature abstraction, or broad rewrites where a small fix would do); prefer the smallest change that resolves a real problem. For each finding, give a one-line title, the file or area affected, and a sentence explaining the concern and a suggested fix. Output Markdown only; no preamble."

  # Shipped review-focus guidance: gate on the deliverable and real
  # contract violations, not contrived edge cases or style nits. Kept as a
  # shipped paragraph (not an overlay) so every user gets it.
  review_prompt+="

# Review/audit focus -- do NOT be nitpicky
Gate strictly on whether the DELIVERABLE does what the spec/plan asks, on REAL contract violations, and on the agent's own build/test results. A finding must reflect input that can plausibly occur in real data/usage. Do NOT raise (these are NOISE, not findings): contrived or low-probability input-string permutations (e.g. exotic multi-country location strings, a stray word matching a token) -- if it won't occur in the real data source, it is not a finding; precedence/ordering micro-cases between heuristic signals on hand-crafted inputs; style nits, naming, defensive code for cases that cannot occur, or speculative hardening; the same area whittled round after round -- once the core capability works and prior real findings are addressed, return VIABLE / MET, do not manufacture a fresh edge case each pass to avoid passing. Prefer FEWER, higher-confidence findings. When unsure whether something is real and important, omit it. Converge; do not ping-pong."

  # When the orchestrator supplies the plan via --criteria-file, make the
  # reviewer also act as the checkpoint gate: verify the change against EACH
  # acceptance criterion and end with a single machine-readable verdict line the
  # orchestrator keys its advance/retry decision on.
  if [[ -n "$criteria_block" ]]; then
    review_prompt+=" This change is meant to implement the plan below, which ends with an acceptance-criteria checkpoint. After your findings, add a section headed '## Acceptance criteria' that checks the actual implemented diff against EACH criterion in the plan. For code-reviewable criteria, list a verdict of MET, PARTIAL, or NOT MET with a one-line justification grounded in the diff. For criteria that require unavailable external capabilities such as browser operation, screenshots, visual/manual inspection, network access, PR/CI access, or other tools this read-only reviewer does not have, use verdict EXTERNAL and state the exact verification the orchestrator/user must perform; EXTERNAL criteria do not make the final verdict NOT MET. Do not claim you ran a browser/manual check here. Then output, as the VERY LAST line, exactly 'ACCEPTANCE CRITERIA: MET' if every code-reviewable criterion is fully and correctly met and only EXTERNAL criteria remain, otherwise exactly 'ACCEPTANCE CRITERIA: NOT MET'. Do not soften a PARTIAL or unmet code-reviewable criterion into MET. The plan follows between the markers.
<plan>
$criteria_block
</plan>"
  fi

  # Run a fresh read-only child on this backend with the selected review model.
  local agent; agent="$(backend_child_agent_name review)"
  local rc id_capture out_capture; id_capture="$(mktemp)"; out_capture="$(mktemp)"

  child_store_begin "$ckey" "$(current_backend)" review "$repo" "${review_branch:-auto}" "$child_log" "${prior:+preserve-id}"
  child_run 0 "$repo" "$review_prompt" "$agent" "$prior" \
    "$child_log" "$out_capture" "$id_capture" "$store_file" "$ckey" "${model:-$CEREBRO_REVIEW_MODEL}"
  rc=$?

  # The findings are the run's closing message; write them to out_path.
  if (( rc == 0 )) && [[ -s "$out_capture" ]]; then
    cp "$out_capture" "$out_path" || rc=$?
  fi
  local _cap_id; _cap_id="$(cat "$id_capture" 2>/dev/null || true)"
  rm -f "$id_capture"

  # A failed run must not supply a findings/report path; retain a native ID for resume.
  if (( rc != 0 )) || [[ ! -s "$out_capture" || ! -s "$out_path" ]]; then
    rm -f "$out_capture"
    # Retain incomplete work when this attempt or the prior run has a native ID.
    [[ -z "$_cap_id" && -z "$prior" ]] && child_store_done "$ckey"
    log_event "review_failed" "rc=$rc log=$child_log out=$out_path"
    warn "review: review run failed (rc=$rc)"
    [[ -s "$child_log" ]] && warn "see event log: $child_log"
    child_fail_stderr "$child_log"
    die "review: review run failed; not echoing a findings path"
  fi

  # The native ID is already persisted; successful completion retires the child.
  child_store_done "$ckey"
  rm -f "$out_capture"

  # Record the HEAD we just reviewed. The next `cerebro review` (without --base)
  # will diff against this SHA so the reviewer only sees what apply-review
  # changed, not the full PR diff again.
  local current_sha current_branch
  current_sha="$(git -C "$repo" rev-parse HEAD 2>/dev/null)"
  current_branch="$(git -C "$repo" rev-parse --abbrev-ref HEAD 2>/dev/null)"
  if [[ -n "$current_sha" && -n "$current_branch" ]]; then
    mkdir -p "$state_dir"
    jq -n --arg repo "$canonical_repo" --arg branch "$current_branch" \
          --arg sha "$current_sha" --arg ts "$(ts_iso)" \
          --arg findings "$out_path" \
          '{repo:$repo, branch:$branch, last_reviewed_sha:$sha, last_findings:$findings, ts:$ts}' \
          > "$state_file" 2>/dev/null || true
  fi

  log_event "review_finished" "$out_path"
  echo "$out_path"
}

# ----- subcommand: cerebro apply-review <repo> <findings> [--notes ...] ----

cmd_apply_review() {
  require_session
  build_timeout_cmd

  local repo="${1:-}"; shift || true
  local findings=""
  local prompt_text=""
  local notes=""
  local saw_prompt=0
  local pair=0
  local model=""
  if [[ $# -gt 0 && "${1:-}" != --* ]]; then
    findings="$1"; shift
  fi
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --prompt) saw_prompt=1; shift; prompt_text="${1:-}"; shift || true ;;
      --notes)  shift; notes="${1:-}";                     shift || true ;;
      --model)  shift; model="${1:-}";                     shift || true ;;
      --pair)   pair=1; shift ;;
      *) die "apply-review: unknown arg: $1" ;;
    esac
  done
  [[ -n "$model" ]] && require_model_for_backend "$model" "$(current_backend)" apply-review
  # An explicitly-passed --prompt must carry a non-empty operand. Without
  # this guard, `--prompt` with no value (or `--prompt ""`) leaves
  # prompt_text empty and would slip into the default-findings fallback
  # below, silently running a mutating apply-review the caller never asked
  # for. Treat it as a usage error instead.
  if (( saw_prompt )) && [[ -z "$prompt_text" ]]; then
    die "apply-review: --prompt requires a non-empty value"
  fi
  [[ -n "$repo" ]] \
    || die "usage: cerebro apply-review <repo-abs-path> (<findings-path> [--notes \"...\"] | --prompt \"<text>\") [--model <provider/model>]"
  [[ "$repo" = /* ]] || die "apply-review: repo path must be absolute: $repo"
  [[ -d "$repo" ]] || die "apply-review: repo not a directory: $repo"

  # Default findings: when neither a findings path nor --prompt is given,
  # fall back to the last review's findings for this repo+branch so the
  # orchestrator cannot pass a guessed/stale name. Gate on saw_prompt too:
  # a genuinely-omitted --prompt may default, an explicitly-passed one
  # (already validated non-empty above) never silently falls back.
  if [[ -z "$findings" && -z "$prompt_text" ]] && (( ! saw_prompt )); then
    local rk sf
    rk="$(repo_state_key "$repo" 2>/dev/null)" || true
    sf="$CEREBRO_SESSION_DIR/review-state/$rk.json"
    local cur_branch
    cur_branch="$(git -C "$repo" rev-parse --abbrev-ref HEAD 2>/dev/null)"
    if [[ -n "$rk" && -r "$sf" ]]; then
      local lf lb
      lf="$(jq -r '.last_findings // empty' "$sf" 2>/dev/null)"
      lb="$(jq -r '.branch // empty'        "$sf" 2>/dev/null)"
      if [[ -n "$lf" && "$lb" == "$cur_branch" && -s "$lf" ]]; then
        findings="$lf"
        say "cerebro: apply-review defaulting to last review findings: $findings"
      fi
    fi
    [[ -n "$findings" ]] || die "apply-review: no findings path given and no prior review for this repo+branch in this session; run 'cerebro review $repo' first, or pass --prompt \"<text>\""
  fi

  if [[ -n "$findings" && -n "$prompt_text" ]]; then
    die "apply-review: pass either <findings-path> or --prompt, not both"
  fi
  if [[ -z "$findings" && -z "$prompt_text" ]]; then
    die "apply-review: requires <findings-path> or --prompt \"<text>\""
  fi
  if [[ -n "$prompt_text" && -n "$notes" ]]; then
    die "apply-review: --notes is only meaningful with a findings file; bake the context into --prompt instead"
  fi
  if [[ -n "$findings" ]]; then
    # Existence + staleness check. die (exit 1) on a bad path, naming the
    # correct last-review path when we know it; warn (non-fatal) when the
    # caller passed a valid-but-older findings file.
    local rk sf lf="" lb="" cur_branch
    rk="$(repo_state_key "$repo" 2>/dev/null)" || true
    sf="$CEREBRO_SESSION_DIR/review-state/$rk.json"
    cur_branch="$(git -C "$repo" rev-parse --abbrev-ref HEAD 2>/dev/null)"
    # Only treat .last_findings as "latest for this repo+branch" when the
    # stored branch matches the current branch -- same guard as the
    # default-findings path. Otherwise a post-branch-switch state file would
    # name another branch's findings as this branch's latest.
    if [[ -n "$rk" && -r "$sf" ]]; then
      lb="$(jq -r '.branch // empty' "$sf" 2>/dev/null)"
      if [[ -n "$lb" && "$lb" == "$cur_branch" ]]; then
        lf="$(jq -r '.last_findings // empty' "$sf" 2>/dev/null)"
      fi
    fi
    if [[ ! -r "$findings" || ! -s "$findings" ]]; then
      if [[ -n "$lf" && -s "$lf" ]]; then
        die "apply-review: findings not readable/empty: $findings (the last review for this repo+branch is: $lf)"
      fi
      die "apply-review: findings not readable/empty: $findings"
    fi
    if [[ -n "$lf" && "$lf" != "$findings" ]]; then
      warn "apply-review: '$findings' is not the latest review for this repo+branch (latest: $lf)"
    fi
  fi

  local child_log; child_log="$(child_log_path apply-review)"

  local provider; provider="$(backend_child_provider apply-review)"
  local agent; agent="$(backend_child_agent_name apply-review)"

  # Child-session continuity is only for interrupted/incomplete apply-review
  # work. A completed fixer child must not be the starting context for another
  # sub-agent on the same branch.
  local store_file; store_file="$(child_sessions_file)"
  local ar_branch; ar_branch="$(git -C "$repo" rev-parse --abbrev-ref HEAD 2>/dev/null)"
  local ckey prior=""
  ckey="$(child_key "$repo" apply-review "${ar_branch:-default}")"
  if prior="$(child_session_get "$ckey")" && [[ -n "$prior" ]] && child_session_running_fresh "$ckey"; then
    :
  else
    prior=""
  fi

  if [[ -n "$findings" ]]; then
    say "cerebro: applying review fixes in $repo"
    log_event "apply_review_started" "findings=$findings resume=${prior:-none}"
  else
    say "cerebro: applying inline fix in $repo"
    log_event "apply_review_started" "prompt=inline resume=${prior:-none}"
  fi

  local PAIR_SID="" PAIR_FIFO="" PAIR_STEER="" PAIR_IDLE="" PAIR_STALL="" PAIR_STALL_BUSY=""
  local PAIR_PORT="" PAIR_SERVE_PID="" PAIR_BASE_URL="" PAIR_OPTS=() PAIR_PGID="" PAIR_LAUNCH=()
  (( pair )) && pair_begin apply-review "$repo" "$ar_branch" "$child_log" "$prior"

  local child_prompt
  child_prompt="$(
    if [[ -n "$findings" ]]; then
      printf 'Apply the following review findings on the current branch. Commit and push so the existing PR updates.\n\n<orchestrator-notes>\n%s\n</orchestrator-notes>\n\n<findings>\n' "$notes"
      cat "$findings"
      printf '\n</findings>\n'
    else
      printf 'Apply the following fix on the current branch. Commit and push so the existing PR updates.\n\n<task>\n%s\n</task>\n' "$prompt_text"
    fi
  )"

  local rc id_capture msg_capture; id_capture="$(mktemp)"; msg_capture="$(mktemp)"
  local stall_n=0
  while :; do
    child_store_begin "$ckey" "$provider" apply-review "$repo" "${ar_branch:-default}" "$child_log" "${prior:+preserve-id}"
    child_run "$pair" "$repo" "$child_prompt" "$agent" "$prior" \
      "$child_log" "$msg_capture" "$id_capture" "$store_file" "$ckey" "$model"
    rc=$?
    pair_cleanup "$pair"

    if (( pair )) && pair_stalled "$child_log"; then
      if (( stall_n < ${CEREBRO_PAIR_STALL_RETRIES:-2} )); then
        stall_n=$((stall_n + 1))
        pair_stall_backoff "$stall_n"
        pair_stall_clear "$child_log"
        pair_begin apply-review "$repo" "$ar_branch" "$child_log" "$PAIR_SID"
        prior="$PAIR_SID"
        continue
      fi
      pair_stall_clear "$child_log"
      log_event "pair_stall_giveup" "after=$stall_n stalls log=$child_log resume=$PAIR_SID"
      rm -f "$id_capture" "$msg_capture"
      die "apply-review: paired child stalled $stall_n time(s) and was not restarted further; it remains resumable (id $PAIR_SID) -- see $child_log"
    fi
    break
  done

  if (( rc != 0 )); then
    local _cap_id; _cap_id="$(cat "$id_capture" 2>/dev/null || true)"
    rm -f "$id_capture" "$msg_capture"
    # Retain incomplete work when this attempt or the prior run has a native ID.
    [[ -z "$_cap_id" && -z "$prior" ]] && child_store_done "$ckey"
    log_event "apply_review_failed" "rc=$rc log=$child_log"
    warn "apply-review: child failed (rc=$rc); see $child_log"
    child_fail_stderr "$child_log"
    die "apply-review: child failed (rc=$rc); see $child_log"
  fi
  child_store_done "$ckey"
  local child_id; child_id="$(cat "$id_capture" 2>/dev/null || true)"
  rm -f "$id_capture"
  log_event "apply_review_finished" "$child_log"
  pair_report "$pair" "$child_log"
  surface_child_reply "$msg_capture" apply-review "$child_id"
  rm -f "$msg_capture"
  echo "$child_log"
}
