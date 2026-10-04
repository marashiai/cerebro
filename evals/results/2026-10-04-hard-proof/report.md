# Cerebro evaluation results

These results measure selected local CSV and persisted-job fixtures. They describe the recorded run; they are not a broad model benchmark or evidence of a statistically significant product advantage.

Product comparisons grade the same portable task outcome for every arm. Offline native fixture checks measure transport and lifecycle, not model effectiveness.

Only complete expected arm groups with identical case, repetition, and full model/effort settings enter comparative success, time, token, and cost aggregates. A failed or errored trial remains a failure in its matched unit; its elapsed time and recorded usage remain included. Missing arms are listed as incomplete.

Pass-rate intervals are descriptive 95% Wilson reference intervals on trials, conditional on selected tasks. They do not account for dependence between repetitions. Paired deltas use a deterministic percentile bootstrap resampling whole cases, retaining their repetitions together, only at 10 or more distinct cases. Below that threshold paired intervals are unavailable; no significance claim is made.

**Recorded trials:** 18. **Errors:** 1. [Sanitized trial data](trials.json) · [Aggregate statistics](aggregate.json).

## Run provenance

| Recorded field | Value |
| --- | --- |
| source_commit | `8c794bb2ff8f7f9640a6b92dbd931f23430a0b96` |
| source_diff_sha256 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| created_at | `2026-10-04T10:20:47.186008+00:00` |
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

6 matched units across 2 distinct cases; 0 incomplete units (0 recorded trials excluded). 1 errors among all 18 recorded trials in this cohort.

Worker cap: 1 requested, 1 effective; timing: **isolated**. Arms run sequentially within each case/repeat group. Parallel timing measures shared load, not isolated latency.

| Role | Model | Effort |
| --- | --- | --- |
| implementation | gpt-6-luna | low |
| review | gpt-5.6-terra | medium |
| supervisor | gpt-5.6-terra | medium |

Jev model: `jev-latest`; confidence threshold: 0.8; parent deadline: 600s. Endpoint fingerprint: `17abdbd0cf748030a25a6c7693461b98ac2889b23590f11c185652ec125830e9`.

| Arm | Passed / trials | Pass rate | Wilson 95% | Seconds / attempted task | Errors |
| --- | ---: | ---: | ---: | ---: | ---: |
| Bare implementor | 2/6 | 33.3% | 9.7–70.0% | 65.26 | 0 |
| Bare supervisor model | 5/6 | 83.3% | 43.6–97.0% | 151.73 | 0 |
| Supervisor + implementor + reviewer + Jev | 3/6 | 50.0% | 18.8–81.2% | 473.62 | 1 |

Task outcomes are independent of role/workflow artifacts. `condition_valid` and `role_separated_success` are separate diagnostics; inspect their denominators before attributing gains to role separation. Unstarted interrupted trials stay in outcome denominators but have no timed sample.


| Arm | Valid condition / observed | Role-separated success / observed |
| --- | ---: | ---: |
| Bare implementor | 6/6 | not measured |
| Bare supervisor model | 6/6 | not measured |
| Supervisor + implementor + reviewer + Jev | 5/5 | 3/5 |

| Arm | Known input | Cached subset | Cache-write subset | Known output | Complete usage trials | Estimated USD / task | Priced trials |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Bare implementor | 891,616 | 807,680 | 0 | 16,354 | 6/6 | 0.004108 | 6/6 |
| Bare supervisor model | 1,160,340 | 1,037,312 | 0 | 51,308 | 6/6 | 0.178202 | 6/6 |
| Supervisor + implementor + reviewer + Jev | 7,753,591 | 5,262,848 | 0 | 208,738 | 5/6 | unknown | 5/6 |

Deltas are **after − before**; positive seconds or USD mean additional resource use. Intervals in parentheses are paired case-bootstrap 95% intervals.

| Comparison | Δ pass (percentage points) | Improved / regressed / tied | Δ seconds / task | Δ estimated USD / task |
| --- | ---: | ---: | ---: | ---: |
| Bare implementor → Bare supervisor model | +50.0 (unavailable) | 4 / 1 / 1 | +86.5 (unavailable) | +0.174095 (unavailable) |
| Bare implementor → Supervisor + implementor + reviewer + Jev | +16.7 (unavailable) | 2 / 1 / 3 | +408.4 (unavailable) | unknown (unavailable) |
| Bare supervisor model → Supervisor + implementor + reviewer + Jev | -33.3 (unavailable) | 0 / 2 / 4 | +321.9 (unavailable) | unknown (unavailable) |

![Matched quality, time, recorded tokens and estimated price](cohort-01-bars.svg)

[PNG](cohort-01-bars.png) · [SVG](cohort-01-bars.svg)

![Quality versus wall time and estimated price](cohort-01-tradeoffs.svg)

[PNG](cohort-01-tradeoffs.png) · [SVG](cohort-01-tradeoffs.svg)

### Per-case outcomes and overhead

Each cell shows **passed / trials; mean seconds; mean estimated USD** on the same matched units.

| Case / capability | Bare implementor | Bare supervisor model | Supervisor + implementor + reviewer + Jev |
| --- | ---: | ---: | ---: |
| comparison-inventory-reservations / stateful-rules-and-scope | 2/3; 61.8s; $0.004139 | 2/3; 131.7s; $0.140494 | 2/3; 448.9s; $0.443705 |
| comparison-patch-apply / precise-parsing-and-scope | 0/3; 68.7s; $0.004077 | 3/3; 171.8s; $0.215911 | 1/3; 498.3s; $unknown |

### Recorded provider and role usage

These totals cover recorded workers only. Each token count includes its **known entries / recorded entries**. Completeness of all workers for a trial is reported above.

| Arm / provider / role / model | Input | Cached | Cache-write | Output | Complete entries | Estimated USD |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Bare implementor / openai / implementation / gpt-6-luna | 891,616 (6/6) | 807,680 (6/6) | 0 (6/6) | 16,354 (6/6) | 6/6 | 0.024647 |
| Bare supervisor model / openai / supervisor / gpt-5.6-terra | 1,160,340 (6/6) | 1,037,312 (6/6) | 0 (6/6) | 51,308 (6/6) | 6/6 | 1.069214 |
| Supervisor + implementor + reviewer + Jev / jev / jev-attention / jev-1.13.0 | 1,717,655 (175/175) | unknown (0/175) | unknown (0/175) | 73,870 (175/175) | 175/175 | 0.072142 |
| Supervisor + implementor + reviewer + Jev / openai / execute / gpt-6-luna | 3,079,344 (17/17) | 2,774,784 (17/17) | 0 (17/17) | 49,874 (17/17) | 17/17 | 0.083141 |
| Supervisor + implementor + reviewer + Jev / openai / review / gpt-5.6-terra | 1,729,230 (17/17) | 1,428,992 (17/17) | 0 (17/17) | 65,965 (17/17) | 16/17 | unknown |
| Supervisor + implementor + reviewer + Jev / openai / supervisor / gpt-5.6-terra | 1,227,362 (6/6) | 1,059,072 (6/6) | 0 (6/6) | 19,029 (6/6) | 5/6 | unknown |

### Recorded diagnostics

Cells show **sum / observed trials (mean)**; missing observations stay missing.

| Metric | Bare implementor | Bare supervisor model | Supervisor + implementor + reviewer + Jev |
| --- | ---: | ---: | ---: |
| accepted_steers | 0.0 / 6 (0.00) | 0.0 / 6 (0.00) | 0.0 / 5 (0.00) |
| ambiguous_source_ownership | 0.0 / 6 (0.00) | 0.0 / 6 (0.00) | 0.0 / 5 (0.00) |
| attempted_trial | 6.0 / 6 (1.00) | 6.0 / 6 (1.00) | 6.0 / 6 (1.00) |
| condition_valid | 6.0 / 6 (1.00) | 6.0 / 6 (1.00) | 5.0 / 5 (1.00) |
| drift_exposed | 0.0 / 6 (0.00) | 0.0 / 6 (0.00) | 0.0 / 5 (0.00) |
| failed_job_attempts | 0.0 / 6 (0.00) | 0.0 / 6 (0.00) | 0.0 / 5 (0.00) |
| functional_pass | 2.0 / 6 (0.33) | 5.0 / 6 (0.83) | 4.0 / 5 (0.80) |
| functional_success | 2.0 / 6 (0.33) | 5.0 / 6 (0.83) | 4.0 / 5 (0.80) |
| implementation_job_attempts | 0.0 / 6 (0.00) | 0.0 / 6 (0.00) | 14.0 / 5 (2.80) |
| independent_reviews | 0.0 / 6 (0.00) | 0.0 / 6 (0.00) | 5.0 / 5 (1.00) |
| jev_attention_enabled | 0.0 / 6 (0.00) | 0.0 / 6 (0.00) | 6.0 / 6 (1.00) |
| jev_batches | 0.0 / 6 (0.00) | 0.0 / 6 (0.00) | 148.0 / 5 (29.60) |
| jev_notices | 0.0 / 6 (0.00) | 0.0 / 6 (0.00) | 5.0 / 5 (1.00) |
| jev_observer_failures | 0.0 / 6 (0.00) | 0.0 / 6 (0.00) | 0.0 / 5 (0.00) |
| jev_provider_failures | 0.0 / 6 (0.00) | 0.0 / 6 (0.00) | 0.0 / 5 (0.00) |
| parent_seconds | 388.4 / 6 (64.74) | 907.3 / 6 (151.22) | 2,237.4 / 5 (447.48) |
| portable_checks_passed | 2.0 / 6 (0.33) | 5.0 / 6 (0.83) | 3.0 / 5 (0.60) |
| recorded_passing_test_runs | 6.0 / 6 (1.00) | 7.0 / 6 (1.17) | 9.0 / 5 (1.80) |
| role_separated_success | unknown | unknown | 3.0 / 5 (0.60) |
| scope_pass | 6.0 / 6 (1.00) | 6.0 / 6 (1.00) | 5.0 / 5 (1.00) |
| scope_preserved | 6.0 / 6 (1.00) | 6.0 / 6 (1.00) | 5.0 / 5 (1.00) |
| supervisor_source_edits | 0.0 / 6 (0.00) | 0.0 / 6 (0.00) | 0.0 / 5 (0.00) |
| task_success | 2.0 / 6 (0.33) | 5.0 / 6 (0.83) | 3.0 / 5 (0.60) |
| transient_unrelated_edit_count | 0.0 / 6 (0.00) | 0.0 / 6 (0.00) | 0.0 / 5 (0.00) |
| transient_unrelated_edits | 0.0 / 6 (0.00) | 0.0 / 6 (0.00) | 0.0 / 5 (0.00) |
| truthful_completion | 6.0 / 6 (1.00) | 6.0 / 6 (1.00) | 3.0 / 5 (0.60) |
| unfinished_jobs | 0.0 / 6 (0.00) | 0.0 / 6 (0.00) | 0.0 / 5 (0.00) |
| verified_delivery | 6.0 / 6 (1.00) | 6.0 / 6 (1.00) | 5.0 / 5 (1.00) |
| workspace_preserved | 6.0 / 6 (1.00) | 6.0 / 6 (1.00) | 5.0 / 5 (1.00) |
