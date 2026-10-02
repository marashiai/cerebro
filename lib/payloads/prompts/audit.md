Audit the implementation plan below (between the <plan> markers) against
the repository you are running in. The plan was written by an
orchestrator with full conversation context; you are the fresh pair of
eyes checking it against the actual code. Verify:

* Reach: does every file / symbol / call site the plan names actually
  exist, and did the plan find ALL the places that must change (grep
  for the callers, the interface implementors, the schema users)?
  Flag both phantom targets and missed ones.
* Scope creep: does the plan do MORE than the request -- extra files,
  options, endpoints, or steps the spec did not call for?
* Over-engineering: new abstractions, indirection, config knobs,
  backwards-compat shims, or defensive code for cases that cannot
  occur, where a smaller direct change would do.
* Misunderstanding: does the plan misread the requirement or the code
  -- solving a different or larger problem than the spec describes, or
  contradicting how the code actually works?

Verify against the code, not against plausibility -- ground every
finding in something you actually read. Keep the audit proportional:
targeted lookups, not a re-derivation of the plan. For each issue give
a one-line title, the plan step and file/symbol affected, and a
sentence of evidence with the suggested fix. Do not restate the plan or
pad with praise. Output Markdown only; no preamble. End with, as the
VERY LAST line, exactly `PLAN AUDIT: VIABLE` if the plan is correctly
scoped and grounded in the real code, otherwise exactly
`PLAN AUDIT: ISSUES FOUND`.
