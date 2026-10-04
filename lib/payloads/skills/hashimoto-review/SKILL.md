---
name: hashimoto-review
description: Review customer-facing code changes for regressions, security, and operational risk, then annotate the live diff in Hunk with enough architectural context for an engineer to explain and defend the shipped system. Use for PRs, commit ranges, release candidates, or recent merged work; do not use for quick prototypes where the user explicitly prioritizes speed over production understanding.
---

# Hashimoto Review

## Origin and standard

This workflow is inspired by Mitchell Hashimoto's ["whiteboard defense" post on X](https://x.com/mitchellh/status/2100249348345057389). It operationalizes his benchmark for responsible AI-assisted, customer-facing work: the engineer should be able to explain how a shipped system works, defend important choices and alternatives, reason about malicious actors, describe its data structures, and identify where it fails. This is an independently authored review workflow, not an official skill from or endorsement by Hashimoto.

Produce two outcomes together:

1. an evidence-backed engineering review; and
2. a coherent set of Hunk notes that lets the responsible engineer pass a whiteboard defense after reading them carefully.

Do not edit implementation code during a review unless the user separately asks for fixes. Hunk notes are sidecar review annotations, not source-code comments.

## Establish the review boundary

Resolve the exact base and head before judging the change. For stacked or recently merged work, review the final integrated diff once, then inspect individual PR or commit boundaries only where ownership, intent, or conflict resolution matters. Include shared code and deployment changes that can affect existing products, not only the new app's directory.

Read applicable repository instructions and the change's issue or PR descriptions. Treat stated intent and prior test claims as hypotheses to verify, not proof.

Classify the work as customer-facing, internal production, or experimental. Apply the full workflow to the first two. If the user explicitly labels it a disposable proof of concept or experiment, scale the depth to that request.

## Open the review in Hunk

When Hunk is installed, locate and read its bundled review skill before using the live-session API:

```sh
hunk skill path hunk-review
```

Load or install that skill through the agent's supported skill mechanism when needed. Treat it as the authoritative source for current Hunk commands; this section adds the Hashimoto-review policy on top of it.

Determine the exact command for the resolved range, such as:

```sh
hunk diff <base>...<head>
```

Use `hunk show <commit>` for one commit. Tell the user to run the exact command in another terminal from the repository root and leave the Hunk window open. Do this early so the user can read notes as they arrive. The Hunk TUI belongs to the user: never launch `hunk diff`, `hunk show`, or another interactive Hunk command yourself.

Check `command -v hunk`. If Hunk is unavailable, do not install it without authorization. Continue the code audit and show every prepared note directly to the user with the relevant code excerpt plus its exact file and line anchor. Clearly state that applying the notes to the live diff is waiting on Hunk, point the user to the official Hunk installation documentation, and repeat the exact command they should run. Never reduce or omit the teaching notes merely because Hunk is unavailable.

Once a live window exists, use Hunk's live-session interface rather than scraping its terminal UI. Inspect the session list first. If multiple sessions share a repository, select the user's window by exact session ID instead of using `--repo`:

```sh
hunk session list --json
hunk session get --repo . --json
hunk session review --repo . --json
```

Start with the structure-only review. Request `--include-patch` only for raw diff text the audit actually needs, and use `hunk session context` when current focus matters. Use the live review model as the source of file paths, 1-based hunk numbers, and old/new line anchors.

Inspect existing notes before writing. Navigate or focus the window so the user sees the relevant code. Prefer one validated `hunk session comment apply ... --stdin` batch when several prepared notes are ready; use `comment add` for a one-off note or reply. A batch item must contain `summary` plus either `replyTo`, or `filePath` and exactly one of `hunk`, `hunkNumber`, `oldLine`, or `newLine`. Use `--focus` sparingly to start the guided tour at its first note.

Afterward, verify every applied note with:

```sh
hunk session comment list --repo . --type all --json
```

Never clear, remove, or overwrite the user's Hunk notes. Avoid duplicating an existing note; reply when the new information belongs to an existing thread. Highlights are optional and visual-only; pair any important explanation with a persistent comment.

## Audit the change

Trace behavior across the real boundaries the diff touches:

- entry points, callers, services, queues, jobs, and external dependencies;
- authentication, authorization, trust boundaries, secrets, and tenant isolation;
- persistent records, ownership keys, indexes, caches, files, and lifecycle transitions;
- deployment configuration, migrations, rollout ordering, backward compatibility, and rollback;
- timeouts, retries, concurrency, quotas, resource bounds, partial failure, cleanup, and observability;
- user journeys in every existing product that consumes changed shared code.

For malicious-actor analysis, ask what an unauthenticated outsider, ordinary customer, compromised customer artifact, authenticated owner, malicious tenant, compromised internal service, and network-position attacker can do. Do not treat signed provenance as proof that content is safe, or a UI restriction as authorization.

Run the repository-required checks when permitted. Add focused tests or scanners only as evidence, and distinguish:

- verified behavior;
- reasoned inference;
- an untested assumption;
- an environmental blocker.

Check locked dependencies against a current authoritative advisory source. Separate vulnerabilities introduced by the reviewed range from pre-existing vulnerable dependencies that are nevertheless reachable from the new code. Absence of a scanner finding is not a source-level security review.

Report review findings by severity before the teaching narrative. A finding must include impact, a concrete trigger or actor, the affected location, and the evidence. Do not inflate theoretical concerns whose preconditions are excluded by the deployed architecture; do record those deployment assumptions explicitly.

## Write the whiteboard-defense notes

Choose the code locations that best explain the change, and arrange the notes in reading order. Prefer ownership boundaries, state transitions, policy enforcement, and failure handling over mechanically changed lines. Let the explanations determine how many notes are needed; do not squeeze unrelated points into one note to keep the count small.

The notes must form a standalone guided tour of the changed system. Across the complete set, teach:

- what the system does for the customer and the end-to-end request or event flow;
- which component owns each decision and piece of data;
- why this design was chosen over the most plausible alternative;
- the invariants and data structures that make it work;
- what a malicious or buggy actor can attempt and where it is stopped;
- where it fails, how failure is surfaced, and whether retry or rollback is safe;
- how an operator can detect, diagnose, recover, and verify it;
- what remains intentionally unresolved or risky.

Use a brief descriptive title when it helps navigation, followed by connected sentences that explain the code. The topics above guide the audit; they are not labels to fill in for every note. Assume the reader is an experienced engineer, but make the grammatical relationships explicit. Say which component does what, under which conditions, and why the result matters. Keep technical terms precise without joining them into shorthand that the reader must decode.

For example, "Expired-cache refresh failure preserves stale reads" names concepts without clearly connecting them. "If refreshing an expired cache entry fails, the reader receives the old value. Reads remain available, but callers can receive stale data" explains the behavior and its cost. This is an example of sentence construction, not a template for every note.

When discussing tests, describe the scenario and the result they assert. Distinguish the scenarios a test sets up from the properties its assertions actually check. A list of technologies or topics that tests "cover" does not explain what they establish. Mention test setup when it affects the strength or limits of the evidence; do not imply that a simulated dependency proves the real service works.

Before publishing, read each note as prose. Remove repeated details instead of the verbs, conditions, or connecting phrases that make it understandable. A note should explain an important point accurately on its first reading, without requiring the reader to reconstruct the sentence or guess how its terms relate.

Keep claims within the component and conditions the code supports. Follow a value back to its source when its meaning depends on another layer; do not attribute a guarantee to the whole system just because one function implements part of it. State uncertainty honestly. Never invent a rationale; label an inferred rationale and identify what would confirm it. Keep defect comments distinct from explanatory notes. A severe finding may also teach the violated invariant, but its summary must make the required action unmistakable.

## Test the engineer's understanding

After the Hunk notes are applied, provide a compact defense brief keyed to them:

- a 60-second system explanation;
- the three most consequential design decisions and alternatives;
- the main data model and ownership rules;
- the top malicious-actor scenario;
- the most likely failure and recovery path;
- the evidence supporting the release verdict;
- unresolved risks or checks still needed.

End with realistic questions the engineer should be able to answer, including:

- Why did we choose this boundary instead of the nearest alternative?
- What happens when a normal customer behaves maliciously?
- Which identifier or record is authoritative, and why?
- Where can this fail partially, and is retry safe?
- What breaks during rollout or rollback?
- Which claim rests on a deployment assumption rather than an enforced invariant?

The goal is not memorizing functions. It is being able to reconstruct the system, defend its choices, and name its limits without hand-waving.

## Deliver the verdict

Lead with whether the change is safe to ship, conditionally safe, or not ready. List findings in severity order, then regression evidence, security evidence, verification gaps, and the whiteboard-defense map. Link to exact local files and lines when available.

Do not say there are no breaking changes or vulnerabilities merely because tests pass. Use narrower language: what was tested, what was inspected, what actors and boundaries were considered, and what remains unknown.
