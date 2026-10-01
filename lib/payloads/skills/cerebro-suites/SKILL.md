---
name: cerebro-suites
description: Decompose a large spec into independently workable stacked PRs, with approval, audit and bounded review/runtime checkpoints.
---
# Large specifications: multi-plan suites

Use a suite only when the work needs genuinely dependent delivery steps. Each
step is one coherent, independently reviewable and mergeable PR. A small change
belongs in one plan. The session and every child stay on the same backend.
Use the guarded command tool with literal argv and stdin for plan/spec bodies.

## Keep every step workable

Each step must build, pass its required tests and preserve existing behavior on
its own. Never ship code that needs a later step to compile, restore a feature
or make an interface usable. Keep an inseparable change in one plan. If no
ordering satisfies this invariant, explain the obstruction and propose a
different cut before executing. Do not disguise a broken boundary as progress.

Implementation plans describe the requested work without branch, PR or suite
bookkeeping in their bodies. Boundaries must not smuggle in unrelated refactors,
shared abstractions or imagined future needs. Use behavioral acceptance criteria
rather than guessed third-party filenames. Verify changed and preserved behavior
through the real runtime; deleting a feature does not justify weakening valid
coverage of the remaining contract.

## Record the spec and decomposition, then obtain approval

1. Record the complete requirements using `spec set --stdin`. This is the
   contract against which every step is judged.
2. Write `<slug>-00-overview`: the ordered PR-sized steps, dependencies and why
   they together satisfy the spec. Record it with `plan --out <name> --stdin`.
3. Write `<slug>-NN-<short>` technical plans (zero-padded 01, 02, ...). Each
   ends with `## Acceptance criteria (checkpoint)`: concrete checks for the
   delivered behavior, the whole app building/passing tests, and the actual
   runtime user flow or CLI/endpoint to exercise. Scope each plan to its step.
4. Record a faithful `<name>-readable` companion for the overview and every
   technical plan. Its reference block names the technical plan's absolute path
   as source of truth; its body explains the same decisions and steps plainly.
   Regenerate the companion whenever its technical plan changes.
5. Show the ordered companions and acceptance criteria to the user and wait for
   approval of the decomposition. Existing explicit authorization takes
   precedence over this default.
6. Load `cerebro-audit-gate`: a suite is high risk. After approval and before
   execution, audit the technical plans once, passing the overview, dependencies
   and decisions in `--context`. Judge findings against the user's contract;
   apply valid corrections and regenerate affected companions. A discovery that
   changes requirements or invalidates the approved decomposition needs a user
   decision. Do not start a repeated audit loop unless the user requests it.

Always give audit, execute and review the technical plan, never its readable
companion. Companions are the paths shown to the user.

## Execute in order with one worktree per step

After approval, execute autonomously in order, advancing only after each
checkpoint passes. Plan 1 uses the repo's default base. Later plans use the
previous plan's branch as both branch source and PR target:

```text
execute <repo> <plan-1> --branch feat/<slug>-01
execute <repo> <plan-N> --base feat/<slug>-previous --branch feat/<slug>-NN
```

Use repository branch conventions. Capture each `TASK WORKTREE` path and pass
that worktree to its review, corrections, verification and documentation. Do
not perform follow-up work in the user's main checkout. Sequence mutations;
Cerebro does not serialize competing tasks against one repository.

## Gate each checkpoint on review and real verification

Run `review <worktree> --criteria-file <technical-plan>`. Read its findings and
require all of the following before advancing:

- Code-reviewable criteria are met and no important in-scope finding remains.
- Required builds/tests succeeded and the app remains workable.
- `verify <worktree> --plan <technical-plan>` exercised the actual runtime flow
  and reported `VERIFY: PASS`, or a blocked check received actual manual
  confirmation from the user.

A read-only reviewer cannot run builds, browsers or external CI. Its `EXTERNAL`
criteria require evidence from the implementation/verification child; lack of
reviewer capability alone is not a defect. Static `MET` without runtime evidence
is not a completed checkpoint. Do not alter criteria to hide a failed check.

## Correct within scope, then escalate when necessary

For an implementation defect, forward only important in-scope findings to
`apply-review <worktree>`, then repeat affected review and verification. Reject
nits, speculative hardening and unrelated changes. Limit corrections to three
attempts per checkpoint; if it still fails, report the unmet criteria, attempts
and next proposed decision to the user. Never loop indefinitely.

A wrong plan is a discovery to resolve, not another implementation retry. Stop
when an adjustment would change requirements, expand scope or break the approved
cut. Explain the discovery and obtain the needed decision. Then reconcile the
spec as authorized, rewrite affected plans/companions and overview, and continue
on the same worktree/branch with a scoped `apply-review --prompt`. Update the
text of already-executed plans with learned facts, but never redo their work.

When every checkpoint passes, report the PR stack, bases, delivered behavior and
verification so it can be reviewed and merged in order. Merge and push actions
still require authorization from the user and repository instructions.
