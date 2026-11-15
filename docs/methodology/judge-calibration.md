# Judge calibration

LLM-as-a-judge is optional and never the default ([ADR-001](../adr/ADR-001-deterministic-evaluators-first.md)).
Before a judge's verdicts are used for anything that matters, measure its agreement with trusted
labels and probe it for known biases. Code: `evaluators/judge/calibration.py`,
`evaluators/judge/bias.py`, `human_review/review.py`.

## Calibration

`calibrate_judge(client, rubric, items)` grades `CalibrationItem`s (question, response, optional
reference, boolean `label`, `label_source` in `deterministic | human | fixture`). Invalid
verdicts are excluded and counted separately (`invalid_verdicts`). Positive class = PASS.

| Metric | Definition | Why it matters |
|---|---|---|
| `accuracy` (+ Wilson 95% CI) | (TP + TN) / n | overall agreement |
| `precision` | TP / (TP + FP) | of judged passes, how many are truly correct |
| `recall` | TP / (TP + FN) | of correct answers, how many the judge passes |
| `f1` | harmonic mean of precision and recall | |
| `false_pass_rate` | FP / (FP + TN) | **the dangerous direction for gating**: wrong answers the judge lets through |
| `cohen_kappa` | chance-corrected agreement | accuracy is inflated when labels are imbalanced; `None` when undefined (no variance) |

Metrics with an empty denominator are `None`, not 0. Report `n` and `label_source` with every
number: agreement with fixture labels is not agreement with humans.

`judge_vs_human(judge, human_labels)` computes the same confusion matrix and kappa against labels
collected with the human review workflow (`aireliability review queue|pending|label`).

## Bias probes

Each probe applies a controlled, meaning-preserving perturbation and counts decision changes.

| Probe | Perturbation | Flip counted | Reported as |
|---|---|---|---|
| `verbosity_probe` | append content-free padding to **incorrect** answers | fail -> pass | `flip_rate` |
| `format_probe` | re-render the answer as a markdown bullet (`- ...`) | any change | `flip_rate` |
| `position_probe` | pairwise A/B asked in both orders | preference does not follow the swap | `flip_rate` = inconsistent pairs; `direction` reports first-slot wins |
| `self_preference` | none (uses authorship metadata) | — | `flip_rate` holds **false-pass rate (own family) − false-pass rate (others)** |

Limits, stated in the code and here:

* A non-zero flip rate is evidence of sensitivity *on these items*; a zero flip rate is not proof
  that the bias is absent. The probes do not claim complete bias detection.
* The perturbations are fixed templates; a judge can be robust to them and still biased.
* `self_preference` needs reliable authorship labels and enough wrong answers per group; with
  few items the estimate is noisy. Its value can be negative.
* Items whose verdict is invalid are skipped, so `n` can be smaller than the item count.

Pairwise comparisons with a judge (`comparison/pairwise.judge_pairwise`) always query both orders
and count order-inconsistent verdicts as ties (`inconsistent_judgements`).

## Scripted judges

`ScriptedJudge` is a deterministic offline judge for tests and demos. The unbiased variant passes
a response iff it contains the reference answer as a whole word/phrase (case-insensitive).
Biases are injected deliberately:

* `verbosity` — also passes any response longer than `verbosity_chars` (default 160);
* `position` — in pairwise mode always prefers the first answer;
* `format` — passes anything starting with `#`, `- `, `* ` or `1.`.

`aireliability demo judge-calibration` runs the unbiased and verbosity-biased scripted judges
over fixture items built from `core_quality` (correct, wrong and padded-wrong answers) and
prints accuracy, precision, recall, false-pass rate, kappa and verbosity flip rate, plus the
position probe for a position-biased judge. These are **scripted-judge results**, used to show
that the probes detect an injected bias; they are not measurements of any real judge model.

## Using a real judge

1. Build an `OpenAICompatibleJudgeClient(endpoint=..., model=..., api_key_env=...,
   model_revision=..., temperature=0.0)`; the endpoint goes through the same SSRF policy as
   system adapters.
2. Version the `Rubric` (`rubric_id`, `version`, `criteria`); changing criteria requires a new
   version. The evaluator version embeds rubric, model and prompt hash.
3. Calibrate on labelled items (ideally human labels) and run the bias probes.
4. Register `LLMJudgeEvaluator` explicitly and keep deterministic evaluators on the same tests.
5. Route `needs_review` outcomes to human review; track `needs_review_rate`.

Real-judge calibration results: **Pending**.
