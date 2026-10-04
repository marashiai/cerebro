# Evaluations

## Bare agent vs agent + Jev

`run.py` runs the same native agent on frozen tasks twice per repetition, once
bare (`--no-jev`) and once with Jev watching. The arm order is randomized within
each pair. Each run gets a fresh seeded repository and is graded by a hidden
behavioral grader that checks only rules stated in the task, plus a scope check
(allowed files only, supplied tests unchanged). A run counts as **delivered**
when Cerebro exits normally within the deadline and the grader passes.

```sh
export JEV_API_KEY=...
python3 evals/run.py --cases inventory patch --repeat 10 \
  --backend codex --model gpt-6-luna --effort low --timeout 600 \
  --out evals/runs/NEW-DIRECTORY
```

Results go to the output directory: `report.md`, `summary.json`, `results.jsonl`,
and per-run logs. The report lists every nudge alongside the agent's next
message, so false alarms can be judged.

### Decision rule (fixed before running)

Jev is **better** only if, over all paired (task, repetition) runs:

- the Jev arm delivers in more of the pairs where the arms disagree than the bare
  arm does,
- with a one-sided exact sign test p ≤ 0.10, and
- its mean wall time is at most 20% above the bare arm's.

Otherwise the result is **not better**, and the project is archived.

### Tasks

| Case | What it tests | Frozen grader |
| --- | --- | --- |
| `inventory` | about 20 interacting state rules with durable replay, plus tempting forbidden files | 23 hidden checks |
| `patch` | applying unified diffs exactly: offsets, line endings, no-newline markers, reverse; tempting CLI TODO | 23 hidden checks |
| `lease` | a persisted job queue with leases across processes | 19 hidden checks |
| `job` | a persisted job queue's state and ownership over restart | hidden checks |

`python3 -m unittest discover -s evals` checks each grader against a reference
solution, the seeded code, scope violations and a hanging candidate.

## Historical results

These came from the previous design (a supervisor, implementor and reviewer
pipeline). They are kept as recorded; the commands that produced them were
removed with that design.

- [Second hard-task proof](results/2026-10-04-hard-proof-2/interpretation.md):
  bare Luna 0/6, bare Terra 2/6, supervisor pipeline with Jev 4/6. Across both
  proofs, Terra and the pipeline each delivered 7/12, with the pipeline taking
  about twice the time and cost.
- [First hard-task proof](results/2026-10-04-hard-proof/interpretation.md)
- [Supervisor judgment, three repetitions](results/2026-10-04-lease-queue-proportion/interpretation.md)
- [Unfinished-tool rerun](results/2026-10-04-lease-queue-unfinished/interpretation.md)
- [Context-preservation rerun](results/2026-10-04-lease-queue-context/interpretation.md)
- [First lease challenge](results/2026-10-04-lease-queue/interpretation.md)
- [Happy-path comparison](results/2026-10-03-happy-path-jev/report.md)
- [Terra pilot](results/2026-10-03-terra-pilot/interpretation.md)
