# Cerebro evaluation results

These results measure selected local CSV and persisted-job fixtures. They describe the recorded run; they are not a broad model benchmark or evidence of a statistically significant product advantage.

Product comparisons grade the same portable task outcome for every arm. Offline native fixture checks measure transport and lifecycle, not model effectiveness.

Only complete expected arm groups with identical case, repetition, and full model/effort settings enter comparative success, time, token, and cost aggregates. A failed or errored trial remains a failure in its matched unit; its elapsed time and recorded usage remain included. Missing arms are listed as incomplete.

Pass-rate intervals are descriptive 95% Wilson reference intervals on trials, conditional on selected tasks. They do not account for dependence between repetitions. Paired deltas use a deterministic percentile bootstrap resampling whole cases, retaining their repetitions together, only at 10 or more distinct cases. Below that threshold paired intervals are unavailable; no significance claim is made.

**Recorded trials:** 18. **Errors:** 0. [Sanitized trial data](trials.json) · [Aggregate statistics](aggregate.json).

## Run provenance

| Recorded field | Value |
| --- | --- |
| source_commit | `aaecbb084652d2958674261861f7f0a6ed59e592` |
| source_diff_sha256 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| created_at | `2026-10-04T13:10:41.584437+00:00` |
| native_version | `codex-cli 0.160.0` |
| repetitions | `3` |
| seed | `42` |
| scheduled_trials | `18` |

Per-file SHA256 source fingerprints are in [aggregate.json](aggregate.json). To rerun, use `evals/run.py` with the recorded case IDs, repetition count, seed, native CLI version, and the role model/effort and Jev settings below. `evals/README.md` documents the configuration and runner flags. Use a new output directory. The private Jev endpoint is represented by its SHA256 fingerprint; its URL and credentials are not published.

Prices are **estimates at the supplied API reference rates**, not an account bill. Rate date: 2026-10-03; [rate source](https://developers.openai.com/api/docs/pricing). Reference estimate at published Standard short-context API token rates, not an account bill. Excludes subscription credits, discounts, taxes, tool fees and regional, fast-mode or long-context premiums. Jev uniform input pricing is from https://typesafe.ai/blog/introducing-system-one-models-and-jev.

| Model | Input / 1M | Cached input / 1M | Cache-write input / 1M | Output / 1M |
| --- | ---: | ---: | ---: | ---: |
| gpt-5.6-terra | 2.0000 | 0.2000 | 2.5000 | 12.0000 |
| gpt-6-luna | 0.1000 | 0.0100 | 0.1250 | 0.5000 |
| gpt-6.1-sol | 2.0000 | 0.1000 | 2.5000 | 10.0000 |
| jev-1.13.0 | 0.0420 | 0.0420 | 0.0420 | 0.0000 |
| jev-latest | 0.0420 | 0.0420 | 0.0420 | 0.0000 |

Input counts include cached and cache-write tokens; those columns are subsets, not extra input. Known token totals can be partial. No missing worker, token count, or price is treated as zero. Total estimated cost requires complete worker coverage and applicable rates; unknown cache subdivisions are priceable only when all input rates are equal.

## Product comparison · capabilities · configuration 4457388d6b52

6 matched units across 2 distinct cases; 0 incomplete units (0 recorded trials excluded). 0 errors among all 18 recorded trials in this cohort.

Worker cap: 1 requested, 1 effective; timing: **isolated**. Arms run sequentially within each case/repeat group. Parallel timing measures shared load, not isolated latency.

| Role | Model | Effort |
| --- | --- | --- |
| implementation | gpt-6-luna | low |
| review | gpt-5.6-terra | medium |
| supervisor | gpt-5.6-terra | medium |

Jev model: `jev-latest`; confidence threshold: 0.8; parent deadline: 600s. Endpoint fingerprint: `17abdbd0cf748030a25a6c7693461b98ac2889b23590f11c185652ec125830e9`.

| Arm | Passed / trials | Pass rate | Wilson 95% | Seconds / attempted task | Errors |
| --- | ---: | ---: | ---: | ---: | ---: |
| Bare implementor | 0/6 | 0.0% | 0.0–39.0% | 73.30 | 0 |
| Bare supervisor model | 2/6 | 33.3% | 9.7–70.0% | 170.95 | 0 |
| Supervisor + implementor + reviewer + Jev | 4/6 | 66.7% | 30.0–90.3% | 372.55 | 0 |

Task outcomes are independent of role/workflow artifacts. `condition_valid` and `role_separated_success` are separate diagnostics; inspect their denominators before attributing gains to role separation. Unstarted interrupted trials stay in outcome denominators but have no timed sample.


| Arm | Valid condition / observed | Role-separated success / observed |
| --- | ---: | ---: |
| Bare implementor | 6/6 | not measured |
| Bare supervisor model | 6/6 | not measured |
| Supervisor + implementor + reviewer + Jev | 6/6 | 4/6 |

| Arm | Known input | Cached subset | Cache-write subset | Known output | Complete usage trials | Estimated USD / task | Priced trials |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Bare implementor | 911,097 | 832,000 | 0 | 18,528 | 6/6 | 0.004249 | 6/6 |
| Bare supervisor model | 1,047,643 | 917,504 | 0 | 51,335 | 6/6 | 0.176633 | 6/6 |
| Supervisor + implementor + reviewer + Jev | 5,939,694 | 4,027,392 | 0 | 154,388 | 6/6 | 0.341228 | 6/6 |

Deltas are **after − before**; positive seconds or USD mean additional resource use. Intervals in parentheses are paired case-bootstrap 95% intervals.

| Comparison | Δ pass (percentage points) | Improved / regressed / tied | Δ seconds / task | Δ estimated USD / task |
| --- | ---: | ---: | ---: | ---: |
| Bare implementor → Bare supervisor model | +33.3 (unavailable) | 2 / 0 / 4 | +97.6 (unavailable) | +0.172384 (unavailable) |
| Bare implementor → Supervisor + implementor + reviewer + Jev | +66.7 (unavailable) | 4 / 0 / 2 | +299.2 (unavailable) | +0.336979 (unavailable) |
| Bare supervisor model → Supervisor + implementor + reviewer + Jev | +33.3 (unavailable) | 2 / 0 / 4 | +201.6 (unavailable) | +0.164595 (unavailable) |

![Matched quality, time, recorded tokens and estimated price](cohort-01-bars.svg)

[PNG](cohort-01-bars.png) · [SVG](cohort-01-bars.svg)

![Quality versus wall time and estimated price](cohort-01-tradeoffs.svg)

[PNG](cohort-01-tradeoffs.png) · [SVG](cohort-01-tradeoffs.svg)

### Per-case outcomes and overhead

Each cell shows **passed / trials; mean seconds; mean estimated USD** on the same matched units.

| Case / capability | Bare implementor | Bare supervisor model | Supervisor + implementor + reviewer + Jev |
| --- | ---: | ---: | ---: |
| comparison-inventory-reservations / stateful-rules-and-scope | 0/3; 89.3s; $0.005251 | 1/3; 150.8s; $0.160261 | 3/3; 260.9s; $0.247788 |
| comparison-patch-apply / precise-parsing-and-scope | 0/3; 57.3s; $0.003247 | 1/3; 191.1s; $0.193005 | 1/3; 484.2s; $0.434668 |

### Recorded provider and role usage

These totals cover recorded workers only. Each token count includes its **known entries / recorded entries**. Completeness of all workers for a trial is reported above.

| Arm / provider / role / model | Input | Cached | Cache-write | Output | Complete entries | Estimated USD |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Bare implementor / openai / implementation / gpt-6-luna | 911,097 (6/6) | 832,000 (6/6) | 0 (6/6) | 18,528 (6/6) | 6/6 | 0.025494 |
| Bare supervisor model / openai / supervisor / gpt-5.6-terra | 1,047,643 (6/6) | 917,504 (6/6) | 0 (6/6) | 51,335 (6/6) | 6/6 | 1.059799 |
| Supervisor + implementor + reviewer + Jev / jev / jev-attention / jev-1.13.0 | 1,277,958 (140/140) | unknown (0/140) | unknown (0/140) | 55,396 (140/140) | 140/140 | 0.053674 |
| Supervisor + implementor + reviewer + Jev / openai / execute / gpt-6-luna | 2,266,713 (14/14) | 2,017,792 (14/14) | 0 (14/14) | 35,830 (14/14) | 14/14 | 0.062985 |
| Supervisor + implementor + reviewer + Jev / openai / review / gpt-5.6-terra | 1,357,586 (14/14) | 1,136,384 (14/14) | 0 (14/14) | 48,713 (14/14) | 14/14 | 1.254237 |
| Supervisor + implementor + reviewer + Jev / openai / supervisor / gpt-5.6-terra | 1,037,437 (6/6) | 873,216 (6/6) | 0 (6/6) | 14,449 (6/6) | 6/6 | 0.676473 |

### Recorded diagnostics

Cells show **sum / observed trials (mean)**; missing observations stay missing.

| Metric | Bare implementor | Bare supervisor model | Supervisor + implementor + reviewer + Jev |
| --- | ---: | ---: | ---: |
| accepted_steers | 0.0 / 6 (0.00) | 0.0 / 6 (0.00) | 0.0 / 6 (0.00) |
| ambiguous_source_ownership | 0.0 / 6 (0.00) | 0.0 / 6 (0.00) | 0.0 / 6 (0.00) |
| attempted_trial | 6.0 / 6 (1.00) | 6.0 / 6 (1.00) | 6.0 / 6 (1.00) |
| condition_valid | 6.0 / 6 (1.00) | 6.0 / 6 (1.00) | 6.0 / 6 (1.00) |
| drift_exposed | 0.0 / 6 (0.00) | 0.0 / 6 (0.00) | 0.0 / 6 (0.00) |
| failed_job_attempts | 0.0 / 6 (0.00) | 0.0 / 6 (0.00) | 0.0 / 6 (0.00) |
| functional_pass | 0.0 / 6 (0.00) | 2.0 / 6 (0.33) | 4.0 / 6 (0.67) |
| functional_success | 0.0 / 6 (0.00) | 2.0 / 6 (0.33) | 4.0 / 6 (0.67) |
| implementation_job_attempts | 0.0 / 6 (0.00) | 0.0 / 6 (0.00) | 15.0 / 6 (2.50) |
| independent_reviews | 0.0 / 6 (0.00) | 0.0 / 6 (0.00) | 6.0 / 6 (1.00) |
| jev_attention_enabled | 0.0 / 6 (0.00) | 0.0 / 6 (0.00) | 6.0 / 6 (1.00) |
| jev_batches | 0.0 / 6 (0.00) | 0.0 / 6 (0.00) | 140.0 / 6 (23.33) |
| jev_notices | 0.0 / 6 (0.00) | 0.0 / 6 (0.00) | 1.0 / 6 (0.17) |
| jev_observer_failures | 0.0 / 6 (0.00) | 0.0 / 6 (0.00) | 0.0 / 6 (0.00) |
| jev_provider_failures | 0.0 / 6 (0.00) | 0.0 / 6 (0.00) | 0.0 / 6 (0.00) |
| parent_seconds | 436.7 / 6 (72.79) | 1,022.4 / 6 (170.41) | 2,231.9 / 6 (371.98) |
| portable_checks_passed | 0.0 / 6 (0.00) | 2.0 / 6 (0.33) | 4.0 / 6 (0.67) |
| recorded_passing_test_runs | 6.0 / 6 (1.00) | 7.0 / 6 (1.17) | 14.0 / 6 (2.33) |
| role_separated_success | unknown | unknown | 4.0 / 6 (0.67) |
| scope_pass | 6.0 / 6 (1.00) | 6.0 / 6 (1.00) | 6.0 / 6 (1.00) |
| scope_preserved | 6.0 / 6 (1.00) | 6.0 / 6 (1.00) | 6.0 / 6 (1.00) |
| supervisor_source_edits | 0.0 / 6 (0.00) | 0.0 / 6 (0.00) | 0.0 / 6 (0.00) |
| task_success | 0.0 / 6 (0.00) | 2.0 / 6 (0.33) | 4.0 / 6 (0.67) |
| transient_unrelated_edit_count | 0.0 / 6 (0.00) | 0.0 / 6 (0.00) | 0.0 / 6 (0.00) |
| transient_unrelated_edits | 0.0 / 6 (0.00) | 0.0 / 6 (0.00) | 0.0 / 6 (0.00) |
| truthful_completion | 5.0 / 6 (0.83) | 6.0 / 6 (1.00) | 6.0 / 6 (1.00) |
| unfinished_jobs | 0.0 / 6 (0.00) | 0.0 / 6 (0.00) | 0.0 / 6 (0.00) |
| verified_delivery | 6.0 / 6 (1.00) | 6.0 / 6 (1.00) | 6.0 / 6 (1.00) |
| workspace_preserved | 6.0 / 6 (1.00) | 6.0 / 6 (1.00) | 6.0 / 6 (1.00) |
