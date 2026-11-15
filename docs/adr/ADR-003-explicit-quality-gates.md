# ADR-003: Explicit quality gates

**Status:** Accepted

## Context

"Ship if the score looks good" is not reviewable. Release criteria must be written down,
versioned, applied identically in CI and locally, and produce reasons a reviewer can check.
Safety-relevant categories must be able to block a release regardless of overall improvement.

## Decision

* Gate policies are YAML files (`configs/gates/*.yaml`) parsed into typed `GateSpec`s of three
  kinds: `metric` (min/max on a run or comparison metric), `category` (pass rate, regressions,
  pass-rate drop) and `critical_failures` (max failed CRITICAL tests).
* Each gate declares `on_violation` (`fail`/`warn`) and `on_missing` (`fail`/`warn`/`skip`), so a
  metric that cannot be computed never passes silently.
* `evaluate_gates` returns a `ReleaseDecision` (`pass`/`warning`/`fail`) where every gate has an
  observed value, threshold and reason; blocking reasons and warnings are listed explicitly.
* With `block_on_critical_failure` (default true) any non-passing CRITICAL test blocks the
  release independently of all gates.
* The CLI exits with code 2 on FAIL (`run --fail-on-gate`, `gates check`, red-team campaigns
  above their attack-success limit), which is what Jenkins uses to fail a build.

## Consequences

Positive:

* Release criteria are code-reviewed and versioned with the repository.
* A safety category with `max_regressions: 0` vetoes a release even when overall accuracy
  improves.
* Missing data is visible as a WARNING or FAIL instead of an implicit pass.

Negative / trade-offs:

* Thresholds must be chosen and maintained by people; badly chosen thresholds give false
  confidence or constant noise.
* Fixed thresholds on small suites are coarse (one test can move a category by many points);
  confidence intervals are reported but not used for gating.
* Policies that need a baseline (e.g. `regression_rate`) only warn when run without one.
