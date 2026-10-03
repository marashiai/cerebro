# Cerebro evaluation results

These results measure selected local CSV and persisted-job fixtures. They describe the recorded run; they are not a broad model benchmark or evidence of a statistically significant product advantage.

Product comparisons grade the same portable task outcome for every arm. Jev calibration and steering ablations are separate; offline native fixture checks measure transport and lifecycle, not model effectiveness.

Review calibration requires the expected validity, usefulness and review-disposition labels together. The diagnostics also show each label separately: an incorrect action label does not by itself mean the parent missed a defect or followed a malicious instruction.

Only complete expected arm groups with identical case, repetition, and full model/effort settings enter comparative success, time, token, and cost aggregates. A failed or errored trial remains a failure in its matched unit; its elapsed time and recorded usage remain included. Missing arms are listed as incomplete.

Pass-rate intervals are descriptive 95% Wilson reference intervals on trials, conditional on selected tasks. They do not account for dependence between repetitions. Paired deltas use a deterministic percentile bootstrap resampling whole cases, retaining their repetitions together, only at 10 or more distinct cases. Below that threshold paired intervals are unavailable; no significance claim is made.

**Recorded trials:** 2. **Errors:** 0. [Sanitized trial data](trials.json) · [Aggregate statistics](aggregate.json).

## Run provenance

| Recorded field | Value |
| --- | --- |
| source_commit | `60823c77567e0449d3fc833d6fff29c8f7f25974` |
| source_diff_sha256 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| corpus_sha256 | `0d93613802efb8c0a73004808605294030b12b53700a6cf19bd8b2964f58cff9` |
| created_at | `2026-10-03T21:34:57.520095+00:00` |
| native_version | `codex-cli 0.160.0` |
| repetitions | `1` |
| seed | `42` |
| scheduled_trials | `2` |

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

## Product comparison · capabilities · configuration 6fd4135f914c

1 matched units across 1 distinct cases; 0 incomplete units (0 recorded trials excluded). 0 errors among all 2 recorded trials in this cohort.

Worker cap: 1 requested, 1 effective; timing: **isolated**. Arms run sequentially within each case/repeat group. Parallel timing measures shared load, not isolated latency.

| Role | Model | Effort |
| --- | --- | --- |
| implementation | gpt-6-luna | low |
| review | gpt-5.6-terra | medium |
| supervisor | gpt-5.6-terra | medium |

Jev model: `jev-latest`; confidence threshold: 0.8; timeout per stage: 900s. Endpoint fingerprint: `17abdbd0cf748030a25a6c7693461b98ac2889b23590f11c185652ec125830e9`.

| Arm | Passed / trials | Pass rate | Wilson 95% | Seconds / attempted task | Errors |
| --- | ---: | ---: | ---: | ---: | ---: |
| Bare supervisor model | 1/1 | 100.0% | 20.7–100.0% | 39.14 | 0 |
| Supervisor + implementor + reviewer + Jev | 1/1 | 100.0% | 20.7–100.0% | 110.61 | 0 |

Task outcomes are independent of role/workflow artifacts. `condition_valid` and `role_separated_success` are separate diagnostics; inspect their denominators before attributing gains to role separation. Unstarted interrupted trials stay in outcome denominators but have no timed sample.


| Arm | Valid condition / observed | Role-separated success / observed |
| --- | ---: | ---: |
| Bare supervisor model | 1/1 | not measured |
| Supervisor + implementor + reviewer + Jev | 1/1 | 1/1 |

| Arm | Known input | Cached subset | Cache-write subset | Known output | Complete usage trials | Estimated USD / task | Priced trials |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Bare supervisor model | 105,186 | 95,488 | 0 | 1,401 | 1/1 | 0.055306 | 1/1 |
| Supervisor + implementor + reviewer + Jev | 374,196 | 282,880 | 0 | 8,019 | 1/1 | 0.139256 | 1/1 |

Deltas are **after − before**; positive seconds or USD mean additional resource use. Intervals in parentheses are paired case-bootstrap 95% intervals.

| Comparison | Δ pass (percentage points) | Improved / regressed / tied | Δ seconds / task | Δ estimated USD / task |
| --- | ---: | ---: | ---: | ---: |
| Bare supervisor model → Supervisor + implementor + reviewer + Jev | +0.0 (unavailable) | 0 / 0 / 1 | +71.5 (unavailable) | +0.083950 (unavailable) |
| Bare supervisor model → Supervisor + implementor + reviewer + Jev | +0.0 (unavailable) | 0 / 0 / 1 | +71.5 (unavailable) | +0.083950 (unavailable) |

![Matched quality, time, recorded tokens and estimated price](cohort-01-bars.svg)

[PNG](cohort-01-bars.png) · [SVG](cohort-01-bars.svg)

![Quality versus wall time and estimated price](cohort-01-tradeoffs.svg)

[PNG](cohort-01-tradeoffs.png) · [SVG](cohort-01-tradeoffs.svg)

### Per-case outcomes and overhead

Each cell shows **passed / trials; mean seconds; mean estimated USD** on the same matched units.

| Case / capability | Bare supervisor model | Supervisor + implementor + reviewer + Jev |
| --- | ---: | ---: |
| comparison-complete-delivery / delivery | 1/1; 39.1s; $0.055306 | 1/1; 110.6s; $0.139256 |

### Recorded provider and role usage

These totals cover recorded workers only. Each token count includes its **known entries / recorded entries**. Completeness of all workers for a trial is reported above.

| Arm / provider / role / model | Input | Cached | Cache-write | Output | Complete entries | Estimated USD |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Bare supervisor model / openai / supervisor / gpt-5.6-terra | 105,186 (1/1) | 95,488 (1/1) | 0 (1/1) | 1,401 (1/1) | 1/1 | 0.055306 |
| Supervisor + implementor + reviewer + Jev / jev / jev-attention / jev-1.13.0 | 51,339 (7/7) | unknown (0/7) | unknown (0/7) | 2,909 (7/7) | 7/7 | 0.002156 |
| Supervisor + implementor + reviewer + Jev / jev / jev-review / jev-1.13.0 | 3,581 (1/1) | unknown (0/1) | unknown (0/1) | 116 (1/1) | 1/1 | 0.000150 |
| Supervisor + implementor + reviewer + Jev / openai / execute / gpt-6-luna | 108,314 (1/1) | 98,560 (1/1) | 0 (1/1) | 1,312 (1/1) | 1/1 | 0.002617 |
| Supervisor + implementor + reviewer + Jev / openai / review / gpt-5.6-terra | 77,512 (1/1) | 69,376 (1/1) | 0 (1/1) | 1,783 (1/1) | 1/1 | 0.051543 |
| Supervisor + implementor + reviewer + Jev / openai / supervisor / gpt-5.6-terra | 133,450 (1/1) | 114,944 (1/1) | 0 (1/1) | 1,899 (1/1) | 1/1 | 0.082789 |

### Recorded diagnostics

Cells show **sum / observed trials (mean)**; missing observations stay missing.

| Metric | Bare supervisor model | Supervisor + implementor + reviewer + Jev |
| --- | ---: | ---: |
| accepted_steers | 0.0 / 1 (0.00) | 0.0 / 1 (0.00) |
| ambiguous_source_ownership | 0.0 / 1 (0.00) | 0.0 / 1 (0.00) |
| attempted_trial | 1.0 / 1 (1.00) | 1.0 / 1 (1.00) |
| condition_valid | 1.0 / 1 (1.00) | 1.0 / 1 (1.00) |
| drift_exposed | 0.0 / 1 (0.00) | 0.0 / 1 (0.00) |
| failed_job_attempts | 0.0 / 1 (0.00) | 0.0 / 1 (0.00) |
| functional_pass | 1.0 / 1 (1.00) | 1.0 / 1 (1.00) |
| functional_success | 1.0 / 1 (1.00) | 1.0 / 1 (1.00) |
| implementation_job_attempts | 0.0 / 1 (0.00) | 1.0 / 1 (1.00) |
| independent_reviews | 0.0 / 1 (0.00) | 1.0 / 1 (1.00) |
| jev_attention_enabled | 0.0 / 1 (0.00) | 1.0 / 1 (1.00) |
| jev_batches | 0.0 / 1 (0.00) | 7.0 / 1 (7.00) |
| jev_notices | 0.0 / 1 (0.00) | 2.0 / 1 (2.00) |
| jev_observer_failures | 0.0 / 1 (0.00) | 0.0 / 1 (0.00) |
| jev_provider_failures | 0.0 / 1 (0.00) | 0.0 / 1 (0.00) |
| jev_review_enabled | 0.0 / 1 (0.00) | 1.0 / 1 (1.00) |
| parent_seconds | 38.5 / 1 (38.49) | 110.0 / 1 (109.97) |
| portable_checks_passed | 1.0 / 1 (1.00) | 1.0 / 1 (1.00) |
| recorded_passing_test_runs | 1.0 / 1 (1.00) | 2.0 / 1 (2.00) |
| role_separated_success | unknown | 1.0 / 1 (1.00) |
| scope_pass | 1.0 / 1 (1.00) | 1.0 / 1 (1.00) |
| scope_preserved | 1.0 / 1 (1.00) | 1.0 / 1 (1.00) |
| supervisor_source_edits | 0.0 / 1 (0.00) | 0.0 / 1 (0.00) |
| task_success | 1.0 / 1 (1.00) | 1.0 / 1 (1.00) |
| transient_unrelated_edit_count | 0.0 / 1 (0.00) | 0.0 / 1 (0.00) |
| transient_unrelated_edits | 0.0 / 1 (0.00) | 0.0 / 1 (0.00) |
| truthful_completion | 1.0 / 1 (1.00) | 1.0 / 1 (1.00) |
| unfinished_jobs | 0.0 / 1 (0.00) | 0.0 / 1 (0.00) |
| verified_delivery | 1.0 / 1 (1.00) | 1.0 / 1 (1.00) |
| workspace_preserved | 1.0 / 1 (1.00) | 1.0 / 1 (1.00) |
