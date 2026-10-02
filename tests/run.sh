#!/usr/bin/env bash
# Plain-bash tests for Cerebro's command guards, sessions and native children.
# Backend fixtures use local transports without model calls.
# Run with: bash tests/run.sh

set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CEREBRO_BIN="$here/../bin/cerebro"
[[ -x "$CEREBRO_BIN" ]] || { echo "cerebro not found or not executable: $CEREBRO_BIN" >&2; exit 1; }

# Isolated sandbox.
WORKDIR="$(mktemp -d)"
WORKDIR="$(cd "$WORKDIR" && pwd -P)"
trap 'rm -rf "$WORKDIR"' EXIT

export CEREBRO_HOME="$WORKDIR/cerebro-home"
export CEREBRO_SESSION_ID="test-session"
export CEREBRO_JEV_ENABLED=0
# No backend invocation may escape to a personal installation or credential.
# Per-test fixtures prepend their directory; an unexpected backend fails closed.
unset CEREBRO_BACKEND CEREBRO_RESUME_BACKEND \
      CEREBRO_MODEL CEREBRO_SUPERVISOR_MODEL CEREBRO_REVIEW_MODEL \
      CEREBRO_PI_CMD CEREBRO_CLAUDE_CMD CEREBRO_CODEX_CMD \
      CEREBRO_CLAUDE_BASE_URL CEREBRO_CLAUDE_AUTH_TOKEN \
      PI_CODING_AGENT_DIR CEREBRO_PI_CHECKED \
      CEREBRO_JEV_API_KEY
BACKEND_GUARDS="$WORKDIR/backend-guards"
mkdir -p "$BACKEND_GUARDS"
for backend in pi claude codex; do
  cat > "$BACKEND_GUARDS/$backend" <<'EOF'
#!/usr/bin/env bash
printf 'test refused unconfigured backend fixture: %s\n' "$0" >&2
exit 97
EOF
  chmod +x "$BACKEND_GUARDS/$backend"
done
export PATH="$BACKEND_GUARDS:$PATH"

initialize_session() {
  local directory="$1" backend="${2:-pi}"
  mkdir -p "$directory/children" "$directory/plans"
  : > "$directory/transcript.jsonl"
  jq -cn --arg backend "$backend" '{backend:$backend,role:"supervisor"}' > "$directory/metadata.json"
}
initialize_session "$CEREBRO_HOME/sessions/$CEREBRO_SESSION_ID"

REPO="$WORKDIR/repo"
mkdir -p "$REPO"
(
  cd "$REPO"
  git init -q -b main . 2>/dev/null || git init -q .
  git config user.email test@example.com
  git config user.name test
  git commit --allow-empty -q -m init
  : > main.sh
  git add main.sh
  git commit -q -m "add main.sh"
) || { echo "failed to set up test repo" >&2; exit 1; }

pass=0
fail=0
failures=()

# install_pi_fixture <dir> <JSON configuration> -- native Pi JSON RPC.
# Scenario hooks run in the actual child cwd and receive the prompt on
# stdin, so worktree and mutation tests exercise the real CLI boundary.
install_pi_fixture() {
  local directory="$1" configuration="$2"
  mkdir -p "$directory"
  printf '%s\n' "$configuration" > "$directory/fixture.json"
  cat > "$directory/pi" <<EOF
#!/usr/bin/env bash
exec python3 "$here/pi_fixture.py" "$directory" "\$@"
EOF
  chmod +x "$directory/pi"
}

seed_pi_session() {
  python3 "$here/pi_fixture.py" --seed "$1" "$REPO"
}

# run_case <id> <description> <expected-rc> -- <cmd...>
# Optional: STDERR_CONTAINS=<substring> env to assert a substring of stderr.
# Optional: STDOUT_CONTAINS=<substring> env to assert a substring of stdout.
run_case() {
  local id="$1" desc="$2" expected="$3"
  shift 3
  [[ "$1" == "--" ]] && shift
  local needle="${STDERR_CONTAINS:-}"
  local out_needle="${STDOUT_CONTAINS:-}"
  local out err rc
  out="$("$@" 2>"$WORKDIR/stderr")"
  rc=$?
  err="$(cat "$WORKDIR/stderr")"

  local note=""
  if (( rc != expected )); then
    note="rc=$rc (expected $expected)"
  fi
  if [[ -n "$needle" && "$err" != *"$needle"* ]]; then
    note="${note:+$note; }stderr missing '$needle': $err"
  fi
  if [[ -n "$out_needle" && "$out" != *"$out_needle"* ]]; then
    note="${note:+$note; }stdout missing '$out_needle': $out"
  fi

  if [[ -z "$note" ]]; then
    printf 'PASS  %s  %s\n' "$id" "$desc"
    pass=$((pass + 1))
  else
    printf 'FAIL  %s  %s  [%s]\n' "$id" "$desc" "$note"
    fail=$((fail + 1))
    failures+=("$id $desc :: $note")
  fi
  unset STDERR_CONTAINS STDOUT_CONTAINS
}

# --- 1. git happy paths ---
run_case 01 "git status happy" 0 -- "$CEREBRO_BIN" git "$REPO" status

run_case 02 "git log --oneline happy" 0 -- "$CEREBRO_BIN" git "$REPO" log --oneline -n 1

run_case 03 "git diff HEAD~1 HEAD happy" 0 -- "$CEREBRO_BIN" git "$REPO" diff HEAD~1 HEAD

# --- 4. denied git subcommand ---
STDERR_CONTAINS="not on allow-list" \
run_case 04 "git commit denied" 4 -- "$CEREBRO_BIN" git "$REPO" commit -m x

# --- 5. denied git flag (branch mutate) ---
STDERR_CONTAINS="mutating flag" \
run_case 05 "git branch -d denied" 5 -- "$CEREBRO_BIN" git "$REPO" branch -d main

# --- 6. denied git config write (positional with no --get) ---
STDERR_CONTAINS="missing --get" \
run_case 06 "git config user.email x@y denied" 5 -- "$CEREBRO_BIN" git "$REPO" config user.email x@y

# --- 7. denied global git flag (subcommand position is a flag) ---
STDERR_CONTAINS="subcommand position cannot be a flag" \
run_case 07 "git -c foo=bar log denied" 5 -- "$CEREBRO_BIN" git "$REPO" -c foo=bar log

# --- 8. shell metachars are inert (no shell in the exec path) ---
"$CEREBRO_BIN" git "$REPO" log ';foo;' >/dev/null 2>"$WORKDIR/stderr"
err="$(cat "$WORKDIR/stderr")"
if [[ "$err" != *"shell metacharacter"* ]]; then
  printf 'PASS  08  shell-metachar arg reaches git\n'
  pass=$((pass + 1))
else
  printf 'FAIL  08  bridge still rejects shell metachars: %s\n' "$err"
  fail=$((fail + 1))
  failures+=("08 :: $err")
fi

# --- 9. non-repo path ---
STDERR_CONTAINS="not a git repo" \
run_case 09 "git /tmp status (not a repo)" 3 -- "$CEREBRO_BIN" git /tmp status

# --- 10. non-absolute path ---
STDERR_CONTAINS="must be absolute" \
run_case 10 "git relative status" 3 -- "$CEREBRO_BIN" git relative status

# --- 11. denied gh write ---
STDERR_CONTAINS="not allow-listed" \
run_case 11 "gh pr create denied" 4 -- "$CEREBRO_BIN" gh "$REPO" pr create

# --- 12. denied gh api method ---
STDERR_CONTAINS="write flag" \
run_case 12 "gh api -X POST denied" 5 -- "$CEREBRO_BIN" gh "$REPO" api -X POST /repos/x/y

# --- 13. denied gh write (gist create); gist list itself is allow-listed ---
STDERR_CONTAINS="not allow-listed" \
run_case 13 "gh gist create denied" 4 -- "$CEREBRO_BIN" gh "$REPO" gist create

# --- 14. read happy ---
run_case 14 "read main.sh happy" 0 -- "$CEREBRO_BIN" read "$REPO" main.sh

# --- 15. read escape ---
STDERR_CONTAINS="path escapes repo" \
run_case 15 "read ../etc/passwd denied" 6 -- "$CEREBRO_BIN" read "$REPO" ../etc/passwd

# --- 16. read non-file: benign by default (marker + exit 0) ---
STDOUT_CONTAINS="(not found:" \
run_case 16 "read . (directory) benign miss" 0 -- "$CEREBRO_BIN" read "$REPO" .

# --- 16b. read non-file --strict-missing restores exit 3 ---
STDERR_CONTAINS="not a regular file" \
run_case 16b "read . (directory) --strict-missing" 3 -- "$CEREBRO_BIN" read "$REPO" . --strict-missing

# --- 16c. read missing in-repo file: benign by default ---
STDOUT_CONTAINS="(not found:" \
run_case 16c "read no/such/file.txt benign miss" 0 -- "$CEREBRO_BIN" read "$REPO" no/such/file.txt

# --- 16d. read missing in-repo file --strict-missing ---
STDERR_CONTAINS="not a regular file" \
run_case 16d "read no/such/file.txt --strict-missing" 3 -- "$CEREBRO_BIN" read "$REPO" no/such/file.txt --strict-missing

# --- 17. grep zero matches: benign by default ('(no matches)' + exit 0) ---
if command -v rg >/dev/null 2>&1; then
  STDOUT_CONTAINS="(no matches)" \
  run_case 17 "grep zero-match benign" 0 -- "$CEREBRO_BIN" grep "$REPO" 'something'
else
  printf 'SKIP  17  grep zero-match (rg not installed)\n'
fi

# --- 17b. grep with NO flag args (regression for nounset + empty rg_args) ---
if command -v rg >/dev/null 2>&1; then
  STDOUT_CONTAINS="(no matches)" \
  run_case 17b "grep no-flag-args zero-match benign" 0 -- "$CEREBRO_BIN" grep "$REPO" 'no-such-literal'
fi

# --- 17c. grep zero matches --strict-missing restores rg-native exit 1 ---
if command -v rg >/dev/null 2>&1; then
  run_case 17c "grep zero-match --strict-missing (rg exit 1)" 1 -- "$CEREBRO_BIN" grep "$REPO" 'something' --strict-missing
fi

# --- 17d. grep bad regex: genuine rg error stays hard (rc >= 2) ---
if command -v rg >/dev/null 2>&1; then
  "$CEREBRO_BIN" grep "$REPO" '(' >/dev/null 2>&1
  rc=$?
  if [[ $rc -ge 2 ]]; then
    printf 'PASS  17d  grep bad-regex stays hard (rc=%d)\n' "$rc"
    pass=$((pass + 1))
  else
    printf 'FAIL  17d  grep bad-regex [rc=%d expected >=2]\n' "$rc"
    fail=$((fail + 1))
    failures+=("17d grep bad-regex :: rc=$rc")
  fi
fi

# --- 18. grep escape ---
STDERR_CONTAINS="path escapes repo" \
run_case 18 "grep --path ../.. escape denied" 6 -- "$CEREBRO_BIN" grep "$REPO" foo --path ../..

# --- 19. ls happy ---
out="$("$CEREBRO_BIN" ls "$REPO" 2>/dev/null)"
rc=$?
if [[ $rc -eq 0 && "$out" == *"main.sh"* ]]; then
  printf 'PASS  19  ls happy (lists main.sh)\n'
  pass=$((pass + 1))
else
  printf 'FAIL  19  ls happy [rc=%d out=%s]\n' "$rc" "$out"
  fail=$((fail + 1))
  failures+=("19 ls happy :: rc=$rc out=$out")
fi

# --- 20. ls escape ---
STDERR_CONTAINS="path escapes repo" \
run_case 20 "ls ../.. escape denied" 6 -- "$CEREBRO_BIN" ls "$REPO" ../..

# --- 20b. ls missing in-repo dir: benign by default ---
STDOUT_CONTAINS="(not found:" \
run_case 20b "ls no/such/dir benign miss" 0 -- "$CEREBRO_BIN" ls "$REPO" no/such/dir

# --- 20c. ls missing in-repo dir --strict-missing ---
STDERR_CONTAINS="not a directory" \
run_case 20c "ls no/such/dir --strict-missing" 3 -- "$CEREBRO_BIN" ls "$REPO" no/such/dir --strict-missing

# --- 20d. ls bare-abs missing path: benign by default (exit-7 routing) ---
STDOUT_CONTAINS="(not found:" \
run_case 20d "ls bare-abs missing benign" 0 -- "$CEREBRO_BIN" ls "$WORKDIR/does-not-exist"

# --- 20e. ls bare-abs missing path --strict-missing ---
STDERR_CONTAINS="not found" \
run_case 20e "ls bare-abs missing --strict-missing" 3 -- "$CEREBRO_BIN" ls "$WORKDIR/does-not-exist" --strict-missing

# --- 21. unknown top-level subcommand ---
STDERR_CONTAINS="unknown subcommand" \
run_case 21 "cerebro doesnotexist" 1 -- "$CEREBRO_BIN" doesnotexist

# --- 22. read outside repo (not a git worktree): benign by default ---
STDOUT_CONTAINS="(not found:" \
run_case 22 "read /etc passwd (not a worktree) benign" 0 -- "$CEREBRO_BIN" read /etc passwd

# --- 22b. read /etc passwd --strict-missing restores exit 3 ---
STDERR_CONTAINS="not a git worktree" \
run_case 22b "read /etc passwd --strict-missing" 3 -- "$CEREBRO_BIN" read /etc passwd --strict-missing

# --- 23. grep bare-abs: pattern required (no worktree, but pattern missing) ---
STDERR_CONTAINS="usage" \
run_case 23 "grep /etc (no pattern) usage error" 2 -- "$CEREBRO_BIN" grep /etc

# Pre-create a directory the bare-abs cases below can read out of.
mkdir -p "$WORKDIR/lookups"
printf 'findme\n' > "$WORKDIR/lookups/needle.txt"

# --- 24. ls bare-abs against a directory the sandbox controls ---
out="$("$CEREBRO_BIN" ls "$WORKDIR/lookups" 2>/dev/null)"
rc=$?
if [[ $rc -eq 0 && "$out" == *"needle.txt"* ]]; then
  printf 'PASS  24  ls bare-abs (lists needle.txt)\n'
  pass=$((pass + 1))
else
  printf 'FAIL  24  ls bare-abs [rc=%d out=%s]\n' "$rc" "$out"
  fail=$((fail + 1))
  failures+=("24 ls bare-abs :: rc=$rc out=$out")
fi

# --- 25. git symbolic-ref SET form denied (read form is allowed; see 73) ---
STDERR_CONTAINS="SET form" \
run_case 25 "git symbolic-ref SET form denied" 5 -- "$CEREBRO_BIN" git "$REPO" symbolic-ref HEAD refs/heads/x

# --- 26. git remote add denied ---
STDERR_CONTAINS="git remote" \
run_case 26 "git remote add denied" 5 -- "$CEREBRO_BIN" git "$REPO" remote add foo http://example/

# --- 27. git remote -v add smuggle denied ---
STDERR_CONTAINS="git remote: mutating action" \
run_case 27 "git remote -v add denied" 5 -- "$CEREBRO_BIN" git "$REPO" remote -v add foo http://example/

# --- 28. git remote set-url denied ---
STDERR_CONTAINS="git remote: mutating action" \
run_case 28 "git remote set-url denied" 5 -- "$CEREBRO_BIN" git "$REPO" remote set-url origin foo

# --- 29. git diff --no-index denied ---
STDERR_CONTAINS="no-index" \
run_case 29 "git diff --no-index denied" 5 -- "$CEREBRO_BIN" git "$REPO" diff --no-index /etc/passwd /etc/hosts

# --- 30. git blame --contents denied ---
STDERR_CONTAINS="contents" \
run_case 30 "git blame --contents denied" 5 -- "$CEREBRO_BIN" git "$REPO" blame --contents /etc/passwd HEAD --

# --- 31. git config --file denied ---
STDERR_CONTAINS="git config" \
run_case 31 "git config --file /etc/passwd denied" 5 -- "$CEREBRO_BIN" git "$REPO" config --file /etc/passwd --get foo

# --- 32. git config --global denied ---
STDERR_CONTAINS="git config" \
run_case 32 "git config --global denied" 5 -- "$CEREBRO_BIN" git "$REPO" config --global --get user.email

# --- 33. git ls-files --exclude-from denied ---
STDERR_CONTAINS="ls-files" \
run_case 33 "git ls-files --exclude-from denied" 5 -- "$CEREBRO_BIN" git "$REPO" ls-files --exclude-from /etc/passwd

# --- 34. gh api -XPOST attached short denied ---
STDERR_CONTAINS="write flag" \
run_case 34 "gh api -XPOST denied (attached)" 5 -- "$CEREBRO_BIN" gh "$REPO" api -XPOST /repos/x/y

# --- 35. gh api -Ffoo=bar attached short denied ---
STDERR_CONTAINS="write flag" \
run_case 35 "gh api -Ffoo=bar denied (attached)" 5 -- "$CEREBRO_BIN" gh "$REPO" api -Ffoo=bar /repos/x/y

# --- 36. gh api -ffoo=bar attached short denied ---
STDERR_CONTAINS="write flag" \
run_case 36 "gh api -ffoo=bar denied (attached)" 5 -- "$CEREBRO_BIN" gh "$REPO" api -ffoo=bar /repos/x/y

# --- 37. gh api --method=POST attached long denied ---
STDERR_CONTAINS="write flag" \
run_case 37 "gh api --method=POST denied (attached)" 5 -- "$CEREBRO_BIN" gh "$REPO" api --method=POST /repos/x/y

# --- 38. git config --list defaults to local (succeeds) ---
run_case 38 "git config --list happy (forced --local)" 0 -- "$CEREBRO_BIN" git "$REPO" config --list

# --- 39. git config -fpath attached form denied ---
STDERR_CONTAINS="git config" \
run_case 39 "git config -f/etc/passwd denied (attached)" 5 -- "$CEREBRO_BIN" git "$REPO" config -f/etc/passwd --get foo

# --- 40. git config --file=/etc/passwd attached form denied ---
STDERR_CONTAINS="git config" \
run_case 40 "git config --file=/etc/passwd denied (attached)" 5 -- "$CEREBRO_BIN" git "$REPO" config --file=/etc/passwd --get foo

# --- 41/42. .git/index left untouched by read-only bridge ---
# We poke a workdir file's mtime so a stat-only refresh of the index would
# otherwise happen. With `--no-optional-locks` plumbed into the bridge,
# `git status` skips the lazy index rewrite. (`git diff` upstream still
# refreshes the index when stat info is stale even with --no-optional-locks,
# so we exercise diff without the artificial mtime poke -- under realistic
# use the bridge must not touch the index there either.)
stat_index() {
  python3 - "$REPO/.git/index" <<'PY'
import os, sys
s = os.stat(sys.argv[1])
print(s.st_mtime_ns, s.st_ino, s.st_size)
PY
}

touch -t 202001010000 "$REPO/main.sh"
before_status="$(stat_index)"
"$CEREBRO_BIN" git "$REPO" status >/dev/null 2>&1
after_status="$(stat_index)"
if [[ "$before_status" == "$after_status" ]]; then
  printf 'PASS  41  git status leaves .git/index untouched\n'
  pass=$((pass + 1))
else
  printf 'FAIL  41  git status mutated .git/index [before=%s after=%s]\n' \
    "$before_status" "$after_status"
  fail=$((fail + 1))
  failures+=("41 git status leaves .git/index untouched :: before=$before_status after=$after_status")
fi

# Settle the index after the status path (also clears any pending stat
# discrepancy from earlier tests) before sampling for the diff test.
git -C "$REPO" update-index --refresh >/dev/null 2>&1 || true
before_diff="$(stat_index)"
"$CEREBRO_BIN" git "$REPO" diff >/dev/null 2>&1
after_diff="$(stat_index)"
if [[ "$before_diff" == "$after_diff" ]]; then
  printf 'PASS  42  git diff leaves .git/index untouched\n'
  pass=$((pass + 1))
else
  printf 'FAIL  42  git diff mutated .git/index [before=%s after=%s]\n' \
    "$before_diff" "$after_diff"
  fail=$((fail + 1))
  failures+=("42 git diff leaves .git/index untouched :: before=$before_diff after=$after_diff")
fi

# --- 43-46. external-helper flags refused on read-only subcommands ---
STDERR_CONTAINS="external helper flag" \
run_case 43 "git diff --ext-diff denied" 5 -- "$CEREBRO_BIN" git "$REPO" diff --ext-diff
STDERR_CONTAINS="external helper flag" \
run_case 44 "git log --textconv denied" 5 -- "$CEREBRO_BIN" git "$REPO" log --textconv
STDERR_CONTAINS="external helper flag" \
run_case 45 "git show --filters denied" 5 -- "$CEREBRO_BIN" git "$REPO" show --filters
STDERR_CONTAINS="external helper flag" \
run_case 46 "git blame --textconv denied" 5 -- "$CEREBRO_BIN" git "$REPO" blame --textconv main.sh

# --- 47. positive: diff still works when repo config sets diff.external=/bin/false ---
# Without the `--no-ext-diff` injection (or with a working override), an
# attacker-controlled `.git/config` could redirect every diff through an
# arbitrary program. The bridge must produce normal diff output here.
git -C "$REPO" config diff.external /bin/false
echo "tampered" > "$REPO/main.sh"
diff_out="$("$CEREBRO_BIN" git "$REPO" diff -- main.sh 2>"$WORKDIR/stderr")"
diff_rc=$?
diff_err="$(cat "$WORKDIR/stderr")"
git -C "$REPO" config --unset diff.external
if [[ $diff_rc -eq 0 && "$diff_out" == *"+tampered"* && "$diff_err" != *"external diff"* ]]; then
  printf 'PASS  47  git diff bypasses repo diff.external=/bin/false\n'
  pass=$((pass + 1))
else
  printf 'FAIL  47  git diff with diff.external=/bin/false [rc=%d out=%s err=%s]\n' \
    "$diff_rc" "$diff_out" "$diff_err"
  fail=$((fail + 1))
  failures+=("47 diff.external bypass :: rc=$diff_rc out=$diff_out err=$diff_err")
fi
# Restore main.sh so later test rounds see a clean tree.
git -C "$REPO" checkout -q -- main.sh 2>/dev/null || true

# --- 48-50. gh happy paths via a PATH stub ---
# We can't (and don't want to) call the real `gh` from tests. Drop a stub on
# PATH that records argv to a file, then assert each allowed dispatch reaches
# the stub with the expected argv. This guards the actual exec path -- denial
# tests alone would miss a regression that broke `exec gh "$top" "$@"`.
GH_STUB_DIR="$WORKDIR/gh-stub"
mkdir -p "$GH_STUB_DIR"
GH_ARGV_LOG="$WORKDIR/gh-argv.log"
cat > "$GH_STUB_DIR/gh" <<EOF
#!/usr/bin/env bash
printf '%s\n' "\$*" >> "$GH_ARGV_LOG"
exit 0
EOF
chmod +x "$GH_STUB_DIR/gh"

gh_happy() {
  local id="$1" desc="$2" expected_argv="$3"; shift 3
  : > "$GH_ARGV_LOG"
  PATH="$GH_STUB_DIR:$PATH" "$CEREBRO_BIN" gh "$REPO" "$@" >/dev/null 2>"$WORKDIR/stderr"
  local rc=$?
  local got; got="$(cat "$GH_ARGV_LOG" 2>/dev/null)"
  if [[ $rc -eq 0 && "$got" == "$expected_argv" ]]; then
    printf 'PASS  %s  %s\n' "$id" "$desc"
    pass=$((pass + 1))
  else
    printf 'FAIL  %s  %s [rc=%d argv=%q expected=%q]\n' \
      "$id" "$desc" "$rc" "$got" "$expected_argv"
    fail=$((fail + 1))
    failures+=("$id $desc :: rc=$rc argv=$got expected=$expected_argv")
  fi
}

gh_happy 48 "gh pr view 123 dispatches" "pr view 123"        pr view 123
gh_happy 49 "gh pr list --limit 5 dispatches" "pr list --limit 5" pr list --limit 5
gh_happy 50 "gh api /repos/foo/bar dispatches" "api /repos/foo/bar" api /repos/foo/bar

# --- 51. shell metachars pass through to gh (jq -q with commas/parens) ---
gh_happy 51 "gh pr view --json with -q containing commas/parens/spaces" \
  "pr view 64 --json baseRefName,headRefName,commits -q .baseRefName, .headRefName, (.commits | length)" \
  pr view 64 --json baseRefName,headRefName,commits -q ".baseRefName, .headRefName, (.commits | length)"

# ========================================================================
# Category A coverage (52-63): forgiving argv shapes for read/grep/ls.
# ========================================================================

# --- 52. read abs file path infers enclosing repo ---
out="$("$CEREBRO_BIN" read "$REPO/main.sh" --range 1:1 2>"$WORKDIR/stderr")"
rc=$?
err="$(cat "$WORKDIR/stderr")"
if [[ $rc -eq 0 && "$err" == *"inferred repo"* ]]; then
  printf 'PASS  52  read with abs file path infers repo\n'
  pass=$((pass + 1))
else
  printf 'FAIL  52  read abs-file repo-infer [rc=%d err=%s]\n' "$rc" "$err"
  fail=$((fail + 1))
  failures+=("52 read abs-file repo-infer :: rc=$rc err=$err")
fi

# --- 53. read abs file no flag (legacy file form ok) ---
run_case 53 "read abs file no flag happy" 0 -- "$CEREBRO_BIN" read "$REPO/main.sh"

# --- 54. read --range N-M ---
run_case 54 "read --range N-M" 0 -- "$CEREBRO_BIN" read "$REPO" main.sh --range 1-1

# --- 55. read --range N..M ---
run_case 55 "read --range N..M" 0 -- "$CEREBRO_BIN" read "$REPO" main.sh --range 1..1

# --- 56. read --range N M (two ints) ---
run_case 56 "read --range N M" 0 -- "$CEREBRO_BIN" read "$REPO" main.sh --range 1 1

# --- 57. read --from N --to M ---
run_case 57 "read --from N --to M" 0 -- "$CEREBRO_BIN" read "$REPO" main.sh --from 1 --to 1

# --- 58. read --range N (open-ended) ---
run_case 58 "read --range bare-N" 0 -- "$CEREBRO_BIN" read "$REPO" main.sh --range 1

# --- 59. read ./main.sh ---
run_case 59 "read ./main.sh" 0 -- "$CEREBRO_BIN" read "$REPO" ./main.sh

# --- 60. read bogus --range value emits canonical hint ---
STDERR_CONTAINS="canonical: --range" \
run_case 60 "read bad --range value with hint" 2 -- "$CEREBRO_BIN" read "$REPO" main.sh --range abc

# --- 61. grep --type rs aliased to rust ---
if command -v rg >/dev/null 2>&1; then
  "$CEREBRO_BIN" grep "$REPO" 'pattern' --type rs >/dev/null 2>"$WORKDIR/stderr"
  rc=$?
  err="$(cat "$WORKDIR/stderr")"
  if [[ ( $rc -eq 0 || $rc -eq 1 ) && "$err" != *"unrecognized file type"* ]]; then
    printf 'PASS  61  grep --type rs aliased to rust (rc=%d)\n' "$rc"
    pass=$((pass + 1))
  else
    printf 'FAIL  61  grep --type rs alias [rc=%d err=%s]\n' "$rc" "$err"
    fail=$((fail + 1))
    failures+=("61 grep --type rs alias :: rc=$rc err=$err")
  fi
else
  printf 'SKIP  61  grep --type rs aliased (rg not installed)\n'
fi

# --- 62. grep --type yml aliased to yaml ---
if command -v rg >/dev/null 2>&1; then
  "$CEREBRO_BIN" grep "$REPO" 'pattern' --type yml >/dev/null 2>"$WORKDIR/stderr"
  rc=$?
  err="$(cat "$WORKDIR/stderr")"
  if [[ ( $rc -eq 0 || $rc -eq 1 ) && "$err" != *"unrecognized file type"* ]]; then
    printf 'PASS  62  grep --type yml aliased to yaml (rc=%d)\n' "$rc"
    pass=$((pass + 1))
  else
    printf 'FAIL  62  grep --type yml alias [rc=%d err=%s]\n' "$rc" "$err"
    fail=$((fail + 1))
    failures+=("62 grep --type yml alias :: rc=$rc err=$err")
  fi
else
  printf 'SKIP  62  grep --type yml aliased (rg not installed)\n'
fi

# --- 63. grep unknown arg with canonical hint ---
STDERR_CONTAINS="canonical: cerebro grep" \
run_case 63 "grep unknown arg with hint" 2 -- "$CEREBRO_BIN" grep "$REPO" foo --nope

# ========================================================================
# Category B coverage (64-73d): broadened git allow-list.
# ========================================================================

run_case 64 "git rev-list HEAD happy" 0 -- "$CEREBRO_BIN" git "$REPO" rev-list -n 1 HEAD
run_case 65 "git count-objects happy" 0 -- "$CEREBRO_BIN" git "$REPO" count-objects
run_case 66 "git show-ref happy" 0 -- "$CEREBRO_BIN" git "$REPO" show-ref
run_case 67 "git check-ref-format happy" 0 -- "$CEREBRO_BIN" git "$REPO" check-ref-format refs/heads/main
run_case 68 "git var GIT_EDITOR happy" 0 -- "$CEREBRO_BIN" git "$REPO" var GIT_EDITOR
run_case 69 "git diff-tree happy" 0 -- "$CEREBRO_BIN" git "$REPO" diff-tree -r HEAD
run_case 70 "git range-diff self happy" 0 -- "$CEREBRO_BIN" git "$REPO" range-diff HEAD~1..HEAD HEAD~1..HEAD

# --- 71. git archive --output denied (matched by global deny-list) ---
STDERR_CONTAINS="denied global flag: --output" \
run_case 71 "git archive --output denied" 5 -- "$CEREBRO_BIN" git "$REPO" archive --output /tmp/x.tar HEAD

# --- 72. git hash-object -w denied ---
STDERR_CONTAINS="-w writes" \
run_case 72 "git hash-object -w denied" 5 -- \
  bash -c "printf x | '$CEREBRO_BIN' git '$REPO' hash-object -w --stdin"

# --- 73. git symbolic-ref read form happy ---
run_case 73 "git symbolic-ref read form happy" 0 -- "$CEREBRO_BIN" git "$REPO" symbolic-ref HEAD

# --- 73b. git apply requires --check ---
STDERR_CONTAINS="only --check form allowed" \
run_case 73b "git apply without --check denied" 5 -- "$CEREBRO_BIN" git "$REPO" apply some.patch

# --- 73c. git fetch reaches git (allow-list + no mutating flags) ---
"$CEREBRO_BIN" git "$REPO" fetch >/dev/null 2>"$WORKDIR/stderr"
rc=$?
err="$(cat "$WORKDIR/stderr")"
if [[ "$err" != *"not on allow-list"* && "$err" != *"mutating flag"* && "$err" != *"denied global flag"* ]]; then
  printf 'PASS  73c  git fetch reaches git (rc=%d)\n' "$rc"
  pass=$((pass + 1))
else
  printf 'FAIL  73c  git fetch blocked by bridge [rc=%d err=%s]\n' "$rc" "$err"
  fail=$((fail + 1))
  failures+=("73c git fetch reaches git :: rc=$rc err=$err")
fi

# --- 73d. git fetch --prune denied ---
STDERR_CONTAINS="mutating flag: --prune" \
run_case 73d "git fetch --prune denied" 5 -- "$CEREBRO_BIN" git "$REPO" fetch --prune

# --- 73e. git fast-export --export-marks denied ---
STDERR_CONTAINS="mutating flag: --export-marks" \
run_case 73e "git fast-export --export-marks denied" 5 -- \
  "$CEREBRO_BIN" git "$REPO" fast-export --export-marks=/tmp/marks --all

# --- 73f. git replace positional SET form denied (no --list) ---
STDERR_CONTAINS="positional arg without --list" \
run_case 73f "git replace SET form denied" 5 -- \
  "$CEREBRO_BIN" git "$REPO" replace HEAD HEAD~1

# --- 73g. git symbolic-ref --delete denied ---
STDERR_CONTAINS="mutating flag: --delete" \
run_case 73g "git symbolic-ref --delete denied" 5 -- \
  "$CEREBRO_BIN" git "$REPO" symbolic-ref --delete HEAD

# ========================================================================
# Category C coverage (74-83b): broadened gh allow-list (via PATH stub).
# ========================================================================

gh_happy 74 "gh workflow list dispatches" "workflow list" workflow list

STDERR_CONTAINS="not allow-listed" \
run_case 75 "gh workflow run denied" 4 -- "$CEREBRO_BIN" gh "$REPO" workflow run wf.yml

gh_happy 76 "gh secret list dispatches" "secret list" secret list

STDERR_CONTAINS="not allow-listed" \
run_case 77 "gh secret set denied" 4 -- "$CEREBRO_BIN" gh "$REPO" secret set NAME

gh_happy 78 "gh cache list dispatches" "cache list" cache list
gh_happy 79 "gh label list dispatches" "label list" label list
gh_happy 80 "gh codespace list dispatches" "codespace list" codespace list

# --- 80b. gh codespace ports (bare) dispatches ---
gh_happy 80b "gh codespace ports happy" "codespace ports" codespace ports

# --- 80c. gh codespace ports forward denied (nested mutating verb) ---
STDERR_CONTAINS="codespace ports" \
run_case 80c "gh codespace ports forward denied" 4 -- \
  "$CEREBRO_BIN" gh "$REPO" codespace ports forward 8080

# --- 80d. gh codespace ports visibility denied (nested mutating verb) ---
STDERR_CONTAINS="codespace ports" \
run_case 80d "gh codespace ports visibility denied" 4 -- \
  "$CEREBRO_BIN" gh "$REPO" codespace ports visibility 8080:private

# --- 80e. gh codespace ports -c <name> forward denied (flag-before-subcmd) ---
STDERR_CONTAINS="forward" \
run_case 80e "gh codespace ports -c name forward denied" 4 -- \
  "$CEREBRO_BIN" gh "$REPO" codespace ports -c some-name forward 8080:8080

# --- 80f. gh codespace ports --json visibility happy (visibility as JSON field) ---
gh_happy 80f "gh codespace ports --json visibility happy" \
  "codespace ports --json visibility" \
  codespace ports --json visibility

# --- 80g. gh codespace ports -c visibility forward denied (flag value skipped) ---
STDERR_CONTAINS="forward" \
run_case 80g "gh codespace ports -c visibility forward denied" 4 -- \
  "$CEREBRO_BIN" gh "$REPO" codespace ports -c visibility forward 8080:8080

# --- 80h. gh codespace ports --codespace=visibility happy (equals-form value) ---
gh_happy 80h "gh codespace ports --codespace=visibility happy" \
  "codespace ports --codespace=visibility" \
  codespace ports --codespace=visibility

# --- 80i. gh codespace ports --repo-owner <owner> forward denied (codex case) ---
STDERR_CONTAINS="forward" \
run_case 80i "gh codespace ports --repo-owner alice forward denied" 4 -- \
  "$CEREBRO_BIN" gh "$REPO" codespace ports --repo-owner alice forward 8080:8080

# --- 80j. gh codespace ports --repo-owner=alice happy (equals form is self-contained) ---
gh_happy 80j "gh codespace ports --repo-owner=alice happy" \
  "codespace ports --repo-owner=alice" \
  codespace ports --repo-owner=alice

# --- 80k. gh codespace ports --display-name <name> forward denied (audit-added flag) ---
STDERR_CONTAINS="forward" \
run_case 80k "gh codespace ports --display-name name forward denied" 4 -- \
  "$CEREBRO_BIN" gh "$REPO" codespace ports --display-name my-space forward 8080:8080

STDERR_CONTAINS="not allow-listed" \
run_case 81 "gh auth token denied" 4 -- "$CEREBRO_BIN" gh "$REPO" auth token

gh_happy 82 "gh config get editor dispatches" "config get editor" config get editor

STDERR_CONTAINS="runs arbitrary code" \
run_case 83 "gh extension install denied with reason" 4 -- "$CEREBRO_BIN" gh "$REPO" extension install owner/repo

STDERR_CONTAINS="side-effect" \
run_case 83b "gh browse top-level denied" 4 -- "$CEREBRO_BIN" gh "$REPO" browse

# ========================================================================
# Category D coverage (84-92): bare-abs read/grep/ls.
# ========================================================================

# Sandbox-local file outside any worktree.
printf 'hello\n' > "$WORKDIR/outside.txt"

# --- 84. read bare-abs file happy ---
out="$("$CEREBRO_BIN" read "$WORKDIR/outside.txt" 2>"$WORKDIR/stderr")"
rc=$?
if [[ $rc -eq 0 && "$out" == *"hello"* ]]; then
  printf 'PASS  84  read bare-abs file happy\n'
  pass=$((pass + 1))
else
  printf 'FAIL  84  read bare-abs file happy [rc=%d out=%s]\n' "$rc" "$out"
  fail=$((fail + 1))
  failures+=("84 read bare-abs file :: rc=$rc out=$out")
fi

# --- 85. read bare-abs file with --range ---
run_case 85 "read bare-abs --range" 0 -- "$CEREBRO_BIN" read "$WORKDIR/outside.txt" --range 1:1

# --- 86. read bare-abs special path: security refusal stays hard (exit 6) ---
STDERR_CONTAINS="special path" \
run_case 86 "read /dev/null denied (security)" 6 -- "$CEREBRO_BIN" read /dev/null

# --- 87. read bare-abs another special path: security refusal stays hard ---
STDERR_CONTAINS="special path" \
run_case 87 "read /dev/tty denied (under /dev/)" 6 -- "$CEREBRO_BIN" read /dev/tty

# --- 88. read bare-abs nonexistent: benign by default (exit-7 routing) ---
STDOUT_CONTAINS="(not found:" \
run_case 88 "read nonexistent bare-abs benign" 0 -- "$CEREBRO_BIN" read /no/such/path/xyz

# --- 88b. read bare-abs nonexistent --strict-missing restores exit 3 ---
STDERR_CONTAINS="not found" \
run_case 88b "read nonexistent bare-abs --strict-missing" 3 -- "$CEREBRO_BIN" read /no/such/path/xyz --strict-missing

# --- 89. grep bare-abs happy (sandbox dir) ---
if command -v rg >/dev/null 2>&1; then
  out="$("$CEREBRO_BIN" grep "$WORKDIR/lookups" findme 2>/dev/null)"
  rc=$?
  if [[ ( $rc -eq 0 || $rc -eq 1 ) && "$out" == *"needle.txt"*"findme"* ]]; then
    printf 'PASS  89  grep bare-abs happy\n'
    pass=$((pass + 1))
  else
    printf 'FAIL  89  grep bare-abs happy [rc=%d out=%s]\n' "$rc" "$out"
    fail=$((fail + 1))
    failures+=("89 grep bare-abs happy :: rc=$rc out=$out")
  fi
else
  printf 'SKIP  89  grep bare-abs happy (rg not installed)\n'
fi

# --- 90. grep bare-abs missing pattern ---
STDERR_CONTAINS="usage" \
run_case 90 "grep bare-abs missing pattern" 2 -- "$CEREBRO_BIN" grep "$WORKDIR/lookups"

# --- 91. ls bare-abs happy ---
out="$("$CEREBRO_BIN" ls "$WORKDIR/lookups" 2>/dev/null)"
rc=$?
if [[ $rc -eq 0 && "$out" == *"needle.txt"* ]]; then
  printf 'PASS  91  ls bare-abs lists needle.txt\n'
  pass=$((pass + 1))
else
  printf 'FAIL  91  ls bare-abs [rc=%d out=%s]\n' "$rc" "$out"
  fail=$((fail + 1))
  failures+=("91 ls bare-abs :: rc=$rc out=$out")
fi

# --- 92. ls bare-abs special path: security refusal stays hard (exit 6) ---
STDERR_CONTAINS="special path" \
run_case 92 "ls /dev denied (security)" 6 -- "$CEREBRO_BIN" ls /dev

# ========================================================================
# apply-review default-findings and staleness validation.
# These validation paths fire BEFORE any child spawn, so the error cases need
# no backend. The happy cases use a native Pi RPC fixture, so apply-review
# exercises the real transport and completes.
# ========================================================================

SESS_DIR="$CEREBRO_HOME/sessions/$CEREBRO_SESSION_ID"
RSTATE="$SESS_DIR/review-state"
CHILDREN="$SESS_DIR/children"
# Per-repo key the same way cerebro computes it: sha1 of the canonical
# worktree root, first 16 hex.
RKEY="$(git -C "$REPO" rev-parse --show-toplevel \
        | python3 -c 'import hashlib,sys; print(hashlib.sha1(sys.stdin.read().strip().encode()).hexdigest()[:16])')"
BRANCH="$(git -C "$REPO" rev-parse --abbrev-ref HEAD)"

# Native Pi fixture: one successful RPC session. The production adapter
# and parser are used by every successful child path below.
PI_STUB_DIR="$WORKDIR/pi-stub"
mkdir -p "$PI_STUB_DIR"
install_pi_fixture "$PI_STUB_DIR" '{}'
STUB_OK=0; [[ -x "$PI_STUB_DIR/pi" ]] && STUB_OK=1
STUB_PATH="$PI_STUB_DIR:$PATH"

seed_review_state() {  # $1 = last_findings path
  mkdir -p "$RSTATE"
  jq -n --arg repo "$(git -C "$REPO" rev-parse --show-toplevel)" \
        --arg branch "$BRANCH" --arg sha "$(git -C "$REPO" rev-parse HEAD)" \
        --arg findings "$1" --arg ts "2026-01-01T00:00:00Z" \
        '{repo:$repo, branch:$branch, last_reviewed_sha:$sha, last_findings:$findings, ts:$ts}' \
        > "$RSTATE/$RKEY.json"
}

# --- 93. apply-review with no findings defaults to last review's findings ---
if (( STUB_OK )); then
  printf 'review findings here\n' > "$CHILDREN/review-TEST.md"
  seed_review_state "$CHILDREN/review-TEST.md"
  STDERR_CONTAINS="defaulting to last review findings" \
  run_case 93 "apply-review defaults to last review findings" 0 -- \
    env PATH="$STUB_PATH" "$CEREBRO_BIN" apply-review "$REPO"
else
  printf 'SKIP  93  apply-review default findings (Pi fixture unavailable)\n'
fi

# --- 94. apply-review, no findings + no prior review -> clear error ---
rm -f "$RSTATE/$RKEY.json"
STDERR_CONTAINS="no prior review for this repo+branch" \
run_case 94 "apply-review no findings, no prior review errors" 1 -- \
  "$CEREBRO_BIN" apply-review "$REPO"

# --- 95. nonexistent explicit findings names the correct last-review path ---
seed_review_state "$CHILDREN/review-TEST.md"
STDERR_CONTAINS="the last review for this repo+branch is:" \
run_case 95 "apply-review bad explicit findings names latest" 1 -- \
  "$CEREBRO_BIN" apply-review "$REPO" /no/such/findings.md

# --- 96. stale (older) findings warns non-fatally but still applies ---
if (( STUB_OK )); then
  printf 'older findings\n' > "$WORKDIR/older.md"
  seed_review_state "$CHILDREN/review-TEST.md"   # newest != older.md
  STDERR_CONTAINS="not the latest review" \
  run_case 96 "apply-review stale findings warns, applies" 0 -- \
    env PATH="$STUB_PATH" "$CEREBRO_BIN" apply-review "$REPO" "$WORKDIR/older.md"
else
  printf 'SKIP  96  apply-review stale findings (Pi fixture unavailable)\n'
fi

# --- 99. regression: --notes with --prompt still rejected ---
STDERR_CONTAINS="only meaningful with a findings file" \
run_case 99 "apply-review --notes + --prompt still errors" 1 -- \
  "$CEREBRO_BIN" apply-review "$REPO" --prompt "x" --notes "y"

# --- 100. --prompt with NO operand is a usage error, never a findings fallback.
# Seed a valid last review so a buggy fallback WOULD succeed; the guard must
# still reject the empty --prompt rather than silently apply those findings.
printf 'seeded findings\n' > "$CHILDREN/review-TEST.md"
seed_review_state "$CHILDREN/review-TEST.md"
STDERR_CONTAINS="--prompt requires a non-empty value" \
run_case 100 "apply-review --prompt (no value) errors, no findings fallback" 1 -- \
  "$CEREBRO_BIN" apply-review "$REPO" --prompt

# --- 100b. --prompt "" (explicit empty operand) is likewise a usage error. ---
seed_review_state "$CHILDREN/review-TEST.md"
STDERR_CONTAINS="--prompt requires a non-empty value" \
run_case 100b "apply-review --prompt '' errors, no findings fallback" 1 -- \
  "$CEREBRO_BIN" apply-review "$REPO" --prompt ""

# --- 102. explicit-findings staleness check must NOT cross branches. ---
# Seed review state on the current branch naming review-TEST.md, then switch
# to a new branch and apply a DIFFERENT (older) findings file. The stored
# state belongs to the other branch, so cerebro must not name review-TEST.md
# as "latest for this repo+branch".
if (( STUB_OK )); then
  printf 'older findings\n' > "$WORKDIR/older2.md"
  seed_review_state "$CHILDREN/review-TEST.md"   # state recorded for $BRANCH
  git -C "$REPO" checkout -q -b other-branch
  out="$(env PATH="$STUB_PATH" "$CEREBRO_BIN" apply-review "$REPO" "$WORKDIR/older2.md" 2>"$WORKDIR/stderr")"
  rc=$?
  err="$(cat "$WORKDIR/stderr")"
  git -C "$REPO" checkout -q "$BRANCH"
  if [[ $rc -eq 0 && "$err" != *"not the latest review"* && "$err" != *"review-TEST.md"* ]]; then
    printf 'PASS  102  staleness naming does not cross branches\n'; pass=$((pass + 1))
  else
    printf 'FAIL  102  staleness check crossed branches [rc=%d err=%s]\n' "$rc" "$err"
    fail=$((fail + 1))
    failures+=("102 branch-cross staleness :: rc=$rc err=$err")
  fi
else
  printf 'SKIP  102  apply-review branch-switch staleness (Pi fixture unavailable)\n'
fi

# ========================================================================
# 103. Concurrent mutating runs must write to DISTINCT child-log files.
# After dropping the per-repo lock, two same-session mutating ops can start
# within the same second. A bare <subcmd>-<ts> child-log name would let both
# tee into ONE file -> truncated/interleaved logs and an ambiguous echoed
# path. The child-log name is now collision-resistant (PID + random token),
# so each run gets its own file. We launch two apply-review ops concurrently
# (a stub that sleeps to force overlapping writes, tagging each emitted line
# with a per-run token), then assert the two echoed paths differ and that
# neither log shows the other run's token (no interleave/truncation).
# ========================================================================
if (( STUB_OK )); then
  CONC_STUB_DIR="$WORKDIR/conc-stub"
  mkdir -p "$CONC_STUB_DIR"
  install_pi_fixture "$CONC_STUB_DIR" '{"mode":"concurrent","delay":0.4,"text_count":300}'
  CONC_PATH="$CONC_STUB_DIR:$PATH"

  env PATH="$CONC_PATH" "$CEREBRO_BIN" apply-review "$REPO" \
    --prompt "do work TOKEN=AAAA" >"$WORKDIR/conc1.out" 2>/dev/null &
  c1=$!
  env PATH="$CONC_PATH" "$CEREBRO_BIN" apply-review "$REPO" \
    --prompt "do work TOKEN=BBBB" >"$WORKDIR/conc2.out" 2>/dev/null &
  c2=$!
  wait "$c1"; r1=$?
  wait "$c2"; r2=$?

  # The echoed child-log path is the final stdout line of each run.
  clog1="$(tail -1 "$WORKDIR/conc1.out")"
  clog2="$(tail -1 "$WORKDIR/conc2.out")"

  conc_ok=1; conc_why=""
  if (( r1 != 0 || r2 != 0 )); then
    conc_ok=0; conc_why="nonzero rc (r1=$r1 r2=$r2)"
  fi
  if [[ -z "$clog1" || -z "$clog2" || "$clog1" == "$clog2" ]]; then
    conc_ok=0; conc_why="${conc_why:+$conc_why; }child logs not distinct: '$clog1' vs '$clog2'"
  fi
  if [[ ! -f "$clog1" || ! -f "$clog2" ]]; then
    conc_ok=0; conc_why="${conc_why:+$conc_why; }child log file(s) missing"
  else
    a1="$(jq -s '[.[] | select(.type == "message_update" and .assistantMessageEvent.delta == "AAAA")] | length' "$clog1")"
    b1="$(grep -c 'BBBB' "$clog1")"
    b2="$(jq -s '[.[] | select(.type == "message_update" and .assistantMessageEvent.delta == "BBBB")] | length' "$clog2")"
    a2="$(grep -c 'AAAA' "$clog2")"
    if (( a1 != 300 || b1 != 0 || b2 != 300 || a2 != 0 )); then
      conc_ok=0
      conc_why="${conc_why:+$conc_why; }interleave/truncation (A1=$a1 B1=$b1 A2=$a2 B2=$b2)"
    fi
  fi

  if (( conc_ok )); then
    printf 'PASS  103  concurrent mutating runs use distinct child logs\n'
    pass=$((pass + 1))
  else
    printf 'FAIL  103  concurrent mutating runs collided [%s]\n' "$conc_why"
    fail=$((fail + 1))
    failures+=("103 concurrent child-log collision :: $conc_why")
  fi
else
  printf 'SKIP  103  concurrent child-log distinctness (Pi fixture unavailable)\n'
fi

# ========================================================================
# 104-110. Preference learning: learn-note (pending journal), learn-set
# (active learnings, size-capped), and learnings (inspection). These files
# are global under $CEREBRO_HOME and persist across sessions.
# ========================================================================
LEARN_ACTIVE="$CEREBRO_HOME/learnings.md"
LEARN_PENDING="$CEREBRO_HOME/pending-learnings.md"

# --- 104. learnings on a clean home reports none ---
STDOUT_CONTAINS="(none yet)" \
run_case 104 "learnings empty reports none" 0 -- "$CEREBRO_BIN" learnings

# --- 105. learn-note appends to the pending journal ---
run_case 105 "learn-note records a signal" 0 -- \
  "$CEREBRO_BIN" learn-note "user repeatedly asks to simplify"
if [[ -s "$LEARN_PENDING" ]] && grep -q "user repeatedly asks to simplify" "$LEARN_PENDING"; then
  printf 'PASS  105b  learn-note wrote pending journal\n'; pass=$((pass + 1))
else
  printf 'FAIL  105b  learn-note did not write pending journal\n'; fail=$((fail + 1))
  failures+=("105b learn-note pending journal missing entry")
fi

# --- 106. learn-note with blank text errors ---
STDERR_CONTAINS="usage: cerebro learn-note" \
run_case 106 "learn-note blank errors" 1 -- "$CEREBRO_BIN" learn-note "   "

# --- 107. learn-set writes the active learnings ---
run_case 107 "learn-set writes active learnings" 0 -- \
  "$CEREBRO_BIN" learn-set "- Keep diffs small; avoid over-engineering."
if [[ -s "$LEARN_ACTIVE" ]] && grep -q "avoid over-engineering" "$LEARN_ACTIVE"; then
  printf 'PASS  107b  learn-set wrote active learnings\n'; pass=$((pass + 1))
else
  printf 'FAIL  107b  learn-set did not write active learnings\n'; fail=$((fail + 1))
  failures+=("107b learn-set active learnings missing")
fi

# --- 108. learnings now shows the active set ---
STDOUT_CONTAINS="avoid over-engineering" \
run_case 108 "learnings shows active set" 0 -- "$CEREBRO_BIN" learnings

# --- 109. learn-set rejects oversized payloads (system-message budget) ---
BIG="$(head -c 1700 < /dev/zero | tr '\0' 'x')"
STDERR_CONTAINS="too large" \
run_case 109 "learn-set oversized rejected" 1 -- "$CEREBRO_BIN" learn-set "$BIG"
# The prior (valid) active learnings must survive a rejected overwrite.
if grep -q "avoid over-engineering" "$LEARN_ACTIVE"; then
  printf 'PASS  109b  rejected learn-set left active learnings intact\n'; pass=$((pass + 1))
else
  printf 'FAIL  109b  rejected learn-set clobbered active learnings\n'; fail=$((fail + 1))
  failures+=("109b oversized learn-set clobbered active learnings")
fi

# --- 110. learn-set with blank text errors ---
STDERR_CONTAINS="usage: cerebro learn-set" \
run_case 110 "learn-set blank errors" 1 -- "$CEREBRO_BIN" learn-set ""

# --- 110a. learn-set --stdin records body verbatim (backticks/dollar signs) ---
LSSESS="lsess"
LSHOME="$WORKDIR/learn-home"
initialize_session "$LSHOME/sessions/$LSSESS"
env CEREBRO_HOME="$LSHOME" CEREBRO_SESSION_ID="$LSSESS" \
  "$CEREBRO_BIN" learn-set --stdin >/dev/null 2>&1 <<'STDIN_EOF'
- Keep diffs small; use `$VAR` literally.
- Prefer `backticks` and $dollar signs verbatim.
STDIN_EOF
LSFILE="$LSHOME/learnings.md"
if grep -q 'Keep diffs small' "$LSFILE" \
   && grep -q '`backticks`' "$LSFILE" \
   && grep -q '\$dollar signs' "$LSFILE"; then
  printf 'PASS  110a learn-set --stdin records body verbatim\n'; pass=$((pass + 1))
else
  printf 'FAIL  110a learn-set --stdin verbatim [file=%s]\n' \
    "$(cat "$LSFILE" 2>/dev/null)"; fail=$((fail + 1))
  failures+=("110a learn-set --stdin verbatim")
fi

# --- 110b. learn-set --stdin plus inline body is ambiguous ---
lserr="$(env CEREBRO_HOME="$LSHOME" CEREBRO_SESSION_ID="$LSSESS" \
  "$CEREBRO_BIN" learn-set "inline" --stdin 2>&1 <<'STDIN_EOF'
stdin body
STDIN_EOF
)"
if [[ "$lserr" == *"mutually exclusive"* ]]; then
  printf 'PASS  110b learn-set --stdin + inline rejected\n'; pass=$((pass + 1))
else
  printf 'FAIL  110b learn-set --stdin + inline not rejected [err=%s]\n' "$lserr"; fail=$((fail + 1))
  failures+=("110b learn-set --stdin ambiguous")
fi

# --- 110c. learn-set --stdin with empty body errors ---
lserr="$(env CEREBRO_HOME="$LSHOME" CEREBRO_SESSION_ID="$LSSESS" \
  "$CEREBRO_BIN" learn-set --stdin 2>&1 < /dev/null)"
if [[ "$lserr" == *"usage: cerebro learn-set"* ]]; then
  printf 'PASS  110c learn-set --stdin empty rejected\n'; pass=$((pass + 1))
else
  printf 'FAIL  110c learn-set --stdin empty not rejected [err=%s]\n' "$lserr"; fail=$((fail + 1))
  failures+=("110c learn-set --stdin empty")
fi

# --- 111. execute: unknown arg still rejected (stacked-branch flags added) ---
STDERR_CONTAINS="unknown arg" \
run_case 111 "execute unknown arg rejected" 1 -- "$CEREBRO_BIN" execute "$REPO" --frob

# --- 112. execute: --base/--branch without a plan or --prompt still errors ---
# Confirms the new flags parse but don't bypass the plan/prompt requirement,
# and fire before any child Pi session is spawned.
STDERR_CONTAINS="requires <plan-path> or --prompt" \
run_case 112 "execute --base/--branch needs plan or prompt" 1 -- \
  "$CEREBRO_BIN" execute "$REPO" --base feat/step-1 --branch feat/step-2

# --- 112c. verify: missing repo arg rejected ---
STDERR_CONTAINS="usage: cerebro verify" \
run_case 112c "verify no repo arg rejected" 1 -- "$CEREBRO_BIN" verify

# --- 112d. verify: relative repo path rejected ---
STDERR_CONTAINS="must be absolute" \
run_case 112d "verify relative repo rejected" 1 -- \
  "$CEREBRO_BIN" verify relative --prompt "x"

# --- 112e. verify: neither --plan nor --prompt rejected ---
STDERR_CONTAINS="requires --plan <path> or --prompt" \
run_case 112e "verify needs plan or prompt" 1 -- "$CEREBRO_BIN" verify "$REPO"

# --- 112f. verify: both --plan and --prompt rejected ---
STDERR_CONTAINS="not both" \
run_case 112f "verify plan+prompt rejected" 1 -- \
  "$CEREBRO_BIN" verify "$REPO" --plan "$REPO/README.md" --prompt "x"

# --- 112g. verify: unknown arg rejected ---
STDERR_CONTAINS="unknown arg" \
run_case 112g "verify unknown arg rejected" 1 -- \
  "$CEREBRO_BIN" verify "$REPO" --prompt "x" --bogus

# --- 112h. the verifier selector is the shared role label ---
vname="$(bash -c '
  set -uo pipefail
  CEREBRO_LIB_DIR="$1"; shift
  . "$CEREBRO_LIB_DIR/config.sh"
  . "$CEREBRO_LIB_DIR/helpers.sh"
  . "$CEREBRO_LIB_DIR/backend.sh"
  backend_child_agent_name "$1"' _ "$here/../lib" verify 2>/dev/null)"
if [[ "$vname" == "verify" ]]; then
  printf 'PASS  112h backend_child_agent_name verify -> verify\n'; pass=$((pass + 1))
else
  printf 'FAIL  112h agent name wrong [%s]\n' "$vname"; fail=$((fail + 1))
  failures+=("112h verify agent name :: $vname")
fi

# --- 112i. backend_answerable_pattern verify echoes pi:verify ---
vprov="$(bash -c '
  set -uo pipefail
  CEREBRO_LIB_DIR="$1"; shift
  . "$CEREBRO_LIB_DIR/config.sh"
  . "$CEREBRO_LIB_DIR/helpers.sh"
  . "$CEREBRO_LIB_DIR/backend.sh"
  backend_answerable_pattern "$1"' _ "$here/../lib" verify 2>/dev/null)"
if [[ "$vprov" == "pi:verify" ]]; then
  printf 'PASS  112i backend_answerable_pattern verify -> pi:verify\n'; pass=$((pass + 1))
else
  printf 'FAIL  112i answerable provider wrong [%s]\n' "$vprov"; fail=$((fail + 1))
  failures+=("112i verify provider :: $vprov")
fi

# --- 112j. the verifier has writable native tools and the shared role skill.
vbody="$(bash -c '
  CEREBRO_LIB_DIR="$1"
  . "$CEREBRO_LIB_DIR/config.sh"
  . "$CEREBRO_LIB_DIR/helpers.sh"
  . "$CEREBRO_LIB_DIR/payloads.sh"
  child_sys_prompt verify' _ "$here/../lib" 2>/dev/null)"
if python3 "$here/backend_contract_test.py" verify \
   && [[ "$vbody" == *"engineering skill"* && "$vbody" == *"production-equivalent"* && "$vbody" == *"non-interactive"* ]]; then
  printf 'PASS  112j verifier native permissions and shared skill carry the verification contract\n'; pass=$((pass + 1))
else
  printf 'FAIL  112j verifier policy/skill contract missing\n'; fail=$((fail + 1))
  failures+=("112j verifier native policy/skill")
fi

# --- 112k. verify: relative --plan path rejected (must be absolute) ---
STDERR_CONTAINS="plan path must be absolute" \
run_case 112k "verify relative plan rejected" 1 -- \
  "$CEREBRO_BIN" verify "$REPO" --plan relative.md

# --- 113. review: --criteria-file missing path fails fast (before the reviewer) ---
STDERR_CONTAINS="cannot read --criteria-file" \
run_case 113 "review --criteria-file missing path" 1 -- \
  "$CEREBRO_BIN" review "$REPO" --criteria-file "$WORKDIR/no-such-plan.md"

# --- 113b. review: --criteria-file empty file also fails fast ---
: > "$WORKDIR/empty-plan.md"
STDERR_CONTAINS="cannot read --criteria-file" \
run_case 113b "review --criteria-file empty file" 1 -- \
  "$CEREBRO_BIN" review "$REPO" --criteria-file "$WORKDIR/empty-plan.md"

# --- 114. review: unknown arg rejected ---
STDERR_CONTAINS="unknown arg" \
run_case 114 "review unknown arg rejected" 1 -- "$CEREBRO_BIN" review "$REPO" --frob

# ========================================================================
# 115-122. Session spec: the requirements of record. `spec set` replaces the
# current spec and archives every version to an append-only history;
# `spec` / `spec history` read them back. These are per-session files that
# survive a context compaction.
# ========================================================================
SPEC_FILE="$CEREBRO_HOME/sessions/$CEREBRO_SESSION_ID/spec.md"
SPEC_HIST="$CEREBRO_HOME/sessions/$CEREBRO_SESSION_ID/spec-history.jsonl"

# --- 115. spec on a fresh session reports none ---
STDOUT_CONTAINS="no session spec recorded yet" \
run_case 115 "spec empty reports none" 0 -- "$CEREBRO_BIN" spec

# --- 116. spec set records the current spec ---
run_case 116 "spec set records spec" 0 -- \
  "$CEREBRO_BIN" spec set "Build a widget that does X under constraint Y."
if [[ -s "$SPEC_FILE" ]] && grep -q "constraint Y" "$SPEC_FILE"; then
  printf 'PASS  116b  spec set wrote spec.md\n'; pass=$((pass + 1))
else
  printf 'FAIL  116b  spec set did not write spec.md\n'; fail=$((fail + 1))
  failures+=("116b spec set spec.md missing")
fi
if [[ -s "$SPEC_HIST" ]] && grep -q "constraint Y" "$SPEC_HIST"; then
  printf 'PASS  116c  spec set appended to history\n'; pass=$((pass + 1))
else
  printf 'FAIL  116c  spec set did not append to history\n'; fail=$((fail + 1))
  failures+=("116c spec set history missing entry")
fi

# --- 117. spec prints the current spec ---
STDOUT_CONTAINS="constraint Y" \
run_case 117 "spec prints current spec" 0 -- "$CEREBRO_BIN" spec

# --- 118. spec set again overrides current but keeps history ---
run_case 118 "spec set overrides current" 0 -- \
  "$CEREBRO_BIN" spec set "Revised: build a gadget that does Z."
# Current spec is the newest text only...
if grep -q "gadget that does Z" "$SPEC_FILE" && ! grep -q "constraint Y" "$SPEC_FILE"; then
  printf 'PASS  118b  spec.md holds only the latest version\n'; pass=$((pass + 1))
else
  printf 'FAIL  118b  spec.md did not override cleanly\n'; fail=$((fail + 1))
  failures+=("118b spec.md override failed")
fi
# ...but history retains BOTH versions.
hist_lines="$(grep -c '' "$SPEC_HIST" 2>/dev/null || printf 0)"
if [[ "$hist_lines" -eq 2 ]] && grep -q "constraint Y" "$SPEC_HIST" && grep -q "gadget that does Z" "$SPEC_HIST"; then
  printf 'PASS  118c  history retains all versions\n'; pass=$((pass + 1))
else
  printf 'FAIL  118c  history lost a version (lines=%s)\n' "$hist_lines"; fail=$((fail + 1))
  failures+=("118c spec history lost a version")
fi

# --- 119. spec footer reports the history count ---
STDOUT_CONTAINS="2 version(s) recorded" \
run_case 119 "spec reports history count" 0 -- "$CEREBRO_BIN" spec

# --- 120. spec history prints every version oldest first ---
STDOUT_CONTAINS="2 version(s) total" \
run_case 120 "spec history prints all versions" 0 -- "$CEREBRO_BIN" spec history

# --- 121. spec set with blank text errors ---
STDERR_CONTAINS="usage: cerebro spec set" \
run_case 121 "spec set blank errors" 1 -- "$CEREBRO_BIN" spec set "   "

# --- 121b. spec with an unknown action errors ---
STDERR_CONTAINS="usage: cerebro spec" \
run_case 121b "spec unknown action errors" 1 -- "$CEREBRO_BIN" spec frobnicate

# --- 122. status surfaces the recorded spec ---
STDOUT_CONTAINS="session spec: present" \
run_case 122 "status shows session spec" 0 -- "$CEREBRO_BIN" status

# --- 124. requirement revisions preserve the current contract and its history.
GSESS="requirements-session"
GDIR="$CEREBRO_HOME/sessions/$GSESS"
initialize_session "$GDIR"
env CEREBRO_SESSION_ID="$GSESS" "$CEREBRO_BIN" spec set "Task A: first task" \
  >/dev/null 2>"$WORKDIR/stderr"
env CEREBRO_SESSION_ID="$GSESS" "$CEREBRO_BIN" spec set "Task B: a different task" \
  >/dev/null 2>"$WORKDIR/stderr"

# --- 124b. both versions are archived and the current requirement is updated ---
if grep -q "Task B: a different task" "$GDIR/spec.md" \
   && [[ "$(grep -c '' "$GDIR/spec-history.jsonl" 2>/dev/null || printf 0)" -eq 2 ]]; then
  printf 'PASS  124b  replace still recorded spec + history\n'; pass=$((pass + 1))
else
  printf 'FAIL  124b  replace did not record cleanly\n'; fail=$((fail + 1))
  failures+=("124b replace record")
fi

# --- 124c. spec set --stdin records body verbatim (backticks/dollar signs) ---
SSDIR="$WORKDIR/ssess-home"
mkdir -p "$SSDIR/sessions"
SSSESS="spec-stdin-sess"
initialize_session "$SSDIR/sessions/$SSSESS"
env CEREBRO_HOME="$SSDIR" CEREBRO_SESSION_ID="$SSSESS" \
  "$CEREBRO_BIN" spec set --stdin >/dev/null 2>&1 <<'STDIN_EOF'
# Spec via stdin
Contains `backticks` and $dollar signs.
Multi-line body.
STDIN_EOF
if grep -q '# Spec via stdin' "$SSDIR/sessions/$SSSESS/spec.md" \
   && grep -q '`backticks`' "$SSDIR/sessions/$SSSESS/spec.md" \
   && grep -q '\$dollar signs' "$SSDIR/sessions/$SSSESS/spec.md" \
   && grep -q 'Multi-line body.' "$SSDIR/sessions/$SSSESS/spec.md"; then
  printf 'PASS  124c spec set --stdin records body verbatim\n'; pass=$((pass + 1))
else
  printf 'FAIL  124c spec set --stdin verbatim [file=%s]\n' \
    "$(cat "$SSDIR/sessions/$SSSESS/spec.md" 2>/dev/null)"; fail=$((fail + 1))
  failures+=("124c spec set --stdin verbatim")
fi

# --- 124d. spec set --stdin plus inline body is ambiguous ---
serr="$(env CEREBRO_HOME="$SSDIR" CEREBRO_SESSION_ID="$SSSESS" \
  "$CEREBRO_BIN" spec set "inline" --stdin 2>&1 <<'STDIN_EOF'
stdin body
STDIN_EOF
)"
if [[ "$serr" == *"mutually exclusive"* ]]; then
  printf 'PASS  124d spec set --stdin + inline rejected\n'; pass=$((pass + 1))
else
  printf 'FAIL  124d spec set --stdin + inline not rejected [err=%s]\n' "$serr"; fail=$((fail + 1))
  failures+=("124d spec set --stdin ambiguous")
fi

# --- 124e. spec set --stdin with empty body errors ---
serr="$(env CEREBRO_HOME="$SSDIR" CEREBRO_SESSION_ID="$SSSESS" \
  "$CEREBRO_BIN" spec set --stdin 2>&1 < /dev/null)"
if [[ "$serr" == *"usage: cerebro spec set"* ]]; then
  printf 'PASS  124e spec set --stdin empty rejected\n'; pass=$((pass + 1))
else
  printf 'FAIL  124e spec set --stdin empty not rejected [err=%s]\n' "$serr"; fail=$((fail + 1))
  failures+=("124e spec set --stdin empty")
fi

# ========================================================================
# 125-128. Child agent session persistence. The native fixture assigns its
# session ID; Cerebro stores it under child-sessions.json, does not reuse
# completed child sessions, and resumes only entries left in status=running.
# ========================================================================
if (( STUB_OK )); then
  # Native state reports a durable session path after the user prompt persists.
  ID_STUB_DIR="$WORKDIR/pi-id-stub"
  mkdir -p "$ID_STUB_DIR"
  install_pi_fixture "$ID_STUB_DIR" '{}'
  ID_STUB_PATH="$ID_STUB_DIR:$PATH"

  ESESS="exec-session"; EDIR="$CEREBRO_HOME/sessions/$ESESS"
  initialize_session "$EDIR"
  EPLAN1="$EDIR/plans/plan-one.md"
  EPLAN2="$EDIR/plans/plan-two.md"
  printf 'plan one\n' > "$EPLAN1"
  printf 'plan two\n' > "$EPLAN2"

  # --- 125. execute with --branch captures the child session id ---
  env PATH="$ID_STUB_PATH" CEREBRO_SESSION_ID="$ESESS" \
    "$CEREBRO_BIN" execute "$REPO" "$EPLAN1" --branch feat/test \
    >/dev/null 2>&1
  exec_id="$(jq -r '.[].id' "$EDIR/child-sessions.json" 2>/dev/null)"
  if [[ "$exec_id" == /* && -s "$exec_id" ]] \
     && head -1 "$exec_id" | jq -e '.type == "session" and .version == 3 and (.id | type) == "string"' >/dev/null; then
    printf 'PASS  125  execute --branch records child session id\n'; pass=$((pass + 1))
  else
    printf 'FAIL  125  execute did not record child id [got=%s]\n' "$exec_id"; fail=$((fail + 1))
    failures+=("125 execute capture :: got=$exec_id")
  fi

  # --- 125b. the first execute logged resume=none (no prior session) ---
  if grep -q 'resume=none' "$EDIR/transcript.jsonl"; then
    printf 'PASS  125b  first execute logged resume=none\n'; pass=$((pass + 1))
  else
    printf 'FAIL  125b  first execute did not log resume=none\n'; fail=$((fail + 1))
    failures+=("125b resume=none missing")
  fi

  # --- 126. a second plan on the same repo+branch does not resume plan one's id ---
  env PATH="$ID_STUB_PATH" CEREBRO_SESSION_ID="$ESESS" \
    "$CEREBRO_BIN" execute "$REPO" "$EPLAN2" --branch feat/test \
    >/dev/null 2>&1
  exec_entries="$(jq 'length' "$EDIR/child-sessions.json" 2>/dev/null)"
  if [[ "$exec_entries" -eq 2 ]] && ! grep -qF "resume=$exec_id" "$EDIR/transcript.jsonl" \
     && jq -e '[.[].id] | unique | length == 2' "$EDIR/child-sessions.json" >/dev/null; then
    printf 'PASS  126  same-branch second plan starts its own child session\n'; pass=$((pass + 1))
  else
    printf 'FAIL  126  same-branch plan reused a child [entries=%s transcript=%s]\n' \
      "$exec_entries" "$(cat "$EDIR/transcript.jsonl")"; fail=$((fail + 1))
    failures+=("126 same-branch plan isolation")
  fi

  # --- 126b. re-running a completed execute key also starts fresh. ---
  env PATH="$ID_STUB_PATH" CEREBRO_SESSION_ID="$ESESS" \
    "$CEREBRO_BIN" execute "$REPO" "$EPLAN1" --branch feat/test \
    >/dev/null 2>&1
  if ! grep -qF "resume=$exec_id" "$EDIR/transcript.jsonl"; then
    printf 'PASS  126b completed execute child is not auto-resumed\n'; pass=$((pass + 1))
  else
    printf 'FAIL  126b completed execute child was resumed [transcript=%s]\n' \
      "$(cat "$EDIR/transcript.jsonl")"; fail=$((fail + 1))
    failures+=("126b completed execute auto-resume")
  fi

  # --- 126c. distinct --base/--branch specifies the task's source and branch
  # name, with the PR target conditional on delivery authority. ---
  PROMPT_STUB_DIR="$WORKDIR/pi-prompt-stub"
  mkdir -p "$PROMPT_STUB_DIR"
  install_pi_fixture "$PROMPT_STUB_DIR" '{}'
  PROMPT_STUB_PATH="$PROMPT_STUB_DIR:$PATH"
  PROMPT_CAPTURE="$WORKDIR/stacked-prompt.txt"
  git -C "$REPO" branch feat/slug-01 main
  env PATH="$PROMPT_STUB_PATH" CEREBRO_SESSION_ID="$ESESS" \
    PROMPT_CAPTURE="$PROMPT_CAPTURE" \
    "$CEREBRO_BIN" execute "$REPO" --prompt "stack on plan one" \
      --base feat/slug-01 --branch feat/slug-02 >/dev/null 2>&1
  erc=$?
  eprompt="$(cat "$PROMPT_CAPTURE" 2>/dev/null || true)"
  if [[ $erc -eq 0 \
        && "$eprompt" == *"Requested starting reference: feat/slug-01"* \
        && "$eprompt" == *"Selected branch: feat/slug-02"* \
        && "$eprompt" == *"PR targeting comes from the task delivery instructions"* ]]; then
    printf 'PASS  126c task branch source, name and authorized PR target\n'; pass=$((pass + 1))
  else
    printf 'FAIL  126c task branch constraints wrong [rc=%d prompt=%s]\n' \
      "$erc" "$eprompt"; fail=$((fail + 1))
    failures+=("126c task branch constraints :: rc=$erc")
  fi

  # --- 129. a rejected stored session fails without restarting mutating work.
  REJECT_STUB_DIR="$WORKDIR/pi-reject-stub"
  install_pi_fixture "$REJECT_STUB_DIR" '{"mode":"reject"}'
  REJECT_STUB_PATH="$REJECT_STUB_DIR:$PATH"
  FSESS="rejected-session"; FDIR="$CEREBRO_HOME/sessions/$FSESS"
  initialize_session "$FDIR"
  FKEY="$(printf '%s\0execute\0branch:feat/test|prompt:go' "$REPO" | shasum | cut -d' ' -f1 | cut -c1-16)"
  MISSING_PI_SESSION="$REJECT_STUB_DIR/missing-session.jsonl"
  SELECTED_WORKSPACE="$(python3 "$here/../lib/python/task_workspace.py" prepare "$REPO" "" feat/test "" "$FDIR/child-sessions.json" "$FKEY" 0)"
  jq -n --argjson workspace "$SELECTED_WORKSPACE" --arg k "$FKEY" --arg repo "$REPO" --arg id "$MISSING_PI_SESSION" --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
     '{($k): {id:$id, provider:"pi", role:"execute", repo:$repo,
              branch:"feat/test", status:"running", updated_at:$ts, workspace:$workspace}}' \
     > "$FDIR/child-sessions.json"
  env PATH="$REJECT_STUB_PATH" CEREBRO_SESSION_ID="$FSESS" \
    "$CEREBRO_BIN" execute "$REPO" --prompt "go" --branch feat/test >/dev/null 2>&1
  frc=$?
  rejected_id="$(jq -r --arg k "$FKEY" '.[$k].id' "$FDIR/child-sessions.json" 2>/dev/null)"
  if [[ $frc -ne 0 && "$rejected_id" == "$MISSING_PI_SESSION" && ! -e "$MISSING_PI_SESSION" ]]; then
    printf 'PASS  129  rejected resume surfaces failure and preserves the stored session\n'; pass=$((pass + 1))
  else
    printf 'FAIL  129  rejected resume restarted work [rc=%d id=%s]\n' "$frc" "$rejected_id"; fail=$((fail + 1))
    failures+=("129 rejected resume :: rc=$frc id=$rejected_id")
  fi

  # --- 130. a native resume that performs work and then fails must not be
  # replayed as a fresh execution. Its existing ID stays resumable.
  WORK_COUNT="$WORKDIR/realfail-count"
  WORK_STUB_DIR="$WORKDIR/pi-realfail-stub"
  mkdir -p "$WORK_STUB_DIR"
  install_pi_fixture "$WORK_STUB_DIR" \
    "$(jq -n --arg count "$WORK_COUNT" '{mode:"worked-failure",count_file:$count}')"
  WORK_STUB_PATH="$WORK_STUB_DIR:$PATH"
  export WORK_COUNT

  WSESS="realfail-session"; WDIR="$CEREBRO_HOME/sessions/$WSESS"
  initialize_session "$WDIR"
  # Seed a fresh running stored id so resume is attempted.
  WKEY="$(printf '%s\0execute\0branch:feat/test|prompt:go' "$REPO" | shasum | cut -d' ' -f1 | cut -c1-16)"
  PRIOR_PI_SESSION="$WORK_STUB_DIR/prior-session.jsonl"; seed_pi_session "$PRIOR_PI_SESSION"
  SELECTED_WORKSPACE="$(python3 "$here/../lib/python/task_workspace.py" prepare "$REPO" "" feat/test "" "$WDIR/child-sessions.json" "$WKEY" 0)"
  jq -n --argjson workspace "$SELECTED_WORKSPACE" --arg k "$WKEY" --arg repo "$REPO" --arg id "$PRIOR_PI_SESSION" --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
     '{($k): {id:$id, provider:"pi", role:"execute", repo:$repo,
              branch:"feat/test", status:"running", updated_at:$ts, workspace:$workspace}}' \
     > "$WDIR/child-sessions.json"
  : > "$WORK_COUNT"
  env PATH="$WORK_STUB_PATH" CEREBRO_SESSION_ID="$WSESS" \
    "$CEREBRO_BIN" execute "$REPO" --prompt "go" --branch feat/test >/dev/null 2>&1
  wrc=$?
  invocations="$(wc -c < "$WORK_COUNT" | tr -d ' ')"
  stored_id="$(jq -r --arg k "$WKEY" '.[$k].id' "$WDIR/child-sessions.json" 2>/dev/null)"
  stored_status="$(jq -r --arg k "$WKEY" '.[$k].status' "$WDIR/child-sessions.json" 2>/dev/null)"
  # Exactly one prompt reached the native session; no fresh execution was
  # created after its failed terminal event.
  if [[ $wrc -ne 0 && "$invocations" -eq 1 && "$stored_id" == "$PRIOR_PI_SESSION" \
        && "$stored_status" == "running" ]] \
     && ! grep -q '"what":"execute_resume_failed"' "$WDIR/transcript.jsonl"; then
    printf 'PASS  130  resumed execute with prior work does not re-run fresh (stays resumable)\n'; pass=$((pass + 1))
  else
    printf 'FAIL  130  resumed real failure re-ran fresh [rc=%d invocations=%s id=%s status=%s]\n' \
      "$wrc" "$invocations" "$stored_id" "$stored_status"; fail=$((fail + 1))
    failures+=("130 mutating resume re-run :: rc=$wrc invocations=$invocations id=$stored_id status=$stored_status")
  fi

  # --- 145. execute runs the child in an isolated worktree: it announces the
  # worktree path, the worktree persists after the run, and the user's MAIN
  # checkout (including a pre-existing uncommitted file) is left untouched. ---
  WTSESS="wt-session"; WTDIR="$CEREBRO_HOME/sessions/$WTSESS"
  initialize_session "$WTDIR"
  printf 'precious local work\n' > "$REPO/MY-UNCOMMITTED.txt"
  main_head_before="$(git -C "$REPO" rev-parse HEAD)"
  main_branch_before="$(git -C "$REPO" rev-parse --abbrev-ref HEAD)"
  wtout="$(env PATH="$ID_STUB_PATH" CEREBRO_SESSION_ID="$WTSESS" \
    "$CEREBRO_BIN" execute "$REPO" --prompt "do work in a worktree" --worktree 2>/dev/null)"
  wt145="$(printf '%s\n' "$wtout" | sed -n 's/^=== TASK WORKTREE: \(.*\) (branch .*$/\1/p' | head -1)"
  main_head_after="$(git -C "$REPO" rev-parse HEAD)"
  main_branch_after="$(git -C "$REPO" rev-parse --abbrev-ref HEAD)"
  if [[ -n "$wt145" && -d "$wt145" && "$wt145" == "$CEREBRO_HOME/worktrees/"* ]] \
     && git -C "$wt145" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
     && [[ -f "$REPO/MY-UNCOMMITTED.txt" \
           && "$(cat "$REPO/MY-UNCOMMITTED.txt")" == "precious local work" \
           && "$main_head_after" == "$main_head_before" \
           && "$main_branch_after" == "$main_branch_before" ]]; then
    printf 'PASS  145  execute runs in a persistent worktree; main checkout untouched\n'; pass=$((pass + 1))
  else
    printf 'FAIL  145  worktree isolation wrong [wt=%s exists=%d main_uncommitted=%d head=%s/%s]\n' \
      "$wt145" "$([[ -d "$wt145" ]] && echo 1 || echo 0)" \
      "$([[ -f "$REPO/MY-UNCOMMITTED.txt" ]] && echo 1 || echo 0)" \
      "$main_head_before" "$main_head_after"; fail=$((fail + 1))
    failures+=("145 worktree isolation :: wt=$wt145")
  fi
  rm -f "$REPO/MY-UNCOMMITTED.txt"

  # --- 146. a follow-up addressed by the WORKTREE path reuses it: apply-review
  # with <wt> as <repo> commits on that worktree's branch, in that same
  # worktree, and creates NO new worktree. ---
  COMMIT_STUB_DIR="$WORKDIR/pi-commit-stub"
  mkdir -p "$COMMIT_STUB_DIR"
  COMMIT_HOOK="$COMMIT_STUB_DIR/commit.sh"
  cat > "$COMMIT_HOOK" <<'EOF'
printf 'applied by follow-up\n' >> applied.txt
git add applied.txt
git commit -q -m "stub follow-up commit"
EOF
  install_pi_fixture "$COMMIT_STUB_DIR" \
    "$(jq -n --arg hook "$COMMIT_HOOK" '{hook:$hook}')"
  COMMIT_STUB_PATH="$COMMIT_STUB_DIR:$PATH"
  FUSESS="followup-session"; FUDIR="$CEREBRO_HOME/sessions/$FUSESS"
  initialize_session "$FUDIR"
  WT_FU="$CEREBRO_HOME/worktrees/fu-reuse-test"
  git -C "$REPO" worktree add -q -b feat/fu-reuse "$WT_FU" main >/dev/null 2>&1
  fu_head_before="$(git -C "$WT_FU" rev-parse HEAD)"
  wt_count_before="$(find "$CEREBRO_HOME/worktrees" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ')"
  env PATH="$COMMIT_STUB_PATH" CEREBRO_SESSION_ID="$FUSESS" \
    "$CEREBRO_BIN" apply-review "$WT_FU" --prompt "apply the fix" >/dev/null 2>&1
  fu_rc=$?
  fu_head_after="$(git -C "$WT_FU" rev-parse HEAD)"
  fu_branch_after="$(git -C "$WT_FU" rev-parse --abbrev-ref HEAD)"
  wt_count_after="$(find "$CEREBRO_HOME/worktrees" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ')"
  if [[ $fu_rc -eq 0 && "$fu_branch_after" == "feat/fu-reuse" \
        && "$fu_head_after" != "$fu_head_before" \
        && -f "$WT_FU/applied.txt" \
        && "$wt_count_before" == "$wt_count_after" ]]; then
    printf 'PASS  146  follow-up addressed by worktree path reuses it (no new worktree)\n'; pass=$((pass + 1))
  else
    printf 'FAIL  146  worktree reuse wrong [rc=%d branch=%s head=%s/%s count=%s/%s]\n' \
      "$fu_rc" "$fu_branch_after" "$fu_head_before" "$fu_head_after" \
      "$wt_count_before" "$wt_count_after"; fail=$((fail + 1))
    failures+=("146 worktree reuse :: rc=$fu_rc branch=$fu_branch_after")
  fi

  # --- 147. `cerebro worktrees cleanup` removes a stale worktree (its branch has
  # no open PR and no unpushed commits) but keeps one with unpushed commits, one
  # whose branch has a (simulated) open PR, and one with uncommitted/untracked
  # work in its tree. A failed PR lookup (gh non-zero) must also KEEP, never read
  # as no-PR. ---
  WT_GC_STALE="$CEREBRO_HOME/worktrees/gc-stale"
  WT_GC_AHEAD="$CEREBRO_HOME/worktrees/gc-ahead"
  WT_GC_PR="$CEREBRO_HOME/worktrees/gc-pr"
  WT_GC_DIRTY="$CEREBRO_HOME/worktrees/gc-dirty"
  WT_GC_PRFAIL="$CEREBRO_HOME/worktrees/gc-prfail"
  git -C "$REPO" worktree add -q -b feat/gc-stale  "$WT_GC_STALE"  main >/dev/null 2>&1
  git -C "$REPO" worktree add -q -b feat/gc-ahead  "$WT_GC_AHEAD"  main >/dev/null 2>&1
  git -C "$REPO" worktree add -q -b feat/gc-pr     "$WT_GC_PR"     main >/dev/null 2>&1
  git -C "$REPO" worktree add -q -b feat/gc-dirty  "$WT_GC_DIRTY"  main >/dev/null 2>&1
  git -C "$REPO" worktree add -q -b feat/gc-prfail "$WT_GC_PRFAIL" main >/dev/null 2>&1
  python3 - "$WTDIR/child-sessions.json" "$WT_GC_STALE" "$WT_GC_AHEAD" "$WT_GC_PR" "$WT_GC_DIRTY" "$WT_GC_PRFAIL" <<'PYWORKSPACES'
import json, subprocess, sys
from pathlib import Path
p = Path(sys.argv[1]); records = json.loads(p.read_text())
for value in sys.argv[2:]:
    records[Path(value).name] = {'workspace': {'path': str(Path(value).resolve()), 'created_worktree': True, 'common_dir': str((Path(value) / subprocess.check_output(['git', '-C', value, 'rev-parse', '--git-common-dir'], text=True).strip()).resolve())}, 'status': 'done'}
p.write_text(json.dumps(records))
PYWORKSPACES
  # gc-ahead carries a commit ahead of the base ref (unpushed) -> must be kept.
  printf 'ahead\n' >> "$WT_GC_AHEAD/main.sh"
  git -C "$WT_GC_AHEAD" add main.sh >/dev/null 2>&1
  git -C "$WT_GC_AHEAD" commit -q -m "unpushed work" >/dev/null 2>&1
  # gc-dirty has only UNCOMMITTED + UNTRACKED work (no commit ahead) -> must be
  # kept by the dirty-tree check, or that work would be destroyed.
  printf 'uncommitted\n' >> "$WT_GC_DIRTY/main.sh"
  printf 'untracked\n' > "$WT_GC_DIRTY/scratch.txt"
  # gh stub: OPEN PR for feat/gc-pr; a forced lookup FAILURE (exit 2) for
  # feat/gc-prfail (transient auth/network) which must KEEP; clean empty (exit 0)
  # for the rest, the only genuine "no open PR".
  GC_GH_DIR="$WORKDIR/gc-gh-stub"; mkdir -p "$GC_GH_DIR"
  cat > "$GC_GH_DIR/gh" <<'EOF'
#!/usr/bin/env bash
br=""; prev=""
for a in "$@"; do [[ "$prev" == "--head" ]] && br="$a"; prev="$a"; done
[[ "$br" == "feat/gc-pr" ]]     && { echo OPEN; exit 0; }
[[ "$br" == "feat/gc-prfail" ]] && exit 2   # lookup failure -> unknown -> keep
exit 0                                        # successful empty -> no open PR
EOF
  chmod +x "$GC_GH_DIR/gh"
  env PATH="$GC_GH_DIR:$PATH" CEREBRO_SESSION_ID="$WTSESS" \
    "$CEREBRO_BIN" worktrees cleanup >/dev/null 2>&1
  if [[ ! -d "$WT_GC_STALE" && -d "$WT_GC_AHEAD" && -d "$WT_GC_PR" \
        && -d "$WT_GC_DIRTY" && -d "$WT_GC_PRFAIL" ]]; then
    printf 'PASS  147  cleanup removes stale, keeps unpushed/open-PR/dirty/PR-lookup-failed\n'; pass=$((pass + 1))
  else
    printf 'FAIL  147  cleanup verdicts wrong [stale=%d ahead=%d pr=%d dirty=%d prfail=%d]\n' \
      "$([[ -d "$WT_GC_STALE" ]] && echo 1 || echo 0)" \
      "$([[ -d "$WT_GC_AHEAD" ]] && echo 1 || echo 0)" \
      "$([[ -d "$WT_GC_PR" ]] && echo 1 || echo 0)" \
      "$([[ -d "$WT_GC_DIRTY" ]] && echo 1 || echo 0)" \
      "$([[ -d "$WT_GC_PRFAIL" ]] && echo 1 || echo 0)"; fail=$((fail + 1))
    failures+=("147 worktrees cleanup verdicts")
  fi
  # Tidy the kept test worktrees so they don't perturb later worktree scans.
  for w in "$WT_FU" "$WT_GC_AHEAD" "$WT_GC_PR" "$WT_GC_DIRTY" "$WT_GC_PRFAIL"; do
    git -C "$REPO" worktree remove --force "$w" >/dev/null 2>&1 || true
  done
  git -C "$REPO" worktree prune >/dev/null 2>&1 || true
  for b in feat/fu-reuse feat/gc-stale feat/gc-ahead feat/gc-pr feat/gc-dirty feat/gc-prfail; do
    git -C "$REPO" branch -D "$b" >/dev/null 2>&1 || true
  done
else
  printf 'SKIP  125  execute child-session capture (Pi fixture unavailable)\n'
  printf 'SKIP  125b execute resume=none log (Pi fixture unavailable)\n'
  printf 'SKIP  126  same-branch execute isolation (Pi fixture unavailable)\n'
  printf 'SKIP  126b completed execute no-auto-resume (Pi fixture unavailable)\n'
  printf 'SKIP  126c distinct base/branch stacked-mode prompt (Pi fixture unavailable)\n'
  printf 'SKIP  129  execute rejected-resume propagation (Pi fixture unavailable)\n'
  printf 'SKIP  130  execute mutating-resume no-rerun (Pi fixture unavailable)\n'
  printf 'SKIP  145  execute worktree isolation (Pi fixture unavailable)\n'
  printf 'SKIP  146  follow-up worktree reuse (Pi fixture unavailable)\n'
  printf 'SKIP  147  worktrees cleanup (Pi fixture unavailable)\n'
fi

# Native read-only reviewer fixture: record CLI arguments and RPC commands,
# then produce findings through the real stream parser.
REVIEW_STUB_DIR="$WORKDIR/pi-review-stub"
mkdir -p "$REVIEW_STUB_DIR"
REVIEW_ARGV_LOG="$WORKDIR/review-argv.log"
install_pi_fixture "$REVIEW_STUB_DIR" \
  "$(jq -n --arg log "$REVIEW_ARGV_LOG" '{text:"## Findings: no issues found",request_log:$log}')"

# --- 127r. the native reviewer policy denies direct writes/shell/delegation;
# inspection uses guarded Cerebro commands and the shared reviewer note.
run_case 127r "reviewer native permissions enforce read-only inspection" 0 -- \
  python3 "$here/backend_contract_test.py" review

if [[ -x "$REVIEW_STUB_DIR/pi" ]]; then
  REVIEW_STUB_PATH="$REVIEW_STUB_DIR:$PATH"
  RSESS="review-session"; RDIR="$CEREBRO_HOME/sessions/$RSESS"
  initialize_session "$RDIR"

  # --- 127. review records the durable Pi session file under a review key ---
  : > "$REVIEW_ARGV_LOG"
  rev_out="$(env PATH="$REVIEW_STUB_PATH" CEREBRO_SESSION_ID="$RSESS" \
    "$CEREBRO_BIN" review "$REPO" 2>/dev/null)"
  rv_id="$(jq -r '.[] | select(.provider=="pi" and .role=="review") | .id' "$RDIR/child-sessions.json" 2>/dev/null)"
  if [[ "$rv_id" == /* && -s "$rv_id" ]] \
     && head -1 "$rv_id" | jq -e '.type == "session" and (.id | length) > 0' >/dev/null; then
    printf 'PASS  127  review records the Pi session file under a review key\n'; pass=$((pass + 1))
  else
    printf 'FAIL  127  review did not record the session id [got=%s]\n' "$rv_id"; fail=$((fail + 1))
    failures+=("127 review capture :: got=$rv_id")
  fi

  # --- 127b. reviewers use native RPC with only the guarded command tool.
  if jq -e -s 'any(.[] | select(.argv); .argv as $a |
        ($a | index("--mode")) as $m | ($a | index("--tools")) as $t |
        .role == "reviewer" and $m != null and $a[$m+1] == "rpc" and
        $t != null and $a[$t+1] == "mcp__cerebro__command") and
        any(.[]; .type == "get_state") and any(.[]; .type == "prompt")' \
        "$REVIEW_ARGV_LOG" >/dev/null; then
    printf 'PASS  127b review uses native RPC and guarded command tools\n'; pass=$((pass + 1))
  else
    printf 'FAIL  127b review native session request wrong\n'; fail=$((fail + 1))
    failures+=("127b review native requests")
  fi

  # --- 127c. without a model override the reviewer retains the backend's
  # configured default, rather than injecting an unrelated provider model.
  if jq -e -s 'all(.[] | select(.argv); (.argv | index("--model")) == null)' "$REVIEW_ARGV_LOG" >/dev/null; then
    printf 'PASS  127c review retains the native default model\n'; pass=$((pass + 1))
  else
    printf 'FAIL  127c review overwrote the native default model\n'; fail=$((fail + 1))
    failures+=("127c review native default model")
  fi

  # --- 127m. --model overrides the review model through native model selection. ---
  : > "$REVIEW_ARGV_LOG"
  env PATH="$REVIEW_STUB_PATH" CEREBRO_SESSION_ID="$RSESS" \
    "$CEREBRO_BIN" review "$REPO" --model minimax/minimax-m3 >/dev/null 2>&1
  if jq -e -s 'any(.[] | select(.argv); .argv as $a |
        ($a | index("--model")) as $m | $m != null and $a[$m+1] == "minimax/minimax-m3")' "$REVIEW_ARGV_LOG" >/dev/null; then
    printf 'PASS  127m  review --model overrides the review model per call\n'; pass=$((pass + 1))
  else
    printf 'FAIL  127m  review --model not passed through [argv=%s]\n' "$(cat "$REVIEW_ARGV_LOG")"; fail=$((fail + 1))
    failures+=("127m review --model :: argv=$(cat "$REVIEW_ARGV_LOG")")
  fi

  # --- 127n. native model selectors retain their literal backend spelling.
  : > "$REVIEW_ARGV_LOG"
  vfn_out="$(env PATH="$REVIEW_STUB_PATH" CEREBRO_SESSION_ID="$RSESS" \
    "$CEREBRO_BIN" verify "$REPO" --prompt "verify it" --model glm-5.2:cloud 2>&1)"
  vfn_rc=$?
  if (( vfn_rc == 0 )) \
      && jq -e -s 'any(.[] | select(.argv); .argv as $a |
          ($a | index("--model")) as $m | $m != null and $a[$m+1] == "glm-5.2:cloud")' "$REVIEW_ARGV_LOG" >/dev/null; then
    printf 'PASS  127n  verify forwards native Pi model selectors literally\n'; pass=$((pass + 1))
  else
    printf 'FAIL  127n  verify lost the native model selector [rc=%s out=%s argv=%s]\n' "$vfn_rc" "$vfn_out" "$(cat "$REVIEW_ARGV_LOG")"; fail=$((fail + 1))
    failures+=("127n verify native --model :: rc=$vfn_rc out=$vfn_out")
  fi

  # --- 127e. the findings are the run's final message, written to out_path ---
  if [[ -s "$rev_out" ]] && grep -q 'no issues found' "$rev_out"; then
    printf 'PASS  127e  review findings are the run final message (written to out_path)\n'; pass=$((pass + 1))
  else
    printf 'FAIL  127e  review findings not captured [out=%s]\n' "$rev_out"; fail=$((fail + 1))
    failures+=("127e review findings capture :: out=$rev_out")
  fi

  # --- 127d. criteria review still puts the external-tool guidance in the prompt ---
  criteria_plan="$(env CEREBRO_SESSION_ID="$RSESS" \
    "$CEREBRO_BIN" plan $'## Acceptance criteria (checkpoint)\n- Real browser check passes' --out criteria-target 2>/dev/null)"
  : > "$REVIEW_ARGV_LOG"
  env PATH="$REVIEW_STUB_PATH" CEREBRO_SESSION_ID="$RSESS" \
    "$CEREBRO_BIN" review "$REPO" --criteria-file "$criteria_plan" >/dev/null 2>&1
  if grep -q "use verdict EXTERNAL" "$REVIEW_ARGV_LOG" \
      && grep -q "EXTERNAL criteria do not make the final verdict NOT MET" "$REVIEW_ARGV_LOG"; then
    printf 'PASS  127d  criteria prompt externalizes unavailable browser checks\n'; pass=$((pass + 1))
  else
    printf 'FAIL  127d  criteria prompt missing external-tool guidance [argv=%s]\n' "$(cat "$REVIEW_ARGV_LOG")"; fail=$((fail + 1))
    failures+=("127d criteria external guidance missing")
  fi

  # --- 128. a second completed review does not resume the stored review session ---
  : > "$REVIEW_ARGV_LOG"
  env PATH="$REVIEW_STUB_PATH" CEREBRO_SESSION_ID="$RSESS" \
    "$CEREBRO_BIN" review "$REPO" >/dev/null 2>&1
  rv_again_id="$(jq -r '.[] | select(.provider=="pi" and .role=="review") | .id' "$RDIR/child-sessions.json" 2>/dev/null)"
  if [[ "$rv_again_id" == /* && -s "$rv_again_id" && "$rv_again_id" != "$rv_id" ]] \
     && jq -e -s 'any(.[]; .type == "get_state") and
         all(.[] | select(.argv); (.argv | index("--session")) == null)' "$REVIEW_ARGV_LOG" >/dev/null \
     && ! grep -qF "resume=$rv_id" "$RDIR/transcript.jsonl"; then
    printf 'PASS  128  completed review session is not auto-resumed\n'; pass=$((pass + 1))
  else
    printf 'FAIL  128  completed review was resumed [argv=%s]\n' "$(cat "$REVIEW_ARGV_LOG")"; fail=$((fail + 1))
    failures+=("128 review completed auto-resume")
  fi

  # --- 128c. audit passes plan+context to a native reviewer, records the
  # session, and echoes the findings path. ---
  audit_plan="$(env CEREBRO_SESSION_ID="$RSESS" \
    "$CEREBRO_BIN" plan "# The plan: touch lib/thing.sh" --out audit-target 2>/dev/null)"
  : > "$REVIEW_ARGV_LOG"
  audit_out="$(env PATH="$REVIEW_STUB_PATH" CEREBRO_SESSION_ID="$RSESS" \
    "$CEREBRO_BIN" audit "$REPO" "$audit_plan" --context "key paths: lib/" 2>/dev/null)"
  audit_expected="$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve())' "$RDIR/audits/audit-target-audit.md")"
  audit_argv="$(cat "$REVIEW_ARGV_LOG")"
  audit_id="$(jq -r '.[] | select(.provider=="pi" and .role=="audit") | .id' "$RDIR/child-sessions.json" 2>/dev/null)"
  if [[ "$audit_out" == "$audit_expected" && -s "$audit_out" \
        && "$audit_argv" == *"touch lib/thing.sh"* \
        && "$audit_argv" == *"key paths: lib/"* \
        && "$audit_id" == /* && -s "$audit_id" && "$audit_id" != "$rv_id" ]] \
     && jq -e -s 'any(.[] | select(.argv); .role == "reviewer" and
          .child_role == "audit" and (.argv | index("--cerebro-mcp-config")) != null)' "$REVIEW_ARGV_LOG" >/dev/null; then
    printf 'PASS  128c  audit runs the native reviewer with plan+context, records session\n'; pass=$((pass + 1))
  else
    printf 'FAIL  128c  audit run wrong [out=%s id=%s]\n' "$audit_out" "$audit_id"; fail=$((fail + 1))
    failures+=("128c audit :: out=$audit_out id=$audit_id")
  fi

  # --- 128d. a re-audit of the same completed plan starts a fresh session ---
  : > "$REVIEW_ARGV_LOG"
  env PATH="$REVIEW_STUB_PATH" CEREBRO_SESSION_ID="$RSESS" \
    "$CEREBRO_BIN" audit "$REPO" "$audit_plan" >/dev/null 2>&1
  audit_again_rc=$?
  audit_again_id="$(jq -r '.[] | select(.provider=="pi" and .role=="audit") | .id' "$RDIR/child-sessions.json" 2>/dev/null)"
  if (( audit_again_rc == 0 )) \
     && [[ "$audit_again_id" == /* && -s "$audit_again_id" && "$audit_again_id" != "$audit_id" ]] \
     && jq -e -s 'any(.[]; .type == "get_state") and
         all(.[] | select(.argv); (.argv | index("--session")) == null)' "$REVIEW_ARGV_LOG" >/dev/null; then
    printf 'PASS  128d re-audit does not resume the stored session\n'; pass=$((pass + 1))
  else
    printf 'FAIL  128d re-audit resumed [argv=%s]\n' "$(cat "$REVIEW_ARGV_LOG")"; fail=$((fail + 1))
    failures+=("128d audit resume argv present")
  fi
else
  printf 'SKIP  127  review session capture (Pi fixture unavailable)\n'
  printf 'SKIP  127b review native session/tools (Pi fixture unavailable)\n'
  printf 'SKIP  127c review model (Pi fixture unavailable)\n'
  printf 'SKIP  127e review findings (Pi fixture unavailable)\n'
  printf 'SKIP  127d review external criteria guidance (Pi fixture unavailable)\n'
  printf 'SKIP  128  review completed no-auto-resume (Pi fixture unavailable)\n'
  printf 'SKIP  128c audit run (Pi fixture unavailable)\n'
  printf 'SKIP  128d audit completed no-auto-resume (Pi fixture unavailable)\n'
fi

# ========================================================================
# 129x. All child roles use the selected backend. Claude review is exercised
# through its guarded native MCP tool surface, not a separate reviewer backend.
# ========================================================================

# --- 129a. default Pi child selectors are shared role labels. ---
rb_default="$(bash -c '
  set -uo pipefail
  CEREBRO_LIB_DIR="$1"; shift
  . "$CEREBRO_LIB_DIR/config.sh"
  . "$CEREBRO_LIB_DIR/helpers.sh"
  . "$CEREBRO_LIB_DIR/backend.sh"
  . "$CEREBRO_LIB_DIR/backend-pi.sh"
  for r in "$@"; do printf "%s|" "$(backend_child_agent_name "$r")"; done' \
  _ "$here/../lib" review audit verify improve 2>/dev/null)"
if [[ "$rb_default" == "review|audit|verify|improve|" ]]; then
  printf 'PASS  129a backend_child_agent_name default pi roles\n'; pass=$((pass + 1))
else
  printf 'FAIL  129a backend_child_agent_name default [%s]\n' "$rb_default"; fail=$((fail + 1))
  failures+=("129a backend_child_agent_name default :: $rb_default")
fi

# --- 129b. under CEREBRO_BACKEND=claude, backend_child_agent_name
# returns the role label itself (composed inline via --append-system-prompt). ---
rb_claude="$(CEREBRO_BACKEND=claude bash -c '
  set -uo pipefail
  CEREBRO_LIB_DIR="$1"; shift
  . "$CEREBRO_LIB_DIR/config.sh"
  . "$CEREBRO_LIB_DIR/helpers.sh"
  . "$CEREBRO_LIB_DIR/backend.sh"
  . "$CEREBRO_LIB_DIR/backend-pi.sh"
  . "$CEREBRO_LIB_DIR/backend-claude.sh"
  for r in "$@"; do printf "%s|" "$(backend_child_agent_name "$r")"; done' \
  _ "$here/../lib" review audit verify improve 2>/dev/null)"
if [[ "$rb_claude" == "review|audit|verify|improve|" ]]; then
  printf 'PASS  129b backend_child_agent_name claude role labels\n'; pass=$((pass + 1))
else
  printf 'FAIL  129b backend_child_agent_name claude [%s]\n' "$rb_claude"; fail=$((fail + 1))
  failures+=("129b backend_child_agent_name claude :: $rb_claude")
fi

# --- 129c. a resumed session's backend owns every role, including reviews.
providers="$(CEREBRO_BACKEND=pi CEREBRO_RESUME_BACKEND=codex bash -c '
  CEREBRO_LIB_DIR="$1"
  . "$CEREBRO_LIB_DIR/config.sh"
  . "$CEREBRO_LIB_DIR/backend.sh"
  for role in execute review audit verify improve; do
    printf "%s:%s|" "$(backend_child_provider "$role")" "$(backend_child_agent_name "$role")"
  done' _ "$here/../lib")"
if [[ "$providers" == "codex:execute|codex:review|codex:audit|codex:verify|codex:improve|" ]]; then
  printf 'PASS  129c resumed backend owns development/review/verification roles\n'; pass=$((pass + 1))
else
  printf 'FAIL  129c same-backend role dispatch [%s]\n' "$providers"; fail=$((fail + 1))
  failures+=("129c same-backend role dispatch")
fi

# --- 129d. claude reviewer system prompt carries the read-only reviewer note
# (the same shared note the native Pi reviewer loads). ---
rsp="$(CEREBRO_BACKEND=claude bash -c '
  set -uo pipefail
  CEREBRO_LIB_DIR="$1"; shift
  . "$CEREBRO_LIB_DIR/config.sh"
  . "$CEREBRO_LIB_DIR/helpers.sh"
  . "$CEREBRO_LIB_DIR/payloads.sh"
  child_sys_prompt "$1"' _ "$here/../lib" review 2>/dev/null)"
if [[ "$rsp" == *"READ-ONLY reviewer"* && "$rsp" == *'["git", "/absolute/worktree", "diff", "BASE"]'* ]]; then
  printf 'PASS  129d claude reviewer sys prompt is the read-only note\n'; pass=$((pass + 1))
else
  printf 'FAIL  129d claude reviewer sys prompt [%s]\n' "$rsp"; fail=$((fail + 1))
  failures+=("129d claude reviewer sys prompt :: $rsp")
fi

# --- 129e. backend_claude_endpoint_env pins the gateway to the supplied
# model (the review model for the reviewer path) and no longer dies when the
# effective model is the shipped editing default -- the review-model
# difference is a suggestion, not a rule, so a custom endpoint with the
# default model now pins and proceeds. ---
pin="$(CEREBRO_CLAUDE_BASE_URL=http://localhost:11434 CEREBRO_REVIEW_MODEL=review-native bash -c '
  CEREBRO_LIB_DIR="$1"
  . "$CEREBRO_LIB_DIR/config.sh"
  . "$CEREBRO_LIB_DIR/helpers.sh"
  . "$CEREBRO_LIB_DIR/commands/models.sh"
  . "$CEREBRO_LIB_DIR/backend-claude.sh"
  backend_claude_endpoint_env "$CEREBRO_REVIEW_MODEL"
  printf "%s" "$ANTHROPIC_MODEL"' _ "$here/../lib" 2>/dev/null)"
default_pin="$(CEREBRO_CLAUDE_BASE_URL=http://localhost:11434 CEREBRO_MODEL=editing-native bash -c '
  CEREBRO_LIB_DIR="$1"
  . "$CEREBRO_LIB_DIR/config.sh"
  . "$CEREBRO_LIB_DIR/helpers.sh"
  . "$CEREBRO_LIB_DIR/commands/models.sh"
  . "$CEREBRO_LIB_DIR/backend-claude.sh"
  backend_claude_endpoint_env
  printf "%s" "$ANTHROPIC_MODEL"' _ "$here/../lib" 2>/dev/null)"
if [[ "$pin" == "review-native" && "$default_pin" == "editing-native" ]]; then
  printf 'PASS  129e endpoint_env pins the selected review/editing native models\n'; pass=$((pass + 1))
else
  printf 'FAIL  129e endpoint_env [pin=%s default=%s]\n' "$pin" "$default_pin"; fail=$((fail + 1))
  failures+=("129e endpoint model pinning")
fi

# --- 130. cerebro models prints the user's model catalog ($CEREBRO_HOME/
# models-config.json): text listing with id / capabilities / description, and
# --json emits the raw array. A missing catalog is not an error. ---
MDLHOME="$WORKDIR/mdl-home"
mkdir -p "$MDLHOME"
cat > "$MDLHOME/models-config.json" <<'MDL_EOF'
{"models":[
  {"id":"github-copilot/gemini-3.1-pro-preview","capabilities":["vision","tools","thinking"],"contextTokens":256000,"description":"Strong generalist; smaller context window."},
  {"id":"minimax/minimax-m3","capabilities":["vision","tools","audio"],"contextTokens":512000,"description":"Vision + audio; use for screenshot verification."}
]}
MDL_EOF
mdl_out="$(CEREBRO_HOME="$MDLHOME" "$CEREBRO_BIN" models 2>/dev/null)"
mdl_json="$(CEREBRO_HOME="$MDLHOME" "$CEREBRO_BIN" models --json 2>/dev/null)"
mkdir -p "$WORKDIR/empty-home"
mdl_missing="$(CEREBRO_HOME="$WORKDIR/empty-home" "$CEREBRO_BIN" models 2>/dev/null)"
if [[ "$mdl_out" == *"## minimax/minimax-m3"* \
   && "$mdl_out" == *"capabilities: vision, tools, audio"* \
   && "$mdl_out" == *"screenshot verification"* \
   && "$mdl_json" == *'"id":"minimax/minimax-m3"'* \
   && "$mdl_json" == *'"vision"'* \
   && "$mdl_missing" == *"Create one to let the orchestrator pick models per task"* ]]; then
  printf 'PASS  130  cerebro models lists catalog (text + json) and handles missing\n'; pass=$((pass + 1))
else
  printf 'FAIL  130  cerebro models [out=%s json=%s missing=%s]\n' "$mdl_out" "$mdl_json" "$mdl_missing"; fail=$((fail + 1))
  failures+=("130 cerebro models :: out=$mdl_out json=$mdl_json missing=$mdl_missing")
fi

# --- 130b. models_context_tokens <id> reads contextTokens from the catalog
# (integer for a known id; empty for an unknown id, a missing field, or no
# catalog). ---
ctx_known="$(CEREBRO_HOME="$MDLHOME" bash -c '
  set -uo pipefail
  CEREBRO_LIB_DIR="$1"; CEREBRO_HOME="$2"
  . "$CEREBRO_LIB_DIR/config.sh"
  . "$CEREBRO_LIB_DIR/helpers.sh"
  . "$CEREBRO_LIB_DIR/commands/models.sh"
  models_context_tokens "minimax/minimax-m3"' _ "$here/../lib" "$MDLHOME" 2>/dev/null)"
ctx_unknown="$(CEREBRO_HOME="$MDLHOME" bash -c '
  set -uo pipefail
  CEREBRO_LIB_DIR="$1"; CEREBRO_HOME="$2"
  . "$CEREBRO_LIB_DIR/config.sh"
  . "$CEREBRO_LIB_DIR/helpers.sh"
  . "$CEREBRO_LIB_DIR/commands/models.sh"
  models_context_tokens "nope/missing"' _ "$here/../lib" "$MDLHOME" 2>/dev/null)"
if [[ "$ctx_known" == "512000" && -z "$ctx_unknown" ]]; then
  printf 'PASS  130b models_context_tokens reads contextTokens; empty for unknown\n'; pass=$((pass + 1))
else
  printf 'FAIL  130b models_context_tokens [known=%s unknown=%s]\n' "$ctx_known" "$ctx_unknown"; fail=$((fail + 1))
  failures+=("130b models_context_tokens :: known=$ctx_known unknown=$ctx_unknown")
fi

# --- 130c. backend_claude_endpoint_env exports CLAUDE_CODE_AUTO_COMPACT_WINDOW
# from the model's contextTokens when CEREBRO_CLAUDE_BASE_URL is set, and leaves
# it unset when the base URL is unset (subscription path). The shipped default
# CEREBRO_MODEL (github-copilot/gemini-3.1-pro-preview) has contextTokens 256000
# in the fixture. ---
acw_set="$(CEREBRO_MODEL=github-copilot/gemini-3.1-pro-preview CEREBRO_CLAUDE_BASE_URL=http://localhost:11434 CEREBRO_HOME="$MDLHOME" bash -c '
  set -uo pipefail
  CEREBRO_LIB_DIR="$1"; CEREBRO_HOME="$2"
  . "$CEREBRO_LIB_DIR/config.sh"
  . "$CEREBRO_LIB_DIR/helpers.sh"
  . "$CEREBRO_LIB_DIR/commands/models.sh"
  . "$CEREBRO_LIB_DIR/backend-claude.sh"
  backend_claude_endpoint_env
  printf "%s" "${CLAUDE_CODE_AUTO_COMPACT_WINDOW:-}"' _ "$here/../lib" "$MDLHOME" 2>/dev/null)"
acw_unset="$(CEREBRO_HOME="$MDLHOME" bash -c '
  set -uo pipefail
  unset CLAUDE_CODE_AUTO_COMPACT_WINDOW
  CEREBRO_LIB_DIR="$1"; CEREBRO_HOME="$2"
  . "$CEREBRO_LIB_DIR/config.sh"
  . "$CEREBRO_LIB_DIR/helpers.sh"
  . "$CEREBRO_LIB_DIR/commands/models.sh"
  . "$CEREBRO_LIB_DIR/backend-claude.sh"
  backend_claude_endpoint_env
  printf "%s" "${CLAUDE_CODE_AUTO_COMPACT_WINDOW:-}"' _ "$here/../lib" "$MDLHOME" 2>/dev/null)"
if [[ "$acw_set" == "256000" && -z "$acw_unset" ]]; then
  printf 'PASS  130c endpoint_env exports AUTO_COMPACT_WINDOW when base url set\n'; pass=$((pass + 1))
else
  printf 'FAIL  130c endpoint_env [set=%s unset=%s]\n' "$acw_set" "$acw_unset"; fail=$((fail + 1))
  failures+=("130c endpoint_env :: set=$acw_set unset=$acw_unset")
fi

# --- 130d. cerebro model-env <id> prints the right exports; --no-compact
# switches to CLAUDE_CODE_MAX_CONTEXT_TOKENS + DISABLE_COMPACT; an unknown id
# prints no export (safe eval no-op). ---
me_default="$(CEREBRO_HOME="$MDLHOME" "$CEREBRO_BIN" model-env minimax/minimax-m3 2>/dev/null)"
me_nocomp="$(CEREBRO_HOME="$MDLHOME" "$CEREBRO_BIN" model-env minimax/minimax-m3 --no-compact 2>/dev/null)"
me_unknown="$(CEREBRO_HOME="$MDLHOME" "$CEREBRO_BIN" model-env nope/missing 2>/dev/null)"
if [[ "$me_default" == "export CLAUDE_CODE_AUTO_COMPACT_WINDOW=512000" \
   && "$me_nocomp" == *"export CLAUDE_CODE_MAX_CONTEXT_TOKENS=512000"* \
   && "$me_nocomp" == *"export DISABLE_COMPACT=1"* \
   && -z "$me_unknown" ]]; then
  printf 'PASS  130d cerebro model-env prints exports (+ --no-compact, unknown no-op)\n'; pass=$((pass + 1))
else
  printf 'FAIL  130d model-env [default=%s nocomp=%s unknown=%s]\n' "$me_default" "$me_nocomp" "$me_unknown"; fail=$((fail + 1))
  failures+=("130d model-env :: default=$me_default nocomp=$me_nocomp unknown=$me_unknown")
fi

# --- 130e. backend_claude_acp_child_spec registers catalog model ids with the
# upstream Claude SDK via ANTHROPIC_DEFAULT_{OPUS,SONNET,FABLE}_MODEL and
# ANTHROPIC_CUSTOM_MODEL_OPTION (id-registration only; the editor-facing
# picker is owned by the proxy -- see 130f). HAIKU is reserved for the
# ANTHROPIC_DEFAULT_HAIKU_MODEL=$CEREBRO_SUPERVISOR_MODEL mirror (background
# tasks). No _NAME/_DESCRIPTION/_SUPPORTED_CAPABILITIES companions are
# emitted (the proxy rewrites the picker from the catalog, so the SDK's own
# picker labels are invisible to the editor). The id-registration env is gated
# on CEREBRO_CLAUDE_BASE_URL (subscription path = stock anthropic picker,
# unchanged) and on a parseable catalog (missing/garbage = {}). ---

# Fixture catalog: 4 entries so we exercise the 3-slot path AND the
# ANTHROPIC_CUSTOM_MODEL_OPTION overflow row.
MDLACP="$WORKDIR/mdl-acp-home"
mkdir -p "$MDLACP"
cat > "$MDLACP/models-config.json" <<'MDLACP_EOF'
{"models":[
  {"id":"glm-5.2:cloud","name":"GLM 5.2","capabilities":["tools","thinking"],"contextTokens":1000000,"description":"Most advanced model; 1M context."},
  {"id":"minimax-m3:cloud","name":"MiniMax M3","capabilities":["vision","tools","thinking"],"contextTokens":512000,"description":"Fast model with vision."},
  {"id":"kimi-k2.7-code:cloud","name":"Kimi K2.7 Code","capabilities":["vision","tools","thinking"],"contextTokens":256000,"description":"Second-best, 256K context."},
  {"id":"extra:cloud","name":"Extra","capabilities":["vision"],"description":"4th entry -> custom model option."}
]}
MDLACP_EOF
cat > "$MDLACP/models-config-5.json" <<'MDLACP_EOF'
{"models":[
  {"id":"a:cloud","name":"A","capabilities":["vision"]},
  {"id":"b:cloud","name":"B","capabilities":["vision"]},
  {"id":"c:cloud","name":"C","capabilities":["vision"]},
  {"id":"d:cloud","name":"D","capabilities":["vision"]},
  {"id":"e:cloud","name":"E","capabilities":["vision"]}
]}
MDLACP_EOF

# Helper: load the lib files needed for backend_claude_acp_child_spec
# (mirrors the sourcing pattern from tests 130b/130c). Usage:
#   spec="$(load_claude_libs <lib_dir> <home> '<commands to eval>')"
# The lib path and home dir are passed as positional args, not env vars,
# so the subshell can read them after set -u. Stderr is silenced because
# the bash subshell prints harmless "set -u" unbound-variable warnings
# when sourced files reference unset env vars we don't care about here.
load_claude_libs() {
  local lib="$1" home="$2" cmd="$3"
  CEREBRO_LIB_DIR="$lib" CEREBRO_HOME="$home" \
    bash -c '
    set -uo pipefail
    CEREBRO_LIB_DIR="$1"; CEREBRO_HOME="$2"
    . "$CEREBRO_LIB_DIR/config.sh"
    . "$CEREBRO_LIB_DIR/helpers.sh"
    . "$CEREBRO_LIB_DIR/payloads.sh"
    . "$CEREBRO_LIB_DIR/session-store.sh"
    . "$CEREBRO_LIB_DIR/backend.sh"
    . "$CEREBRO_LIB_DIR/backend-pi.sh"
    . "$CEREBRO_LIB_DIR/backend-claude.sh"
    for f in "$CEREBRO_LIB_DIR"/commands/*.sh; do . "$f"; done
    '"$cmd" _ "$lib" "$home" 2>/dev/null
}

spec="$(load_claude_libs "$here/../lib" "$MDLACP" \
  'CEREBRO_CLAUDE_BASE_URL=http://localhost:11434 \
   CEREBRO_CLAUDE_AUTH_TOKEN=ollama \
   CEREBRO_MODEL=implementation-native CEREBRO_SUPERVISOR_MODEL=glm-5.2:cloud \
   backend_claude_acp_child_spec')"
spec_no_url="$(load_claude_libs "$here/../lib" "$MDLACP" \
  'CEREBRO_CLAUDE_BASE_URL="" \
   CEREBRO_MODEL=implementation-native CEREBRO_SUPERVISOR_MODEL=glm-5.2:cloud \
   backend_claude_acp_child_spec')"
spec_no_cat="$(load_claude_libs "$here/../lib" "$WORKDIR/empty-home" \
  'CEREBRO_CLAUDE_BASE_URL=http://localhost:11434 \
   CEREBRO_CLAUDE_AUTH_TOKEN=ollama \
   CEREBRO_MODEL=implementation-native CEREBRO_SUPERVISOR_MODEL=glm-5.2:cloud \
   backend_claude_acp_child_spec')"
BADHOME="$WORKDIR/mdl-bad"
mkdir -p "$BADHOME"
printf 'garbage\n' > "$BADHOME/models-config.json"
spec_bad_cat="$(load_claude_libs "$here/../lib" "$BADHOME" \
  'CEREBRO_CLAUDE_BASE_URL=http://localhost:11434 \
   CEREBRO_CLAUDE_AUTH_TOKEN=ollama \
   CEREBRO_MODEL=implementation-native CEREBRO_SUPERVISOR_MODEL=glm-5.2:cloud \
   backend_claude_acp_child_spec')"
cp "$MDLACP/models-config-5.json" "$MDLACP/models-config.json"
spec_5="$(load_claude_libs "$here/../lib" "$MDLACP" \
  'CEREBRO_CLAUDE_BASE_URL=http://localhost:11434 \
   CEREBRO_CLAUDE_AUTH_TOKEN=ollama \
   CEREBRO_MODEL=implementation-native CEREBRO_SUPERVISOR_MODEL=glm-5.2:cloud \
   backend_claude_acp_child_spec')"
cat > "$MDLACP/models-config.json" <<'MDLACP_EOF'
{"models":[
  {"id":"glm-5.2:cloud","name":"GLM 5.2","capabilities":["tools","thinking"],"contextTokens":1000000,"description":"Most advanced model; 1M context."},
  {"id":"minimax-m3:cloud","name":"MiniMax M3","capabilities":["vision","tools","thinking"],"contextTokens":512000,"description":"Fast model with vision."},
  {"id":"kimi-k2.7-code:cloud","name":"Kimi K2.7 Code","capabilities":["vision","tools","thinking"],"contextTokens":256000,"description":"Second-best, 256K context."},
  {"id":"extra:cloud","name":"Extra","capabilities":["vision"],"description":"4th entry -> custom model option."}
]}
MDLACP_EOF

# Assert: 4-entry catalog -> 3 slot ids + 1 custom option id registered.
# NO companions (_NAME/_DESCRIPTION/_SUPPORTED_CAPABILITIES) are emitted:
# the proxy rewrites the editor-facing picker, so the SDK's own labels are
# invisible. HAIKU stays = CEREBRO_SUPERVISOR_MODEL, not a
# catalog entry. ANTHROPIC_MODEL pins the initial selection.
spec_ok=1
spec_msg=""
for v in \
    '"ANTHROPIC_DEFAULT_OPUS_MODEL": "glm-5.2:cloud"' \
    '"ANTHROPIC_DEFAULT_SONNET_MODEL": "minimax-m3:cloud"' \
    '"ANTHROPIC_DEFAULT_FABLE_MODEL": "kimi-k2.7-code:cloud"' \
    '"ANTHROPIC_CUSTOM_MODEL_OPTION": "extra:cloud"' \
    '"ANTHROPIC_DEFAULT_HAIKU_MODEL": "glm-5.2:cloud"' \
    '"ANTHROPIC_MODEL": "glm-5.2:cloud"' \
    '"CLAUDE_CODE_AUTO_COMPACT_WINDOW": "1000000"'; do
  if ! grep -qF -- "$v" <<<"$spec"; then
    spec_ok=0; spec_msg="$spec_msg missing=$v"
  fi
done
# NO companion env vars should appear (the proxy owns the picker labels).
for v in \
    'ANTHROPIC_DEFAULT_OPUS_MODEL_NAME' \
    'ANTHROPIC_DEFAULT_OPUS_MODEL_DESCRIPTION' \
    'ANTHROPIC_DEFAULT_OPUS_MODEL_SUPPORTED_CAPABILITIES' \
    'ANTHROPIC_DEFAULT_SONNET_MODEL_NAME' \
    'ANTHROPIC_DEFAULT_SONNET_MODEL_DESCRIPTION' \
    'ANTHROPIC_DEFAULT_SONNET_MODEL_SUPPORTED_CAPABILITIES' \
    'ANTHROPIC_DEFAULT_FABLE_MODEL_NAME' \
    'ANTHROPIC_DEFAULT_FABLE_MODEL_DESCRIPTION' \
    'ANTHROPIC_DEFAULT_FABLE_MODEL_SUPPORTED_CAPABILITIES' \
    'ANTHROPIC_CUSTOM_MODEL_OPTION_NAME' \
    'ANTHROPIC_CUSTOM_MODEL_OPTION_DESCRIPTION' \
    'ANTHROPIC_CUSTOM_MODEL_OPTION_SUPPORTED_CAPABILITIES' \
    'ANTHROPIC_DEFAULT_HAIKU_MODEL_NAME' \
    'ANTHROPIC_DEFAULT_HAIKU_MODEL_DESCRIPTION' \
    'ANTHROPIC_DEFAULT_HAIKU_MODEL_SUPPORTED_CAPABILITIES'; do
  if grep -qF -- "$v" <<<"$spec"; then
    spec_ok=0; spec_msg="$spec_msg unexpected_companion=$v"
  fi
done

# Subscription path: select the supervisor model without gateway or catalog env.
no_url_ok=1
no_url_msg=""
for v in \
    '"ANTHROPIC_DEFAULT_OPUS_MODEL"' \
    '"ANTHROPIC_DEFAULT_SONNET_MODEL"' \
    '"ANTHROPIC_DEFAULT_FABLE_MODEL"' \
    '"ANTHROPIC_CUSTOM_MODEL_OPTION"' \
    '"ANTHROPIC_BASE_URL"' \
    '"ANTHROPIC_AUTH_TOKEN"' \
    '"ANTHROPIC_API_KEY"' \
    '"ANTHROPIC_DEFAULT_HAIKU_MODEL"'; do
  if grep -qF -- "$v" <<<"$spec_no_url"; then
    no_url_ok=0; no_url_msg="$no_url_msg unexpected=$v"
  fi
done
if ! grep -qF '"CLAUDE_CONFIG_DIR"' <<<"$spec_no_url"; then
  no_url_ok=0; no_url_msg="$no_url_msg missing_CLAUDE_CONFIG_DIR"
fi
if ! jq -e '.env.ANTHROPIC_MODEL == "glm-5.2:cloud"' <<<"$spec_no_url" >/dev/null; then
  no_url_ok=0; no_url_msg="$no_url_msg missing_supervisor_model"
fi

# Missing catalog: no id-registration env (no-op merge), but base env still present.
no_cat_ok=1
no_cat_msg=""
for v in \
    '"ANTHROPIC_DEFAULT_OPUS_MODEL"' \
    '"ANTHROPIC_DEFAULT_SONNET_MODEL"' \
    '"ANTHROPIC_DEFAULT_FABLE_MODEL"' \
    '"ANTHROPIC_CUSTOM_MODEL_OPTION"'; do
  if grep -qF -- "$v" <<<"$spec_no_cat"; then
    no_cat_ok=0; no_cat_msg="$no_cat_msg unexpected=$v"
  fi
done
if ! grep -qF '"ANTHROPIC_BASE_URL": "http://localhost:11434"' <<<"$spec_no_cat"; then
  no_cat_ok=0; no_cat_msg="$no_cat_msg missing_base_url"
fi

# Unparseable catalog: same as missing.
bad_cat_ok=1
bad_cat_msg=""
for v in \
    '"ANTHROPIC_DEFAULT_OPUS_MODEL"' \
    '"ANTHROPIC_DEFAULT_SONNET_MODEL"' \
    '"ANTHROPIC_DEFAULT_FABLE_MODEL"' \
    '"ANTHROPIC_CUSTOM_MODEL_OPTION"'; do
  if grep -qF -- "$v" <<<"$spec_bad_cat"; then
    bad_cat_ok=0; bad_cat_msg="$bad_cat_msg unexpected=$v"
  fi
done

# 5-entry catalog: 3 slots + 1 custom option; 5th entry is not registered
# (the SDK can't accept it via set_config_option, but the orchestrator can
# still reach it via `cerebro <subcmd> --model <id>`).
spec5_ok=1
spec5_msg=""
for v in \
    '"ANTHROPIC_DEFAULT_OPUS_MODEL": "a:cloud"' \
    '"ANTHROPIC_DEFAULT_SONNET_MODEL": "b:cloud"' \
    '"ANTHROPIC_DEFAULT_FABLE_MODEL": "c:cloud"' \
    '"ANTHROPIC_CUSTOM_MODEL_OPTION": "d:cloud"'; do
  if ! grep -qF -- "$v" <<<"$spec_5"; then
    spec5_ok=0; spec5_msg="$spec5_msg missing=$v"
  fi
done
if grep -qF 'e:cloud' <<<"$spec_5"; then
  spec5_ok=0; spec5_msg="$spec5_msg overflow_leaked=e:cloud"
fi

if (( spec_ok )) && (( no_url_ok )) && (( no_cat_ok )) \
   && (( bad_cat_ok )) && (( spec5_ok )); then
  printf 'PASS  130e acp child spec registers catalog ids with SDK (3 slots + custom; no companions; gated on base URL)\n'; pass=$((pass + 1))
else
  printf 'FAIL  130e acp child spec id-registration [spec=%s no_url=%s no_cat=%s bad_cat=%s spec5=%s :: %s%s%s%s%s]\n' \
    "$spec_ok" "$no_url_ok" "$no_cat_ok" "$bad_cat_ok" "$spec5_ok" \
    "$spec_msg" "$no_url_msg" "$no_cat_msg" "$bad_cat_msg" "$spec5_msg"
  fail=$((fail + 1))
  failures+=("130e acp child spec id-registration :: $spec_msg $no_url_msg $no_cat_msg $bad_cat_msg $spec5_msg")
fi

# --- 130f. acp_server.py _rewrite_model_option replaces the upstream's
# `model` config option with a catalog-sourced one (catalog names/descriptions,
# no Anthropic tier labels), preserving the upstream's currentValue and
# leaving all other config options (mode, effort, etc.) untouched. A
# missing/unparseable/empty catalog leaves the upstream's model option
# unchanged (the subscription path keeps its stock anthropic picker). ---
acp_py_ok=1
acp_py_msg=""
ACP_REWRITE_PY=""
for _cand in /opt/homebrew/bin/python3 python3 python3.13 python3.12 python3.11 python3.10; do
  _p="$(command -v "$_cand" 2>/dev/null)" || continue
  if "$_p" -c 'import acp' 2>/dev/null; then ACP_REWRITE_PY="$_p"; break; fi
done
if [[ -n "$ACP_REWRITE_PY" ]]; then
  ACPTEST="$WORKDIR/acp-rewrite-test.py"
  cat > "$ACPTEST" <<'PYEOF'
import ast, json, os, sys, tempfile
from pathlib import Path
from typing import Optional
sys.path.insert(0, os.path.join(os.environ["CEREBRO_LIB_DIR"], "python"))
CEREBRO_HOME = os.environ["CEREBRO_HOME"]
_CATALOG_PATH = os.path.join(CEREBRO_HOME, "models-config.json")
_SUPERVISOR_MODEL = os.environ["CEREBRO_SUPERVISOR_MODEL"]
_HAS_CUSTOM_ENDPOINT = bool(os.environ.get("CEREBRO_CLAUDE_BASE_URL"))

from acp.schema import SessionConfigOptionSelect, SessionConfigSelectOption

source = ast.parse((Path(os.environ["CEREBRO_LIB_DIR"]) / "python" / "acp_server.py").read_text())
helpers = [node for node in source.body if isinstance(node, ast.FunctionDef)
           and node.name in ("_load_catalog", "_catalog_model_option", "_rewrite_model_option")]
exec(compile(ast.Module(body=helpers, type_ignores=[]), "acp_server.py", "exec"))

# Test 1: catalog present -> model option replaced with catalog entries.
upstream_model = SessionConfigOptionSelect(
    id="model", name="Model", description="AI model to use",
    category="model", type="select", current_value="claude-opus-4-8",
    options=[
        SessionConfigSelectOption(value="claude-opus-4-8", name="Claude Opus 4.8"),
        SessionConfigSelectOption(value="claude-sonnet-5", name="Claude Sonnet 5"),
    ],
)
mode_opt = SessionConfigOptionSelect(
    id="mode", name="Mode", category="mode", type="select",
    current_value="default",
    options=[SessionConfigSelectOption(value="default", name="Default")],
)
result = _rewrite_model_option([mode_opt, upstream_model])
model = [o for o in result if o.id == "model"][0]
mode = [o for o in result if o.id == "mode"][0]
# currentValue preserved from upstream.
assert model.current_value == "claude-opus-4-8", f"current_value={model.current_value}"
# Options are the catalog entries (4 of them in this fixture).
vals = [(o.value, o.name, o.description) for o in model.options]
assert vals == [
    ("glm-5.2:cloud", "GLM 5.2", "Most advanced model; 1M context."),
    ("minimax-m3:cloud", "MiniMax M3", "Fast model with vision."),
    ("kimi-k2.7-code:cloud", "Kimi K2.7 Code", "Second-best, 256K context."),
    ("extra:cloud", "Extra", "4th entry -> custom model option."),
], f"options={vals}"
# Mode option untouched.
assert mode.current_value == "default"
assert len(mode.options) == 1
# No Anthropic tier labels in the model option's option names.
for o in model.options:
    assert "Opus" not in o.name and "Sonnet" not in o.name and "Haiku" not in o.name

# Test 2: empty catalog -> upstream's model option passed through unchanged.
empty_home = tempfile.mkdtemp()
os.environ["CEREBRO_HOME"] = empty_home
_CATALOG_PATH = os.path.join(empty_home, "models-config.json")
result2 = _rewrite_model_option([mode_opt, upstream_model])
model2 = [o for o in result2 if o.id == "model"][0]
assert model2 is upstream_model, "empty catalog should pass through unchanged"
assert any(o.value == "claude-opus-4-8" for o in model2.options)

# Test 3: None config_options -> returned as-is.
assert _rewrite_model_option(None) is None

# Test 4: upstream has no model option -> one is injected. Reset
# _CATALOG_PATH to the real catalog first (test 2 pointed it at an empty dir).
_CATALOG_PATH = os.path.join(os.environ["CEREBRO_HOME_ORIG"], "models-config.json")
result4 = _rewrite_model_option([mode_opt])
assert any(o.id == "model" for o in result4), "model option should be injected"
assert result4[-1].current_value == _SUPERVISOR_MODEL, "picker must select the supervisor model"

# Test 5: subscription path (no CEREBRO_CLAUDE_BASE_URL) -> upstream's
# Anthropic picker is passed through UNCHANGED, even when a catalog exists.
# The catalog models aren't available on the real Anthropic API, so the
# editor must see the SDK's stock Anthropic models.
_HAS_CUSTOM_ENDPOINT = False
result5 = _rewrite_model_option([upstream_model])
model5 = [o for o in result5 if o.id == "model"][0]
assert model5 is upstream_model, "subscription path should pass through unchanged"
assert any(o.value == "claude-opus-4-8" for o in model5.options), "stock anthropic models should be present"
assert not any(o.value == "glm-5.2:cloud" for o in model5.options), "catalog models should NOT be present on subscription path"
_HAS_CUSTOM_ENDPOINT = True

print("OK")
PYEOF
  acp_out="$(CEREBRO_LIB_DIR="$here/../lib" CEREBRO_HOME="$MDLACP" \
    CEREBRO_HOME_ORIG="$MDLACP" \
    CEREBRO_CLAUDE_BASE_URL=http://localhost:11434 \
    CEREBRO_MODEL=implementation-native CEREBRO_SUPERVISOR_MODEL=glm-5.2:cloud \
    "$ACP_REWRITE_PY" "$ACPTEST" 2>&1)"
  if [[ "$acp_out" == "OK" ]]; then
    printf 'PASS  130f acp_server _rewrite_model_option: catalog-driven picker, no Anthropic labels, preserves currentValue\n'; pass=$((pass + 1))
  else
    printf 'FAIL  130f acp_server _rewrite_model_option [out=%s]\n' "$acp_out"; fail=$((fail + 1))
    failures+=("130f acp_server _rewrite_model_option :: $acp_out")
  fi
else
  printf 'SKIP  130f acp_server _rewrite_model_option (agent-client-protocol SDK / Python unavailable)\n'
fi

# claude reviewer stub: emulate `claude -p` for the read-only reviewer -- log
# argv (to assert -p / review model / read-only allowedTools) and emit a
# stream-json event stream whose result text is the findings. The task prompt
# arrives on stdin (discarded).
CLAUDE_STUB_DIR="$WORKDIR/claude-review-stub"
mkdir -p "$CLAUDE_STUB_DIR"
CLAUDE_REVIEW_ARGV="$WORKDIR/claude-review-argv.log"
CRSID="CLAUDESESS-1"
cat > "$CLAUDE_STUB_DIR/claude" <<EOF
#!/usr/bin/env bash
python3 -c 'import json,sys; open(sys.argv[1],"a").write(json.dumps(sys.argv[2:])+"\n")' "$CLAUDE_REVIEW_ARGV" "\$@"
cat >/dev/null
printf '{"type":"system","subtype":"init","session_id":"%s"}\n' "$CRSID"
printf '{"type":"result","subtype":"success","result":"## Findings: no issues found"}\n'
exit 0
EOF
chmod +x "$CLAUDE_STUB_DIR/claude"

if [[ -x "$CLAUDE_STUB_DIR/claude" ]]; then
  CLAUDE_STUB_PATH="$CLAUDE_STUB_DIR:$PATH"
  CSESS="claude-review-session"; CDIR="$CEREBRO_HOME/sessions/$CSESS"
  initialize_session "$CDIR" claude

  # --- 129f. CEREBRO_BACKEND=claude runs `claude -p` on the review
  # model with the read-only allowedTools and records provider=claude. ---
  : > "$CLAUDE_REVIEW_ARGV"
  crev_out="$(env PATH="$CLAUDE_STUB_PATH" CEREBRO_BACKEND=claude CEREBRO_REVIEW_MODEL=review-native \
    CEREBRO_SESSION_ID="$CSESS" "$CEREBRO_BIN" review "$REPO" 2>/dev/null)"
  crev_argv="$(cat "$CLAUDE_REVIEW_ARGV")"
  crev_id="$(jq -r '.[] | select(.provider=="claude" and .role=="review") | .id' \
    "$CDIR/child-sessions.json" 2>/dev/null)"
  if [[ "$crev_id" == "$CRSID" && -s "$crev_out" ]] \
     && grep -q 'no issues found' "$crev_out" \
     && jq -e 'index("-p") != null and index("--strict-mcp-config") != null
       and (index("--tools") as $i | .[$i+1]=="")
       and (index("--allowedTools") as $i | .[$i+1]=="mcp__cerebro__command")
       and (index("--permission-mode") as $i | .[$i+1]=="dontAsk")
       and (index("--model") as $i | .[$i+1]=="review-native")' "$CLAUDE_REVIEW_ARGV" >/dev/null; then
    printf 'PASS  129f review under claude: claude -p + review model + read-only tools + provider=claude\n'; pass=$((pass + 1))
  else
    printf 'FAIL  129f review under claude [id=%s argv=%s out=%s]\n' "$crev_id" "$crev_argv" "$crev_out"; fail=$((fail + 1))
    failures+=("129f review under claude :: id=$crev_id argv=$crev_argv out=$crev_out")
  fi
else
  printf 'SKIP  129f review under claude (claude stub unavailable)\n'
fi

# ========================================================================
# 131-139. Pair-programming mode (--pair) uses the native Pi RPC process.
# The fixture sends real-shaped messages and agent_settled completion while
# production code owns the steering pipe, restart and session-file resume.
# ========================================================================
PAIR_STUB_DIR="$WORKDIR/pi-pair-stub"
install_pi_fixture "$PAIR_STUB_DIR" '{"text":"child working"}'

if [[ -x "$PAIR_STUB_DIR/pi" ]]; then
  PAIR_STUB_PATH="$PAIR_STUB_DIR:$PATH"
  PSESS="pair-session"; PDIR="$CEREBRO_HOME/sessions/$PSESS"
  initialize_session "$PDIR"

  # Background driver: wait for the child's steering pipe to appear, then inject
  # one steering message with `cerebro steer "<msg>"` (auto-discovers the single
  # live child), retrying until it lands in .steering.md. A steer is queued the
  # instant it reaches the live pipe and applied at the next idle window, so we
  # fire as soon as the pipe exists rather than racing a single short window.
  pair_drive() {
    local steerout="$1" f="" sp i j
    for i in $(seq 1 1000); do
      f="$(ls "$PDIR"/children/*.steer.fifo 2>/dev/null | head -1)"
      [[ -n "$f" ]] && break
      sleep 0.05
    done
    [[ -n "$f" ]] || return 0
    sp="${f%.steer.fifo}.steering.md"
    # Keep firing until it lands. A steer to a not-yet-live pipe is a harmless
    # no-op; once the pump is listening the steer queues and is applied at the
    # next idle. Re-checking before each fire bounds duplicates to the apply lag.
    for i in $(seq 1 200); do
      grep -q 'hashmap' "$sp" 2>/dev/null && break
      "$CEREBRO_BIN" steer "actually use a hashmap here" >"$steerout" 2>&1
      sleep 0.3
    done
  }

  # --- 131-133b. execute --pair: live one-shot steer round trip ---
  pair_drive "$WORKDIR/steer.out" &
  STEERER_PID=$!
  pout="$(env PATH="$PAIR_STUB_PATH" CEREBRO_SESSION_ID="$PSESS" CEREBRO_PAIR_IDLE=10 \
    "$CEREBRO_BIN" execute "$REPO" --prompt "do the work" --pair --worktree 2>"$WORKDIR/perr")"
  prc=$?
  wait "$STEERER_PID" 2>/dev/null
  perr="$(cat "$WORKDIR/perr")"

  # --- 131. execute --pair runs native RPC and produces a child log ---
  if [[ $prc -eq 0 && "$pout" == *".jsonl"* ]]; then
    printf 'PASS  131  execute --pair runs the child through native Pi RPC\n'; pass=$((pass + 1))
  else
    printf 'FAIL  131  execute --pair did not complete [rc=%d out=%s err=%s]\n' "$prc" "$pout" "$perr"; fail=$((fail + 1))
    failures+=("131 execute --pair :: rc=$prc")
  fi

  # --- 132. execute --pair folds the live steering into a PAIR STEERING block ---
  if [[ "$pout" == *"=== PAIR STEERING"* && "$pout" == *"actually use a hashmap here"* \
        && "$pout" != *"do the work"* ]]; then
    printf 'PASS  132  execute --pair folds back the live steering\n'; pass=$((pass + 1))
  else
    printf 'FAIL  132  execute --pair steering block wrong [out=%s]\n' "$pout"; fail=$((fail + 1))
    failures+=("132 execute --pair steering :: out=$pout")
  fi

  # --- 133. the steering is persisted to a .steering.md beside the child log ---
  clog="$(printf '%s\n' "$pout" | tail -1)"
  spath="${clog%.jsonl}.steering.md"
  if [[ -s "$spath" ]] && grep -q 'actually use a hashmap here' "$spath"; then
    printf 'PASS  133  steering persisted to .steering.md\n'; pass=$((pass + 1))
  else
    printf 'FAIL  133  steering file wrong [path=%s]\n' "$spath"; fail=$((fail + 1))
    failures+=("133 steering file :: path=$spath")
  fi

  # --- 133b. a pair_steering event was logged ---
  if grep -q '"what":"pair_steering"' "$PDIR/transcript.jsonl"; then
    printf 'PASS  133b  pair_steering event logged\n'; pass=$((pass + 1))
  else
    printf 'FAIL  133b  pair_steering event not logged\n'; fail=$((fail + 1))
    failures+=("133b pair_steering event missing")
  fi

  # --- 134. the pair banner identifies the child and both steering forms. ---
  pair_worktree="$(printf '%s\n' "$pout" | sed -n 's/^=== TASK WORKTREE: \(.*\) (branch .*$/\1/p' | head -1)"
  pair_session_label="cerebro:execute:$(basename "$pair_worktree")"
  if [[ -d "$pair_worktree" && "$perr" == *"PAIR MODE -- watch this execute"* \
        && "$perr" == *"session : $pair_session_label"* \
        && "$perr" == *'cerebro steer "<message>"'* \
        && "$perr" == *"cerebro steer ${clog%.jsonl}.steer.fifo"* ]]; then
    printf 'PASS  134  pair banner identifies the child and direct steering controls\n'; pass=$((pass + 1))
  else
    printf 'FAIL  134  pair banner wrong [perr=%s]\n' "$perr"; fail=$((fail + 1))
    failures+=("134 pair banner :: perr=$perr")
  fi

  # --- 134c. execute --pair: `cerebro restart` abandons the child + reverts ---
  RESTART_DIAG="wrong approach: rebuilt X instead of extending Y"
  restart_drive() {
    local f="" i j
    for i in $(seq 1 1000); do
      f="$(ls "$PDIR"/children/*.steer.fifo 2>/dev/null | head -1)"
      [[ -n "$f" ]] && break
      sleep 0.05
    done
    [[ -n "$f" ]] || return 0
    for i in $(seq 1 200); do
      [[ -e "${f%.steer.fifo}.restart" ]] && break
      "$CEREBRO_BIN" restart "$f" "$RESTART_DIAG" >/dev/null 2>&1 && break
      sleep 0.3
    done
  }

  restart_drive &
  RESTARTER_PID=$!
  rpout="$(env PATH="$PAIR_STUB_PATH" CEREBRO_SESSION_ID="$PSESS" CEREBRO_PAIR_IDLE=15 \
    "$CEREBRO_BIN" execute "$REPO" --prompt "do the work to restart" --pair 2>"$WORKDIR/rperr")"
  rprc=$?
  wait "$RESTARTER_PID" 2>/dev/null
  if [[ $rprc -eq 0 && "$rpout" == *"=== RESTART REQUESTED ==="* \
        && "$rpout" == *"$RESTART_DIAG"* \
        && "$rpout" == *"=== END RESTART REQUESTED ==="* ]]; then
    printf 'PASS  134c  execute --pair restart retains work + surfaces diagnosis\n'; pass=$((pass + 1))
  else
    printf 'FAIL  134c  execute --pair restart wrong [rc=%d out=%s]\n' \
      "$rprc" "$rpout"; fail=$((fail + 1))
    failures+=("134c execute --pair restart :: rc=$rprc")
  fi

  # --- 134d. after restart the child was reaped: its steer fifo is gone. ---
  rst_fifo="$(ls "$PDIR"/children/*.steer.fifo 2>/dev/null | head -1)"
  if [[ -z "$rst_fifo" ]]; then
    printf 'PASS  134d  restart reaped the child (steer fifo cleaned up)\n'; pass=$((pass + 1))
  else
    printf 'FAIL  134d  restart left a live steer fifo [%s]\n' "$rst_fifo"; fail=$((fail + 1))
    failures+=("134d restart fifo not cleaned :: $rst_fifo")
  fi

  # --- 135. an unpaired native child completes without a live-steering banner. ---
  env PATH="$PAIR_STUB_PATH" CEREBRO_SESSION_ID="$PSESS" \
    "$CEREBRO_BIN" execute "$REPO" --prompt "no pairing" >/dev/null 2>"$WORKDIR/nperr"
  nprc=$?
  npperr="$(cat "$WORKDIR/nperr")"
  if [[ $nprc -eq 0 && "$npperr" != *"PAIR MODE"* ]]; then
    printf 'PASS  135  default execute stays clean (no pair banner)\n'; pass=$((pass + 1))
  else
    printf 'FAIL  135  default execute leaked pair mode [rc=%d err=%s]\n' "$nprc" "$npperr"; fail=$((fail + 1))
    failures+=("135 default execute pair leak :: rc=$nprc")
  fi

  # --- 136. audit has no pair mode (read-only reviewer has no live-steer) ---
  pair_plan="$(env CEREBRO_SESSION_ID="$PSESS" \
    "$CEREBRO_BIN" plan "# Add a cache" --out pair-plan 2>/dev/null)"
  qerr="$(env CEREBRO_SESSION_ID="$PSESS" \
    "$CEREBRO_BIN" audit "$REPO" "$pair_plan" --pair 2>&1 >/dev/null)"
  qrc=$?
  if [[ $qrc -ne 0 && "$qerr" == *"unknown arg: --pair"* ]]; then
    printf 'PASS  136  audit rejects --pair (read-only reviewer has no live-steer)\n'; pass=$((pass + 1))
  else
    printf 'FAIL  136  audit --pair not rejected [rc=%d err=%s]\n' "$qrc" "$qerr"; fail=$((fail + 1))
    failures+=("136 audit --pair rejection :: rc=$qrc")
  fi

  # --- 137. apply-review --prompt --pair pairs on the current branch ---
  apout="$(env PATH="$PAIR_STUB_PATH" CEREBRO_SESSION_ID="$PSESS" CEREBRO_PAIR_IDLE=2 \
    "$CEREBRO_BIN" apply-review "$REPO" --prompt "tidy up" --pair 2>"$WORKDIR/aperr")"
  arc=$?
  if [[ $arc -eq 0 && "$apout" == *".jsonl"* && "$(cat "$WORKDIR/aperr")" == *"PAIR MODE"* ]]; then
    printf 'PASS  137  apply-review --pair runs the child through native Pi RPC\n'; pass=$((pass + 1))
  else
    printf 'FAIL  137  apply-review --pair wrong [rc=%d out=%s]\n' "$arc" "$apout"; fail=$((fail + 1))
    failures+=("137 apply-review --pair :: rc=$arc")
  fi

  # --- 139. a frozen paired child is reaped and relaunched (stall -> restart).
  # The first native process persists its user input then freezes; the pump
  # marks it stalled, and Cerebro resumes that same session in a new process.
  STALL_STATE="$WORKDIR/pair-stall-once.state"
  rm -f "$STALL_STATE"
  rstout="$(env PATH="$PAIR_STUB_PATH" CEREBRO_SESSION_ID="$PSESS" \
    FAKE_STALL_STATE="$STALL_STATE" \
    CEREBRO_PAIR_IDLE=1 CEREBRO_PAIR_STALL=1 CEREBRO_PAIR_STALL_BUSY=1 \
    CEREBRO_PAIR_STALL_RETRIES=1 CEREBRO_PAIR_STALL_BACKOFF=0 \
    "$CEREBRO_BIN" execute "$REPO" --prompt "stall once then resume" --pair 2>"$WORKDIR/rsterr")"
  rstrc=$?
  if [[ $rstrc -eq 0 && "$rstout" == *".jsonl"* ]] \
        && grep -q '"what":"pair_stall_restart"' "$PDIR/transcript.jsonl"; then
    printf 'PASS  139  paired stall reaps and restarts the child\n'; pass=$((pass + 1))
  else
    printf 'FAIL  139  paired stall did not restart [rc=%d out=%s err=%s]\n' \
      "$rstrc" "$rstout" "$(cat "$WORKDIR/rsterr")"; fail=$((fail + 1))
    failures+=("139 pair stall restart :: rc=$rstrc")
  fi

  # --- 139b. a rejected native prompt must propagate failure through the
  # stream pipeline and retain its diagnostic in the child error sidecar.
  RJSESS="pair-reject-session"; RJDIR="$CEREBRO_HOME/sessions/$RJSESS"
  initialize_session "$RJDIR"
  rjout="$(env PATH="$PAIR_STUB_PATH" CEREBRO_SESSION_ID="$RJSESS" \
    FAKE_REJECT_PROMPT=1 CEREBRO_PAIR_IDLE=1 \
    "$CEREBRO_BIN" execute "$REPO" --prompt "prompt that gets rejected" --pair \
    >"$WORKDIR/rjout" 2>"$WORKDIR/rjerr")"
  rjrc=$?
  rjerr="$(cat "$WORKDIR/rjerr")"
  rjerror="$(ls "$RJDIR"/children/*.err.log 2>/dev/null | head -1)"
  rjerrortxt="$(cat "$rjerror" 2>/dev/null || true)"
  if [[ $rjrc -ne 0 ]] \
     && grep -q '"what":"execute_failed"' "$RJDIR/transcript.jsonl" \
     && ! grep -q '"what":"execute_finished"' "$RJDIR/transcript.jsonl" \
     && [[ "$rjerrortxt" == *"fixture prompt rejected"* && "$rjerr" == *"fixture prompt rejected"* ]]; then
    printf 'PASS  139b  rejected initial prompt surfaces a failure (not exit 0)\n'; pass=$((pass + 1))
  else
    printf 'FAIL  139b  rejected prompt did not surface failure [rc=%d err=%s sidecar=%s]\n' \
      "$rjrc" "$rjerr" "$rjerrortxt"; fail=$((fail + 1))
    failures+=("139b rejected prompt :: rc=$rjrc")
  fi
else
  for t in 131 132 133 133b 134 134c 134d 134e 135 136 137 139 139b; do
    printf 'SKIP  %s  pair-mode (Pi fixture unavailable)\n' "$t"
  done
fi

# ========================================================================
# 140-143. Resume-on-continue for in-flight children. A child's resumable id
# is persisted the instant it starts (not on success), entries carry a
# running/done status, and `cerebro status` surfaces still-running (=
# interrupted) children so the orchestrator can resume them on continue.
# ========================================================================

# --- 141. cerebro status surfaces a still-running (interrupted) child with a
# resume hint. No stub needed: we seed a fresh running entry directly. ---
SSESS="status-inflight-session"; SDIR="$CEREBRO_HOME/sessions/$SSESS"
initialize_session "$SDIR"
STATUS_PI_SESSION="$WORKDIR/status-native.jsonl"; seed_pi_session "$STATUS_PI_SESSION"
jq -n --arg k deadbeefdeadbeef --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
   --arg repo "$REPO" --arg id "$STATUS_PI_SESSION" \
   '{($k): {id:$id, provider:"pi", role:"execute", repo:$repo,
            branch:"feat/wip", log:"/tmp/x.jsonl", status:"running",
            started_at:$ts, updated_at:$ts}}' \
   > "$SDIR/child-sessions.json"
sout="$(env CEREBRO_SESSION_ID="$SSESS" "$CEREBRO_BIN" status 2>/dev/null)"
if grep -q 'interrupted / in-flight children' <<<"$sout" \
   && grep -q 'feat/wip' <<<"$sout" \
   && grep -q 'resume:' <<<"$sout"; then
  printf 'PASS  141  status lists interrupted in-flight children with a resume hint\n'; pass=$((pass + 1))
else
  printf 'FAIL  141  status missing in-flight section [out=%s]\n' "$sout"; fail=$((fail + 1))
  failures+=("141 status in-flight :: out=$sout")
fi

# --- 142. a stale (over-TTL) running entry is NOT listed as in-flight. ---
jq -n --arg k cafecafecafecafe --arg id "$STATUS_PI_SESSION" \
   '{($k): {id:$id, provider:"pi", role:"execute", repo:"/r",
            branch:"feat/old", log:"/tmp/o.jsonl", status:"running",
            started_at:"2000-01-01T00:00:00Z", updated_at:"2000-01-01T00:00:00Z"}}' \
   > "$SDIR/child-sessions.json"
sout2="$(env CEREBRO_SESSION_ID="$SSESS" "$CEREBRO_BIN" status 2>/dev/null)"
if ! grep -q 'feat/old' <<<"$sout2"; then
  printf 'PASS  142  status omits stale (over-TTL) in-flight children\n'; pass=$((pass + 1))
else
  printf 'FAIL  142  status listed a stale in-flight child [out=%s]\n' "$sout2"; fail=$((fail + 1))
  failures+=("142 stale in-flight listed")
fi

if (( STUB_OK )); then
  # --- 140. a successful execute marks its child status=done (so it does NOT
  # show up as interrupted), and the id is recorded. ---
  DSESS="done-status-session"; DDIR="$CEREBRO_HOME/sessions/$DSESS"
  initialize_session "$DDIR"
  env PATH="$ID_STUB_PATH" CEREBRO_SESSION_ID="$DSESS" \
    "$CEREBRO_BIN" execute "$REPO" --prompt "do it" --branch feat/done >/dev/null 2>&1
  dstatus="$(jq -r '.[].status' "$DDIR/child-sessions.json" 2>/dev/null)"
  dlist="$(env CEREBRO_SESSION_ID="$DSESS" "$CEREBRO_BIN" status 2>/dev/null \
           | sed -n '/in-flight children/,/last review/p')"
  if [[ "$dstatus" == "done" ]] && ! grep -q 'feat/done' <<<"$dlist"; then
    printf 'PASS  140  successful execute marks status=done (off the in-flight list)\n'; pass=$((pass + 1))
  else
    printf 'FAIL  140  execute did not mark done [status=%s list=%s]\n' "$dstatus" "$dlist"; fail=$((fail + 1))
    failures+=("140 done status :: status=$dstatus")
  fi

  # --- 143. apply-review does not auto-resume a completed same-branch child. ---
  ARSESS="apply-resume-session"; ARDIR="$CEREBRO_HOME/sessions/$ARSESS"
  initialize_session "$ARDIR"
  env PATH="$ID_STUB_PATH" CEREBRO_SESSION_ID="$ARSESS" \
    "$CEREBRO_BIN" apply-review "$REPO" --prompt "first fix" >/dev/null 2>&1
  previous_apply_id="$(jq -r '.[].id' "$ARDIR/child-sessions.json")"
  env PATH="$ID_STUB_PATH" CEREBRO_SESSION_ID="$ARSESS" \
    "$CEREBRO_BIN" apply-review "$REPO" --prompt "second fix" >/dev/null 2>&1
  if ! grep -qF "resume=$previous_apply_id" "$ARDIR/transcript.jsonl" \
     && [[ "$(jq -r '.[].id' "$ARDIR/child-sessions.json")" != "$previous_apply_id" ]]; then
    printf 'PASS  143  completed apply-review child is not auto-resumed\n'; pass=$((pass + 1))
  else
    printf 'FAIL  143  apply-review resumed a completed child [transcript=%s]\n' "$(cat "$ARDIR/transcript.jsonl")"; fail=$((fail + 1))
    failures+=("143 apply-review completed auto-resume")
  fi

  # --- 144. doc-write likewise starts fresh after a completed same-branch child. ---
  DWSESS="doc-resume-session"; DWDIR="$CEREBRO_HOME/sessions/$DWSESS"
  initialize_session "$DWDIR"
  env PATH="$ID_STUB_PATH" CEREBRO_SESSION_ID="$DWSESS" \
    "$CEREBRO_BIN" doc-write "$REPO" --prompt "doc pass one" >/dev/null 2>&1
  previous_doc_id="$(jq -r '.[].id' "$DWDIR/child-sessions.json")"
  env PATH="$ID_STUB_PATH" CEREBRO_SESSION_ID="$DWSESS" \
    "$CEREBRO_BIN" doc-write "$REPO" --prompt "doc pass two" >/dev/null 2>&1
  if ! grep -qF "resume=$previous_doc_id" "$DWDIR/transcript.jsonl" \
     && [[ "$(jq -r '.[].id' "$DWDIR/child-sessions.json")" != "$previous_doc_id" ]]; then
    printf 'PASS  144  completed doc-write child is not auto-resumed\n'; pass=$((pass + 1))
  else
    printf 'FAIL  144  doc-write resumed a completed child [transcript=%s]\n' "$(cat "$DWDIR/transcript.jsonl")"; fail=$((fail + 1))
    failures+=("144 doc-write completed auto-resume")
  fi
else
  for t in 140 143 144; do
    printf 'SKIP  %s  child resume-on-continue (Pi fixture unavailable)\n' "$t"
  done
fi

# ========================================================================
# 150-156. `cerebro answer` -- resume a paused child with an answer.
# Validation/resolution paths fire before any pi invocation; the
# stub-backed cases verify the resume actually happens.
# ========================================================================

# --- 150. validation: empty answer ---
STDERR_CONTAINS="empty answer" \
run_case 150 "answer empty-answer rejected" 1 -- "$CEREBRO_BIN" answer CHILD-123

# --- 151. validation: old selector flags are no longer accepted ---
STDERR_CONTAINS="unknown arg" \
run_case 151 "answer selector flags rejected" 1 -- "$CEREBRO_BIN" answer CHILD-123 "go" --role bogus

# --- 152. validation: missing child id ---
STDERR_CONTAINS="usage" \
run_case 152 "answer missing child id rejected" 1 -- "$CEREBRO_BIN" answer

# --- 153. no stored child session with that id in the current parent session ---
STDERR_CONTAINS="no fresh child session" \
run_case 153 "answer unknown child session" 1 -- "$CEREBRO_BIN" answer NO-SUCH-CHILD "go"

if (( STUB_OK )); then
  # Fresh and resumed answers share the native session/prompt transport.
  # On resume, the stream carries the requested stored session ID.
  ANSWER_STUB_DIR="$WORKDIR/pi-answer-stub"
  mkdir -p "$ANSWER_STUB_DIR"
  install_pi_fixture "$ANSWER_STUB_DIR" '{}'
  ANSWER_STUB_PATH="$ANSWER_STUB_DIR:$PATH"

  # --- 154. answer resolves the child session id and resumes it ---
  ANSESS="answer-session"; ANDIR="$CEREBRO_HOME/sessions/$ANSESS"
  initialize_session "$ANDIR"
  # Seed a stored execute session for this repo.
  env PATH="$ANSWER_STUB_PATH" CEREBRO_SESSION_ID="$ANSESS" \
    "$CEREBRO_BIN" execute "$REPO" --prompt "do the thing" --branch feat/ans >/dev/null 2>&1
  answer_session="$(jq -r '.[].id' "$ANDIR/child-sessions.json")"
  ans_out="$(env PATH="$ANSWER_STUB_PATH" CEREBRO_SESSION_ID="$ANSESS" \
    "$CEREBRO_BIN" answer "$answer_session" "use option B" 2>/dev/null)"
  if grep -q '"what":"answer_started"' "$ANDIR/transcript.jsonl" \
     && grep -qF "resume=$answer_session" "$ANDIR/transcript.jsonl"; then
    printf 'PASS  154  answer resumes the stored execute session\n'; pass=$((pass + 1))
  else
    printf 'FAIL  154  answer did not resume [transcript=%s]\n' "$(cat "$ANDIR/transcript.jsonl")"; fail=$((fail + 1))
    failures+=("154 answer resume")
  fi

  # --- 155. answer surfaces the child's closing message and child id on stdout ---
  if [[ "$ans_out" == *"child closing message"* && "$ans_out" == *"child session: $answer_session"* \
        && "$ans_out" == *"ok"* ]]; then
    printf 'PASS  155  answer surfaces the child closing message\n'; pass=$((pass + 1))
  else
    printf 'FAIL  155  answer did not surface closing message [out=%s]\n' "$ans_out"; fail=$((fail + 1))
    failures+=("155 answer surface :: out=$ans_out")
  fi

  # --- 155b. answer targets the exact child session id when several plans
  # share one branch. ---
  AXSESS="answer-exact-session"; AXDIR="$CEREBRO_HOME/sessions/$AXSESS"
  initialize_session "$AXDIR"
  AXP1="$AXDIR/plans/one.md"; AXP2="$AXDIR/plans/two.md"
  printf 'one\n' > "$AXP1"; printf 'two\n' > "$AXP2"
  AXK1="$(printf '%s\0execute\0branch:feat/ans|plan:%s' "$REPO" "$AXP1" | shasum | cut -d' ' -f1 | cut -c1-16)"
  AXK2="$(printf '%s\0execute\0branch:feat/ans|plan:%s' "$REPO" "$AXP2" | shasum | cut -d' ' -f1 | cut -c1-16)"
  AXID1="$ANSWER_STUB_DIR/child-one.jsonl"; seed_pi_session "$AXID1"
  AXID2="$ANSWER_STUB_DIR/child-two.jsonl"; seed_pi_session "$AXID2"
  jq -n --arg k1 "$AXK1" --arg k2 "$AXK2" --arg repo "$REPO" \
        --arg id1 "$AXID1" --arg id2 "$AXID2" \
        --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        '{($k1): {id:$id1, provider:"pi", role:"execute", repo:$repo,
                  branch:"feat/ans", status:"done", updated_at:$ts},
          ($k2): {id:$id2, provider:"pi", role:"execute", repo:$repo,
                  branch:"feat/ans", status:"done", updated_at:$ts}}' \
        > "$AXDIR/child-sessions.json"
  for answer_key in "$AXK1" "$AXK2"; do
    python3 "$here/../lib/python/task_workspace.py" prepare "$REPO" "" feat/ans "" \
      "$AXDIR/child-sessions.json" "$answer_key" 0 >/dev/null
  done
  env PATH="$ID_STUB_PATH" CEREBRO_SESSION_ID="$AXSESS" \
    "$CEREBRO_BIN" answer "$AXID2" "use option B" >/dev/null 2>&1
  if grep -qF "resume=$AXID2" "$AXDIR/transcript.jsonl" \
     && ! grep -qF "resume=$AXID1" "$AXDIR/transcript.jsonl"; then
    printf 'PASS  155b answer targets exact same-branch plan child\n'; pass=$((pass + 1))
  else
    printf 'FAIL  155b answer did not target exact child [transcript=%s]\n' \
      "$(cat "$AXDIR/transcript.jsonl")"; fail=$((fail + 1))
    failures+=("155b answer exact child")
  fi

  # --- 156. answer rejects non-answerable child sessions such as a review/audit
  # child (only execute / apply-review / doc-write children pause for answers). ---
  CSESS="answer-review-session"; CSDIR="$CEREBRO_HOME/sessions/$CSESS"
  initialize_session "$CSDIR"
  ANSWER_REVIEW_ID="$ANSWER_STUB_DIR/audit.jsonl"; seed_pi_session "$ANSWER_REVIEW_ID"
  jq -n --arg repo "$REPO" --arg id "$ANSWER_REVIEW_ID" --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        '{auditkey: {id:$id, provider:"pi", role:"audit", repo:$repo,
                    branch:"audit", status:"done", updated_at:$ts}}' \
        > "$CSDIR/child-sessions.json"
  STDERR_CONTAINS="not an answerable pi child" \
  run_case 156 "answer review/audit child rejected" 1 -- \
    env CEREBRO_SESSION_ID="$CSESS" "$CEREBRO_BIN" answer "$ANSWER_REVIEW_ID" "go"
else
  for t in 154 155 155b 156; do
    printf 'SKIP  %s  answer resume (Pi fixture unavailable)\n' "$t"
  done
fi

# --- 157. parse_stream survives a closed stderr preview pipe. ---
PRS="$WORKDIR/parse-stream-result"
PIS="$WORKDIR/parse-stream-id"
PARSE_SESSION="$WORKDIR/parser-native.jsonl"; seed_pi_session "$PARSE_SESSION"
{
  jq -cn --arg id "$PARSE_SESSION" '{type:"session.started",session_id:$id}'
  printf '%s\n' '{"type":"tool_execution_start","toolCallId":"c1","toolName":"bash","args":{"command":"echo parse"}}'
  printf '%s\n' '{"type":"message_end","message":{"role":"assistant","content":[{"type":"text","text":"ok"}],"stopReason":"stop"}}'
  printf '%s\n' '{"type":"agent_settled"}'
} | { python3 "$here/../lib/python/parse_stream.py" "$PRS" "$PIS" "" "" pi 2>&1; } | python3 -c 'pass' >/dev/null
prsrc=$?
if [[ $prsrc -eq 0 && "$(cat "$PRS" 2>/dev/null)" == "ok" && "$(cat "$PIS" 2>/dev/null)" == "$PARSE_SESSION" ]]; then
  printf 'PASS  157  parse_stream survives closed stderr preview pipe\n'; pass=$((pass + 1))
else
  printf 'FAIL  157  parse_stream died on closed stderr [rc=%d result=%s id=%s]\n' \
    "$prsrc" "$(cat "$PRS" 2>/dev/null)" "$(cat "$PIS" 2>/dev/null)"; fail=$((fail + 1))
  failures+=("157 parse_stream closed stderr :: rc=$prsrc")
fi

# --- 157a. parse_stream exits 5 (child stalled) after the inactivity window
# with no new events. Uses a small CEREBRO_CHILD_IDLE_TIMEOUT so the test runs
# fast; emits one event, then holds the pipe open with a sleep so the parser
# sees EOF only after the stall fires. ---
PRS2="$WORKDIR/parse-stall-result"
PIS2="$WORKDIR/parse-stall-id"
# Pipe one event, then sleep (holding stdin open) so the inactivity timer
# fires before EOF. Bound the whole thing so a bug that never stalls still
# terminates.
( printf '%s\n' '{"type":"agent_start"}'
  sleep 30 ) | env CEREBRO_CHILD_IDLE_TIMEOUT=2 \
  python3 "$here/../lib/python/parse_stream.py" "$PRS2" "$PIS2" "" "" pi >"$WORKDIR/stall-out" 2>"$WORKDIR/stall-err" &
STALL_PID=$!
# Poll up to ~8s for the parser to exit on the stall.
stall_rc=""
for _ in 1 2 3 4 5 6 7 8; do
  sleep 1
  if ! kill -0 "$STALL_PID" 2>/dev/null; then
    wait "$STALL_PID"; stall_rc=$?
    break
  fi
done
if [[ -z "$stall_rc" ]]; then
  kill "$STALL_PID" 2>/dev/null; wait "$STALL_PID" 2>/dev/null
  printf 'FAIL  157a parse_stream never stalled\n'; fail=$((fail + 1))
  failures+=("157a parse_stream stall :: never exited")
elif [[ "$stall_rc" == "5" ]] \
   && grep -q 'child stalled' "$WORKDIR/stall-err"; then
  printf 'PASS  157a parse_stream exits 5 on inactivity stall\n'; pass=$((pass + 1))
else
  printf 'FAIL  157a parse_stream stall [rc=%s err=%s]\n' \
    "$stall_rc" "$(cat "$WORKDIR/stall-err" 2>/dev/null)"; fail=$((fail + 1))
  failures+=("157a parse_stream stall :: rc=$stall_rc")
fi

# ========================================================================
# plan (orchestrator-written) and audit argument validation. `cerebro plan`
# spawns no child: it records markdown the orchestrator composed, like
# `spec set`. `cerebro audit` is the read-only child that checks a plan.
# ========================================================================

# --- 158. plan: blank content rejected ---
STDERR_CONTAINS="usage: cerebro plan" \
run_case 158 "plan blank content rejected" 1 -- "$CEREBRO_BIN" plan "   "

# --- 159. plan records the markdown and echoes the path ---
plan_out="$("$CEREBRO_BIN" plan "# My plan

Do the thing." --out my-plan 2>/dev/null)"
expected_plan_path="$(python3 -c 'from pathlib import Path; import sys; print(Path(sys.argv[1]).resolve())' "$CEREBRO_HOME/sessions/$CEREBRO_SESSION_ID/plans/my-plan.md")"
if [[ "$plan_out" == "$expected_plan_path" ]] \
   && grep -q '# My plan' "$plan_out" && grep -q 'Do the thing.' "$plan_out"; then
  printf 'PASS  159  plan records content and echoes the path\n'; pass=$((pass + 1))
else
  printf 'FAIL  159  plan did not record content [out=%s]\n' "$plan_out"; fail=$((fail + 1))
  failures+=("159 plan record :: out=$plan_out")
fi

# --- 160. plan: same --out overwrites (the revision flow) ---
"$CEREBRO_BIN" plan "# Revised plan" --out my-plan >/dev/null 2>&1
if grep -q '# Revised plan' "$plan_out" && ! grep -q 'Do the thing.' "$plan_out"; then
  printf 'PASS  160  plan --out same name overwrites the file\n'; pass=$((pass + 1))
else
  printf 'FAIL  160  plan --out did not overwrite [file=%s]\n' "$(cat "$plan_out" 2>/dev/null)"; fail=$((fail + 1))
  failures+=("160 plan overwrite")
fi

# --- 160a. plan --stdin records body verbatim (backticks/dollar signs literal) ---
stdin_out="$("$CEREBRO_BIN" plan --out stdin-plan --stdin <<'STDIN_EOF'
# Plan via stdin
Body with `backticks` and $dollar signs and "quotes".
A second line.
STDIN_EOF
)"
if [[ "$stdin_out" == *stdin-plan.md ]] \
   && grep -q '# Plan via stdin' "$stdin_out" \
   && grep -q '`backticks`' "$stdin_out" \
   && grep -q '\$dollar signs' "$stdin_out" \
   && grep -q 'A second line.' "$stdin_out"; then
  printf 'PASS  160a plan --stdin records body verbatim\n'; pass=$((pass + 1))
else
  printf 'FAIL  160a plan --stdin verbatim [out=%s file=%s]\n' \
    "$stdin_out" "$(cat "$stdin_out" 2>/dev/null)"; fail=$((fail + 1))
  failures+=("160a plan --stdin verbatim")
fi

# --- 160b. plan --stdin plus inline body is ambiguous ---
err="$("$CEREBRO_BIN" plan "inline body" --stdin <<'STDIN_EOF' 2>&1
stdin body
STDIN_EOF
)"
if [[ "$err" == *"mutually exclusive"* ]]; then
  printf 'PASS  160b plan --stdin + inline body rejected\n'; pass=$((pass + 1))
else
  printf 'FAIL  160b plan --stdin + inline body not rejected [err=%s]\n' "$err"; fail=$((fail + 1))
  failures+=("160b plan --stdin ambiguous")
fi

# --- 160c. plan --stdin with empty body errors ---
err="$(: | "$CEREBRO_BIN" plan --stdin 2>&1)"
if [[ "$err" == *"usage: cerebro plan"* ]]; then
  printf 'PASS  160c plan --stdin empty rejected\n'; pass=$((pass + 1))
else
  printf 'FAIL  160c plan --stdin empty not rejected [err=%s]\n' "$err"; fail=$((fail + 1))
  failures+=("160c plan --stdin empty")
fi

# --- 161. audit: missing/empty plan file rejected ---
STDERR_CONTAINS="plan file missing or empty" \
run_case 161 "audit missing plan rejected" 1 -- \
  "$CEREBRO_BIN" audit "$REPO" "$WORKDIR/no-such-plan.md"

# --- 162. audit: relative repo path rejected ---
STDERR_CONTAINS="must be absolute" \
run_case 162 "audit relative repo rejected" 1 -- \
  "$CEREBRO_BIN" audit relative "$plan_out"

# --- 163. audit: unknown arg rejected ---
STDERR_CONTAINS="unknown arg" \
run_case 163 "audit unknown arg rejected" 1 -- \
  "$CEREBRO_BIN" audit "$REPO" "$plan_out" --bogus

# --- 164. plans rm deletes a plan dropped from a suite ---
"$CEREBRO_BIN" plans rm my-plan >/dev/null 2>&1
plans_list="$("$CEREBRO_BIN" plans 2>/dev/null)"
if [[ ! -e "$plan_out" && "$plans_list" != *"my-plan.md"* ]]; then
  printf 'PASS  164  plans rm deletes the plan file\n'; pass=$((pass + 1))
else
  printf 'FAIL  164  plans rm left the plan behind [list=%s]\n' "$plans_list"; fail=$((fail + 1))
  failures+=("164 plans rm")
fi

# --- 165. plans rm: missing plan rejected ---
STDERR_CONTAINS="no such plan" \
run_case 165 "plans rm missing plan rejected" 1 -- "$CEREBRO_BIN" plans rm nope

# --- 166. plans rm is confined to the session plans dir ---
printf 'outside\n' > "$WORKDIR/outside.md"
STDERR_CONTAINS="no such plan" \
run_case 166 "plans rm path-confined to plans dir" 1 -- \
  "$CEREBRO_BIN" plans rm "$WORKDIR/outside.md"
if [[ -e "$WORKDIR/outside.md" ]]; then
  printf 'PASS  166b plans rm did not touch a file outside the plans dir\n'; pass=$((pass + 1))
else
  printf 'FAIL  166b plans rm deleted an outside file\n'; fail=$((fail + 1))
  failures+=("166b plans rm escaped the plans dir")
fi

# ========================================================================
# 167-176. Harness overlays (user-owned, append-onto-shipped-prompts) and
# `cerebro improve` argument validation + prompt loader. Overlays live under
# $CEREBRO_HOME/overlays/ and are never materialised.
# ========================================================================

OVERLAY_DIR="$CEREBRO_HOME/overlays"

# --- 167. overlay set grader writes overlays/grader.md ---
run_case 167 "overlay set grader writes file" 0 -- \
  "$CEREBRO_BIN" overlay set grader "End every report with GRADER_X."
if [[ -s "$OVERLAY_DIR/grader.md" ]] && grep -q "GRADER_X" "$OVERLAY_DIR/grader.md"; then
  printf 'PASS  167b overlay set grader wrote overlays/grader.md\n'; pass=$((pass + 1))
else
  printf 'FAIL  167b overlay set grader did not write file\n'; fail=$((fail + 1))
  failures+=("167b overlay grader file missing")
fi

# --- 168. overlay show grader prints the body ---
STDOUT_CONTAINS="GRADER_X" \
run_case 168 "overlay show grader prints body" 0 -- "$CEREBRO_BIN" overlay show grader

# --- 168b. overlay show (no target) lists targets with present/absent ---
STDOUT_CONTAINS="grader" \
run_case 168b "overlay show lists all targets" 0 -- "$CEREBRO_BIN" overlay show

# --- 169. overlay rm grader removes the file ---
run_case 169 "overlay rm grader" 0 -- "$CEREBRO_BIN" overlay rm grader
if [[ ! -e "$OVERLAY_DIR/grader.md" ]]; then
  printf 'PASS  169b overlay rm removed overlays/grader.md\n'; pass=$((pass + 1))
else
  printf 'FAIL  169b overlay rm left the file behind\n'; fail=$((fail + 1))
  failures+=("169b overlay grader file not removed")
fi

# --- 170. invalid target rejected with the valid-target list ---
STDERR_CONTAINS="valid targets" \
run_case 170 "overlay set bogus target rejected" 1 -- \
  "$CEREBRO_BIN" overlay set bogus "x"

# --- 171. empty body rejected with usage ---
STDERR_CONTAINS="usage: cerebro overlay set" \
run_case 171 "overlay set empty body rejected" 1 -- \
  "$CEREBRO_BIN" overlay set system "   "

# --- 172. over-cap body rejected ---
BIG="$(head -c 5000 < /dev/zero | tr '\0' 'x')"
STDERR_CONTAINS="too large" \
run_case 172 "overlay set over-cap body rejected" 1 -- \
  "$CEREBRO_BIN" overlay set system "$BIG"

  # --- 173. materialise_home writes stable child skills that do NOT contain
  # user overlays (overlays are read on demand, not appended). Verify the
  # execute role prompt is byte-for-byte stable before and after setting/removing an
  # overlay. ---
  ov_fn() {  # run a lib function in a sourced subshell with the test env
    bash -c '
      set -uo pipefail
      CEREBRO_LIB_DIR="$1"; shift
      . "$CEREBRO_LIB_DIR/config.sh"
      . "$CEREBRO_LIB_DIR/helpers.sh"
      . "$CEREBRO_LIB_DIR/payloads.sh"
      . "$CEREBRO_LIB_DIR/session-store.sh"
      . "$CEREBRO_LIB_DIR/backend.sh"
      . "$CEREBRO_LIB_DIR/backend-pi.sh"
      . "$CEREBRO_LIB_DIR/backend-claude.sh"
      . "$CEREBRO_LIB_DIR/backend-codex.sh"
      for _f in "$CEREBRO_LIB_DIR"/commands/*.sh; do . "$_f"; done
      "$@"
    ' _ "$here/../lib" "$@"
  }

  ov_before="$(ov_fn child_sys_prompt execute 2>/dev/null)"
  "$CEREBRO_BIN" overlay set execute "ZZMARKER" >/dev/null 2>&1
  ov_with="$(ov_fn child_sys_prompt execute 2>/dev/null)"
  "$CEREBRO_BIN" overlay rm execute >/dev/null 2>&1
  ov_without="$(ov_fn child_sys_prompt execute 2>/dev/null)"
  if [[ "$ov_with" == "$ov_before" && "$ov_without" == "$ov_before" \
        && "$ov_with" != *"ZZMARKER"* ]]; then
    printf 'PASS  173  shared child role prompt is stable and ignores overlays\n'; pass=$((pass + 1))
  else
    printf 'FAIL  173  overlay loader wiring [with=%s without=%s]\n' \
      "${ov_with: -40}" "${ov_without: -40}"; fail=$((fail + 1))
    failures+=("173 overlay loader wiring")
  fi

# --- 174. materialise_home creates overlays/ but writes NO file into it. ---
MHOME="$WORKDIR/mhome"
mh_out="$(CEREBRO_HOME="$MHOME" ov_fn materialise_home 2>&1)"
if [[ -d "$MHOME/overlays" ]] && [[ -z "$(ls -A "$MHOME/overlays" 2>/dev/null)" ]]; then
  printf 'PASS  174  materialise_home creates empty overlays/ dir\n'; pass=$((pass + 1))
else
  printf 'FAIL  174  materialise_home overlays/ [exists=%s contents=%s out=%s]\n' \
    "$([[ -d "$MHOME/overlays" ]] && echo y || echo n)" \
    "$(ls -A "$MHOME/overlays" 2>/dev/null)" "$mh_out"; fail=$((fail + 1))
  failures+=("174 materialise_home overlays dir")
fi

# --- 174b. every backend resolves the same canonical skill body through its
# native discovery root; Claude links to the shared .agents skill directory.
sk174b_ok=1
for source in "$here/../lib/payloads/skills"/*/SKILL.md; do
  topic="$(basename "$(dirname "$source")")"
  canonical="$MHOME/.agents/skills/$topic/SKILL.md"
  claude="$MHOME/.claude/skills/$topic/SKILL.md"
  if [[ ! -s "$canonical" || ! -L "$MHOME/.claude/skills/$topic" ]] \
     || ! cmp -s "$canonical" "$claude" \
     || [[ "$(cat "$source")" != "$(cat "$canonical")" ]]; then
    sk174b_ok=0; break
  fi
done
if (( sk174b_ok )); then
  printf 'PASS  174b all backends resolve canonical shared skills\n'; pass=$((pass + 1))
else
  printf 'FAIL  174b shared skill materialisation mismatch [%s]\n' "$topic"; fail=$((fail + 1))
  failures+=("174b canonical shared skills :: $topic")
fi

# --- 174c. bootstrap uses the same supervisor source as on-demand discovery.
bootstrap="$(cat "$MHOME/system-prompt.md")"
shared_supervisor="$(ov_fn cerebro_skill_body "$MHOME/.agents/skills/cerebro-supervisor/SKILL.md")"
if [[ -n "$bootstrap" && "$bootstrap" == "$shared_supervisor" \
      && "$bootstrap" == *"Delegate development"* && "$bootstrap" == *"MCP"* ]]; then
  printf 'PASS  174c supervisor bootstrap and native skill share one source\n'; pass=$((pass + 1))
else
  printf 'FAIL  174c supervisor bootstrap contract mismatch\n'; fail=$((fail + 1))
  failures+=("174c supervisor bootstrap/source")
fi

# --- 174d. the Claude ACP wrapper exposes the same guarded command tool and
# the shared supervisor contract as terminal mode.
agent174d="$(ov_fn claude_orchestrator_agent_file 2>/dev/null)"
if [[ "$agent174d" == *"tools: mcp__cerebro__command"* && "$agent174d" == *"$shared_supervisor"* ]]; then
  printf 'PASS  174d Claude ACP wrapper uses the guarded command/supervisor contract\n'; pass=$((pass + 1))
else
  printf 'FAIL  174d Claude ACP wrapper contract mismatch\n'; fail=$((fail + 1))
  failures+=("174d Claude ACP wrapper")
fi

# --- 174e. every discovered skill has a native name matching its directory
# and a description, plus a nonempty loadable body.
sk174e_ok=1
for source in "$here/../lib/payloads/skills"/*/SKILL.md; do
  topic="$(basename "$(dirname "$source")")"
  if [[ "$(head -1 "$source")" != "---" ]] \
      || ! grep -q "^name: $topic$" "$source" \
      || ! grep -q '^description: .' "$source" \
      || [[ -z "$(ov_fn cerebro_skill_body "$source")" ]]; then
    sk174e_ok=0; break
  fi
done
if (( sk174e_ok )); then
  printf 'PASS  174e all shared skills have valid native discovery metadata/body\n'; pass=$((pass + 1))
else
  printf 'FAIL  174e invalid shared skill [%s]\n' "$topic"; fail=$((fail + 1))
  failures+=("174e shared skill metadata :: $topic")
fi

# --- 175. improve with no repo arg / non-absolute path errors with usage. ---
STDERR_CONTAINS="usage: cerebro improve" \
run_case 175 "improve no repo arg rejected" 1 -- "$CEREBRO_BIN" improve

STDERR_CONTAINS="must be absolute" \
run_case 175b "improve non-absolute repo rejected" 1 -- "$CEREBRO_BIN" improve relative/path

# --- 176. cerebro_improve_prompt composes the five meta-skill components
# (analyzer/retriever/allocator/proposer/evolver) after the readonly note,
# and includes a HILL CLIMB instruction. ---
imp_prompt="$(ov_fn cerebro_improve_prompt 2>/dev/null)"
if [[ "$imp_prompt" == *"HILL CLIMB"* ]] && [[ "$imp_prompt" == *"recur"* ]]; then
  printf 'PASS  176  cerebro_improve_prompt composes meta-skill components\n'; pass=$((pass + 1))
else
  printf 'FAIL  176  cerebro_improve_prompt missing meta-skill content\n'; fail=$((fail + 1))
  failures+=("176 cerebro_improve_prompt content")
fi

# --- 176c. utility and chronological-history helpers use realistic schemas. ---
improve_py_out="$(python3 "$here/improve_test.py" 2>&1)"
if [[ "$improve_py_out" == "all checks passed" ]]; then
  printf 'PASS  176c improve utility + chronological history fixtures\n'; pass=$((pass + 1))
else
  printf 'FAIL  176c improve utility/history [out=%s]\n' "$improve_py_out"; fail=$((fail + 1))
  failures+=("176c improve utility/history :: $improve_py_out")
fi

# --- 176d. finding counting supports plain lists and Markdown headings. ---
COUNT_FINDINGS="$WORKDIR/improve-count.md"
cat > "$COUNT_FINDINGS" <<'EOF'
1. Plain finding
## 2. Heading finding
### 3. Another heading finding
not a finding
EOF
counted="$(ov_fn improve_count_findings "$COUNT_FINDINGS" 2>/dev/null)"
if [[ "$counted" == 3 ]]; then
  printf 'PASS  176d improve finding counting supports list + heading formats\n'; pass=$((pass + 1))
else
  printf 'FAIL  176d improve finding count [got=%s]\n' "$counted"; fail=$((fail + 1))
  failures+=("176d improve finding count :: $counted")
fi

# Deterministic read-only reviewer used for isolated end-to-end improve flows.
# Its final text depends only on whether the visible prompt is fast or meta.
IMPROVE_STUB_DIR="$WORKDIR/improve-review-stub"
mkdir -p "$IMPROVE_STUB_DIR"
IMPROVE_STUB_LOG="$WORKDIR/improve-review-prompts.log"
install_pi_fixture "$IMPROVE_STUB_DIR" '{"mode":"improve"}'

improve_home() {
  local home="$1" session="$2"
  mkdir -p "$home/sessions/$session/children" "$home/overlays"
  : > "$home/sessions/$session/transcript.jsonl"
  printf '{"created_at":"2026-01-01T00:00:00Z","backend":"pi","role":"supervisor"}\n' > "$home/sessions/$session/metadata.json"
}

# --- 176e. H=2 fires on invocation two and emits fast then meta paths. ---
IMP_H2="$WORKDIR/improve-h2"; IMP_H2_SESSION="improve-h2-session"
improve_home "$IMP_H2" "$IMP_H2_SESSION"
: > "$IMPROVE_STUB_LOG"
h2_first="$(env PATH="$IMPROVE_STUB_DIR:$PATH" IMPROVE_STUB_LOG="$IMPROVE_STUB_LOG" \
  CEREBRO_HOME="$IMP_H2" CEREBRO_SESSION_ID="$IMP_H2_SESSION" CEREBRO_META_HORIZON=2 \
  "$CEREBRO_BIN" improve "$REPO" 2>/dev/null)"
h2_second="$(env PATH="$IMPROVE_STUB_DIR:$PATH" IMPROVE_STUB_LOG="$IMPROVE_STUB_LOG" \
  CEREBRO_HOME="$IMP_H2" CEREBRO_SESSION_ID="$IMP_H2_SESSION" CEREBRO_META_HORIZON=2 \
  "$CEREBRO_BIN" improve "$REPO" 2>/dev/null)"
h2_types="$(jq -r '.runs[].type' "$IMP_H2/improvement-history.json" 2>/dev/null | tr '\n' ' ')"
if [[ "$(printf '%s\n' "$h2_first" | grep -c '/improve.md$')" -eq 1 \
      && "$(printf '%s\n' "$h2_first" | grep -c 'meta-improve.md$')" -eq 0 \
      && "$(printf '%s\n' "$h2_second" | grep -cE '/(improve|meta-improve)\.md$')" -eq 2 \
      && "$h2_types" == "fast fast meta " ]]; then
  printf 'PASS  176e improve H=2 schedule includes current fast run\n'; pass=$((pass + 1))
else
  printf 'FAIL  176e improve H=2 [first=%s second=%s types=%s]\n' "$h2_first" "$h2_second" "$h2_types"; fail=$((fail + 1))
  failures+=("176e improve H=2 schedule")
fi

# --- 176f. H=1 and explicit --meta both run meta in the same invocation;
# model and meta-overlay content reach the reviewer prompts. ---
IMP_H1="$WORKDIR/improve-h1"; improve_home "$IMP_H1" "improve-h1-session"
: > "$IMPROVE_STUB_LOG"
h1_out="$(env PATH="$IMPROVE_STUB_DIR:$PATH" IMPROVE_STUB_LOG="$IMPROVE_STUB_LOG" \
  CEREBRO_HOME="$IMP_H1" CEREBRO_SESSION_ID="improve-h1-session" CEREBRO_META_HORIZON=1 \
  "$CEREBRO_BIN" improve "$REPO" --model test/reviewer 2>/dev/null)"
IMP_FORCE="$WORKDIR/improve-force"; improve_home "$IMP_FORCE" "improve-force-session"
printf 'META OVERLAY ROUTING MARKER\n' > "$IMP_FORCE/overlays/meta-analyzer.md"
: > "$IMPROVE_STUB_LOG"
force_out="$(env PATH="$IMPROVE_STUB_DIR:$PATH" IMPROVE_STUB_LOG="$IMPROVE_STUB_LOG" \
  CEREBRO_HOME="$IMP_FORCE" CEREBRO_SESSION_ID="improve-force-session" CEREBRO_META_HORIZON=99 \
  "$CEREBRO_BIN" improve "$REPO" --meta --model test/reviewer 2>/dev/null)"
if [[ "$(printf '%s\n' "$h1_out" | grep -cE '/(improve|meta-improve)\.md$')" -eq 2 \
      && "$(printf '%s\n' "$force_out" | grep -cE '/(improve|meta-improve)\.md$')" -eq 2 \
      && "$(grep -c 'META OVERLAY ROUTING MARKER' "$IMPROVE_STUB_LOG")" -eq 2 \
      && "$(jq -s '[.[] | select(.argv | type == "array") | .argv | index("--model") as $i | select($i != null and .[$i+1] == "test/reviewer")] | length' "$IMPROVE_STUB_LOG")" -eq 2 \
      && "$(grep -c 'META-LOOP' "$IMPROVE_STUB_LOG")" -eq 1 ]]; then
  printf 'PASS  176f improve H=1/--meta + overlay/meta/model prompt routing\n'; pass=$((pass + 1))
else
  printf 'FAIL  176f improve H=1/force routing [h1=%s force=%s log=%s]\n' \
    "$h1_out" "$force_out" "$IMPROVE_STUB_LOG"; fail=$((fail + 1))
  failures+=("176f improve H=1/force routing")
fi

# --- 176g. stale files, empty/malformed output, and reviewer failures are
# never surfaced as successful findings paths. ---
improve_failure_case() {
  local mode="$1"
  local home="$WORKDIR/improve-$mode" session="improve-$mode-session"
  improve_home "$home" "$session"
  mkdir -p "$home/sessions/$session/improvements"
  printf 'STALE\nHILL CLIMB: ISSUES FOUND\n' > "$home/sessions/$session/improvements/improve.md"
  local out rc
  out="$(env PATH="$IMPROVE_STUB_DIR:$PATH" IMPROVE_STUB_LOG="$IMPROVE_STUB_LOG" \
    IMPROVE_STUB_MODE="$mode" CEREBRO_HOME="$home" CEREBRO_SESSION_ID="$session" \
    CEREBRO_META_HORIZON=99 "$CEREBRO_BIN" improve "$REPO" 2>/dev/null)"; rc=$?
  [[ $rc -ne 0 && -z "$out" && ! -s "$home/sessions/$session/improvements/improve.md" \
     && ! -e "$home/improvement-history.json" ]]
}
if improve_failure_case empty && improve_failure_case malformed && improve_failure_case failure; then
  printf 'PASS  176g improve rejects stale/empty/malformed/failed reviewer output\n'; pass=$((pass + 1))
else
  printf 'FAIL  176g improve reviewer failure safety\n'; fail=$((fail + 1))
  failures+=("176g improve reviewer failure safety")
fi

# --- 176h. a malformed meta verdict clears stale meta output while preserving
# the independently successful fast report and history entry. ---
IMP_META_BAD="$WORKDIR/improve-meta-malformed"; IMP_META_BAD_SESSION="improve-meta-malformed-session"
improve_home "$IMP_META_BAD" "$IMP_META_BAD_SESSION"
mkdir -p "$IMP_META_BAD/sessions/$IMP_META_BAD_SESSION/improvements"
printf 'STALE\nMETA CLIMB: ISSUES FOUND\n' > \
  "$IMP_META_BAD/sessions/$IMP_META_BAD_SESSION/improvements/meta-improve.md"
meta_bad_out="$(env PATH="$IMPROVE_STUB_DIR:$PATH" IMPROVE_STUB_LOG="$IMPROVE_STUB_LOG" \
  IMPROVE_STUB_MODE=meta-malformed CEREBRO_HOME="$IMP_META_BAD" \
  CEREBRO_SESSION_ID="$IMP_META_BAD_SESSION" CEREBRO_META_HORIZON=1 \
  "$CEREBRO_BIN" improve "$REPO" 2>/dev/null)"; meta_bad_rc=$?
meta_bad_types="$(jq -r '.runs[].type' "$IMP_META_BAD/improvement-history.json" 2>/dev/null | tr '\n' ' ')"
if [[ $meta_bad_rc -eq 0 && "$(printf '%s\n' "$meta_bad_out" | grep -c '/improve.md$')" -eq 1 \
      && "$(printf '%s\n' "$meta_bad_out" | grep -c 'meta-improve.md$')" -eq 0 \
      && ! -s "$IMP_META_BAD/sessions/$IMP_META_BAD_SESSION/improvements/meta-improve.md" \
      && "$meta_bad_types" == "fast " ]]; then
  printf 'PASS  176h malformed meta verdict clears stale output, preserves fast result\n'; pass=$((pass + 1))
else
  printf 'FAIL  176h malformed meta verdict [rc=%d out=%s types=%s]\n' \
    "$meta_bad_rc" "$meta_bad_out" "$meta_bad_types"; fail=$((fail + 1))
  failures+=("176h malformed meta verdict")
fi

# --- 176b. $CEREBRO_HOME/config.json supplies option defaults with precedence
# env > config.json > hardcoded default. Unknown keys are ignored, a missing
# file falls back to the hardcoded defaults, and an env var overrides the file
# for the keys it sets while the file still applies to the rest. Uses env -i so
# the test runner's own exported CEREBRO_* vars don't leak in. ---
CFGHOME="$WORKDIR/cfg-home"
mkdir -p "$CFGHOME"
cat > "$CFGHOME/config.json" <<'CFG_EOF'
{
  "backend": "claude",
  "model": "anthropic/claude-opus-4",
  "timeout": 300,
  "pair_idle": 12,
  "child_session_ttl": 3600,
  "unknown_key": "ignored"
}
CFG_EOF
cfg_vals="$(env -i HOME="$HOME" PATH="$PATH" \
  CEREBRO_LIB_DIR="$here/../lib" CEREBRO_HOME="$CFGHOME" bash -c '
    set -uo pipefail
    . "$CEREBRO_LIB_DIR/config.sh"
    printf "backend=%s model=%s timeout=%s pair_idle=%s child_session_ttl=%s review_model=%s pair_stall=%s\n" \
      "$CEREBRO_BACKEND" "$CEREBRO_MODEL" "$CEREBRO_TIMEOUT" "$CEREBRO_PAIR_IDLE" \
      "$CEREBRO_CHILD_SESSION_TTL" "$CEREBRO_REVIEW_MODEL" "$CEREBRO_PAIR_STALL"' 2>/dev/null)"
cfg_env="$(env -i HOME="$HOME" PATH="$PATH" \
  CEREBRO_LIB_DIR="$here/../lib" CEREBRO_HOME="$CFGHOME" \
  CEREBRO_BACKEND=pi CEREBRO_TIMEOUT=0 bash -c '
    set -uo pipefail
    . "$CEREBRO_LIB_DIR/config.sh"
    printf "backend=%s model=%s timeout=%s pair_idle=%s\n" \
      "$CEREBRO_BACKEND" "$CEREBRO_MODEL" "$CEREBRO_TIMEOUT" "$CEREBRO_PAIR_IDLE"' 2>/dev/null)"
mkdir -p "$WORKDIR/cfg-empty"
cfg_missing="$(env -i HOME="$HOME" PATH="$PATH" \
  CEREBRO_LIB_DIR="$here/../lib" CEREBRO_HOME="$WORKDIR/cfg-empty" bash -c '
    set -uo pipefail
    . "$CEREBRO_LIB_DIR/config.sh"
    printf "backend=%s model=%s timeout=%s pair_idle=%s\n" \
      "$CEREBRO_BACKEND" "$CEREBRO_MODEL" "$CEREBRO_TIMEOUT" "$CEREBRO_PAIR_IDLE"' 2>/dev/null)"
if [[ "$cfg_vals" == "backend=claude model=anthropic/claude-opus-4 timeout=300 pair_idle=12 child_session_ttl=3600 review_model=anthropic/claude-opus-4 pair_stall=180" \
   && "$cfg_env" == "backend=pi model=anthropic/claude-opus-4 timeout=0 pair_idle=12" \
   && "$cfg_missing" == "backend=pi model= timeout=0 pair_idle=60" ]]; then
  printf 'PASS  176b config.json option defaults (env > file > default)\n'; pass=$((pass + 1))
else
  printf 'FAIL  176b config.json [vals=%s env=%s missing=%s]\n' "$cfg_vals" "$cfg_env" "$cfg_missing"; fail=$((fail + 1))
  failures+=("176b config.json :: vals=$cfg_vals env=$cfg_env missing=$cfg_missing")
fi

# ========================================================================
# 177. ACP relay: `cerebro acp` is a thin JSON-RPC proxy between an ACP editor
# and a per-session upstream child. tests/acp/relay_test.py is the editor side;
# it spawns acp_server.py with a stub upstream and asserts sessionId remap, the
# cwd remap (user repo -> additional_directory), the forced agent/mode pin, the
# session/update relay, and metadata foreign-id recording. Requires the
# agent-client-protocol Python SDK (cerebro's one third-party dep) on a
# Python >= 3.10; skipped when absent.
# ========================================================================
ACP_PY=""
for _cand in /opt/homebrew/bin/python3 python3 python3.13 python3.12 python3.11 python3.10; do
  _p="$(command -v "$_cand" 2>/dev/null)" || continue
  [[ -x "$_p" ]] || continue
  if "$_p" -c 'import sys,acp; sys.exit(0 if sys.version_info[:2]>=(3,10) else 1)' 2>/dev/null; then
    ACP_PY="$_p"; break
  fi
done
if [[ -n "$ACP_PY" ]]; then
  acp_out="$("$ACP_PY" "$here/acp/relay_test.py" "$here/.." 2>"$WORKDIR/stderr")"
  acp_rc=$?
  acp_err="$(cat "$WORKDIR/stderr")"
  if [[ $acp_rc -eq 0 && "$acp_out" == *"all checks passed"* ]]; then
    printf 'PASS  177  ACP relay: remap + pin + session/update + metadata\n'; pass=$((pass + 1))
  else
    printf 'FAIL  177  ACP relay [rc=%d out=%s err=%s]\n' "$acp_rc" "$acp_out" "$acp_err"; fail=$((fail + 1))
    failures+=("177 ACP relay :: rc=$acp_rc out=$acp_out err=$acp_err")
  fi
else
  printf 'SKIP  177  ACP relay (agent-client-protocol SDK / Python >=3.10 unavailable)\n'
fi

# ========================================================================
# 178-180. `cerebro acp restart`: signal the running ACP proxy (for this
# CEREBRO_HOME) to exit so the editor respawns it and the new session picks
# up fresh config. Tests do NOT spawn a real acp_server.py (the agent-
# client-protocol Python SDK isn't always available); they stand in a
# long-lived python `time.sleep` with `acp_server.py` in argv and the
# desired CEREBRO_HOME in env, which is what cmd_acp_restart's discovery
# (`acp_running_proxy_pids` + `acp_proxy_env_home`) actually keys on.
# `bin/cerebro` is invoked with the test's isolated CEREBRO_HOME (set in
# the harness preamble) so the discovery filter targets that home.
# ========================================================================

# --- 178. no proxy running -> "nothing to do", exit 0 ---
restart_out="$(env CEREBRO_BACKEND=claude "$CEREBRO_BIN" acp restart 2>&1)"
restart_rc=$?
if (( restart_rc == 0 )) && [[ "$restart_out" == *"no running proxy"* ]]; then
  printf 'PASS  178  acp restart with no proxy -> exit 0 + "no running proxy"\n'; pass=$((pass + 1))
else
  printf 'FAIL  178  acp restart no proxy [rc=%d out=%s]\n' "$restart_rc" "$restart_out"; fail=$((fail + 1))
  failures+=("178 acp restart no proxy :: rc=$restart_rc out=$restart_out")
fi

# --- 179. one fake proxy with matching CEREBRO_HOME -> killed ---
# Stand-in process: a python `time.sleep` whose argv contains `acp_server.py`
# (so `acp_running_proxy_pids` matches) and whose env has CEREBRO_HOME equal
# to the test's isolated home (so the env filter matches). The harness sets
# CEREBRO_HOME for us at the top of the file.
CEREBRO_HOME="$CEREBRO_HOME" python3 -c 'import time; time.sleep(60)' acp_server.py \
  >/dev/null 2>&1 &
FAKE_PROXY_PID=$!
# Give the child a moment to register as a ps-visible process.
for _ in 1 2 3 4 5 6 7 8 9 10; do
  kill -0 "$FAKE_PROXY_PID" 2>/dev/null && ps -A -o pid= -p "$FAKE_PROXY_PID" >/dev/null 2>&1 && break
  sleep 0.1
done
if ! kill -0 "$FAKE_PROXY_PID" 2>/dev/null; then
  printf 'SKIP  179  acp restart kills proxy (fake proxy failed to start)\n'
elif ! command -v ps >/dev/null 2>&1; then
  kill "$FAKE_PROXY_PID" 2>/dev/null; wait "$FAKE_PROXY_PID" 2>/dev/null
  printf 'SKIP  179  acp restart kills proxy (ps not available)\n'
else
  restart_out="$(env CEREBRO_BACKEND=claude "$CEREBRO_BIN" acp restart 2>&1)"
  restart_rc=$?
  # Allow up to 3s for SIGTERM -> process exit.
  gone=0
  for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
    kill -0 "$FAKE_PROXY_PID" 2>/dev/null || { gone=1; break; }
    sleep 0.2
  done
  wait "$FAKE_PROXY_PID" 2>/dev/null
  if (( restart_rc == 0 )) && (( gone == 1 )) \
     && [[ "$restart_out" == *"restarting proxy pid=$FAKE_PROXY_PID"* ]] \
     && [[ "$restart_out" == *"stopped"* ]]; then
    printf 'PASS  179  acp restart kills the matching proxy\n'; pass=$((pass + 1))
  else
    printf 'FAIL  179  acp restart kills proxy [rc=%d gone=%d out=%s]\n' "$restart_rc" "$gone" "$restart_out"; fail=$((fail + 1))
    failures+=("179 acp restart kills proxy :: rc=$restart_rc gone=$gone out=$restart_out")
    kill -KILL "$FAKE_PROXY_PID" 2>/dev/null; wait "$FAKE_PROXY_PID" 2>/dev/null
  fi
fi

# --- 180. fake proxy with a DIFFERENT CEREBRO_HOME -> ignored, left alive ---
OTHER_HOME="$WORKDIR/other-home"
mkdir -p "$OTHER_HOME"
CEREBRO_HOME="$OTHER_HOME" python3 -c 'import time; time.sleep(30)' acp_server.py \
  >/dev/null 2>&1 &
OTHER_PROXY_PID=$!
for _ in 1 2 3 4 5 6 7 8 9 10; do
  kill -0 "$OTHER_PROXY_PID" 2>/dev/null && ps -A -o pid= -p "$OTHER_PROXY_PID" >/dev/null 2>&1 && break
  sleep 0.1
done
if ! kill -0 "$OTHER_PROXY_PID" 2>/dev/null; then
  printf 'SKIP  180  acp restart ignores other CEREBRO_HOME (fake proxy failed to start)\n'
else
  restart_out="$(env CEREBRO_BACKEND=claude "$CEREBRO_BIN" acp restart 2>&1)"
  restart_rc=$?
  other_alive=0
  kill -0 "$OTHER_PROXY_PID" 2>/dev/null && other_alive=1
  # Cleanup: kill the other-home proxy.
  kill -KILL "$OTHER_PROXY_PID" 2>/dev/null; wait "$OTHER_PROXY_PID" 2>/dev/null
  if (( restart_rc == 0 )) && (( other_alive == 1 )) \
     && [[ "$restart_out" == *"no running proxy"* ]]; then
    printf 'PASS  180  acp restart ignores proxies for a different CEREBRO_HOME\n'; pass=$((pass + 1))
  else
    printf 'FAIL  180  acp restart ignores other CEREBRO_HOME [rc=%d other_alive=%d out=%s]\n' "$restart_rc" "$other_alive" "$restart_out"; fail=$((fail + 1))
    failures+=("180 acp restart other home :: rc=$restart_rc other_alive=$other_alive out=$restart_out")
  fi
fi

# --- 181. unknown `cerebro acp` subcommand -> die, no proxy started ---
bogus_out="$(env CEREBRO_BACKEND=claude "$CEREBRO_BIN" acp bogus 2>&1)"
bogus_rc=$?
if (( bogus_rc != 0 )) && [[ "$bogus_out" == *"unknown subcommand"* ]]; then
  printf 'PASS  181  acp rejects unknown subcommand (no proxy started)\n'; pass=$((pass + 1))
else
  printf 'FAIL  181  acp bogus [rc=%d out=%s]\n' "$bogus_rc" "$bogus_out"; fail=$((fail + 1))
  failures+=("181 acp bogus :: rc=$bogus_rc out=$bogus_out")
fi

# --- 183. `cerebro acp restart` reaps orphan upstream children whose parent
# proxy has died. We simulate the orphan state by backgrounding the
# "upstream child" inside a subshell that exits immediately -- the
# kernel reparents the child to PID 1 (launchd) the moment the subshell
# exits, exactly the state the new proxy must clean up before serving
# `session/load` for that session. (A real upstream child reaches the
# same state when its parent -- the `acp_server.py` proxy that exec'd
# over `bin/cerebro acp` -- dies; the proxy has no living ancestor in
# the session, so the kernel reparenting target is also PID 1.)
REAP_SID="11111111-2222-3333-4444-555555555555"
REAP_ACP_DIR="$CEREBRO_HOME/acp/$REAP_SID"
mkdir -p "$REAP_ACP_DIR"

# Spawn the orphan upstream child via an exiting subshell so the kernel
# reparents it to PID 1. The cmdline must mention one of {acp, openode,
# claude, npx} (see acp_reap_orphans' PPID==1 pre-filter) so the reaper
# actually pays the env-read cost for this PID.
( CEREBRO_HOME="$CEREBRO_HOME" CEREBRO_SESSION_ID="$REAP_SID" \
    python3 -c 'import time; time.sleep(120)' \
    fake_upstream_acp_child \
    >/dev/null 2>&1 & )
# Find the child PID. We don't have a $! because the spawn happened
# inside a subshell. Search ps for the most recent python3 sleep that
# has CEREBRO_SESSION_ID=<our sid> in env. The sleep-window below gives
# the subshell time to exit and the kernel to reparent.
REAP_CHILD_PID=""
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
  while IFS= read -r line; do
    pid="${line%% *}"; line="${line#"$pid" }"
    ppid="${line%% *}"; line="${line#"$ppid" }"
    [[ "$ppid" == "1" ]] || continue
    if ps eww -p "$pid" 2>/dev/null | tail -n +2 \
         | tr ' \t' '\n' | grep -Fxq "CEREBRO_SESSION_ID=$REAP_SID"; then
      REAP_CHILD_PID="$pid"
      break 2
    fi
  done < <(ps -A -o pid=,ppid=,command= 2>/dev/null | awk '{$1=$1; print}')
  sleep 0.1
done
if [[ -z "$REAP_CHILD_PID" ]]; then
  rmdir "$REAP_ACP_DIR" 2>/dev/null || true
  printf 'SKIP  183  acp restart reaps orphans (could not produce an orphan child)\n'
else
  # Run restart. No proxy is running; restart's reaper should still
  # find and kill the orphan (this is the safety net for "editor killed
  # the proxy some other way" cases -- the orphan was left behind by a
  # PREVIOUS proxy lifecycle, not by the current `cerebro acp restart`).
  restart_out="$(env CEREBRO_BACKEND=claude "$CEREBRO_BIN" acp restart 2>&1)"
  restart_rc=$?
  orphan_gone=0
  for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
    kill -0 "$REAP_CHILD_PID" 2>/dev/null || { orphan_gone=1; break; }
    sleep 0.2
  done
  wait "$REAP_CHILD_PID" 2>/dev/null
  rmdir "$REAP_ACP_DIR" 2>/dev/null || true
  if (( restart_rc == 0 )) && (( orphan_gone == 1 )) \
     && [[ "$restart_out" == *"reaped"* ]] \
     && [[ "$restart_out" == *"reaping orphan upstream child pid=$REAP_CHILD_PID"* ]]; then
    printf 'PASS  183  acp restart reaps orphan upstream child(ren)\n'; pass=$((pass + 1))
  else
    printf 'FAIL  183  acp restart reaps orphans [rc=%d gone=%d out=%s]\n' "$restart_rc" "$orphan_gone" "$restart_out"; fail=$((fail + 1))
    failures+=("183 acp restart reaps orphans :: rc=$restart_rc gone=$orphan_gone out=$restart_out")
    kill -KILL "$REAP_CHILD_PID" 2>/dev/null; wait "$REAP_CHILD_PID" 2>/dev/null
  fi
fi

# --- 184. `cerebro acp restart` is idempotent: running it again with no
# proxy and no orphans is a no-op (no errors, exit 0).
restart_out2="$(env CEREBRO_BACKEND=claude "$CEREBRO_BIN" acp restart 2>&1)"
restart_rc2=$?
if (( restart_rc2 == 0 )) && [[ "$restart_out2" == *"no running proxy"* ]] \
   && [[ "$restart_out2" != *"reap"* ]]; then
  printf 'PASS  184  acp restart is idempotent (no proxy + no orphans)\n'; pass=$((pass + 1))
else
  printf 'FAIL  184  acp restart idempotent [rc=%d out=%s]\n' "$restart_rc2" "$restart_out2"; fail=$((fail + 1))
  failures+=("184 acp restart idempotent :: rc=$restart_rc2 out=$restart_out2")
fi

# --- 182. `cerebro acp-mint` and `cerebro acp-set-foreign` (legacy top-level
# forms) are no longer recognised: they should fall through to
# "unknown subcommand". The internal mint/set-foreign are reachable as
# `cerebro acp mint` and `cerebro acp set-foreign`.
for legacy in "acp-mint" "acp-set-foreign"; do
  leg_out="$("$CEREBRO_BIN" "$legacy" 2>&1)"
  leg_rc=$?
  if (( leg_rc != 0 )) && [[ "$leg_out" == *"unknown subcommand"* ]]; then
    printf 'PASS  182  legacy `%s` rejected (use `cerebro acp %s`)\n' "$legacy" "${legacy#acp-}"; pass=$((pass + 1))
  else
    printf 'FAIL  182  legacy `%s` [rc=%d out=%s]\n' "$legacy" "$leg_rc" "$leg_out"; fail=$((fail + 1))
    failures+=("182 legacy $legacy :: rc=$leg_rc out=$leg_out")
  fi
done

# --- 185. detached process helper survives its launcher and records completion.
DETACH_OUT="$CEREBRO_HOME/sessions/$CEREBRO_SESSION_ID/detach-test/survived.out"
DETACH_STATUS="$DETACH_OUT.status"
DETACH_PID="$DETACH_OUT.pid"
DETACH_JOBS="$CEREBRO_HOME/sessions/$CEREBRO_SESSION_ID/detached-jobs"
DETACH_JOB_ID="00000000-0000-0000-0000-000000000185"
DETACH_JOB="$DETACH_JOBS/$DETACH_JOB_ID.json"
mkdir -p "$DETACH_JOBS"
detach_start="$(date +%s)"
python3 "$here/../lib/python/detach_process.py" \
  "$DETACH_OUT" "$DETACH_STATUS" "$DETACH_PID" \
  "$DETACH_JOB" "$DETACH_JOB_ID" verify \
  /bin/sh -c 'sleep 1; printf survived'
detach_launch_rc=$?
detach_elapsed=$(( $(date +%s) - detach_start ))
detach_wait="$($CEREBRO_BIN wait "$DETACH_JOB_ID" 2>"$WORKDIR/detach-wait.err")"
detach_wait_rc=$?
detach_body="$(cat "$DETACH_OUT" 2>/dev/null)"
detach_status="$(cat "$DETACH_STATUS" 2>/dev/null)"
detach_jobs="$($CEREBRO_BIN jobs 2>&1)"
if (( detach_launch_rc == 0 && detach_wait_rc == 0 && detach_elapsed <= 1 )) \
   && [[ "$detach_body" == "survived" && "$detach_status" == "0" \
          && -s "$DETACH_PID" && "$detach_jobs" == *"[succeeded] $DETACH_JOB_ID"* ]] \
   && jq -e --arg id "$DETACH_JOB_ID" --arg output "$DETACH_OUT" \
        '.exit_code == 0 and .state == "completed" and .job_id == $id and
         .output == $output and .text == "survived"' <<<"$detach_wait" >/dev/null; then
  printf 'PASS  185  detached process survives launcher and persists job state\n'; pass=$((pass + 1))
else
  printf 'FAIL  185  detached process [launch=%d wait=%d elapsed=%d body=%s status=%s wait_out=%s jobs=%s]\n' \
    "$detach_launch_rc" "$detach_wait_rc" "$detach_elapsed" "$detach_body" "$detach_status" "$detach_wait" "$detach_jobs"
  fail=$((fail + 1))
  failures+=("185 detached process :: launch=$detach_launch_rc wait=$detach_wait_rc elapsed=$detach_elapsed body=$detach_body status=$detach_status wait_out=$detach_wait jobs=$detach_jobs")
fi

# --- 186. detach rejects arbitrary commands and paths outside session scratch.
detach_bad_out="$WORKDIR/detach-bad.out"
printf 'outside status remains untouched\n' > "$detach_bad_out.status"
detach_bad="$($CEREBRO_BIN detach --output "$detach_bad_out" -- status 2>&1)"
detach_bad_rc=$?
detach_path_bad="$($CEREBRO_BIN detach --output "$detach_bad_out" -- verify 2>&1)"
detach_path_bad_rc=$?
wait_path_bad="$($CEREBRO_BIN wait "$detach_bad_out.status" 2>&1)"
wait_path_bad_rc=$?
if (( detach_bad_rc != 0 && detach_path_bad_rc != 0 && wait_path_bad_rc != 0 )) \
   && [[ "$detach_bad" == *"not a long-running child subcommand"* \
         && "$detach_path_bad" == *"output must be under"* \
         && "$wait_path_bad" == *"output must be under"* \
         && "$(cat "$detach_bad_out.status")" == "outside status remains untouched" \
         && ! -e "$detach_bad_out" && ! -e "$detach_bad_out.pid" ]]; then
  printf 'PASS  186  detach/wait reject arbitrary subcommands and paths\n'; pass=$((pass + 1))
else
  printf 'FAIL  186  detach validation [cmd_rc=%d cmd=%s path_rc=%d path=%s wait_rc=%d wait=%s]\n' \
    "$detach_bad_rc" "$detach_bad" "$detach_path_bad_rc" "$detach_path_bad" \
    "$wait_path_bad_rc" "$wait_path_bad"
  fail=$((fail + 1))
  failures+=("186 detach validation :: cmd_rc=$detach_bad_rc cmd=$detach_bad path_rc=$detach_path_bad_rc path=$detach_path_bad wait_rc=$wait_path_bad_rc wait=$wait_path_bad")
fi

# --- 187. waiter propagates a detached child's non-zero exit code.
DETACH_FAIL_OUT="$CEREBRO_HOME/sessions/$CEREBRO_SESSION_ID/detach-test/failed.out"
DETACH_FAIL_ID="00000000-0000-0000-0000-000000000187"
python3 "$here/../lib/python/detach_process.py" \
  "$DETACH_FAIL_OUT" "$DETACH_FAIL_OUT.status" "$DETACH_FAIL_OUT.pid" \
  "$DETACH_JOBS/$DETACH_FAIL_ID.json" "$DETACH_FAIL_ID" review \
  /bin/sh -c 'sleep 0.2; exit 7' >/dev/null
detach_fail_wait="$($CEREBRO_BIN wait "$DETACH_FAIL_ID" 2>"$WORKDIR/detach-fail-wait.err")"
detach_fail_rc=$?
if (( detach_fail_rc == 7 )) \
   && jq -e --arg id "$DETACH_FAIL_ID" --arg output "$DETACH_FAIL_OUT" \
        '.exit_code == 7 and .state == "completed" and .job_id == $id and .output == $output' \
        <<<"$detach_fail_wait" >/dev/null; then
  printf 'PASS  187  detached waiter propagates child failure\n'; pass=$((pass + 1))
else
  printf 'FAIL  187  detached waiter failure [rc=%d out=%s]\n' "$detach_fail_rc" "$detach_fail_wait"
  fail=$((fail + 1))
  failures+=("187 detached waiter failure :: rc=$detach_fail_rc out=$detach_fail_wait")
fi

# --- 188. cancel verifies and terminates the monitor's full descendant tree.
DETACH_CANCEL_OUT="$CEREBRO_HOME/sessions/$CEREBRO_SESSION_ID/detach-test/cancel.out"
DETACH_CANCEL_ID="00000000-0000-0000-0000-000000000188"
DETACH_CANCEL_CHILD="$CEREBRO_HOME/sessions/$CEREBRO_SESSION_ID/detach-test/cancel-child.pid"
python3 "$here/../lib/python/detach_process.py" \
  "$DETACH_CANCEL_OUT" "$DETACH_CANCEL_OUT.status" "$DETACH_CANCEL_OUT.pid" \
  "$DETACH_JOBS/$DETACH_CANCEL_ID.json" "$DETACH_CANCEL_ID" execute \
  /bin/sh -c 'sleep 30 & printf "%s\n" "$!" > "$1"; wait' sh \
  "$DETACH_CANCEL_CHILD" >/dev/null
for _ in 1 2 3 4 5 6 7 8 9 10; do
  [[ -s "$DETACH_CANCEL_CHILD" ]] && break
  sleep 0.1
done
detach_cancel_monitor="$(cat "$DETACH_CANCEL_OUT.pid" 2>/dev/null)"
detach_cancel_child="$(cat "$DETACH_CANCEL_CHILD" 2>/dev/null)"
"$CEREBRO_BIN" wait "$DETACH_CANCEL_ID" >"$WORKDIR/cancel-wait-one" 2>&1 &
CANCEL_WAITER_ONE=$!
"$CEREBRO_BIN" wait "$DETACH_CANCEL_ID" >"$WORKDIR/cancel-wait-two" 2>&1 &
CANCEL_WAITER_TWO=$!
detach_cancel_out="$($CEREBRO_BIN cancel "$DETACH_CANCEL_ID" 2>&1)"
detach_cancel_rc=$?
wait "$CANCEL_WAITER_ONE"; cancel_wait_one_rc=$?
wait "$CANCEL_WAITER_TWO"; cancel_wait_two_rc=$?
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if ! kill -0 "$detach_cancel_monitor" 2>/dev/null \
     && ! kill -0 "$detach_cancel_child" 2>/dev/null; then
    break
  fi
  sleep 0.1
done
detach_cancel_status="$(cat "$DETACH_CANCEL_OUT.status" 2>/dev/null)"
detach_cancel_jobs="$($CEREBRO_BIN jobs 2>&1)"
if (( detach_cancel_rc == 0 && cancel_wait_one_rc == 130 && cancel_wait_two_rc == 130 )) \
   && ! kill -0 "$detach_cancel_monitor" 2>/dev/null \
   && ! kill -0 "$detach_cancel_child" 2>/dev/null \
   && [[ "$detach_cancel_status" == "130" \
         && "$detach_cancel_out" == *"cancelled detached job $DETACH_CANCEL_ID"* \
         && "$detach_cancel_jobs" == *"[cancelled] $DETACH_CANCEL_ID"* ]] \
   && jq -e --arg id "$DETACH_CANCEL_ID" \
        '.exit_code == 130 and .state == "completed" and .job_id == $id' \
        "$WORKDIR/cancel-wait-one" "$WORKDIR/cancel-wait-two" >/dev/null; then
  printf 'PASS  188  cancellation terminates descendants and every waiter reports130\n'; pass=$((pass + 1))
else
  printf 'FAIL  188  detached cancel [rc=%d monitor=%s child=%s status=%s out=%s jobs=%s]\n' \
    "$detach_cancel_rc" "$detach_cancel_monitor" "$detach_cancel_child" \
    "$detach_cancel_status" "$detach_cancel_out" "$detach_cancel_jobs"
  fail=$((fail + 1))
  failures+=("188 detached cancel :: rc=$detach_cancel_rc monitor=$detach_cancel_monitor child=$detach_cancel_child status=$detach_cancel_status out=$detach_cancel_out jobs=$detach_cancel_jobs")
  kill -KILL "$detach_cancel_monitor" "$detach_cancel_child" 2>/dev/null || true
fi

# --- 189. a stale running record fails instead of waiting forever.
DETACH_STALE_OUT="$CEREBRO_HOME/sessions/$CEREBRO_SESSION_ID/detach-test/stale.out"
DETACH_STALE_ID="00000000-0000-0000-0000-000000000189"
printf 'running\n' > "$DETACH_STALE_OUT.status"
printf '99999999\n' > "$DETACH_STALE_OUT.pid"
printf 'interrupted evidence\n' > "$DETACH_STALE_OUT"
printf '{"id":"%s","command":"verify","output":"%s","status":"%s","pid_file":"%s","result":"%s","pid":99999999,"created_at":"2026-07-14T00:00:00Z"}\n' \
  "$DETACH_STALE_ID" "$DETACH_STALE_OUT" "$DETACH_STALE_OUT.status" \
  "$DETACH_STALE_OUT.pid" "$DETACH_STALE_OUT" > "$DETACH_JOBS/$DETACH_STALE_ID.json"
detach_stale_wait="$($CEREBRO_BIN wait "$DETACH_STALE_ID" 2>"$WORKDIR/detach-stale-wait.err")"
detach_stale_rc=$?
detach_stale_status="$(cat "$DETACH_STALE_OUT.status" 2>/dev/null)"
if (( detach_stale_rc == 125 )) \
   && [[ "$detach_stale_status" == "125" \
         && "$(cat "$WORKDIR/detach-stale-wait.err")" == *"monitor disappeared before completion"* ]] \
   && jq -e --arg id "$DETACH_STALE_ID" --arg output "$DETACH_STALE_OUT" \
        '.exit_code == 125 and .state == "completed" and .job_id == $id and
         .output == $output and (.text | contains("interrupted evidence"))' \
        <<<"$detach_stale_wait" >/dev/null; then
  printf 'PASS  189  detached waiter detects stale monitor records\n'; pass=$((pass + 1))
else
  printf 'FAIL  189  detached stale monitor [rc=%d status=%s out=%s]\n' \
    "$detach_stale_rc" "$detach_stale_status" "$detach_stale_wait"
  fail=$((fail + 1))
  failures+=("189 detached stale monitor :: rc=$detach_stale_rc status=$detach_stale_status out=$detach_stale_wait")
fi

# ========================================================================
# 190-197. Interactive guard (require_interactive): capability-based TTY
# check instead of a parent-executable allow-list. Cerebro must accept any
# controller that allocates a genuine PTY (shell, editor, another agent such
# as Codex with `tty: true`) and still reject pipes/redirects/cron. Driven
# through tests/pty_run.py, which forks a real PTY and optionally wraps the
# child in an intermediate process whose kernel `comm` basename is a chosen
# name (the dimension the old heuristic used).
#
# The suite sets CEREBRO_SESSION_ID globally (which bypasses the guard), so
# every case here runs with `env -u CEREBRO_SESSION_ID` to make the guard fire.
# `cerebro list` is the lightweight probe: it calls require_interactive then, on
# an empty home, prints "cerebro: no sessions yet" and exits 0 -- no backend
# launch, no network.
# ========================================================================
PTY_HOME="$WORKDIR/pty-home"
mkdir -p "$PTY_HOME"
PTY_RUN="$here/pty_run.py"
pty_probe_rc() {  # <captured-helper-output> -> echoes the RC=<n> value
  printf '%s\n' "$1" | sed -n 's/^RC=//p'
}
pty_out() {       # <captured-helper-output> -> prints the ---OUTPUT--- body
  awk '/^---OUTPUT---$/{f=1;next} /^---END---$/{f=0} f' <<<"$1"
}

# --- 190. genuine PTY with parent "codex" is accepted (the reported bug) ---
res="$(env -u CEREBRO_SESSION_ID CEREBRO_HOME="$PTY_HOME" \
  python3 "$PTY_RUN" --parent codex --timeout 6 -- "$CEREBRO_BIN" list 2>&1)"
rc190="$(pty_probe_rc "$res")"; out190="$(pty_out "$res")"
if [[ "$rc190" == "0" && "$out190" == *"no sessions yet"* ]]; then
  printf 'PASS  190  PTY with parent codex is accepted\n'; pass=$((pass + 1))
else
  printf 'FAIL  190  PTY+codex rejected [rc=%s out=%s]\n' "$rc190" "$out190"
  fail=$((fail + 1)); failures+=("190 PTY+codex :: rc=$rc190 out=$out190")
fi

# --- 191. genuine PTY with another non-shell controller ("node") accepted ---
rm -rf "$PTY_HOME"; mkdir -p "$PTY_HOME"
res="$(env -u CEREBRO_SESSION_ID CEREBRO_HOME="$PTY_HOME" \
  python3 "$PTY_RUN" --parent node --timeout 6 -- "$CEREBRO_BIN" list 2>&1)"
rc191="$(pty_probe_rc "$res")"; out191="$(pty_out "$res")"
if [[ "$rc191" == "0" && "$out191" == *"no sessions yet"* ]]; then
  printf 'PASS  191  PTY with non-shell parent node is accepted\n'; pass=$((pass + 1))
else
  printf 'FAIL  191  PTY+node rejected [rc=%s out=%s]\n' "$rc191" "$out191"
  fail=$((fail + 1)); failures+=("191 PTY+node :: rc=$rc191 out=$out191")
fi

# --- 192. ordinary interactive shell launch (parent "bash") remains accepted ---
rm -rf "$PTY_HOME"; mkdir -p "$PTY_HOME"
res="$(env -u CEREBRO_SESSION_ID CEREBRO_HOME="$PTY_HOME" \
  python3 "$PTY_RUN" --parent bash --timeout 6 -- "$CEREBRO_BIN" list 2>&1)"
rc192="$(pty_probe_rc "$res")"; out192="$(pty_out "$res")"
if [[ "$rc192" == "0" && "$out192" == *"no sessions yet"* ]]; then
  printf 'PASS  192  PTY with parent bash (ordinary shell) is accepted\n'; pass=$((pass + 1))
else
  printf 'FAIL  192  PTY+bash rejected [rc=%s out=%s]\n' "$rc192" "$out192"
  fail=$((fail + 1)); failures+=("192 PTY+bash :: rc=$rc192 out=$out192")
fi

# --- 193. no-TTY (cron-style: stdin from /dev/null, stdout to a file) rejected ---
rm -rf "$PTY_HOME"; mkdir -p "$PTY_HOME"
env -u CEREBRO_SESSION_ID CEREBRO_HOME="$PTY_HOME" \
  "$CEREBRO_BIN" list </dev/null >"$WORKDIR/193-out" 2>"$WORKDIR/193-err"
rc193=$?; err193="$(cat "$WORKDIR/193-err")"
if (( rc193 != 0 )) && [[ "$err193" == *"stdin and stdout must be terminals"* ]]; then
  printf 'PASS  193  no-TTY launch rejected\n'; pass=$((pass + 1))
else
  printf 'FAIL  193  no-TTY not rejected [rc=%d err=%s]\n' "$rc193" "$err193"
  fail=$((fail + 1)); failures+=("193 no-TTY :: rc=$rc193 err=$err193")
fi

# --- 194. piped stdin rejected ---
rm -rf "$PTY_HOME"; mkdir -p "$PTY_HOME"
err194="$(printf '' | env -u CEREBRO_SESSION_ID CEREBRO_HOME="$PTY_HOME" \
  "$CEREBRO_BIN" list 2>&1 >/dev/null)"; rc194=$?
if (( rc194 != 0 )) && [[ "$err194" == *"stdin and stdout must be terminals"* ]]; then
  printf 'PASS  194  piped stdin rejected\n'; pass=$((pass + 1))
else
  printf 'FAIL  194  piped stdin not rejected [rc=%d err=%s]\n' "$rc194" "$err194"
  fail=$((fail + 1)); failures+=("194 piped-stdin :: rc=$rc194 err=$err194")
fi

# --- 195. redirected stdout rejected ---
rm -rf "$PTY_HOME"; mkdir -p "$PTY_HOME"
env -u CEREBRO_SESSION_ID CEREBRO_HOME="$PTY_HOME" \
  "$CEREBRO_BIN" list >"$WORKDIR/195-out" 2>"$WORKDIR/195-err"
rc195=$?; err195="$(cat "$WORKDIR/195-err")"
if (( rc195 != 0 )) && [[ "$err195" == *"stdin and stdout must be terminals"* ]]; then
  printf 'PASS  195  redirected stdout rejected\n'; pass=$((pass + 1))
else
  printf 'FAIL  195  redirected stdout not rejected [rc=%d err=%s]\n' "$rc195" "$err195"
  fail=$((fail + 1)); failures+=("195 redirected-stdout :: rc=$rc195 err=$err195")
fi

# --- 196. full interactive session through a PTY: receive a prompt, continue,
# EOF cleanly. A stub `pi` (the real orchestrator backend execs it) reads
# stdin lines and echoes them, so we can prove the PTY relays input both ways
# across multiple turns and that Ctrl-D ends the session. ---
PTY_STUB_DIR="$WORKDIR/pty-pi-stub"
mkdir -p "$PTY_STUB_DIR"
cat > "$PTY_STUB_DIR/pi" <<'EOF'
#!/usr/bin/env bash
if [[ "${1:-}" == "--version" ]]; then echo 0.99.2; exit 0; fi
echo "STUB-READY"
while IFS= read -r line; do
  printf 'stub: %s\n' "$line"
done
exit 0
EOF
chmod +x "$PTY_STUB_DIR/pi"
rm -rf "$PTY_HOME"; mkdir -p "$PTY_HOME"
cat > "$WORKDIR/196-script" <<'EOF'
EXPECT STUB-READY
SEND hello
EXPECT stub: hello
SEND one more turn
EXPECT stub: one more turn
SENDEOF
EOF
res="$(env -u CEREBRO_SESSION_ID CEREBRO_BACKEND=pi \
  CEREBRO_HOME="$PTY_HOME" PATH="$PTY_STUB_DIR:$PATH" \
  python3 "$PTY_RUN" --parent codex --timeout 8 --script "$WORKDIR/196-script" \
  -- "$CEREBRO_BIN" 2>&1)"
rc196="$(pty_probe_rc "$res")"; out196="$(pty_out "$res")"
if [[ "$rc196" == "0" \
   && "$out196" == *"STUB-READY"* \
   && "$out196" == *"stub: hello"* \
   && "$out196" == *"stub: one more turn"* ]]; then
  printf 'PASS  196  interactive session driven through PTY (prompt + continue + EOF)\n'; pass=$((pass + 1))
else
  printf 'FAIL  196  PTY session drive failed [rc=%s out=%s]\n' "$rc196" "$out196"
  fail=$((fail + 1)); failures+=("196 PTY-session :: rc=$rc196 out=$out196")
fi

# --- 197. interruption through the PTY: Ctrl-C terminates the session (the PTY
# forwards SIGINT to the foreground process). Any non-zero rc proves it did not
# hang and was interrupted rather than exiting cleanly. ---
rm -rf "$PTY_HOME"; mkdir -p "$PTY_HOME"
cat > "$WORKDIR/197-script" <<'EOF'
EXPECT STUB-READY
SENDINTR
EOF
res="$(env -u CEREBRO_SESSION_ID CEREBRO_BACKEND=pi \
  CEREBRO_HOME="$PTY_HOME" PATH="$PTY_STUB_DIR:$PATH" \
  python3 "$PTY_RUN" --parent codex --timeout 8 --script "$WORKDIR/197-script" \
  -- "$CEREBRO_BIN" 2>&1)"
rc197="$(pty_probe_rc "$res")"
if [[ "$rc197" != "0" && "$rc197" != "none" ]]; then
  printf 'PASS  197  Ctrl-C through PTY interrupts the session (rc=%s)\n' "$rc197"; pass=$((pass + 1))
else
  printf 'FAIL  197  Ctrl-C did not interrupt [rc=%s]\n' "$rc197"
  fail=$((fail + 1)); failures+=("197 PTY-intr :: rc=$rc197")
fi

# ========================================================================
# 198x. playwright_isolate_child -- the @playwright/mcp browser profile is
# isolated per cerebro child (in-memory) so concurrent browser-capable children
# don't collide on Chromium's SingletonLock. Unit check of the helper plus a
# propagation through native adapter launch tests below.
# ========================================================================

# --- 198a. playwright_isolate_child exports PLAYWRIGHT_MCP_ISOLATED=1 by
# default, and exports nothing when CEREBRO_PLAYWRIGHT_ISOLATED=0. ---
iso_default="$(bash -c '
  set -uo pipefail
  CEREBRO_LIB_DIR="$1"
  . "$CEREBRO_LIB_DIR/config.sh"
  . "$CEREBRO_LIB_DIR/helpers.sh"
  playwright_isolate_child
  printenv PLAYWRIGHT_MCP_ISOLATED' _ "$here/../lib" 2>/dev/null)"
iso_optout="$(CEREBRO_PLAYWRIGHT_ISOLATED=0 bash -c '
  set -uo pipefail
  CEREBRO_LIB_DIR="$1"
  . "$CEREBRO_LIB_DIR/config.sh"
  . "$CEREBRO_LIB_DIR/helpers.sh"
  playwright_isolate_child
  printenv PLAYWRIGHT_MCP_ISOLATED' _ "$here/../lib" 2>/dev/null)"
if [[ "$iso_default" == "1" && -z "$iso_optout" ]]; then
  printf 'PASS  198a playwright_isolate_child default=1, opt-out=none\n'; pass=$((pass + 1))
else
  printf 'FAIL  198a playwright_isolate_child [default=%s optout=%s]\n' "$iso_default" "$iso_optout"; fail=$((fail + 1))
  failures+=("198a playwright_isolate_child :: default=$iso_default optout=$iso_optout")
fi

# ========================================================================
# 199. PTY MCP ANSI parser (lib/python/cerebro_mcp_ansi.py) -- the pure-stdlib
# normalizer that turns raw PTY bytes into the text cerebro_wait/cerebro_read return.
# Pattern A (always runs, 3.9 OK): ANSI strip, \r spinner collapse, CRLF line
# endings (PTY ONLCR) keep content, \b/\t, incremental cursor/text_since,
# tail_lines, UTF-8 split across feeds, buffer cap.
# ========================================================================
cerebro_unit_out="$(python3 "$here/cerebro_mcp_unit.py" 2>&1)"
if [[ "$cerebro_unit_out" == *"all checks passed"* ]]; then
  printf 'PASS  199  PTY MCP ANSI parser unit\n'; pass=$((pass + 1))
else
  printf 'FAIL  199  PTY MCP ANSI parser unit [out=%s]\n' "$cerebro_unit_out"; fail=$((fail + 1))
  failures+=("199 cerebro-mcp parser unit :: $cerebro_unit_out")
fi

# ========================================================================
# 200. PTY MCP server (lib/python/cerebro_mcp_server.py) end-to-end over stdio.
# Pattern B (gated on Python >=3.10 + the `mcp` SDK; SKIP when absent): the test
# IS the MCP client -- it spawns the server, does the initialize handshake,
# asserts tools/list exposes the 9 cerebro_* tools, then drives a trivial
# interactive program through cerebro_spawn -> cerebro_wait -> cerebro_send -> cerebro_read ->
# cerebro_wait(exit) -> cerebro_status -> cerebro_close -> cerebro_list. Mirrors the ACP relay
# gating at block 177.
# ========================================================================
CEREBRO_MCP_PY=""
for _cand in /opt/homebrew/bin/python3 python3 python3.13 python3.12 python3.11 python3.10; do
  _p="$(command -v "$_cand" 2>/dev/null)" || continue
  [[ -x "$_p" ]] || continue
  if "$_p" -c 'import sys; from mcp.server.mcpserver import MCPServer; sys.exit(0 if sys.version_info[:2]>=(3,10) else 1)' 2>/dev/null; then
    CEREBRO_MCP_PY="$_p"; break
  fi
done
if [[ -n "$CEREBRO_MCP_PY" ]]; then
  cerebro_srv_out="$("$CEREBRO_MCP_PY" "$here/cerebro_mcp_server.py" "$here/.." 2>"$WORKDIR/stderr")"
  cerebro_srv_rc=$?
  cerebro_srv_err="$(cat "$WORKDIR/stderr")"
  if [[ $cerebro_srv_rc -eq 0 && "$cerebro_srv_out" == *"all checks passed"* ]]; then
    printf 'PASS  200  PTY MCP server e2e (spawn/wait/send/read/exit/close over stdio)\n'; pass=$((pass + 1))
  else
    printf 'FAIL  200  PTY MCP server e2e [rc=%d out=%s err=%s]\n' "$cerebro_srv_rc" "$cerebro_srv_out" "$cerebro_srv_err"; fail=$((fail + 1))
    failures+=("200 cerebro-mcp server e2e :: rc=$cerebro_srv_rc out=$cerebro_srv_out err=$cerebro_srv_err")
  fi
else
  printf 'SKIP  200  PTY MCP server e2e (mcp SDK / Python >=3.10 unavailable)\n'
fi

# Native adapters and the guarded MCP tool are exercised without model calls.
run_case 201 "guarded MCP command roles, literal arguments and steering provenance" 0 -- \
  python3 "$here/command_tools_test.py"
run_case 202 "Claude/Codex native session, review, resume and live steering" 0 -- \
  python3 "$here/native_backends_test.py"

run_case 203 "Pi version gate, native settled completion and paired worktree isolation" 0 -- \
  python3 "$here/pi_native_test.py"
run_case 204 "plan/audit/detach outputs stay confined to the owning session" 0 -- \
  python3 "$here/confinement_test.py"
run_case 205 "native turn admission, completion ordering and boundary steering" 0 -- \
  python3 "$here/pair_adapters_test.py"
run_case 206 "native MCP configuration retains delegated backend settings" 0 -- \
  python3 "$here/mcp_environment_test.py"
run_case 208 "Claude native prompt hook records input and binds its session UUID" 0 -- \
  python3 "$here/claude_hook_test.py"
run_case 209 "Claude ACP session configuration and unsupported parent guards" 0 -- \
  python3 "$here/acp_launch_test.py"
run_case 210 "native Pi guarded tools and exact parent session binding" 0 -- \
  node "$here/pi_extension_test.mjs"
run_case 212 "shared workflow skills and explanatory review/verification task packets" 0 -- \
  python3 "$here/workflow_skills_test.py"
run_case 211 "independent role models reach native supervisor launch and resume" 0 -- \
  python3 "$here/role_models_test.py"
run_case 213 "typed Jev classifications and live steering handoffs" 0 -- \
  python3 "$here/jev_watch_test.py"
run_case 215 "checkout/branch reuse, explicit isolation and recovery" 0 -- \
  python3 "$here/workspace_test.py"
run_case 214 "recall searches space, newline and glob-bearing session paths" 0 -- \
  python3 "$here/recall_test.py"

printf '\n%d passed, %d failed\n' "$pass" "$fail"
if (( fail > 0 )); then
  printf '\nFailures:\n'
  for f in "${failures[@]}"; do printf '  %s\n' "$f"; done
  exit 1
fi
exit 0
