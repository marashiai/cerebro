# cerebro

> **Archived. This project is no longer maintained.**
>
> Cerebro was useful when AI coding agents were less advanced. They hallucinated,
> drifted from the request, and needed planning and steering to finish real work.
> Current agents rarely hallucinate and rarely need that kind of steering or
> up-front planning. The original purpose is gone, so the project is archived.
>
> The evaluations agree:
>
> - [Second hard-task proof](evals/results/2026-10-04-hard-proof-2/interpretation.md)
>   and [first hard-task proof](evals/results/2026-10-04-hard-proof/interpretation.md):
>   across both, a single strong agent and Cerebro with Jev each delivered 7 of 12
>   tasks. Cerebro took about twice the time and cost.
> - [Supervisor judgment](evals/results/2026-10-04-lease-queue-proportion/interpretation.md):
>   bare Terra delivered 3 of 3, Cerebro 2 of 3 without Jev and 1 of 3 with it,
>   at 1.2–1.6 times the time. Correction cycles risked the deadline.
> - [Happy-path comparison](evals/results/2026-10-03-happy-path-jev/report.md):
>   both passed, with Cerebro adding time and cost.
>
> A later rewrite tried a single agent watched only by Jev; it is kept in the git
> history (commits `776e9de` to `184eee8`). Replaying 89 real corrections a user
> made to Codex agents showed that neither Jev nor a strong LLM could anticipate
> them from the agent's activity: about 0.60 and 0.58 area under the ROC curve on
> held-out cases, where 0.5 is chance.

**A strong supervisor, a focused implementor, and an independent review.**

Cerebro runs through your existing **Pi, Codex, or Claude Code** installation.
The supervisor inspects the repository and makes a concrete plan. One task packet
starts an implementor that does the coding and tests, then automatically starts
an independent reviewer. The supervisor adjudicates the implementation evidence
and original findings, and either finishes or delegates a focused correction.

All roles keep the backend's normal tools, authentication, configuration, and
repository instructions. Separate role contexts and controller-owned orchestration
provide cooperation. Cerebro does not add a tool security sandbox, mandatory
verifier/documentation agents, review ceremonies, or learning framework.

## Start

```bash
curl -fsSL https://raw.githubusercontent.com/aminmarashi/cerebro/main/install.sh | bash
cerebro
```

Describe the goal and repository path. Commits, pushes, PRs, and deployment follow
your instructions and the repository's delivery authority.

Cerebro requires the selected native CLI, `jq`, `python3`, and Git for coding tasks.
Pi is the default backend and requires version 0.99.2 or newer. Choose another
backend with `CEREBRO_BACKEND=codex` or `CEREBRO_BACKEND=claude`.

## Model settings

Set role defaults in `$CEREBRO_HOME/config.json` (`~/.cerebro/config.json` by default):

```json
{
  "backend": "claude",
  "supervisor_model": "your-native-supervisor-model",
  "model": "your-native-implementor-model",
  "review_model": "your-native-review-model"
}
```

Optional `supervisor_effort`, `implementor_effort`, and `review_effort` are passed
unchanged to the selected backend. Environment variables override config, and a
task packet can override implementor/reviewer settings. Omitted model and effort
settings preserve native defaults. Prefer a different configured reviewer model;
Cerebro reports the resolved choices, preserves same-model selections, and does
not claim diversity when native defaults are unresolved.

## Watch and recover

Optional Jev watching reads native events and wakes the supervisor for concerns.
In-scope progress stays quiet. The supervisor can record a concise disposition,
steer a correction, or cancel within the task's authority. Completion and notices
use blocking event notifications, with no polling loop.

Tasks, native conversation IDs, original handoffs, and logs survive disconnects.
Questions and incomplete work stop before review. Resuming continues only the
unfinished stage; completed implementation/review stages do not run again.
Native work remains steerable with Jev disabled.

See [usage](docs/USAGE.md), [architecture](docs/ARCHITECTURE.md), and
[offline checks](tests/README.md). [Evaluation artifacts](evals/README.md) document
measured runs and their model/backend settings; older results describe the
workflow that existed when they were collected.
