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

# ----- act 1: ask -> plan -> go ---------------------------------------------
wipe
slow 0.4
say "${D}\$${N} ${B}cerebro${N}"
slow 0.6
say "${D}==> cerebro: starting session 3f2c91ae${N}"
say ""
slow 0.7

type_line "add rate limiting to the public API in ~/work/webapp"
slow 0.8
say "${MA}●${N} Capturing the requirements, then drafting a plan."
slow 0.4
say "  ${D}⏺ cerebro spec set \"Rate-limit the public API; 429 + Retry-After…\"${N}"
slow 0.6
say "  ${D}⏺ cerebro grep /Users/amin/work/webapp \"router.use\" --path src${N}"
slow 0.5
say "  ${D}⏺ cerebro plan \"# Rate-limit the public API…\" --out plan-1${N}"
slow 0.5
say "  ${D}⏺ cerebro plan \"# Rate-limit the public API (readable)…\" --out plan-1-readable${N}"
slow 1.4
say "${MA}●${N} Plan ready → ${YE}/Users/amin/.cerebro/sessions/3f2c91ae/plans/plan-1-readable.md${N}"
say "    1. Add a rate limiter that caps how often each client can hit the public API."
say "    2. Apply it to every public endpoint; the limits come from config, not hard-coded."
say "    3. Over-limit requests get a 429 with a Retry-After telling them when to come back."
say "  ${D}(plain-English companion; links to the technical plan it was written from)${N}"
say "  Read it and tell me to go."
say ""
slow 1.6

type_line "go"
slow 1.0

# ----- act 2: execute -> review loop -> verify -> done ----------------------
wipe
say "  ${D}⏺ cerebro execute /Users/amin/work/webapp plans/plan-1.md${N}"
slow 0.6
say "    ${D}Bash: git fetch origin main && git switch -c feat/api-rate-limit${N}"
slow 0.6
say "    ${D}Write: src/middleware/rate-limit.ts${N}"
slow 0.5
say "    ${D}Edit: src/app.ts${N}"
slow 0.6
say "    ${D}Bash: npm test${N}  ${GR}✓ 42 passed${N}"
slow 0.7
say "    ${D}Bash: gh pr create…${N} → ${YE}github.com/amin/webapp/pull/128${N}"
slow 0.6
say "${MA}●${N} PR ${B}#128${N} is open. Running independent review against the diff."
slow 0.4
say "  ${D}⏺ cerebro review /Users/amin/work/webapp${N}"
slow 1.4
say "${MA}●${N} 2 findings, both in scope: missing ${B}Retry-After${N} header,"
say "  refill race in the bucket. Applying them."
slow 0.5
say "  ${D}⏺ cerebro apply-review … --notes \"Retry-After; refill race\"${N}"
slow 1.0
say "  ${D}⏺ cerebro review …${N}  ${D}(incremental: only the new commits)${N}  ${GR}no findings${N}"
slow 0.8
say "${MA}●${N} Review is quiet. Verifying end to end against the running app."
slow 0.4
say "    ${D}playwright: 101 requests in 60s → ${N}${GR}429 + Retry-After ✓${N}"
slow 0.9
say ""
say "${GR}●${N} ${B}Done: PR #128 — planned, reviewed, applied, verified e2e.${N}"
slow 4.0
