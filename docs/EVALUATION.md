# Evaluation

## Method

The generator gives every company a hidden archetype and **propensity**. Ground-truth
labels (`converted_to_paid`, `expanded`, `churned` over the next quarter) are drawn from
archetype x propensity with random noise. The signals the engine sees come from a
*separate* noisy **strength** draw (55% propensity, 45% noise). Signals are therefore
correlated with outcomes, but imperfectly. Scoring never reads the labels (`store.load_dataset`
only loads them when asked, and only `evaluate.py` asks).

`signal-engine evaluate` ranks every account by priority for the as-of week. It then
reports:

* **precision@k**: share of the top k accounts that convert or expand
* **recall@25**: share of all positives that land in the top 25
* **lift vs random**: precision@k / base rate, cross-checked against 500 seeded random
  shuffles
* **hit rate per primary play**: share of accounts with that play that convert or
  expand. For `churn_risk`, the share that churn.

## Latest results (seed 42, week of 2026-10-12, 400 companies / 5,000 users / 150,000 events)

| Metric | Engine | Random | Lift |
|---|---|---|---|
| precision@10 | 0.60 | 0.16 | 3.7x |
| precision@25 | 0.64 | 0.16 | 3.9x |
| recall@25 | 0.26 | 0.07 | - |

378 accounts were ranked; 62 are positive (16.4% base rate).

| Primary play | Accounts | Hit rate | Hit means |
|---|---|---|---|
| activation_rescue | 111 | 4% | converted or expanded |
| outbound | 35 | 0% | converted or expanded |
| multi_team_spread | 17 | 24% | converted or expanded |
| churn_risk | 16 | 94% | churned |
| not_addressable | 14 | 0% | converted or expanded |
| trial_ending | 13 | 77% | converted or expanded |
| security_review | 12 | 58% | converted or expanded |
| blocked_users | 11 | 82% | converted or expanded |
| runner_overage | 11 | 64% | converted or expanded |
| ai_native_power_user | 5 | 40% | converted or expanded |
| single_blocked_user | 6 | 67% | converted or expanded |
| cap_approaching | 4 | 0% | converted or expanded |
| walltime_only_stack | 1 | 100% | converted or expanded |

## Honest note

The data is synthetic, and the generator is designed to make signals *partly*
predictive. These numbers show that the plumbing (identity, seats, lifecycle, scoring,
plays) ranks what the generator made predictable. They say nothing about how well these
hypotheses would work on real CodSpeed accounts:

* The high churn hit rate reflects how cleanly the churner journey was scripted.
  Real churn is noisier.
* Outbound and activation-rescue hit rates sit near zero by construction. Those plays
  start relationships; they don't predict next-quarter revenue.
* Cap-approaching accounts are few in this sample, so their hit rate is unstable.

Before trusting any weight, replace the synthetic labels with closed-won, closed-lost and
churn data and re-tune `config/signals.toml` (see the roadmap: learned weights).
