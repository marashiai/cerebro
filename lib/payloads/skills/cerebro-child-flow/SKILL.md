---
name: cerebro-child-flow
description: Child lifecycle guidance -- what to do when a child pauses with a question (answer from spec/recall yourself, else relay to the user, resume via cerebro answer), and how to resume after an interruption (cerebro status first, re-arm waits on live detached jobs, resume interrupted children with the same command instead of restarting). Invoke when a child paused with a question or you are resuming an interrupted child.
---
# When a child stops to ask a question

Every child you spawn (execute / apply-review / doc-write) runs
NON-INTERACTIVELY: there is no human at its keyboard, so it cannot ask a
question mid-run. It is told that when it hits a GENUINE blocker -- a
decision with real consequences it cannot responsibly make alone -- it
should STOP and end with that question as its FINAL message rather than
guess. So a child command can return having NOT finished the work: its
closing message is a question, not "PR opened" / "docs updated".

Watch for this. For execute / apply-review /
doc-write, the command surfaces the child's closing message under a
`----- <role> child closing message -----` banner in its output -- READ
it. If that message is a question (not a completion), the child is paused
and waiting; the PR/branch is half-done, not done. The banner also
prints `child session: <id>` and the exact `cerebro answer <id>
"<answer>"` form to use.

When a child paused with a question:

  1. Try to answer it YOURSELF first. Check the session spec, the plan /
     the user's stated requirements, `cerebro recall` (prior chats and
     decisions), and ordinary engineering judgement. If the answer is
     already settled there, you do NOT need to bother the user -- just
     answer.
  2. If you genuinely do not know -- the decision is the user's to make
     and nothing on record settles it -- ASK THE USER the same question
     (relay it plainly, with the child's options and recommendation), and
     wait for their reply.
  3. Deliver the answer with the printed `cerebro answer
     <child-session-id> "<answer>"` command. This RESUMES the same child
     session and feeds your answer as its next turn, so it continues from
     where it paused instead of restarting. After it returns, treat its
     output exactly like the original command's: it may now be done, or it
     may pause again with a further question -- loop back to step 1.

Do not guess on a decision that matters, and do not bounce a question to
the user that the spec or recall already answers. The point of the pause
is to get the RIGHT answer cheaply, not to redo work.

# Resuming after an interruption

A child agent (audit / execute / review / apply-review / doc-write) runs
as a detached cerebro job. If the parent session closes while one is running,
the child keeps running and its persistent job record remains discoverable.
If the child itself is interrupted, cerebro also persists its resumable
conversation id the instant it starts, so the work is not lost.

WHENEVER you resume a session, or the user says "continue", "pick up
where we left off", "carry on", or similar AND a child may have been
running: FIRST run `cerebro status` and read both its "detached jobs" and
"interrupted / in-flight children" sections. A detached job marked `running`
is still alive: do NOT launch a duplicate. Use the command tool to run
`wait <job-id>` and block on its completion notification, without log or
child-state polling. Completed jobs remain listed even days later; read their
final result and continue from the recorded handoff. Use
`cerebro jobs` to redisplay the registry directly. Use `cerebro cancel
<job-id>` only when the user asks to stop that work or the work is no longer
relevant; cancellation terminates the monitor and its full descendant tree.

For each interrupted child that has no live detached job, RESUME IT by re-issuing the SAME command you
ran before -- same role, same repo, same plan or prompt for execute, and
the same `--branch` when one was used. For apply-review / doc-write /
review, use the same branch checked out. cerebro keys incomplete execute
children on repo+role+branch+plan-or-prompt, finds the stored conversation
id, and relaunches the child with `--resume` so it continues its half-done
work instead of starting over and duplicating commits. Do NOT start a
fresh run for work that was already in flight; that would redo mutating
work.
A failed native resume preserves the child ID and reports the error; it never
silently launches a fresh conversation. Diagnose it before reissuing work.
If the listed child is no longer relevant (the user changed direction),
say so and move on rather than resuming it. Once a child finishes cleanly
it drops off this list; only incomplete (interrupted or failed) children
appear.
