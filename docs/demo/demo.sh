#!/usr/bin/env bash
# Scripted playback of a representative cerebro session, used by demo.tape
# to render docs/demo.gif. This is a simulation for the README animation --
# it does not run a native backend.

# colors
B=$'\033[1m'; D=$'\033[2m'; N=$'\033[0m'
CY=$'\033[36m'; GR=$'\033[32m'; MA=$'\033[35m'; YE=$'\033[33m'

say()  { printf '%s\n' "$1"; }
slow() { sleep "$1"; }
wipe() { printf '\033[2J\033[H'; }

# Type a user chat line character by character.
type_line() {
  printf '%s' "${B}${CY}❯ ${N}${B}"
  local s="$1" i
  for ((i = 0; i < ${#s}; i++)); do
    printf '%s' "${s:i:1}"
    sleep 0.02
  done
  printf '%s\n' "$N"
}

# Ask for scoped work and authorized commits; keep the plan adjustable.
wipe
slow 0.4
say "${D}\$${N} ${B}CEREBRO_BACKEND=pi cerebro${N}"
slow 0.6
say "${D}==> cerebro: starting session 3f2c91ae (pi)${N}"
say ""
slow 0.7

type_line "add rate limiting to the public API in ~/work/webapp; commit each small step, then open a PR"
slow 0.8
say "${MA}●${N} I'll delegate small commits with Jev watching and fresh reviews."
slow 0.4
say "  ${D}⏺ cerebro spec set \"Rate-limit the public API; 429 + Retry-After…\"${N}"
slow 0.6
say "${MA}●${N} Possible commits:"
say "    1. Add the limiter and verify it."
say "    2. Connect it to public endpoints and verify the user flow."
say "  ${D}(adjusted as we learn more; no separate approval needed)${N}"
say ""
slow 1.6

# Delegate, verify and offer fresh explanatory review after each commit.
wipe
say "  ${D}⏺ MCP command: execute /Users/amin/work/webapp --prompt \"Add the limiter as one coherent commit and verify it\" --watch${N}"
slow 0.6
say "    ${D}Bash: git fetch origin main && git switch -c feat/api-rate-limit${N}"
slow 0.6
say "    ${D}Write: src/middleware/rate-limit.ts${N}"
slow 0.5
say "    ${D}Edit: src/app.ts${N}"
slow 0.6
say "  ${D}Jev notice #1: possible_deviation (0.94), job 5ca71312-843a-4591-9bdb-9755c2794269 still running${N}"
say "  ${D}Cited event: replacing the authentication middleware during rate-limit work${N}"
say "${MA}●${N} That expands the task. I'll steer it back to the agreed scope."
say "  ${D}⏺ MCP command: steer /explicit/child.steer.fifo \"Preserve auth; limit the change to rate limiting\"${N}"
say "  ${D}⏺ MCP command: wait 5ca71312-843a-4591-9bdb-9755c2794269 --after 1${N}"
slow 0.6
say "    ${D}Bash: npm test${N}  ${GR}✓ 42 passed${N}"
slow 0.7
say "    ${D}Commit: feat(api): add configurable rate limiter${N}"
slow 0.6
say "${MA}●${N} First commit is ready. A fresh reviewer will explain the change."
slow 0.4
say "  ${D}⏺ cerebro review /Users/amin/.cerebro/worktrees/api-rate-limit --base 7d914ea --explain${N}"
slow 1.4
say "${MA}●${N} 2 findings, both in scope: missing ${B}Retry-After${N} header,"
say "  refill race in the bucket. Applying them."
slow 0.5
say "  ${D}⏺ cerebro apply-review … --notes \"Retry-After; refill race\"${N}"
slow 1.0
say "  ${D}⏺ cerebro review … --base 3a8fc02 --explain${N}  ${GR}no findings${N}"
say "${MA}●${N} Review notes explain the limiter, ownership and failure paths."
say "  ${D}Open hunk diff 3a8fc02...HEAD in the task worktree to read the live notes.${N}"
say "  ${D}File-anchored notes are also available here; I’m continuing verification.${N}"
slow 0.8
say "${MA}●${N} Review is quiet. Verifying end to end against the running app."
slow 0.4
say "    ${D}playwright: 101 requests in 60s → ${N}${GR}429 + Retry-After ✓${N}"
slow 0.9
say ""
say "    ${D}Bash: gh pr create…${N} → ${YE}github.com/amin/webapp/pull/128${N}"
say "${GR}●${N} ${B}Done: PR #128 — focused commits, fresh review and verified behavior.${N}"
slow 4.0
