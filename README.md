# Cerebro (archived)

> **This project is archived and no longer maintained.**

Cerebro was built to make AI coding agents more reliable by adding oversight. At
first that meant a strong supervisor that planned the work, a faster implementor
that wrote the code, and an independent reviewer. Later it meant Jev, a cheap
"System 1" classifier that watched a single agent and nudged it back on track.

That was useful when coding agents were less capable: they hallucinated,
drifted from the request, and needed planning and steering to finish real work.
Current agents rarely hallucinate and rarely need that kind of steering or
up-front planning. The original purpose is gone, so the project is archived.

## What the evaluations showed

The evidence is in [evals](evals/README.md):

- **Supervisor pipeline.** On frozen tasks with hidden graders, a single strong
  agent (Terra) delivered as often as the supervisor, implementor and reviewer
  pipeline (7 of 12 each across two hard-task runs). The pipeline took about
  twice the time and cost. Review did catch real defects a weak implementor
  left (Luna alone delivered 2 of 12), but a strong agent alone did as well.
- **Jev watching one agent.** 89 real corrections a user made to Codex agents
  were replayed through Jev with the context Jev would have had. Jev could not
  anticipate them: about 0.60 on held-out cases, where 0.5 is chance. A strong
  LLM given the same context did no better (0.58). Most corrections depended on
  knowledge or preferences only the user had, not on visible agent mistakes.

## What is here

The final version is a small Go command that runs Codex, Claude Code or Pi
headlessly while Jev watches its event stream:

- `cmd/cerebro`: the command.
- `internal/agent`: the native protocols for steering and interrupting a running
  agent, verified against Codex 0.160.0, Claude Code 2.1.289 and Pi 0.99.2.
- `internal/watch` and `internal/jev`: Jev's context and nudge policy.
- `cmd/jev-replay` and `evals/calibration`: the tools that replay recorded
  corrections through Jev.
- `evals/`: the frozen task fixtures, graders and every published result.

```sh
go build -o cerebro ./cmd/cerebro
JEV_API_KEY=... ./cerebro run --backend codex "Fix the parser's quoted-comma handling"
```
