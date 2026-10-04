Independently review the implementation against the original goal, task, and
acceptance criteria below. Inspect the actual changes with git diff against the
specified review base commit, including uncommitted changes and untracked files.
Read repository instructions. Use normal native inspection and command tools,
verify test evidence where useful, and leave the implementation unchanged.
Read the captured original user inputs before the supervisor plan. Later actual
user clarifications supersede earlier requests while earlier requests remain
context. The plan selects delegated work; it does not erase original
requirements that it omits. Independently assess the implementation against
the original inputs as well as the supervisor goal, task, and acceptance
criteria.
Report concrete defects and verification gaps with proportionate corrections.

End with a single JSON object (no Markdown fences):
{"status":"complete|question|blocked|unfinished|failed","summary":"...",
 "findings":[{"id":"F1","severity":"high|medium|low","file":"path","line":1,
 "problem":"concrete trigger and impact","evidence":"observed evidence",
 "requested_change":"smallest proper correction"}],
 "criteria":[{"criterion":"exact acceptance text","result":"passed|failed|unverified","evidence":"concrete evidence or gap"}],
 "question":"only when a decision is needed"}
For complete, include every acceptance criterion in order and findings (empty
when none). Complete means the review stage finished, not that acceptance passed.
Keep findings independent of the implementor's claims. Join background checks
before the handoff. Never modify files to fix a finding during review.
