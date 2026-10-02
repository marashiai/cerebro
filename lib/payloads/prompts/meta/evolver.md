## Evolver -- routing and apply-target policy

The concrete LOCAL apply target for each finding, so ANY user applies it
offline with no GitHub:

  - orchestrator behaviour -> `cerebro learn-set` (a durable preference)
    or `cerebro overlay set system` (a broader orchestrator addition)
  - a child role prompt (execute / apply-review / doc-write) ->
    `cerebro overlay set <role>`
  - the session backend's grader (audit or review) -> `cerebro overlay set grader`
  - the improvement procedure itself (analyzer / retriever / allocator /
    proposer / evolver) -> `cerebro overlay set meta-<component>`
  - (maintainers only, optional) the SAME change upstreamed to the
    shipped payload via a normal reviewed PR -- name the real file too.

The harness surface list (ILLUSTRATIVE, not exhaustive -- GREP the repo
for the real definition site):
  - Supervisor: `lib/payloads/skills/cerebro-supervisor/SKILL.md`.
  - Children: `lib/payloads/skills/cerebro-worker/SKILL.md`, the upstream
    `engineering` skill, and `lib/payloads/prompts/noninteractive-note.md`.
  - Graders: the AUDIT grader at `lib/payloads/prompts/audit.md`; the REVIEW
    grader is at `lib/payloads/prompts/review.md`.
  - Improvement procedure: `lib/payloads/prompts/meta/{analyzer,retriever,allocator,proposer,evolver}.md`
  - Tool surfaces: `lib/python/command_server.py`; native role restrictions in
    `lib/backend-*.sh`; read-only bridges in `lib/commands/bridge.sh`
    (read/grep/ls) and `lib/commands/git.sh` / `lib/commands/gh.sh`.
  - Drift classification: `lib/python/scope_watch.py` and `lib/payloads/jev/questions.json`.
  - Already-applied state to avoid re-proposing: `learnings.md`,
    `overlays/*.md` (Read these and skip anything already addressed).

End with, as the VERY LAST line, exactly `HILL CLIMB: ISSUES FOUND` if you
filed at least one recurring issue, otherwise exactly `HILL CLIMB: NO
CHANGES RECOMMENDED`.
