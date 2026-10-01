---
name: cerebro-improve
description: The two-timescale hill-climbing self-improvement loop -- the fast loop (task-skill improvement via cerebro improve) and the slow loop (meta-skill improvement via --meta), routing accepted findings to overlays/learnings, and the proactive-but-occasional cadence. Invoke when running cerebro improve.
---
# The hill-climbing loop (two-timescale self-improvement)

Beyond learning one user's preferences, you can improve the HARNESS
itself by mining its own accumulated traces. This is a TWO-TIMESCALE
loop: the fast loop evolves task-level prompts (what the agent does), and
the slow loop evolves the improvement procedure itself (how the agent
improves) -- a bounded recursive self-improvement. Run this on the user's
request -- it complements, it does not replace, the live
preference learning described in `cerebro-commands`.

## Fast loop (task-skill improvement)

  1. MINE. Run `cerebro improve <cerebro-source-repo>` (the absolute
     path to the cerebro source, so the reviewer can cite the real harness
     files). It analyses the trace corpus under your home and writes
     findings ending in a `HILL CLIMB:` verdict line; the path(s) are
     echoed on stdout. It ONLY proposes -- it never changes the harness.
     Every 2 successful fast-loop runs (configurable via
     CEREBRO_META_HORIZON), the slow loop runs within that invocation. A
     chronological local history drives this schedule; it does not record
     proposal acceptance or prove that a proposal caused later outcomes.
  2. READ the findings file. Apply the SAME scope/importance gate you
     use on a review: take the smallest change that fixes a real,
     recurring problem; reject gold-plating, speculative additions, and
     anything already covered by learnings.md or an existing overlay.
  3. ROUTE each accepted item to its LOCAL apply target -- this works
     for EVERY user with no GitHub:
       * orchestrator behaviour -> `cerebro learn-set` (a durable
         preference) or `cerebro overlay set system`.
       * a child role prompt -> `cerebro overlay set <execute|
         apply-review|doc-write>`.
       * the review grader (audit or review) -> `cerebro overlay set grader` (read before reviewing/auditing).
     ONLY when the user maintains the cerebro source do shipped-payload
     changes ADDITIONALLY flow through the normal reviewed
     `plan` -> `execute` -> `review` loop (a real PR against the source).
  4. NEVER rewrite the harness unsupervised. `improve` proposes; you
     route accepted items through `cerebro learn-set` / `cerebro overlay set` (or an upstream PR);
     the user stays in the loop.

## Slow loop (meta-skill improvement)

  5. When the slow loop fires (automatically every H fast-loop runs, or
     manually via `cerebro improve <repo> --meta`), a SECOND findings file
     is written ending in a `META CLIMB:` verdict line. This file proposes
     changes to the improvement PROCEDURE itself -- the five meta-skill
     components that parameterise how `improve` diagnoses, retrieves,
     allocates, proposes, and routes:
       * `cerebro overlay set meta-analyzer` -- diagnosis policy (what
         to look for, how to tag failures).
       * `cerebro overlay set meta-retriever` -- trace sampling and
         cross-reference policy.
       * `cerebro overlay set meta-allocator` -- scope and effort
         allocation policy.
       * `cerebro overlay set meta-proposer` -- finding format and
         proposal structure.
       * `cerebro overlay set meta-evolver` -- routing and apply-target
         policy.
     Apply the SAME scope/importance gate: the smallest change to the
     improvement procedure that fixes a real, recurring meta-level problem.
     You can also force the slow loop with `cerebro improve <repo> --meta`
     when you see the same meta-level failure across improvement runs.

BE PROACTIVE about this loop -- it only helps if it actually runs, so
do not wait to be asked every time. At NATURAL checkpoints -- after
finishing a batch of work or shipping a feature, at the end of a work
session, or when the SAME friction recurs across runs (a child
repeating a mistake, reviews flagging the same class of issue, the user
making the same correction) -- OFFER to run `cerebro improve`, and run
it on the user's go. Keep it OCCASIONAL: an intermittent tune-up over
the accumulated traces, not a per-action step -- never run it every
turn and never interrupt active work to do it. Application stays
human-approved exactly as above: surface the findings, apply the
scope/importance gate, and route accepted items to `cerebro overlay
set` / `cerebro learn-set` (or, for the maintainer, the reviewed PR
loop). NEVER auto-apply.
