---
name: cerebro-commands
description: Command reference, model selection, delegation, bridge guards and durable job completion. Load for exact command syntax.
---
# Available subcommands

Use the Cerebro MCP `command` tool with an argv array excluding `cerebro`.
The CLI forms below describe the same arguments; do not put shell commands,
heredocs, pipes, redirects or tilde paths in argv. Pass absolute paths. For
large text, use a command's `--stdin` flag and the tool's `stdin` field:

```json
{"argv":["plan","--out","plan-1","--stdin"],"stdin":"<plan markdown>"}
```

Long child commands are detached automatically; the tool blocks on completion
and returns the final handoff, exit code, job ID and output path. Supervisor
work uses final handoffs and guarded inspection; live logs belong to observers.


  cerebro plan "<plan markdown>" [--out <name>] [--stdin] [--from-file <path>]
    Record a plan YOU wrote to sessions/<this-session>/plans/<name>.md
    (auto-numbered plan-N when --out is omitted); the path is echoed on
    stdout. This is how a plan you composed reaches disk -- same pattern
    as `cerebro spec set`. Draft the plan yourself from the conversation,
    the spec, and the read-only bridges (`cerebro grep/read/ls/git`). For
    the TECHNICAL plan, keep paths, function names, and file names concrete
    and grounded in code you actually inspected. Work like a lazy senior
    engineer: the SMALLEST change that satisfies the request -- no scope
    creep, no gold-plating, no future-proofing nobody asked for. The
    technical plan describes only the work itself; never mention branches,
    PRs, or orchestration mechanics in its body. Re-running with the same
    --out OVERWRITES the file -- that is how you revise a plan.

    Supply an inline argument, `--stdin`, or `--from-file <path>` — exactly
    one body source. Use MCP stdin for plans of any size; the parent does not
    need a scratch file or Write tool. `--from-file` ingests an existing file.

    COMPANION (human-readable plan). For every technical plan
    `<name>.md`, ALSO record a plain-English companion at
    `<name>-readable.md` via `cerebro plan "<readable md>" --out
    <name>-readable` (use MCP stdin as for the technical plan). The companion
    BEGINS with a reference block naming
    the technical plan's ABSOLUTE path and stating it is the source of
    truth, e.g.:
      > **Technical plan (source of truth -- this is what gets
      > executed):** `/abs/.../<name>.md`
      > If the two ever disagree, the technical plan wins.
    Its body is a FAITHFUL plain-English translation of the same plan --
    same goal, decisions, scope, and step order -- under headings like
    Goal, Approach, Key decisions & trade-offs, Steps (numbered),
    Acceptance criteria in plain terms. It strips the dense code
    references: NO file names, symbols, or call sites in the body (an
    explicit exception to the concrete-paths rule above) -- the ONLY path
    it contains is the reference to its technical plan. The technical
    plan remains the source of truth; the companion must never contradict
    it. Whenever you revise a technical plan, regenerate its companion so
    the two stay in sync; whenever you remove a plan (`cerebro plans rm
    <name>`), remove its companion too (`cerebro plans rm
    <name>-readable`).

  cerebro plans [rm <name>]
    List the plan files in the current session with timestamps. With
    `rm <name>`, delete a plan file -- use it when a mid-flight revision
    drops a step from a suite, so the stale plan cannot be listed or
    picked up later. Removal is confined to this session's plans dir.

  cerebro models [--json]
    List the model catalog the user maintains at
    $CEREBRO_HOME/models-config.json. Each entry has an `id` (the exact
    native model ID you pass to a subcommand's --model flag), a
    `capabilities` list (open set; the one that matters for delegation is
    `vision` -- multimodal image input, required to read browser
    screenshots), an optional integer `contextTokens` (the model's
    context-window size; cerebro exports it as CLAUDE_CODE_AUTO_COMPACT_WINDOW
    behind a custom claude endpoint so Claude Code doesn't fall back to 200k
    for an unrecognized id), and a free-text `description` of the model's fit
    (context window, reasoning depth, cost, etc.). Use this to CHOOSE the
    model per task: e.g. if the default review model lacks `vision`, pick
    a vision-capable entry from this list and pass it to `cerebro verify
    --model <id>`. The orchestrator's own model and the editing children
    default to CEREBRO_MODEL; the review/audit/verify/improve children
    default to CEREBRO_REVIEW_MODEL (a suggested different model, not a
    rule); --model overrides either default per call. A missing catalog
    prints a one-line note (or `--json` prints an empty array) -- then
    explicit model settings or the native backend default apply. You can fan a review
    across several models by calling `cerebro review --model <id>` once
    per catalog entry.

    A session and every child use the same backend. OpenCode requires native
    `provider/model` IDs; Codex and Claude accept their native model IDs.
    Model punctuation does not select a backend. Keep catalog choices relevant
    to this session's backend. Empty model settings use the native default;
    CEREBRO_REVIEW_MODEL otherwise inherits CEREBRO_MODEL. A different review
    model is optional; fresh read-only context provides independent review.

  cerebro model-env <id> [--no-compact]
    Print shell `export` lines that tell Claude Code the model's real context
    window (read from that catalog entry's `contextTokens`), for use before a
    direct `claude --model <id>` launch against a custom endpoint. Default
    exports CLAUDE_CODE_AUTO_COMPACT_WINDOW=<tokens>; --no-compact exports
    CLAUDE_CODE_MAX_CONTEXT_TOKENS=<tokens> + DISABLE_COMPACT=1 (true window,
    compaction off). A model with no contextTokens prints no exports. You do
    not normally call this from inside a session -- cerebro already exports the
    window for spawned children -- but it's the user-facing form for manual
    `claude` launches.

  cerebro audit <repo-abs-path> <plan-path> [--context "<text>"]
                [--out <name>] [--model <id>]
    Run the independent read-only reviewer against a plan you
    wrote, to check it against the ACTUAL code with fresh, independent
    eyes. It receives the plan file, the current session spec, and
    --context (pass the crucial context the auditor cannot otherwise
    know: key source paths, decisions already made, constraints from
    the conversation). It verifies reach (phantom or missed
    files/symbols/call sites), scope creep, over-engineering, and
    misread requirements, then writes Markdown findings to
    sessions/<this-session>/audits/<name>.md (default <plan-name>-audit;
    path echoed on stdout) ending with a single line
    `PLAN AUDIT: VIABLE` or `PLAN AUDIT: ISSUES FOUND`. READ the
    findings file. Re-auditing the same plan overwrites the findings
    file and resumes the same reviewer conversation, so the auditor keeps
    its earlier exploration across revision rounds. The audited
    <plan-path> is ALWAYS the technical `<name>.md`, never the
    `-readable` companion. --model overrides the default review model
    (CEREBRO_REVIEW_MODEL) for this call; see `cerebro models`.

  cerebro verify <repo-abs-path> (--plan <path> | --prompt "<text>")
                 [--context "<text>"] [--model <id>]
    Delegate the END-TO-END / visual verification of a shipped change to a
    verify subagent WITH browser capability (you do not have one). You
    CANNOT drive a running app, click a UI, or observe rendered behaviour
    yourself, so any e2e/visual check goes through this. Hand it
    the worktree path (the `<wt>` from a `cerebro execute`), the plan
    path (or --prompt for an ad-hoc check), and a --context string of
    what to observe. The verify subagent builds/runs the REAL deployment
    artifact the change ships, drives the actual user flow(s) the plan
    delivers with a real browser (or invokes the real entrypoint/CLI for
    a non-UI change), and JUDGES whether the plan's HIGH-LEVEL
    REQUIREMENTS are met end-to-end -- it is NOT a second nitpicky code
    review (that is `cerebro review`'s job); it does not raise style
    nits, naming, defensive-code suggestions, or contrived edge cases.
    It writes a Markdown report to
    sessions/<this-session>/children/verify-*.md (path echoed on stdout)
    whose FINAL line is exactly one of `VERIFY: PASS` (requirements met,
    used for real), `VERIFY: FAIL` (list which requirements are not met,
    with what was observed vs expected), or `VERIFY: BLOCKED` (genuine
    blocker -- no browser, credentials it lacks, an env it cannot reach;
    the agent ends with a question you relay to the user and resume via
    `cerebro answer`). READ the report; only `VERIFY: PASS` counts as the
    e2e check satisfied. When verify is BLOCKED, fall back to asking the
    user to test manually and wait for their confirmation. Browser
    verification reads screenshots, which needs the `vision` capability
    -- run `cerebro models` to find a vision-capable model and pass it
    with --model when the default review model lacks vision.

  cerebro improve <cerebro-repo-abs-path> [--context "<focus>"]
                  [--meta] [--model <id>]
    Run the independent read-only reviewer as an ANALYSIS agent over
    cerebro's accumulated agent traces under your home, to mine problems
    that RECUR across runs and propose the smallest fixes back into the
    harness -- the hill-climbing loop (load `cerebro-improve`). Pass the
    cerebro SOURCE repo (absolute) so the reviewer cites the real harness files;
    --context narrows where to look. It writes Markdown findings to
    sessions/<this-session>/improvements/improve.md (path echoed on
    stdout) ending with a single line `HILL CLIMB: ISSUES FOUND` or
    `HILL CLIMB: NO CHANGES RECOMMENDED`. It only ANALYSES and PROPOSES:
    READ the findings file and ROUTE each accepted item yourself (overlay
    set / learn-set); improve NEVER auto-applies a harness change. On
    failure no path is echoed. --model overrides the default review model
    for this call; see `cerebro models`.

  cerebro execute <repo-abs-path> (<plan-path> | --prompt "<text>")
                  [--base <branch>] [--branch <name>] [--pair] [--model <id>]
    Spawn a writable child on this session's backend that runs in an ISOLATED git worktree
    of <repo> (under $CEREBRO_HOME/worktrees/), never the user's live
    checkout -- so an agent can never disturb the user's working tree. It
    fetches the base branch, branches from the up-to-date base, implements
    the work, commits, pushes, and opens a PR via gh -- all inside the
    worktree (shared .git + remotes, so push and gh work normally). The
    default form takes a <plan-path> from `cerebro plan`; use it
    AFTER the user has read the plan and told you to proceed. That
    <plan-path> is ALWAYS the technical `<name>.md`, never the
    `-readable` companion (the child needs the technical detail). The
    `--prompt "<text>"` form skips the plan file and hands <text>
    straight to the child as the task to do -- use it only when the
    user has explicitly asked to skip planning.
    On success execute ANNOUNCES the worktree on stdout as
    `=== TASK WORKTREE: <path> (branch <B>) ===`. The worktree PERSISTS
    after the run, and you MUST pass that <path> as the <repo> argument
    for this task's follow-up review / apply-review / doc-write / restart
    (NOT the user's main checkout) so they act on the agent's actual work.
    --base <branch> and --branch <name> drive STACKED PRs (used by the
    multi-plan suite workflow below): --base pins the branch this PR
    forks from and targets (so plan 2 stacks on plan 1's branch instead
    of main), and --branch pins the new branch's exact name so you know
    it deterministically and can pass it as the next plan's --base. Omit
    both for a normal standalone PR off the repo's default base. Each
    plan gets its own worktree; execute always creates a fresh branch.
    If the plan file ends with an acceptance-criteria checkpoint, the
    child implements to meet it and self-verifies before opening the PR.
    AGENTS.md bootstrap: if the repo lacks AGENTS.md / CLAUDE.md at
    the root, execute auto-adds them from the user-editable templates
    at $CEREBRO_HOME/templates/ as a separate first commit before the
    plan work. Existing AGENTS.md / CLAUDE.md are never modified. You
    don't have to mention this explicitly to the user unless they ask.
    --pair enables live observation and steering (load `cerebro-pair`):
    another cerebro session can observe the live execute session and you
    can steer it. --model overrides the default editing model
    (CEREBRO_MODEL) for this call; see `cerebro models`. Note the execute
    child also self-verifies with a browser when the plan calls for e2e,
    so if it must read screenshots pick a vision-capable model.

  cerebro review <repo-abs-path> [--base <ref>] [--criteria-file <plan-path>] [--model <id>]
    Run the independent read-only reviewer against the current branch diff vs <ref>.
    Default base resolution: if a previous `cerebro review` ran in
    this session on the same repo + branch, the base defaults to the
    SHA that was HEAD at that time -- so re-reviews after an
    `apply-review` only inspect the new changes, not the entire PR
    diff again. Otherwise the default is the PR base from gh, then
    origin/HEAD, then `main`. Pass --base explicitly only when you
    deliberately want to widen the scope (e.g., the user asks for a
    full re-review). The findings file path is echoed on stdout.
    --criteria-file <plan-path> turns the review into a CHECKPOINT
    gate: the reviewer additionally checks the diff against every acceptance
    criterion in that plan and ends the findings file with a single
    line `ACCEPTANCE CRITERIA: MET` or `ACCEPTANCE CRITERIA: NOT MET`.
    Criteria that require browser/manual/network/CI tools outside the
    read-only reviewer may be labelled `EXTERNAL`; those are not
    review failures, and YOU must verify them separately before the
    checkpoint can pass.
    build/test/lint EXECUTION criteria (cargo build/test, eslint,
    cypress) are EXTERNAL by nature -- the read-only reviewer cannot
    run them, and the implementing child already verifies them before
    committing. The orchestrator gate DISREGARDS reviewer verdicts that are
    solely "could not run X in the read-only sandbox", and gates on real
    findings plus the implementing child's own build/test run. Do NOT
    bake a reviewer-instruction preamble into criteria files to exclude
    them (the plan/review agents correctly refuse it as review-gaming);
    enforce the exclusion at the orchestrator's gate DECISION.
    Pass the plan you just executed so the multi-plan suite workflow
    can decide whether to advance to the next plan. The --criteria-file
    plan is ALWAYS the technical `<name>.md`, never the `-readable`
    companion. Read the findings file (as always) to see the
    per-criterion verdicts and the bugs. --model overrides the default
    review model (CEREBRO_REVIEW_MODEL) for this call; see `cerebro models`.

  cerebro apply-review <repo-abs-path>
                       (<findings-path> [--notes "..."] | --prompt "<text>")
                       [--pair] [--model <id>]
    Spawn a writable child on this session's backend with cwd=<repo> to apply
    fixes on
    the current branch. The default form takes a <findings-path> from
    `cerebro review`. SCOPE: include in --notes only findings that are
    BOTH clearly within the scope of the plan being worked on AND
    genuinely important -- real bugs, regressions, security issues,
    data-loss or correctness problems, missing tests for the new
    behaviour. Do NOT forward minor or speculative suggestions, and do
    NOT forward anything that would over-engineer the code
    (gold-plating, defensive handling for cases that cannot occur,
    premature abstraction, or a broad rewrite where a small fix would
    do). Keep the applied change as small as the fix actually
    requires. Out-of-scope improvements (unrelated refactors, style
    nits in files the plan didn't touch, broader tech-debt
    suggestions) must be NAMED to the user in your chat summary but
    NOT forwarded as --notes. If you are genuinely unsure whether a
    finding is important enough to apply, or whether its fix would
    over-engineer the code, ASK the user before acting on it rather
    than applying it on your own.
    The <findings-path> is ALWAYS the path echoed by your most
    recent `cerebro review` -- never a name you reconstruct. If you
    omit it (and don't pass --prompt), apply-review uses the last
    review's findings for this repo+branch automatically.
    The `--prompt
    "<text>"` form skips the findings file and hands <text> straight
    to the child as the fix instruction -- use it only when the user
    has explicitly asked to skip review, e.g. for a
    merge conflict or a fix they already diagnosed. The child commits
    and pushes on the same branch, so the existing PR updates in
    place.

  cerebro doc-write <repo-abs-path>
                    (<plan-path> [--notes "..."] | --prompt "<text>")
                    [--pair] [--model <id>]
    Spawn a writable child on this session's backend with cwd=<repo> to update
    docs
    based on the plan and the recent diff. The <plan-path> is ALWAYS the
    technical `<name>.md`, never the `-readable` companion. The
    `--prompt "<text>"` form takes inline doc instructions instead of a
    plan file -- only when the user has explicitly asked to skip
    planning.
    Commits and pushes on the same branch.
    --pair enables live observation and steering (load `cerebro-pair`).

  cerebro answer <child-session-id> "<answer>" [--model <id>]
    Resume a child that PAUSED with a question (see "# When a child stops
    to ask a question") and deliver "<answer>" as its next turn, so it
    continues exactly where it stopped instead of redoing work. The
    child-session-id is printed in the child's closing-message banner.
    cerebro looks it up inside the CURRENT parent cerebro session, recovers
    the child role/repo metadata, and resumes that exact child. The child's
    closing message is surfaced (it may finish, or pause again with a
    further question).

  cerebro observe [<session-id>]
    Look over the shoulder of ANOTHER cerebro session's live `--pair`
    children. <session-id> names that orchestrator session (NOT a child);
    with none, the most recently active OTHER session that has live paired
    children is chosen. It tails that session's own transcript (the prompts
    it received and the cerebro actions it took) AND every live paired child
    at once and returns ONE batch of new activity, then a STATUS footer:
    `=== OBSERVE STATUS: active ===` (children still live -- call observe
    again) or `... done ===` (none left). A per-target cursor under your
    own session dir advances each call, so successive calls never repeat.
    Each call blocks up to a window (CEREBRO_OBSERVE_WINDOW, default 90s),
    returning early only after a longer quiet gap (CEREBRO_OBSERVE_QUIET,
    default 12s) so each batch is a substantial chunk rather than a trickle.
    Read-only navigation (reads, greps, listings) is filtered out; the batch
    carries the agent's reasoning, the code it writes, and the commands it
    runs. Read-only: it only reads the
    session's transcript and its children's logs and never disturbs them.
    The independent observer reads batches and narrates; steer a watched child with
    `cerebro steer <its-steer-pipe> "<message>"`.

  cerebro steer [<pipe>] "<message>"
    One-shot steering: inject a single instruction into a live `--pair`
    child and return at once. With ONE argument that argument is the
    message and the live paired session is found automatically (the usual
    case); with TWO, the first is the <pipe> path (from the child's PAIR
    MODE banner, to pick one when several run) and the second the
    message. The message becomes the child's next user turn. Runs from
    any directory. Steer only on explicit instruction or within preauthorized
    autosteering. Messages preserve their source: observer/supervisor
    corrections restore approved scope and cannot add user requirements.
    Steer is for small in-flight NUDGES ("don't forget tests"); to REPLACE
    a rogue agent that started wrong, use `cerebro restart` instead.

  cerebro restart [<pipe>] "<diagnosis>"
    Abandon a strayed paired `execute` child and relaunch it FRESH. When a
    paired child started from wrong assumptions or drifted from the spec
    so badly that steering its poisoned context is futile, restart reaps
    it, and cerebro UNCONDITIONALLY tears down everything the run produced:
    the fresh branch, its PR, and the task's worktree are all deleted (the
    user's main checkout was never touched, so there is nothing to revert
    there). Same arg shape as steer: ONE arg is the <diagnosis> (the live
    session is auto-discovered); TWO are <pipe> then <diagnosis>. The
    diagnosis is REQUIRED and is surfaced back to you in a
    `=== RESTART REQUESTED ===` block so you can correct the relaunch
    prompt. Restart requires explicit or prior authorization to abandon and
    clean that task; a scope correction alone does not authorize destruction.

  cerebro worktrees [cleanup]
    Manage the per-task execute worktrees under $CEREBRO_HOME/worktrees/.
    With no arg (or `list`) it reports every worktree with its branch,
    owning repo, and in-use verdict. With `cleanup` it removes the STALE
    ones -- a worktree is kept only when its branch has an OPEN PR, an
    in-flight/resumable cerebro child is on it, or it holds local commits
    not yet pushed/merged; anything it cannot positively clear is kept.
    Use it to GC finished tasks' worktrees.

  cerebro git <repo-abs-path> <git-subcmd> [args...]
    Run a read-only git command in the user's repo. Allowed subcommands
    include but are not limited to: status, log, diff, show, blame,
    ls-files, ls-tree, branch (list-only), remote (-v/show/get-url),
    rev-parse, cat-file, describe, tag --list, config --get/--list (no
    --file/--global/--system/--worktree), for-each-ref, stash list/show,
    reflog show, shortlog, name-rev, merge-base, rev-list, ls-remote,
    fetch (no --prune / --force / --shallow-* / --unshallow / --multiple /
    --update-head-ok / --recurse-submodules-default; bare `git fetch`
    works), count-objects, show-ref, show-branch, verify-commit,
    verify-tag, whatchanged, range-diff, diff-tree, diff-index,
    diff-files, grep (git-grep), check-ignore, check-attr,
    check-ref-format, var, help, version, patch-id, request-pull,
    merge-tree, get-tar-commit-id, fast-export (no --export-marks /
    --import-marks external file flags), archive (stdout only;
    --output denied), fsck (no --write-cache/--lost-found),
    hash-object (no -w), interpret-trailers (no --in-place),
    apply --check, bundle verify/list-heads, notes list/show,
    submodule status/summary, worktree list, replace --list,
    bisect view/log, symbolic-ref (read form only -- -d/--delete and
    the two-positional SET form are denied), column. diff --no-index,
    blame --contents, and similar path-arg-escape options are
    rejected. Anything else mutating (commit, push, checkout,
    branch -d, config --add, stash push, ...) is rejected. The wrapper
    invokes git via execve, so shell metacharacters in args are inert.
    Use this for "what does HEAD look like vs main", "show me the
    diff", "list branches".

  cerebro gh <repo-abs-path> <gh-subcmd> [args...]
    Run a read-only gh command. Allowed verbs by top:
      pr view/list/diff/checks/status
      issue view/list/status
      run view/list/watch
      repo view/list
      release view/list/verify
      search repos/prs/issues/code/commits
      auth status                     # auth token is NOT allowed
      workflow view/list              # `run` etc. denied
      ruleset view/list/check
      project view/list/field-list/item-list
      secret list, variable list/get, cache list, label list
      ssh-key list, gpg-key list, codespace view/list/logs/ports
        (ports: bare list form only -- forward/visibility denied)
      attestation verify              # download denied
      org list, alias list, config get/list
      extension list/search           # install denied (arbitrary code)
      gist view/list, licenses list/view
      status, completion              # bare tops, no verb
      api                             # GET only; -X/--method/-F/-f/--raw-field/--input denied
    Side-effecty / interactive tops (browse, copilot, preview,
    agent-task, co, skill) are rejected wholesale. Use this for
    "what's on the PR", "what did the CI run say", "is gh
    authenticated".

  cerebro read <repo-abs-path> <path> [--range N:M] [--strict-missing]
  cerebro read <abs-file-path> [--range N:M] [--strict-missing]
    Read one file. The two-positional form resolves <path>
    inside <repo>; symlinks or `..` that escape the repo are rejected.
    The single-positional form accepts an absolute path: cerebro tries
    to infer the enclosing git worktree (and resolves within it), and
    falls back to a bare-abs read otherwise. Bare-abs reads refuse
    /dev/*, /proc/*, /sys/* and anything that is not a regular file
    or directory. --range is 1-indexed inclusive; either side may be
    blank for open-ended. Accepted forms: --range N:M | --range N-M
    (digit-only sides) | --range N..M | --range N M | --range N |
    --range :M | --from N --to M. By default a missing or wrong-type
    in-bounds target is NOT an error: it prints `(not found: <path>)`
    to stdout and exits 0. --strict-missing restores the old exit 3.

  cerebro grep <repo-abs-path> <pattern> [--glob G]... [--type T]... [--fixed-strings] [-i] [--path SUB] [--strict-missing]
  cerebro grep <abs-dir-path> <pattern> [...same flags...]
    Ripgrep inside a user repo, or against any absolute directory.
    Hard caps: 200 matches per file, 400 columns per line. Common
    --type aliases (rs, tsx, jsx, yml, rb, kt) are mapped to their
    canonical rg type name; unknown values pass through unchanged.
    By default zero matches prints `(no matches)` to stdout and exits
    0, and a missing/wrong-type search path prints `(not found: ...)`
    and exits 0. --strict-missing restores rg-native semantics (exit 1
    for zero matches, exit 3 for a missing path).

  cerebro ls <repo-abs-path> [path] [--strict-missing]
  cerebro ls <abs-dir-path> [--strict-missing]
    List a directory inside a user repo, or any absolute directory.
    Trailing `/` marks subdirs. Bare-abs ls refuses /dev/*, /proc/*,
    /sys/*. By default a missing or wrong-type target prints
    `(not found: <path>)` to stdout and exits 0; --strict-missing
    restores the old exit 3.

  Exit codes for the bridges above: 2 usage, 4 subcommand not on the
  allow-list (git/gh), 5 denied flag (git/gh), 6 path escapes the repo
  or refused special path (/dev /proc /sys). For read/ls/grep, a benign
  in-bounds miss is NOT an error: the bridge prints `(not found: <path>)`
  (or `(no matches)` for grep) to stdout and exits 0. Pass
  --strict-missing to make a missing/wrong-type target exit 3 instead.
  git/gh's own non-zero exits propagate as-is; rg exit >=2 (e.g. bad
  regex) propagates, rg exit 1 (zero matches) is treated as success.
  Treat denied/usage failures as programmer error and adapt; do not
  retry the same denied call.

  Bridge usage (avoid silent false-empties):
    cerebro grep accepts ONLY --glob, --type, --fixed-strings, -i, --path,
    --strict-missing. It already prints path:line:match; native rg flags such
    as -n/-l/-A/-B are usage errors. Scope with --path <repo-relative-subdir>.
    Check every tool result's exit_code. Do not interpret blank output after
    a denied call as absence. A real miss prints `(no matches)` or
    `(not found: <path>)`; denied/usage diagnostics require a corrected call.
    For ls/read of a subpath, prefer the repo plus relative-path form.

  cerebro recall <query>
    Search across all cerebro sessions' transcripts and child logs.
    Use this when the user references prior work ("did we already do
    the rename in the orders service?"). The query is matched
    LITERALLY first; on a miss with a multi-word query it auto-
    broadens to "any term" (case-insensitive, first 100 hits) and
    prints a note saying so. Prefer one distinctive term per call.

  cerebro spec [show | set "<specification and requirements>" [--stdin] | history]
    The session spec -- the requirements of record for the task at hand.
      * `cerebro spec show` (or no action): print the current spec followed by a
        count of historical versions. Read this to re-ground yourself
        after a context compaction, or whenever you are unsure whether an
        in-flight adjustment still meets the requirements.
      * `cerebro spec set "<text>"` (or `spec set --stdin` with MCP stdin):
        record the current specification and requirements. The
        new text REPLACES the current spec; the prior version is archived
        to the append-only spec history first, so the full history is
        preserved. Call this BEFORE planning, and again every time the
        user adds, removes, or changes a requirement. Capture WHAT must
        be delivered and any constraints the user stated -- not your plan
        for how to do it. Use MCP stdin for large specs. Only user authority
        changes requirements; observer/supervisor steering preserves the
        existing spec rather than silently replacing it.
      * `cerebro spec history`: print every recorded version, oldest
        first, each with its timestamp -- the full evolution of the
        task's requirements across the session.

  cerebro status
    Print the current session state -- the session spec, plans on file,
    last child invocation, last review, and a learnings summary.

  cerebro learnings
    Print the active learned preferences and a count of pending
    signals. Read learnings.md at the start of each session; honor the
    preferences it contains by default unless the user overrides in the
    moment.

  cerebro learn-note "<observation>"
    Append ONE preference signal to the global pending journal
    (pending-learnings.md). Use it the moment the user reveals a
    general preference, directly ("always keep diffs small", "stop
    over-engineering") or indirectly (e.g. they repeatedly ask you to
    simplify, reject a heavy solution, or trim a review down to the
    essential fix). Write a single concrete sentence; don't editorialise.
    This only records evidence -- it does NOT change your behaviour yet.

  cerebro learn-set "<consolidated learnings>" [--stdin]
    REPLACE the active learnings (learnings.md) with a small,
    consolidated set you compose after reviewing clear, repeated
    evidence in the pending journal. Keep it to a few short, GENERAL bullets
    (cap ~1600 chars; the call is rejected if you exceed it). Before
    calling, read the current learnings.md and pending-learnings.md so
    you merge rather than clobber. Use MCP stdin for large bodies. Promote an
    explicit user directive or repeated clear evidence; ask about an ambiguous
    signal rather than inventing a preference.

  cerebro overlay set <target> "<text>"
  cerebro overlay show [<target>]
  cerebro overlay rm <target>
    Manage user-LOCAL harness overlays under $CEREBRO_HOME/overlays/.
    Each overlay is a plain-markdown file that you (or the user) can
    READ when relevant. `set` replaces the file; `show` prints one overlay
    (or, with no target, lists each target with present/absent + size);
    `rm` removes one. Targets include:
      * system       -> read for cross-cutting orchestrator behaviour
      * execute      -> read before implementing
      * apply-review -> read before applying review findings
      * doc-write    -> read before writing docs
      * grader       -> read before reviewing/auditing
      * meta-*       -> tune the improvement procedure (load cerebro-improve)
    Use learnings for durable cross-cutting preferences and overlays to
    tune a specific surface.

  cerebro jobs
    List this session's persistent detached jobs, including completed jobs.
    On resume use status/jobs first. Do not duplicate a task still running.

  cerebro wait <job-id|absolute-output.status>
    Block on the monitor's Unix completion socket and return its exit code.
    A completed job returns from the recorded final status. No child-state
    or log polling is needed. A waiter does not own the child.

  cerebro cancel <job-id>
    Terminate the monitor and its descendants only when cancellation is
    authorized. Preserve useful final evidence before deciding what comes next.

  cerebro detach --output <absolute-path> -- <child-subcommand> [...]
    Explicit CLI detachment for audit/improve/execute/review/apply-review/
    verify/doc-write. Output must be within this session or its private
    /tmp/cerebro-<session-id> directory. The MCP tool already detaches long
    commands; manual detachment is unnecessary for normal delegation.

  cerebro guide <skill-name>
    Load a shared workflow skill by name, for example cerebro-audit-gate,
    cerebro-suites, cerebro-child-flow, cerebro-pair or cerebro-improve.
