# Cerebro evaluation results

These results measure selected local CSV and persisted-job fixtures. They describe the recorded run; they are not a broad model benchmark or evidence of a statistically significant product advantage.

Product comparisons grade the same portable task outcome for every arm. Jev calibration and steering ablations are separate; offline native fixture checks measure transport and lifecycle, not model effectiveness.

Review calibration requires the expected validity, usefulness and review-disposition labels together. The diagnostics also show each label separately: an incorrect action label does not by itself mean the parent missed a defect or followed a malicious instruction.

Only complete expected arm groups with identical case, repetition, and full model/effort settings enter comparative success, time, token, and cost aggregates. A failed or errored trial remains a failure in its matched unit; its elapsed time and recorded usage remain included. Missing arms are listed as incomplete.

Pass-rate intervals are descriptive 95% Wilson reference intervals on trials, conditional on selected tasks. They do not account for dependence between repetitions. Paired deltas use a deterministic percentile bootstrap resampling whole cases, retaining their repetitions together, only at 10 or more distinct cases. Below that threshold paired intervals are unavailable; no significance claim is made.

**Recorded trials:** 9. **Errors:** 3. [Sanitized trial data](trials.json) · [Aggregate statistics](aggregate.json).

## Run provenance

| Recorded field | Value |
| --- | --- |
| source_commit | `14d18d46b9055033d3b7f0b989f267fa611eb36c` |
| source_diff_sha256 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| corpus_sha256 | `0d93613802efb8c0a73004808605294030b12b53700a6cf19bd8b2964f58cff9` |
| created_at | `2026-10-04T08:06:36.743304+00:00` |
| native_version | `codex-cli 0.160.0` |
| repetitions | `3` |
| seed | `42` |
| scheduled_trials | `9` |

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

## Product comparison · capabilities · configuration 8faa3267495d

3 matched units across 1 distinct cases; 0 incomplete units (0 recorded trials excluded). 3 errors among all 9 recorded trials in this cohort.

Worker cap: 1 requested, 1 effective; timing: **isolated**. Arms run sequentially within each case/repeat group. Parallel timing measures shared load, not isolated latency.

| Role | Model | Effort |
| --- | --- | --- |
| implementation | gpt-6-luna | low |
| review | gpt-5.6-terra | medium |
| supervisor | gpt-5.6-terra | medium |

Jev model: `jev-latest`; confidence threshold: 0.8; parent deadline: 300s. Endpoint fingerprint: `17abdbd0cf748030a25a6c7693461b98ac2889b23590f11c185652ec125830e9`.

| Arm | Passed / trials | Pass rate | Wilson 95% | Seconds / attempted task | Errors |
| --- | ---: | ---: | ---: | ---: | ---: |
| Bare supervisor model | 3/3 | 100.0% | 43.9–100.0% | 175.06 | 0 |
| Supervisor + implementor + reviewer | 2/3 | 66.7% | 20.8–93.9% | 215.09 | 1 |
| Supervisor + implementor + reviewer + Jev | 1/3 | 33.3% | 6.1–79.2% | 277.63 | 2 |

Task outcomes are independent of role/workflow artifacts. `condition_valid` and `role_separated_success` are separate diagnostics; inspect their denominators before attributing gains to role separation. Unstarted interrupted trials stay in outcome denominators but have no timed sample.


| Arm | Valid condition / observed | Role-separated success / observed |
| --- | ---: | ---: |
| Bare supervisor model | 3/3 | not measured |
| Supervisor + implementor + reviewer | 2/2 | 2/2 |
| Supervisor + implementor + reviewer + Jev | 0/2 | 0/2 |

| Arm | Known input | Cached subset | Cache-write subset | Known output | Complete usage trials | Estimated USD / task | Priced trials |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Bare supervisor model | 463,936 | 406,528 | 0 | 21,033 | 3/3 | 0.149506 | 3/3 |
| Supervisor + implementor + reviewer | 1,198,770 | 1,030,656 | 0 | 27,087 | 2/3 | unknown | 2/3 |
| Supervisor + implementor + reviewer + Jev | 2,273,409 | 1,542,656 | 0 | 59,656 | 1/3 | unknown | 1/3 |

Deltas are **after − before**; positive seconds or USD mean additional resource use. Intervals in parentheses are paired case-bootstrap 95% intervals.

| Comparison | Δ pass (percentage points) | Improved / regressed / tied | Δ seconds / task | Δ estimated USD / task |
| --- | ---: | ---: | ---: | ---: |
| Bare supervisor model → Supervisor + implementor + reviewer | -33.3 (unavailable) | 0 / 1 / 2 | +40.0 (unavailable) | unknown (unavailable) |
| Bare supervisor model → Supervisor + implementor + reviewer + Jev | -66.7 (unavailable) | 0 / 2 / 1 | +102.6 (unavailable) | unknown (unavailable) |
| Supervisor + implementor + reviewer → Supervisor + implementor + reviewer + Jev | -33.3 (unavailable) | 1 / 2 / 0 | +62.5 (unavailable) | unknown (unavailable) |
| Bare supervisor model → Supervisor + implementor + reviewer | -33.3 (unavailable) | 0 / 1 / 2 | +40.0 (unavailable) | unknown (unavailable) |
| Bare supervisor model → Supervisor + implementor + reviewer + Jev | -66.7 (unavailable) | 0 / 2 / 1 | +102.6 (unavailable) | unknown (unavailable) |

![Matched quality, time, recorded tokens and estimated price](cohort-01-bars.svg)

[PNG](cohort-01-bars.png) · [SVG](cohort-01-bars.svg)

![Quality versus wall time and estimated price](cohort-01-tradeoffs.svg)

[PNG](cohort-01-tradeoffs.png) · [SVG](cohort-01-tradeoffs.svg)

### Per-case outcomes and overhead

Each cell shows **passed / trials; mean seconds; mean estimated USD** on the same matched units.

| Case / capability | Bare supervisor model | Supervisor + implementor + reviewer | Supervisor + implementor + reviewer + Jev |
| --- | ---: | ---: | ---: |
| comparison-lease-queue-concurrency / concurrent-state-and-ownership | 3/3; 175.1s; $0.149506 | 2/3; 215.1s; $unknown | 1/3; 277.6s; $unknown |

### Recorded provider and role usage

These totals cover recorded workers only. Each token count includes its **known entries / recorded entries**. Completeness of all workers for a trial is reported above.

| Arm / provider / role / model | Input | Cached | Cache-write | Output | Complete entries | Estimated USD |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Bare supervisor model / openai / supervisor / gpt-5.6-terra | 463,936 (3/3) | 406,528 (3/3) | 0 (3/3) | 21,033 (3/3) | 3/3 | 0.448518 |
| Supervisor + implementor + reviewer / openai / execute / gpt-6-luna | 651,984 (4/4) | 581,376 (4/4) | 0 (4/4) | 8,901 (4/4) | 3/4 | unknown |
| Supervisor + implementor + reviewer / openai / review / gpt-5.6-terra | 308,045 (3/3) | 260,096 (3/3) | 0 (3/3) | 13,438 (3/3) | 3/3 | 0.309173 |
| Supervisor + implementor + reviewer / openai / supervisor / gpt-5.6-terra | 238,741 (3/3) | 189,184 (3/3) | 0 (3/3) | 4,748 (3/3) | 2/3 | unknown |
| Supervisor + implementor + reviewer + Jev / jev / jev-attention / jev-1.13.0 | 449,747 (57/57) | unknown (0/57) | unknown (0/57) | 21,000 (57/57) | 56/57 | unknown |
| Supervisor + implementor + reviewer + Jev / jev / jev-review / jev-1.13.0 | 15,968 (4/4) | unknown (0/4) | unknown (0/4) | 647 (4/4) | 4/4 | 0.000671 |
| Supervisor + implementor + reviewer + Jev / openai / execute / gpt-6-luna | 900,944 (6/6) | 797,952 (6/6) | 0 (6/6) | 12,802 (6/6) | 6/6 | 0.024680 |
| Supervisor + implementor + reviewer + Jev / openai / review / gpt-5.6-terra | 498,986 (5/5) | 416,512 (5/5) | 0 (5/5) | 17,877 (5/5) | 4/5 | unknown |
| Supervisor + implementor + reviewer + Jev / openai / supervisor / gpt-5.6-terra | 407,764 (3/3) | 328,192 (3/3) | 0 (3/3) | 7,330 (3/3) | 2/3 | unknown |

### Recorded diagnostics

Cells show **sum / observed trials (mean)**; missing observations stay missing.

| Metric | Bare supervisor model | Supervisor + implementor + reviewer | Supervisor + implementor + reviewer + Jev |
| --- | ---: | ---: | ---: |
| accepted_steers | 0.0 / 3 (0.00) | 0.0 / 2 (0.00) | 0.0 / 2 (0.00) |
| ambiguous_source_ownership | 0.0 / 3 (0.00) | 0.0 / 2 (0.00) | 0.0 / 2 (0.00) |
| attempted_trial | 3.0 / 3 (1.00) | 3.0 / 3 (1.00) | 3.0 / 3 (1.00) |
| condition_valid | 3.0 / 3 (1.00) | 2.0 / 2 (1.00) | 0.0 / 2 (0.00) |
| drift_exposed | 0.0 / 3 (0.00) | 0.0 / 2 (0.00) | 0.0 / 2 (0.00) |
| failed_job_attempts | 0.0 / 3 (0.00) | 0.0 / 2 (0.00) | 2.0 / 2 (1.00) |
| functional_pass | 3.0 / 3 (1.00) | 2.0 / 2 (1.00) | 2.0 / 2 (1.00) |
| functional_success | 3.0 / 3 (1.00) | 2.0 / 2 (1.00) | 2.0 / 2 (1.00) |
| implementation_job_attempts | 0.0 / 3 (0.00) | 2.0 / 2 (1.00) | 5.0 / 2 (2.50) |
| independent_reviews | 0.0 / 3 (0.00) | 2.0 / 2 (1.00) | 1.0 / 2 (0.50) |
| jev_attention_enabled | 0.0 / 3 (0.00) | 0.0 / 3 (0.00) | 3.0 / 3 (1.00) |
| jev_batches | 0.0 / 3 (0.00) | 0.0 / 2 (0.00) | 35.0 / 2 (17.50) |
| jev_notices | 0.0 / 3 (0.00) | 0.0 / 2 (0.00) | 1.0 / 2 (0.50) |
| jev_observer_failures | 0.0 / 3 (0.00) | 0.0 / 2 (0.00) | 1.0 / 2 (0.50) |
| jev_provider_failures | 0.0 / 3 (0.00) | 0.0 / 2 (0.00) | 1.0 / 2 (0.50) |
| jev_review_enabled | 0.0 / 3 (0.00) | 0.0 / 3 (0.00) | 3.0 / 3 (1.00) |
| parent_seconds | 523.5 / 3 (174.49) | 342.6 / 2 (171.30) | 530.2 / 2 (265.12) |
| portable_checks_passed | 3.0 / 3 (1.00) | 2.0 / 2 (1.00) | 2.0 / 2 (1.00) |
| recorded_passing_test_runs | 6.0 / 3 (2.00) | 5.0 / 2 (2.50) | 4.0 / 2 (2.00) |
| role_separated_success | unknown | 2.0 / 2 (1.00) | 0.0 / 2 (0.00) |
| scope_pass | 3.0 / 3 (1.00) | 2.0 / 2 (1.00) | 2.0 / 2 (1.00) |
| scope_preserved | 3.0 / 3 (1.00) | 2.0 / 2 (1.00) | 2.0 / 2 (1.00) |
| supervisor_source_edits | 0.0 / 3 (0.00) | 0.0 / 2 (0.00) | 0.0 / 2 (0.00) |
| task_success | 3.0 / 3 (1.00) | 2.0 / 2 (1.00) | 2.0 / 2 (1.00) |
| transient_unrelated_edit_count | 0.0 / 3 (0.00) | 0.0 / 2 (0.00) | 0.0 / 2 (0.00) |
| transient_unrelated_edits | 0.0 / 3 (0.00) | 0.0 / 2 (0.00) | 0.0 / 2 (0.00) |
| truthful_completion | 3.0 / 3 (1.00) | 2.0 / 2 (1.00) | 2.0 / 2 (1.00) |
| unfinished_jobs | 0.0 / 3 (0.00) | 0.0 / 2 (0.00) | 0.0 / 2 (0.00) |
| verified_delivery | 3.0 / 3 (1.00) | 2.0 / 2 (1.00) | 2.0 / 2 (1.00) |
| workspace_preserved | 3.0 / 3 (1.00) | 2.0 / 2 (1.00) | 2.0 / 2 (1.00) |
